

import { useGhostStore } from '@/lib/store'
import type { AgentState } from '@/types'

const STATE_FG: Record<AgentState, string> = {
  idle:      '#475569',
  thinking:  '#ffdd00',
  acting:    '#ff8800',
  success:   '#00ff88',
  failed:    '#ff4444',
  moving:    '#00ccff',
  gathering: '#00ccff',
  writing:   '#44ff88',
  printing:  '#44ff88',
}

function ts(n: number): string {
  return new Date(n).toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export default function ActivityLog() {
  const log = useGhostStore(s => s.log)
  const recent = log.slice(0, 8)

  return (
    <div
      className="absolute bottom-0 left-0 right-0 flex items-center gap-0 px-3 overflow-hidden z-[100]"
      style={{
        height: 40,
        background: 'linear-gradient(0deg,#0d1117 0%,#0a0a0f 100%)',
        borderTop: '1px solid #21262d',
      }}
    >
      {recent.map((entry, i) => (
        <div
          key={entry.id}
          className="flex items-center gap-1.5 shrink-0 mr-5"
          style={{ opacity: 1 - i * 0.1 }}
        >
          <span className="text-[8px] font-mono" style={{ color: '#334155' }}>
            {ts(entry.timestamp)}
          </span>
          <span
            className="text-[8px] font-bold font-mono"
            style={{ color: STATE_FG[entry.state] ?? '#475569' }}
          >
            [{entry.agentId.toUpperCase()}]
          </span>
          <span className="text-[8px] font-mono" style={{ color: '#64748b' }}>
            {entry.message.slice(0, 32)}
          </span>
        </div>
      ))}
    </div>
  )
}
