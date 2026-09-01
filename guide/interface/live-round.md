
# A round on screen

A round always runs in the same order, and the stage indicator at the top names the phase it is in: RECON, ATTACK, DEFENSE, TARGET, JUDGE, ROUND, COMPLETE. The backend races ahead of the animation, so each phase is held back until the previous one has fully resolved on screen. Phases never overlap on screen even when they overlap in time.

## The exchange itself

The target's mainscreen is the transcript of the round as the target saw it.

![The target mainscreen during an exchange](../../picture/10-target-mainscreen-exchange.png)

Each line is prefixed by who produced it: the payload that arrived, the defending project's intent classification with its score and threshold, the target's own reply, and then what the platform did with all of that. The event count at the bottom is clickable and opens the full stream for the round.

## Who stopped it, in the moment

The centre banner names the outcome, and it is resolved from the judge's real verdict rather than from whichever animation finished last. The banner can never disagree with the judge console.

![A defended round at close range](../../picture/12-target-zone-defended-closeup.png)

`DEFENDED` here sits above a mainscreen line reading "BLOCKED BY BLUE INPUT GATE — attack stopped, not de...". That distinction matters enough that the report gives it a section of its own: an attack stopped at the defending project's input gate is not the same result as a target that declined on its own. See [../scoring/defense-attribution.md](../scoring/defense-attribution.md).

![The round outcome banner](../../picture/02-round-outcome-target-secured.png)

The same outcome across the whole room. Speech above an avatar is that role's own text for the round, not a caption the frontend invented: red announces a breach it did not get, the judge says the defense held, and the two disagree because they are each reporting their own view, not the result.

![The centre of the room at a round boundary](../../picture/04-combat-zoom-round-defended.png)

Long runs look like this. The round counter in the header keeps climbing, the score updates per round, and the scene stays readable because the layout never moves.

## After the last round

When the configured round count is reached, or when you stop the battle by hand, the header switches to `COMPLETE` and the reporter starts work.

![Battle complete, report compiling](../../picture/07-battle-complete-compiling-report.png)

![The reporter walking its route](../../picture/17-reporter-errand-on-the-move.png)

The reporter's tour is not filler. It walks the red zone, the target's station, the blue zone and the judge's desk in that order, because that is the order it collects material in, and then it walks to the printer. The label above it says which leg it is on. The report status panel at the bottom right tracks the same work in text, and turns into a link when the report is ready.

There is no automatic saturation stop. A battle ends when the round count you set is reached or when you stop it, and the report says which of the two happened.
