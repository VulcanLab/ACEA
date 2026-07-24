import { useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useGhostStore } from '@/lib/store'
import type { NarrativeReport, ZoneInsights } from '@/types'
import { arenaApi } from '@/lib/arenaApi'
import { augmentNarrativeStatsFromArena } from '@/lib/reportStatsFallback'

// ── Tab definition ────────────────────────────────────────────────────────────

type TabId = 'overview' | 'red' | 'blue' | 'target' | 'full'

const TABS: Array<{ id: TabId; label: string; color: string }> = [
  { id: 'overview', label: 'OVERVIEW',    color: '#00ff88' },
  { id: 'red',      label: 'RED TEAM',    color: '#ff4444' },
  { id: 'blue',     label: 'BLUE TEAM',   color: '#4488ff' },
  { id: 'target',   label: 'TARGET AI',   color: '#cc44ff' },
  { id: 'full',     label: 'FULL REPORT', color: '#ffdd00' },
]

// Section keywords that map to tabs — first match wins
const SECTION_MAP: Record<TabId, string[]> = {
  overview: ['executive summary', 'strategic assessment', 'recommendations'],
  red:      ['red team', 'attack analysis', 'attack deep', 'offensive'],
  blue:     ['blue team', 'defense analysis', 'defense deep', 'defensive'],
  target:   ['target ai', 'target behavior', 'victim'],
  full:     [],
}

/** Split markdown into named sections keyed by ## heading */
function splitSections(md: string): Record<string, string> {
  const result: Record<string, string> = {}
  let currentKey = '__preamble__'
  let buffer: string[] = []

  for (const line of md.split('\n')) {
    if (line.startsWith('## ')) {
      result[currentKey] = buffer.join('\n')
      currentKey = line.slice(3).trim().toLowerCase()
      buffer = [`## ${line.slice(3).trim()}`]
    } else {
      buffer.push(line)
    }
  }
  result[currentKey] = buffer.join('\n')
  return result
}

/** Gather sections that match a tab's keywords */
function tabContent(sections: Record<string, string>, tabId: TabId, fullMd: string): string {
  if (tabId === 'full') return fullMd
  const keywords = SECTION_MAP[tabId]
  const matched = Object.entries(sections)
    .filter(([key]) => keywords.some(kw => key.includes(kw)))
    .map(([, content]) => content)
  return matched.length ? matched.join('\n\n---\n\n') : '*No data for this section.*'
}

// ── Zone config ───────────────────────────────────────────────────────────────

const ZONE_ROWS: Array<{ key: keyof ZoneInsights; label: string; color: string }> = [
  { key: 'red_team',        label: 'Red Team',  color: '#ff4444' },
  { key: 'target_ai',       label: 'Target AI', color: '#cc44ff' },
  { key: 'blue_team',       label: 'Blue Team', color: '#4488ff' },
  { key: 'judge',           label: 'Judge',     color: '#ffdd00' },
  { key: 'overall_summary', label: 'Summary',   color: '#00ff88' },
]

// ── Markdown renderer (pixel-art dark theme) ──────────────────────────────────

