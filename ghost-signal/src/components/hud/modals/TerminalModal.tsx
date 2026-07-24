import { useRef, useEffect, useState } from 'react'
import { useGhostStore } from '@/lib/store'
import type { ChatMsg, ChatRole } from '@/types'

// ─── Role presentation ───────────────────────────────────────────────────────

const ROLE_LABEL: Record<ChatRole, string> = {
  recon: 'Recon Analyst',
  strategy: 'Strategy Analyzer',
  rewriter: 'Attack Rewriter',
  enhancer: 'Defense Enhancer',
  attack: 'Red Project',
  defense: 'Blue Project',
  judge: 'Arbiter',
  asis: 'ASIS',
  target: 'Target AI',
  system: 'System',
}

const ROLE_COLOR: Record<ChatRole, string> = {
  recon: '#fdba74',
  strategy: '#f97316',
  rewriter: '#fb923c',
  enhancer: '#38bdf8',
  attack: '#ef4444',
  defense: '#4ade80',
  judge: '#facc15',
  asis: '#c084fc',
  target: '#e879f9',
  system: '#94a3b8',
}

// Which roles each side can surface (for the filter bar order).
const RED_ROLES: ChatRole[] = ['recon', 'strategy', 'rewriter', 'attack', 'judge', 'asis']
const BLUE_ROLES: ChatRole[] = ['recon', 'strategy', 'enhancer', 'defense', 'judge', 'asis']

const RED_IDLE = 'No active battle. Launch one to watch the red side\'s roles think in real time — Recon reads the connected project, Strategy diagnoses, the Rewriter turns it into the next move, the Arbiter scores, and ASIS reasons about code-level improvement.'
const BLUE_IDLE = 'No active battle. Launch one to watch the blue side\'s roles think in real time — Recon reads the connected project, Strategy diagnoses gaps, the Defense Enhancer proposes tightening, the Arbiter scores, and ASIS reasons about code-level improvement.'

function fmtTime(ts: number): string {
  try {
    const d = new Date(ts)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

// ─── Chat bubble ──────────────────────────────────────────────────────────────

function Bubble({ msg }: { msg: ChatMsg }) {
  const color = ROLE_COLOR[msg.role] ?? '#94a3b8'
  return (
    <div className="mb-2.5">
      <div className="flex items-center gap-2 mb-0.5">
        <span
          className="text-[9px] font-bold px-1.5 py-0.5 rounded"
          style={{ color, background: `${color}18`, letterSpacing: '0.04em' }}
        >
          {ROLE_LABEL[msg.role] ?? msg.role}
        </span>
        {msg.round > 0 && (
          <span className="text-[8px] font-mono" style={{ color: '#475569' }}>R{msg.round}</span>
        )}
        <span className="text-[8px] font-mono ml-auto" style={{ color: '#334155' }}>{fmtTime(msg.ts)}</span>
      </div>
      <div
        className="text-[11px] leading-[1.6] whitespace-pre-wrap break-words px-2.5 py-1.5 rounded"
        style={{ background: '#111826', color: '#cbd5e1', borderLeft: `2px solid ${color}` }}
      >
        {msg.text}
        {msg.meta && (
          <div className="text-[8px] font-mono mt-1" style={{ color: '#475569' }}>{msg.meta}</div>
        )}
      </div>
    </div>
  )
}

// ─── Main modal ──────────────────────────────────────────────────────────────

export default function TerminalModal({ side }: { side: 'red' | 'blue' }) {
  const closeModal = useGhostStore(s => s.closeModal)
  const battleMode = useGhostStore(s => s.battleMode)
  const modal = useGhostStore(s => s.modal)
  const agentChat = useGhostStore(s => s.agentChat)

  const isLive = battleMode === 'live'
  const isRed = side === 'red'
  const accent = isRed ? '#f97316' : '#4488ff'
  const title = isRed ? 'RED SIDE · THINKING' : 'BLUE SIDE · THINKING'
  const roles = isRed ? RED_ROLES : BLUE_ROLES

  // Optional initial filter from the agent that opened this (per-agent view).
  const initialFilter = ((modal?.data as { roleFilter?: ChatRole } | undefined)?.roleFilter) ?? 'all'
  const [filter, setFilter] = useState<ChatRole | 'all'>(initialFilter)
  useEffect(() => { setFilter(initialFilter) }, [initialFilter])

  const all = agentChat[side]
  const msgs = filter === 'all' ? all : all.filter(m => m.role === filter)

  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [msgs.length])

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative w-[600px] rounded-sm flex flex-col"
        style={{
          background: '#0d1117',
          border: `2px solid ${accent}`,
          boxShadow: `0 0 40px ${accent}30, 0 0 80px ${accent}12`,
          height: 600,
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Title bar */}
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0"
          style={{ borderBottom: `1px solid ${accent}35`, background: `${accent}0d` }}
        >
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: accent }} />
            <span className="text-[10px] font-bold tracking-[0.16em]" style={{ color: accent }}>{title}</span>
            {isLive
              ? <span className="text-[8px] font-mono px-1.5 py-0.5 rounded" style={{ background: '#00ff8815', color: '#00ff88' }}>LIVE</span>
              : <span className="text-[8px] font-mono px-1.5 py-0.5 rounded" style={{ background: '#33415522', color: '#64748b' }}>IDLE</span>}
          </div>
          <button
            onClick={closeModal}
            className="text-[10px] font-mono hover:opacity-100 opacity-40 transition-opacity"
            style={{ color: accent }}
          >
            [ESC]
          </button>
        </div>

        {/* Role filter bar */}
        <div className="flex items-center gap-1 px-3 py-1.5 shrink-0 flex-wrap" style={{ borderBottom: `1px solid ${accent}18` }}>
          {(['all', ...roles] as (ChatRole | 'all')[]).map(r => {
            const active = filter === r
            const c = r === 'all' ? accent : ROLE_COLOR[r]
            return (
              <button
                key={r}
                onClick={() => setFilter(r)}
                className="text-[8px] font-mono px-1.5 py-0.5 rounded transition-opacity"
                style={{
                  color: active ? '#0d1117' : c,
                  background: active ? c : `${c}14`,
                  fontWeight: active ? 700 : 400,
                }}
              >
                {r === 'all' ? 'ALL' : ROLE_LABEL[r]}
              </button>
            )
          })}
        </div>

        {/* Chat pane */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-3 min-h-0"
          style={{ background: isRed ? '#0a0200' : '#00050f' }}
        >
          {msgs.length === 0 ? (
            <div className="text-[10px] font-mono leading-[1.7] px-1" style={{ color: '#1e3a5f' }}>
              {isLive ? 'Waiting for this role\'s real reasoning to arrive…' : (isRed ? RED_IDLE : BLUE_IDLE)}
            </div>
          ) : (
            msgs.map(m => <Bubble key={m.id} msg={m} />)
          )}
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-between px-3 py-1.5 text-[8px] font-mono shrink-0"
          style={{ borderTop: `1px solid ${accent}20`, color: '#334155' }}
        >
          <span>REAL·REASONING·ONLY · {msgs.length} msg{msgs.length === 1 ? '' : 's'}</span>
          <span style={{ color: isLive ? '#00ff88' : '#334155' }}>{isLive ? '● STREAM·LIVE' : '◌ IDLE'}</span>
        </div>
      </div>
    </div>
  )
}
