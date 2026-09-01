
# Battle history

Battle history used to live only in the running process, so a restart made every past run disappear from the interface even though the rows were still in the database. History is now served from Postgres through `GET /api/battles/history/dates` and `GET /api/battles/history`, and the drawer reads from those.

![The battles drawer with a month calendar](../../picture/21-battle-history-calendar.png)

## Why a calendar

The first version of this was a flat list of dates. That works for a week and falls apart after a month of experiments, so the drawer opens on a month grid instead.

A tinted cell is a day that has battles. The hairline under a date carries the day's red-blue split, so you can see at a glance which days went which way without opening anything. Days with nothing recorded are not clickable, and the current day is outlined. The arrows move between months.

Under the grid, the `LIVE` row and a completion count for the selected day. The capture reads `132/134 complete`, meaning two of that day's battles did not finish. That count is the backend's answer, not the drawer's guess, so an interrupted sweep is visible without going near the database.

## The day list

Selecting a day lists its battles newest first, each with the session id, the start time, the score, the rounds completed against the rounds configured, and the status. The button on each row opens that battle's report.

Clicking a row for a battle that is still running re-attaches the visualizer to it. The per-session event stream replays its history, so the scene, the transcripts and the score come back as they were, and the inline pause, resume and stop controls work from there. A battle that kept running after a browser refresh is always recoverable.

## One thing that had to be fixed

Because the drawer floats over a Phaser canvas, clicking a date used to fall through to whatever was underneath and open an unrelated panel. Three changes fixed it: disabled dates are marked `aria-disabled` rather than `disabled`, so they still exist as hit targets and can swallow the click; the drawer stops propagation at its own boundary; and the scene disables its own input while the drawer is open. If you are extending the drawer, keep all three.
