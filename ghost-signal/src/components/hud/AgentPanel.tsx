

import { useGhostStore } from '@/lib/store'
import StatusBadge from '@/components/ui/StatusBadge'
import type { AgentRole } from '@/types'

const ROLE_TAG: Record<AgentRole, string> = {
  attacker: 'ATK',
  defender: 'DEF',
  victim:   'TGT',
  judge:    'JDG',
  reporter: 'RPT',
}

interface Props { side: 'red' | 'blue' }

export default function AgentPanel({ side }: Props) {
  const agents = useGhostStore(s => s.agents)
  // Panels list the platform's assisting models only. The participant fighters
  // (redFighter / blueFighter) are field-only actors, not assisting agents, so
  // they are excluded from the side panels.
  const list = Array.from(agents.values()).filter(a =>
    a.id !== 'redFighter' && a.id !== 'blueFighter' &&
    (side === 'red' ? a.role === 'attacker' : a.role === 'defender')
  )

  const accent = side === 'red' ? '#ff4444' : '#4488ff'

  return (
    <div
      className="absolute top-[56px] bottom-[40px] flex flex-col gap-1.5 p-2 z-[100]"
      style={{
        [side === 'red' ? 'left' : 'right']: 0,
        width: 158,
        background: `${accent}08`,
        [side === 'red' ? 'borderRight' : 'borderLeft']: `1px solid ${accent}25`,
        pointerEvents: 'none',
      }}
    >
      <div
        className="text-[8px] tracking-[0.25em] pb-1 mb-0.5"
        style={{ color: accent, borderBottom: `1px solid ${accent}30` }}
      >
        {side === 'red' ? '[ RED TEAM ]' : '[ BLUE TEAM ]'}
      </div>

      {list.map(agent => (
        <div
          key={agent.id}
          className="flex flex-col gap-1 p-1.5 rounded"
          style={{
            background: '#0d111755',
            border: `1px solid ${agent.color}18`,
            borderLeft: `2px solid ${agent.color}80`,
          }}
        >
          {/* Name + role badge */}
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold" style={{ color: agent.color }}>
              {agent.label}
            </span>
            <span
              className="text-[8px] px-1 py-px rounded-sm font-mono"
              style={{ background: `${agent.color}15`, color: `${agent.color}bb` }}
            >
              {ROLE_TAG[agent.role]}
            </span>
          </div>

          <StatusBadge state={agent.state} />

          {/* Model name */}
          <div className="text-[8px] truncate mt-0.5" style={{ color: '#334155' }} title={agent.model}>
            {agent.model.split('/').pop()}
          </div>

          {/* Last message */}
          {agent.message && (
            <div className="text-[8px] truncate italic" style={{ color: '#3d4a5a' }}>
              {agent.message.slice(0, 26)}
            </div>
          )}
        </div>
      ))}

      {/* Bottom: judge + reporter summary */}
      {side === 'blue' && (
        <div className="mt-auto pt-1" style={{ borderTop: '1px solid #21262d' }}>
          {['judge', 'reporter'].map(id => {
            const a = agents.get(id)
            if (!a) return null
            return (
              <div key={id} className="flex items-center justify-between py-0.5">
                <span className="text-[9px] font-bold" style={{ color: a.color }}>{a.label}</span>
                <StatusBadge state={a.state} />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
