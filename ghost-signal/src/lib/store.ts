import { create } from 'zustand'
import type {
  AgentData, LogEntry, ModalType, NarrativeReport, ZoneInsights, MainScreenChatLine, ChatMsg,
} from '@/types'
import type { ServiceRecord, BattleSummary } from './arenaApi'
import { INITIAL_AGENTS } from './agentConfig'

interface ModalPayload {
  type: ModalType
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data?: any
}

export type BattleMode = 'mock' | 'live'
export type BattleStatus = 'idle' | 'running' | 'paused' | 'stopping' | 'complete' | 'error'

interface GhostStore {
  agents: Map<string, AgentData>
  log: LogEntry[]
  modal: ModalPayload
  missionId: string
  connected: boolean
  lastReport: NarrativeReport | null
  zoneInsights: ZoneInsights | null

  // Live battle state
  battleMode: BattleMode
  battleStatus: BattleStatus
  sessionId: string | null
  services: ServiceRecord[]
  backendOnline: boolean
  // All backend battles (running/paused/complete), polled ~10s. Feeds the battle
  // sidebar + the ATTACH picker from one source.
  battles: BattleSummary[]
  // Per-battle improvement loop selection (default off). Gates assist/ASIS visuals.
  innerLoopEnabled: boolean
  outerLoopEnabled: boolean
  // True while a round's combat animation is playing. Self-improve visuals defer
  // until this clears so phases never overlap on screen.
  combatBusy: boolean

  // Human-readable current pipeline phase (recon → round → judging → ASIS → …)
  // for the on-screen stage indicator. Updated from live WS events.
  phase: string
  phaseDetail: string

  // Per-round scoreboard (updated on judge.verdict, reset on new battle)
  currentRound: number
  redWins: number
  blueWins: number
  lastEvolutionHint: Record<string, unknown> | null

  // Execution terminal state
  liveAttackLog: string[]
  liveDefenseLog: string[]
  lastAttack: { payload: string; type: string; confidence: number } | null
  lastDefense: { decision: string; reason: string; confidence: number } | null
  lastVerdict: { verdict: string; harmScore: number; reason: string } | null

  /** Center mainscreen — chat-style feed (red / blue / target / judge), from live WS only */
  mainScreenFeed: MainScreenChatLine[]
  pendingTargetPrompt: string | null   // text sent to target-ai this round (after blue allow)

  /** Per-role thinking chat per side — populated ONLY from real WS events (never mock). */
  agentChat: { red: ChatMsg[]; blue: ChatMsg[] }
  /** Last launch config, for the RUN AGAIN control. */
  lastLaunch: { red: string; blue: string; rounds: number; inner: boolean; outer: boolean } | null

  /**
   * When true, Phaser pauses all tweens + timers so the arena is visually
   * static (used after STOP; mock ambient bus stays off until next LAUNCH).
   */
  sceneFrozen: boolean

  updateAgent: (id: string, patch: Partial<AgentData>) => void
  pushLog: (entry: Omit<LogEntry, 'id' | 'timestamp'>) => void
  openModal: (type: ModalType, data?: unknown) => void
  closeModal: () => void
  setConnected: (v: boolean) => void
  setLastReport: (report: NarrativeReport | null) => void
  setZoneInsights: (insights: ZoneInsights | null) => void

  setBattleMode: (mode: BattleMode) => void
  setBattleStatus: (status: BattleStatus) => void
  setSessionId: (id: string | null) => void
  setLoopFlags: (inner: boolean, outer: boolean) => void
  setCombatBusy: (busy: boolean) => void
  setBattles: (b: BattleSummary[]) => void
  setServices: (services: ServiceRecord[]) => void
  setBackendOnline: (online: boolean) => void
  setPhase: (phase: string, detail?: string) => void

  setCurrentRound: (n: number) => void
  setRoundWins: (red: number, blue: number) => void
  setLastEvolutionHint: (hint: Record<string, unknown> | null) => void

  setLiveAttackLog: (log: string[]) => void
  setLiveDefenseLog: (log: string[]) => void
  setLastAttack: (a: { payload: string; type: string; confidence: number } | null) => void
  setLastDefense: (d: { decision: string; reason: string; confidence: number } | null) => void
  setLastVerdict: (v: { verdict: string; harmScore: number; reason: string } | null) => void

  setPendingTargetPrompt: (prompt: string | null) => void
  appendMainScreenFeed: (line: Omit<MainScreenChatLine, 'id'> & { id?: string }) => void
  clearMainScreenFeed: () => void
  appendAgentChat: (side: 'red' | 'blue', msg: Omit<ChatMsg, 'id'> & { id?: string }) => void
  clearAgentChat: () => void
  setLastLaunch: (l: GhostStore['lastLaunch']) => void