const MD_COMPONENTS: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  h1: ({ children }) => (
    <h1 style={{ color: '#00ff88', fontSize: 11, fontFamily: 'monospace', letterSpacing: '0.12em', marginTop: 12, marginBottom: 4, fontWeight: 700 }}>
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 style={{ color: '#00ff88', fontSize: 10, fontFamily: 'monospace', letterSpacing: '0.12em', marginTop: 10, marginBottom: 3, fontWeight: 700, borderBottom: '1px solid #00ff8830', paddingBottom: 2 }}>
      {String(children).toUpperCase()}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 style={{ color: '#4488ff', fontSize: 9, fontFamily: 'monospace', letterSpacing: '0.1em', marginTop: 8, marginBottom: 2, fontWeight: 600 }}>
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 style={{ color: '#cc44ff', fontSize: 9, fontFamily: 'monospace', marginTop: 6, marginBottom: 2 }}>
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p style={{ color: '#94a3b8', fontSize: 9, fontFamily: 'monospace', lineHeight: 1.6, marginBottom: 6 }}>
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong style={{ color: '#cbd5e1', fontWeight: 700 }}>{children}</strong>
  ),
  em: ({ children }) => (
    <em style={{ color: '#94a3b8', fontStyle: 'italic' }}>{children}</em>
  ),
  li: ({ children }) => (
    <li style={{ color: '#94a3b8', fontSize: 9, fontFamily: 'monospace', marginBottom: 3, lineHeight: 1.5 }}>
      {children}
    </li>
  ),
  ul: ({ children }) => (
    <ul style={{ paddingLeft: 14, marginBottom: 6 }}>{children}</ul>
  ),
  ol: ({ children }) => (
    <ol style={{ paddingLeft: 14, marginBottom: 6 }}>{children}</ol>
  ),
  hr: () => (
    <hr style={{ border: 'none', borderTop: '1px solid #00ff8820', margin: '8px 0' }} />
  ),
  code: ({ children, className }) => {
    const isBlock = className?.includes('language-')
    if (isBlock) {
      return (
        <pre style={{ background: '#0d1117', border: '1px solid #21262d', borderRadius: 3, padding: '6px 8px', overflowX: 'auto', marginBottom: 6 }}>
          <code style={{ color: '#00ff88', fontSize: 8, fontFamily: 'monospace' }}>{children}</code>
        </pre>
      )
    }
    return <code style={{ color: '#cc44ff', fontSize: 8, fontFamily: 'monospace', background: '#1a1a2e', padding: '1px 4px', borderRadius: 2 }}>{children}</code>
  },
  blockquote: ({ children }) => (
    <blockquote style={{ borderLeft: '2px solid #4488ff', paddingLeft: 8, marginLeft: 0, marginBottom: 6 }}>{children}</blockquote>
  ),
  table: ({ children }) => (
    <div style={{ overflowX: 'auto', marginBottom: 8 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 8, fontFamily: 'monospace' }}>
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead style={{ background: '#00ff880d', borderBottom: '1px solid #00ff8840' }}>{children}</thead>
  ),
  th: ({ children }) => (
    <th style={{ color: '#00ff88', padding: '3px 8px', textAlign: 'left', letterSpacing: '0.06em', fontWeight: 700, whiteSpace: 'nowrap' }}>
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td style={{ color: '#94a3b8', padding: '2px 8px', borderBottom: '1px solid #21262d', verticalAlign: 'top' }}>
      {children}
    </td>
  ),
  tr: ({ children }) => (
    <tr style={{ transition: 'background 0.1s' }}>{children}</tr>
  ),
}

// ── Stat bar ──────────────────────────────────────────────────────────────────

function StatBar({ report }: { report: NarrativeReport }) {
  const s = report.statistics
  const pct = (v: number) => `${(v * 100).toFixed(0)}%`
  const items = [
    { label: 'Rounds',    value: String(s.total_rounds) },
    { label: 'Red wins',  value: String(s.red_wins),  color: '#ff4444' },
    { label: 'Blue wins', value: String(s.blue_wins), color: '#4488ff' },
    { label: 'ASR',       value: pct(s.attack_success_rate), color: s.attack_success_rate > 0.5 ? '#ff4444' : '#00ff88' },
    { label: 'DR',        value: pct(s.defense_rate),        color: s.defense_rate > 0.5 ? '#00ff88' : '#ff4444' },
    { label: 'Avg harm',  value: s.avg_harmfulness_score.toFixed(3), color: s.avg_harmfulness_score > 0.5 ? '#ff8800' : '#64748b' },
    { label: 'Max harm',  value: s.max_harmfulness_score.toFixed(3), color: s.max_harmfulness_score > 0.7 ? '#ff4444' : '#64748b' },
  ]
  return (
    <div
      className="flex flex-wrap gap-x-5 gap-y-0.5 px-3 py-1.5 text-[8px] font-mono"
      style={{ borderBottom: '1px solid #00ff8818', background: '#00ff880a' }}
    >
      {items.map(({ label, value, color }) => (
        <span key={label} style={{ color: '#4a6a5a' }}>
          {label}:{' '}
          <span style={{ color: color ?? '#00ff88' }}>{value}</span>
        </span>
      ))}
    </div>
  )
}

// ── Zone intelligence panel ───────────────────────────────────────────────────

