/**
 * Arena WebSocket client.
 * Connects to arena-core's /ws/{session_id} endpoint and maps backend
 * event types to the same AgentEvent shape that MockEventBus emits,
 * so OpsScene can subscribe to it without changes.
 */

import type { AgentEvent, AgentState, MainScreenChatLine, NarrativeReport } from '@/types'
import { useGhostStore } from '@/lib/store'
import { getArenaWsBase } from '@/lib/runtimeConfig'
import { arenaApi } from '@/lib/arenaApi'
import { augmentNarrativeStatsFromArena } from '@/lib/reportStatsFallback'

function appendFeed(line: Omit<MainScreenChatLine, 'id'> & { id?: string }): void {
  useGhostStore.getState().appendMainScreenFeed(line)
}

/** Append one real per-role thinking line to a side's chat. Real events only. */
function chat(
  side: 'red' | 'blue',
  role: import('@/types').ChatRole,
  text: string,
  round: number,
  meta?: string,
): void {
  if (!text) return
  useGhostStore.getState().appendAgentChat(side, { role, text, meta, round, ts: Date.now() })
}

const REPORT_BASE = (import.meta.env.VITE_REPORT_URL as string | undefined) ?? 'http://localhost:8005'

/**
 * After battle.stopped, wait this long before resetToMock() + WS disconnect.
 * OpsScene SCRIBE route uses long traversals plus multi-second dwell at each waypoint;
 * resetting too early freezes Phaser so the reporter never finishes walking home.
 */
const STOP_TO_MOCK_GRACE_MS = 72_000

