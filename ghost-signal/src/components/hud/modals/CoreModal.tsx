

import { useGhostStore } from '@/lib/store'
import StatusBadge from '@/components/ui/StatusBadge'

export default function CoreModal() {
  const { closeModal, agents, missionId, connected } = useGhostStore()
  const list = Array.from(agents.values())
  const activeCount = list.filter(a => a.state !== 'idle').length

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative w-[500px] rounded-sm flex flex-col"
        style={{
          background: '#0d1117',
          border: '2px solid #cc44ff',
          boxShadow: '0 0 40px #cc44ff28, 0 0 80px #cc44ff10',
          maxHeight: 600,
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Title bar */}
        <div
          className="flex items-center justify-between px-3 py-2"
          style={{ borderBottom: '1px solid #cc44ff30', background: '#cc44ff0e' }}
        >
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: '#cc44ff', boxShadow: '0 0 6px #cc44ff' }} />
            <span className="text-[10px] font-bold tracking-[0.18em]" style={{ color: '#cc44ff' }}>
              AI·CORE — SYSTEM STATE
            </span>
          </div>
          <button
            onClick={closeModal}
            className="text-[10px] font-mono opacity-50 hover:opacity-100"
            style={{ color: '#cc44ff' }}
          >
            [ESC]
          </button>
        </div>

        <div className="p-3 space-y-3 overflow-y-auto flex-1">
          {/* Stats grid */}
          <div className="grid grid-cols-4 gap-2">
            {[
              { label: 'MISSION',  value: missionId,           color: '#ffdd00' },
              { label: 'AGENTS',   value: String(list.length), color: '#00ff88' },
              { label: 'ACTIVE',   value: String(activeCount), color: '#ff8800' },
              { label: 'STREAM',   value: connected ? 'LIVE' : 'OFF', color: connected ? '#00ff88' : '#ff4444' },
            ].map(({ label, value, color }) => (
              <div
                key={label}
                className="p-2 rounded-sm"
                style={{ background: '#161b22', border: '1px solid #21262d' }}
              >
                <div className="text-[8px] font-mono mb-1" style={{ color: '#3d4a5a' }}>{label}</div>
                <div className="text-[13px] font-bold font-mono" style={{ color }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Agent registry */}
          <div style={{ borderTop: '1px solid #21262d', paddingTop: 10 }}>
            <div className="text-[8px] font-mono tracking-[0.2em] mb-2" style={{ color: '#3d4a5a' }}>
              AGENT REGISTRY
            </div>
            <div className="space-y-1">
              {list.map(a => (
                <div
                  key={a.id}
                  className="flex items-center justify-between px-2 py-1.5 rounded-sm"
                  style={{ background: '#161b22', borderLeft: `2px solid ${a.color}60` }}
                >
                  <div className="flex items-center gap-2 w-28">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: a.color }} />
                    <span className="text-[10px] font-bold font-mono truncate" style={{ color: a.color }}>
                      {a.label}
                    </span>
                  </div>
                  <StatusBadge state={a.state} />
                  <div className="text-[8px] font-mono w-44 text-right truncate" style={{ color: '#334155' }}>
                    {a.model}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
