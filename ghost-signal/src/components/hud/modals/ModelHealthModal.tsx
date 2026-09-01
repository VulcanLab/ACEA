/**
 * Which configured models answer, and which do not.
 *
 * Auto-opens when a battle halts on adapter errors — often a model that went
 * unreachable or ran out of credit mid-run — and can be opened any time from the
 * TopBar indicator.
 *
 * Laid out as ONE wide table rather than a stack of boxes. The earlier version gave
 * every failing model its own bordered card and then repeated the whole roster below
 * it, so a deployment with ten roles produced a column taller than the window and the
 * operator had to scroll past duplicated information to find out which role was
 * actually broken. A model is one row: state, name, roles, latency, and what went wrong.
 */
import { useEffect, useMemo, useState } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, type PreflightResult, type PreflightModel } from '@/lib/arenaApi'

const BAD = '#ff5577'
const OKC = '#22c55e'
const BG = '#06070d'
const MUTED = '#64748b'

/** Long provider dumps are unreadable inline; keep the part that names the cause. */
function shortReason(error?: string): string {
  const raw = (error || 'unreachable').replace(/\s+/g, ' ').trim()
  // Providers wrap the useful sentence in nested JSON. Pull out a human phrase when one
  // is obviously present, otherwise show the head of the message.
  const m = raw.match(/"message"\s*:\s*"([^"]{8,160})/)
  const text = m ? m[1] : raw
  return text.length > 150 ? text.slice(0, 150) + '…' : text
}

export default function ModelHealthModal() {
  const closeModal = useGhostStore(s => s.closeModal)
  const payload = useGhostStore(s => s.modal.data) as { reason?: string } | undefined
  const [result, setResult] = useState<PreflightResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const refresh = async (recheck: boolean) => {
    setLoading(true); setErr('')
    try {
      const r = recheck ? await arenaApi.recheckPreflight()
                        : (await arenaApi.getHealth()).litellm_preflight ?? null
      setResult(r)
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  // On open: re-run preflight live so the status reflects "right now".
  useEffect(() => { void refresh(true) }, [])

  const models: PreflightModel[] = result?.models ?? []

  // Failing models first: they are the reason this panel is open.
  const ordered = useMemo(
    () => [...models].sort((a, b) => Number(a.ok) - Number(b.ok)),
    [models],
  )
  const failing = models.filter(m => !m.ok)
  const accent = failing.length ? BAD : OKC

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col"
        style={{
          // Wide rather than tall: the content is a table, and the role column needs room.
          width: 'min(920px, calc(100vw - 48px))',
          maxHeight: 'min(560px, 85vh)',
          background: BG,
          border: `2px solid ${accent}`,
          boxShadow: `0 0 40px ${accent}33`,
        }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-3 py-2 shrink-0"
             style={{ borderBottom: `1px solid ${MUTED}44` }}>
          <span className="text-[11px] font-bold tracking-[0.2em]" style={{ color: accent }}>
            MODEL HEALTH
          </span>
          <div className="flex items-center gap-3">
            {!loading && !err && (
              <span className="text-[10px] font-mono" style={{ color: MUTED }}>
                {failing.length === 0
                  ? `all ${models.length} reachable`
                  : `${failing.length} of ${models.length} unreachable`}
              </span>
            )}
            <button type="button" onClick={() => void refresh(true)}
                    className="text-[10px] font-mono px-2 py-0.5 rounded-sm"
                    style={{ color: MUTED, border: `1px solid ${MUTED}55` }}>
              ↻ re-check
            </button>
            <button type="button" onClick={closeModal}
                    className="text-[12px] font-mono opacity-70 hover:opacity-100"
                    style={{ color: MUTED }}>✕</button>
          </div>
        </div>

        {payload?.reason && (
          <div className="px-3 py-1.5 text-[11px] font-mono shrink-0"
               style={{ background: `${BAD}14`, color: '#fca5a5' }}>
            {payload.reason}
          </div>
        )}

        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="px-3 py-3 text-[11px] font-mono" style={{ color: MUTED }}>
              Checking every configured model…
            </div>
          )}
          {err && (
            <div className="px-3 py-3 text-[11px] font-mono" style={{ color: '#fca5a5' }}>
              Could not fetch preflight: {err}
            </div>
          )}

          {!loading && !err && (
            <table className="w-full text-[11px] font-mono border-collapse">
              <thead>
                <tr style={{ color: MUTED }}>
                  <th className="text-left font-normal px-3 py-1" style={{ width: 44 }} />
                  <th className="text-left font-normal px-2 py-1">model</th>
                  <th className="text-left font-normal px-2 py-1" style={{ width: 190 }}>roles</th>
                  <th className="text-left font-normal px-2 py-1" style={{ width: 64 }}>latency</th>
                  <th className="text-left font-normal px-2 py-1">status</th>
                </tr>
              </thead>
              <tbody>
                {ordered.map(m => (
                  <tr key={m.model}
                      style={{ borderTop: '1px solid #161c27',
                               background: m.ok ? 'transparent' : `${BAD}0c` }}>
                    <td className="px-3 py-1 align-top"
                        style={{ color: m.ok ? OKC : BAD }}>{m.ok ? '✓' : '✗'}</td>
                    <td className="px-2 py-1 align-top"
                        style={{ color: m.ok ? '#cbd5e1' : BAD, fontWeight: m.ok ? 400 : 700,
                                 wordBreak: 'break-all' }}>
                      {m.model}
                    </td>
                    <td className="px-2 py-1 align-top" style={{ color: MUTED }}>
                      {m.roles.join(', ') || '—'}
                    </td>
                    <td className="px-2 py-1 align-top" style={{ color: MUTED }}>
                      {typeof m.latency_seconds === 'number'
                        ? `${m.latency_seconds.toFixed(2)}s` : '—'}
                    </td>
                    <td className="px-2 py-1 align-top"
                        style={{ color: m.ok ? MUTED : '#fca5a5' }}
                        // The trimmed reason is what fits; the original stays on hover.
                        title={m.ok ? '' : (m.error || '')}>
                      {m.ok ? 'ok' : shortReason(m.error)}
                    </td>
                  </tr>
                ))}
                {ordered.length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-3" style={{ color: MUTED }}>
                    No models configured.
                  </td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div className="px-3 py-2 shrink-0 text-[10px] font-mono"
             style={{ borderTop: `1px solid ${MUTED}44`, color: MUTED }}>
          A role with an unreachable model cannot run. Point it at a model that answers in
          .env and restart that service — hover a status for the provider&apos;s full message.
        </div>
      </div>
    </div>
  )
}
