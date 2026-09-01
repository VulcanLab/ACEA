
# Arena layout

The visualization at `:3030` is one screen, split into five fixed zones. Nothing in it is decorative. Every avatar, banner and console is driven by an event the backend actually published, and when the backend has nothing to say the screen stays quiet.

![The full operations room during a live battle](../../picture/01-arena-overview-live-battle.png)

Reading left to right: the red zone, the target's zone in the middle, the blue zone, and along the bottom the judge's desk and the reporter's desk. The header carries the mission id, the model roster, the live round counter and the score.

## The five zones

| Zone | Who works there |
|---|---|
| Red, left | The connected red project's fighter, plus the platform's three red-side helpers |
| Centre | Target-AI, its mainscreen, and the ground both fighters meet on |
| Blue, right | The connected blue project's fighter, plus the platform's three blue-side helpers |
| Judge, bottom left | The arbiter and its verdict console |
| Reporter, bottom right | The scribe, its report status panel, and the printer |

## Fighters and helpers are not the same thing

Each side has exactly one fighter. That avatar stands for the connected project itself: one protocol channel, however many models the project runs internally. The fighter is what walks to centre and carries out the round.

The three other avatars per side are the platform's own assisting agents, and they belong to the platform rather than to your project. Red has a Recon Analyst, a Strategy Analyzer and an Attack Rewriter; blue has a Recon Analyst, a Strategy Analyzer and a Defense Enhancer. They only do visible work when the inner loop is switched on for the battle.

![Red-side helpers at their consoles](../../picture/06-red-zone-agents-acting.png)

Each panel shows the role, its `ATK` or `DEF` badge, its current state, and one line of what it is doing. `IDLE` with "standing by" underneath means exactly that: the loop is off, or this round did not need that helper.

![Blue-side helpers reporting a blocked attack](../../picture/08-blue-zone-threat-neutralized.png)

The blue side reads the same way. "Threat neutralized" above a helper is that helper's own report of the round, and the intent score beside it is the classifier output the defending project returned.

## Movement has a reason attached

An early version let idle agents wander at random inside their zone. On screen that read as jitter, and worse, it implied activity that was not happening. Idle movement is now an errand: a destination plus a short reason, defined per role in `ghost-signal/src/lib/sceneConfig.ts`.

![Agents walking to specific workstations](../../picture/19-agents-at-workstations.png)

An attacker walks to its console to read the last verdict, to the bench to draft a payload, or up to the boundary it probes. A defender walks to the input gate, the rule board, or the output side. The judge walks between the transcript, the rubric and the place it records a verdict. Nobody walks through the target's station, and nobody teleports: every move is a tween, so you can always see who went where.

Some errands are triggered by the run rather than by idleness. When the backend publishes `red.attack.sent`, the red fighter stages the next attempt and the recon helper goes to read the verdict. On `battle.complete`, the reporter starts its tour. That mapping lives in `ERRAND_ON_EVENT` in the same file.

## The system state panel

The system state overlay is the platform's own view of who is on the field. It names the mission, counts the agents in the scene, says how many are active right now, and reports whether the event stream is live.

![System state and the agent registry](../../picture/03-ai-core-system-state.png)

Underneath, the agent registry lists every avatar with its last state: `IDLE`, `THINKING`, `SUCCESS` or `FAILED`. The capture above was taken before a battle started, so all eleven are `IDLE` and the active count is zero, while the stream already reads `LIVE`. Press `ESC` to close it.

This is the panel to open first when the scene looks stuck. An agent parked on `FAILED` while the round counter keeps moving usually means that role's model call is erroring, which the model health panel will confirm.
