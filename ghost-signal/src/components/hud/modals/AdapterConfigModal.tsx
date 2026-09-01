/**
 * Shown when arena-core refuses start (e.g. REQUIRE_ADAPTER_SOURCES without .env paths).
 */
import { useGhostStore } from '@/lib/store'
import type { AdapterConfigErrorDetail } from '@/lib/arenaApi'

const BORDER = '#00ff88'
const BG = '#06070d'

export default function AdapterConfigModal() {
  const closeModal = useGhostStore(s => s.closeModal)
  const payload = useGhostStore(s => s.modal.data) as AdapterConfigErrorDetail | undefined
  const msg = payload?.message ?? 'Configure red/blue adapter sources in .env.'

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col max-w-lg"
        style={{
          width: 480,
          maxHeight: '90vh',
          background: BG,
          border: `2px solid ${BORDER}`,
          boxShadow: `0 0 40px ${BORDER}33`,
        }}
        onClick={e => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0"
          style={{ borderBottom: `1px solid ${BORDER}44`, background: `${BORDER}12` }}
        >
          <span className="text-[11px] font-bold tracking-[0.2em]" style={{ color: BORDER }}>
            ADAPTER CONFIG REQUIRED
          </span>
          <button
            type="button"
            onClick={closeModal}
            className="text-[10px] font-mono opacity-70 hover:opacity-100"
            style={{ color: BORDER }}
          >
            [ESC]
          </button>
        </div>
        <div className="px-4 py-4 text-[10px] font-mono leading-relaxed" style={{ color: '#cbd5e1' }}>
          <p className="mb-3" style={{ color: '#fca5a5' }}>
            Cannot start a battle until both teams are wired in <code className="text-[#94a3b8]">.env</code>.
          </p>
          <p className="mb-3 whitespace-pre-wrap break-words" style={{ color: '#e2e8f0' }}>
            {msg}
          </p>
          <p className="mb-2" style={{ color: '#64748b' }}>
            Set at least one of per team:
          </p>
          <ul className="list-disc pl-4 space-y-1 mb-4" style={{ color: '#94a3b8' }}>
            <li><code>RED_ADAPTER_URL</code> / <code>BLUE_ADAPTER_URL</code></li>
            <li>or <code>RED_ADAPTER_URLS</code> / <code>BLUE_ADAPTER_URLS</code></li>
            <li>or <code>RED_ADAPTER_PATH</code> / <code>BLUE_ADAPTER_PATH</code> (Docker build context)</li>
          </ul>
          <p style={{ color: '#64748b', fontSize: 9 }}>
            Turn off strict checks with <code>REQUIRE_ADAPTER_SOURCES=false</code> only for local dev.
          </p>
        </div>
      </div>
    </div>
  )
}
