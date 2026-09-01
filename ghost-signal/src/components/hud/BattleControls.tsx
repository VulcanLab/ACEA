/**
 * BattleControls — compact panel for launching live battles.
 * On load: auto-detects any running battle from the backend and connects
 * the WebSocket immediately, so the pixel scene always mirrors real events.
 */
import { useState, useEffect, useRef } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, ArenaHttpError, describeBattleStartFailure } from '@/lib/arenaApi'
import { arenaWsClient } from '@/lib/arenaWsClient'
import type { AgentState, LogEntry } from '@/types'
import type { BattleStatus } from '@/lib/store'

/** Connect to a live session — switches store to LIVE mode */
function connectLive(
  sessionId: string,
  redServiceId: string,
  blueServiceId: string,
  status: string,
  pushLog: (e: Omit<LogEntry, 'id' | 'timestamp'>) => void,
  setBattleMode: (m: 'mock' | 'live') => void,
  setBattleStatus: (s: BattleStatus) => void,
  setSessionId: (id: string) => void,
  setConnected: (v: boolean) => void,
) {
  useGhostStore.getState().clearMainScreenFeed()
  useGhostStore.getState().setLastVerdict(null)
  useGhostStore.getState().setSceneFrozen(false)
  setSessionId(sessionId)
  setBattleStatus((status as BattleStatus) ?? 'running')
  setBattleMode('live')
  setConnected(true)
  arenaWsClient.connect(sessionId)
  pushLog({
    agentId: 'system',
    message: `Auto-connected to live battle ${sessionId.slice(0, 8)} (red:${redServiceId.slice(0, 6)} vs blue:${blueServiceId.slice(0, 6)})`,
    state: 'acting' as AgentState,
  })
}

