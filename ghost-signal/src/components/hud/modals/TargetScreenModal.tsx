/**
 * TargetScreenModal — center "main display" for the attack ↔ target-AI exchange.
 *
 * Primary content: RED attack payload ↔ TARGET-AI reply (large, prominent).
 * Secondary content: Blue block/allow decision (compact badge), Judge verdict (small footer).
 */
import { useEffect, useRef } from 'react'
import { useGhostStore } from '@/lib/store'
import type { MainScreenChatLine } from '@/types'

const ACCENT = '#cc44ff'

function ts(t: number): string {
  return new Date(t).toISOString().slice(11, 19)
}

// ─── Round separator ─────────────────────────────────────────────────────────

function RoundSeparator({ line }: { line: MainScreenChatLine }) {
  return (
    <div className="flex items-center gap-2 my-1">
      <div className="flex-1 h-px" style={{ background: '#1e293b' }} />
      <span className="text-[8px] font-mono px-2" style={{ color: '#334155' }}>
        ROUND {line.round} · {ts(line.ts)}
      </span>
      <div className="flex-1 h-px" style={{ background: '#1e293b' }} />
    </div>
  )
}

// ─── Attack bubble (RED payload — primary) ───────────────────────────────────

function AttackBubble({ line }: { line: MainScreenChatLine }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span
          className="text-[8px] font-mono font-bold px-2 py-0.5 rounded-sm"
          style={{ background: '#3a0a0a', color: '#f87171' }}
        >
          RED · ATTACK PAYLOAD
        </span>
        {line.meta && (
          <span className="text-[8px] font-mono" style={{ color: '#475569' }}>
            {line.meta}
          </span>
        )}
        <span className="ml-auto text-[8px] font-mono" style={{ color: '#334155' }}>
          {ts(line.ts)}
        </span>
      </div>
      <div
        className="rounded-sm border-l-2 px-3 py-2.5"
        style={{ borderColor: '#ef4444', background: '#1a0508' }}
      >
        <div
          className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-words"
          style={{ color: '#fca5a5' }}
        >
          {line.body || '(empty payload)'}
        </div>
      </div>
    </div>
  )
}

// ─── Target-AI raw completion (unfiltered model output) ──────────────────────

function RawModelBubble({ line }: { line: MainScreenChatLine }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span
          className="text-[8px] font-mono font-bold px-2 py-0.5 rounded-sm"
          style={{ background: '#1a2540', color: '#93c5fd' }}
        >
          TARGET-AI · RAW MODEL OUTPUT
        </span>
        {line.meta && (
          <span className="text-[8px] font-mono truncate max-w-[220px]" style={{ color: '#475569' }}>
            {line.meta}
          </span>
        )}
        <span className="ml-auto text-[8px] font-mono" style={{ color: '#334155' }}>
          {ts(line.ts)}
        </span>
      </div>
      <div
        className="rounded-sm border-l-2 px-3 py-2.5"
        style={{ borderColor: '#60a5fa', background: '#0a1628' }}
      >
        <div
          className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-words"
          style={{ color: '#bfdbfe' }}
        >
          {line.body || '(empty)'}
        </div>
      </div>
    </div>
  )
}

// ─── After blue OUTPUT filter (observable / red-facing text) ─────────────────

function DeliveredBubble({ line }: { line: MainScreenChatLine }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span
          className="text-[8px] font-mono font-bold px-2 py-0.5 rounded-sm"
          style={{ background: '#0f172a', color: '#67e8f9' }}
        >
          BLUE · OUTPUT FILTER → DELIVERED
        </span>
        {line.meta && (
          <span className="text-[8px] font-mono truncate max-w-[220px]" style={{ color: '#475569' }}>
            {line.meta}
          </span>
        )}
        <span className="ml-auto text-[8px] font-mono" style={{ color: '#334155' }}>
          {ts(line.ts)}
        </span>
      </div>
      <div
        className="rounded-sm border-l-2 px-3 py-2.5"
        style={{ borderColor: '#22d3ee', background: '#061216' }}
      >
        <div
          className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-words"
          style={{ color: '#cffafe' }}
        >
          {line.body || '(empty)'}
        </div>
      </div>
    </div>
  )
}

// ─── Blue decision badge (compact) ──────────────────────────────────────────

