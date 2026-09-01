import Phaser from 'phaser'

// Internal color palette (hex numbers for Phaser)
const C = {
  dark:     0x0a0a0f,
  floor:    0x161b22,
  wall:     0x0d1117,
  mid:      0x21262d,
  light:    0x334155,
  white:    0xffffff,
  red:      0xff4444,
  orange:   0xff8800,
  blue:     0x4488ff,
  cyan:     0x00ccff,
  green:    0x00ff88,
  yellow:   0xffdd00,
  purple:   0xcc44ff,
}

export class BootScene extends Phaser.Scene {
  constructor() {
    super({ key: 'BootScene' })
  }

  create(): void {
    this.makeZoneTiles()
    this.makeAgentSprites()
    this.makeObjectTextures()
    this.makeDecorTextures()
    this.scene.start('OpsScene')
  }

  // ── Zone floor tiles ────────────────────────────────────────────────────
  private makeZoneTiles(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    const makeTile = (key: string, fill: number, line: number) => {
      g.clear()
      g.fillStyle(fill)
      g.fillRect(0, 0, 48, 48)
      g.lineStyle(1, line, 0.35)
      g.strokeRect(0, 0, 48, 48)
      g.generateTexture(key, 48, 48)
    }

    makeTile('tile_neutral', C.floor,    C.mid)
    makeTile('tile_red',     0x170808,   0x2e1010)
    makeTile('tile_blue',    0x08101a,   0x10223a)
    makeTile('tile_judge',   0x141208,   0x2a2414)
    makeTile('tile_dark',    C.dark,     C.mid)

    g.destroy()
  }

  // ── Agent pixel-art sprites (48×64 per frame) ──────────────────────────
  private makeAgentSprites(): void {
    type RoleSpec = { key: string; body: number; accent: number; visor: number; hasHelmet: boolean }
    const specs: RoleSpec[] = [
      { key: 'spr_atk1',     body: C.red,    accent: C.orange, visor: C.orange, hasHelmet: true  },
      { key: 'spr_atk2',     body: C.orange, accent: C.red,    visor: C.red,    hasHelmet: true  },
      { key: 'spr_def1',     body: C.blue,   accent: C.cyan,   visor: C.cyan,   hasHelmet: false },
      { key: 'spr_def2',     body: C.cyan,   accent: C.blue,   visor: C.blue,   hasHelmet: false },
      { key: 'spr_victim',   body: C.purple, accent: C.white,  visor: C.white,  hasHelmet: false },
      { key: 'spr_judge',    body: C.yellow, accent: C.white,  visor: C.white,  hasHelmet: true  },
      { key: 'spr_reporter', body: C.green,  accent: C.white,  visor: C.white,  hasHelmet: false },
    ]
    for (const s of specs) this.drawAgent(s.key, s.body, s.accent, s.visor, s.hasHelmet)
    // Participant fighters — one per side, representing the connected external
    // project itself. Distinct silhouette (chevron crest + pauldrons + mast).
    this.drawAgent('spr_red_fighter',  C.red,  C.white, C.orange, true,  true)
    this.drawAgent('spr_blue_fighter', C.blue, C.white, C.cyan,   false, true)
  }