export default function BattleControls() {
  const {
    backendOnline, battleMode, battleStatus, services, sessionId,
    setBattleMode, setBattleStatus, setSessionId, setServices,
    setBackendOnline, setConnected, pushLog,
  } = useGhostStore()

  const [redId, setRedId] = useState('')
  const [blueId, setBlueId] = useState('')
  const [roundsInfinite, setRoundsInfinite] = useState(false)
  const [roundsInput, setRoundsInput] = useState('3')
  const [error, setError] = useState('')
  const autoConnectedRef = useRef(false)  // ensure we only auto-connect once

  // Probe backend health on mount + every 15s
  // Also: on first successful probe, check for an already-running battle and auto-connect
  useEffect(() => {
    let mounted = true
    const probe = async () => {
      try {
        await arenaApi.health()
        if (!mounted) return
        setBackendOnline(true)

        const svcs = await arenaApi.listServices()
        setServices(svcs)

        const red = svcs.find(s => s.type === 'red')
        const blue = svcs.find(s => s.type === 'blue')
        if (red && !redId) setRedId(red.id)
        if (blue && !blueId) setBlueId(blue.id)

        // Refresh the assisting-AI sprites with their real role + model
        // from the backend roster, so the board shows the actual collaborating
        // agents (not hardcoded labels). Best-effort; ignore if unavailable.
        try {
          const roster = await arenaApi.getAgentRoster()
          if (mounted) {
            const upd = useGhostStore.getState().updateAgent
            for (const a of [...roster.red, ...roster.blue]) {
              upd(a.id, { label: a.role, model: a.model })
            }
          }
        } catch { /* roster endpoint optional */ }

        // Auto-connect to a running battle:
        //   • First time online (autoConnectedRef false): always check
        //   • Subsequent polls: only if we're not already watching a live running session
        const currentMode    = useGhostStore.getState().battleMode
        const currentStatus  = useGhostStore.getState().battleStatus
        const currentSession = useGhostStore.getState().sessionId
        const needAutoConnect =
          !autoConnectedRef.current ||
          (currentMode === 'live' && currentStatus !== 'running' && currentStatus !== 'paused')

        if (needAutoConnect) {
          autoConnectedRef.current = true
          try {
            const battles = await arenaApi.listBattles()
            const active = battles.find(
              b => (b.status === 'running' || b.status === 'paused') && b.session_id !== currentSession
            )
            if (active) {
              connectLive(
                active.session_id,
                active.red_service_id,
                active.blue_service_id,
                active.status,
                pushLog, setBattleMode, setBattleStatus, setSessionId, setConnected,
              )
            }
          } catch {
            // listBattles not critical — ignore
          }
        }
      } catch {
        if (mounted) setBackendOnline(false)
      }
    }
    probe()
    const t = setInterval(probe, 15_000)
    return () => { mounted = false; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])


  const startLive = async () => {
    if (!redId || !blueId) { setError('Select red and blue services'); return }
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
    try {
      const res = await arenaApi.startBattle(redId, blueId, roundsArg)
      connectLive(
        res.session_id, redId, blueId, 'running',
        pushLog, setBattleMode, setBattleStatus, setSessionId, setConnected,
      )
    } catch (e) {
      if (e instanceof ArenaHttpError) {
        const d = e.detail as { code?: string; message?: string; failures?: Array<{ model: string; roles: string[]; error: string }> } | undefined
        if (d?.code === 'ADAPTER_CONFIG_MISSING') {
          useGhostStore.getState().openModal('adapter_config', d as { code: 'ADAPTER_CONFIG_MISSING'; message: string })
          return
        }
        if (d?.code === 'MODEL_UNREACHABLE') {
          // A configured model failed its pre-launch probe — open Model Health
          // so the operator sees exactly which model/role to fix in .env.
          const names = (d.failures ?? []).map(f => `${f.model} (${f.roles.join(',')})`).join('; ')
          useGhostStore.getState().pushLog({
            agentId: 'system',
            message: `Launch blocked — unreachable model(s): ${names || 'see Model Health'}`,
            state: 'failed',
          })
          useGhostStore.getState().openModal('model_health', {
            reason: d.message || 'A configured model did not return a valid response — fix it in .env and relaunch.',
          })
          setError('Launch blocked — a configured model is unreachable. See Model Health.')
          return
        }
      }
      setError(describeBattleStartFailure(e))
    }
  }

  const switchMock = async () => {
    if (
      battleMode === 'live' &&
      sessionId &&
      (battleStatus === 'running' || battleStatus === 'paused')
    ) {
      // Signal the backend to stop. The battle loop will finish the current
      // round (so execution_traces is clean), then emit battle.stopped via WS.
      // The WS handler animates agents + reporter, fetches the narrative, then
      // after ~STOP_TO_MOCK_GRACE_MS (see arenaWsClient) resets to frozen mock mode.
      setBattleStatus('stopping')
      try {
        await arenaApi.stopBattle(sessionId)
      } catch {
        // Best-effort: even if the HTTP call fails, the in-memory flag may
        // still get set. Log it but don't block the UI.
        pushLog({ agentId: 'system', message: 'Stop request failed — retrying…', state: 'failed' })
      }
      // WS remains connected to receive battle.stopped.
      return
    }

    // Force-switch: already stopped / complete / error, or no live session.
    arenaWsClient.disconnect()
    setSessionId(null)
    setBattleMode('mock')
    setBattleStatus('idle')
    setConnected(false)
    pushLog({ agentId: 'system', message: 'Returned to idle — launch a battle to begin.', state: 'idle' })
  }

  const pause = async () => {
    if (!sessionId) return
    await arenaApi.pauseBattle(sessionId)
    setBattleStatus('paused')
  }

  const resume = async () => {
    if (!sessionId) return
    await arenaApi.resumeBattle(sessionId)
    setBattleStatus('running')
  }

  const PANEL_STYLE: React.CSSProperties = {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: 32,
    background: 'linear-gradient(90deg,#0d1117 0%,#0a1020 100%)',
    borderTop: '1px solid #1e2a3a',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 12,
    paddingRight: 12,
    zIndex: 200,
    fontFamily: 'monospace',
    fontSize: 9,
  }

  const BTN: React.CSSProperties = {
    padding: '2px 8px',
    borderRadius: 3,
    border: '1px solid',
    cursor: 'pointer',
    fontSize: 9,
    fontFamily: 'monospace',
    letterSpacing: '0.06em',
  }

  const SEL: React.CSSProperties = {
    background: '#0d1117',
    color: '#94a3b8',
    border: '1px solid #21262d',
    borderRadius: 3,
    fontSize: 9,
    fontFamily: 'monospace',
    padding: '1px 4px',
    height: 20,
  }

  const ROUND_LABEL: React.CSSProperties = {
    ...SEL,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    cursor: 'pointer',
  }

  const ROUND_INPUT: React.CSSProperties = {
    ...SEL,
    width: 48,
    cursor: 'text',
  }

  return (
    <div style={PANEL_STYLE}>
      {/* Mode badge */}
      <span style={{ color: battleMode === 'live' ? '#00ff88' : '#475569', letterSpacing: '0.12em' }}>
        {battleMode === 'live' ? '⬤ LIVE' : '◌ MOCK'}
      </span>

      <span style={{ color: '#1e2a3a' }}>│</span>

      {/* Backend status */}
      <span style={{ color: backendOnline ? '#64748b' : '#334155' }}>
        BACKEND {backendOnline ? <span style={{ color: '#00ff88' }}>ONLINE</span> : <span style={{ color: '#ef4444' }}>OFFLINE</span>}
      </span>

      {backendOnline && battleMode === 'mock' && (
        <>
          <span style={{ color: '#1e2a3a' }}>│</span>

          {/* Red service selector */}
          <span style={{ color: '#ff4444' }}>RED</span>
          <select value={redId} onChange={e => setRedId(e.target.value)} style={SEL}>
            <option value="">—</option>
            {services.filter(s => s.type === 'red').map(s => (
              <option key={s.id} value={s.id}>{s.name || s.id}</option>
            ))}
          </select>

          {/* Blue service selector */}
          <span style={{ color: '#4488ff' }}>BLUE</span>
          <select value={blueId} onChange={e => setBlueId(e.target.value)} style={SEL}>
            <option value="">—</option>
            {services.filter(s => s.type === 'blue').map(s => (
              <option key={s.id} value={s.id}>{s.name || s.id}</option>
            ))}
          </select>

          {/* Rounds: custom count + optional infinite */}
          <span style={{ color: '#475569' }}>RND</span>
          <label style={ROUND_LABEL} title="∞ applies only to the next LAUNCH. LIVE·ERROR means adapter retries exhausted (not Pause); check service logs.">
            <input
              type="checkbox"
              checked={roundsInfinite}
              onChange={e => setRoundsInfinite(e.target.checked)}
              style={{ width: 10, height: 10, accentColor: '#00ff88' }}
            />
            <span>∞</span>
          </label>
          <input
            type="number"
            min={1}
            step={1}
            value={roundsInput}
            onChange={e => setRoundsInput(e.target.value)}
            disabled={roundsInfinite}
            style={{
              ...ROUND_INPUT,
              opacity: roundsInfinite ? 0.45 : 1,
            }}
            title="Round count (ignored when ∞ is checked)"
          />

          <button
            style={{ ...BTN, background: '#0a2a1a', color: '#00ff88', borderColor: '#00ff44' }}
            onClick={startLive}
          >
            ▶ LAUNCH
          </button>
        </>
      )}

      {battleMode === 'live' && (
        <>
          <span style={{ color: '#1e2a3a' }}>│</span>
          <span style={{ color: '#475569' }}>
            {sessionId?.slice(0, 8)}
          </span>
          <span
            title={
              battleStatus === 'error'
                ? 'Adapter failures exceeded retry budget: scene frozen, not Pause. Check arena and adapter logs.'
                : undefined
            }
            style={{
            color: battleStatus === 'running'  ? '#00ff88'
                 : battleStatus === 'paused'   ? '#ffdd00'
                 : battleStatus === 'stopping' ? '#ff8800'
                 : battleStatus === 'error'    ? '#ef4444'
                 : '#94a3b8'
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

          {/* STOP — only active when running or paused; disabled while stopping */}
          {battleStatus === 'stopping' ? (
            <span style={{ fontSize: 9, fontFamily: 'monospace', color: '#ff8800', letterSpacing: '0.06em' }}>
              ⟳ REPORTER COMPILING…
            </span>
          ) : (battleStatus === 'running' || battleStatus === 'paused') && (
            <button
              style={{ ...BTN, background: '#1a0a0a', color: '#ff4444', borderColor: '#ff4444' }}
              // eslint-disable-next-line @typescript-eslint/no-misused-promises
              onClick={switchMock}
            >
            ✕ STOP
          </button>
          )}
        </>
      )}

      {/* spacer */}
      <span style={{ flex: 1 }} />

      {error && <span style={{ color: '#ef4444' }}>{error}</span>}

      {battleMode === 'mock' && !backendOnline && (
        <span style={{ color: '#64748b', letterSpacing: '0.04em' }}>
          Backend offline — scene frozen, no mock autoplay
        </span>
      )}
    </div>
  )
}