function BlueBadge({ line }: { line: MainScreenChatLine }) {
  const isBlock = line.variant === 'blocked'
  const color   = isBlock ? '#22c55e' : '#facc15'
  const label   = isBlock ? '🛡 BLOCKED' : '⚠ FORWARDED TO TARGET-AI'
  return (
    <div className="flex flex-col gap-1 pl-2">
      <div className="flex items-center gap-2">
        <div className="w-px h-4 shrink-0" style={{ background: color + '60' }} />
        <span className="text-[8px] font-mono font-bold" style={{ color }}>
          BLUE · {label}
        </span>
        {line.meta && (
          <span className="text-[8px] font-mono" style={{ color: '#374151' }}>
            {line.meta}
          </span>
        )}
        <span className="ml-auto text-[8px] font-mono shrink-0" style={{ color: '#1f2937' }}>
          {ts(line.ts)}
        </span>
      </div>
      {!isBlock && line.body && (
        <div
          className="ml-3 rounded-sm border-l-2 px-3 py-2"
          style={{ borderColor: `${color}99`, background: '#0c0812' }}
        >
          <div
            className="text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-words"
            style={{ color: '#fde68a' }}
          >
            {line.body}
          </div>
        </div>
      )}
      {isBlock && line.body && (
        <div className="ml-3 text-[8px] font-mono pl-1" style={{ color: '#64748b' }}>
          {line.body}
        </div>
      )}
    </div>
  )
}

// ─── Passthrough chip (blue output identical to RAW — avoid duplicate prose) ─

function DeliveredPassthroughChip({ line }: { line: MainScreenChatLine }) {
  return (
    <div
      className="flex flex-wrap items-center gap-2 pl-2 py-0.5 text-[8px] font-mono rounded-sm mx-1"
      style={{ background: '#0f172a', border: '1px solid rgba(34,211,238,0.35)' }}
    >
      <span style={{ color: '#22d3ee', fontWeight: 700 }}>BLUE OUTPUT GATE</span>
      <span style={{ color: '#475569' }}>R{line.round}</span>
      <span style={{ color: '#94a3b8' }}>{line.meta || 'passthrough · same text as RAW above'}</span>
      <span className="ml-auto" style={{ color: '#334155' }}>{ts(line.ts)}</span>
    </div>
  )
}

// ─── Judge verdict chip (compact) ───────────────────────────────────────────

