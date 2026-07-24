import { useState, type MouseEvent } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, type BattleSummary } from '@/lib/arenaApi'
import { connectLive } from '@/lib/connectLive'

// Status → dot color
const DOT: Record<string, string> = {
  running: '#00ff88', started: '#00ff88', paused: '#ffdd00',
  complete: '#64748b', stopped: '#94a3b8', error: '#ef4444',
}
const isActive = (s: string) => s === 'running' || s === 'paused' || s === 'started'

function Row({ b }: { b: BattleSummary }) {
  const sessionId = useGhostStore(s => s.sessionId)
  const watching = sessionId === b.session_id
  const dot = DOT[b.status] ?? '#64748b'
  const rounds = b.max_rounds == null || b.max_rounds === 0 ? '∞' : String(b.max_rounds)

  const attach = () => connectLive(b.session_id, b.red_service_id, b.blue_service_id, b.status)
  const pause = (e: MouseEvent) => { e.stopPropagation(); void arenaApi.pauseBattle(b.session_id).catch(() => null) }
  const resume = (e: MouseEvent) => { e.stopPropagation(); void arenaApi.resumeBattle(b.session_id).catch(() => null) }
  const stop = (e: MouseEvent) => { e.stopPropagation(); void arenaApi.stopBattle(b.session_id).catch(() => null) }

  const btn = {
    fontSize: 8, fontFamily: 'monospace', padding: '1px 4px', borderRadius: 2,
    cursor: 'pointer', background: '#0d1117', border: '1px solid #334155', color: '#94a3b8',
  } as const

  return (
    <div
      onClick={attach}
      title={`Attach to ${b.session_id}`}
      style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px',
        borderRadius: 3, cursor: 'pointer', fontFamily: 'monospace', fontSize: 9,
        background: watching ? '#0d2a1a' : '#12161f',
        border: `1px solid ${watching ? '#00ff44' : '#26303f'}`, marginBottom: 3,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0,
        boxShadow: isActive(b.status) ? `0 0 6px ${dot}` : 'none' }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: '#cbd5e1', display: 'flex', gap: 6 }}>
          <span>{b.session_id.slice(0, 8)}</span>
          {watching && <span style={{ color: '#00ff88' }}>▶ watching</span>}
        </div>
        <div style={{ color: '#64748b', fontSize: 8 }}>
          <span style={{ color: '#ef4444' }}>{b.red_wins}</span>
          :<span style={{ color: '#4488ff' }}>{b.blue_wins}</span>
          {' · '}r{b.current_round}/{rounds}{' · '}{b.status}
        </div>
      </div>
      {isActive(b.status) && (
        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
          {b.status === 'paused'
            ? <button style={btn} onClick={resume} title="Resume">▶</button>
            : <button style={btn} onClick={pause} title="Pause">⏸</button>}
          <button style={{ ...btn, color: '#ff6b6b', borderColor: '#ff444455' }} onClick={stop} title="Stop">✕</button>
        </div>
      )}
    </div>
  )
}

export default function BattleSidebar() {
  const battles = useGhostStore(s => s.battles)
  const backendOnline = useGhostStore(s => s.backendOnline)
  const [open, setOpen] = useState(false)

  const active = battles.filter(b => isActive(b.status))
  const recent = battles.filter(b => !isActive(b.status)).slice(0, 8)

  const accent = active.length ? '#00ff88' : '#64748b'

  // Left-edge side drawer: a thin vertical tab pinned below the top bar; click to
  // slide the panel open over the left side (full battle-area height). Wrapper is
  // click-through except its own controls, so it never blocks the scene when shut.
  return (
    <div style={{
      position: 'absolute', top: 56, left: 0, bottom: 40, zIndex: 130,
      display: 'flex', alignItems: 'stretch', fontFamily: 'monospace', pointerEvents: 'none',
    }}>
      {open ? (
        <div style={{
          pointerEvents: 'auto', width: 288, height: '100%', display: 'flex', flexDirection: 'column',
          background: '#0a0e17', borderRight: '1px solid #334155',
          boxShadow: '0 0 0 1px #000, 8px 0 32px #000000cc',
        }}>
          {/* header */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '6px 8px', borderBottom: '1px solid #1e293b',
          }}>
            <span style={{ fontSize: 10, letterSpacing: '0.1em', color: accent }}>
              ⚔ BATTLES{active.length ? ` · ${active.length} live` : ''}
            </span>
            <button onClick={() => setOpen(false)} title="Collapse"
              style={{ fontSize: 11, cursor: 'pointer', background: 'transparent', border: 'none', color: '#94a3b8' }}>
              ◀
            </button>
          </div>
          {/* list */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
            {!backendOnline && <div style={{ color: '#ef4444', fontSize: 9 }}>backend offline</div>}
            {backendOnline && battles.length === 0 && (
              <div style={{ color: '#475569', fontSize: 9 }}>no battles yet — press LAUNCH</div>
            )}
            {active.length > 0 && (
              <>
                <div style={{ color: '#7dd3fc', fontSize: 8, letterSpacing: '0.08em', margin: '2px 0 4px' }}>
                  RUNNING / PAUSED
                </div>
                {active.map(b => <Row key={b.session_id} b={b} />)}
              </>
            )}
            {recent.length > 0 && (
              <>
                <div style={{ color: '#64748b', fontSize: 8, letterSpacing: '0.08em', margin: '8px 0 4px' }}>
                  RECENT
                </div>
                {recent.map(b => <Row key={b.session_id} b={b} />)}
              </>
            )}
          </div>
        </div>
      ) : (
        // Collapsed: a slim vertical tab at the very left edge.
        <button
          onClick={() => setOpen(true)}
          title="Backend battles — attach / control"
          style={{
            pointerEvents: 'auto', alignSelf: 'flex-start', marginTop: 24,
            writingMode: 'vertical-rl', transform: 'rotate(180deg)',
            fontSize: 9, letterSpacing: '0.14em', padding: '10px 3px', cursor: 'pointer',
            borderRadius: '0 4px 4px 0',
            background: active.length ? '#0d2a1a' : '#0a0e17',
            border: `1px solid ${active.length ? '#00ff44' : '#21262d'}`, borderLeft: 'none',
            color: accent,
          }}
        >
          ⚔ BATTLES{active.length ? ` ${active.length}` : ''} ▶
        </button>
      )}
    </div>
  )
}
