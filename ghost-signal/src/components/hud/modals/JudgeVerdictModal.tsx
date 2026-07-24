import { useGhostStore } from '@/lib/store'

/** Yellow-themed pixel modal showing the last judge verdict + reasoning. */
export default function JudgeVerdictModal() {
  const { closeModal, lastVerdict, mainScreenFeed } = useGhostStore()

  // Collect judge-role feed lines for a compact history display
  const judgeLines = mainScreenFeed.filter(l => l.variant === 'judge').slice(-8)

  const v = lastVerdict?.verdict
  const blueHeld = v === 'failure' || v === 'failed'

  const verdictColor =
    v === 'success' ? '#ff4444' :
    blueHeld ? '#44ff88' :
    v === 'partial' ? '#ff8800' : '#ffdd00'

  const verdictLabel =
    v === 'success' ? 'BREACH — RED WIN' :
    blueHeld ? 'BLOCKED — BLUE WIN' :
    v === 'partial' ? 'PARTIAL BREACH' : '— AWAITING —'

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative w-[560px] rounded-sm flex flex-col"
        style={{
          background: '#0d1107',
          border: '2px solid #ffdd00',
          boxShadow: '0 0 40px #ffdd0028, 0 0 80px #ffdd0010',
          maxHeight: 640,
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Title bar */}
        <div
          className="flex items-center justify-between px-3 py-2"
          style={{ borderBottom: '1px solid #ffdd0030', background: '#ffdd000e' }}
        >
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: '#ffdd00', boxShadow: '0 0 6px #ffdd00' }} />
            <span className="text-[10px] font-bold tracking-[0.18em]" style={{ color: '#ffdd00' }}>
              ARBITER · VERDICT MONITOR
            </span>
          </div>
          <button
            onClick={closeModal}
            className="text-[10px] font-mono opacity-50 hover:opacity-100"
            style={{ color: '#ffdd00' }}
          >
            [ESC]
          </button>
        </div>

        <div className="p-3 space-y-3 overflow-y-auto flex-1">
          {/* Current verdict banner */}
          <div
            className="p-3 rounded-sm flex items-center justify-between"
            style={{ background: '#141208', border: `1px solid ${verdictColor}60` }}
          >
            <div className="space-y-1">
              <div className="text-[8px] font-mono tracking-[0.2em]" style={{ color: '#ffdd0080' }}>
                LAST VERDICT
              </div>
              <div className="text-[18px] font-bold font-mono" style={{ color: verdictColor, textShadow: `0 0 12px ${verdictColor}60` }}>
                {verdictLabel}
              </div>
            </div>
            {lastVerdict && (
              <div
                className="px-3 py-2 rounded-sm text-center"
                style={{ background: '#0d1117', border: '1px solid #21262d', minWidth: 80 }}
              >
                <div className="text-[8px] font-mono mb-1" style={{ color: '#3d4a5a' }}>HARM SCORE</div>
                {(() => {
                  const s = lastVerdict.harmScore
                  const unit = s <= 1 ? s * 100 : Math.min(s, 100)
                  const norm = unit / 100
                  return (
                <div
                  className="text-[22px] font-bold font-mono"
                  style={{
                    color: norm > 0.6 ? '#ff4444' : norm > 0.3 ? '#ff8800' : '#44ff88',
                  }}
                >
                  {unit.toFixed(0)}
                </div>
                  )
                })()}
                <div className="text-[7px] font-mono" style={{ color: '#3d4a5a' }}>/100</div>
              </div>
            )}
          </div>

          {/* Judge reasoning */}
          {lastVerdict?.reason && (
            <div
              className="p-2 rounded-sm"
              style={{ background: '#161b22', border: '1px solid #21262d' }}
            >
              <div className="text-[8px] font-mono tracking-[0.2em] mb-2" style={{ color: '#3d4a5a' }}>
                JUDGE REASONING
              </div>
              <div
                className="text-[10px] font-mono leading-relaxed"
                style={{ color: '#c9b857', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
              >
                {lastVerdict.reason}
              </div>
            </div>
          )}

          {/* Recent judge feed */}
          {judgeLines.length > 0 && (
            <div style={{ borderTop: '1px solid #21262d', paddingTop: 10 }}>
              <div className="text-[8px] font-mono tracking-[0.2em] mb-2" style={{ color: '#3d4a5a' }}>
                VERDICT HISTORY  ({judgeLines.length} entries)
              </div>
              <div className="space-y-1">
                {judgeLines.map(line => (
                  <div
                    key={line.id}
                    className="px-2 py-1.5 rounded-sm flex items-start gap-2"
                    style={{ background: '#141208', borderLeft: '2px solid #ffdd0030' }}
                  >
                    <span className="text-[8px] font-mono shrink-0" style={{ color: '#ffdd0060' }}>
                      R{line.round}
                    </span>
                    <span className="text-[9px] font-mono" style={{ color: '#9a8a3a', wordBreak: 'break-all' }}>
                      {line.body.slice(0, 200)}{line.body.length > 200 ? '…' : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Empty state */}
          {!lastVerdict && judgeLines.length === 0 && (
            <div
              className="py-8 text-center"
              style={{ color: '#3d4a5a' }}
            >
              <div className="text-[12px] font-mono mb-2">⚖</div>
              <div className="text-[10px] font-mono tracking-[0.15em]">NO VERDICTS YET</div>
              <div className="text-[9px] font-mono mt-1 opacity-60">Launch a battle to see judge output here</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
