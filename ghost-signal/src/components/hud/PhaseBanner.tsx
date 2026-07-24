/**
 * On-screen stage indicator. Shows the platform's CURRENT pipeline phase
 * (recon → attack → defense → target → judge → self-improve → report), driven
 * live from WS events (see arenaWsClient._updatePhase). Makes otherwise-quiet
 * stages — especially pre-battle recon and between-battle ASIS — visible.
 */
import { useGhostStore } from '@/lib/store'

const PHASE_COLOR: Record<string, string> = {
  IDLE: '#64748b',
  RECON: '#ffaa33',
  ATTACK: '#ff5566',
  DEFENSE: '#4a9eff',
  TARGET: '#cc44ff',
  JUDGE: '#ffdd00',
  ROUND: '#94a3b8',
  HOLDING: '#ffb020',
  'SELF-IMPROVE': '#00ff88',
  COMPLETE: '#00ff88',
  STOPPED: '#ff8800',
}

export default function PhaseBanner() {
  const phase = useGhostStore(s => s.phase)
  const detail = useGhostStore(s => s.phaseDetail)
  const color = PHASE_COLOR[phase] ?? '#64748b'
  const live = phase !== 'IDLE' && phase !== 'COMPLETE' && phase !== 'STOPPED'

  return (
    <div
      className="pointer-events-none fixed left-1/2 -translate-x-1/2 z-[60] flex items-center gap-2 px-3 py-1 rounded-sm"
      style={{
        top: 44,
        background: '#06070dcc',
        border: `1px solid ${color}66`,
        boxShadow: `0 0 14px ${color}44`,
        fontFamily: 'JetBrains Mono, monospace',
      }}
    >
      <span
        style={{
          width: 8, height: 8, borderRadius: '50%', background: color,
          boxShadow: `0 0 6px ${color}`,
          animation: live ? 'phase-pulse 1.1s ease-in-out infinite' : 'none',
        }}
      />
      <span className="text-[11px] font-bold tracking-[0.22em]" style={{ color }}>
        {phase}
      </span>
      {detail && (
        <span className="text-[10px]" style={{ color: '#94a3b8' }}>
          · {detail}
        </span>
      )}
      <style>{`@keyframes phase-pulse{0%,100%{opacity:1}50%{opacity:.35}}`}</style>
    </div>
  )
}
