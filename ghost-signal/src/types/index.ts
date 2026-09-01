export type AgentState =
  | 'idle'
  | 'thinking'
  | 'acting'
  | 'success'
  | 'failed'
  | 'moving'
  | 'gathering'
  | 'writing'
  | 'printing'

export type AgentRole =
  | 'attacker'
  | 'defender'
  | 'victim'
  | 'judge'
  | 'reporter'

export type Zone = 'red' | 'blue' | 'center' | 'judge' | 'reporter'

export type ModalType =
  | 'terminal_red'
  | 'terminal_blue'
  | 'printer'
  | 'core'
  | 'target_screen'
  | 'adapter_config'
  | 'judge_verdict'
  | 'model_health'
  | 'battle_readiness'
  | null

/** Streaming chat-style feed for the center mainscreen — real WS events only. */
export type MainScreenChatVariant =
  | 'round_start'
  | 'attack'
  | 'blocked'
  | 'allowed'
  | 'target_raw'
  | 'delivered'
  /** Output filter unchanged — avoids duplicating RAW on MAINSCREEN */
  | 'delivered_passthrough'
  | 'reply'
  | 'judge'

/** Per-role "thinking" chat message — populated ONLY from real backend events. */
export type ChatRole =
  | 'recon' | 'strategy' | 'rewriter' | 'enhancer'
  | 'attack' | 'defense' | 'judge' | 'target' | 'system'

export interface ChatMsg {
  id: string
  role: ChatRole
  text: string
  meta?: string
  ts: number
  round: number
}

export interface MainScreenChatLine {
  id: string
  round: number
  role: 'red' | 'blue' | 'target' | 'system'
  variant: MainScreenChatVariant
  body: string
  meta?: string
  ts: number
}

export interface AgentData {
  id: string
  role: AgentRole
  zone: Zone
  state: AgentState
  label: string
  model: string
  message: string
  color: string
}

export interface LogEntry {
  id: string
  timestamp: number
  agentId: string
  message: string
  state: AgentState
}

export type AgentEvent =
  | { type: 'state_change'; agentId: string; state: AgentState; message: string }
  | { type: 'judge_verdict'; score: number; result: 'success' | 'failed'; reason: string }
  | { type: 'reporter_move'; destination: Zone }
  | { type: 'reporter_patrol'; zone: Zone }
  | { type: 'print_report'; content: string }
  | { type: 'system_alert'; message: string }
  | { type: 'combat_start'; attackerIds: string[]; defenderIds: string[]; target: string }
  /** STOP / battle end — Phaser returns sprites to SPAWN home (instant on STOP) */
  | { type: 'arena_force_home'; instant: boolean; resumeAmbientFloat: boolean }
  | { type: 'comprehension'; active: boolean; message: string }

// ── Report Writer types ───────────────────────────────────────────────────────

export interface ZoneInsights {
  red_team: string
  target_ai: string
  blue_team: string
  judge: string
  overall_summary: string
}

export interface NarrativeStatistics {
  total_rounds: number
  red_wins: number
  blue_wins: number
  attack_success_rate: number
  defense_rate: number
  avg_harmfulness_score: number
  max_harmfulness_score: number
}

export interface NarrativeReport {
  session_id: string
  zone_insights: ZoneInsights
  narrative: string
  statistics: NarrativeStatistics
  cached?: boolean
  mode?: string
  status?: string
  red_service_id?: string | null
  blue_service_id?: string | null
  created_at?: string | null
  ended_at?: string | null
}
