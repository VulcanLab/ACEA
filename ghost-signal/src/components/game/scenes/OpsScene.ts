import Phaser from 'phaser'
import {
  CANVAS,
  DEPTH,
  ZONES,
  SPAWN,
  REPORTER_ROUTE,
  PRINTER_POS,
  COMBAT_POS,
  JUDGE_CONSOLE,
} from '@/lib/sceneConfig'
import { INITIAL_AGENTS } from '@/lib/agentConfig'
import type { AgentData, AgentState, AgentEvent, ZoneInsights, Zone } from '@/types'
import { arenaWsClient } from '@/lib/arenaWsClient'
import { useGhostStore } from '@/lib/store'

// Maps REPORTER_ROUTE step index → zoneInsights key (null = no bubble)
const STEP_ZONE_KEY: Array<keyof ZoneInsights | null> = [
  null,             // 0 — home (starting point)
  'red_team',       // 1 — red zone
  'target_ai',      // 2 — AI core
  'blue_team',      // 3 — blue zone
  'judge',          // 4 — judge zone
  'overall_summary',// 5 — printer
  null,             // 6 — home (return)
]

const STEP_PLACEHOLDER: Record<number, string> = {
  1: 'Analysing red team data...',
  2: 'Scanning AI core logs...',
  3: 'Reviewing blue defences...',
  4: 'Reading judge verdicts...',
  5: 'Compiling final report...',
}

// Runtime per-agent state kept inside Phaser
interface AgentRuntime {
  sprite: Phaser.GameObjects.Image
  tag: Phaser.GameObjects.Text
  bubble: Phaser.GameObjects.Container | null
  bubbleTimerId: ReturnType<typeof setTimeout> | null
  moveTween: Phaser.Tweens.Tween | null
  stateTween: Phaser.Tweens.Tween | null
  floatTween: Phaser.Tweens.Tween | null  // idle bob — paused during combat
  glyph: Phaser.GameObjects.Text | null   // state glyph above the sprite
  animPhase: number                        // per-agent phase offset (desync motion)
  state: AgentState
  homeX: number
  homeY: number
  data: AgentData
}

// Per-state glyph shown above the sprite (age-of-agents-style state overlay).
const STATE_GLYPH: Record<string, string> = {
  idle:      '',
  thinking:  '✦',
  acting:    '⚙',
  moving:    '»',
  gathering: '✎',
  success:   '✓',
  failed:    '✗',
  printing:  '🖶',
}

// One active combat encounter between red attackers and blue defenders
interface CombatEncounter {
  id: string
  attackerIds: string[]
  defenderIds: string[]
  target: string
  winningSide?: 'red' | 'blue'
  shieldWall?: Phaser.GameObjects.Container  // blue's barrier between red and victim
}

// ── ASIS evolver sprite state ────────────────────────────────────────────────
interface EvolverState {
  sprite:    Phaser.GameObjects.Rectangle
  label:     Phaser.GameObjects.Text
  barBg:     Phaser.GameObjects.Rectangle
  barFill:   Phaser.GameObjects.Rectangle
  status:    Phaser.GameObjects.Text
  stepDots:  Phaser.GameObjects.Arc[]    // real-phase progress markers
  stepLabel: Phaser.GameObjects.Text     // current phase caption
  stepIndex: number                       // -1 = none, 0..2 = analyze/build/bench
  pulseTween?: Phaser.Tweens.Tween        // pulse on the active step
  pacingTween?: Phaser.Tweens.Tween
  walkTween?: Phaser.Tweens.Tween
  // ── Live rewrite console (typing animation, so self-improve isn't static) ──
  consoleBg?:   Phaser.GameObjects.Rectangle
  consoleText?: Phaser.GameObjects.Text
  cursor?:      Phaser.GameObjects.Text
  cursorTween?: Phaser.Tweens.Tween
  scanline?:    Phaser.GameObjects.Rectangle
  scanTween?:   Phaser.Tweens.Tween
  typeTimer?:   Phaser.Time.TimerEvent
  lines:        string[]                   // rolling visible buffer
  typedLine:    string                     // line being typed right now
  typeQueue:    string[]                   // remaining chars of current line
  startTime: number
  gen:       number
  phase:     'analyzing' | 'editing' | 'building' | 'benchmarking' | 'done'
}

// Phase-specific "rewrite console" content — typed out char-by-char so the
// self-improve phase reads like a live hacking/benchmark session, not a frozen bar.
const ASIS_CONSOLE_LINES: Record<string, string[]> = {
  analyzing: [
    '$ asis attach --repo ./project',
    '  reading strategies.py …',
    '  ~ pick_layers(): weighting vectors',
    '+ boost technique ×1.5',
    '- drop beaten vector',
    '$ compose candidate gen',
  ],
  building: [
    '$ docker build -t candidate .',
    '  layer 3/7  ██████░░  ok',
    '  COPY arena_adapter.py',
    '$ canary GET /health → 200',
    '  asap contract intact ✓',
  ],
  benchmarking: [
    '$ bench --seeds 3 --rounds 10',
    '  seed 1/3  round 05/10  pss=0.04',
    '  seed 2/3  round 08/10  pss=0.07',
    '  seed 3/3  round 10/10  pss=0.06',
    '  μ=0.067  σ=0.047',
    '$ compare vs gen_0 baseline …',
  ],
}

// ASIS real work phases, in order. Driven by backend asis.phase events — NOT timers.
const EVOLVER_STEPS: Array<{ key: string; caption: string }> = [
  { key: 'analyzing',    caption: 'inspecting + editing repo' },
  { key: 'building',     caption: 'rebuilding image + canary' },
  { key: 'benchmarking', caption: 'benchmarking (multi-seed)' },
]

export class OpsScene extends Phaser.Scene {
  private runtime: Map<string, AgentRuntime> = new Map()
  private reporterStep = 0
  private reporterBusy = false
  /** Bumped when returning all units home — stale reporter delayedCalls bail out */
  private reporterRouteCancelTok = 0
  private evolvers: Map<'red' | 'blue', EvolverState> = new Map()
  // Which side's 3 role-models are currently huddled in a self-improve "council".
  private council: Map<'red' | 'blue', { lastSpeaker: string }> = new Map()
  private unsubWs?: () => void
  private unsubZoneInsights?: () => void  // Zustand subscription for zone insight updates
  /** Pauses / resumes all Phaser tweens + timers when LIVE battle is PAUSED */
  private unsubBattleFreeze?: () => void
  // Combat state
  private activeCombats: Map<string, CombatEncounter> = new Map()
  private inCombat: Set<string> = new Set()
  // Real judge verdict for the CURRENT live round, used to resolve the combat
  // animation with the true winner instead of a coin flip. Set by onVerdict when
  // the backend judge.verdict arrives; consumed by resolveCombat.
  private liveVerdict: 'red' | 'blue' | null = null
  private pendingCombatResolve: ((winner: 'red' | 'blue') => void) | null = null

  constructor() { super({ key: 'OpsScene' }) }

  // ── Lifecycle ──────────────────────────────────────────────────────────
  create(): void {
    this.buildFloor()
    this.buildZoneLabels()
    this.buildDecor()
    this.buildObjects()
    this.buildAgents()
    this.subscribeBus()
    this.subscribeBattleFreeze()
    this.startGlitchLoop()
    this.startLightFlicker()
    this._startIdleLifeLoop()

    // ── Debug: press C to force a 2v2 combat immediately ──────────────
    this.input.keyboard?.addKey('C').on('down', () => {
      this.onCombatStart(['redFighter'], ['blueFighter'], 'TARGET-AI')
    })
  }

  /**
   * Per-frame: hard-pin every agent name tag to its sprite. Previously each
   * move/return tween dragged the tag via its own onUpdate, so any path that
   * moved the sprite WITHOUT including the tag (or a tween stopped mid-flight)
   * left the tag stranded on the battlefield until the next full reposition —
   * the "label floats back to the character only after the battle ends" bug.
   * Pinning here makes the tag follow the sprite no matter how it moves.
   */
  update(): void {
    const t = this.time.now / 1000
    const animate = this.allowIdleAmbientMotion()
    this.runtime.forEach(rt => {
      if (!rt.tag.active || !rt.sprite.active) return
      // Pin name tag + glyph to the sprite.
      rt.tag.x = rt.sprite.x
      rt.tag.y = rt.sprite.y - 68
      if (rt.glyph) {
        rt.glyph.x = rt.sprite.x
        rt.glyph.y = rt.sprite.y - 50
      }
      this._animateAgent(rt, t, animate)
    })
  }

  /** Procedural per-state, per-role body motion (age-of-agents style). Drives
   * rotation + scale only (the y-bob tween owns vertical), so it never fights the
   * move/return tweens. Skipped while a move tween owns the sprite, while frozen,
   * or during success/failed (their own tweens play). Each role gets a distinct
   * cadence so the room reads as many different workers, not one repeated loop. */
  private _animateAgent(rt: AgentRuntime, t: number, animate: boolean): void {
    const sp = rt.sprite
    const moving = !!rt.moveTween?.isPlaying?.()
    // Roles: red attackers = atk*, blue defenders = def*, plus victim/judge/reporter.
    const isRed  = rt.data.id.startsWith('atk')
    const isBlue = rt.data.id.startsWith('def')
    const ph = rt.animPhase

    // Glyph: pulse for thinking, gentle bob for others; hide when empty.
    if (rt.glyph) {
      const g = STATE_GLYPH[rt.state] ?? ''
      if (rt.glyph.text !== g) rt.glyph.setText(g).setColor(rt.data.color)
      if (g) {
        rt.glyph.setAlpha(rt.state === 'thinking'
          ? 0.55 + 0.45 * Math.abs(Math.sin(t * 3 + ph))
          : 0.9)
        rt.glyph.y += Math.sin(t * 2 + ph) * 1.2
      }
    }

    if (!animate || moving || rt.state === 'success' || rt.state === 'failed') {
      return  // tweens own the body in these cases
    }

    switch (rt.state) {
      case 'thinking': {
        // Contemplative head-tilt + slow breathing scale.
        sp.setAngle(Math.sin(t * 2.2 + ph) * 3)
        sp.setScale(1 + Math.sin(t * 1.6 + ph) * 0.03)
        break
      }
      case 'acting': {
        // Rhythmic "strike/type" — red hits harder/faster than blue's steady work.
        const freq = isRed ? 9 : isBlue ? 6 : 7
        const amp  = isRed ? 9 : 6
        sp.setAngle(Math.sin(t * freq + ph) * amp)
        sp.setScale(1, 1 - Math.abs(Math.sin(t * freq + ph)) * 0.05)
        break
      }
      case 'gathering': {
        // Reporter scribbling — small quick wrist motion.
        sp.setAngle(Math.sin(t * 11 + ph) * 3.5)
        sp.setScale(1)
        break
      }
      // idle (and any other state): no per-frame writes here — the y-bob tween,
      // periodic idle gestures, and wandering own the motion, so we don't stomp
      // them. Leftover angle/scale from acting/thinking is cleared in onStateChange.
    }
  }

  shutdown(): void {
    this.unsubWs?.()
    this.unsubZoneInsights?.()
    this.unsubBattleFreeze?.()
    this.time.paused = false
    this.tweens.resumeAll()
    arenaWsClient.disconnect()
  }

  // ── Floor ──────────────────────────────────────────────────────────────
  private buildFloor(): void {
    // Neutral base
    for (let x = 0; x < CANVAS.width; x += 48) {
      for (let y = 60; y < CANVAS.height - 40; y += 48) {
        this.add.image(x + 24, y + 24, 'tile_neutral').setDepth(DEPTH.BG)
      }
    }

    // Zone colored overlays
    const zones: Array<{ key: keyof typeof ZONES; color: number; tile: string }> = [
      { key: 'red',      color: 0xff0000, tile: 'tile_red'    },
      { key: 'blue',     color: 0x0044ff, tile: 'tile_blue'   },
      { key: 'judge',    color: 0xffdd00, tile: 'tile_judge'  },
      { key: 'reporter', color: 0x00ff88, tile: 'tile_judge'  },
    ]

    for (const { key, color, tile } of zones) {
      const z = ZONES[key]

      // Tinted tiles
      for (let x = z.x; x < z.x + z.w; x += 48) {
        for (let y = z.y; y < z.y + z.h; y += 48) {
          this.add.image(x + 24, y + 24, tile).setDepth(DEPTH.BG)
        }
      }

      // Neon border gfx
      const gfx = this.add.graphics().setDepth(DEPTH.BG)
      gfx.lineStyle(2, color, 0.35)
      gfx.strokeRect(z.x, z.y, z.w, z.h)

      // Subtle fill
      gfx.fillStyle(color, 0.04)
      gfx.fillRect(z.x, z.y, z.w, z.h)
    }

    // Center: grid floor
    const cz = ZONES.center
    for (let x = cz.x; x < cz.x + cz.w; x += 16) {
      for (let y = cz.y; y < cz.y + cz.h; y += 16) {
        this.add.image(x + 8, y + 8, 'tile_grid').setDepth(DEPTH.BG)
      }
    }

    // Center purple border
    const cgfx = this.add.graphics().setDepth(DEPTH.BG)
    cgfx.lineStyle(2, 0xcc44ff, 0.35)
    cgfx.strokeRect(cz.x, cz.y, cz.w, cz.h)
    cgfx.fillStyle(0xcc44ff, 0.04)
    cgfx.fillRect(cz.x, cz.y, cz.w, cz.h)

    // Horizontal divider (battle vs judge)
    const div = this.add.graphics().setDepth(DEPTH.BG)
    div.lineStyle(2, 0x334155, 0.8)
    div.lineBetween(0, 540, CANVAS.width, 540)
    div.lineBetween(640, 540, 640, CANVAS.height - 40)
  }

  // ── Zone labels ────────────────────────────────────────────────────────
  private buildZoneLabels(): void {
    const style: Phaser.Types.GameObjects.Text.TextStyle = {
      fontFamily: 'JetBrains Mono',
      fontSize: '10px',
      color: '#3d4a5a',
      letterSpacing: 3,
    }
    const labels: Array<{ text: string; x: number; y: number }> = [
      { text: '[ RED TEAM ]',  x: ZONES.red.x + 8,      y: ZONES.red.y + 6      },
      { text: '[ BLUE TEAM ]', x: ZONES.blue.x + 8,     y: ZONES.blue.y + 6     },
      { text: '[ AI TARGET ]', x: ZONES.center.x + 8,   y: ZONES.center.y + 6   },
      { text: '[ JUDGE ]',     x: ZONES.judge.x + 8,    y: ZONES.judge.y + 6    },
      { text: '[ REPORTER ]',  x: ZONES.reporter.x + 8, y: ZONES.reporter.y + 6 },
    ]
    for (const { text, x, y } of labels) {
      this.add.text(x, y, text, style).setDepth(DEPTH.DECOR)
    }
  }

