/**
 * Pre-flight "Battle Readiness" panel.
 *
 * Unified launch gate: shows per-side admission + origin (external vs the
 * platform's built-in default opponent), model reachability, and an overall
 * verdict. Three states — READY (green), WARNING (amber, needs ack), BLOCKED
 * (red). The confirm button only enables when the battle can truly run.
 *
 * Opened from the launch bar with modal data:
 *   { red, blue, rounds, onConfirm: () => void }
 */
import { useEffect, useState } from 'react'
import { useGhostStore } from '@/lib/store'
import { arenaApi, type BattleReadiness, type ReadinessSide } from '@/lib/arenaApi'

const OK = '#00ff88'
const WARN = '#ffb020'
const BAD = '#ff5577'
const BG = '#06070d'
const MUTED = '#64748b'
const TEXT = '#cbd5e1'

type Data = {
  red: string
  blue: string
  rounds: number
  onConfirm?: (opts?: { inner: boolean }) => void
}

function Dot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
        background: color, boxShadow: `0 0 6px ${color}`,
      }}
    />
  )
}

function SidePanel({ role, side }: { role: 'RED' | 'BLUE'; side: ReadinessSide }) {
  const roleColor = role === 'RED' ? '#ff6b6b' : '#4a9eff'
  const admitted = side.admitted
  const isDefault = side.origin === 'default'
  const statusColor = !admitted ? BAD : isDefault ? WARN : OK
  const originLabel =
    side.origin === 'default' ? 'PLATFORM-DEFAULT ⚠'
    : side.origin === 'user' ? 'EXTERNAL'
    : 'NONE'
  const caps = side.capabilities || {}
  const capRows =
    role === 'RED'
      ? [['attack generation', !!(caps.supports_attack_generation ?? caps.attack_type)]]
      : [
          ['input guard', !!caps.supports_input_guard],
          ['output guard', !!caps.supports_output_guard],
        ]

  return (
    <div
      className="flex-1 rounded-sm flex flex-col"
      style={{ border: `1px solid ${statusColor}55`, background: '#0a0b12' }}
    >
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: `1px solid ${statusColor}33`, background: `${roleColor}12` }}
      >
        <span className="text-[11px] font-bold tracking-[0.2em]" style={{ color: roleColor }}>
          {role}
        </span>
        <span className="flex items-center gap-1.5 text-[9px]" style={{ color: statusColor }}>
          <Dot color={side.health === 'ok' ? OK : BAD} />
          {side.health === 'ok' ? 'ONLINE' : 'OFFLINE'}
        </span>
      </div>
      <div className="px-3 py-2.5 text-[10px] font-mono space-y-2" style={{ color: TEXT }}>
        <div className="flex items-center justify-between">
          <span style={{ color: MUTED }}>source</span>
          <span
            className="px-1.5 py-0.5 rounded-sm text-[9px] font-bold"
            style={{
              color: isDefault ? WARN : side.origin === 'user' ? OK : MUTED,
              background: isDefault ? `${WARN}18` : side.origin === 'user' ? `${OK}18` : '#1e293b',
              border: `1px solid ${isDefault ? WARN : side.origin === 'user' ? OK : MUTED}44`,
            }}
          >
            {originLabel}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span style={{ color: MUTED }}>adapter</span>
          <span className="truncate max-w-[150px]" style={{ color: '#e2e8f0' }}>
            {side.name ?? '—'}
          </span>
        </div>
        <div className="pt-1 space-y-1" style={{ borderTop: '1px solid #1e293b' }}>
          {capRows.map(([label, on]) => (
            <div key={label as string} className="flex items-center gap-1.5">
              <span style={{ color: (on as boolean) ? OK : BAD }}>{(on as boolean) ? '✓' : '✗'}</span>
              <span style={{ color: MUTED }}>{label as string}</span>
            </div>
          ))}
          <div className="flex items-center gap-1.5">
            <span style={{ color: admitted ? OK : BAD }}>{admitted ? '✓' : '✗'}</span>
            <span style={{ color: MUTED }}>ASAP admission</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ReadinessModal() {
  const closeModal = useGhostStore(s => s.closeModal)
  const openModal = useGhostStore(s => s.openModal)
  const data = useGhostStore(s => s.modal.data) as Data | undefined
  const [r, setR] = useState<BattleReadiness | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [ack, setAck] = useState(false)
  // Per-battle strategy evolution — opt-in, default off.
  const [innerLoop, setInnerLoop] = useState(false)

  const load = async () => {
    setLoading(true); setErr(null)
    try {
      setR(await arenaApi.getBattleReadiness(data?.red ?? '', data?.blue ?? ''))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() /* eslint-disable-next-line */ }, [])

  const v = r?.verdict
  const warnings = v?.warnings ?? []
  const blockers = v?.blockers ?? []
  const canLaunch = !!v?.can_launch && (warnings.length === 0 || ack)
  const headColor = blockers.length ? BAD : warnings.length ? WARN : OK
  const headLabel = loading ? 'CHECKING…' : blockers.length ? 'BLOCKED' : warnings.length ? 'REVIEW REQUIRED' : 'READY'

  return (
    <div className="modal-backdrop" onClick={closeModal}>
      <div
        className="relative rounded-sm flex flex-col"
        style={{
          width: 620, maxHeight: '90vh', background: BG,
          border: `2px solid ${headColor}`, boxShadow: `0 0 40px ${headColor}33`,
        }}
        onClick={e => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0"
          style={{ borderBottom: `1px solid ${headColor}44`, background: `${headColor}12` }}
        >
          <span className="flex items-center gap-2 text-[11px] font-bold tracking-[0.2em]" style={{ color: headColor }}>
            <Dot color={headColor} /> PRE-FLIGHT · {headLabel}
          </span>
          <button
            type="button" onClick={closeModal}
            className="text-[10px] font-mono opacity-70 hover:opacity-100"
            style={{ color: headColor }}
          >
            [ESC]
          </button>
        </div>

        <div className="px-4 py-3 overflow-y-auto" style={{ minHeight: 200 }}>
          {loading && <p className="text-[10px] font-mono" style={{ color: MUTED }}>Probing adapters + models…</p>}
          {err && <p className="text-[10px] font-mono" style={{ color: BAD }}>readiness check failed: {err}</p>}

          {r && (
            <>
              <div className="flex gap-3 mb-3">
                <SidePanel role="RED" side={r.red} />
                <SidePanel role="BLUE" side={r.blue} />
              </div>

              {/* Models */}
              <div
                className="rounded-sm px-3 py-2 mb-3 flex items-center justify-between text-[10px] font-mono"
                style={{ border: `1px solid ${r.models.ok ? OK : BAD}44`, background: '#0a0b12' }}
              >
                <span className="flex items-center gap-1.5">
                  <Dot color={r.models.ok ? OK : BAD} />
                  <span style={{ color: MUTED }}>LiteLLM models</span>
                </span>
                <span style={{ color: r.models.ok ? OK : BAD }}>
                  {r.models.ok ? 'all reachable' : `${r.models.failures.length} unreachable`}
                </span>
              </div>

              {/* Blockers */}
              {blockers.map((b, i) => (
                <div key={`b${i}`} className="text-[10px] font-mono mb-1.5 flex gap-2" style={{ color: '#fca5a5' }}>
                  <span style={{ color: BAD }}>✗</span>
                  <span>{b.message}</span>
                </div>
              ))}
              {/* Warnings */}
              {warnings.map((w, i) => (
                <div key={`w${i}`} className="text-[10px] font-mono mb-1.5 flex gap-2" style={{ color: '#fcd9a0' }}>
                  <span style={{ color: WARN }}>⚠</span>
                  <span>{w.message}</span>
                </div>
              ))}

              {/* Warning acknowledgement */}
              {blockers.length === 0 && warnings.length > 0 && (
                <label className="flex items-start gap-2 mt-2 text-[10px] font-mono cursor-pointer" style={{ color: '#e2e8f0' }}>
                  <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} style={{ accentColor: WARN, marginTop: 2 }} />
                  <span>I understand I am battling the platform's built-in test opponent, not an external project.</span>
                </label>
              )}
            </>
          )}

          {/* In-context strategy evolution — optional per battle (default off) */}
          <div className="mt-3 pt-2.5" style={{ borderTop: '1px solid #1e293b' }}>
            <div className="text-[9px] font-mono mb-1.5 tracking-[0.08em]" style={{ color: '#7dd3fc' }}>
              EVOLUTION · OPTIONAL (default off)
            </div>
            <label className="flex items-start gap-2 mb-1 text-[10px] font-mono cursor-pointer" style={{ color: '#cbd5e1' }}>
              <input type="checkbox" checked={innerLoop} onChange={e => setInnerLoop(e.target.checked)} style={{ accentColor: OK, marginTop: 2 }} />
              <span>Inner loop — in-context strategy evolution each round (no code change)</span>
            </label>
          </div>
        </div>

        {/* Footer actions */}
        <div
          className="flex items-center justify-between px-4 py-2.5 shrink-0 gap-2"
          style={{ borderTop: `1px solid ${headColor}33`, background: '#08090f' }}
        >
          <button
            type="button" onClick={() => void load()}
            className="text-[10px] font-mono px-2 py-1 rounded-sm opacity-80 hover:opacity-100"
            style={{ color: MUTED, border: '1px solid #1e293b' }}
          >
            ↻ re-check
          </button>
          <div className="flex items-center gap-2">
            {r && r.models && !r.models.ok && (
              <button
                type="button" onClick={() => openModal('model_health')}
                className="text-[10px] font-mono px-2 py-1 rounded-sm"
                style={{ color: BAD, border: `1px solid ${BAD}55` }}
              >
                model health →
              </button>
            )}
            <button
              type="button"
              disabled={!canLaunch}
              onClick={() => { data?.onConfirm?.({ inner: innerLoop }); closeModal() }}
              className="text-[11px] font-mono font-bold px-4 py-1.5 rounded-sm tracking-[0.15em]"
              style={{
                color: canLaunch ? '#05210f' : MUTED,
                background: canLaunch ? OK : '#141821',
                border: `1px solid ${canLaunch ? OK : '#1e293b'}`,
                cursor: canLaunch ? 'pointer' : 'not-allowed',
                boxShadow: canLaunch ? `0 0 16px ${OK}55` : 'none',
              }}
            >
              ▶ CONFIRM LAUNCH
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
