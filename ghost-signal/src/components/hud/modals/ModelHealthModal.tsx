/**
 * Closable popup that shows LiteLLM model reachability — exactly which model(s)
 * cannot be reached and why. Auto-opens when a battle halts on adapter errors
 * (often a model that went unreachable / rate-limited mid-run), and can be
 * opened manually any time from the TopBar model-health indicator.
 */
import { useEffect, useState } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, type PreflightResult, type PreflightModel } from '@/lib/arenaApi'

const BORDER = '#ff5577'
const OKC = '#22c55e'
const BG = '#06070d'

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
  const failing = models.filter(m => !m.ok)

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col"
        style={{ width: 560, maxHeight: '90vh', background: BG, border: `2px solid ${BORDER}`,
                 boxShadow: `0 0 40px ${BORDER}33` }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-3 py-2 shrink-0"
             style={{ borderBottom: `1px solid ${BORDER}44`, background: `${BORDER}12` }}>
          <span className="text-[11px] font-bold tracking-[0.2em]" style={{ color: BORDER }}>
            MODEL HEALTH · LiteLLM reachability
          </span>
          <button type="button" onClick={closeModal}
                  className="text-[12px] font-mono opacity-70 hover:opacity-100"
                  style={{ color: BORDER }}>✕ close</button>
        </div>

        <div className="px-4 py-3 overflow-auto text-[12px] font-mono" style={{ color: '#cbd5e1' }}>
          {payload?.reason && (
            <div className="mb-3 px-2 py-1 rounded-sm" style={{ background: `${BORDER}18`, color: '#fca5a5' }}>
              {payload.reason}
            </div>
          )}

          {loading && <div style={{ color: '#94a3b8' }}>Checking every configured model…</div>}
          {err && <div style={{ color: '#fca5a5' }}>Could not fetch preflight: {err}</div>}

          {!loading && !err && (
            <>
              <div className="mb-2">
                {failing.length === 0
                  ? <span style={{ color: OKC }}>✓ All {models.length} configured models reachable.</span>
                  : <span style={{ color: BORDER }}>
                      ✗ {failing.length} of {models.length} model(s) UNREACHABLE — these break the roles below:
                    </span>}
              </div>

              {failing.map(m => (
                <div key={m.model} className="mb-2 px-2 py-1 rounded-sm"
                     style={{ border: `1px solid ${BORDER}55`, background: `${BORDER}10` }}>
                  <div style={{ color: BORDER, fontWeight: 700 }}>{m.model}</div>
                  <div style={{ color: '#94a3b8' }}>roles: {m.roles.join(', ') || '—'}</div>
                  <div style={{ color: '#fca5a5', wordBreak: 'break-word' }}>
                    {(m.error || 'unreachable').slice(0, 300)}
                  </div>
                </div>
              ))}

              <div className="mt-3 mb-1" style={{ color: '#64748b' }}>All models:</div>
              {models.map(m => (
                <div key={'all-' + m.model} className="flex justify-between gap-2 py-0.5">
                  <span style={{ color: m.ok ? OKC : BORDER }}>
                    {m.ok ? '✓' : '✗'} {m.model}
                  </span>
                  <span style={{ color: '#64748b' }}>
                    {m.roles.join(', ')}
                    {typeof m.latency_seconds === 'number' ? ` · ${m.latency_seconds.toFixed(2)}s` : ''}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="flex items-center justify-between px-3 py-2 shrink-0"
             style={{ borderTop: `1px solid ${BORDER}44` }}>
          <span className="text-[10px]" style={{ color: '#64748b' }}>
            Fix: set a reachable model in .env, then restart the affected service.
          </span>
          <button type="button" onClick={() => void refresh(true)}
                  className="text-[11px] font-mono px-2 py-1 rounded-sm"
                  style={{ color: BORDER, border: `1px solid ${BORDER}66` }}>
            ↻ re-check
          </button>
        </div>
      </div>
    </div>
  )
}