function JudgeChip({ line }: { line: MainScreenChatLine }) {
  const meta = line.meta ?? ''
  const isRedWin = /^verdict=success\b/.test(meta)
  const color = isRedWin ? '#ef4444' : '#22c55e'
  const label = isRedWin ? 'RED WIN' : 'BLUE WIN'
  const metaTail = meta.replace(/^verdict=(success|failure)\s*[· ]\s*/, '')
  return (
    <div
      className="flex flex-col gap-0.5 px-2 py-1 rounded-sm text-[8px] font-mono"
      style={{ background: '#0b0e14', border: `1px solid ${color}30` }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span style={{ color: '#475569' }}>JUDGE R{line.round}</span>
        <span style={{ color, fontWeight: 700 }}>{label}</span>
        {metaTail && (
          <span style={{ color: '#64748b', wordBreak: 'break-word' }}>
            {metaTail}
          </span>
        )}
      </div>
      {line.body && (
        <div className="pl-1 whitespace-pre-wrap break-words leading-relaxed" style={{ color: '#94a3b8' }}>
          {line.body}
        </div>
      )}
    </div>
  )
}

// ─── Main modal ──────────────────────────────────────────────────────────────

export default function TargetScreenModal() {
  const closeModal = useGhostStore(s => s.closeModal)
  const feed       = useGhostStore(s => s.mainScreenFeed)
  const battleMode = useGhostStore(s => s.battleMode)
  const sessionId  = useGhostStore(s => s.sessionId)
  const isLive     = battleMode === 'live'
  const scrollRef  = useRef<HTMLDivElement>(null)

  const attackCount = feed.filter(l => l.variant === 'attack').length
  const rawCount    = feed.filter(l => l.variant === 'target_raw').length
  const deliveredCt = feed.filter(l => l.variant === 'delivered').length
  const passCt      = feed.filter(l => l.variant === 'delivered_passthrough').length

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [feed.length])

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col"
        style={{
          width: 780,
          height: 620,
          background: '#06070d',
          border: `2px solid ${ACCENT}`,
          boxShadow: `0 0 50px ${ACCENT}38, 0 0 100px ${ACCENT}15`,
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Title bar */}
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0"
          style={{ borderBottom: `1px solid ${ACCENT}40`, background: `${ACCENT}0d` }}
        >
          <div className="flex items-center gap-2">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: ACCENT, boxShadow: `0 0 6px ${ACCENT}` }}
            />
            <span className="text-[11px] font-bold tracking-[0.2em]" style={{ color: ACCENT }}>
              MAINSCREEN · ATTACK ↔ TARGET-AI
            </span>
            {isLive && (
              <span
                className="text-[8px] font-mono px-1.5 py-0.5 rounded"
                style={{ background: '#00ff8815', color: '#00ff88' }}
              >
                LIVE
              </span>
            )}
            {sessionId && (
              <span className="text-[8px] font-mono opacity-40" style={{ color: ACCENT }}>
                {sessionId.slice(0, 8)}
              </span>
            )}
          </div>
          <button
            onClick={closeModal}
            className="text-[10px] font-mono opacity-40 hover:opacity-100 transition"
            style={{ color: ACCENT }}
          >
            [ESC]
          </button>
        </div>

        {/* Stats sub-header */}
        <div
          className="flex gap-4 px-3 py-1.5 text-[8px] font-mono shrink-0"
          style={{ borderBottom: `1px solid ${ACCENT}18`, background: `${ACCENT}06` }}
        >
          <span style={{ color: '#475569' }}>
            ATTACKS <span style={{ color: '#f87171' }}>{attackCount}</span>
          </span>
          <span style={{ color: '#475569' }}>
            MODEL RAW <span style={{ color: '#93c5fd' }}>{rawCount}</span>
          </span>
          <span style={{ color: '#475569' }}>
            OUTPUT GATE <span style={{ color: '#67e8f9' }}>{deliveredCt}</span>
            modified /{' '}
            <span style={{ color: '#22d3ee' }}>{passCt}</span>
            passthru
          </span>
          <span style={{ color: '#475569' }}>
            EVENTS <span style={{ color: '#64748b' }}>{feed.length}</span>
          </span>
          <span className="ml-auto" style={{ color: '#1f2937' }}>
            {isLive ? '● STREAMING' : '◌ IDLE'}
          </span>
        </div>

        {/* Feed */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-4 py-3 min-h-0 flex flex-col gap-2"
          style={{ background: '#020710', fontFamily: 'JetBrains Mono, monospace' }}
        >
          {feed.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-[10px] font-mono">
              <div style={{ color: ACCENT, fontSize: 20 }}>⬡</div>
              <div style={{ color: '#1e3a5f' }}>
                {isLive ? 'waiting for round events…' : 'no battle session — press LAUNCH'}
              </div>
              <div
                className="text-center max-w-md leading-relaxed"
                style={{ color: '#1e293b', fontSize: 9 }}
              >
                Flow: BLUE input gate → Target AI completion (RAW) → BLUE output gate (DELIVERED).
                Older sessions may still show TARGET-AI · RESPONSE tiles (legacy variant).
              </div>
            </div>
          ) : (
            feed.map(line => {
              switch (line.variant) {
                case 'round_start':
                  return <RoundSeparator key={line.id} line={line} />
                case 'attack':
                  return <AttackBubble key={line.id} line={line} />
                case 'blocked':
                case 'allowed':
                  return <BlueBadge key={line.id} line={line} />
                case 'target_raw':
                  return <RawModelBubble key={line.id} line={line} />
                case 'delivered':
                  return <DeliveredBubble key={line.id} line={line} />
                case 'delivered_passthrough':
                  return <DeliveredPassthroughChip key={line.id} line={line} />
                case 'reply':
                  return <DeliveredBubble key={line.id} line={line} />
                case 'judge':
                  return <JudgeChip key={line.id} line={line} />
                default:
                  return null
              }
            })
          )}
        </div>

        {/* Footer */}
        <div
          className="flex justify-between px-3 py-1.5 text-[8px] font-mono shrink-0"
          style={{ borderTop: `1px solid ${ACCENT}1a`, color: '#1f2937' }}
        >
          <span>RED PAYLOAD ↔ TARGET-AI EXCHANGE STREAM</span>
          <span style={{ color: isLive ? '#00ff88' : '#1f2937' }}>
            {isLive ? '● STREAM·LIVE' : '◌ OFFLINE'}
          </span>
        </div>
      </div>
    </div>
  )
}
