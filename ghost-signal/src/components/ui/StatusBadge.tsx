import type { AgentState } from '@/types'

const STATE_STYLE: Record<AgentState, { bg: string; fg: string; label: string }> = {
  idle:      { bg: '#161b22', fg: '#475569', label: 'IDLE'     },
  thinking:  { bg: '#1a1500', fg: '#ffdd00', label: 'THINKING' },
  acting:    { bg: '#1a0900', fg: '#ff8800', label: 'ACTING'   },
  success:   { bg: '#001a08', fg: '#00ff88', label: 'SUCCESS'  },
  failed:    { bg: '#1a0000', fg: '#ff4444', label: 'FAILED'   },
  moving:    { bg: '#001018', fg: '#00ccff', label: 'MOVING'   },
  gathering: { bg: '#001018', fg: '#00ccff', label: 'GATHER'   },
  writing:   { bg: '#001a08', fg: '#44ff88', label: 'WRITING'  },
  printing:  { bg: '#001a08', fg: '#44ff88', label: 'PRINT'    },
}

export default function StatusBadge({ state }: { state: AgentState }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE.idle
  return (
    <span
      className="inline-block text-[9px] font-mono font-bold px-1.5 py-px rounded-sm tracking-widest"
      style={{ background: s.bg, color: s.fg, border: `1px solid ${s.fg}35` }}
    >
      {s.label}
    </span>
  )
}