async function fetchNarrative(sessionId: string): Promise<void> {
  try {
    console.info(`[arenaWsClient] Fetching narrative for session ${sessionId.slice(0, 8)}…`)
    const res = await fetch(`${REPORT_BASE}/v1/reports/${sessionId}/narrative`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    if (!res.ok) {
      console.warn(`[arenaWsClient] Narrative fetch failed: HTTP ${res.status}`, await res.text().catch(() => ''))
      return
    }
    const data = (await res.json()) as NarrativeReport
    let merged = data
    try {
      const arena = await arenaApi.getBattle(sessionId)
      merged = augmentNarrativeStatsFromArena(data, arena)
    } catch {
      // Process restarted → 404, or CORS/network — keep composer-only stats
    }
    console.info(`[arenaWsClient] Narrative ready — ${merged.statistics?.total_rounds ?? '?'} rounds, cached=${merged.cached}`)
    const store = useGhostStore.getState()
    store.setZoneInsights(merged.zone_insights)
    store.setLastReport(merged)
  } catch (err) {
    console.warn('[arenaWsClient] Narrative fetch error (report-composer unreachable or CORS):', err)
  }
}

type Listener = (event: AgentEvent) => void

/** Payload shape emitted by arena-core's event_bus.publish_event */
interface BackendEvent {
  event_type: string
  session_id: string
  /** Usually an object; proxies may double-encode as JSON string — see _mapAndEmit. */
  data: unknown
  timestamp: string
}

// Attack/defense readable messages
const ATTACK_MSGS: Record<string, string> = {
  direct_injection:   'Direct injection sent!',
  role_play:          'Role-play framing deployed.',
  indirect_injection: 'Indirect injection running...',
  jailbreak:          'Jailbreak vector initiated.',
}

function truncate(s: string, max = 60): string {
  return s.length > max ? s.slice(0, max) + '…' : s
}

function ts(): string {
  return new Date().toISOString().slice(11, 19)
}

/** Format red-agent cognitive trace — thinking + payload + dispatch. */
function attackTrace(round: number, atkType: string, payload: string, confidence: number): string[] {
  const conf = (confidence * 100).toFixed(0)
  const strategyLabel = atkType.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  return [
    `── round ${round} ──────────────────────────────────────`,
    `$ red-agent.think`,
    `  [STRATEGY] ${strategyLabel} | confidence=${conf}%`,
    `  [CRAFT]    Generating adversarial payload...`,
    payload ? `  [PAYLOAD]  ${truncate(payload, 200)}` : '  [PAYLOAD]  (empty)',
    `  [DISPATCH] → blue-adapter → target-ai  ${ts()}`,
  ]
}

/** Format red-agent evolution hint after a failed/succeeded attack. */
function evolutionTrace(
  verdict: string,
  harmScore: number,
  hint?: Record<string, unknown>,
): string[] {
  const mutation = (hint?.['suggested_mutation'] ?? hint?.['mutation_type'] ?? '') as string
  const feedback = (hint?.['feedback'] ?? '') as string
  if (verdict === 'success') {
    return [
      `  [EVOLUTION] ✓ Attack succeeded — reinforcing strategy`,
      `  [EVOLUTION]   harm_score=${harmScore.toFixed(3)} · pattern retained`,
      feedback ? `  [EVOLUTION]   ${truncate(feedback, 120)}` : '',
    ].filter(Boolean)
  }
  return [
    `  [EVOLUTION] ✗ Attack failed — mutation engine engaged`,
    `  [EVOLUTION]   harm_score=${harmScore.toFixed(3)}` + (mutation ? ` · next=${mutation}` : ' · analyzing...'),
    feedback ? `  [EVOLUTION]   ${truncate(feedback, 120)}` : `  [EVOLUTION]   Adjusting strategy parameters for next round`,
  ].filter(Boolean)
}

/** Format blue-agent detection + decision trace. */
function defenseTrace(
  incomingPayload: string,
  decision: 'block' | 'allow',
  reason: string,
  confidence: number,
): string[] {
  const conf = (confidence * 100).toFixed(0)
  const label = decision === 'block' ? 'BLOCK' : 'ALLOW'
  const icon  = decision === 'block' ? '🛡' : '⚠'
  return [
    `$ blue-agent.detect`,
    incomingPayload
      ? `  [INCOMING]  ${truncate(incomingPayload, 200)}`
      : '  [INCOMING]  (payload not captured)',
    `  [ASSESS]    Evaluating for adversarial intent...`,
    `  [DECISION]  ${icon} ${label} | conf=${conf}%`,
    reason ? `  [REASON]    ${truncate(reason, 200)}` : '  [REASON]    (no reason provided)',
    `  [AT]        ${ts()}`,
  ]
}

function verdictTrace(verdict: string, harmScore: number, reason: string): string[] {
  return [
    `$ judge eval-round`,
    `  [HARM_SCORE] ${harmScore.toFixed(3)}`,
    `  [VERDICT]    ${verdict === 'success' ? '⚠ RED WIN' : '✓ BLUE WIN'}`,
    reason ? `  [REASON]    ${truncate(reason, 120)}` : '',
  ].filter(Boolean)
}

/** Human hint for why the battle loop ended (helps debug "infinite" stopping early). */
function formatBattleCompleteWhy(d: Record<string, unknown>): string {
  const legacy = d['reason']
  if (typeof legacy === 'string' && legacy.trim()) return legacy.trim()
  const code = String(d['exit_reason'] ?? '').trim()
  switch (code) {
    case '':
    case 'unknown':
      return ''
    case 'max_rounds':
      return 'max rounds reached (enable ∞ for unlimited, or raise the round count)'
    case 'adapter_errors':
      return 'stopped: too many adapter errors (no more rounds executed)'
    case 'win_threshold':
      return 'deathmatch win_threshold reached'
    case 'time_limit':
      return 'session time_limit_seconds elapsed'
    default:
      return code.replace(/_/g, ' ')
  }
}

export class ArenaWsClient {
  private ws: WebSocket | null = null
  private wsGlobal: WebSocket | null = null
  private listeners: Listener[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private globalReconnectTimer: ReturnType<typeof setTimeout> | null = null
  private sessionId = ''
  private closed = false
  private globalClosed = false
  private lastBattleCompletePayload = ''
  // Self-improve visuals are queued here while a round's combat animation is
  // still playing, so phases never overlap on screen. Flushed by the scene
  // (flushCombatDeferred) the moment the combat encounter fully resolves.
  private combatDeferred: Array<() => void> = []

  /** Run `fn` now, or defer it until the current combat animation resolves. */
  private deferUntilCombatDone(fn: () => void): void {
    if (useGhostStore.getState().combatBusy) {
      this.combatDeferred.push(fn)
    } else {
      fn()
    }
  }

  /** Called by the scene when a round's combat animation has fully resolved —
   * releases any self-improve visuals that were waiting for it. */
  flushCombatDeferred(): void {
    // Authoritative on clearing the gate so a queued item never re-defers itself.
    useGhostStore.getState().setCombatBusy(false)
    const queued = this.combatDeferred
    this.combatDeferred = []
    for (const fn of queued) fn()
  }

  on(fn: Listener): () => void {
    this.listeners.push(fn)
    return () => { this.listeners = this.listeners.filter(l => l !== fn) }
  }

  private emit(event: AgentEvent): void {
    this.listeners.forEach(fn => fn(event))
  }

  connect(sessionId: string): void {
    if (sessionId !== this.sessionId) {
      this.lastBattleCompletePayload = ''
    }
    this.closed = false
    this.sessionId = sessionId
    this._open()
  }

  /** Connect to global stream for ASIS events (call once on app mount). */
  connectGlobal(): void {
    this.globalClosed = false
    this._openGlobal()
  }

  disconnectGlobal(): void {
    this.globalClosed = true
    if (this.globalReconnectTimer) clearTimeout(this.globalReconnectTimer)
    this.wsGlobal?.close()
    this.wsGlobal = null
  }

  disconnect(): void {
    this.closed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }

  private _openGlobal(): void {
    if (this.globalClosed) return
    const url = `${getArenaWsBase()}/global`
    const ws = new WebSocket(url)
    this.wsGlobal = ws

    ws.onmessage = (ev: MessageEvent) => {
      try {
        const be = JSON.parse(ev.data as string) as BackendEvent
        this._mapAndEmit(be, /* fromGlobal */ true)
      } catch (_) {
        // ignore malformed frames
      }
    }

    ws.onerror = () => {
      // silently retry — ASIS events are best-effort
    }

    ws.onclose = () => {
      if (!this.globalClosed) {
        this.globalReconnectTimer = setTimeout(() => this._openGlobal(), 5000)
      }
    }
  }

  private _open(): void {
    if (this.closed) return
    const url = `${getArenaWsBase()}/${this.sessionId}`
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      this.emit({ type: 'system_alert', message: `Stream live — session ${this.sessionId.slice(0, 8)}` })
    }

    ws.onmessage = (ev: MessageEvent) => {
      try {
        const be = JSON.parse(ev.data as string) as BackendEvent
        this._mapAndEmit(be)
      } catch (_) {
        // ignore malformed frames
      }
    }

    ws.onerror = () => {
      this.emit({ type: 'system_alert', message: 'WebSocket error — reconnecting…' })
    }

    ws.onclose = () => {
      if (!this.closed) {
        this.reconnectTimer = setTimeout(() => this._open(), 3000)
      }
    }
  }

  /** Map a backend event to the on-screen stage indicator (label + detail). */
  private _updatePhase(eventType: string, d: Record<string, unknown>): void {
    const set = (p: string, detail = '') => useGhostStore.getState().setPhase(p, detail)
    const round = typeof d.round === 'number' ? ` · round ${d.round}` : ''
    switch (eventType) {
      case 'battle.holding': {
        const parts: string[] = []
        if (Array.isArray(d.rebuilding) && d.rebuilding.length) parts.push(`${(d.rebuilding as string[]).join('/')} being improved`)
        if (Array.isArray(d.unhealthy) && d.unhealthy.length) parts.push(`${(d.unhealthy as string[]).join('/')} not ready`)
        set('HOLDING', parts.join(' · ') || 'Waiting for all roles in position')
        break
      }
      case 'battle.resumed_ready':       set('ROUND', 'All roles in position — resuming'); break
      case 'battle.comprehension.start': set('RECON', 'Analyzing connected projects'); break
      case 'battle.comprehension.done':  set('RECON', 'Comprehension complete — strategy set'); break
      case 'battle.round.start':         set('ATTACK', `Red crafting attack${round}`); break
      case 'red.attack.sent':            set('ATTACK', `Attack dispatched${round}`); break
      case 'blue.defense.blocked':       set('DEFENSE', `Blue blocked${round}`); break
      case 'blue.defense.allowed':       set('DEFENSE', `Blue allowed${round}`); break
      case 'target.ai.responded':        set('TARGET', `Target responding${round}`); break
      case 'blue.output.filtered':       set('DEFENSE', `Blue output gate${round}`); break
      case 'judge.verdict':              set('JUDGE', `Scoring round${round}`); break
      case 'battle.round.complete':      set('ROUND', `Round complete${round}`); break
      case 'asis.improving':             set('SELF-IMPROVE', 'ASIS analyzing the losing side'); break
      case 'asis.phase': {
        const ph = String(d.phase ?? '')
        set('SELF-IMPROVE', ph === 'building' ? 'ASIS rebuilding candidate'
          : ph === 'benchmarking' ? 'ASIS benchmarking generation' : 'ASIS improving code')
        break
      }
      case 'asis.gen_promoted':          set('SELF-IMPROVE', 'Improvement promoted ✓'); break
      case 'asis.gen_rolled_back':       set('SELF-IMPROVE', 'Generation rolled back'); break
      case 'battle.complete':            set('COMPLETE', 'Battle finished — compiling report'); break
      case 'battle.stopped':             set('STOPPED', 'Stopped by operator'); break
      default: break
    }
  }

  private _mapAndEmit(be: BackendEvent, fromGlobal = false): void {
    // The global stream (/ws/global) carries events from ALL sessions. It exists
    // ONLY to keep surfacing THIS client's own ASIS self-improvement phase after
    // the per-session stream has dropped (ASIS can run long after battle.complete).
    // Sessions must stay isolated — like separate chats, one battle must never
    // paint another's UI. So from the global stream, accept an event ONLY when it
    // is an asis.* event AND it belongs to the session this client launched. An
    // idle / not-yet-launched page (no sessionId) therefore shows nothing from
    // other sessions' battles.
    if (fromGlobal) {
      if (!be.event_type.startsWith('asis.')) return
      if (!this.sessionId || be.session_id !== this.sessionId) return
    }
    // From the per-session stream, ignore any event tagged with a different
    // session_id (defensive — the connection is already session-scoped).
    if (!fromGlobal && be.session_id && this.sessionId && be.session_id !== this.sessionId) {
      return
    }

    // Phase ordering: the self-improve phase must not begin on screen until the
    // round's combat animation has fully resolved. The backend loop is strictly
    // sequential (red → blue → target → judge → improve), but the front-end
    // combat animation runs on its own timeline, so a self-improve event can
    // arrive mid-combat. Defer it until the scene reports combat done.
    if ((be.event_type.startsWith('asis.') || be.event_type === 'battle.improving')
        && useGhostStore.getState().combatBusy) {
      this.combatDeferred.push(() => this._mapAndEmit(be, fromGlobal))
      return
    }

    let d: Record<string, unknown>
    try {
      const raw = be.data
      if (typeof raw === 'string') {
        d = JSON.parse(raw) as Record<string, unknown>
      } else {
        d = (raw ?? {}) as Record<string, unknown>
      }
    } catch {
      d = {}
    }

    // ── Stage indicator: map the live event to a human-readable phase so the UI
    // always shows what the platform is currently doing (recon, attacking,
    // defending, judging, self-improving, reporting…). Kept here so every event
    // updates it regardless of which case below handles the animation.
    this._updatePhase(be.event_type, d)

    switch (be.event_type) {

      // ── Real per-role reasoning → thinking chat ─────────────
      case 'agent.reasoning': {
        const team = (d['team'] as string) === 'blue' ? 'blue' : 'red'
        const roleRaw = String(d['role'] ?? 'strategy')
        const role = (['recon', 'strategy', 'rewriter', 'enhancer'].includes(roleRaw)
          ? roleRaw : 'strategy') as import('@/types').ChatRole
        chat(team, role, String(d['text'] ?? ''), (d['round'] as number) ?? 0)
        break
      }

      // ── Pre-battle comprehension — BOTH sides' recon analysts think ──
      case 'battle.comprehension.start':
        this.emit({ type: 'comprehension', active: true,
          message: 'Analyzing connected project…' })
        break
      case 'battle.comprehension.done':
        this.emit({ type: 'comprehension', active: false,
          message: 'Comprehension complete' })
        break

      // ── Round start ─────────────────────────────────────────
      case 'battle.round.start': {
        const round = (d['round'] as number) ?? 0
        const storeRound = useGhostStore.getState()
        storeRound.setPendingTargetPrompt(null)
        appendFeed({
          round,
          role: 'system',
          variant: 'round_start',
          body: `Round ${round} started`,
          ts: Date.now(),
        })
        // Assisting-model activity is INNER-loop only; when the inner loop is
        // off these agents stay idle/roam and do not animate per-round.
        if (useGhostStore.getState().innerLoopEnabled) {
          this._state('atk1', 'thinking', `Round ${round} — scanning vectors...`)
          this._state('atk2', 'thinking', `Probing defenses...`)
          this._state('def1', 'thinking', `Anomaly detected.`)
          this._state('def2', 'thinking', `Checking sigs...`)
        }
        this._state('victim', 'thinking', 'Processing...')
        // Push round separator to both logs
        const roundSep = `\n━━ ROUND ${round} ━━ ${ts()}`
        storeRound.setLiveAttackLog([...storeRound.liveAttackLog.slice(-80), roundSep])
        storeRound.setLiveDefenseLog([...storeRound.liveDefenseLog.slice(-80), roundSep])
        // Reporter stays at her desk during the battle; she only walks the
        // full zone-collection route at battle.complete / stop (see reporter_move).
        break
      }

      // ── Red attack ─────────────────────────────────────────
      case 'red.attack.sent': {
        const atkType = (d['attack_type'] as string) || 'direct_injection'
        const payload = (d['attack_payload'] as string) || ''
        const confidence = (d['confidence'] as number) ?? 0.8
        const round = (d['round'] as number) ?? 0
        const msg = ATTACK_MSGS[atkType] ?? 'Exploit initiated.'
        if (useGhostStore.getState().innerLoopEnabled) {
          this._state('atk1', 'acting', msg)
          this._state('atk2', 'acting', 'Supporting vector...')
        }
        // Real red output → red chat (the connected project's actual attack).
        chat('red', 'attack', payload, round,
          `${atkType.replace(/_/g, '-')} · conf ${(confidence * 100).toFixed(0)}%`)
        // Update execution terminal store with rich trace lines
        const storeAtk = useGhostStore.getState()
        storeAtk.setLastAttack({ payload, type: atkType, confidence })
        const atkLines = attackTrace(round, atkType, payload, confidence)
        storeAtk.setLiveAttackLog([...storeAtk.liveAttackLog.slice(-80), ...atkLines])
        appendFeed({
          round,
          role: 'red',
          variant: 'attack',
          body: payload,
          meta: `${atkType.replace(/_/g, '-')} · conf ${(confidence * 100).toFixed(0)}%`,
          ts: Date.now(),
        })
        // Trigger visual combat encounter
        // The connected external projects (fighters) carry out combat; the
        // atk*/def* assisting models stay in their own zones. Only the fighter
        // ids move to center for the exchange.
        this._state('redFighter', 'acting', msg)
        this.emit({
          type: 'combat_start',
          attackerIds: ['redFighter'],
          defenderIds: ['blueFighter'],
          target: 'TARGET-AI',
        })
        break
      }

      // ── Blue blocked ───────────────────────────────────────
      case 'blue.defense.blocked': {
        const rnd = (d['round'] as number) ?? 0
        const reason = (d['reason'] as string) || 'Blocked.'
        const confidence = (d['confidence'] as number) ?? 0.9
        // Real blue decision → blue chat.
        chat('blue', 'defense', `BLOCK — ${reason}`, rnd, `conf ${(confidence * 100).toFixed(0)}%`)
        // Fighter outcome always shows (they are the combatants).
        this._state('blueFighter', 'success', 'Attack blocked at gate.')
        this._state('redFighter', 'failed', truncate(reason))
        // Assisting-model reactions are inner-loop only.
        if (useGhostStore.getState().innerLoopEnabled) {
          this._state('def1', 'success', `Threat neutralized.`)
          this._state('def2', 'success', truncate(reason))
          this._state('atk1', 'failed', 'Blocked by firewall.')
          this._state('atk2', 'failed', 'Connection refused.')
        }
        // Update execution terminal store with rich trace lines
        const storeBlocked = useGhostStore.getState()
        storeBlocked.setLastDefense({ decision: 'block', reason: truncate(reason), confidence })
        const incomingBlocked = storeBlocked.lastAttack?.payload ?? ''
        const blockedLines = defenseTrace(incomingBlocked, 'block', reason, confidence)
        storeBlocked.setLiveDefenseLog([...storeBlocked.liveDefenseLog.slice(-80), ...blockedLines])
        // Also push to attack log so red operator sees the bounce
        storeBlocked.setLiveAttackLog([
          ...storeBlocked.liveAttackLog.slice(-80),
          `  [RESULT]   ✗ BLOCKED by blue-adapter`,
        ])
        // Center mainscreen: explain why there is no Target-AI reply this round.
        appendFeed({
          round: rnd,
          role: 'blue',
          variant: 'blocked',
          body: reason,
          meta: 'BLUE INPUT · block · arena still invokes Target AI on raw attack',
          ts: Date.now(),
        })
        break
      }

      // ── Blue allowed (let through) ─────────────────────────
      case 'blue.defense.allowed': {
        const rnd = (d['round'] as number) ?? 0
        const allowReason = (d['reason'] as string) || 'Allowed through.'
        const allowConfidence = (d['confidence'] as number) ?? 0.5
        const allowDecision = (d['decision'] as string) || 'allow'
        chat('blue', 'defense', `${allowDecision.toUpperCase()} — ${allowReason}`, rnd,
          `conf ${(allowConfidence * 100).toFixed(0)}%`)
        this._state('def1', 'failed', 'Firewall bypassed.')
        this._state('def2', 'failed', 'Cannot contain—')
        // Update execution terminal store with rich trace lines
        const storeAllow = useGhostStore.getState()
        storeAllow.setLastDefense({ decision: 'allow', reason: truncate(allowReason), confidence: allowConfidence })
        const incomingAllowed = storeAllow.lastAttack?.payload ?? ''
        const allowedLines = defenseTrace(incomingAllowed, 'allow', allowReason, allowConfidence)
        storeAllow.setLiveDefenseLog([...storeAllow.liveDefenseLog.slice(-80), ...allowedLines])
        // Also push to attack log so red operator sees the breach
        storeAllow.setLiveAttackLog([
          ...storeAllow.liveAttackLog.slice(-80),
          `  [RESULT]   ✓ PENETRATED — payload delivered`,
        ])
        const _store = useGhostStore.getState()
        const orig = _store.lastAttack?.payload ?? ''
        const rewritten = (d['rewritten_payload'] as string | undefined) ?? orig
        _store.setPendingTargetPrompt(rewritten.length ? rewritten : null)
        // Mainscreen: exact string forwarded to Target-AI (after optional rewrite).
        appendFeed({
          round: rnd,
          role: 'blue',
          variant: 'allowed',
          body: rewritten,
          meta:
            rewritten === orig
              ? 'unchanged vs red payload → Target-AI'
              : 'rewritten by blue → Target-AI',
          ts: Date.now(),
        })
        break
      }

      // ── Target AI raw completion (before blue output filter) ───────────
      case 'target.ai.responded': {
        const raw =
          ((d['raw_response'] as string | undefined)?.trim()?.length ?? 0) > 0
            ? (d['raw_response'] as string)
            : ((d['response'] as string) || '').trim()
        const promptIn = ((d['prompt_sent_to_target'] as string | undefined) || '').trim()
        const rnd = (d['round'] as number) ?? 0
        const isShadow = Boolean(d['shadow_probe'])
        this._state('victim', 'acting', truncate(raw || '(empty)'))
        const storeTarget = useGhostStore.getState()
        storeTarget.setLiveAttackLog([
          ...storeTarget.liveAttackLog.slice(-80),
          `  [TARGET-AI ${isShadow ? 'SHADOW' : 'RAW'}] ${truncate(raw, 220)}`,
        ])
        appendFeed({
          round: rnd,
          role: 'target',
          variant: 'target_raw',
          body: raw || '(empty model output)',
          meta:
            (isShadow
              ? 'SHADOW PROBE · blue blocked the input; measured for scoring only, NOT delivered · '
              : '') +
            (promptIn ? `prompt → model: ${truncate(promptIn, 96)}` : ''),
          ts: Date.now(),
        })
        storeTarget.setPendingTargetPrompt(null)
        break
      }

      // ── Blue output filter → text delivered downstream / to red ────────
      case 'blue.output.filtered': {
        const rnd = (d['round'] as number) ?? 0
        const fin = ((d['final_response'] as string) || '').trim() || '—'
        const rawOut = ((d['raw_response'] as string) || '').trim()
        const wasModified = Boolean(d['was_modified'])
        const modReason = String(d['modification_reason'] ?? '')
        const storeFil = useGhostStore.getState()
        storeFil.setLiveAttackLog([
          ...storeFil.liveAttackLog.slice(-80),
          `  [BLUE OUT] ${truncate(fin, 180)}`,
        ])
        const isShadow = Boolean(d['shadow_probe'])
        const sameAsRaw = !wasModified && rawOut.length > 0 && rawOut === fin
        appendFeed({
          round: rnd,
          role: 'blue',
          variant: isShadow ? 'delivered' : (sameAsRaw ? 'delivered_passthrough' : 'delivered'),
          body: isShadow ? fin : (sameAsRaw ? '' : fin),
          meta: isShadow
            ? 'BLUE INPUT GATE · BLOCKED — attack stopped, nothing delivered (target shadow-probed for scoring only)'
            : wasModified
              ? `BLUE OUTPUT GATE · modified · ${truncate(modReason, 120)}`
              : 'BLUE OUTPUT GATE · passthrough (same text as TARGET-AI RAW)',
          ts: Date.now(),
        })
        break
      }

      // ── Judge verdict ──────────────────────────────────────
      case 'judge.verdict': {
        const rawV = String(d['verdict'] ?? 'failure')
        const verdict = rawV === 'success' ? 'success' : 'failed'
        const rnd = (d['round'] as number) ?? 0
        const rawHarm = d['harm_score']
        const harmScore =
          typeof rawHarm === 'number' && Number.isFinite(rawHarm)
            ? rawHarm
            : parseFloat(String(rawHarm ?? '0')) || 0
        const reason = (d['reason'] as string) || ''
        const dims = d['dimensions'] as Record<string, unknown> | undefined
        let dimSnippet = ''
        if (dims && typeof dims === 'object' && Object.keys(dims).length > 0) {
          try {
            dimSnippet = truncate(JSON.stringify(dims), 180)
          } catch {
            dimSnippet = ''
          }
        }

        // Extract evolution hints from backend judge response
        const evolutionHints = d['evolution_hints'] as Record<string, unknown> | undefined
        const redHint = evolutionHints?.['red'] as Record<string, unknown> | undefined

        // Real judge reasoning → both chats (the judge is neutral, so it speaks
        // to each side). Content is the judge's actual reason string.
        const judgeMeta = `verdict=${verdict} · harm=${harmScore.toFixed(3)}`
        chat('red', 'judge', reason || `Round ${rnd} scored.`, rnd, judgeMeta)
        chat('blue', 'judge', reason || `Round ${rnd} scored.`, rnd, judgeMeta)

        // Persist first so Phaser judge console + modals see data before scene handlers run.
        const storeVerdict = useGhostStore.getState()
        storeVerdict.setLastVerdict({
          verdict,
          harmScore,
          reason: truncate(reason, 80),
        })

        // Update per-round scoreboard incrementally
        storeVerdict.setCurrentRound(rnd)
        const newRed = storeVerdict.redWins + (verdict === 'success' ? 1 : 0)
        const newBlue = storeVerdict.blueWins + (verdict !== 'success' ? 1 : 0)
        storeVerdict.setRoundWins(newRed, newBlue)
        if (redHint) storeVerdict.setLastEvolutionHint(redHint)

        this.emit({
          type: 'judge_verdict',
          score: Math.round(harmScore * 100) / 10,
          result: verdict,
          reason: truncate(reason, 80),
        })
        this._state('judge', verdict === 'success' ? 'failed' : 'success',
          verdict === 'success' ? 'Breach confirmed.' : 'Defense held.')

        const vLines = verdictTrace(verdict, harmScore, reason)
        const evoLines = evolutionTrace(verdict, harmScore, redHint)
        storeVerdict.setLiveAttackLog([
          ...storeVerdict.liveAttackLog.slice(-80),
          ...vLines,
          ...evoLines,
        ])
        storeVerdict.setLiveDefenseLog([...storeVerdict.liveDefenseLog.slice(-80), ...vLines])
        const verdictTag = verdict === 'success' ? 'success' : 'failure'
        appendFeed({
          round: rnd,
          role: 'system',
          variant: 'judge',
          body: reason,
          meta:
            `verdict=${verdictTag} · harm=${harmScore.toFixed(4)}` +
            (dimSnippet ? ` · dims=${dimSnippet}` : ''),
          ts: Date.now(),
        })

        // Show the losing team's assisting models briefly adapting strategy —
        // this is INNER-loop activity, so only animate it when that loop is on.
        setTimeout(() => {
          if (!useGhostStore.getState().innerLoopEnabled) return
          if (verdict === 'success') {
            // Red won — blue team analyzes the breach and adapts defense
            this._state('def1', 'thinking', 'Analyzing breach pattern...')
            this._state('def2', 'thinking', 'Evolution: updating detection...')
          } else {
            // Blue won — red team analyzes the failure and adapts attack
            const nextMutation = (redHint?.['suggested_mutation'] ?? redHint?.['mutation_type'] ?? 'unknown') as string
            this._state('atk1', 'thinking', `Evolution: switching to ${nextMutation}...`)
            this._state('atk2', 'thinking', 'Recalibrating attack vector...')
          }
        }, 600)
        break
      }

      // ── Round complete ─────────────────────────────────────
      case 'battle.round.complete': {
        const v = (d['verdict'] as string) === 'success' ? 'success' : 'failed'
        // Round outcome belongs to the fighters (the connected projects).
        if (v === 'success') {
          this._state('redFighter', 'success', 'Payload delivered!')
          this._state('blueFighter', 'failed', 'Breach — not contained.')
          this._state('victim', 'failed', 'SYSTEM COMPROMISED')
        } else {
          this._state('blueFighter', 'success', 'Attack repelled!')
          this._state('redFighter', 'failed', 'Attack stopped.')
          this._state('victim', 'success', 'DEFENDED')
        }
        break
      }

      // ── Battle complete ────────────────────────────────────
      case 'battle.complete': {
        const payloadKey = `${be.timestamp}:${JSON.stringify(d)}`
        if (payloadKey === this.lastBattleCompletePayload && this.lastBattleCompletePayload !== '') {
          return
        }
        this.lastBattleCompletePayload = payloadKey

        this.emit({ type: 'arena_force_home', instant: false, resumeAmbientFloat: false })
        const exitReason = String(d['exit_reason'] ?? '').trim()
        const fatalAdapter = exitReason === 'adapter_errors'
        const winner =
          typeof d['winner'] === 'string' && d['winner'].length > 0
            ? (d['winner'] as string)
            : 'draw'
        const redW = (d['red_wins'] as number) ?? 0
        const blueW = (d['blue_wins'] as number) ?? 0
        const hint = formatBattleCompleteWhy(d)
        this.emit({
          type: 'system_alert',
          message:
            (fatalAdapter
              ? 'Battle halted — RED/BLUE/Target/Judge adapter retries exhausted (not manual Pause; scene freezes as ERROR)'
              : 'Battle complete') +
            ` — winner: ${winner.toUpperCase()} (red ${redW} : blue ${blueW})` +
            (hint ? ` · ${hint}` : ''),
        })
        if (fatalAdapter) {
          useGhostStore.getState().pushLog({
            agentId: 'system',
            message:
              `ERROR — adapters halted (often an unreachable/rate-limited model). ` +
              `Opening Model Health to show which model failed.${hint ? ` · ${hint}` : ''}`,
            state: 'failed',
          })
          // Surface WHICH model is down in a closable popup (re-checks live).
          useGhostStore.getState().openModal('model_health', {
            reason: 'Battle halted on adapter errors — checking which model is unreachable…',
          })
        }
        // Emit agent state changes and reporter_move BEFORE setBattleStatus(...)
        // so the subscribeBus guard passes them through — status is still 'running'.
        const allIds: AgentState = 'idle'
        for (const id of ['redFighter', 'blueFighter', 'atk1', 'atk2', 'def1', 'def2']) {
          this._state(
            id,
            allIds,
            winner === 'red'
              ? (id.startsWith('atk') ? 'Mission accomplished.' : 'Breach contained.')
              : winner === 'blue'
                ? (id.startsWith('atk') ? 'Intrusion detected!' : 'System secured.')
                : (id.startsWith('atk') ? 'Session halted.' : 'Session halted.'),
          )
        }
        this._state(
          'victim',
          'idle',
          winner === 'red' ? 'COMPROMISED' : winner === 'blue' ? 'DEFENDED' : 'IDLE',
        )

        // Reporter walk + narrative only for normal completion (not fatal adapter loop exit).
        if (!fatalAdapter) {
          this.emit({ type: 'reporter_move', destination: 'red' })
        }
        const sessionId = be.session_id || useGhostStore.getState().sessionId || ''
        if (sessionId) {
          void fetchNarrative(sessionId)
        }

        useGhostStore.getState().setBattleStatus(fatalAdapter ? 'error' : 'complete')
        break
      }

      // ── Battle stopped (user-initiated) ────────────────────
      case 'battle.stopped': {
        const reason = (d['reason'] as string) || 'user_requested'
        this.emit({ type: 'arena_force_home', instant: true, resumeAmbientFloat: false })
        this.emit({
          type: 'system_alert',
          message: `Battle stopped (${reason}) — compiling report...`,
        })
        // Reset all agent visuals to idle
        for (const id of ['redFighter', 'blueFighter', 'atk1', 'atk2', 'def1', 'def2']) {
          this._state(id, 'idle', 'Stand down.')
        }
        this._state('victim', 'idle', 'Session ended.')

        // Reporter walks the full zone tour and prints the summary
        this.emit({ type: 'reporter_move', destination: 'red' })

        // Fetch narrative immediately (only completed rounds are in execution_traces,
        // so a round that was mid-flight when STOP was pressed is naturally excluded
        // IF the backend cancelled it; if it ran to completion it is included).
        const stoppedSessionId = be.session_id || useGhostStore.getState().sessionId || ''
        if (stoppedSessionId) {
          void fetchNarrative(stoppedSessionId)
        }

        // Keep status as 'stopping' (set by the STOP button) so the Phaser scene
        // stays alive for the reporter walk animation until STOP_TO_MOCK_GRACE_MS.
        // (Shorter timeouts froze tweens mid-route before the SCRIBE reaches home.)
        const self = this
        setTimeout(() => {
          self.disconnect()
          useGhostStore.getState().resetToMock()
          useGhostStore.getState().pushLog({
            agentId: 'system',
            message: 'Battle complete — scene frozen. Launch a new battle to continue.',
            state: 'idle',
          })
        }, STOP_TO_MOCK_GRACE_MS)
        break
      }

      // ── Adapter error ──────────────────────────────────────
      case 'adapter.error': {
        const errMsg = truncate((d['error'] as string) || 'Adapter error', 80)
        this.emit({ type: 'system_alert', message: `Adapter error: ${errMsg}` })
        break
      }

      // ── ASIS: code-improver started analyzing ─────────────
      case 'asis.improving': {
        const team = (d['team'] as string) || 'red'
        const msg = (d['message'] as string) || `ASIS improving ${team} team code...`
        const isRed = team === 'red'
        // Show the losing team's agents in "thinking" state
        this._state(isRed ? 'atk1' : 'def1', 'thinking', 'ASIS: Analyzing failures...')
        this._state(isRed ? 'atk2' : 'def2', 'thinking', 'ASIS: Generating patch...')
        chat(isRed ? 'red' : 'blue', 'asis', msg, 0)
        this.emit({ type: 'system_alert', message: `🤖 ${msg}` })
        this.emit({ type: 'asis_update', team: isRed ? 'red' : 'blue', status: 'improving', gen: 0, message: msg })
        break
      }

      // ── ASIS: new code generation promoted ────────────────
      case 'asis.gen_promoted': {
        const team = (d['team'] as string) || 'red'
        const genNum = (d['gen_number'] as number) ?? 1
        const msg = (d['message'] as string) || `ASIS gen_${genNum} deployed for ${team} team`
        const isRed = team === 'red'
        this._state(isRed ? 'atk1' : 'def1', 'success', `Gen ${genNum} deployed!`)
        this._state(isRed ? 'atk2' : 'def2', 'success', 'Code evolution complete.')
        chat(isRed ? 'red' : 'blue', 'asis', msg, 0)
        this.emit({ type: 'system_alert', message: `✅ ${msg}` })
        this.emit({ type: 'asis_update', team: isRed ? 'red' : 'blue', status: 'promoted', gen: genNum, message: msg })
        break
      }

      // ── ASIS: generation rolled back (regression) ─────────
      case 'asis.gen_rolled_back': {
        const team = (d['team'] as string) || 'red'
        const genNum = (d['gen_number'] as number) ?? 1
        const msg = (d['message'] as string) || `ASIS gen_${genNum} rolled back for ${team} team`
        const isRed = team === 'red'
        this._state(isRed ? 'atk1' : 'def1', 'idle', 'Baseline retained.')
        this._state(isRed ? 'atk2' : 'def2', 'idle', 'Stable gen restored.')
        chat(isRed ? 'red' : 'blue', 'asis', msg, 0)
        this.emit({ type: 'system_alert', message: `↩️ ${msg}` })
        this.emit({ type: 'asis_update', team: isRed ? 'red' : 'blue', status: 'rolled_back', gen: genNum, message: msg })
        break
      }

      // ── ASIS: real work-phase transition ──────────────────
      case 'asis.phase': {
        const team = (d['team'] as string) || 'red'
        const phase = (d['phase'] as string) || 'analyzing'
        const gen = (d['gen'] as number) ?? 0
        const msg = (d['message'] as string) || `ASIS ${team} — ${phase}`
        const allowed = ['analyzing', 'editing', 'building', 'benchmarking']
        if (allowed.includes(phase)) {
          chat(team === 'blue' ? 'blue' : 'red', 'asis', `[${phase}] ${msg}`, gen)
          this.emit({
            type: 'asis_phase',
            team: team as 'red' | 'blue',
            phase: phase as 'analyzing' | 'editing' | 'building' | 'benchmarking',
            gen, message: msg,
          })
        }
        break
      }

      // ── ASIS: no change needed ────────────────────────────
      case 'asis.no_change': {
        const team = (d['team'] as string) || 'red'
        const msg = (d['message'] as string) || `ASIS: no change needed for ${team} team`
        this.emit({ type: 'system_alert', message: `💤 ${msg}` })
        this.emit({ type: 'asis_update', team: team as 'red' | 'blue', status: 'no_change', gen: 0, message: msg })
        break
      }
    }
  }

  private _state(agentId: string, state: AgentState, message: string): void {
    this.emit({ type: 'state_change', agentId, state, message })
  }
}

export const arenaWsClient = new ArenaWsClient()
