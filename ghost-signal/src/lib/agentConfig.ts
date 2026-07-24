import type { AgentData } from '@/types'

// The per-team sprites are the platform's SELF-IMPROVEMENT AIs that assist the
// plugged-in red/blue projects (NOT the project's own internal models). Each
// team gets a Strategy Analyzer + a Rewriter/Enhancer (the evolution wrapper's
// two roles). Labels + models are refreshed at runtime from arena-core's
// /api/agents/roster so the display reflects the real collaborating AIs.
// IDs stay fixed (atk1/atk2/def1/def2) because combat + WS events key on them.
export const INITIAL_AGENTS: AgentData[] = [
  {
    id: 'atk1',
    role: 'attacker',
    zone: 'red',
    state: 'idle',
    label: 'Strategy Analyzer',
    model: '',
    message: 'Standing by...',
    color: '#ff4444',
  },
  {
    id: 'atk2',
    role: 'attacker',
    zone: 'red',
    state: 'idle',
    label: 'Attack Rewriter',
    model: '',
    message: 'Standing by...',
    color: '#ff8800',
  },
  {
    id: 'atk3',
    role: 'attacker',
    zone: 'red',
    state: 'idle',
    label: 'Recon Analyst',
    model: '',
    message: 'Analyzing target project...',
    color: '#ffaa33',
  },
  {
    id: 'def1',
    role: 'defender',
    zone: 'blue',
    state: 'idle',
    label: 'Strategy Analyzer',
    model: '',
    message: 'Monitoring...',
    color: '#4488ff',
  },
  {
    id: 'def2',
    role: 'defender',
    zone: 'blue',
    state: 'idle',
    label: 'Defense Enhancer',
    model: '',
    message: 'Monitoring...',
    color: '#00ccff',
  },
  {
    id: 'def3',
    role: 'defender',
    zone: 'blue',
    state: 'idle',
    label: 'Recon Analyst',
    model: '',
    message: 'Analyzing target project...',
    color: '#33ddff',
  },
  {
    // The connected external RED project itself — the single protocol channel
    // through which it competes. This is the fighter that carries out combat;
    // the atk* agents above are the platform's assisting models, not fighters.
    id: 'redFighter',
    role: 'attacker',
    zone: 'red',
    state: 'idle',
    label: 'Red Project',
    model: '',
    message: 'Connected.',
    color: '#ff3355',
  },
  {
    // The connected external BLUE project itself (see redFighter).
    id: 'blueFighter',
    role: 'defender',
    zone: 'blue',
    state: 'idle',
    label: 'Blue Project',
    model: '',
    message: 'Connected.',
    color: '#3388ff',
  },
  {
    id: 'victim',
    role: 'victim',
    zone: 'center',
    state: 'idle',
    label: 'TARGET-AI',
    model: '',
    message: 'Online.',
    color: '#cc44ff',
  },
  {
    id: 'judge',
    role: 'judge',
    zone: 'judge',
    state: 'idle',
    label: 'Arbiter',
    model: '',
    message: 'Awaiting verdict...',
    color: '#ffdd00',
  },
  {
    id: 'reporter',
    role: 'reporter',
    zone: 'reporter',
    state: 'idle',
    label: 'Scribe',
    model: '',
    message: 'Idle.',
    color: '#44ff88',
  },
]
