import { useMemo, useState } from 'react'
import type { BattleDay } from '@/lib/arenaApi'

const MONO = "'JetBrains Mono', monospace"
const WEEKDAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

/** Local YYYY-MM-DD for a Date — never via toISOString, which shifts the day
 *  for anyone east or west of UTC and would highlight the wrong cell. */
const key = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/** Fixed month names rather than the browser locale: the weekday row and every other
 *  label in this panel are ASCII terminal type, and a locale-formatted heading put a
 *  different script in the middle of them. */
const monthLabel = (year: number, month: number) => `${MONTHS[month]} ${year}`

/** Monday-first offset of the 1st, so the grid lines up with the weekday header. */
const leadingBlanks = (year: number, month: number) => (new Date(year, month, 1).getDay() + 6) % 7

const daysInMonth = (year: number, month: number) => new Date(year, month + 1, 0).getDate()

/**
 * A month grid over the days that have battles.
 *
 * The list this replaces was one row per day, newest first. Fine for a week; by the
 * time the platform had run for a month it was a 25-row scroll where finding a date
 * meant reading every label, and it had no way to show that a day in between simply
 * had nothing. A calendar answers "which days have runs" at a glance, keeps a date in
 * the same place every time you look for it, and shows the gaps as gaps.
 *
 * Density is carried by cell tint rather than a number, so the eye can pick out the
 * heavy days; the count is still in the tooltip and under the selection.
 */
export default function BattleCalendar({
  days, selected, onSelect,
}: {
  days: BattleDay[]
  selected: string | null
  onSelect: (date: string | null) => void
}) {
  const byDate = useMemo(() => {
    const map = new Map<string, BattleDay>()
    for (const d of days) map.set(d.date, d)
    return map
  }, [days])

  const busiest = useMemo(
    () => days.reduce((max, d) => Math.max(max, d.battles), 0) || 1, [days])

  // Open on the month of the newest day that has data, so the panel is useful the
  // moment it appears rather than showing an empty current month.
  const initial = useMemo(() => {
    const newest = days[0]?.date
    const d = newest ? new Date(`${newest}T00:00:00`) : new Date()
    return { year: d.getFullYear(), month: d.getMonth() }
  }, [days])
  const [view, setView] = useState(initial)

  const today = key(new Date())
  const total = daysInMonth(view.year, view.month)
  const blanks = leadingBlanks(view.year, view.month)

  const step = (delta: number) => {
    const d = new Date(view.year, view.month + delta, 1)
    setView({ year: d.getFullYear(), month: d.getMonth() })
  }

  const monthHasData = days.some(d => d.date.startsWith(
    `${view.year}-${String(view.month + 1).padStart(2, '0')}`))

  const cell = (date: string | null, label: string) => {
    if (!date) return <div key={label} />
    const day = byDate.get(date)
    const on = selected === date
    const isToday = date === today
    const share = day ? 0.14 + 0.5 * (day.battles / busiest) : 0
    const wins = day ? day.red_wins + day.blue_wins : 0
    const redShare = wins ? (day!.red_wins / wins) * 100 : 0

    return (
      <button
        key={date}
        type="button"
        // Not `disabled`: a disabled button is not a hit-test target, so the click
        // fell through the drawer and landed on the arena canvas behind it. An empty
        // day absorbs its own click and does nothing with it.
        aria-disabled={!day}
        onClick={() => { if (day) onSelect(date) }}
        title={day
          ? `${date} — ${day.battles} battle(s), ${day.complete} complete · red ${day.red_wins} : blue ${day.blue_wins}`
          : `${date} — nothing ran`}
        style={{
          position: 'relative', padding: '3px 0 5px', minHeight: 26,
          fontFamily: MONO, fontSize: 9, lineHeight: 1.1,
          cursor: day ? 'pointer' : 'default',
          color: on ? '#e2e8f0' : day ? '#cbd5e1' : '#334155',
          background: on ? '#0d2a1a' : day ? `rgba(56,189,248,${share})` : 'transparent',
          border: `1px solid ${on ? '#00ff44' : isToday ? '#334155' : 'transparent'}`,
          borderRadius: 2,
        }}
      >
        {label}
        {/* Who won that day, as a hairline. Absent when nothing was decided, so an
            empty bar never reads as a zero. */}
        {wins > 0 && (
          <span style={{
            position: 'absolute', left: 2, right: 2, bottom: 1, height: 2,
            display: 'flex', borderRadius: 1, overflow: 'hidden', background: '#1e293b',
          }}>
            <span style={{ width: `${redShare}%`, background: '#ef4444' }} />
            <span style={{ flex: 1, background: '#4488ff' }} />
          </span>
        )}
      </button>
    )
  }

  const cells = [
    ...Array.from({ length: blanks }, (_, i) => cell(null, `blank-${i}`)),
    ...Array.from({ length: total }, (_, i) => {
      const d = new Date(view.year, view.month, i + 1)
      return cell(key(d), String(i + 1))
    }),
  ]

  const btn = {
    background: 'transparent', border: 'none', color: '#64748b',
    cursor: 'pointer', fontFamily: MONO, fontSize: 11, padding: '0 4px',
  } as const

  return (
    <div
      // The calendar owns every pointer event inside its own bounds — including the
      // gaps between cells, which are not covered by any button.
      onPointerDown={e => e.stopPropagation()}
      onClick={e => e.stopPropagation()}
      style={{ padding: '6px 8px', borderBottom: '1px solid #1e293b' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button style={btn} onClick={() => step(-1)} title="Previous month">◀</button>
        <span style={{
          fontFamily: MONO, fontSize: 9, letterSpacing: '0.1em',
          color: monthHasData ? '#7dd3fc' : '#475569',
        }}>
          {monthLabel(view.year, view.month)}
        </span>
        <button style={btn} onClick={() => step(1)} title="Next month">▶</button>
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2, marginTop: 6,
      }}>
        {WEEKDAYS.map((w, i) => (
          <div key={`${w}${i}`} style={{
            fontFamily: MONO, fontSize: 8, color: '#475569', textAlign: 'center',
          }}>{w}</div>
        ))}
        {cells}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
        <button
          onClick={() => onSelect(null)}
          style={{
            ...btn, fontSize: 8, letterSpacing: '0.08em',
            color: selected === null ? '#00ff88' : '#64748b',
          }}
        >
          {selected === null ? '● LIVE' : '○ LIVE'}
        </button>
        {selected && (
          <span style={{ fontFamily: MONO, fontSize: 8, color: '#7dd3fc' }}>
            {selected}
            {byDate.get(selected) && ` · ${byDate.get(selected)!.complete}/${byDate.get(selected)!.battles} complete`}
          </span>
        )}
      </div>
    </div>
  )
}