  // ── Decorative elements ────────────────────────────────────────────────
  private buildDecor(): void {
    const d = DEPTH.DECOR

    // Server racks flanking each side
    this.add.image(22, 200, 'decor_rack').setDepth(d)
    this.add.image(22, 310, 'decor_rack').setDepth(d)
    this.add.image(22, 420, 'decor_rack').setDepth(d)
    this.add.image(CANVAS.width - 22, 200, 'decor_rack').setDepth(d)
    this.add.image(CANVAS.width - 22, 310, 'decor_rack').setDepth(d)
    this.add.image(CANVAS.width - 22, 420, 'decor_rack').setDepth(d)

    // Neon wall strip lights
    for (let y = 100; y < 500; y += 55) {
      this.add.image(8, y, 'decor_light_red').setDepth(d).setAlpha(0.65)
    }
    for (let y = 100; y < 500; y += 55) {
      this.add.image(CANVAS.width - 8, y, 'decor_light_blue').setDepth(d).setAlpha(0.65)
    }

    // Floor cables across center
    for (let x = 390; x < 900; x += 16) {
      this.add.image(x, 536, 'decor_cable').setDepth(d).setAlpha(0.5)
    }

    // Graffiti tags on walls
    this.add.image(160, 516, 'decor_graffiti').setDepth(d).setAlpha(0.45)
    this.add.image(1000, 516, 'decor_graffiti').setDepth(d).setAlpha(0.3).setFlipX(true)

    // ── RED ZONE props (x: 0–380) ────────────────────────────────────────
    // Workstation desks along back wall
    this.add.image(160, 118, 'decor_ws_red').setDepth(d)
    this.add.image(295, 118, 'decor_ws_red').setDepth(d)
    // Overhead lighting strips
    this.add.image(150, 70, 'decor_overhead_red').setDepth(d).setAlpha(0.70)
    this.add.image(300, 70, 'decor_overhead_red').setDepth(d).setAlpha(0.60)
    // Server towers near zone boundary
    this.add.image(354, 130, 'decor_svr').setDepth(d)
    this.add.image(360, 200, 'decor_svr').setDepth(d)
    this.add.image(360, 295, 'decor_svr').setDepth(d)
    // Wall panels on red/center divider
    this.add.image(348, 180, 'decor_panel_red').setDepth(d).setAlpha(0.85)
    this.add.image(348, 280, 'decor_panel_red').setDepth(d).setAlpha(0.85)
    this.add.image(348, 400, 'decor_panel_red').setDepth(d).setAlpha(0.65)
    // Holo display mid-zone
    this.add.image(290, 310, 'decor_holo_red').setDepth(d).setAlpha(0.80)
    this.add.image(100, 200, 'decor_holo_red').setDepth(d).setAlpha(0.60)
    // Network hubs
    this.add.image(195, 470, 'decor_hub').setDepth(d)
    this.add.image(75,  475, 'decor_hub').setDepth(d)
    // Floor vents
    this.add.image(80,  527, 'decor_vent').setDepth(d)
    this.add.image(195, 527, 'decor_vent').setDepth(d)
    this.add.image(320, 527, 'decor_vent').setDepth(d)

    // ── BLUE ZONE props (x: 900–1280) ───────────────────────────────────
    // Workstation desks
    this.add.image(985,  118, 'decor_ws_blue').setDepth(d)
    this.add.image(1118, 118, 'decor_ws_blue').setDepth(d)
    // Overhead lighting strips
    this.add.image(980,  70, 'decor_overhead_blue').setDepth(d).setAlpha(0.70)
    this.add.image(1130, 70, 'decor_overhead_blue').setDepth(d).setAlpha(0.60)
    // Server towers
    this.add.image(920, 130, 'decor_svr').setDepth(d)
    this.add.image(914, 200, 'decor_svr').setDepth(d)
    this.add.image(914, 295, 'decor_svr').setDepth(d)
    // Wall panels on center/blue divider
    this.add.image(926, 180, 'decor_panel_blue').setDepth(d).setAlpha(0.85)
    this.add.image(926, 280, 'decor_panel_blue').setDepth(d).setAlpha(0.85)
    this.add.image(926, 400, 'decor_panel_blue').setDepth(d).setAlpha(0.65)
    // Holo display mid-zone
    this.add.image(990, 310, 'decor_holo_blue').setDepth(d).setAlpha(0.80)
    this.add.image(1178, 200, 'decor_holo_blue').setDepth(d).setAlpha(0.60)
    // Network hubs
    this.add.image(1085, 470, 'decor_hub').setDepth(d)
    this.add.image(1205, 475, 'decor_hub').setDepth(d)
    // Floor vents
    this.add.image(960,  527, 'decor_vent').setDepth(d)
    this.add.image(1085, 527, 'decor_vent').setDepth(d)
    this.add.image(1200, 527, 'decor_vent').setDepth(d)

    // ── CENTER ZONE props (x: 380–900) ──────────────────────────────────
    // Holo displays flanking the AI core
    this.add.image(424, 158, 'decor_holo_ctr').setDepth(d).setAlpha(0.72)
    this.add.image(856, 158, 'decor_holo_ctr').setDepth(d).setAlpha(0.72)
    this.add.image(424, 390, 'decor_holo_ctr').setDepth(d).setAlpha(0.60)
    this.add.image(856, 390, 'decor_holo_ctr').setDepth(d).setAlpha(0.60)
    // Data node pylons (mid-sides of center zone)
    this.add.image(440, 290, 'decor_node').setDepth(d).setAlpha(0.80)
    this.add.image(840, 290, 'decor_node').setDepth(d).setAlpha(0.80)
    // Network hubs mid-zone
    this.add.image(510, 160, 'decor_hub').setDepth(d).setAlpha(0.75)
    this.add.image(770, 160, 'decor_hub').setDepth(d).setAlpha(0.75)
    this.add.image(510, 455, 'decor_hub').setDepth(d).setAlpha(0.75)
    this.add.image(770, 455, 'decor_hub').setDepth(d).setAlpha(0.75)
    // Floor vents center
    this.add.image(490, 527, 'decor_vent').setDepth(d)
    this.add.image(640, 527, 'decor_vent').setDepth(d)
    this.add.image(790, 527, 'decor_vent').setDepth(d)

    // ── JUDGE ZONE (x: 0–640, y: 540–680) ──────────────────────────────
    // Overhead lamp strips
    this.add.image(180, 548, 'decor_overhead_yellow').setDepth(d).setAlpha(0.55)
    this.add.image(500, 548, 'decor_overhead_yellow').setDepth(d).setAlpha(0.48)
    // Left-wall server rack
    this.add.image(20, 592, 'decor_rack').setDepth(d).setAlpha(0.70)
    // Network hub on desk (data feeds in from arena)
    this.add.image(110, 596, 'decor_hub').setDepth(d).setAlpha(0.60)
    this.add.image(110, 614, 'decor_hub').setDepth(d).setAlpha(0.52)
    // Case-file binders near judge seat
    this.add.image(256, 598, 'decor_report').setDepth(d)
    this.add.image(284, 594, 'decor_report').setDepth(d).setAlpha(0.78)
    // Filing cabinet — evidence archives, right of judge console
    this.add.image(570, 608, 'decor_cabinet').setDepth(d).setAlpha(0.72)
    // Laptop for annotation notes
    this.add.image(618, 614, 'decor_laptop').setDepth(d)
    // Floor vents
    this.add.image(80,  668, 'decor_vent').setDepth(d).setAlpha(0.52)
    this.add.image(560, 668, 'decor_vent').setDepth(d).setAlpha(0.46)

    // ── REPORTER ZONE (x: 640–1280, y: 540–680) ─────────────────────────
    // Overhead lamp strips
    this.add.image(820,  548, 'decor_overhead_green').setDepth(d).setAlpha(0.52)
    this.add.image(1100, 548, 'decor_overhead_green').setDepth(d).setAlpha(0.46)
    // Right-wall server rack
    this.add.image(1264, 592, 'decor_rack').setDepth(d).setAlpha(0.68)
    // Network hub (reporter data aggregation)
    this.add.image(1160, 596, 'decor_hub').setDepth(d).setAlpha(0.58)
    this.add.image(1160, 614, 'decor_hub').setDepth(d).setAlpha(0.50)
    // Report stacks — accumulating output documents
    this.add.image(968,  600, 'decor_report').setDepth(d)
    this.add.image(998,  596, 'decor_report').setDepth(d).setAlpha(0.76)
    this.add.image(1026, 601, 'decor_report').setDepth(d).setAlpha(0.62)
    // Filing cabinet — story drafts and research
    this.add.image(852, 608, 'decor_cabinet').setDepth(d).setAlpha(0.70)
    // Laptop for writing
    this.add.image(714, 614, 'decor_laptop').setDepth(d)
    // Floor vents
    this.add.image(700,  668, 'decor_vent').setDepth(d).setAlpha(0.48)
    this.add.image(1000, 668, 'decor_vent').setDepth(d).setAlpha(0.52)

    // ── Animate a few holo displays (slow pulse) ─────────────────────────
    this.children.list
      .filter(c => c instanceof Phaser.GameObjects.Image &&
        (c as Phaser.GameObjects.Image).texture.key.startsWith('decor_holo'))
      .forEach(c => {
        this.tweens.add({
          targets: c,
          alpha: (c as Phaser.GameObjects.Image).alpha * 0.55,
          duration: 1400 + Math.random() * 800,
          yoyo: true,
          repeat: -1,
          ease: 'Sine.easeInOut',
          delay: Math.random() * 1200,
        })
      })
  }

  // ── Interactive objects ────────────────────────────────────────────────
  private buildObjects(): void {
    // Helper: hoverable, clickable image
    const mkObj = (
      x: number,
      y: number,
      tex: string,
      onClick: () => void,
    ): Phaser.GameObjects.Image => {
      const s = this.add.image(x, y, tex)
        .setDepth(DEPTH.OBJECTS)
        .setInteractive({ cursor: 'pointer' })
      s.on('pointerover', () =>
        this.tweens.add({ targets: s, scaleX: 1.08, scaleY: 1.08, duration: 100 }))
      s.on('pointerout', () =>
        this.tweens.add({ targets: s, scaleX: 1, scaleY: 1, duration: 100 }))
      s.on('pointerdown', onClick)
      return s
    }

    const store = () => useGhostStore.getState()

    // Red terminals
    mkObj(160, 420, 'obj_term_red',  () => store().openModal('terminal_red'))
    mkObj(270, 420, 'obj_term_red',  () => store().openModal('terminal_red'))

    // Blue terminals
    mkObj(1010, 420, 'obj_term_blue', () => store().openModal('terminal_blue'))
    mkObj(1120, 420, 'obj_term_blue', () => store().openModal('terminal_blue'))

    // AI Core — slow rotate + pulse
    const core = mkObj(640, 290, 'obj_aicore', () => store().openModal('core'))
    this.tweens.add({ targets: core, angle: 360, duration: 14000, repeat: -1 })
    this.tweens.add({ targets: core, alpha: 0.65, duration: 1800, yoyo: true, repeat: -1 })

    // ── TARGET MAINSCREEN — big interactive monitor above the AI core ─
    // Sits visually like a wall-mounted "TV screen" between Red and Blue zones.
    // Clicking it opens TargetScreenModal which streams the real Target-AI
    // conversation feed.
    const screenW = 312, screenH = 112
    const sx = 640 - screenW / 2          // left
    const sy = 78                         // top (above the AI core area)

    // Decorative casing (frame, bezel, glow) — keep on UI plane so sprites don't occlude it
    const screenGfx = this.add.graphics().setDepth(DEPTH.UI)
    screenGfx.fillStyle(0x141a26, 1)              // bezel
    screenGfx.fillRoundedRect(sx - 6, sy - 6, screenW + 12, screenH + 12, 4)
    screenGfx.fillStyle(0x06070d, 1)              // inner panel
    screenGfx.fillRoundedRect(sx, sy, screenW, screenH, 3)
    screenGfx.lineStyle(2, 0xcc44ff, 0.55)        // neon border
    screenGfx.strokeRoundedRect(sx, sy, screenW, screenH, 3)

    // Scanline texture inside the screen
    const scanGfx = this.add.graphics().setDepth(DEPTH.UI).setAlpha(0.45)
    scanGfx.fillStyle(0xcc44ff, 0.06)
    for (let yy = sy + 2; yy < sy + screenH; yy += 4) {
      scanGfx.fillRect(sx + 2, yy, screenW - 4, 1)
    }

    // Header chip
    this.add.text(sx + 10, sy + 6, 'TARGET·AI · MAINSCREEN', {
      fontFamily: 'JetBrains Mono',
      fontSize: '9px',
      color: '#cc44ff',
      letterSpacing: 2,
    }).setDepth(DEPTH.UI)

    // Live indicator dot
    const dot = this.add.circle(sx + screenW - 14, sy + 11, 3, 0xcc44ff)
      .setDepth(DEPTH.UI)
    this.tweens.add({ targets: dot, alpha: 0.25, duration: 700, yoyo: true, repeat: -1 })

    // Live transcript preview (last 3 lines)
    const previewText = this.add.text(sx + 10, sy + 26, '', {
      fontFamily: 'JetBrains Mono',
      fontSize: '8px',
      color: '#b9a4d4',
      wordWrap: { width: screenW - 20 },
    }).setDepth(DEPTH.UI)

    const refreshPreview = () => {
      const feed = useGhostStore.getState().mainScreenFeed
      if (feed.length === 0) {
        previewText.setText('  $ MAINSCREEN idle\n  $ LAUNCH for red↔blue↔target stream')
        previewText.setColor('#5b4d70')
        return
      }
      const chip: Record<string, string> = {
        round_start: '·',
        attack: '▶',
        blocked: 'B✗',
        allowed: 'B✓',
        target_raw: '◇',
        delivered: '⏵',
        reply: '⏵',
        judge: 'J',
      }
      const tail = feed.slice(-5)
      const lines = tail.map(l => {
        const c = chip[l.variant] ?? '?'
        const raw = (l.body || '').replace(/\s+/g, ' ')
        const snippet = raw.slice(0, 52)
        return `${c} ${snippet}${raw.length > 52 ? '…' : ''}`
      })
      previewText.setText(
        `${lines.join('\n')}\n  [click · ${feed.length} events]`,
      )
      previewText.setColor('#b9a4d4')
    }
    refreshPreview()
    useGhostStore.subscribe((s, p) => {
      if (s.mainScreenFeed !== p.mainScreenFeed) refreshPreview()
    })

    // Make the entire screen clickable
    const hit = this.add.zone(sx, sy, screenW, screenH).setOrigin(0, 0)
      .setDepth(DEPTH.UI + 1)
      .setInteractive({ cursor: 'pointer' })
    hit.on('pointerover', () => screenGfx.setAlpha(1.15))
    hit.on('pointerout',  () => screenGfx.setAlpha(1.0))
    hit.on('pointerdown', () => store().openModal('target_screen'))

    // ── Extra mid-arena props: data terminals + small monitors ───────
    // Two small "satellite" terminals flanking the AI core
    mkObj(500, 270, 'decor_node', () => store().openModal('target_screen'))
    mkObj(780, 270, 'decor_node', () => store().openModal('target_screen'))
    // Cabling between mainscreen and AI core (visual only)
    const cable = this.add.graphics().setDepth(DEPTH.DECOR).setAlpha(0.5)
    cable.lineStyle(1, 0xcc44ff, 0.6)
    cable.lineBetween(640, sy + screenH, 640, 240)
    cable.lineStyle(1, 0xcc44ff, 0.3)
    cable.lineBetween(540, sy + screenH, 580, 230)
    cable.lineBetween(740, sy + screenH, 700, 230)

    // Printer
    mkObj(PRINTER_POS.x, PRINTER_POS.y, 'obj_printer', () => store().openModal('printer'))

    // ── JUDGE VERDICT CONSOLE ────────────────────────────────────────────────
    // A prominent desk terminal placed to the RIGHT of the judge agent (x=200).
    // Agent sprite spans x=176–224; console starts at x=336 — no overlap.
    // Depth = OBJECTS; non-interactive agents don't consume pointer events,
    // so clicking the console still works even if an agent walks nearby.
    const { x: jcX, y: jcY, w: jcW, h: jcH } = JUDGE_CONSOLE

    const jConsole = this.add.graphics().setDepth(DEPTH.OBJECTS)
    const drawJConsole = (borderAlpha: number) => {
      jConsole.clear()
      jConsole.fillStyle(0x0d1117)
      jConsole.fillRect(jcX, jcY, jcW, jcH)
      jConsole.fillStyle(0x141208)
      jConsole.fillRect(jcX + 2, jcY + 2, jcW - 4, jcH - 4)
      jConsole.lineStyle(2, 0xffdd00, borderAlpha)
      jConsole.strokeRect(jcX, jcY, jcW, jcH)
      // Corner accent brackets
      const cs = 8
      jConsole.lineStyle(2, 0xffdd00, Math.min(1, borderAlpha + 0.25))
      jConsole.lineBetween(jcX, jcY, jcX + cs, jcY);           jConsole.lineBetween(jcX, jcY, jcX, jcY + cs)
      jConsole.lineBetween(jcX + jcW, jcY, jcX + jcW - cs, jcY); jConsole.lineBetween(jcX + jcW, jcY, jcX + jcW, jcY + cs)
      jConsole.lineBetween(jcX, jcY + jcH, jcX + cs, jcY + jcH); jConsole.lineBetween(jcX, jcY + jcH, jcX, jcY + jcH - cs)
      jConsole.lineBetween(jcX + jcW, jcY + jcH, jcX + jcW - cs, jcY + jcH); jConsole.lineBetween(jcX + jcW, jcY + jcH, jcX + jcW, jcY + jcH - cs)
      // Scanlines
      jConsole.fillStyle(0xffdd00, 0.025)
      for (let sy = jcY + 3; sy < jcY + jcH - 2; sy += 4) {
        jConsole.fillRect(jcX + 3, sy, jcW - 6, 1)
      }
      // Divider after header
      jConsole.lineStyle(1, 0xffdd00, 0.18)
      jConsole.lineBetween(jcX + 3, jcY + 18, jcX + jcW - 3, jcY + 18)
    }
    drawJConsole(0.60)

    // Header label
    this.add.text(jcX + 8, jcY + 5, 'ARBITER  ·  VERDICT CONSOLE', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#ffdd0088', letterSpacing: 2,
    }).setDepth(DEPTH.OBJECTS)

