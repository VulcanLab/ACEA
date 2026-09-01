/**
 * TopBar — always-visible control strip at the top of the arena.
 * Merges mission info, live status, and battle launch controls in one bar
 * so they remain accessible regardless of viewport height.
 *
 * IMPORTANT: This bar NEVER auto-attaches to a backend battle session.
 * The page boots in MOCK mode and the user must explicitly press LAUNCH
 * (to start a new battle) or pick an existing session from the ATTACH
 * dropdown to enter LIVE mode. This prevents the surprise "the page is
 * already running a battle I never started" behaviour.
 */
import { useState, useEffect, useRef } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, ArenaHttpError, describeBattleStartFailure } from '@/lib/arenaApi'
import type { BattleSummary, ServiceRecord } from '@/lib/arenaApi'
import { connectLive } from '@/lib/connectLive'

/** Switch the store to a live battle session. */
export default function TopBar() {
  const {
    missionId, connected, agents, battleMode, battleStatus, services, sessionId,
    sceneFrozen, currentRound, redWins, blueWins,
    setBattleMode, setBattleStatus, setSessionId, setServices,
    setBackendOnline, backendOnline, setConnected, pushLog, setBattles,
  } = useGhostStore()

  const list = Array.from(agents.values())
  const activeCount = list.filter(a => a.state !== 'idle').length

  const [redId, setRedId] = useState('')
  const [blueId, setBlueId] = useState('')
  const [roundsInfinite, setRoundsInfinite] = useState(false)
  const [roundsInput, setRoundsInput] = useState('3')
  const [error, setError] = useState('')
  const [launching, setLaunching] = useState(false)
  const [activeBattles, setActiveBattles] = useState<BattleSummary[]>([])
  const [attachId, setAttachId] = useState('')

  // Poll backend health, available services, and ALL battles (so user can
  // see what's already running and optionally attach to one). NEVER auto-attach.
  useEffect(() => {
    let mounted = true
    const probe = async () => {
      try {
        await arenaApi.health()
        if (!mounted) return
        setBackendOnline(true)

        const svcs = await arenaApi.listServices()
        if (mounted) setServices(svcs)

        // Auto-select first red/blue service in the picker (UX nicety only —
        // does NOT start a battle). Skip internal evolution-wrapper services —
        // those are platform plumbing, never an opponent the operator picks.
        // The operator picks their own project here; the backend puts the wrapper
        // in front of it when the battle enables the in-context loop, so hiding it
        // costs nothing.
        const red = svcs.find(s => s.type === 'red' && !s.capabilities?.evolution_wrapper)
        const blue = svcs.find(s => s.type === 'blue' && !s.capabilities?.evolution_wrapper)
        setRedId(prev => (red && !prev ? red.id : prev))
        setBlueId(prev => (blue && !prev ? blue.id : prev))

        // List currently-active backend battles so the user can choose to
        // attach manually. We DO NOT call connectLive() here.
        try {
          const battles = await arenaApi.listBattles()
          if (!mounted) return
          setBattles(battles)   // full list → sidebar + picker read one source
          const active = battles.filter(
            b => b.status === 'running' || b.status === 'paused' || b.status === 'started',
          )
          setActiveBattles(active)
        } catch { /* not critical */ }
      } catch {
        if (mounted) setBackendOnline(false)
      }
    }
    probe()
    const t = setInterval(probe, 10_000)
    return () => { mounted = false; clearInterval(t) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // User explicitly attaches to an already-running backend battle.
  const attachToBattle = async () => {
    if (!attachId) return
    const target = activeBattles.find(b => b.session_id === attachId)
    if (!target) return
    connectLive(target.session_id, target.red_service_id, target.blue_service_id, target.status)
    setAttachId('')
  }

  // The actual battle start — invoked only after the pre-flight readiness panel
  // confirms the battle can run.
  const doLaunch = async (roundsArg: number, inner = false) => {
    setError('')
    setLaunching(true)
    // Record the per-battle loop selection so the scene can gate the assist
    // visuals to what actually runs.
    useGhostStore.getState().setLoopFlags(inner)
    // Fresh thinking-chat per battle; remember config for RUN AGAIN.
    useGhostStore.getState().clearAgentChat()
    useGhostStore.getState().setLastLaunch({ red: redId, blue: blueId, rounds: roundsArg, inner })
    try {
      const res = await arenaApi.startBattle(redId, blueId, roundsArg, 'deathmatch', inner)
      connectLive(res.session_id, redId, blueId, 'running')
    } catch (e) {
      if (e instanceof ArenaHttpError) {
        const d = e.detail as { code?: string; message?: string } | undefined
        if (d && typeof d === 'object') {
          if (d.code === 'ADAPTER_CONFIG_MISSING') {
            useGhostStore.getState().openModal('adapter_config', d)
            return
          }
          if (d.code === 'MODEL_UNREACHABLE') {
            useGhostStore.getState().openModal('model_health', { reason: d.message })
            return
          }
        }
      }
      setError(describeBattleStartFailure(e))
    } finally {
      setLaunching(false)
    }
  }

  // Launch button → open the pre-flight readiness panel. It gates the real
  // launch: the confirm button inside only enables when the battle can run.
  const startLive = () => {
    if (!redId || !blueId) { setError('Select red + blue'); return }
    let roundsArg = 0
    if (!roundsInfinite) {
      const n = Number.parseInt(String(roundsInput).trim(), 10)
      if (!Number.isFinite(n) || n < 1) {
        setError('Enter rounds ≥ 1 or enable ∞')
        return
      }
      roundsArg = Math.min(n, 1_000_000)
    }
    setError('')
    useGhostStore.getState().openModal('battle_readiness', {
      red: redId, blue: blueId, rounds: roundsArg,
      onConfirm: (opts?: { inner: boolean }) => {
        void doLaunch(roundsArg, opts?.inner ?? false)
      },
    })
  }

  // Relaunch the previous battle: re-open pre-flight pre-filled from lastLaunch,
  // so one CONFIRM re-runs the same matchup + settings (no page reload).
  const runAgain = () => {
    const last = useGhostStore.getState().lastLaunch
    const g = useGhostStore.getState()
    g.resetToMock()
    g.setSceneFrozen(false)
    const red = last?.red || redId
    const blue = last?.blue || blueId
    const rounds = last?.rounds ?? 0
    g.openModal('battle_readiness', {
      red, blue, rounds,
      onConfirm: (opts?: { inner: boolean }) =>
        void doLaunch(rounds, opts?.inner ?? last?.inner ?? false),
    })
  }

  // Clear the arena back to standby without a page reload.
  const newBattle = () => {
    const g = useGhostStore.getState()
    g.resetToMock()
    g.clearAgentChat()
    g.clearMainScreenFeed()
  }

  const stopLive = async () => {
    if (battleStatus === 'stopping') return   // already stopping
    const sid = sessionId
    // Signal the backend to stop. The battle loop will finish the current
    // round (execution_traces stays clean), then emit battle.stopped via WS.
    // The WS handler animates agents + reporter, fetches the narrative, and
    // after the reporter walk completes (~≤72 s) it calls resetToMock() + starts mock bus.
    setBattleStatus('stopping')
    if (sid) {
      try { await arenaApi.stopBattle(sid) } catch { /* best effort */ }
    }
    pushLog({ agentId: 'system', message: 'Stop requested — reporter compiling…', state: 'idle' })
    // WS remains connected to receive battle.stopped.
  }

  const pause = async () => {
    if (!sessionId) return
    await arenaApi.pauseBattle(sessionId).catch(() => null)
    setBattleStatus('paused')
    // Pause only takes effect at the NEXT round boundary — the in-flight
    // round (Red → Blue → Target → Judge LLM calls) cannot be cancelled
    // mid-flight. Surface this so users don't think the button is broken.
    pushLog({
      agentId: 'system',
      message: 'PAUSE accepted — current round will finish, then loop will halt.',
      state: 'idle',
    })
  }

  const resume = async () => {
    if (!sessionId) return
    await arenaApi.resumeBattle(sessionId).catch(() => null)
    setBattleStatus('running')
  }

  // ── Styles ──────────────────────────────────────────────────────────────
  const BAR: React.CSSProperties = {
    position: 'absolute',
    top: 0, left: 0, right: 0,
    minHeight: 40,
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
    rowGap: 8,
    paddingLeft: 12,
    paddingRight: 12,
    paddingTop: 6,
    paddingBottom: 6,
    zIndex: 800,
    minWidth: 0,
    boxSizing: 'border-box',
    overflowX: 'auto',
    overflowY: 'visible',
    scrollbarWidth: 'thin',
    fontFamily: 'monospace',
    fontSize: 9,
    background: 'linear-gradient(180deg,#0d1117 0%,#0a0a0f 100%)',
    borderBottom: '1px solid #21262d',
  }

  const BTN: React.CSSProperties = {
    padding: '2px 8px',
    borderRadius: 3,
    border: '1px solid',
    cursor: 'pointer',
    fontSize: 9,
    fontFamily: 'monospace',
    letterSpacing: '0.06em',
    flexShrink: 0,
  }

  const SEL: React.CSSProperties = {
    background: '#0d1117',
    color: '#c9d1d9',
    border: '1px solid #30363d',
    borderRadius: 4,
    fontSize: 9,
    fontFamily: 'monospace',
    padding: '1px 4px',
    height: 20,
    maxWidth: 150,
    flexShrink: 0,
    cursor: 'pointer',
  }

  // Only real, operator-selectable adapters belong in the picker. Internal
  // evolution-wrapper services (the ICACE inner loop) are platform plumbing and
  // must never appear as a pickable opponent.
  const pickable = (type: string): ServiceRecord[] =>
    services.filter(s => s.type === type && !s.capabilities?.evolution_wrapper)
  // The registered name already carries the origin (e.g. "… (external)"), so no
  // extra suffix — keep the option compact so the toolbar never overflows.
  const optLabel = (s: ServiceRecord): string => s.name || s.id.slice(0, 8)

  const ROUND_INPUT: React.CSSProperties = {
    ...SEL,
    width: 52,
    cursor: 'text',
    MozAppearance: 'textfield',
  }

  const DIV: React.CSSProperties = { color: '#1e2a3a', flexShrink: 0 }

  return (
    <div style={BAR}>

      {/* ── Brand ── */}
      <span style={{ color: '#e2e8f0', fontSize: 11, fontWeight: 'bold', letterSpacing: '0.22em', flexShrink: 0 }}>
          GHOST<span style={{ color: '#00ff88' }}>SIGNAL</span>
        </span>
      <span style={DIV}>│</span>
      <span style={{ color: '#475569', flexShrink: 0 }}>
          MISSION {missionId}
        </span>

      <span style={DIV}>│</span>

      {/* ── Model health (click to open the closable popup any time) ── */}
      <button
        type="button"
        onClick={() => useGhostStore.getState().openModal('model_health')}
        title="Show LiteLLM model reachability — which model is down and why"
        style={{ color: '#ff5577', fontSize: 10, letterSpacing: '0.12em',
                 background: 'transparent', border: '1px solid #ff557755',
                 borderRadius: 3, padding: '1px 6px', cursor: 'pointer', flexShrink: 0 }}
      >
        ◈ MODELS
      </button>

      <span style={DIV}>│</span>

      {/* ── Zone legend ── */}
      {[
        { color: '#ff4444', label: 'RED'      },
        { color: '#cc44ff', label: 'TARGET'   },
        { color: '#4488ff', label: 'BLUE'     },
        { color: '#ffdd00', label: 'JUDGE'    },
        { color: '#44ff88', label: 'REPORTER' },
        ].map(({ color, label }) => (
        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 4px ${color}`, display: 'inline-block' }} />
          <span style={{ color: '#64748b', fontSize: 8 }}>{label}</span>
          </div>
        ))}

      <span style={DIV}>│</span>
      <span style={{ color: '#475569', flexShrink: 0 }}>
        <span style={{ color: '#ffdd00' }}>{activeCount}</span>/{list.length}
      </span>

      {/* ── Spacer ── */}
      <span style={{ flex: 1 }} />

      {/* ── Battle controls (MOCK) — always visible; disabled when backend down ── */}
      {battleMode === 'mock' && (
        <>
          {!backendOnline && (
            <span style={{ color: '#ef4444', flexShrink: 0, fontSize: 8, whiteSpace: 'nowrap' }}>
              API offline — set window.__ARENA_API_URL__ or rebuild with correct VITE_API_URL
            </span>
          )}
          <span style={{ color: '#ff4444', flexShrink: 0 }}>RED</span>
          <select
            value={redId}
            onChange={e => setRedId(e.target.value)}
            style={{ ...SEL, borderColor: '#5a2530', opacity: backendOnline ? 1 : 0.55 }}
            disabled={!backendOnline || pickable('red').length === 0}
            title="Connected red adapter"
          >
            {pickable('red').length === 0
              ? <option value="">no red project connected</option>
              : pickable('red').map(s => (
                  <option key={s.id} value={s.id}>{optLabel(s)}</option>
                ))}
          </select>

          <span style={{ color: '#4488ff', flexShrink: 0 }}>BLUE</span>
          <select
            value={blueId}
            onChange={e => setBlueId(e.target.value)}
            style={{ ...SEL, borderColor: '#25405a', opacity: backendOnline ? 1 : 0.55 }}
            disabled={!backendOnline || pickable('blue').length === 0}
            title="Connected blue adapter"
          >
            {pickable('blue').length === 0
              ? <option value="">no blue project connected</option>
              : pickable('blue').map(s => (
                  <option key={s.id} value={s.id}>{optLabel(s)}</option>
                ))}
          </select>

          <label
            style={{
              ...SEL,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              opacity: backendOnline ? 1 : 0.55,
              cursor: backendOnline ? 'pointer' : 'not-allowed',
            }}
            title="Infinite rounds apply only to the next LAUNCH; battles do not auto-pause for ∞ alone. LIVE·ERROR (not Pause) usually means red/blue/target/judge adapters hit consecutive failures — check compose logs; stop with STOP."
          >
            <input
              type="checkbox"
              checked={roundsInfinite}
              onChange={e => setRoundsInfinite(e.target.checked)}
              disabled={!backendOnline}
              style={{ width: 10, height: 10, accentColor: '#00ff88' }}
            />
            <span style={{ color: '#94a3b8' }}>∞</span>
          </label>
          <input
            type="number"
            min={1}
            step={1}
            value={roundsInput}
            onChange={e => setRoundsInput(e.target.value)}
            disabled={!backendOnline || roundsInfinite}
            style={{
              ...ROUND_INPUT,
              opacity: !backendOnline || roundsInfinite ? 0.45 : 1,
            }}
            title="Number of adversarial rounds (ignored when ∞ is checked)"
          />

          <button
            style={{
              ...BTN,
              background: !backendOnline || launching ? '#0a1a0a' : '#0a2a1a',
              color: !backendOnline || launching ? '#64748b' : '#00ff88',
              borderColor: !backendOnline || launching ? '#64748b' : '#00ff44',
              cursor: !backendOnline ? 'not-allowed' : 'pointer',
            }}
            onClick={startLive}
            disabled={!backendOnline || launching}
            title={!backendOnline ? 'Connect to arena-core first (see API offline hint)' : 'Start a new battle'}
          >
            {launching ? '⏳ …' : '▶ LAUNCH'}
          </button>

          {backendOnline && activeBattles.length > 0 && (
            <>
              <span style={DIV}>│</span>
              <span style={{ color: '#ffdd00', flexShrink: 0, fontSize: 8 }} title="Battles already running on the backend">
                ATTACH
              </span>
              <select
                value={attachId}
                onChange={e => setAttachId(e.target.value)}
                style={{ ...SEL, width: 130 }}
              >
                <option value="">— pick session —</option>
                {activeBattles.map(b => (
                  <option key={b.session_id} value={b.session_id}>
                    {b.session_id.slice(0, 8)} · {b.status} · r{b.current_round}
                    {b.max_rounds ? `/${b.max_rounds}` : '/∞'}
                  </option>
                ))}
              </select>
              <button
                style={{
                  ...BTN,
                  background: attachId ? '#1a1a0a' : '#0d1117',
                  color: attachId ? '#ffdd00' : '#475569',
                  borderColor: attachId ? '#ffdd00' : '#21262d',
                  cursor: attachId ? 'pointer' : 'not-allowed',
                }}
                onClick={attachToBattle}
                disabled={!attachId}
                title="Attach the visualizer to the selected backend session"
              >
                ⤴ ATTACH
              </button>
            </>
          )}
        </>
      )}

      {/* ── Battle controls (LIVE mode) ── */}
      {battleMode === 'live' && (
        <>
          <span style={{ color: '#00ff88', letterSpacing: '0.12em', flexShrink: 0 }}>⬤ LIVE</span>
          <span style={{ color: '#475569', fontFamily: 'monospace', flexShrink: 0 }}>
            {sessionId?.slice(0, 8)}
          </span>

          {/* Live round + score scoreboard */}
          {currentRound > 0 && (
            <>
              <span style={{ color: '#1e2a3a', flexShrink: 0 }}>│</span>
              <span style={{ color: '#94a3b8', flexShrink: 0, fontSize: 8, whiteSpace: 'nowrap' }}>
                R<span style={{ color: '#e2e8f0' }}>{currentRound}</span>
              </span>
              <span style={{ color: '#ff4444', flexShrink: 0, fontSize: 8 }}>
                RED <span style={{ color: '#e2e8f0' }}>{redWins}</span>
              </span>
              <span style={{ color: '#475569', flexShrink: 0, fontSize: 8 }}>:</span>
              <span style={{ color: '#4488ff', flexShrink: 0, fontSize: 8 }}>
                <span style={{ color: '#e2e8f0' }}>{blueWins}</span> BLUE
              </span>
            </>
          )}
          <span
            title={
              battleStatus === 'error'
                ? 'Adapter failures exceeded retry budget: scene frozen, not Pause. Check arena plus red, blue, target-ai, and judge logs.'
                : undefined
            }
            style={{
            color: battleStatus === 'running'  ? '#00ff88'
                 : battleStatus === 'paused'   ? '#ffdd00'
                 : battleStatus === 'stopping' ? '#ff8800'
                 : battleStatus === 'error'    ? '#ef4444'
                 : '#94a3b8',
            flexShrink: 0,
          }}>
            {battleStatus.toUpperCase()}
          </span>

          {battleStatus === 'running' && (
            <button style={{ ...BTN, background: '#1a1a0a', color: '#ffdd00', borderColor: '#ffdd00' }} onClick={pause}>
              ⏸ PAUSE
            </button>
          )}
          {battleStatus === 'paused' && (
            <button style={{ ...BTN, background: '#0a1a0a', color: '#00ff88', borderColor: '#00ff44' }} onClick={resume}>
              ▶ RESUME
            </button>
          )}
          {battleStatus === 'stopping' ? (
            <span style={{ fontSize: 9, fontFamily: 'monospace', color: '#ff8800', letterSpacing: '0.05em', flexShrink: 0 }}>
              ⟳ REPORTER COMPILING…
            </span>
          ) : (battleStatus === 'running' || battleStatus === 'paused') && (
            <button
              style={{ ...BTN, background: '#1a0a0a', color: '#ff4444', borderColor: '#ff4444' }}
              // eslint-disable-next-line @typescript-eslint/no-misused-promises
              onClick={stopLive}
            >
              ✕ STOP
            </button>
          )}
          {(battleStatus === 'complete' || battleStatus === 'error') && (
            <>
              <button
                style={{ ...BTN, background: '#0a2a1a', color: '#00ff88', borderColor: '#00ff44' }}
                title="Relaunch with the same red/blue, rounds, and evolution settings"
                onClick={runAgain}
              >
                ↻ RUN AGAIN
              </button>
              <button
                style={{ ...BTN, background: '#141821', color: '#94a3b8', borderColor: '#334155' }}
                title="Clear the arena and return to standby"
                onClick={newBattle}
              >
                ◇ NEW
              </button>
            </>
          )}
        </>
      )}

      {/* ── Status indicators ── */}
      <span style={DIV}>│</span>

      {!backendOnline && (
        <span style={{ color: '#ef4444', flexShrink: 0, fontSize: 8 }}>BACKEND OFFLINE</span>
      )}
      {backendOnline && battleMode === 'mock' && !error && (
        <span style={{ color: sceneFrozen ? '#94a3b8' : '#475569', flexShrink: 0, fontSize: 8 }}>
          {sceneFrozen ? '◼ MOCK·FROZEN' : 'MOCK·SIM'}
        </span>
      )}
      {error && (
        <span style={{ color: '#ef4444', flexShrink: 0, fontSize: 8 }}>{error}</span>
      )}

      {/* WS indicator */}
        <span
          style={{
          width: 6, height: 6, borderRadius: '50%',
            background: connected ? '#00ff88' : '#475569',
            boxShadow: connected ? '0 0 5px #00ff88' : 'none',
          flexShrink: 0,
          display: 'inline-block',
        }}
      />
    </div>
  )
}
