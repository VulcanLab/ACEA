import { useGhostStore, type BattleStatus } from '@/lib/store'
import { arenaWsClient } from '@/lib/arenaWsClient'
import { arenaApi } from '@/lib/arenaApi'

/**
 * Attach the UI to a backend battle session and enter LIVE mode. Used by both a
 * fresh LAUNCH and by re-attaching to an already-running battle (from the ATTACH
 * dropdown or the battle sidebar). The per-session WS replays that battle's
 * history from id "0", so a reconnecting client catches up on the whole battle.
 *
 * Store-driven (reads setters off the store itself) so any component can call it
 * with just the session identity.
 */
export function connectLive(
  sessionId: string,
  redServiceId: string,
  blueServiceId: string,
  status: string,
): void {
  const g = useGhostStore.getState()
  // Reset per-battle view state so a reattach doesn't show stale data or dupes;
  // the replayed history repopulates everything.
  g.clearMainScreenFeed()
  g.clearAgentChat()
  g.setSceneFrozen(false)
  g.setLastVerdict(null)
  g.setCurrentRound(0)
  g.setRoundWins(0, 0)
  g.setLastEvolutionHint(null)
  g.setSessionId(sessionId)
  g.setBattleStatus((status as BattleStatus) ?? 'running')
  g.setBattleMode('live')
  g.setConnected(true)
  // Restore THIS battle's evolution-loop flags BEFORE connecting, so the
  // outer-gated council / ASIS visuals and the inner-gated assist animations
  // render correctly when attaching (or re-attaching) to a battle the UI did not
  // launch (e.g. an API/headless run). The per-session stream replays history on
  // connect, so the flags must be set first or early replayed ASIS events get
  // gated off. Connect happens once the flags are known (or the fetch fails).
  void arenaApi.getBattle(sessionId)
    .then(s => g.setLoopFlags(!!s.inner_loop_enabled, !!s.outer_loop_enabled))
    .catch(() => { /* leave current flags */ })
    .finally(() => arenaWsClient.connect(sessionId))
  g.pushLog({
    agentId: 'system',
    message: `Connected → session ${sessionId.slice(0, 8)} (red:${redServiceId.slice(0, 6)} vs blue:${blueServiceId.slice(0, 6)})`,
    state: 'acting',
  })
}
