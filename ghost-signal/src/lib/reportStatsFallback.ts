/**
 * When report-composer reads PostgreSQL with no execution_traces / zero ledger,
 * the narrative JSON still shows all-zero statistics while arena-core's in-memory
 * BattleSession (GET /api/battles/{id}) has the real scoreboard. Merge for display.
 */
import type { NarrativeReport, NarrativeStatistics } from '@/types'
import type { BattleState } from '@/lib/arenaApi'

/** Stats block from composer JSON or narrative — max harm optional on some payloads */
export type BattleStatsBase = {
  total_rounds: number
  red_wins: number
  blue_wins: number
  attack_success_rate: number
  defense_rate: number
  avg_harmfulness_score: number
  max_harmfulness_score?: number
}

function hasNonZeroStats(s: BattleStatsBase): boolean {
  return (
    (s.total_rounds ?? 0) > 0
    || (s.red_wins ?? 0) > 0
    || (s.blue_wins ?? 0) > 0
  )
}

function mergeArenaIntoStats(arena: BattleState, base: BattleStatsBase): BattleStatsBase {
  const cr = Number(arena.current_round ?? 0)
  const rw = Number(arena.red_wins ?? 0)
  const bw = Number(arena.blue_wins ?? 0)
  const adjudicated = rw + bw
  const total = Math.max(cr, adjudicated, 1)
  return {
    ...base,
    total_rounds: total,
    red_wins: rw,
    blue_wins: bw,
    attack_success_rate: total ? Math.round((rw / total) * 10_000) / 10_000 : 0,
    defense_rate: total ? Math.round((bw / total) * 10_000) / 10_000 : 0,
  }
}

export function augmentNarrativeStatsFromArena(
  report: NarrativeReport,
  arena: BattleState | null,
): NarrativeReport {
  if (!arena || !report.statistics) return report
  if (hasNonZeroStats(report.statistics)) return report
  const cr = Number(arena.current_round ?? 0)
  const rw = Number(arena.red_wins ?? 0)
  const bw = Number(arena.blue_wins ?? 0)
  if (cr === 0 && rw === 0 && bw === 0) return report
  const merged = mergeArenaIntoStats(arena, report.statistics)
  const statistics: NarrativeStatistics = {
    ...merged,
    max_harmfulness_score: merged.max_harmfulness_score ?? report.statistics.max_harmfulness_score ?? 0,
  }
  return { ...report, statistics }
}

/**
 * ReportModal JSON from report-composer GET /v1/reports/{id}.
 * Generic preserves `rounds` and every other field on the payload.
 */
export function augmentComposerStatisticsFromArena<T extends { statistics: BattleStatsBase }>(
  payload: T,
  arena: BattleState | null,
): T {
  if (!arena || !payload.statistics) return payload
  if (hasNonZeroStats(payload.statistics)) return payload
  const cr = Number(arena.current_round ?? 0)
  const rw = Number(arena.red_wins ?? 0)
  const bw = Number(arena.blue_wins ?? 0)
  if (cr === 0 && rw === 0 && bw === 0) return payload
  return {
    ...payload,
    statistics: mergeArenaIntoStats(arena, payload.statistics),
  }
}
