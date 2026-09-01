/**
 * A model failure that waiting will not fix.
 *
 * Throttles and network blips already have a home: a toast, which is the right weight
 * for something that clears itself. An exhausted account is a different event — the run
 * keeps retrying, produces nothing, and the only trace is a line in a log. A 100-round
 * leg was lost that way, and the operator's first sign of it was a truncated error string
 * that cut off before the words "credit balance".
 *
 * So this stays on screen until dismissed, leads with the action rather than the
 * symptom, and keeps the provider's own words one click away for anything unusual.
 */
import { useState } from 'react'

import { useGhostStore } from '../../lib/store'

const BAD = '#f43f5e'
const MUTED = '#64748b'

/** Plain-language heading per category. The code itself is jargon to an operator. */
const HEADING: Record<string, string> = {
  quota_exhausted: 'Out of credit',
  auth_rejected: 'Credentials rejected',
  model_missing: 'Model not available',
  request_rejected: 'Model rejected the request',
}

export default function ModelFaultBanner() {
  const fault = useGhostStore(s => s.modelFault)
  const clearModelFault = useGhostStore(s => s.clearModelFault)
  const openModal = useGhostStore(s => s.openModal)
  const [showDetail, setShowDetail] = useState(false)

  if (!fault) return null

  const heading = HEADING[fault.category] ?? 'Model unavailable'

  return (
    <div
      className="fixed left-1/2 -translate-x-1/2 z-50 px-4 py-3 rounded-sm font-mono"
      style={{
        top: 64,
        width: 'min(680px, calc(100vw - 32px))',
        background: '#1a0d13',
        border: `1px solid ${BAD}`,
        boxShadow: `0 0 24px ${BAD}33`,
      }}
      role="alert"
    >
      <div className="flex items-start gap-3">
        <span style={{ color: BAD }} className="text-[13px] leading-none pt-[2px]">⚠</span>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-[12px] font-bold tracking-[0.08em]" style={{ color: BAD }}>
              {heading.toUpperCase()}
            </span>
            <span className="text-[10px]" style={{ color: MUTED }}>
              round {fault.round} · retrying will not clear this
            </span>
          </div>

          {/* The action, not the symptom. */}
          <p className="text-[11px] mt-1 leading-relaxed" style={{ color: '#e2e8f0' }}>
            {fault.advice}
          </p>

          <div className="flex items-center gap-3 mt-2">
            <button
              type="button"
              onClick={() => openModal('model_health')}
              className="text-[10px] px-2 py-1 rounded-sm"
              style={{ color: BAD, border: `1px solid ${BAD}55` }}
            >
              model health →
            </button>
            {fault.detail && (
              <button
                type="button"
                onClick={() => setShowDetail(v => !v)}
                className="text-[10px] underline"
                style={{ color: MUTED }}
              >
                {showDetail ? 'hide provider message' : 'provider message'}
              </button>
            )}
          </div>

          {/* Kept behind a click: the raw text matters when the category is wrong or
              the failure is one the platform has not seen before. */}
          {showDetail && fault.detail && (
            <pre
              className="text-[10px] mt-2 p-2 rounded-sm whitespace-pre-wrap break-all max-h-32 overflow-y-auto"
              style={{ color: MUTED, background: '#0a0b12', border: '1px solid #1e293b' }}
            >
              {fault.detail}
            </pre>
          )}
        </div>

        <button
          type="button"
          onClick={clearModelFault}
          aria-label="Dismiss"
          className="text-[12px] leading-none px-1"
          style={{ color: MUTED }}
        >
          ✕
        </button>
      </div>
    </div>
  )
}