function ZoneIntelligence({ insights }: { insights: ZoneInsights }) {
  return (
    <div className="px-3 py-2" style={{ borderBottom: '1px solid #00ff8818' }}>
      <div className="text-[7px] font-mono mb-1.5" style={{ color: '#00ff8860', letterSpacing: '0.15em' }}>
        ZONE INTELLIGENCE SNAPSHOT
      </div>
      {ZONE_ROWS.map(({ key, label, color }) => (
        <div key={key} className="flex gap-2 mb-0.5 items-start">
          <span className="text-[8px] font-mono shrink-0 w-[52px]" style={{ color: color + '99' }}>
            {label}
          </span>
          <span className="text-[8px] font-mono leading-tight" style={{ color: '#94a3b8' }}>
            {insights[key] || '—'}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function TabBar({ active, onChange }: { active: TabId; onChange: (t: TabId) => void }) {
          return (
            <div
      className="flex gap-0 shrink-0 overflow-x-auto"
      style={{ borderBottom: '1px solid #00ff8820' }}
    >
      {TABS.map(({ id, label, color }) => (
        <button
          key={id}
          onClick={() => onChange(id)}
          style={{
            fontSize: 7,
            fontFamily: 'monospace',
            padding: '4px 10px',
            letterSpacing: '0.08em',
            background: active === id ? color + '18' : 'transparent',
            color: active === id ? color : '#334155',
            borderBottom: active === id ? `2px solid ${color}` : '2px solid transparent',
            borderTop: 'none',
            borderLeft: 'none',
            borderRight: 'none',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            transition: 'all 0.15s',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Fallback when no report yet ───────────────────────────────────────────────

function NoReport({ onRetry, loading }: { onRetry: () => void; loading: boolean }) {
  const reportUrl = (import.meta.env.VITE_REPORT_URL as string | undefined) ?? 'http://localhost:8005'
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4" style={{ color: '#334155', fontFamily: 'monospace' }}>
      <div style={{ fontSize: 10 }}>
        {loading ? 'SCRIBE COMPILING REPORT…' : 'REPORT NOT YET RECEIVED'}
      </div>
      <div className="text-center max-w-[360px]" style={{ fontSize: 8, color: '#1e293b', lineHeight: 1.7 }}>
        Report composer: <span style={{ color: '#334155' }}>{reportUrl}</span>
        <br />
        If this stays blank, check that report-composer is running and reachable.
        <br />
        Browser DevTools → Console will show the exact fetch error.
      </div>
      <button
        onClick={onRetry}
        disabled={loading}
        style={{
          fontSize: 9, fontFamily: 'monospace', padding: '4px 12px',
          background: loading ? 'transparent' : '#00ff8810',
          color: loading ? '#1e293b' : '#00ff88',
          border: `1px solid ${loading ? '#1e293b' : '#00ff8840'}`,
          borderRadius: 3, cursor: loading ? 'default' : 'pointer',
        }}
      >
        {loading ? '⟳ fetching…' : '↺ retry'}
      </button>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PrinterModal() {
  const { closeModal, lastReport, sessionId, setLastReport, setZoneInsights } = useGhostStore()
  const [activeTab, setActiveTab] = useState<TabId>('overview')
  const [retrying, setRetrying] = useState(false)

  const reportUrl = (import.meta.env.VITE_REPORT_URL as string | undefined) ?? 'http://localhost:8005'
  // Use lastReport.session_id (survives STOP which nulls live sessionId)
  const reportSid = lastReport?.session_id ?? sessionId
  const pdfUrl    = reportSid ? `${reportUrl}/v1/reports/${reportSid}/pdf` : null

  const downloadJson = () => {
    if (!lastReport) return
    const blob = new Blob([JSON.stringify(lastReport, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `battle-report-${(reportSid ?? 'unknown').slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const retryFetch = useCallback(async () => {
    if (!sessionId || retrying) return
    setRetrying(true)
    try {
      const res = await fetch(`${reportUrl}/v1/reports/${sessionId}/narrative`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (res.ok) {
        const data = await res.json() as NarrativeReport
        setZoneInsights(data.zone_insights)
        try {
          const arena = await arenaApi.getBattle(sessionId)
          setLastReport(augmentNarrativeStatsFromArena(data, arena))
        } catch {
          setLastReport(data)
        }
      } else {
        console.warn('[PrinterModal] retry failed:', res.status, await res.text().catch(() => ''))
      }
    } catch (err) {
      console.warn('[PrinterModal] retry error:', err)
    } finally {
      setRetrying(false)
    }
  }, [sessionId, retrying, reportUrl, setLastReport, setZoneInsights])

  // Defensively strip any ```markdown ... ``` fences that slipped through the
  // backend parser (can happen with cached reports generated before the fix).
  const rawNarrative = lastReport?.narrative ?? ''
  const narrative = rawNarrative
    .replace(/^```(?:markdown)?\s*\n?/i, '')
    .replace(/\n?```\s*$/i, '')
    .trim()

  // Build section map once the report is available
  const sections = lastReport ? splitSections(narrative) : {}
  const mdContent = lastReport
    ? (activeTab === 'overview'
        ? narrative   // overview shows full for now; zone+stats already shown
        : tabContent(sections, activeTab, narrative))
    : ''

  // For overview tab, only show exec summary + recommendations sections
  const overviewMd = lastReport
    ? tabContent(sections, 'overview', narrative)
    : ''

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col"
        style={{
          width: 760,
          maxHeight: 600,
          height: 600,
          background: '#06070d',
          border: '2px solid #00ff88',
          boxShadow: '0 0 50px #00ff8840, 0 0 100px #00ff8818',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* ── Title bar ── */}
        <div
          className="flex items-center justify-between px-3 py-1.5 shrink-0"
          style={{ borderBottom: '1px solid #00ff8830', background: '#00ff880d' }}
        >
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#00ff88' }} />
            <span className="text-[9px] font-bold tracking-[0.2em]" style={{ color: '#00ff88' }}>
              SCRIBE·BATTLE·REPORT
            </span>
            {lastReport && (
              <span className="text-[7px] font-mono" style={{ color: '#334155' }}>
                {lastReport.session_id.slice(0, 8)}
                {lastReport.cached && <span style={{ color: '#4488ff' }}> [cached]</span>}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {pdfUrl && lastReport && (
              <a
                href={pdfUrl} target="_blank" rel="noreferrer"
                onClick={e => e.stopPropagation()}
                style={{ fontSize: 8, fontFamily: 'monospace', padding: '2px 6px', background: '#00ff8818', color: '#00ff88', border: '1px solid #00ff8855', borderRadius: 2, textDecoration: 'none' }}
              >↓ Report</a>
            )}
            {lastReport && (
              <button
                onClick={e => { e.stopPropagation(); downloadJson() }}
                style={{ fontSize: 8, fontFamily: 'monospace', padding: '2px 6px', background: '#4488ff18', color: '#4488ff', border: '1px solid #4488ff55', borderRadius: 2, cursor: 'pointer' }}
              >↓ JSON</button>
            )}
            <button
              onClick={closeModal}
              style={{ fontSize: 9, fontFamily: 'monospace', color: '#00ff88', opacity: 0.5, background: 'none', border: 'none', cursor: 'pointer' }}
            >[ESC]</button>
          </div>
        </div>

        {lastReport ? (
          <>
            {/* Stats */}
            <div className="shrink-0"><StatBar report={lastReport} /></div>

            {/* Zone intelligence */}
            <div className="shrink-0"><ZoneIntelligence insights={lastReport.zone_insights} /></div>

            {/* Tab navigation */}
            <TabBar active={activeTab} onChange={setActiveTab} />

            {/* Scrollable markdown content */}
            <div className="flex-1 overflow-y-auto min-h-0 px-4 py-3">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={MD_COMPONENTS}
              >
                {activeTab === 'overview' ? overviewMd : mdContent}
              </ReactMarkdown>
            </div>
          </>
        ) : (
          <NoReport onRetry={retryFetch} loading={retrying} />
        )}

        {/* Footer */}
        <div
          className="flex justify-between px-3 py-1 text-[7px] font-mono shrink-0"
          style={{ borderTop: '1px solid #00ff8818', color: '#334155' }}
        >
          <span>ACEA · SCRIBE OUTPUT</span>
          <span style={{ color: '#00ff88' }}>
            {lastReport ? `COMPLETE · ${lastReport.statistics.total_rounds} ROUNDS` : 'PENDING…'}
          </span>
        </div>
      </div>
    </div>
  )
}