  private drawAgent(
    key: string,
    body: number,
    accent: number,
    visor: number,
    hasHelmet: boolean,
    isFighter = false,
  ): void {
    const W = 48, H = 64
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Drop shadow
    g.fillStyle(0x000000, 0.3)
    g.fillEllipse(W / 2, H - 3, 26, 8)

    // Boots / legs
    g.fillStyle(C.mid)
    g.fillRect(15, 46, 7, 14)
    g.fillRect(26, 46, 7, 14)

    // Torso
    g.fillStyle(body, 0.92)
    g.fillRect(13, 26, 22, 22)

    // Chest detail
    g.fillStyle(accent, 0.55)
    g.fillRect(19, 30, 10, 4)
    g.fillRect(20, 35, 8, 2)

    // Arms
    g.fillStyle(body, 0.80)
    g.fillRect(5,  28, 8, 14)
    g.fillRect(35, 28, 8, 14)

    // Head (balaclava / dark base)
    g.fillStyle(0x1e2530)
    g.fillRect(15, 10, 18, 18)

    // Visor / eye strip
    g.fillStyle(visor, 0.85)
    g.fillRect(17, 14, 14, 5)
    // Visor reflection glint
    g.fillStyle(0xffffff, 0.25)
    g.fillRect(17, 14, 6, 2)

    // Helmet (hackers have tactical helmets / hoods)
    if (hasHelmet) {
      g.fillStyle(C.dark)
      g.fillRect(13, 7, 22, 6)
      g.fillStyle(accent, 0.4)
      g.fillRect(13, 11, 22, 2)
    } else {
      // Hood outline
      g.lineStyle(2, accent, 0.35)
      g.strokeRect(15, 10, 18, 18)
    }

    // Small indicator LED on chest
    g.fillStyle(accent)
    g.fillCircle(34, 32, 2)

    // Fighter variant: this sprite represents the connected external project
    // itself (the single protocol channel). Give it a distinct silhouette —
    // shoulder pauldrons + a forward chevron crest + a taller antenna — so it
    // reads clearly apart from the three assisting-model agents.
    if (isFighter) {
      // Shoulder pauldrons
      g.fillStyle(accent, 0.9)
      g.fillRect(3, 26, 6, 6)
      g.fillRect(39, 26, 6, 6)
      // Forward chevron crest on the chest
      g.fillStyle(0xffffff, 0.9)
      g.fillTriangle(24, 24, 18, 34, 30, 34)
      g.fillStyle(accent, 0.9)
      g.fillTriangle(24, 28, 20, 34, 28, 34)
      // Antenna / banner mast
      g.fillStyle(accent, 1)
      g.fillRect(23, 2, 2, 6)
      g.fillCircle(24, 2, 2)
    }

    g.generateTexture(key, W, H)
    g.destroy()
  }

  // ── Interactive object textures ────────────────────────────────────────
  private makeObjectTextures(): void {
    this.makeTerminal('obj_term_red',  C.green,  0x001500)
    this.makeTerminal('obj_term_blue', C.blue,   0x000515)
    this.makeAICore()
    this.makePrinter()
  }

  private makeTerminal(key: string, borderColor: number, screenBg: number): void {
    const W = 52, H = 68
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Cabinet
    g.fillStyle(0x0d1117)
    g.fillRect(0, 0, W, H)
    g.lineStyle(2, borderColor, 0.85)
    g.strokeRect(1, 1, W - 2, H - 2)

    // Screen
    g.fillStyle(screenBg)
    g.fillRect(5, 5, W - 10, 44)

    // Scan lines on screen
    for (let y = 7; y < 47; y += 4) {
      g.lineStyle(1, borderColor, 0.12)
      g.lineBetween(5, y, W - 5, y)
    }

    // Blinking cursor
    g.fillStyle(borderColor, 0.9)
    g.fillRect(9, 40, 5, 2)

    // Status row
    g.fillStyle(C.mid)
    g.fillRect(5, 51, W - 10, 3)
    g.fillStyle(borderColor, 0.5)
    g.fillRect(5, 51, 12, 3)

    // Desk base
    g.fillStyle(0x21262d)
    g.fillRect(14, 56, W - 28, 7)
    g.fillRect(8,  62, W - 16, 5)

    g.generateTexture(key, W, H)
    g.destroy()
  }

