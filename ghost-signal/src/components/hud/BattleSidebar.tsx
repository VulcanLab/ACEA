import { useEffect, useMemo, useState, type MouseEvent } from 'react'
import { useGhostStore, type BattleStatus } from '@/lib/store'
import {
  arenaApi, reportUrl,
  type BattleDay, type BattleHistoryEntry, type BattleSummary,
} from '@/lib/arenaApi'
import { connectLive } from '@/lib/connectLive'
import BattleCalendar from './BattleCalendar'

// Status → dot color
const DOT: Record<string, string> = {
  running: '#00ff88', started: '#00ff88', paused: '#ffdd00',
  complete: '#64748b', stopped: '#94a3b8', error: '#ef4444',
}
const isActive = (s: string) => s === 'running' || s === 'paused' || s === 'started'

const MONO = "'JetBrains Mono', monospace"

/** Short service label: ids are long and the drawer is narrow, but which projects
 *  fought is the first thing you want to know when reading a past run. */
const shortId = (id: string) => (id.length > 12 ? `${id.slice(0, 10)}…` : id)

/** "14:32" in the viewer's own timezone — a history row is read by a person. */
const clockOf = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false })
    : '--:--'

const dayLabel = (date: string) => {
  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 864e5).toISOString().slice(0, 10)
  if (date === today) return 'TODAY'
  if (date === yesterday) return 'YESTERDAY'
  const [, m, d] = date.split('-')
  return `${m}/${d}`
}