  setSceneFrozen: (frozen: boolean) => void

  /**
   * Atomically resets all live-battle state back to mock-idle.
   * Called by arenaWsClient after the battle.stopped animation completes.
   */
  resetToMock: () => void
}

export const useGhostStore = create<GhostStore>((set) => ({
  agents: new Map(INITIAL_AGENTS.map(a => [a.id, { ...a }])),
  log: [],
  modal: { type: null },
  missionId: `OPS-${String(Date.now()).slice(-4)}`,
  connected: false,
  lastReport: null,
  zoneInsights: null,

  battleMode: 'mock',
  battleStatus: 'idle',
  phase: 'IDLE',
  phaseDetail: 'Standing by',
  sessionId: null,
  innerLoopEnabled: false,
  outerLoopEnabled: false,
  combatBusy: false,
  services: [],
  backendOnline: false,
  battles: [],

  currentRound: 0,
  redWins: 0,
  blueWins: 0,
  lastEvolutionHint: null,

  liveAttackLog: [],
  liveDefenseLog: [],
  lastAttack: null,
  lastDefense: null,
  lastVerdict: null,

  mainScreenFeed: [],
  agentChat: { red: [], blue: [] },
  lastLaunch: null,
  pendingTargetPrompt: null,

  sceneFrozen: true,

  updateAgent: (id, patch) =>
    set(s => {
      const agents = new Map(s.agents)
      const current = agents.get(id)
      if (!current) return s
      agents.set(id, { ...current, ...patch })
      return { agents }
    }),

  pushLog: entry =>
    set(s => ({
      log: [
        {
          ...entry,
          id: Math.random().toString(36).slice(2),
          timestamp: Date.now(),
        },
        ...s.log.slice(0, 199),
      ],
    })),

  openModal: (type, data) => set({ modal: { type, data } }),
  closeModal: () => set({ modal: { type: null } }),
  setConnected: connected => set({ connected }),
  setLastReport: lastReport => set({ lastReport }),
  setZoneInsights: zoneInsights => set({ zoneInsights }),

  setBattleMode: battleMode => set({ battleMode }),
  setBattleStatus: battleStatus => set({ battleStatus }),
  setLoopFlags: (inner, outer) => set({ innerLoopEnabled: inner, outerLoopEnabled: outer }),
  setCombatBusy: busy => set({ combatBusy: busy }),
  setBattles: b => set({ battles: b }),
  setPhase: (phase, detail = '') => set({ phase, phaseDetail: detail }),
  setSessionId: sessionId => set({ sessionId }),
  setServices: services => set({ services }),
  setBackendOnline: backendOnline => set({ backendOnline }),

  setCurrentRound: (n) => set({ currentRound: n }),
  setRoundWins: (red, blue) => set({ redWins: red, blueWins: blue }),
  setLastEvolutionHint: (hint) => set({ lastEvolutionHint: hint }),

  setLiveAttackLog: (log) => set({ liveAttackLog: log }),
  setLiveDefenseLog: (log) => set({ liveDefenseLog: log }),
  setLastAttack: (a) => set({ lastAttack: a }),
  setLastDefense: (d) => set({ lastDefense: d }),
  setLastVerdict: (v) => set({ lastVerdict: v }),

  setPendingTargetPrompt: prompt => set({ pendingTargetPrompt: prompt }),
  appendMainScreenFeed: line =>
    set(s => {
      const id = line.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
      const { id: _drop, ...rest } = line as MainScreenChatLine & { id?: string }
      return { mainScreenFeed: [...s.mainScreenFeed.slice(-120), { ...rest, id }] }
    }),
  clearMainScreenFeed: () => set({ mainScreenFeed: [], pendingTargetPrompt: null }),
  appendAgentChat: (side, msg) =>
    set(s => {
      const id = msg.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
      const { id: _drop, ...rest } = msg as ChatMsg & { id?: string }
      const next = [...s.agentChat[side].slice(-199), { ...rest, id }]
      return { agentChat: { ...s.agentChat, [side]: next } }
    }),
  clearAgentChat: () => set({ agentChat: { red: [], blue: [] } }),
  setLastLaunch: l => set({ lastLaunch: l }),

  setSceneFrozen: frozen => set({ sceneFrozen: frozen }),

  resetToMock: () => set({
    battleMode: 'mock',
    battleStatus: 'idle',
    sessionId: null,
    connected: false,
    currentRound: 0,
    redWins: 0,
    blueWins: 0,
    lastEvolutionHint: null,
    /** After a live session, restore the initial quiet mock desk (no ambient float / mock-bus spam). */
    sceneFrozen: true,
  }),
}))