  private makeAICore(): void {
    const S = 100
    const cx = S / 2, cy = S / 2
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Background
    g.fillStyle(0x08001a)
    g.fillCircle(cx, cy, 48)

    // Outer rotating ring (static reference)
    g.lineStyle(3, C.purple, 0.7)
    g.strokeCircle(cx, cy, 42)

    // Inner ring
    g.lineStyle(2, C.cyan, 0.45)
    g.strokeCircle(cx, cy, 32)

    // Spokes
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2
      g.lineStyle(1, C.purple, 0.30)
      g.lineBetween(cx, cy, cx + Math.cos(a) * 40, cy + Math.sin(a) * 40)
    }

    // Core glow
    g.fillStyle(C.purple, 0.25)
    g.fillCircle(cx, cy, 22)
    g.fillStyle(C.cyan, 0.55)
    g.fillCircle(cx, cy, 10)
    g.fillStyle(0xffffff, 0.7)
    g.fillCircle(cx, cy, 4)

    g.generateTexture('obj_aicore', S, S)
    g.destroy()
  }

  private makePrinter(): void {
    const W = 60, H = 52
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(C.mid)
    g.fillRect(0, 0, W, H)
    g.lineStyle(2, C.green, 0.65)
    g.strokeRect(1, 1, W - 2, H - 2)

    // Paper output slot
    g.fillStyle(C.dark)
    g.fillRect(7, 6, W - 14, 7)

    // Paper sticking out
    g.fillStyle(0xf0eed0)
    g.fillRect(15, 4, W - 30, 5)

    // Vent slits
    for (let i = 0; i < 4; i++) {
      g.fillStyle(C.dark)
      g.fillRect(7, 18 + i * 5, W - 14, 2)
    }

    // Status LED
    g.fillStyle(C.green)
    g.fillCircle(W - 10, H - 10, 4)

    // Brand chip
    g.fillStyle(C.dark)
    g.fillRect(7, H - 14, 18, 8)

    g.generateTexture('obj_printer', W, H)
    g.destroy()
  }

  // ── Decorative textures ────────────────────────────────────────────────
  private makeDecorTextures(): void {
    this.makeServerRack()
    this.makeNeonLight('decor_light_red',  C.red)
    this.makeNeonLight('decor_light_blue', C.blue)
    this.makeGraffiti()
    this.makeCableSegment()
    this.makeFloorGrid()
    // Additional environment props
    this.makeWorkstation('decor_ws_red',  C.red,  0x120000)
    this.makeWorkstation('decor_ws_blue', C.blue, 0x000510)
    this.makeServerTower()
    this.makeWallPanel('decor_panel_red',  C.red)
    this.makeWallPanel('decor_panel_blue', C.blue)
    this.makeNetworkHub()
    this.makeFloorVent()
    this.makeHoloDisplay('decor_holo_red',    C.red,    0x130000)
    this.makeHoloDisplay('decor_holo_ctr',    C.purple, 0x060010)
    this.makeHoloDisplay('decor_holo_blue',   C.blue,   0x00040e)
    this.makeHoloDisplay('decor_holo_yellow', C.yellow, 0x0e0c00)
    this.makeHoloDisplay('decor_holo_green',  C.green,  0x000e04)
    this.makeWallPanel('decor_panel_yellow', C.yellow)
    this.makeWallPanel('decor_panel_green',  C.green)
    this.makeReportPile()
    this.makeLaptop()
    this.makeFilingCabinet()
    this.makeOverheadLight('decor_overhead_red',    C.red)
    this.makeOverheadLight('decor_overhead_blue',   C.blue)
    this.makeOverheadLight('decor_overhead_yellow', C.yellow)
    this.makeOverheadLight('decor_overhead_green',  C.green)
    this.makeDataNode()
  }

  private makeServerRack(): void {
    const W = 44, H = 88
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x0d1117)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, C.mid, 0.9)
    g.strokeRect(0, 0, W, H)

    // Rack units
    const ledColors = [C.green, C.red, C.blue, C.yellow, C.green]
    for (let i = 0; i < 10; i++) {
      g.fillStyle(0x161b22)
      g.fillRect(2, 2 + i * 8, W - 4, 7)
      g.lineStyle(1, C.light, 0.15)
      g.strokeRect(2, 2 + i * 8, W - 4, 7)
      // LED
      const col = ledColors[i % ledColors.length]
      g.fillStyle(col, 0.75)
      g.fillRect(W - 7, 4 + i * 8, 3, 3)
    }

    g.generateTexture('decor_rack', W, H)
    g.destroy()
  }

  private makeNeonLight(key: string, color: number): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)
    g.fillStyle(color, 0.85)
    g.fillRect(0, 0, 6, 28)
    g.lineStyle(1, 0xffffff, 0.2)
    g.strokeRect(0, 0, 6, 28)
    g.generateTexture(key, 6, 28)
    g.destroy()
  }

  private makeGraffiti(): void {
    // Pixelated "//GHOST" tag
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)
    const pixels = [
      // slash slash G H O S T (simplified pixel font 4×5 each, 1px gap)
      [0,1,0,0, 0,0,1,0, 0,1,1,0, 1,0,0,1, 1,1,1,0, 0,1,1,1, 1,0,0,0],
      [0,0,1,0, 0,1,0,0, 1,0,0,1, 1,1,1,1, 1,0,0,1, 1,0,0,0, 0,1,0,0],
      [0,1,0,0, 0,0,1,0, 1,0,1,1, 1,0,0,1, 1,0,0,1, 1,1,1,0, 0,1,0,0],
      [0,0,0,0, 0,0,0,0, 1,0,0,1, 1,0,0,1, 0,1,1,0, 1,0,0,0, 0,1,0,0],
      [0,0,0,0, 0,0,0,0, 0,1,1,0, 1,0,0,1, 0,0,0,0, 1,1,1,1, 1,0,0,0],
    ]
    g.fillStyle(C.green, 0.55)
    pixels.forEach((row, ry) =>
      row.forEach((px, rx) => {
        if (px) g.fillRect(rx * 5, ry * 6, 4, 5)
      })
    )
    g.generateTexture('decor_graffiti', 175, 32)
    g.destroy()
  }

  private makeCableSegment(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)
    g.lineStyle(2, 0x21262d, 1)
    g.lineBetween(0, 4, 16, 4)
    g.lineStyle(1, C.green, 0.15)
    g.lineBetween(0, 4, 16, 4)
    g.generateTexture('decor_cable', 16, 8)
    g.destroy()
  }

  private makeFloorGrid(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)
    g.fillStyle(0x0f1520)
    g.fillRect(0, 0, 16, 16)
    g.lineStyle(1, C.purple, 0.08)
    g.strokeRect(0, 0, 16, 16)
    g.generateTexture('tile_grid', 16, 16)
    g.destroy()
  }

  // ── Workstation desk with dual monitors ────────────────────────────────
  private makeWorkstation(key: string, accent: number, screenBg: number): void {
    const W = 80, H = 52
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Desk surface
    g.fillStyle(0x1a1f28)
    g.fillRect(0, 28, W, 12)
    g.fillStyle(0x252d3a)
    g.fillRect(0, 28, W, 2) // top-edge highlight

    // Left monitor bezel
    g.fillStyle(0x0d1117)
    g.fillRect(4, 4, 28, 26)
    g.lineStyle(1, accent, 0.65)
    g.strokeRect(4, 4, 28, 26)
    g.fillStyle(screenBg)
    g.fillRect(6, 6, 24, 20)
    for (let sy = 8; sy < 25; sy += 3) {
      g.lineStyle(1, accent, 0.07)
      g.lineBetween(6, sy, 30, sy)
    }
    // Terminal text lines
    g.fillStyle(accent, 0.65)
    g.fillRect(8, 9,  15, 1)
    g.fillRect(8, 12, 10, 1)
    g.fillRect(8, 15, 19, 1)
    g.fillRect(8, 18,  7, 1)
    g.fillRect(8, 21,  2, 2) // cursor blink
    // Stand
    g.fillStyle(0x21262d)
    g.fillRect(16, 30, 4, 4)
    g.fillRect(12, 32, 12, 2)

    // Right monitor bezel
    g.fillStyle(0x0d1117)
    g.fillRect(44, 4, 28, 26)
    g.lineStyle(1, accent, 0.65)
    g.strokeRect(44, 4, 28, 26)
    g.fillStyle(screenBg)
    g.fillRect(46, 6, 24, 20)
    for (let sy = 8; sy < 25; sy += 3) {
      g.lineStyle(1, accent, 0.07)
      g.lineBetween(46, sy, 70, sy)
    }
    // Bar-chart data viz on right monitor
    const bars = [6, 11, 5, 14, 9, 13, 7, 10]
    for (let bi = 0; bi < bars.length; bi++) {
      g.fillStyle(accent, 0.30 + (bars[bi] / 14) * 0.50)
      g.fillRect(47 + bi * 3, 25 - bars[bi], 2, bars[bi])
    }
    // Stand
    g.fillStyle(0x21262d)
    g.fillRect(56, 30, 4, 4)
    g.fillRect(52, 32, 12, 2)

    // Keyboard
    g.fillStyle(0x161b22)
    g.fillRect(8, 34, 56, 7)
    g.lineStyle(1, 0x21262d, 0.55)
    for (let ki = 0; ki < 10; ki++) {
      g.strokeRect(9 + ki * 5, 35, 4, 2)
      g.strokeRect(9 + ki * 5, 38, 4, 2)
    }
    g.fillStyle(accent, 0.7)
    g.fillRect(60, 35, 2, 1) // caps-lock LED

    // Legs
    g.fillStyle(0x2d3748)
    g.fillRect(4,  40, 5, 12)
    g.fillRect(71, 40, 5, 12)

    g.generateTexture(key, W, H)
    g.destroy()
  }

  // ── Slim vertical server tower ────────────────────────────────────────
  private makeServerTower(): void {
    const W = 22, H = 60
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x0d1117)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, C.mid, 0.8)
    g.strokeRect(0, 0, W, H)

    const bayColors = [C.green, C.green, C.yellow, C.green, C.red, C.green, C.cyan]
    for (let i = 0; i < 7; i++) {
      g.fillStyle(0x161b22)
      g.fillRect(2, 3 + i * 8, W - 4, 6)
      g.lineStyle(1, 0x21262d, 0.6)
      g.strokeRect(2, 3 + i * 8, W - 4, 6)
      // Activity bar
      g.fillStyle(0x252d3a)
      g.fillRect(3, 5 + i * 8, 10, 2)
      // LED
      g.fillStyle(bayColors[i], 0.85)
      g.fillRect(W - 6, 5 + i * 8, 2, 2)
    }

    // Power button
    g.fillStyle(C.green, 0.9)
    g.fillCircle(W / 2, H - 8, 3)
    g.lineStyle(1, C.green, 0.35)
    g.strokeCircle(W / 2, H - 8, 5)

    g.fillStyle(C.mid, 0.4)
    g.fillRect(0, H - 5, W, 2)

    g.generateTexture('decor_svr', W, H)
    g.destroy()
  }

  // ── Wall-mounted control panel ────────────────────────────────────────
  private makeWallPanel(key: string, color: number): void {
    const W = 72, H = 44
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x0d1117)
    g.fillRect(0, 0, W, H)
    g.lineStyle(2, color, 0.55)
    g.strokeRect(1, 1, W - 2, H - 2)
    g.fillStyle(color, 0.04)
    g.fillRect(2, 2, W - 4, H - 4)

    // Header bar
    g.fillStyle(color, 0.14)
    g.fillRect(2, 2, W - 4, 9)
    g.lineStyle(1, color, 0.30)
    g.lineBetween(2, 11, W - 2, 11)
    // Label stub
    g.fillStyle(color, 0.55)
    g.fillRect(5, 5, 22, 2)
    g.fillRect(30, 5, 10, 2)

    // LED grid: 4 cols × 3 rows
    const ledOn = [1,1,0,1, 1,0,1,1, 0,1,1,0]
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 4; c++) {
        const on = ledOn[r * 4 + c]
        g.fillStyle(on ? color : 0x161b22, on ? 0.85 : 1.0)
        g.fillRect(5 + c * 12, 14 + r * 8, 6, 6)
        if (on) {
          g.lineStyle(1, color, 0.28)
          g.strokeRect(4 + c * 12, 13 + r * 8, 8, 8)
        }
      }
    }

    // Progress bars (right side)
    const fills = [9, 5, 11]
    for (let pi = 0; pi < 3; pi++) {
      g.fillStyle(0x161b22)
      g.fillRect(54, 14 + pi * 7, 14, 5)
      g.fillStyle(color, 0.70)
      g.fillRect(54, 14 + pi * 7, fills[pi], 5)
    }

    // Large status indicator
    g.fillStyle(color, 0.9)
    g.fillCircle(63, H - 9, 4)
    g.lineStyle(1, color, 0.35)
    g.strokeCircle(63, H - 9, 6)

    g.generateTexture(key, W, H)
    g.destroy()
  }

  // ── Network switch / hub ──────────────────────────────────────────────
  private makeNetworkHub(): void {
    const W = 56, H = 18
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x161b22)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, C.mid, 0.9)
    g.strokeRect(0, 0, W, H)

    const portLeds = [C.green, C.green, C.yellow, C.green, C.red, C.green, C.green, C.cyan]
    for (let pi = 0; pi < 8; pi++) {
      g.fillStyle(0x0d1117)
      g.fillRect(3 + pi * 6, 4, 5, 7)
      g.lineStyle(1, C.light, 0.25)
      g.strokeRect(3 + pi * 6, 4, 5, 7)
      g.fillStyle(portLeds[pi], 0.82)
      g.fillRect(4 + pi * 6, 12, 3, 2)
    }
    // Power LED
    g.fillStyle(C.green, 0.9)
    g.fillRect(W - 6, 7, 3, 3)

    g.generateTexture('decor_hub', W, H)
    g.destroy()
  }

  // ── Floor ventilation grate ──────────────────────────────────────────
  private makeFloorVent(): void {
    const W = 48, H = 16
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x0a0a0f)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, C.mid, 0.5)
    g.strokeRect(0, 0, W, H)

    // Horizontal slats
    for (let i = 0; i < 5; i++) {
      g.fillStyle(0x161b22)
      g.fillRect(3, 2 + i * 3, W - 6, 1)
    }
    // Subtle under-glow
    g.fillStyle(C.cyan, 0.05)
    g.fillRect(1, 1, W - 2, H - 2)

    g.generateTexture('decor_vent', W, H)
    g.destroy()
  }

  // ── Holographic monitor display ───────────────────────────────────────
  private makeHoloDisplay(key: string, color: number, bg: number): void {
    const W = 60, H = 44
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(bg)
    g.fillRect(0, 0, W, H)

    // Outer glow border
    g.lineStyle(2, color, 0.7)
    g.strokeRect(1, 1, W - 2, H - 2)
    g.lineStyle(1, color, 0.18)
    g.strokeRect(4, 4, W - 8, H - 8)

    // Corner brackets
    const cs = 7
    g.lineStyle(2, color, 0.95)
    g.lineBetween(1, 1, 1 + cs, 1);    g.lineBetween(1, 1, 1, 1 + cs)
    g.lineBetween(W-1, 1, W-1-cs, 1);  g.lineBetween(W-1, 1, W-1, 1+cs)
    g.lineBetween(1, H-1, 1+cs, H-1);  g.lineBetween(1, H-1, 1, H-1-cs)
    g.lineBetween(W-1,H-1,W-1-cs,H-1); g.lineBetween(W-1,H-1,W-1,H-1-cs)

    // Header stubs
    g.fillStyle(color, 0.50)
    g.fillRect(7, 7, 22, 2)
    g.fillRect(32, 7, 12, 2)
    g.fillRect(46, 7, 6, 2)

    // Waveform
    g.lineStyle(1, color, 0.78)
    const wy = 22
    const wp = [5,wy, 9,wy-5, 14,wy+4, 19,wy-6, 24,wy+2, 29,wy-4, 34,wy+5, 39,wy-3, 44,wy+1, 49,wy-6, 54,wy+3]
    for (let i = 0; i < wp.length - 2; i += 2) {
      g.lineBetween(wp[i], wp[i+1], wp[i+2], wp[i+3])
    }

    // Bar chart (bottom)
    const bData = [6, 10, 14, 8, 16, 11, 13, 9]
    for (let bi = 0; bi < bData.length; bi++) {
      const bh = Math.floor((bData[bi] / 16) * 10)
      g.fillStyle(color, 0.28 + (bData[bi] / 16) * 0.48)
      g.fillRect(5 + bi * 7, H - 6 - bh, 5, bh)
    }

    // Scanline overlay
    for (let sy = 5; sy < H - 5; sy += 4) {
      g.lineStyle(1, color, 0.03)
      g.lineBetween(5, sy, W - 5, sy)
    }

    g.generateTexture(key, W, H)
    g.destroy()
  }

  // ── Stack of printed reports ──────────────────────────────────────────
  private makeReportPile(): void {
    const W = 36, H = 26
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Back sheet (offset)
    g.fillStyle(0xe0ddc8)
    g.fillRect(5, 6, 28, 20)
    g.lineStyle(1, 0x7a7860, 0.45)
    g.strokeRect(5, 6, 28, 20)

    // Mid sheet
    g.fillStyle(0xeceaD4)
    g.fillRect(2, 3, 28, 20)
    g.lineStyle(1, 0x7a7860, 0.45)
    g.strokeRect(2, 3, 28, 20)

    // Top sheet
    g.fillStyle(0xf6f4de)
    g.fillRect(0, 0, 28, 20)
    g.lineStyle(1, 0x5a5840, 0.65)
    g.strokeRect(0, 0, 28, 20)
    // Green header highlight
    g.fillStyle(C.green, 0.25)
    g.fillRect(1, 1, 26, 3)
    // Text lines
    g.fillStyle(0x334155, 0.55)
    g.fillRect(2, 6,  18, 1)
    g.fillRect(2, 9,  14, 1)
    g.fillRect(2, 12, 20, 1)
    g.fillRect(2, 15, 10, 1)

    g.generateTexture('decor_report', W, H)
    g.destroy()
  }

  // ── Open laptop ───────────────────────────────────────────────────────
  private makeLaptop(): void {
    const W = 44, H = 30
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Lid / screen area
    g.fillStyle(0x1a1f28)
    g.fillRect(0, 0, 40, 20)
    g.lineStyle(1, C.mid, 0.75)
    g.strokeRect(0, 0, 40, 20)
    // Screen
    g.fillStyle(0x001208)
    g.fillRect(2, 2, 36, 16)
    g.fillStyle(C.green, 0.70)
    g.fillRect(4, 4,  20, 1)
    g.fillRect(4, 7,  14, 1)
    g.fillRect(4, 10, 24, 1)
    g.fillRect(4, 13,  8, 1)
    g.fillStyle(C.green, 0.90)
    g.fillRect(4, 13, 2, 2) // cursor

    // Base / keyboard
    g.fillStyle(0x161b22)
    g.fillRect(0, 20, 44, 10)
    g.lineStyle(1, C.mid, 0.55)
    g.strokeRect(0, 20, 44, 10)
    // Trackpad
    g.fillStyle(0x0d1117)
    g.fillRect(15, 22, 14, 6)
    g.lineStyle(1, C.light, 0.28)
    g.strokeRect(15, 22, 14, 6)
    // Key stubs
    for (let ki = 0; ki < 7; ki++) {
      g.fillStyle(0x21262d)
      g.fillRect(1 + ki * 4, 21, 3, 2)
    }

    g.generateTexture('decor_laptop', W, H)
    g.destroy()
  }

  // ── Overhead ceiling lamp strip ───────────────────────────────────────
  private makeOverheadLight(key: string, color: number): void {
    const W = 80, H = 10
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    g.fillStyle(0x161b22)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, color, 0.6)
    g.strokeRect(0, 0, W, H)

    // Light strip glow
    g.fillStyle(color, 0.22)
    g.fillRect(2, 2, W - 4, H - 4)

    // Mounting brackets
    g.fillStyle(0x21262d)
    g.fillRect(10, 0, 4, H)
    g.fillRect(W - 14, 0, 4, H)

    // Lens segments
    for (let li = 0; li < 5; li++) {
      g.fillStyle(color, 0.40)
      g.fillRect(16 + li * 10, 1, 8, H - 2)
      g.lineStyle(1, color, 0.20)
      g.strokeRect(16 + li * 10, 1, 8, H - 2)
    }

    g.generateTexture(key, W, H)
    g.destroy()
  }

  // ── Filing cabinet (stacked drawers) ─────────────────────────────────
  private makeFilingCabinet(): void {
    const W = 32, H = 52
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Cabinet body
    g.fillStyle(0x1a1f28)
    g.fillRect(0, 0, W, H)
    g.lineStyle(1, C.mid, 0.85)
    g.strokeRect(0, 0, W, H)

    // Four drawers
    const drawerColors = [C.green, C.yellow, C.green, C.yellow]
    for (let i = 0; i < 4; i++) {
      g.fillStyle(0x21262d)
      g.fillRect(2, 2 + i * 12, W - 4, 10)
      g.lineStyle(1, C.light, 0.18)
      g.strokeRect(2, 2 + i * 12, W - 4, 10)
      // Drawer handle
      g.fillStyle(drawerColors[i], 0.55)
      g.fillRect(W / 2 - 5, 6 + i * 12, 10, 2)
      // Label indicator
      g.fillStyle(C.light, 0.35)
      g.fillRect(4, 4 + i * 12, 14, 1)
    }

    // Foot
    g.fillStyle(0x0d1117)
    g.fillRect(2, H - 3, W - 4, 3)

    g.generateTexture('decor_cabinet', W, H)
    g.destroy()
  }

  // ── Center-zone data node / pylon ─────────────────────────────────────
  private makeDataNode(): void {
    const W = 32, H = 48
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = this.make.graphics({ x: 0, y: 0, add: false } as any)

    // Base pedestal
    g.fillStyle(0x161b22)
    g.fillRect(8, 38, 16, 10)
    g.lineStyle(1, C.purple, 0.45)
    g.strokeRect(8, 38, 16, 10)

    // Shaft
    g.fillStyle(0x0d1117)
    g.fillRect(13, 14, 6, 26)
    g.lineStyle(1, C.cyan, 0.30)
    g.strokeRect(13, 14, 6, 26)

    // Glowing crystal top
    g.lineStyle(1, C.purple, 0.70)
    g.fillStyle(C.purple, 0.20)
    g.fillTriangle(16, 2, 5, 16, 27, 16)
    g.strokeTriangle(16, 2, 5, 16, 27, 16)

    // Inner bright core
    g.fillStyle(C.cyan, 0.55)
    g.fillTriangle(16, 6, 9, 15, 23, 15)

    // Ring pulse lines on shaft
    for (let ri = 0; ri < 3; ri++) {
      g.lineStyle(1, C.purple, 0.35)
      g.lineBetween(12, 18 + ri * 6, 20, 18 + ri * 6)
    }

    g.generateTexture('decor_node', W, H)
    g.destroy()
  }
}
