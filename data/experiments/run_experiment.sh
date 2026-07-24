#!/usr/bin/env bash
# ACEA paper experiment harness.
# Runs 3 configs x N battles (deepteam vs AdaptiveGuard), records per-run stats.
# Sequential (shared code-improver / target). Robust to per-battle timeout/error.
set -u

RED=8ab0b24d
BLUE=90733907
ROUNDS=2
N=${N:-100}
PER_BATTLE_TIMEOUT=${PER_BATTLE_TIMEOUT:-900}   # seconds
ARENA=http://localhost:8800
REPORT=http://localhost:8005
OUTDIR=/Users/yiting.shen/Documents/project/ACEA/data/experiments
RESULTS=$OUTDIR/results.jsonl
PROGRESS=$OUTDIR/progress.log

echo "=== experiment start $(date '+%F %T') N=$N rounds=$ROUNDS ===" >> "$PROGRESS"

run_one() {
  local config=$1 inner=$2 outer=$3 idx=$4
  local t0 t1 wall sid st i
  t0=$(python3 -c 'import time;print(time.time())')
  sid=$(curl -s -m 20 -X POST "$ARENA/api/battles" -H 'Content-Type: application/json' \
    -d "{\"red_service_id\":\"$RED\",\"blue_service_id\":\"$BLUE\",\"max_rounds\":$ROUNDS,\"inner_loop_enabled\":$inner,\"outer_loop_enabled\":$outer}" \
    2>/dev/null | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('session_id',''))
except: print('')" 2>/dev/null)

  if [ -z "$sid" ]; then
    echo "{\"config\":\"$config\",\"idx\":$idx,\"status\":\"launch_failed\",\"session_id\":null}" >> "$RESULTS"
    echo "$(date '+%T') [$config] run $idx LAUNCH_FAILED" >> "$PROGRESS"
    return
  fi

  st=running; i=0
  local maxpolls=$(( PER_BATTLE_TIMEOUT / 5 ))
  until [ $i -ge $maxpolls ]; do
    st=$(curl -s -m 8 "$ARENA/api/battles/$sid" 2>/dev/null | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('status','?'))
except: print('?')" 2>/dev/null)
    case "$st" in complete|stopped|error) break;; esac
    i=$((i+1)); sleep 5
  done
  # Timed out -> stop it so it doesn't linger
  if [ "$st" != "complete" ] && [ "$st" != "stopped" ] && [ "$st" != "error" ]; then
    curl -s -m 8 -X POST "$ARENA/api/battles/$sid/stop" >/dev/null 2>&1
    st=timeout
  fi
  t1=$(python3 -c 'import time;print(time.time())')
  wall=$(python3 -c "print(round($t1-$t0,1))")

  # Pull stats + asis from report-composer (retry briefly; report writes async)
  python3 - "$sid" "$config" "$idx" "$st" "$wall" "$REPORT" "$RESULTS" <<'PY'
import sys, json, time, urllib.request
sid, config, idx, st, wall, report, results = sys.argv[1:8]
def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)
    except Exception:
        return None
d = None
for _ in range(6):
    d = fetch(f"{report}/v1/reports/{sid}")
    if d and d.get("statistics"): break
    time.sleep(2)
rec = {"config": config, "idx": int(idx), "session_id": sid, "status": st, "wall_s": float(wall)}
if d:
    s = d.get("statistics", {}) or {}
    rec.update({
        "total_rounds": s.get("total_rounds"),
        "red_wins": s.get("red_wins"), "blue_wins": s.get("blue_wins"),
        "asr": s.get("attack_success_rate"), "dr": s.get("defense_rate"),
        "avg_harm": s.get("avg_harmfulness_score"),
        "red_pss": s.get("red_pss"), "blue_pss": s.get("blue_pss"),
    })
    ev = d.get("asis_evolution") or {}
    for team in ("red", "blue"):
        t = ev.get(team) or {}
        rec[f"{team}_improved"] = t.get("improved")
        rec[f"{team}_rolled_back"] = t.get("rolled_back_count")
        rec[f"{team}_promoted"] = t.get("promoted_count")
        rec[f"{team}_active_gen"] = t.get("active_gen")
        rec[f"{team}_baseline_pss"] = t.get("baseline_pss")
        rec[f"{team}_active_pss"] = t.get("active_pss")
else:
    rec["report_missing"] = True
with open(results, "a") as f:
    f.write(json.dumps(rec) + "\n")
PY
  echo "$(date '+%T') [$config] run $idx status=$st wall=${wall}s sid=${sid:0:8}" >> "$PROGRESS"
}

for r in $(seq 1 "$N"); do run_one inner  true  false "$r"; done
for r in $(seq 1 "$N"); do run_one outer  false true  "$r"; done
for r in $(seq 1 "$N"); do run_one both   true  true  "$r"; done

echo "=== experiment DONE $(date '+%F %T') ===" >> "$PROGRESS"