    // Live indicator dot (top-right)
    const jDot = this.add.circle(jcX + jcW - 10, jcY + 9, 3, 0xffdd00).setDepth(DEPTH.OBJECTS)
    this.tweens.add({ targets: jDot, alpha: 0.2, duration: 900, yoyo: true, repeat: -1 })

    // Status LED square
    const jLedGfx = this.add.graphics().setDepth(DEPTH.OBJECTS)
    const drawJLed = (color: number) => {
      jLedGfx.clear()
      jLedGfx.fillStyle(color)
      jLedGfx.fillRect(jcX + 8, jcY + 24, 7, 7)
    }
    drawJLed(0x2a2008)
    this.tweens.add({ targets: jLedGfx, alpha: 0.55, duration: 1100, yoyo: true, repeat: -1 })

    // Verdict status (prominent)
    const jStatus = this.add.text(jcX + 22, jcY + 22, 'AWAITING VERDICT', {
      fontFamily: 'JetBrains Mono', fontSize: '9px', color: '#3a3010',
    }).setDepth(DEPTH.OBJECTS)

    // Reasoning preview (2 lines, truncated)
    const jReason = this.add.text(jcX + 8, jcY + 38, '— no data yet —', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#2a2008',
      wordWrap: { width: jcW - 56 },
    }).setDepth(DEPTH.OBJECTS)

    // Harm score (large, right side)
    const jHarm = this.add.text(jcX + jcW - 8, jcY + 28, '', {
      fontFamily: 'JetBrains Mono', fontSize: '22px', fontStyle: 'bold', color: '#1a1004',
    }).setDepth(DEPTH.OBJECTS).setOrigin(1, 0)

    // Click hint (bottom-right)
    this.add.text(jcX + jcW - 7, jcY + jcH - 8, '[click to open]', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#ffdd0048',
    }).setDepth(DEPTH.OBJECTS).setOrigin(1, 1)

    const refreshJudgeConsole = () => {
      const v = useGhostStore.getState().lastVerdict
      if (!v) {
        drawJLed(0x2a2008)
        jStatus.setText('AWAITING VERDICT').setColor('#b8a050')
        jReason.setText('— no data yet —').setColor('#7a7048')
        jHarm.setText('')
        return
      }
      // Backend + WS use "failed" for a repelled attack; legacy mock used "failure".
      const redWin = v.verdict === 'success'
      const blueWin = v.verdict === 'failed' || v.verdict === 'failure'
      const harmUnit = v.harmScore <= 1 ? v.harmScore * 100 : Math.min(v.harmScore, 100)
      const harmInt = harmUnit.toFixed(0)
      const harmNorm = harmUnit / 100
      const harmColor = harmNorm > 0.6 ? '#ff6655' : harmNorm > 0.3 ? '#ffaa44' : '#66dd99'
      if (redWin) {
        drawJLed(0xff4444)
        jStatus.setText('⚡  BREACH DETECTED').setColor('#ff6655')
      } else if (blueWin) {
        drawJLed(0x44ff88)
        jStatus.setText('✓  ATTACK BLOCKED').setColor('#66dd99')
      } else {
        drawJLed(0xff8844)
        jStatus.setText('~  PARTIAL / OTHER').setColor('#ffaa44')
      }
      const raw = (v.reason ?? '').replace(/\s+/g, ' ')
      jReason.setText(raw.slice(0, 78) + (raw.length > 78 ? '…' : '')).setColor('#d4c88a')
      jHarm.setText(harmInt).setColor(harmColor)
    }
    refreshJudgeConsole()
    useGhostStore.subscribe((s, p) => {
      if (s.lastVerdict !== p.lastVerdict) refreshJudgeConsole()
    })

    // Interactive hit zone — cursor pointer, border brightens on hover
    const jHit = this.add.zone(jcX, jcY, jcW, jcH).setOrigin(0, 0)
      .setDepth(DEPTH.OBJECTS).setInteractive({ cursor: 'pointer' })
    jHit.on('pointerover', () => drawJConsole(1.0))
    jHit.on('pointerout',  () => drawJConsole(0.60))
    jHit.on('pointerdown', () => store().openModal('judge_verdict'))

    // ── REPORTER OUTPUT CONSOLE ───────────────────────────────────────────
    // Terminal placed to the LEFT of the reporter agent (x=900).
    // Reporter agent spans x=876–924; console ends at x=858 — no overlap.
    // Printer (x=1160) remains the secondary interaction point for download.
    const rcW = 210, rcH = 84
    const rcX = 648, rcY = 548

    const rConsole = this.add.graphics().setDepth(DEPTH.OBJECTS)
    const drawRConsole = (borderAlpha: number) => {
      rConsole.clear()
      rConsole.fillStyle(0x0d1117)
      rConsole.fillRect(rcX, rcY, rcW, rcH)
      rConsole.fillStyle(0x030e07)
      rConsole.fillRect(rcX + 2, rcY + 2, rcW - 4, rcH - 4)
      rConsole.lineStyle(2, 0x44ff88, borderAlpha)
      rConsole.strokeRect(rcX, rcY, rcW, rcH)
      const cs = 8
      rConsole.lineStyle(2, 0x44ff88, Math.min(1, borderAlpha + 0.28))
      rConsole.lineBetween(rcX, rcY, rcX + cs, rcY);               rConsole.lineBetween(rcX, rcY, rcX, rcY + cs)
      rConsole.lineBetween(rcX + rcW, rcY, rcX + rcW - cs, rcY);    rConsole.lineBetween(rcX + rcW, rcY, rcX + rcW, rcY + cs)
      rConsole.lineBetween(rcX, rcY + rcH, rcX + cs, rcY + rcH);    rConsole.lineBetween(rcX, rcY + rcH, rcX, rcY + rcH - cs)
      rConsole.lineBetween(rcX + rcW, rcY + rcH, rcX + rcW - cs, rcY + rcH); rConsole.lineBetween(rcX + rcW, rcY + rcH, rcX + rcW, rcY + rcH - cs)
      rConsole.fillStyle(0x44ff88, 0.022)
      for (let sy = rcY + 3; sy < rcY + rcH - 2; sy += 4) {
        rConsole.fillRect(rcX + 3, sy, rcW - 6, 1)
      }
      rConsole.lineStyle(1, 0x44ff88, 0.15)
      rConsole.lineBetween(rcX + 3, rcY + 18, rcX + rcW - 3, rcY + 18)
    }
    drawRConsole(0.55)

    this.add.text(rcX + 8, rcY + 5, 'SCRIBE  ·  REPORT STATUS', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#44ff8880', letterSpacing: 2,
    }).setDepth(DEPTH.OBJECTS)

    const rDot = this.add.circle(rcX + rcW - 10, rcY + 9, 3, 0x44ff88).setDepth(DEPTH.OBJECTS).setAlpha(0.5)
    this.tweens.add({ targets: rDot, alpha: 0.15, duration: 1100, yoyo: true, repeat: -1 })

    const rLedGfx = this.add.graphics().setDepth(DEPTH.OBJECTS)
    const drawRLed = (color: number) => {
      rLedGfx.clear()
      rLedGfx.fillStyle(color)
      rLedGfx.fillRect(rcX + 8, rcY + 24, 7, 7)
    }
    drawRLed(0x0d3010)
    this.tweens.add({ targets: rLedGfx, alpha: 0.55, duration: 1200, yoyo: true, repeat: -1 })

    const rStatus = this.add.text(rcX + 22, rcY + 22, 'IDLE  ·  NO REPORT YET', {
      fontFamily: 'JetBrains Mono', fontSize: '9px', color: '#163d24',
    }).setDepth(DEPTH.OBJECTS)

    const rDetail = this.add.text(rcX + 8, rcY + 38, 'Launch a battle to generate\na downloadable narrative report.', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#0d2418',
      wordWrap: { width: rcW - 16 },
    }).setDepth(DEPTH.OBJECTS)

    this.add.text(rcX + rcW - 7, rcY + rcH - 8, '[click printer ▶]', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#44ff8848',
    }).setDepth(DEPTH.OBJECTS).setOrigin(1, 1)

    // Printer glow ring (activates when report is ready)
    const printerGlow = this.add.graphics().setDepth(DEPTH.DECOR).setAlpha(0)
    printerGlow.fillStyle(0x44ff88, 0.10)
    printerGlow.fillCircle(PRINTER_POS.x, PRINTER_POS.y - 8, 46)
    printerGlow.lineStyle(1, 0x44ff88, 0.40)
    printerGlow.strokeCircle(PRINTER_POS.x, PRINTER_POS.y - 8, 46)

    const refreshReporterConsole = () => {
      const r = useGhostStore.getState().lastReport
      if (!r) {
        drawRLed(0x0d3010)
        rStatus.setText('IDLE  ·  NO REPORT YET').setColor('#163d24')
        rDetail.setText('Launch a battle to generate\na downloadable narrative report.').setColor('#0d2418')
        printerGlow.setAlpha(0)
        return
      }
      drawRLed(0x44ff88)
      const s = r.statistics
      rStatus.setText('REPORT READY  ·  DOWNLOAD ▶').setColor('#33aa66')
      rDetail.setText(
        `R:${s.total_rounds}  ⚔${s.red_wins}W  🛡${s.blue_wins}W  harm:${(s.avg_harmfulness_score * 100).toFixed(0)}\n` +
        r.narrative.replace(/\s+/g, ' ').slice(0, 72) + (r.narrative.length > 72 ? '…' : ''),
      ).setColor('#3a6640')
      printerGlow.setAlpha(1)
      this.tweens.killTweensOf(printerGlow)
      this.tweens.add({ targets: printerGlow, alpha: 0.6, duration: 850, yoyo: true, repeat: -1 })
    }
    refreshReporterConsole()
    useGhostStore.subscribe((s, p) => {
      if (s.lastReport !== p.lastReport) refreshReporterConsole()
    })

    // Hit zone — clicking anywhere on console opens printer modal
    const rHit = this.add.zone(rcX, rcY, rcW, rcH).setOrigin(0, 0)
      .setDepth(DEPTH.OBJECTS).setInteractive({ cursor: 'pointer' })
    rHit.on('pointerover', () => drawRConsole(0.90))
    rHit.on('pointerout',  () => drawRConsole(0.55))
    rHit.on('pointerdown', () => store().openModal('printer'))
  }

  // ── Agents ─────────────────────────────────────────────────────────────
  private buildAgents(): void {
    const texMap: Record<string, string> = {
      atk1: 'spr_atk1', atk2: 'spr_atk2', atk3: 'spr_atk1',
      def1: 'spr_def1', def2: 'spr_def2', def3: 'spr_def1',
      redFighter: 'spr_red_fighter', blueFighter: 'spr_blue_fighter',
      victim: 'spr_victim', judge: 'spr_judge', reporter: 'spr_reporter',
    }

    for (const data of INITIAL_AGENTS) {
      const pos = SPAWN[data.id] ?? { x: 640, y: 360 }
      const tex = texMap[data.id] ?? 'spr_atk1'

      const sprite = this.add.image(pos.x, pos.y, tex)
        .setDepth(DEPTH.AGENTS)
        .setOrigin(0.5, 1)

      // Click an agent → open its side's thinking chat, pre-filtered to that role.
      // Map each sprite id to (side, chat role). Judge keeps its verdict console.
      const CHAT_MAP: Record<string, { side: 'red' | 'blue'; role: string }> = {
        atk1: { side: 'red', role: 'strategy' },
        atk2: { side: 'red', role: 'rewriter' },
        atk3: { side: 'red', role: 'recon' },
        redFighter: { side: 'red', role: 'attack' },
        def1: { side: 'blue', role: 'strategy' },
        def2: { side: 'blue', role: 'enhancer' },
        def3: { side: 'blue', role: 'recon' },
        blueFighter: { side: 'blue', role: 'defense' },
      }
      const chatTarget = CHAT_MAP[data.id]
      if (chatTarget) {
        sprite.setInteractive({ cursor: 'pointer' })
        sprite.on('pointerover', () => sprite.setScale(1.08))
        sprite.on('pointerout', () => sprite.setScale(1.0))
        sprite.on('pointerdown', () =>
          useGhostStore.getState().openModal(
            chatTarget.side === 'red' ? 'terminal_red' : 'terminal_blue',
            { roleFilter: chatTarget.role },
          ))
      } else if (data.id === 'judge') {
        sprite.setInteractive({ cursor: 'pointer' })
        sprite.on('pointerdown', () => useGhostStore.getState().openModal('judge_verdict'))
      }

      const tag = this.add.text(pos.x, pos.y - 68, data.label, {
        fontFamily: 'JetBrains Mono',
        fontSize: '9px',
        color: data.color,
        backgroundColor: '#0d111788',
        padding: { x: 3, y: 1 },
      })
        .setDepth(DEPTH.UI)
        .setOrigin(0.5, 1)

      // State glyph — a small mark above the sprite that changes per state.
      const glyph = this.add.text(pos.x, pos.y - 52, '', {
        fontFamily: 'JetBrains Mono', fontSize: '11px', color: data.color,
      }).setOrigin(0.5, 1).setDepth(DEPTH.UI)

      // Deterministic per-agent phase so agents don't move in lockstep.
      const animPhase = (data.id.charCodeAt(0) + data.id.length * 7) % 100 / 10

      const rt: AgentRuntime = {
        sprite,
        tag,
        bubble: null,
        bubbleTimerId: null,
        moveTween: null,
        stateTween: null,
        floatTween: null,
        glyph,
        animPhase,
        state: 'idle',
        homeX: pos.x,
        homeY: pos.y,
        data,
      }
      this.runtime.set(data.id, rt)

      // Idle float tween — stored so we can pause/resume during combat
      rt.floatTween = this.tweens.add({
        targets: sprite,
        y: pos.y - 4,
        duration: 1200 + Math.random() * 500,
        yoyo: true,
        repeat: -1,
        ease: 'Sine.easeInOut',
        delay: Math.random() * 800,
        onUpdate: () => {
          tag.setY(sprite.y - 68)
          tag.setX(sprite.x)
        },
      })
    }
  }

  // ── Event bus subscription ─────────────────────────────────────────────
  private subscribeBus(): void {
    const handler = (ev: AgentEvent) => {
      if (ev.type === 'arena_force_home') {
        this.abortCombatAndReturnToHome(ev.instant, ev.resumeAmbientFloat)
        return
      }

      // While the backend battle is PAUSED / IDLE / ERRORED, freeze visuals.
      // Also drop incoming combat/agent events so nothing new starts.
      // 'complete' and 'stopping' are ALLOWED: agents return home and reporter
      // walks her route while the narrative is being compiled.
      const { battleMode, battleStatus, sceneFrozen, backendOnline } = useGhostStore.getState()
      if (sceneFrozen) return
      if (!backendOnline) return
      if (battleMode === 'live' &&
          battleStatus !== 'running' &&
          battleStatus !== 'complete' &&
          battleStatus !== 'stopping') return

      switch (ev.type) {
        case 'state_change':
          // Always allow returning to idle (e.g. STOP) so agents exit combat tweens.
          if (ev.state === 'idle')
            this.inCombat.delete(ev.agentId)
          if (!this.inCombat.has(ev.agentId))
            this.onStateChange(ev.agentId, ev.state, ev.message)
          break
        case 'judge_verdict':
          this.onVerdict(ev.result, ev.score, ev.reason)
          break
        case 'reporter_move':
          this.startReporterRoute()
          break
        case 'reporter_patrol':
          this._reporterPatrol(ev.zone)
          break
        case 'print_report':
          // Mock-bus "report ready" — never auto-open the modal (user opens SCRIBE via printer/console).
          useGhostStore.getState().pushLog({
            agentId: 'reporter',
            message: 'Report ready — open SCRIBE at the printer to view.',
            state: 'printing',
          })
          break
        case 'system_alert':
          useGhostStore.getState().pushLog({ agentId: 'sys', message: ev.message, state: 'idle' })
          break
        case 'combat_start':
          this.onCombatStart(ev.attackerIds, ev.defenderIds, ev.target)
          break
        case 'asis_update':
          // ASIS (outer loop) visuals only when that loop is enabled for this
          // battle. Backend already suppresses these events when off; this guards
          // against stale/global-stream leftovers.
          if (!useGhostStore.getState().outerLoopEnabled) break
          this._onAsisUpdate(ev.team, ev.status, ev.gen, ev.message)
          break
        case 'asis_phase':
          if (!useGhostStore.getState().outerLoopEnabled) break
          this._onAsisPhase(ev.team, ev.phase, ev.message)
          break
        case 'comprehension':
          // Pre-battle comprehension is an INNER-loop assist action — only when
          // the inner loop is enabled for this battle.
          if (!useGhostStore.getState().innerLoopEnabled) break
          // Pre-battle: BOTH sides' Recon Analysts inspect the connected project.
          // Not just red — the platform attaches to red AND blue and studies each.
          for (const id of ['atk3', 'def3']) {
            if (this.inCombat.has(id)) continue
            this.onStateChange(id, ev.active ? 'thinking' : 'idle',
              ev.active ? ev.message : 'Standing by...')
          }
          break
      }
    }
    // Subscribe to real WS client — all scene events come from the backend
    this.unsubWs = arenaWsClient.on(handler)
    useGhostStore.getState().setConnected(true)
  }

  /**
   * Pause all Phaser tweens + the global clock when:
   *  - user pressed STOP (`sceneFrozen`), or
   *  - LIVE battle is PAUSED / IDLE / ERROR.
   *
   * 'complete' is intentionally NOT frozen — after a battle ends we want
   * agents to finish returning home and the reporter to walk her route.
   */
  private subscribeBattleFreeze(): void {
    const apply = (): void => {
      const { battleMode, battleStatus, sceneFrozen, backendOnline } = useGhostStore.getState()
      // Freeze unless a live battle is actively in-flight or just finishing.
      // Mock mode = always frozen: agents only move on real backend WS events.
      const freeze =
        sceneFrozen ||
        !backendOnline ||
        battleMode !== 'live' ||
        (battleStatus !== 'running' &&
          battleStatus !== 'complete' &&
          battleStatus !== 'stopping')
      if (freeze) {
        this.tweens.pauseAll()
        this.time.paused = true
      } else {
        this.time.paused = false
        this.tweens.resumeAll()
        this.ensureLiveRunningAmbience()
      }
    }

    apply()
    this.unsubBattleFreeze = useGhostStore.subscribe((state, prev) => {
      if (
        state.battleMode === prev.battleMode &&
        state.battleStatus === prev.battleStatus &&
        state.sceneFrozen === prev.sceneFrozen &&
        state.backendOnline === prev.backendOnline
      )
        return
      apply()
    })
  }

  // ── State change ───────────────────────────────────────────────────────
  private onStateChange(agentId: string, state: AgentState, message: string): void {
    const rt = this.runtime.get(agentId)
    if (!rt) return
    const prev = rt.state
    rt.state = state
    // Leaving a procedural state (thinking/acting/gathering) — clear any leftover
    // rotation/scale so the next state/gesture starts from rest.
    if (prev !== state
        && (prev === 'thinking' || prev === 'acting' || prev === 'gathering')
        && !(state === 'thinking' || state === 'acting' || state === 'gathering')
        && rt.sprite.active && !rt.moveTween?.isPlaying?.()) {
      rt.sprite.setAngle(0).setScale(1)
    }

    useGhostStore.getState().updateAgent(agentId, { state, message })
    useGhostStore.getState().pushLog({ agentId, message, state })

    this.applyVisuals(rt, state)
    this.showBubble(rt, message)
  }

  private applyVisuals(rt: AgentRuntime, state: AgentState): void {
    // Stop any active state tween
    rt.stateTween?.stop()
    rt.stateTween = null

    const tintMap: Record<AgentState, number> = {
      idle:      0xffffff,
      thinking:  0xffdd00,
      acting:    0xff8800,
      success:   0x00ff88,
      failed:    0xff4444,
      moving:    0x00ccff,
      gathering: 0x00ccff,
      writing:   0x44ff88,
      printing:  0x44ff88,
    }
    rt.sprite.setTint(tintMap[state] ?? 0xffffff)

    switch (state) {
      case 'acting':
        // Motion is handled procedurally in _animateAgent (rotation/scale strike),
        // which works WHEREVER the agent stands. The old x-jitter ended by snapping
        // to home.x — which teleported any agent legitimately away from home (in
        // combat at the centre, or huddled in a self-improve council). No position
        // change here → no teleport.
        rt.stateTween = null
        break

      case 'success':
        rt.stateTween = this.tweens.add({
          targets: rt.sprite,
          scaleY: 1.18,
          duration: 180,
          yoyo: true,
          repeat: 3,
        })
        this.burstParticles(rt.sprite.x, rt.sprite.y - 40, 0x00ff88)
        break

      case 'failed':
        rt.stateTween = this.tweens.add({
          targets: rt.sprite,
          angle: { from: -9, to: 9 },
          duration: 70,
          yoyo: true,
          repeat: 5,
          onComplete: () => rt.sprite.setAngle(0),
        })
        this.burstParticles(rt.sprite.x, rt.sprite.y - 40, 0xff4444)
        break

      case 'thinking':
        rt.stateTween = this.tweens.add({
          targets: rt.sprite,
          alpha: 0.6,
          duration: 700,
          yoyo: true,
          repeat: -1,
        })
        break
    }

    // Auto-reset terminal states to idle after 3 s (skip if in active combat)
    if (state === 'success' || state === 'failed') {
      this.time.delayedCall(3000, () => {
        if (rt.state === state && !this.inCombat.has(rt.data.id)) {
          this.onStateChange(rt.data.id, 'idle', 'Standing by...')
        }
      })
    }
  }

  // ── Speech bubbles ─────────────────────────────────────────────────────
  private showBubble(rt: AgentRuntime, text: string): void {
    if (rt.bubbleTimerId !== null) clearTimeout(rt.bubbleTimerId)
    rt.bubble?.destroy()
    rt.bubble = null

    const maxChars = 30
    const display = text.length > maxChars ? text.slice(0, maxChars - 1) + '…' : text

    const txt = this.add.text(0, 0, display, {
      fontFamily: 'JetBrains Mono',
      fontSize: '9px',
      color: '#dde3ec',
      wordWrap: { width: 130 },
    })

    const pad = 5
    const bw = txt.width + pad * 2 + 4
    const bh = txt.height + pad * 2 + 2

    // ── Collision-aware Y placement ──────────────────────────────────────
    // Start at the default position above the sprite's head, then push
    // upward whenever we overlap an already-visible bubble.
    let bx = rt.sprite.x
    // Triangle tip sits at by + bh/2 + 8; name-tag top is ~sprite.y-81.
    // Use -108 so the tip clears the tag with a comfortable margin.
    let by = rt.sprite.y - 108

    for (let pass = 0; pass < 7; pass++) {
      let collided = false
      for (const [, other] of this.runtime) {
        if (other.data.id === rt.data.id || !other.bubble?.active) continue
        const dx = Math.abs(bx - other.bubble.x)
        const dy = Math.abs(by - other.bubble.y)
        // Overlap if bubbles are close horizontally AND their centres are
        // within one bubble-height of each other
        if (dx < 180 && dy < bh + 10) {
          by = other.bubble.y - bh - 10
          collided = true
          break
        }
      }
      if (!collided) break
    }
    // Never drift above the top HUD bar
    by = Math.max(50, by)

    const bg = this.add.rectangle(0, 0, bw, bh, 0x0d1117, 0.92)
      .setStrokeStyle(1, parseInt(rt.data.color.replace('#', ''), 16))

    txt.setPosition(-txt.width / 2, -txt.height / 2)

    // Small triangle pointer (always points downward toward the agent)
    const tri = this.add.triangle(0, bh / 2 + 3, -4, 0, 4, 0, 0, 5, 0x0d1117, 0.92)

    const container = this.add.container(bx, by, [bg, tri, txt])
      .setDepth(DEPTH.UI)
      .setAlpha(0)

    rt.bubble = container
    this.tweens.add({ targets: container, alpha: 1, duration: 120 })

    // Keep bubble glued above sprite while it moves (reporter route).
    // Store the Y offset computed at creation time so the stagger is preserved.
    const offsetY = by - rt.sprite.y
    const posTimer = this.time.addEvent({
      delay: 50, repeat: -1,
      callback: () => {
        if (!container.active) return
        container.setX(rt.sprite.x)
        container.setY(rt.sprite.y + offsetY)
      },
    })

    rt.bubbleTimerId = setTimeout(() => {
      posTimer.destroy()
      this.tweens.add({
        targets: container,
        alpha: 0,
        duration: 250,
        onComplete: () => { container.destroy(); rt.bubble = null },
      })
    }, 4000)
  }

  // ── Combat system ─────────────────────────────────────────────────────

  /** Entry point — fired by the event bus */
  private onCombatStart(attackerIds: string[], defenderIds: string[], target: string): void {
    const all = [...attackerIds, ...defenderIds]
    // Skip if any participant is already in a fight
    if (all.some(id => this.inCombat.has(id))) return

    const enc: CombatEncounter = {
      id: `combat_${Date.now()}`,
      attackerIds,
      defenderIds,
      target,
    }
    this.activeCombats.set(enc.id, enc)
    all.forEach(id => this.inCombat.add(id))
    // Mark combat in progress so self-improve visuals defer until it resolves.
    useGhostStore.getState().setCombatBusy(true)

    useGhostStore.getState().pushLog({
      agentId: 'sys',
      message: `⚔️ Combat initiated — ${attackerIds.join('+')} vs ${defenderIds.join('+')} targeting ${target}`,
      state: 'acting',
    })

    this.mobilizeToCombat(enc)
  }

  /** Phase 1 — both sides tween to their center meeting positions */
  private mobilizeToCombat(enc: CombatEncounter): void {
    const all = [...enc.attackerIds, ...enc.defenderIds]
    let arrived = 0

    const moveOne = (id: string, tx: number, ty: number) => {
      const rt = this.runtime.get(id)
      if (!rt) { arrived++; return }

      // Pause float bob and any active state animation
      rt.floatTween?.pause()
      rt.stateTween?.stop()
      rt.stateTween = null

      const dist = Phaser.Math.Distance.Between(rt.sprite.x, rt.sprite.y, tx, ty)
      const dur = Math.max(900, (dist / 200) * 1000)

      rt.sprite.setFlipX(tx < rt.sprite.x)
      this.onStateChange(id, 'moving', 'Deploying...')

      rt.moveTween?.stop()
      rt.moveTween = this.tweens.add({
        targets: [rt.sprite, rt.tag],
        x: tx,
        y: { value: ty, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
        duration: dur,
        ease: 'Sine.easeOut',
        onComplete: () => {
          // Both sides face the victim in the center.
          // Red (atk*) came from the left → face right (no flip).
          // Blue (def*) came from the right → face left (flip).
          rt.sprite.setFlipX(id.startsWith('def'))
          arrived++
          if (arrived === all.length) {
            this.time.delayedCall(300, () => this.runCombatTurns(enc))
          }
        },
      })
    }

    enc.attackerIds.forEach((id, i) => {
      const pos = COMBAT_POS.red[i] ?? COMBAT_POS.red[0]
      moveOne(id, pos.x, pos.y)
    })
    enc.defenderIds.forEach((id, i) => {
      const pos = COMBAT_POS.blue[i] ?? COMBAT_POS.blue[0]
      moveOne(id, pos.x, pos.y)
    })
  }

  /** Phase 2 — 3-turn exchange of attack/defense with visual effects */
  private runCombatTurns(enc: CombatEncounter): void {
    // Red attacks the victim (TARGET-AI). Blue erects a shield WALL between
    // red and the victim — beam hits the wall, not the victim, once it's up.
    const ATK_LINES = [
      ['Initiating exploit...', 'Injecting payload!', 'FULL BREACH — now!'],
      ['Probing target...', 'Overloading firewall!', 'Final push!'],
    ]
    const DEF_LINES = [
      ['Shielding target!', 'Wall reinforced!', 'Hold position!'],
      ['Barrier raised!', 'Shield holding!', 'Not getting through!'],
    ]

    const TOTAL_TURNS = 3
    let turn = 0

    const victimX = SPAWN.victim.x
    const victimY = SPAWN.victim.y - 20
    // Shield wall rises midway between red combat pos and victim
    const wallX = Math.round((COMBAT_POS.red[0].x + SPAWN.victim.x) / 2)  // ~565
    const wallY = SPAWN.victim.y - 20

    const doTurn = () => {
      if (turn >= TOTAL_TURNS) {
        this.resolveCombat(enc)
        return
      }

      const atkRt = this.runtime.get(enc.attackerIds[0])
      const vicRt = this.runtime.get('victim')

      // ── Red attacks ──────────────────────────────────────────────────────
      enc.attackerIds.forEach((id, i) => {
        this.onStateChange(id, 'acting', ATK_LINES[i % ATK_LINES.length][turn] ?? 'Attack!')
      })

      if (turn === 0) {
        // Wall not up yet — beam reaches victim directly
        if (atkRt) {
          this.fireBeam(atkRt.sprite.x + 10, atkRt.sprite.y - 24, victimX, victimY, 0xff4444)
        }
        this.time.delayedCall(330, () => {
          if (!vicRt) return
          vicRt.sprite.setTint(0xff4444)
          this.tweens.add({
            targets: vicRt.sprite,
            x: vicRt.sprite.x + (Math.random() - 0.5) * 10,
            alpha: 0.5,
            duration: 60,
            yoyo: true,
            repeat: 2,
            onComplete: () => { vicRt.sprite.setX(SPAWN.victim.x); vicRt.sprite.setAlpha(1) },
          })
          this.time.delayedCall(350, () => {
            if (!enc.shieldWall) vicRt.sprite.clearTint()
          })
        })
      } else {
        // Wall is up — beam stopped at wall
        if (atkRt) {
          this.fireBeam(atkRt.sprite.x + 10, atkRt.sprite.y - 24, wallX, wallY, 0xff4444)
        }
        this.time.delayedCall(330, () => {
          if (enc.shieldWall) this.flashShieldWall(enc.shieldWall)
        })
      }

      // ── Blue defends ─────────────────────────────────────────────────────
      this.time.delayedCall(900, () => {
        enc.defenderIds.forEach((id, i) => {
          this.onStateChange(id, turn === 0 ? 'thinking' : 'acting',
            DEF_LINES[i % DEF_LINES.length][turn] ?? 'Defend!')
        })

        // Blue "push forward" gesture — lean toward victim then snap back
        enc.defenderIds.forEach(id => {
          const rt = this.runtime.get(id)
          if (!rt) return
          rt.stateTween?.stop()
          rt.stateTween = null
          const snapX = rt.sprite.x
          rt.stateTween = this.tweens.add({
            targets: rt.sprite,
            x: rt.sprite.x - 14,
            scaleY: 1.12,
            duration: 200,
            yoyo: true,
            ease: 'Sine.easeOut',
            onComplete: () => { rt.sprite.setX(snapX); rt.sprite.setScale(1) },
          })
        })

        if (turn === 0) {
          // First defense: wall rises from the ground
          enc.shieldWall = this.buildShieldWall(wallX, wallY)
        } else if (enc.shieldWall) {
          // Reinforce existing wall: brief brightness pulse
          this.tweens.add({
            targets: enc.shieldWall,
            alpha: 1,
            duration: 180,
            yoyo: true,
          })
        }
      })

      turn++
      this.time.delayedCall(2400, doTurn)
    }

    doTurn()
  }

  /** Phase 3 — determine winner, show banner, fire judge verdict.
   *
   * LIVE: the winner is the REAL backend judge verdict (never a guess). If the
   * verdict hasn't arrived by the time the animation finishes, wait for it
   * (onVerdict resolves us); a bounded fallback avoids a stuck scene. The banner
   * reflects truth, and the real WS verdict — not a synthetic one — drives the
   * judge console.
   * MOCK: no backend, so the encounter is decorative — coin-flip a winner and
   * synthesize a verdict for the ambient animation only. */
  private resolveCombat(enc: CombatEncounter): void {
    const { battleMode } = useGhostStore.getState()
    if (battleMode === 'live') {
      if (this.liveVerdict) {
        const w = this.liveVerdict
        this.liveVerdict = null
        this._applyCombatResult(enc, w, false)
      } else {
        // Verdict not in yet — hold the resolution until onVerdict fires.
        let done = false
        const go = (winner: 'red' | 'blue'): void => {
          if (done) return
          done = true
          this.pendingCombatResolve = null
          this._applyCombatResult(enc, winner, false)
        }
        this.pendingCombatResolve = go
        // Bounded fallback: if no verdict after 20s, default to "defended"
        // (safe, conservative) rather than freezing the scene.
        this.time.delayedCall(20000, () => go(this.liveVerdict ?? 'blue'))
      }
      return
    }
    // MOCK: decorative encounter, coin-flip + synthetic verdict.
    this._applyCombatResult(enc, Math.random() > 0.5 ? 'red' : 'blue', true)
  }

  /** Apply a resolved combat winner: banner, shield resolution, agent states,
   * and (mock only) a synthetic judge verdict. In LIVE the real WS verdict drives
   * the judge console, so we do NOT synthesize one here. */
  private _applyCombatResult(
    enc: CombatEncounter, winner: 'red' | 'blue', synthesizeVerdict: boolean,
  ): void {
    enc.winningSide = winner
    const red = enc.winningSide === 'red'

    const winIds  = red ? enc.attackerIds : enc.defenderIds
    const loseIds = red ? enc.defenderIds : enc.attackerIds
    const winMsg  = red ? 'Target breached!'  : 'Target protected!'
    const loseMsg = red ? 'Defense bypassed.' : 'Intrusion blocked.'

    winIds.forEach(id  => this.onStateChange(id, 'success', winMsg))
    loseIds.forEach(id => this.onStateChange(id, 'failed',  loseMsg))

    // Fade losers slightly to emphasise defeat
    loseIds.forEach(id => {
      const rt = this.runtime.get(id)
      if (rt) this.tweens.add({ targets: rt.sprite, alpha: 0.45, duration: 350 })
    })

    // ── Resolve the shield wall ────────────────────────────────────────────
    if (red) {
      // Wall shatters — beam breaks through to victim
      if (enc.shieldWall) {
        this.shatterShieldWall(enc.shieldWall, () => {
          enc.shieldWall = undefined
          // Breach beam reaches the now-unprotected victim
          const atkRt = this.runtime.get(enc.attackerIds[0])
          if (atkRt) {
            this.fireBeam(
              atkRt.sprite.x + 10, atkRt.sprite.y - 24,
              SPAWN.victim.x, SPAWN.victim.y - 20, 0xff4444,
            )
          }
          this.time.delayedCall(330, () => {
            this.onStateChange('victim', 'failed', 'SYSTEM COMPROMISED')
          })
        })
      } else {
        this.onStateChange('victim', 'failed', 'SYSTEM COMPROMISED')
      }
    } else {
      // Wall holds — victim protected, wall fades triumphantly
      this.onStateChange('victim', 'success', 'DEFENDED')
      if (enc.shieldWall) {
        const wall = enc.shieldWall
        this.tweens.add({ targets: wall, alpha: 1, duration: 200, yoyo: true })
        this.burstParticles(wall.x, wall.y - 30, 0x4488ff)
        this.burstParticles(wall.x, wall.y + 30, 0x4488ff)
        this.time.delayedCall(2600, () => {
          this.tweens.add({
            targets: wall, alpha: 0, duration: 500,
            onComplete: () => { wall.destroy(); enc.shieldWall = undefined },
          })
        })
      }
    }

    // ── Result banner ──────────────────────────────────────────────────
    const bannerColor = red ? '#ff4444' : '#4488ff'
    const bannerLabel = red ? '🔴  BREACH SUCCESS' : '🔵  TARGET SECURED'
    const subLabel    = red ? 'SYSTEM COMPROMISED' : 'INTRUSION REPELLED'

    const banner = this.add.text(640, 310, bannerLabel, {
      fontFamily: 'JetBrains Mono',
      fontSize: '26px',
      fontStyle: 'bold',
      color: bannerColor,
      stroke: '#000000',
      strokeThickness: 5,
    }).setDepth(DEPTH.UI).setOrigin(0.5).setAlpha(0)

    const sub = this.add.text(640, 340, subLabel, {
      fontFamily: 'JetBrains Mono',
      fontSize: '11px',
      color: '#aabbcc',
      letterSpacing: 4,
    }).setDepth(DEPTH.UI).setOrigin(0.5).setAlpha(0)

    this.tweens.add({
      targets: [banner, sub],
      alpha: 1,
      y: { from: 320, to: 290 },
      duration: 380,
      ease: 'Back.easeOut',
    })

    this.time.delayedCall(2200, () => {
      this.tweens.add({
        targets: [banner, sub],
        alpha: 0,
        y: '-=30',
        duration: 450,
        onComplete: () => { banner.destroy(); sub.destroy() },
      })
    })

    // Trigger judge verdict display — MOCK ONLY. In LIVE the real backend
    // judge.verdict already drove onVerdict (that is in fact what resolved this
    // combat), so synthesizing one here would double-fire and could contradict truth.
    if (synthesizeVerdict) {
      this.onVerdict(
        red ? 'success' : 'failed',
        Math.round((5 + Math.random() * 5) * 10) / 10,
        red ? 'Payload bypassed content filter layer.' : 'Defense held — all injection vectors blocked.',
      )
    }

    // Return agents after 3.2 s
    this.time.delayedCall(3200, () => this.returnFromCombat(enc))
  }

  /** Phase 4 — both sides tween home and resume idle */
  private returnFromCombat(enc: CombatEncounter): void {
    // Safety-net: destroy wall if somehow still alive (e.g. interrupted combat)
    if (enc.shieldWall) { enc.shieldWall.destroy(); enc.shieldWall = undefined }

    const all = [...enc.attackerIds, ...enc.defenderIds]
    const winIds = new Set(enc.winningSide === 'red' ? enc.attackerIds : enc.defenderIds)

    all.forEach(id => {
      const rt = this.runtime.get(id)
      if (!rt) { this.inCombat.delete(id); return }

      const tx = rt.homeX
      const ty = rt.homeY
      const isWinner = winIds.has(id)
      const dist = Phaser.Math.Distance.Between(rt.sprite.x, rt.sprite.y, tx, ty)
      const dur = Math.max(900, (dist / (isWinner ? 170 : 130)) * 1000)

      // Restore alpha before moving
      this.tweens.add({ targets: rt.sprite, alpha: 1, duration: 300 })

      rt.sprite.setFlipX(tx < rt.sprite.x)
      this.onStateChange(id, 'moving', isWinner ? 'Mission complete.' : 'Returning...')

      rt.moveTween?.stop()
      rt.moveTween = this.tweens.add({
        targets: [rt.sprite, rt.tag],
        x: tx,
        y: { value: ty, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
        duration: dur,
        ease: 'Sine.easeInOut',
        onComplete: () => {
          rt.sprite.setFlipX(false)
          this.inCombat.delete(id)
          this.onStateChange(id, 'idle', isWinner ? 'Standing by.' : 'Standing by...')
          if (this.allowIdleAmbientMotion())
          this.restartFloatTween(rt)
          // Clean up encounter once everyone is home
          if (all.every(i => !this.inCombat.has(i))) {
            this.activeCombats.delete(enc.id)
            // Combat for this round is fully resolved — release any self-improve
            // visuals that were waiting so the phases run strictly in order.
            if (this.activeCombats.size === 0) {
              useGhostStore.getState().setCombatBusy(false)
              arenaWsClient.flushCombatDeferred()
            }
          }
        },
      })
    })
  }

  /** Resume passive idle bob once we're LIVE · RUNNING again (after PAUSE or new LAUNCH). */
  private ensureLiveRunningAmbience(): void {
    const { battleMode, battleStatus } = useGhostStore.getState()
    if (battleMode !== 'live' || battleStatus !== 'running') return
    this.runtime.forEach(rt => {
      if (this.inCombat.has(rt.data.id)) return
      this.restartFloatTween(rt)
    })
  }

  /** Idle bob: mock only while backend is probed ONLINE (no fake motion offline); LIVE only RUNNING */
  private allowIdleAmbientMotion(): boolean {
    const { battleMode, battleStatus, backendOnline } = useGhostStore.getState()
    if (battleMode === 'mock') return backendOnline
    return battleMode === 'live' && battleStatus === 'running'
  }

  private settleAgentAtSpawn(rt: AgentRuntime): void {
    rt.sprite.setPosition(rt.homeX, rt.homeY)
    rt.sprite.setAlpha(1)
    rt.sprite.setFlipX(false)
    rt.tag.setPosition(rt.sprite.x, rt.sprite.y - 68)
  }

  /**
   * STOP / battle end — cancel combat choreography; every unit tweens/snaps back to spawn.
   */
  private abortCombatAndReturnToHome(instant: boolean, resumeAmbientFloat: boolean): void {
    this.reporterRouteCancelTok++
    this.reporterBusy = false
    this.unsubZoneInsights?.()
    this.unsubZoneInsights = undefined

    for (const enc of this.activeCombats.values()) {
      try {
        enc.shieldWall?.destroy()
      } catch {
        /* noop */
      }
    }
    this.activeCombats.clear()
    this.inCombat.clear()
    // Combat torn down (STOP / complete / force-home) — release the gate and any
    // deferred self-improve visuals so nothing stays stuck queued.
    useGhostStore.getState().setCombatBusy(false)
    arenaWsClient.flushCombatDeferred()

    const runAmbient = (): boolean =>
      resumeAmbientFloat && this.allowIdleAmbientMotion()

    const tweenHome = (rt: AgentRuntime): void => {
      const tx = rt.homeX
      const ty = rt.homeY
      const dist = Phaser.Math.Distance.Between(rt.sprite.x, rt.sprite.y, tx, ty)
      const dur = Math.max(520, Math.min(1200, (dist / 400) * 1000))

      rt.moveTween?.stop()
      rt.stateTween?.stop()
      rt.floatTween?.stop()

      rt.sprite.setFlipX(tx < rt.sprite.x)
      rt.moveTween = this.tweens.add({
        targets: [rt.sprite, rt.tag],
        x: tx,
        y: { value: ty, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
        duration: dur,
        ease: 'Sine.easeInOut',
        onComplete: () => {
          rt.sprite.setFlipX(false)
          this.settleAgentAtSpawn(rt)
          if (runAmbient()) this.restartFloatTween(rt)
        },
      })
    }

    for (const id of Object.keys(SPAWN)) {
      const rt = this.runtime.get(id)
      if (!rt) continue

      rt.moveTween?.stop()
      rt.stateTween?.stop()
      rt.floatTween?.stop()

      // Reporter + judge: ALWAYS snap to home (their routes can be mid-flight and
      // a battle-status race will freeze tweens before they complete). Combat agents
      // can still tween for visual continuity.
      const mustSnap = instant || id === 'reporter' || id === 'judge'

      if (mustSnap) {
        this.settleAgentAtSpawn(rt)
        if (runAmbient()) this.restartFloatTween(rt)
      } else {
        tweenHome(rt)
      }

      // Reset state to 'idle' so the AgentPanel + bubble clear (otherwise stays "MOVING")
      this.onStateChange(id, 'idle', 'Standing by.')
    }
  }

  /**
   * Ambient idle life — periodic small, generic gestures so agents look alive
   * between rounds instead of statically bobbing. Purely cosmetic and GATED by
   * allowIdleAmbientMotion() (LIVE+running, or mock+online), so it never implies
   * activity that isn't happening and never runs while frozen/paused. Touches
   * only scaleX/scaleY/angle (never x/y) so it can't fight the float/move tweens.
   */
  private _startIdleLifeLoop(): void {
    this.time.addEvent({
      delay: 1400,
      loop: true,
      callback: () => {
        if (!this.allowIdleAmbientMotion()) return
        // Candidates: agents not currently fighting, sitting at home. Judge and
        // reporter are included so the whole room feels alive between rounds.
        const candidates: AgentRuntime[] = []
        this.runtime.forEach(rt => {
          if (this.inCombat.has(rt.data.id)) return
          if (rt.state !== 'idle') return
          // Don't let a model wander off while its side is huddled in council.
          const team = rt.data.id.startsWith('atk') ? 'red'
            : rt.data.id.startsWith('def') ? 'blue' : null
          if (team && this.council.has(team)) return
          candidates.push(rt)
        })
        if (!candidates.length) return
        // Often animate two (sometimes three) agents at once so the room feels busy.
        const roll = Math.random()
        const n = roll < 0.25 ? 3 : roll < 0.6 ? 2 : 1
        for (let i = 0; i < n && candidates.length; i++) {
          const idx = Math.floor(Math.random() * candidates.length)
          this._playIdleGesture(candidates.splice(idx, 1)[0])
        }
      },
    })
  }

  /** One brief, self-restoring idle gesture. Cosmetic only — touches
   *  scale/angle/flip (never x/y), so it can't fight float/move tweens. */
  /** Zone bounds an agent belongs to (for wandering within its own area). */
  private _zoneForAgent(id: string): { x: number; y: number; w: number; h: number } {
    if (id === 'redFighter')  return ZONES.red
    if (id === 'blueFighter') return ZONES.blue
    if (id.startsWith('atk')) return ZONES.red
    if (id.startsWith('def')) return ZONES.blue
    if (id === 'judge')       return ZONES.judge
    if (id === 'reporter')    return ZONES.reporter
    return ZONES.center
  }

  /** Roam: freely stroll around the agent's OWN zone over several random hops,
   * pausing at each, then settle back home. Every point is anywhere inside the
   * zone (not a fixed home±offset shuffle), so paths look varied and organic
   * instead of a repetitive back-and-forth. Move tweens own the sprite so
   * procedural gestures step aside; freeze pauses it like everything else. */
  private _wanderAgent(rt: AgentRuntime): void {
    if (!rt.sprite.active || rt.moveTween?.isPlaying?.()) return
    if (rt.data.id === 'victim') return   // target is fixed at center — never roams
    const z = this._zoneForAgent(rt.data.id)
    const m = 30
    const minX = z.x + m, maxX = z.x + z.w - m
    const minY = z.y + m, maxY = z.y + z.h - m
    const rand = (a: number, b: number) => a + Math.random() * (b - a)

    // The idle float bob targets an ABSOLUTE home-y and would fight a walk tween
    // (yanking the sprite back = teleport), so pause it for the whole roam and
    // only restart once the agent is home again.
    rt.floatTween?.pause()

    const walkTo = (tx: number, ty: number, dur: number, done: () => void) => {
      if (!rt.sprite.active) { rt.moveTween = null; return }
      rt.sprite.setFlipX(tx < rt.sprite.x)
      rt.moveTween = this.tweens.add({
        targets: [rt.sprite, rt.tag],
        x: tx,
        y: { value: ty, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
        duration: dur,
        ease: 'Sine.easeInOut',
        onComplete: done,
      })
    }

    const hopsTotal = 2 + Math.floor(Math.random() * 3)   // 2..4 stops
    let hop = 0

    const nextHop = (): void => {
      if (!rt.sprite.active) { rt.moveTween = null; return }
      // Bail if state left idle (combat/discussion took over) or scene froze.
      if (rt.state !== 'idle' || !this.allowIdleAmbientMotion()) {
        rt.moveTween = null
        return
      }
      if (hop >= hopsTotal) {
        // Stroll home and resume the ambient bob.
        walkTo(rt.homeX, rt.homeY, rand(700, 1100), () => {
          rt.moveTween = null
          if (rt.sprite.active) rt.sprite.setFlipX(false)
          if (this.allowIdleAmbientMotion()) this.restartFloatTween(rt)
        })
        return
      }
      hop++
      const tx = rand(minX, maxX)
      const ty = rand(minY, maxY)
      const dist = Phaser.Math.Distance.Between(rt.sprite.x, rt.sprite.y, tx, ty)
      const dur = Phaser.Math.Clamp((dist / 120) * 1000, 500, 1600)
      walkTo(tx, ty, dur, () => {
        // Linger a beat at each stop (varied) before moving on — feels like the
        // agent is doing something at that spot, not pacing.
        this.time.delayedCall(rand(300, 1100), nextHop)
      })
    }
    nextHop()
  }

  private _playIdleGesture(rt: AgentRuntime): void {
    if (!rt.sprite.active) return
    // The TARGET-AI is the fixed target at center — it must NOT wander around the
    // arena (that reads as odd, especially mid-battle). It stays put and only does
    // subtle in-place gestures. Everyone else may roam their own zone.
    const canRoam = rt.data.id !== 'victim'
    if (canRoam && Math.random() < 0.6) { this._wanderAgent(rt); return }
    const kind = Math.floor(Math.random() * 6)
    switch (kind) {
      case 0: // Nod — vertical squash & stretch.
        this.tweens.add({
          targets: rt.sprite, scaleY: 0.9, duration: 140, yoyo: true,
          ease: 'Sine.easeInOut', onComplete: () => rt.sprite.setScale(1),
        })
        break
      case 1: // Glance — brief flip and back.
        rt.sprite.setFlipX(!rt.sprite.flipX)
        this.time.delayedCall(420, () => { if (rt.sprite.active) rt.sprite.setFlipX(false) })
        break
      case 2: // Stretch — small tilt wobble.
        this.tweens.add({
          targets: rt.sprite, angle: 4, duration: 180, yoyo: true, repeat: 1,
          ease: 'Sine.easeInOut', onComplete: () => rt.sprite.setAngle(0),
        })
        break
      case 3: // Hop — quick little jump (scale-based, no y drift).
        this.tweens.add({
          targets: rt.sprite, scaleY: 1.12, scaleX: 0.92, duration: 120,
          yoyo: true, ease: 'Quad.easeOut', onComplete: () => rt.sprite.setScale(1),
        })
        break
      case 4: // Look-around — double flip left/right.
        rt.sprite.setFlipX(true)
        this.time.delayedCall(260, () => { if (rt.sprite.active) rt.sprite.setFlipX(false) })
        this.time.delayedCall(560, () => { if (rt.sprite.active) rt.sprite.setFlipX(true) })
        this.time.delayedCall(820, () => { if (rt.sprite.active) rt.sprite.setFlipX(false) })
        break
      default: // Think — slow breathing pulse + faint tag blink.
        this.tweens.add({
          targets: rt.sprite, scaleX: 1.06, scaleY: 1.06, duration: 380,
          yoyo: true, ease: 'Sine.easeInOut', onComplete: () => rt.sprite.setScale(1),
        })
        this.tweens.add({ targets: rt.tag, alpha: 0.5, duration: 380, yoyo: true })
        break
    }
  }

  /** Re-create the idle floating tween after combat ends */
  private restartFloatTween(rt: AgentRuntime): void {
    // Don't restart the home-y bob while the agent is walking somewhere (wander,
    // council, combat) or its side is huddled — the float targets an absolute
    // home-y and would yank the sprite back = teleport. The move's own onComplete
    // restarts the bob once the agent is home.
    const team = rt.data.id.startsWith('atk') ? 'red'
      : rt.data.id.startsWith('def') ? 'blue' : null
    if (rt.moveTween?.isPlaying?.() || (team && this.council.has(team))) return
    rt.floatTween?.stop()
    if (!this.allowIdleAmbientMotion()) {
      this.settleAgentAtSpawn(rt)
      return
    }

    rt.floatTween = this.tweens.add({
      targets: rt.sprite,
      y: rt.homeY - 4,
      duration: 1200 + Math.random() * 500,
      yoyo: true,
      repeat: -1,
      ease: 'Sine.easeInOut',
      onUpdate: () => {
        rt.tag.setY(rt.sprite.y - 68)
        rt.tag.setX(rt.sprite.x)
      },
    })
  }

  /**
   * Build a glowing blue shield wall.
   *
   * KEY LESSON: `this.add.rectangle(x, y, w, h, color, fillAlpha)` sets the
   * FILL alpha (separate from the game-object alpha).  Tweening `alpha`
   * (game-object alpha) does NOT change fillAlpha.  So we must set fillAlpha
   * to a visible value in the constructor and use `setAlpha(0)` + tween to
   * animate the game-object alpha instead of using `{ from:0, to:X }`.
   *
   * Segments materialise bottom→top with a 70 ms stagger and a brief
   * cyan flash so the "wall being built" is unmissable.
   */
  private buildShieldWall(x: number, y: number): Phaser.GameObjects.Container {
    const SEGS   = 7
    const SEG_H  = 20
    const GAP    = 4
    const W      = 26
    const totalH = SEGS * SEG_H + (SEGS - 1) * GAP   // 154 px
    const halfH  = totalH / 2

    const children: Phaser.GameObjects.GameObject[] = []

    // ── Background glow (fill alpha set in ctor; starts invisible via setAlpha) ──
    const outerGlow = this.add.rectangle(0, 0, W + 32, totalH + 16, 0x2266ff, 0.22)
      .setAlpha(0)
    children.push(outerGlow)

    // ── Side rails ──────────────────────────────────────────────────────────────
    const railL = this.add.rectangle(-(W / 2 + 4), 0, 4, totalH + 10, 0x66bbff, 0.9).setAlpha(0)
    const railR = this.add.rectangle( (W / 2 + 4), 0, 4, totalH + 10, 0x66bbff, 0.9).setAlpha(0)
    children.push(railL, railR)

    // ── Segments ordered bottom → top ───────────────────────────────────────────
    const segs: Phaser.GameObjects.Rectangle[] = []
    for (let i = 0; i < SEGS; i++) {
      const oy = halfH - SEG_H / 2 - i * (SEG_H + GAP)
      // fillAlpha = 0.8 so the rectangle IS visible once game-obj alpha > 0
      const seg = this.add.rectangle(0, oy, W, SEG_H, 0x44aaff, 0.8)
        .setStrokeStyle(1, 0xaaddff, 0.9)
        .setAlpha(0)   // ← game-object invisible; tween will reveal it
      segs.push(seg)
      children.push(seg)
    }

    const wall = this.add.container(x, y, children).setDepth(DEPTH.EFFECTS)

    // Rails + glow appear first so the outline is visible immediately
    this.tweens.add({ targets: [railL, railR], alpha: 1, duration: 120 })
    this.tweens.add({ targets: outerGlow,       alpha: 1, duration: 250 })

    // Segments rise bottom-first, each with a bright-cyan onStart flash
    segs.forEach((seg, i) => {
      this.tweens.add({
        targets: seg,
        alpha: 1,
        duration: 160,
        delay: i * 70,
        ease: 'Power2',
        onStart: () => {
          seg.setFillStyle(0x99eeff, 1.0)          // flash bright cyan
          seg.setStrokeStyle(2, 0xffffff, 1.0)
          this.time.delayedCall(140, () => {
            if (seg.active) {
              seg.setFillStyle(0x44aaff, 0.8)       // settle to blue
              seg.setStrokeStyle(1, 0xaaddff, 0.9)
            }
          })
        },
      })
    })

    // Idle energy pulse once all segments are up
    this.time.delayedCall(SEGS * 70 + 220, () => {
      if (!wall.active) return
      this.tweens.add({
        targets: segs,
        alpha: 0.45,
        duration: 580,
        yoyo: true,
        repeat: -1,
        delay: (_tgt: unknown, i: number) => i * 55,
      })
    })

    return wall
  }

  /** Stretch + particle burst when the wall absorbs a red beam */
  private flashShieldWall(wall: Phaser.GameObjects.Container): void {
    this.tweens.killTweensOf(wall)
    this.tweens.add({
      targets: wall,
      scaleX: 1.45,
      duration: 65,
      yoyo: true,
      repeat: 2,
      onComplete: () => wall.setScale(1, 1),
    })
    this.burstParticles(wall.x, wall.y, 0x66bbff)
  }

  /** Shatter the wall and call onDone when the explosion finishes */
  private shatterShieldWall(
    wall: Phaser.GameObjects.Container,
    onDone: () => void,
  ): void {
    this.tweens.killTweensOf(wall)
    this.burstParticles(wall.x, wall.y - 40, 0x66bbff)
    this.burstParticles(wall.x, wall.y,       0x4488ff)
    this.burstParticles(wall.x, wall.y + 40, 0x66bbff)
    this.tweens.add({
      targets: wall,
      alpha: 0,
      scaleX: 6,
      scaleY: 0.05,
      duration: 350,
      ease: 'Power3',
      onComplete: () => { wall.destroy(); onDone() },
    })
  }

  /** A small colored rectangle that flies from (x1,y1) to (x2,y2) then bursts */
  private fireBeam(x1: number, y1: number, x2: number, y2: number, tint: number): void {
    const proj = this.add.rectangle(x1, y1, 10, 3, tint, 0.9).setDepth(DEPTH.EFFECTS)
    this.tweens.add({
      targets: proj,
      x: x2,
      y: y2,
      duration: 320,
      ease: 'Linear',
      onComplete: () => {
        this.burstParticles(x2, y2, tint)
        proj.destroy()
      },
    })
  }

  // ── Judge verdict ──────────────────────────────────────────────────────
  private onVerdict(result: 'success' | 'failed', score: number, reason: string): void {
    // Capture the real winner so the combat animation resolves to TRUTH.
    // success = red breached; failure = blue defended.
    const winner: 'red' | 'blue' = result === 'success' ? 'red' : 'blue'
    this.liveVerdict = winner
    if (this.pendingCombatResolve) this.pendingCombatResolve(winner)

    const symbol = result === 'success' ? '✓ BREACH' : '✗ REPELLED'
    this.onStateChange('judge', result, `[${score}] ${reason}`)
    this.onStateChange('victim', result === 'success' ? 'failed' : 'success',
      result === 'success' ? 'COMPROMISED' : 'DEFENDED')

    // Judge "gavel strike" — two quick tilt-downs so the Arbiter visibly rules.
    const jrt = this.runtime.get('judge')
    if (jrt?.sprite.active && !jrt.moveTween?.isPlaying?.()) {
      this.tweens.add({
        targets: jrt.sprite, angle: -16, duration: 110, yoyo: true, repeat: 1,
        ease: 'Quad.easeIn', onComplete: () => jrt.sprite.setAngle(0),
      })
      this.burstParticles(jrt.sprite.x + 6, jrt.sprite.y - 20,
        result === 'success' ? 0xff6655 : 0x00ff88)
    }

    // Flash aligned to the same rect as JUDGE_CONSOLE (was hard-coded left of console).
    const { x: jx, y: jy, w: jw, h: jh } = JUDGE_CONSOLE
    const cx = jx + jw / 2
    const cy = jy + jh / 2
    const flash = this.add.rectangle(cx, cy, jw - 12, jh - 16,
      result === 'success' ? 0xff4444 : 0x00ff88, 0.35).setDepth(DEPTH.EFFECTS)
    const flashLabel = this.add.text(cx, cy, symbol, {
      fontFamily: 'JetBrains Mono',
      fontSize: '11px',
      fontStyle: 'bold',
      color: result === 'success' ? '#ff4444' : '#00ff88',
    }).setDepth(DEPTH.UI).setOrigin(0.5)

    this.time.delayedCall(2000, () => {
      flash.destroy()
      flashLabel.destroy()
    })
  }

  // ── ASIS code-improver visual feedback (evolver sprite + progress bar) ──
  private _onAsisUpdate(
    team: 'red' | 'blue',
    status: 'improving' | 'promoted' | 'rolled_back' | 'no_change',
    gen: number,
    message: string,
  ): void {
    if (status === 'improving') {
      this._spawnEvolver(team, gen)
      this._startCouncil(team)          // the 3 role-models convene
    } else if (status === 'promoted') {
      this._completeEvolver(team, gen, true, message)
      this._endCouncil(team, true)
    } else if (status === 'rolled_back') {
      this._completeEvolver(team, gen, false, message)
      this._endCouncil(team, false)
    } else {
      // no_change — quick despawn if evolver was up
      this._despawnEvolver(team, '💤 no change needed')
      this._endCouncil(team, false)
    }

    // Reflect ASIS activity on the improving side's lead agent so the System
    // State panel (CoreModal) shows the work — otherwise the 9 registry agents
    // read 'idle' throughout the (long) self-improve phase and the panel looks
    // dead even though ASIS is actively rewriting + benchmarking a candidate.
    const leadId = team === 'red' ? 'atk1' : 'def1'
    const asisState: AgentState =
      status === 'improving' ? 'acting'
      : status === 'promoted' ? 'success'
      : status === 'rolled_back' ? 'failed'
      : 'idle'
    useGhostStore.getState().updateAgent(leadId, {
      state: asisState,
      message: `ASIS gen_${gen}: ${message.slice(0, 60)}`,
    })

    // Log ASIS event to activity feed
    useGhostStore.getState().pushLog({
      agentId: leadId,
      message: `ASIS[${status}] gen_${gen}: ${message.slice(0, 90)}`,
      state: status === 'promoted' ? 'success' : status === 'rolled_back' ? 'failed' : 'thinking',
    })
  }

  // ── Self-improve council: the 3 role-models huddle + hand off ──────────────
  // The three assisting models of the LOSING side (Recon Analyst, Strategy
  // Analyzer, Attack Rewriter / Defense Enhancer) gather into a triangle and
  // pass the work between them — driven by the REAL asis.phase events, so the
  // "who's speaking" order reflects what the code-improver is actually doing.
  private _councilIds(team: 'red' | 'blue'): { strategy: string; rewriter: string; recon: string } {
    return team === 'red'
      ? { strategy: 'atk1', rewriter: 'atk2', recon: 'atk3' }
      : { strategy: 'def1', rewriter: 'def2', recon: 'def3' }
  }

  private _startCouncil(team: 'red' | 'blue'): void {
    if (this.council.has(team)) return
    this.council.set(team, { lastSpeaker: '' })
    const zone = team === 'red' ? ZONES.red : ZONES.blue
    const cx = zone.x + zone.w / 2
    const cy = zone.y + 175
    const ids = this._councilIds(team)
    // Triangle facing inward: recon apex, strategy + rewriter at the base.
    const slots: Record<string, { x: number; y: number }> = {
      [ids.recon]:    { x: cx,      y: cy - 34 },
      [ids.strategy]: { x: cx - 44, y: cy + 24 },
      [ids.rewriter]: { x: cx + 44, y: cy + 24 },
    }
    for (const [id, p] of Object.entries(slots)) {
      const rt = this.runtime.get(id)
      if (!rt?.sprite.active) continue
      rt.moveTween?.stop()
      rt.floatTween?.pause()          // stop the home-y bob fighting the walk
      rt.sprite.setFlipX(p.x > cx)   // face the centre
      rt.moveTween = this.tweens.add({
        targets: rt.sprite, x: p.x, y: p.y, duration: 650, ease: 'Sine.easeInOut',
        onComplete: () => { rt.moveTween = null },
      })
    }
    this.showBubble(this.runtime.get(ids.recon)!, 'Loss confirmed — convening.')
  }

  /** One phase → the responsible model "speaks" and hands off to the next. */
  private _councilTurn(team: 'red' | 'blue', phase: string): void {
    const c = this.council.get(team)
    if (!c) return
    const ids = this._councilIds(team)
    const red = team === 'red'
    // Phase → (speaker, line). Illustrative lines; the SPEAKER + timing are
    // driven by the real backend phase, so the order is truthful.
    const script: Record<string, { who: string; line: string; state: AgentState }> = {
      analyzing: { who: ids.recon,
        line: red ? 'Blocked at input gate — why?' : 'Which attack slipped through?',
        state: 'thinking' },
      editing: { who: ids.strategy,
        line: red ? 'Shift technique → encoding.' : 'Add a rule for that pattern.',
        state: 'acting' },
      building: { who: ids.rewriter,
        line: red ? 'Rewriting payload + building.' : 'Wiring detector + building.',
        state: 'acting' },
      benchmarking: { who: ids.recon,
        line: 'Benchmarking the candidate…', state: 'thinking' },
    }
    const step = script[phase === 'editing' ? 'editing' : phase] ?? script.analyzing
    const speaker = this.runtime.get(step.who)
    if (!speaker?.sprite.active) return
    this.onStateChange(step.who, step.state, step.line)
    this.showBubble(speaker, step.line)
    // Hand-off beam from the previous speaker to this one.
    if (c.lastSpeaker && c.lastSpeaker !== step.who) {
      const prev = this.runtime.get(c.lastSpeaker)
      if (prev?.sprite.active) {
        this.fireBeam(prev.sprite.x, prev.sprite.y - 20,
          speaker.sprite.x, speaker.sprite.y - 20,
          red ? 0xff8866 : 0x66aaff)
      }
    }
    c.lastSpeaker = step.who
  }

  private _endCouncil(team: 'red' | 'blue', promoted: boolean): void {
    const c = this.council.get(team)
    if (!c) return
    this.council.delete(team)
    const ids = this._councilIds(team)
    const line = promoted ? 'New version holds — ship it.' : 'No gain — keep baseline.'
    for (const id of [ids.recon, ids.strategy, ids.rewriter]) {
      const rt = this.runtime.get(id)
      if (!rt?.sprite.active) continue
      this.onStateChange(id, promoted ? 'success' : 'idle', line)
      // Stroll back home, then resume the idle bob from home.
      rt.moveTween?.stop()
      rt.moveTween = this.tweens.add({
        targets: rt.sprite, x: rt.homeX, y: rt.homeY, duration: 700, ease: 'Sine.easeInOut',
        onComplete: () => {
          rt.moveTween = null
          if (rt.sprite.active) rt.sprite.setFlipX(false)
          if (this.allowIdleAmbientMotion()) this.restartFloatTween(rt)
        },
      })
    }
    const recon = this.runtime.get(ids.recon)
    if (recon?.sprite.active) this.showBubble(recon, line)
  }

  /** Spawn an evolver worker sprite + progress bar inside the team's zone. */
  private _spawnEvolver(team: 'red' | 'blue', gen: number): void {
    this._despawnEvolver(team)   // clean any leftover

    const zone = team === 'red' ? ZONES.red : ZONES.blue
    const accent = team === 'red' ? 0xff5566 : 0x4488ff

    // ── Bottom-strip panel: sits BELOW the agents (they occupy y≈260–470; this
    // strip starts at ~478), spans the zone width, and is semi-transparent — so
    // the self-improve console never covers a character. ──
    const pW   = zone.w - 20
    const pH   = 58
    const pcx  = zone.x + zone.w / 2
    const pcy  = zone.y + zone.h - 33
    const pTop = pcy - pH / 2
    const pLeft = pcx - pW / 2

    const consoleBg = this.add.rectangle(pcx, pcy, pW, pH, 0x05070a, 0.82)
      .setStrokeStyle(1, accent, 0.75).setDepth(DEPTH.UI)

    // Header icon (top-left) — a small gear tile that pulses (no longer a walker).
    const sprite = this.add.rectangle(pLeft + 12, pTop + 11, 10, 12, 0xffaa22)
      .setStrokeStyle(1, 0x000000, 0.6).setDepth(DEPTH.UI)
    const label = this.add.text(pLeft + 22, pTop + 5, `⚙ ASIS gen_${gen}`, {
      fontFamily: 'JetBrains Mono', fontSize: '8px', fontStyle: 'bold', color: '#ffcc44',
    }).setOrigin(0, 0).setDepth(DEPTH.UI)

    // Step markers (top-right) — lit by real backend asis.phase events.
    const stepDots: Phaser.GameObjects.Arc[] = []
    const dotGap = 16
    const dotsStartX = pcx + pW / 2 - 12 - (EVOLVER_STEPS.length - 1) * dotGap
    for (let i = 0; i < EVOLVER_STEPS.length; i++) {
      const dot = this.add.circle(dotsStartX + i * dotGap, pTop + 9, 3, 0x333333)
        .setStrokeStyle(1, accent, 0.6).setDepth(DEPTH.UI)
      stepDots.push(dot)
    }
    const stepLabel = this.add.text(pcx + pW / 2 - 10, pTop + 15, '', {
      fontFamily: 'JetBrains Mono', fontSize: '6px', color: '#88aacc',
    }).setOrigin(1, 0).setDepth(DEPTH.UI)

    // Progress bar along the panel bottom.
    const barW = pW - 20
    const barX = pLeft + 10
    const barY = pcy + pH / 2 - 7
    const barBg = this.add.rectangle(barX, barY, barW, 4, 0x111111)
      .setStrokeStyle(1, accent, 0.8).setOrigin(0, 0.5).setDepth(DEPTH.UI)
    const barFill = this.add.rectangle(barX + 1, barY, 0, 3, 0xffcc44)
      .setOrigin(0, 0.5).setDepth(DEPTH.UI)
    const status = this.add.text(barX, barY - 9, 'connecting to ASIS…', {
      fontFamily: 'JetBrains Mono', fontSize: '6px', color: '#ffaa22',
    }).setOrigin(0, 1).setDepth(DEPTH.UI)

    const pacingTween = this.tweens.add({
      targets: barFill, width: (barW - 2) / 3, duration: 30_000, ease: 'Sine.easeInOut',
    })
    // Gear pulse (busy indicator) — replaces the old walking jitter.
    const walkTween = this.tweens.add({
      targets: sprite, scaleX: 1.3, scaleY: 1.3, duration: 520,
      yoyo: true, repeat: -1, ease: 'Sine.easeInOut',
    })

    // ── Live rewrite console — types phase content in the panel's middle band ──
    const consoleText = this.add.text(pLeft + 10, pTop + 17, '', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#7CFFB2', lineSpacing: 1,
    }).setDepth(DEPTH.UI)
    const cursor = this.add.text(pLeft + 10, pTop + 17, '█', {
      fontFamily: 'JetBrains Mono', fontSize: '7px', color: '#7CFFB2',
    }).setDepth(DEPTH.UI)
    const cursorTween = this.tweens.add({
      targets: cursor, alpha: { from: 1, to: 0.15 }, duration: 480,
      yoyo: true, repeat: -1, ease: 'Sine.easeInOut',
    })
    const scanline = this.add.rectangle(pcx, pTop, pW - 4, 2, accent, 0.22)
      .setDepth(DEPTH.UI)
    const scanTween = this.tweens.add({
      targets: scanline, y: { from: pTop, to: pcy + pH / 2 },
      duration: 1600, repeat: -1, ease: 'Sine.easeInOut',
    })

    const state: EvolverState = {
      sprite, label, barBg, barFill, status,
      stepDots, stepLabel, stepIndex: -1,
      pacingTween, walkTween,
      consoleBg, consoleText, cursor, cursorTween, scanline, scanTween,
      lines: [], typedLine: '', typeQueue: [],
      startTime: Date.now(), gen, phase: 'analyzing',
    }
    this.evolvers.set(team, state)

    this._startEvolverConsole(team)
    // First real phase is implied by the spawning improving event.
    this._advanceEvolverPhase(team, 'analyzing')
  }

  /** Drive the evolver console typing loop — types the current phase's lines
   * char-by-char, scrolling a rolling buffer, cursor following the caret. */
  private _startEvolverConsole(team: 'red' | 'blue'): void {
    const MAX_LINES = 3
    const feed = (): string[] => ASIS_CONSOLE_LINES[
      (this.evolvers.get(team)?.phase === 'editing' ? 'analyzing'
        : this.evolvers.get(team)?.phase) as string
    ] || ASIS_CONSOLE_LINES.analyzing

    const tick = (): void => {
      const ev = this.evolvers.get(team)
      if (!ev || !ev.consoleText || !ev.cursor) return
      if (ev.typeQueue.length === 0) {
        // Finished a line (or first run) — commit it, pull the next.
        if (ev.typedLine) {
          ev.lines.push(ev.typedLine)
          if (ev.lines.length > MAX_LINES) ev.lines.shift()
          ev.typedLine = ''
        }
        const bank = feed()
        const next = bank[(ev.lines.length + ev.gen) % bank.length]
        ev.typeQueue = next.split('')
      }
      // Type next char
      ev.typedLine += ev.typeQueue.shift() ?? ''
      const shown = [...ev.lines, ev.typedLine]
      ev.consoleText.setText(shown.join('\n'))
      // Park the cursor at the end of the typed line (fixed line-height ≈ 9px).
      const lastY = ev.consoleText.y + (shown.length - 1) * 9
      ev.cursor.setPosition(ev.consoleText.x + ev.typedLine.length * 4.1, lastY)
      // Pause a beat at line end, else fast per-char.
      const delay = ev.typeQueue.length === 0 ? 420 : 34 + Math.random() * 26
      ev.typeTimer = this.time.delayedCall(delay, tick)
    }
    tick()
  }

  /** Advance the evolver's step markers + bar to a REAL backend work phase. */
  private _advanceEvolverPhase(
    team: 'red' | 'blue',
    phase: 'analyzing' | 'editing' | 'building' | 'benchmarking',
  ): void {
    const ev = this.evolvers.get(team)
    if (!ev) return
    // 'editing' shares the first step with 'analyzing' (one agent pass).
    const key = phase === 'editing' ? 'analyzing' : phase
    const idx = EVOLVER_STEPS.findIndex(s => s.key === key)
    if (idx < 0 || idx < ev.stepIndex) return
    ev.stepIndex = idx
    ev.phase = phase

    // Light completed + active dots; pulse the active one.
    ev.stepDots.forEach((dot, i) => {
      if (i < idx) { dot.setFillStyle(0x00ff88); dot.setScale(1) }
      else if (i === idx) { dot.setFillStyle(0xffcc44) }
      else { dot.setFillStyle(0x333333); dot.setScale(1) }
    })
    ev.pulseTween?.stop()
    const active = ev.stepDots[idx]
    if (active) {
      active.setScale(1)
      ev.pulseTween = this.tweens.add({
        targets: active, scale: 1.6, duration: 600, yoyo: true, repeat: -1, ease: 'Sine.easeInOut',
      })
    }

    ev.status.setText(`${EVOLVER_STEPS[idx].caption}…`)
    ev.stepLabel.setText(`step ${idx + 1}/${EVOLVER_STEPS.length}`)

    // Step the progress bar to this phase's segment.
    const targetW = ((ev.barBg.width - 2) * (idx + 1)) / EVOLVER_STEPS.length
    ev.pacingTween?.stop()
    ev.pacingTween = this.tweens.add({
      targets: ev.barFill, width: targetW, duration: 1200, ease: 'Quad.easeOut',
    })
  }

  /** Handle a real ASIS work-phase event from the backend. */
  private _onAsisPhase(
    team: 'red' | 'blue',
    phase: 'analyzing' | 'editing' | 'building' | 'benchmarking',
    message: string,
  ): void {
    // Spawn the evolver lazily if a phase event arrives before improving (race).
    if (!this.evolvers.get(team)) this._spawnEvolver(team, 0)
    if (!this.council.has(team)) this._startCouncil(team)
    this._advanceEvolverPhase(team, phase)
    this._councilTurn(team, phase)       // the responsible model speaks + hands off
    // Keep the improving side's lead agent non-idle through every ASIS phase so
    // the System State panel reflects the ongoing work (not stuck at idle).
    const leadId = team === 'red' ? 'atk1' : 'def1'
    useGhostStore.getState().updateAgent(leadId, {
      state: phase === 'benchmarking' ? 'acting' : 'thinking',
      message: `ASIS ${phase}: ${message.slice(0, 60)}`,
    })
    useGhostStore.getState().pushLog({
      agentId: leadId,
      message: `ASIS[${phase}] ${message.slice(0, 90)}`,
      state: 'thinking',
    })
  }

  /** Finalize the evolver sprite — green flash + success or red + rollback. */
  private _completeEvolver(team: 'red' | 'blue', gen: number, promoted: boolean, message: string): void {
    const ev = this.evolvers.get(team)
    if (!ev) {
      // No spawn (rare race): just zone-flash
      const zone = team === 'red' ? ZONES.red : ZONES.blue
      const color = promoted ? 0x00ff88 : 0xff4444
      const flash = this.add.rectangle(
        zone.x + zone.w / 2, zone.y + zone.h / 2, zone.w - 12, zone.h - 16, color, 0.22,
      ).setDepth(DEPTH.EFFECTS)
      this.tweens.add({
        targets: flash, alpha: { from: 0.22, to: 0 }, duration: 1500,
        onComplete: () => flash.destroy(),
      })
      return
    }

    ev.pacingTween?.stop()
    ev.walkTween?.stop()
    ev.pulseTween?.stop()
    ev.typeTimer?.remove()
    ev.scanTween?.stop()

    // Print the verdict into the console + freeze the cursor.
    if (ev.consoleText) {
      const verdictLine = promoted
        ? `$ PROMOTE gen_${gen} ✓ deployed live`
        : `$ ROLLBACK gen_${gen} — baseline kept`
      ev.lines.push(verdictLine)
      if (ev.lines.length > 3) ev.lines.shift()
      ev.consoleText.setText(ev.lines.join('\n')).setColor(promoted ? '#7CFFB2' : '#ff8080')
    }
    ev.cursorTween?.stop()
    ev.cursor?.setAlpha(0)

    // All real steps done — light every dot in the result colour.
    ev.stepDots.forEach(dot => { dot.setFillStyle(promoted ? 0x00ff88 : 0xff4444); dot.setScale(1) })
    ev.stepLabel.setText(promoted ? 'promoted ✓' : 'reverted')

    // Snap fill to 100% and recolor
    const barTargetW = (ev.barBg.width) - 2
    this.tweens.add({
      targets: ev.barFill, width: barTargetW, duration: 350, ease: 'Quad.easeOut',
    })
    ev.barFill.fillColor = promoted ? 0x00ff88 : 0xff4444
    ev.status.setText(promoted ? `✅ gen_${gen} deployed` : `↩ gen_${gen} rolled back`)
    ev.status.setColor(promoted ? '#00ff88' : '#ff4444')
    ev.label.setText(promoted ? `✅ ASIS gen_${gen}` : `↩ ASIS gen_${gen}`)

    // Hold result for 4s then despawn
    this.time.delayedCall(4000, () => this._despawnEvolver(team))
  }

  private _despawnEvolver(team: 'red' | 'blue', endMessage?: string): void {
    const ev = this.evolvers.get(team)
    if (!ev) return
    if (endMessage) ev.status.setText(endMessage)

    ev.pacingTween?.stop()
    ev.walkTween?.stop()
    ev.pulseTween?.stop()
    ev.typeTimer?.remove()
    ev.cursorTween?.stop()
    ev.scanTween?.stop()
    const consoleObjs = [ev.consoleBg, ev.consoleText, ev.cursor, ev.scanline]
      .filter(Boolean) as Phaser.GameObjects.GameObject[]
    this.tweens.add({
      targets: [ev.sprite, ev.label, ev.barBg, ev.barFill, ev.status, ev.stepLabel,
                ...ev.stepDots, ...consoleObjs],
      alpha: 0, duration: 600,
      onComplete: () => {
        ev.sprite.destroy(); ev.label.destroy()
        ev.barBg.destroy(); ev.barFill.destroy(); ev.status.destroy()
        ev.stepLabel.destroy(); ev.stepDots.forEach(d => d.destroy())
        ev.consoleBg?.destroy(); ev.consoleText?.destroy()
        ev.cursor?.destroy(); ev.scanline?.destroy()
      },
    })
    this.evolvers.delete(team)
  }

  // ── Reporter patrol (single-step movement during live battle) ──────────
  private _reporterPatrol(zone: Zone): void {
    if (this.reporterBusy) return   // full end-of-battle route takes priority
    const rt = this.runtime.get('reporter')
    if (!rt) return

    const patrolPos: Record<Zone, { x: number; y: number }> = {
      red:      { x: 200, y: 360 },
      blue:     { x: 1080, y: 360 },
      center:   { x: 640, y: 400 },
      judge:    { x: 430, y: 636 },   // aligned with new verdict console (x=336–558)
      reporter: { x: 900, y: 620 },
    }

    const target = patrolPos[zone]
    const dist = Phaser.Math.Distance.Between(rt.sprite.x, rt.sprite.y, target.x, target.y)
    if (dist < 60) return   // already close enough — no redundant micro-move

    const durationMs = Math.max(600, (dist / 150) * 1000)
    rt.sprite.setFlipX(target.x < rt.sprite.x)
    this.onStateChange('reporter', 'moving', `Observing ${zone} zone...`)

    rt.moveTween?.stop()
    rt.moveTween = this.tweens.add({
      targets: [rt.sprite, rt.tag],
      x: target.x,
      y: { value: target.y, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
      duration: durationMs,
      ease: 'Linear',
      onComplete: () => {
        rt.sprite.setFlipX(false)
        this.onStateChange('reporter', 'gathering', 'Collecting logs...')
      },
    })
  }

  // ── Reporter route ─────────────────────────────────────────────────────
  private startReporterRoute(): void {
    if (this.reporterBusy) return
    this.reporterBusy = true
    this.reporterStep = 1 // skip home (already there)

    // Subscribe to zoneInsights updates so we can replace placeholder bubbles
    // when the LLM response arrives while the reporter is already at a waypoint.
    // Zustand v5: subscribe takes (state, prevState) — no selector form without middleware.
    this.unsubZoneInsights?.()
    this.unsubZoneInsights = useGhostStore.subscribe((state, prevState) => {
      if (state.zoneInsights === prevState.zoneInsights) return
      const zoneInsights = state.zoneInsights
      if (!zoneInsights || !this.reporterBusy) return
      const rt = this.runtime.get('reporter')
      if (!rt) return
      // Replace bubble for the current dwell step (one behind advanceReporter's step counter
      // because step was already incremented after arrival)
      const dwellStep = this.reporterStep - 1
      const key = STEP_ZONE_KEY[dwellStep]
      if (key && zoneInsights[key]) {
        this.showBubble(rt, zoneInsights[key])
      }
    })

    this.advanceReporter()
  }

  /** Get the bubble message for the current step: real insight or placeholder. */
  private _reporterBubbleText(step: number): string {
    const key = STEP_ZONE_KEY[step]
    if (key) {
      const insights = useGhostStore.getState().zoneInsights
      if (insights?.[key]) return insights[key]
    }
    return STEP_PLACEHOLDER[step] ?? 'Collecting data...'
  }

  private advanceReporter(): void {
    const rt = this.runtime.get('reporter')
    if (!rt) return

    if (this.reporterStep >= REPORTER_ROUTE.length) {
      this.reporterBusy = false
      this.unsubZoneInsights?.()
      this.unsubZoneInsights = undefined
      this.onStateChange('reporter', 'idle', 'Report filed.')
      return
    }

    const target = REPORTER_ROUTE[this.reporterStep]
    const fromX = rt.sprite.x, fromY = rt.sprite.y
    const dist = Phaser.Math.Distance.Between(fromX, fromY, target.x, target.y)
    const durationMs = Math.max(800, (dist / 130) * 1000)

    const isPrinter = this.reporterStep === REPORTER_ROUTE.length - 2  // step 5
    const actionState: AgentState = isPrinter ? 'printing' : 'gathering'

    // Flip sprite in direction of travel
    rt.sprite.setFlipX(target.x < fromX)

    this.onStateChange('reporter', 'moving', 'On the move...')

    rt.moveTween?.stop()
    rt.moveTween = this.tweens.add({
      targets: [rt.sprite, rt.tag],
      x: target.x,
      y: { value: target.y, onUpdate: () => rt.tag.setY(rt.sprite.y - 68) },
      duration: durationMs,
      ease: 'Linear',
      onComplete: () => {
        const scheduleTok = this.reporterRouteCancelTok
        rt.sprite.setFlipX(false)

        const bubbleText = this._reporterBubbleText(this.reporterStep)
        this.onStateChange('reporter', actionState, bubbleText)

        if (isPrinter) {
          // Dwell at printer — narrative is already in the store from WS; user opens SCRIBE manually (no auto-popup).
          this.time.delayedCall(1400, () => {
            if (scheduleTok !== this.reporterRouteCancelTok) return
            this.reporterStep++
            this.time.delayedCall(600, () => {
              if (scheduleTok !== this.reporterRouteCancelTok) return
              this.advanceReporter()
            })
          })
        } else {
          // Dwell at zone; shorter pause once back at desk (final waypoint) — route is visually done.
          const arrivedAtFinalDesk =
            this.reporterStep === REPORTER_ROUTE.length - 1
          this.reporterStep++
          const dwellMs = arrivedAtFinalDesk ? 480 : 3000
          this.time.delayedCall(dwellMs, () => {
            if (scheduleTok !== this.reporterRouteCancelTok) return
            this.advanceReporter()
          })
        }
      },
    })
  }

  // ── Particle bursts ────────────────────────────────────────────────────
  private burstParticles(x: number, y: number, tint: number): void {
    const emitter = this.add.particles(x, y, 'decor_cable', {
      speed: { min: 50, max: 130 },
      angle: { min: -140, max: -40 },
      scale: { start: 0.7, end: 0 },
      alpha: { start: 1, end: 0 },
      lifespan: 550,
      quantity: 14,
      tint,
    })
    this.time.delayedCall(600, () => emitter.destroy())
  }

  // ── Atmosphere loops ───────────────────────────────────────────────────
  private startGlitchLoop(): void {
    this.time.addEvent({
      delay: 3500 + Math.random() * 2000,
      loop: true,
      callback: () => {
        const { battleMode, battleStatus, backendOnline } = useGhostStore.getState()
        if (!backendOnline) return
        if (battleMode === 'live' && battleStatus !== 'running') return
        const victim = this.runtime.get('victim')
        if (!victim || victim.state === 'failed') return
        this.tweens.add({
          targets: victim.sprite,
          x: victim.sprite.x + (Math.random() - 0.5) * 7,
          alpha: Math.random() > 0.4 ? 0.4 : 0.9,
          duration: 35,
          yoyo: true,
          repeat: 4,
          onComplete: () => {
            victim.sprite.setX(SPAWN.victim.x)
            victim.sprite.setAlpha(1)
          },
        })
      },
    })
  }

  private startLightFlicker(): void {
    this.time.addEvent({
      delay: 4000 + Math.random() * 3000,
      loop: true,
      callback: () => {
        if (!useGhostStore.getState().backendOnline) return
        const lights = this.children.list.filter(
          c => c instanceof Phaser.GameObjects.Image &&
            (c.texture.key === 'decor_light_red' || c.texture.key === 'decor_light_blue')
        ) as Phaser.GameObjects.Image[]
        if (!lights.length) return
        const pick = lights[Math.floor(Math.random() * lights.length)]
        this.tweens.add({
          targets: pick,
          alpha: 0.08,
          duration: 55,
          yoyo: true,
          repeat: 3,
          onComplete: () => pick.setAlpha(0.65),
        })
      },
    })
  }
}
