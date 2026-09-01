/**
 * Arena Core REST API client.
 * Base URL: `window.__ARENA_API_URL__` (runtime) → VITE_API_URL (build) → localhost.
 */
import { getArenaApiUrl } from '@/lib/runtimeConfig'

export interface ServiceRecord {
  id: string
  name: string
  url: string
  type: string
  token?: string
  capabilities?: {
    is_platform_default?: boolean
    evolution_wrapper?: boolean
    supports_input_guard?: boolean
    supports_output_guard?: boolean
    supports_attack_generation?: boolean
    [k: string]: unknown
  }
}

export interface BattleStartResult {
  session_id: string
  status: string
}

export interface ReadinessSide {
  service_id: string | null
  name: string | null
  origin: 'user' | 'default' | null
  admitted: boolean
  health: 'ok' | 'down'
  capabilities: Record<string, unknown>
  reasons: string[]
}
export interface ReadinessIssue { scope: 'red' | 'blue' | 'models'; code: string; message: string }
export interface BattleReadiness {
  red: ReadinessSide
  blue: ReadinessSide
  models: { ok: boolean; failures: { model: string; roles: string[]; error: string }[] }
  verdict: { can_launch: boolean; blockers: ReadinessIssue[]; warnings: ReadinessIssue[] }
}

export interface BattleState {
  id: string
  mode: string
  status: string
  max_rounds: number
  current_round: number
  red_wins: number
  blue_wins: number
  red_service_id: string
  blue_service_id: string
  inner_loop_enabled?: boolean
}

export interface RoundTrace {
  round: number
  attack_payload: string
  attack_type: string
  defense_decision: string
  defense_reason: string
  target_response: string | null
  red_success: boolean
  blue_success: boolean
  harmfulness_score: number
  judge_reasoning: string
}

export interface BattleReport extends BattleState {
  rounds: RoundTrace[]
}

export class ArenaHttpError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(`HTTP ${status}`)
    this.name = 'ArenaHttpError'
  }
}

async function parseErrorBody(res: Response): Promise<unknown> {
  try {
    const j = (await res.json()) as { detail?: unknown }
    return j.detail !== undefined ? j.detail : j
  } catch {
    try {
      return await res.text()
    } catch {
      return null
    }
  }
}

export interface AdapterConfigErrorDetail {
  code: 'ADAPTER_CONFIG_MISSING'
  message: string
}

export interface AdapterHealthErrorDetail {
  code: 'ADAPTER_HEALTH_FAILED'
  message: string
  red_ok: boolean
  blue_ok: boolean
  hint?: string
}

/** Plain-text explanation for POST /api/battles failures (shown in HUD). */
export function describeBattleStartFailure(e: unknown): string {
  if (!(e instanceof ArenaHttpError)) return String(e)
  const d = e.detail as Record<string, unknown> | undefined
  if (
    e.status === 503
    && d
    && d.code === 'ADAPTER_HEALTH_FAILED'
  ) {
    const x = d as unknown as AdapterHealthErrorDetail
    return [x.message + ` (red_ok=${x.red_ok}, blue_ok=${x.blue_ok}).`, x.hint]
      .filter(Boolean)
      .join(' ')
  }
  if (d && typeof d.message === 'string') return d.message
  return e.message || `HTTP ${e.status}`
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${getArenaApiUrl()}${path}`)
  if (!res.ok) throw new ArenaHttpError(res.status, await parseErrorBody(res))
  return res.json() as Promise<T>
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getArenaApiUrl()}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new ArenaHttpError(res.status, await parseErrorBody(res))
  return res.json() as Promise<T>
}

export interface PreflightModel {
  model: string
  roles: string[]
  ok: boolean
  error: string
  latency_seconds?: number
}
export interface PreflightResult {
  ran: boolean
  ok: boolean
  models: PreflightModel[]
}

export interface BattleSummary {
  session_id: string
  status: string
  current_round: number
  max_rounds: number | null   // null = infinite mode
  red_wins: number
  blue_wins: number
  red_service_id: string
  blue_service_id: string
}

/** One day that has recorded battles, as offered by the history rail. */
export interface BattleDay {
  date: string            // YYYY-MM-DD
  battles: number
  complete: number
  red_wins: number
  blue_wins: number
}

/** A battle read back from storage. `live` is false once the process that ran it
 *  has gone: its report is still there, but there is nothing left to attach to. */
export interface BattleHistoryEntry extends BattleSummary {
  created_at: string | null
  ended_at: string | null
  live: boolean
}

/** Printable report for a session, served by the report composer. */
export const reportUrl = (sessionId: string) =>
  `${(import.meta.env.VITE_REPORT_URL as string | undefined) ?? 'http://localhost:8005'}` +
  `/v1/reports/${sessionId}/pdf`

export const arenaApi = {
  health: () => apiGet<{ status: string }>('/health'),

  listServices: () => apiGet<ServiceRecord[]>('/api/services'),

  // Pre-flight readiness: per-side admission + origin, model reachability, verdict.
  getBattleReadiness: (red: string, blue: string) =>
    apiGet<BattleReadiness>(
      `/api/battle-readiness?red=${encodeURIComponent(red)}&blue=${encodeURIComponent(blue)}`,
    ),

  // LiteLLM model preflight — per-model reachability (which model is down + why).
  recheckPreflight: () => apiPost<PreflightResult>('/api/preflight/recheck'),
  getHealth: () => apiGet<{ litellm_preflight?: PreflightResult }>('/health'),

  // Assisting-AI roster — the agents that work on each team's project.
  getAgentRoster: () =>
    apiGet<{
      red: { id: string; slot: string; role: string; model: string }[]
      blue: { id: string; slot: string; role: string; model: string }[]
      shared: { id: string; slot: string; role: string; model: string }[]
    }>('/api/agents/roster'),

  listBattles: () => apiGet<BattleSummary[]>('/api/battles'),

  /** Days that have battles, newest first. Read from storage, so it survives a
   *  restart — the live list above does not. */
  listBattleDays: () => apiGet<BattleDay[]>('/api/battles/history/dates'),

  /** Battles recorded on one day (YYYY-MM-DD), newest first. */
  listBattleHistory: (date: string) =>
    apiGet<BattleHistoryEntry[]>(`/api/battles/history?date=${encodeURIComponent(date)}`),

  startBattle: (
    red_service_id: string,
    blue_service_id: string,
    max_rounds = 3,
    mode = 'deathmatch',
    inner_loop_enabled = false,
  ) => {
    // max_rounds = 0 means "unlimited" → switch to infinite mode and omit the
    // max_rounds field so the backend battle loop runs until the user stops.
    const body: Record<string, unknown> = {
      red_service_id,
      blue_service_id,
      mode: max_rounds === 0 ? 'infinite' : mode,
      // Improvement loops are opt-in per battle (default off).
      inner_loop_enabled,
    }
    if (max_rounds > 0) body.max_rounds = max_rounds
    return apiPost<BattleStartResult>('/api/battles', body)
  },

  getBattle: (sessionId: string) =>
    apiGet<BattleState>(`/api/battles/${sessionId}`),

  getReport: (sessionId: string) =>
    apiGet<BattleReport>(`/api/battles/${sessionId}/report`),

  pauseBattle: (sessionId: string) =>
    apiPost<{ session_id: string; status: string }>(`/api/battles/${sessionId}/pause`),

  resumeBattle: (sessionId: string) =>
    apiPost<{ session_id: string; status: string }>(`/api/battles/${sessionId}/resume`),

  stopBattle: (sessionId: string) =>
    apiPost<{ session_id: string; status: string }>(`/api/battles/${sessionId}/stop`),
}
