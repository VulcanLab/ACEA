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
