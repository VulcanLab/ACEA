import type { AgentEvent, AgentState } from '@/types'

type Listener = (event: AgentEvent) => void

const ATK_MSG: Record<AgentState, string[]> = {
  idle:      ['Standing by...', 'Awaiting orders.', 'System nominal.'],
  thinking:  ['Analyzing target...', 'Scanning vectors...', 'Probing defenses...'],
  acting:    ['Injecting payload!', 'Exploit initiated.', 'Bypass running...'],
  success:   ['Breach confirmed.', 'Payload delivered!', 'Access granted.'],
  failed:    ['Blocked by firewall.', 'Intrusion detected!', 'Connection refused.'],
  moving:    ['Relocating...', 'Changing position...'],
  gathering: ['Collecting data...', 'Scanning logs...'],
  writing:   ['Drafting report...', 'Logging findings...'],
  printing:  ['Printing report...'],
}

const DEF_MSG: Record<AgentState, string[]> = {
  idle:      ['Monitoring traffic.', 'Systems green.', 'All quiet.'],
  thinking:  ['Anomaly detected.', 'Tracing source...', 'Checking sigs...'],
  acting:    ['Deploying firewall!', 'Blocking pattern.', 'Patching vuln.'],
  success:   ['Threat neutralized.', 'Attack repelled!', 'System secured.'],
  failed:    ['Breach detected!', 'Cannot contain—', 'Firewall bypassed.'],
  moving:    ['Repositioning...'],
  gathering: ['Collecting telemetry...'],
  writing:   ['Logging incident...'],
  printing:  ['Printing alert...'],
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]
}

function msgFor(agentId: string, state: AgentState): string {
  const bank = agentId.startsWith('atk') ? ATK_MSG : DEF_MSG
  return pick(bank[state] ?? ['...'])
}

const VERDICT_REASONS = [
  'Payload bypassed content filter.',
  'Response contained restricted data.',
  'Jailbreak vector unsuccessful.',
  'Model refused all injection attempts.',
  'Indirect prompt injection succeeded.',
  'Defense held — no policy violation.',
]

const REPORT_SNIPPETS = [
  `## Incident Report\n\n**Mission:** Prompt injection test\n**Target:** GPT-5-chat\n**Outcome:** Partial breach\n\n### Findings\n- Filter bypass via role-play framing\n- 3 of 5 attempts succeeded\n\n### Recommendation\nTighten system prompt guards.`,
  `## Security Audit\n\n**Test vector:** Indirect injection\n**Result:** Defended\n\n### Summary\nBlue team response time: 1.2s\nAll payloads logged and blocked.`,
]

export class MockEventBus {
  private listeners: Listener[] = []
  private timer: ReturnType<typeof setTimeout> | null = null
  private running = false

  on(fn: Listener): () => void {
    this.listeners.push(fn)
    return () => {
      this.listeners = this.listeners.filter(l => l !== fn)
    }
  }

  private emit(event: AgentEvent): void {
    this.listeners.forEach(fn => fn(event))
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.schedule()
  }

  stop(): void {
    this.running = false
    if (this.timer) clearTimeout(this.timer)
  }

  private schedule(): void {
    if (!this.running) return
    const delay = 1800 + Math.random() * 2200
    this.timer = setTimeout(() => {
      this.fireRandom()
      this.schedule()
    }, delay)
  }

  private fireRandom(): void {
    const roll = Math.random()

    if (roll < 0.50) {
      const agents = ['atk1', 'atk2', 'def1', 'def2'] as const
      const states: AgentState[] = ['thinking', 'acting', 'success', 'failed', 'idle']
      const agentId = pick([...agents])
      const state = pick(states)
      this.emit({ type: 'state_change', agentId, state, message: msgFor(agentId, state) })

    } else if (roll < 0.65) {
      const result = Math.random() > 0.5 ? 'success' : ('failed' as const)
      this.emit({
        type: 'judge_verdict',
        score: Math.round((5 + Math.random() * 5) * 10) / 10,
        result,
        reason: pick(VERDICT_REASONS),
      })

    } else if (roll < 0.78) {
      const state: AgentState = pick(['thinking', 'acting', 'success', 'failed', 'idle'])
      this.emit({
        type: 'state_change',
        agentId: 'victim',
        state,
        message: state === 'failed' ? 'BREACH DETECTED' : state === 'success' ? 'DEFENDED' : 'Processing...',
      })

    } else if (roll < 0.83) {
      // Reporter collects data from each zone then prints a mock report (mock-mode only)
      this.emit({ type: 'reporter_move', destination: 'red' })
      setTimeout(() => {
        const content = pick(REPORT_SNIPPETS)
        this.emit({ type: 'print_report', content })
      }, 9000)

    } else if (roll < 0.98) {
      // Combat encounter — 40% chance of sending both agents per side (2v2)
      const fullSquad = Math.random() < 0.4
      this.emit({
        type: 'combat_start',
        attackerIds: fullSquad ? ['atk1', 'atk2'] : [pick(['atk1', 'atk2'])],
        defenderIds: fullSquad ? ['def1', 'def2'] : [pick(['def1', 'def2'])],
        target: 'TARGET-AI',
      })

    } else {
      this.emit({
        type: 'system_alert',
        message: pick([
          'Anomalous token pattern detected.',
          'Rate limit approaching threshold.',
          'New payload variant identified.',
          'Judge confidence: HIGH',
          'Session entropy elevated.',
        ]),
      })
    }
  }
}

export const mockBus = new MockEventBus()