function Row({ b, entry }: { b: BattleSummary; entry?: BattleHistoryEntry }) {
  const sessionId = useGhostStore(s => s.sessionId)
  const watching = sessionId === b.session_id
  const dot = DOT[b.status] ?? '#64748b'
  const rounds = b.max_rounds == null || b.max_rounds === 0 ? '∞' : String(b.max_rounds)
  // A battle read back from storage after a restart has no live state to attach to.
  const attachable = entry ? entry.live : true

  const attach = () => {
    if (!attachable) return
    connectLive(b.session_id, b.red_service_id, b.blue_service_id, b.status)
  }

  // These controls act on ANY battle in the list, not necessarily the one on screen.
  // The backend call alone is not enough for the one being watched: the arena's
  // animation is driven by local battle status, so pausing from here stopped the battle
  // while the scene carried on as though nothing had happened, and only the top-bar
  // control appeared to work. Mirror the status locally, but ONLY when this row is the
  // battle being watched — otherwise pausing a background run would freeze the scene of
  // a different one.
  const mirror = (status: BattleStatus) => {
    if (watching) useGhostStore.getState().setBattleStatus(status)
  }

  const pause = (e: MouseEvent) => {
    e.stopPropagation()
    void arenaApi.pauseBattle(b.session_id).then(() => mirror('paused')).catch(() => null)
  }
  const resume = (e: MouseEvent) => {
    e.stopPropagation()
    void arenaApi.resumeBattle(b.session_id).then(() => mirror('running')).catch(() => null)
  }
  const stop = (e: MouseEvent) => {
    e.stopPropagation()
    // 'stopping', not 'stopped': the run is winding down and the reporter still has
    // work to do, which is the same state the top-bar control uses.
    void arenaApi.stopBattle(b.session_id).then(() => mirror('stopping')).catch(() => null)
  }
  const openReport = (e: MouseEvent) => {
    e.stopPropagation()
    window.open(reportUrl(b.session_id), '_blank', 'noopener')
  }

  const btn = {
    fontSize: 8, fontFamily: MONO, padding: '1px 4px', borderRadius: 2,
    cursor: 'pointer', background: '#0d1117', border: '1px solid #334155', color: '#94a3b8',
  } as const

  const total = b.red_wins + b.blue_wins
  const redShare = total ? (b.red_wins / total) * 100 : 0

  return (
    <div
      onClick={attach}
      title={attachable ? `Attach to ${b.session_id}` : `${b.session_id} — finished; open its report`}
      style={{
        padding: '4px 6px 5px', borderRadius: 3, marginBottom: 3,
        cursor: attachable ? 'pointer' : 'default', fontFamily: MONO, fontSize: 9,
        background: watching ? '#0d2a1a' : '#12161f',
        border: `1px solid ${watching ? '#00ff44' : '#26303f'}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0,
          boxShadow: isActive(b.status) ? `0 0 6px ${dot}` : 'none' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: '#cbd5e1', display: 'flex', gap: 6, alignItems: 'baseline' }}>
            <span>{b.session_id.slice(0, 8)}</span>
            {watching && <span style={{ color: '#00ff88' }}>▶ watching</span>}
            {entry && !watching && (
              <span style={{ color: '#475569', fontSize: 8 }}>{clockOf(entry.created_at)}</span>
            )}
          </div>
          <div style={{ color: '#64748b', fontSize: 8 }}>
            <span style={{ color: '#ef4444' }}>{b.red_wins}</span>
            :<span style={{ color: '#4488ff' }}>{b.blue_wins}</span>
            {' · '}r{b.current_round}/{rounds}{' · '}{b.status}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
          {isActive(b.status) && (
            b.status === 'paused'
              ? <button style={btn} onClick={resume} title="Resume">▶</button>
              : <button style={btn} onClick={pause} title="Pause">⏸</button>
          )}
          {isActive(b.status) && (
            <button style={{ ...btn, color: '#ff6b6b', borderColor: '#ff444455' }} onClick={stop} title="Stop">✕</button>
          )}
          {!isActive(b.status) && (
            <button style={{ ...btn, color: '#7dd3fc', borderColor: '#38bdf855' }}
              onClick={openReport} title="Open the printable report">⧉</button>
          )}
        </div>
      </div>
      {/* Who won, at a glance: a hairline the width of the row. Rendered only for a
          decided battle, so a run that has not scored yet shows nothing rather than
          an empty bar that reads like a zero. */}
      {total > 0 && (
        <div style={{ display: 'flex', height: 2, marginTop: 4, background: '#1e293b', borderRadius: 1, overflow: 'hidden' }}>
          <div style={{ width: `${redShare}%`, background: '#ef4444' }} />
          <div style={{ flex: 1, background: '#4488ff' }} />
        </div>
      )}
    </div>
  )
}

export default function BattleSidebar() {
  const battles = useGhostStore(s => s.battles)
  const backendOnline = useGhostStore(s => s.backendOnline)
  const [open, setOpen] = useState(false)

  // Tell the scene to stop taking pointer input while the drawer covers it. Stopping
  // propagation on the drawer's own DOM is not enough: the arena listens on its
  // canvas, and a click that lands on a gap would still reach it.
  useEffect(() => {
    useGhostStore.getState().setDrawerOpen(open)
    return () => useGhostStore.getState().setDrawerOpen(false)
  }, [open])
  // null = the live view (what this drawer used to be); a date = that day, read from storage.
  const [day, setDay] = useState<string | null>(null)
  const [days, setDays] = useState<BattleDay[]>([])
  const [history, setHistory] = useState<BattleHistoryEntry[]>([])
  const [loading, setLoading] = useState(false)

  const active = useMemo(() => battles.filter(b => isActive(b.status)), [battles])
  const liveRest = useMemo(() => battles.filter(b => !isActive(b.status)).slice(0, 8), [battles])

  // The day list is small and changes only when a battle starts; refresh it when the
  // drawer opens and whenever a run finishes (battles.length is the cheap proxy).
  useEffect(() => {
    if (!open || !backendOnline) return
    let cancelled = false
    void arenaApi.listBattleDays()
      .then(d => { if (!cancelled) setDays(d) })
      .catch(() => { if (!cancelled) setDays([]) })
    return () => { cancelled = true }
  }, [open, backendOnline, battles.length])

  useEffect(() => {
    if (!open || day === null) return
    let cancelled = false
    setLoading(true)
    void arenaApi.listBattleHistory(day)
      .then(rows => { if (!cancelled) setHistory(rows) })
      .catch(() => { if (!cancelled) setHistory([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, day])

  const accent = active.length ? '#00ff88' : '#64748b'
  const selectedDay = days.find(d => d.date === day)

  // Left-edge side drawer: a thin vertical tab pinned below the top bar; click to
  // slide the panel open over the left side (full battle-area height). Wrapper is
  // click-through except its own controls, so it never blocks the scene when shut.
  return (
    <div style={{
      position: 'absolute', top: 56, left: 0, bottom: 40, zIndex: 130,
      display: 'flex', alignItems: 'stretch', fontFamily: MONO, pointerEvents: 'none',
    }}>
      {open ? (
        <div
          // Everything inside the drawer belongs to the drawer. Without this a click
          // that misses a control — padding, a gap, a disabled-looking cell — reaches
          // the Phaser canvas behind and opens a panel the user never asked for.
          onPointerDown={e => e.stopPropagation()}
          onPointerUp={e => e.stopPropagation()}
          onClick={e => e.stopPropagation()}
          onWheel={e => e.stopPropagation()}
          style={{
          pointerEvents: 'auto', width: 320, height: '100%', display: 'flex', flexDirection: 'column',
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

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <BattleCalendar days={days} selected={day} onSelect={setDay} />

            <div style={{ flex: 1, overflowY: 'auto', padding: 8, minWidth: 0 }}>
              {!backendOnline && <div style={{ color: '#ef4444', fontSize: 9 }}>backend offline</div>}

              {backendOnline && day === null && (
                <>
                  {battles.length === 0 && (
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
                  {liveRest.length > 0 && (
                    <>
                      <div style={{ color: '#64748b', fontSize: 8, letterSpacing: '0.08em', margin: '8px 0 4px' }}>
                        THIS SESSION
                      </div>
                      {liveRest.map(b => <Row key={b.session_id} b={b} />)}
                    </>
                  )}
                  {days.length > 0 && (
                    <div style={{ color: '#475569', fontSize: 8, marginTop: 10, lineHeight: 1.6 }}>
                      pick a day above for earlier runs — they outlive this process
                    </div>
                  )}
                </>
              )}

              {backendOnline && day !== null && (
                <>
                  <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                    color: '#7dd3fc', fontSize: 8, letterSpacing: '0.08em', margin: '2px 0 6px',
                  }}>
                    <span>{day}</span>
                    {selectedDay && (
                      <span style={{ color: '#475569' }}>
                        {selectedDay.complete}/{selectedDay.battles} complete
                      </span>
                    )}
                  </div>
                  {loading && <div style={{ color: '#475569', fontSize: 9 }}>reading…</div>}
                  {!loading && history.length === 0 && (
                    <div style={{ color: '#475569', fontSize: 9 }}>nothing recorded on this date</div>
                  )}
                  {history.map(h => <Row key={h.session_id} b={h} entry={h} />)}
                  {!loading && history.length > 0 && (
                    <div style={{ color: '#475569', fontSize: 8, marginTop: 8, lineHeight: 1.6 }}>
                      ⧉ opens the printable report · rows from a finished process cannot be attached
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      ) : (
        // Collapsed: a slim vertical tab at the very left edge.
        <button
          onClick={() => setOpen(true)}
          title="Backend battles — attach / control / history"
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
