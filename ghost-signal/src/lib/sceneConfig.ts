export const CANVAS = { width: 1280, height: 720 } as const

export const DEPTH = {
  BG:      0,
  DECOR:   1,
  OBJECTS: 2,
  AGENTS:  3,
  EFFECTS: 4,
  UI:      5,
} as const

// Zone rectangles — x,y = top-left corner, w/h = dimensions
// Top 60% = battle area (y 60–540), bottom 40% = judge/reporter (y 540–680)
export const ZONES = {
  red:      { x: 0,    y: 60,  w: 380, h: 480 },
  center:   { x: 380,  y: 60,  w: 520, h: 480 },
  blue:     { x: 900,  y: 60,  w: 380, h: 480 },
  judge:    { x: 0,    y: 540, w: 640, h: 140 },
  reporter: { x: 640,  y: 540, w: 640, h: 140 },
} as const

// Agent home spawn positions
export const SPAWN: Record<string, { x: number; y: number }> = {
  atk1:     { x: 130,  y: 260 },
  atk2:     { x: 240,  y: 360 },
  atk3:     { x: 150,  y: 470 },   // Recon Analyst (3rd red assisting model)
  def1:     { x: 1050, y: 260 },
  def2:     { x: 1150, y: 360 },
  def3:     { x: 1120, y: 470 },   // Recon Analyst (3rd blue assisting model)
  // Participant fighters — the connected external projects. One per side,
  // forward of the assisting agents (toward center) so they lead into combat.
  redFighter:  { x: 320, y: 360 },
  blueFighter: { x: 960, y: 360 },
  victim:   { x: 640,  y: 290 },
  judge:    { x: 430,  y: 636 },
  reporter: { x: 900,  y: 620 },
}

// Reporter multi-zone walking route (in order)
export const REPORTER_ROUTE: Array<{ x: number; y: number }> = [
  { x: 900,  y: 620 },  // 0 — home desk
  { x: 180,  y: 320 },  // 1 — red zone gather
  { x: 640,  y: 290 },  // 2 — AI core inspect
  { x: 1100, y: 320 },  // 3 — blue zone gather
  { x: 430,  y: 636 },  // 4 — judge zone observe
  { x: 1160, y: 640 },  // 5 — printer
  { x: 900,  y: 620 },  // 6 — home
]

export const PRINTER_POS = { x: 1160, y: 640 } as const

/** Judge desk verdict console — must match OpsScene `buildObjects` hit zone */
export const JUDGE_CONSOLE = { x: 336, y: 548, w: 222, h: 88 } as const

// Combat meeting positions in the center zone.
// Red team gathers on the left side, blue on the right.
// Index 0 = first fighter, index 1 = second fighter (staggered vertically).
export const COMBAT_POS = {
  red:  [{ x: 490, y: 265 }, { x: 490, y: 335 }],
  blue: [{ x: 795, y: 265 }, { x: 795, y: 335 }],
} as const

/** Places an agent has business at, and why.
 *
 * Idle motion used to be a random walk inside each zone, which read as jitter. An
 * errand is a destination with a reason attached, so movement on screen corresponds
 * to something the role actually does. Coordinates sit inside or on the edge of the
 * role's own zone; nobody walks through the target's station.
 */
export interface Errand {
  /** Shown above the agent while it travels. Keep it short — it is a label, not prose. */
  reason: string
  x: number
  y: number
  /** Beats to linger on arrival, in ms. */
  dwell: [number, number]
}

export const ERRANDS: Record<string, Errand[]> = {
  // Attackers: their own consoles, the payload bench, and the boundary they probe.
  atk1: [
    { reason: 'reading the last verdict', x: 90,  y: 170, dwell: [900, 1600] },
    { reason: 'drafting a payload',       x: 250, y: 220, dwell: [1200, 2000] },
    { reason: 'probing the boundary',     x: 350, y: 300, dwell: [700, 1200] },
  ],
  atk2: [
    { reason: 'checking technique notes', x: 120, y: 420, dwell: [900, 1500] },
    { reason: 'comparing past rounds',    x: 280, y: 470, dwell: [1000, 1800] },
  ],
  atk3: [
    { reason: 'recon on the target',      x: 340, y: 430, dwell: [900, 1500] },
    { reason: 'logging what was learned', x: 120, y: 490, dwell: [900, 1500] },
  ],
  redFighter: [
    { reason: 'staging the next attempt', x: 300, y: 260, dwell: [700, 1200] },
    { reason: 'reading the defence',      x: 350, y: 360, dwell: [700, 1200] },
  ],
  // Defenders: the filter bench, the rule board, the gate they hold.
  def1: [
    { reason: 'tuning the input gate',    x: 1180, y: 180, dwell: [900, 1600] },
    { reason: 'watching the gate',        x: 940,  y: 280, dwell: [700, 1300] },
  ],
  def2: [
    { reason: 'updating detection rules', x: 1200, y: 420, dwell: [1000, 1800] },
    { reason: 'reviewing what got past',  x: 960,  y: 470, dwell: [900, 1500] },
  ],
  blueFighter: [
    { reason: 'holding the gate',         x: 930,  y: 300, dwell: [700, 1200] },
    { reason: 'checking the output side', x: 1160, y: 340, dwell: [700, 1200] },
  ],
  // The referee reads transcripts and consults the rubric.
  judge: [
    { reason: 'reviewing the transcript', x: 120, y: 600, dwell: [1200, 2000] },
    { reason: 'checking the rubric',      x: 420, y: 610, dwell: [1000, 1700] },
    { reason: 'recording the verdict',    x: 560, y: 600, dwell: [900, 1500] },
  ],
  // The reporter collects material and files it.
  reporter: [
    { reason: 'collecting round data',    x: 740,  y: 600, dwell: [1100, 1900] },
    { reason: 'reading the traces',       x: 980,  y: 610, dwell: [1200, 2000] },
    { reason: 'filing the report',        x: 1200, y: 600, dwell: [1000, 1700] },
  ],
}

/** Errand to run when a particular thing happens, so movement follows the run.
 *  Keyed by the event the backend publishes; the value indexes into ERRANDS. */
export const ERRAND_ON_EVENT: Record<string, Record<string, number>> = {
  'red.attack.sent':   { redFighter: 0, atk1: 1 },
  'blue.decision':     { blueFighter: 0, def1: 0 },
  'judge.verdict':     { judge: 0, atk2: 1 },
  'battle.complete':   { reporter: 2, judge: 2 },
  'report.ready':      { reporter: 2 },
}
