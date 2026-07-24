#!/usr/bin/env bash
# ACEA paper experiment v3 (verifier + best-of-N + canary-tolerant) — one 100-ROUND battle per config, DEFAULT red/blue.
# Configs: both (inner+outer), inner-only, outer-only.
# Resume-capable: a config whose report_<config>.json is already 'complete' is skipped.
# Captures the platform's full report.json (per-round trajectory + asis_evolution)
# per config for downstream analysis. Run under caffeinate to survive sleep.
set -u

RED=8ab0b24d
BLUE=90733907
ROUNDS=100
ARENA=http://localhost:8800
REPORT=http://localhost:8005
OUT=/Users/yiting.shen/Documents/project/ACEA/data/experiments/v3
PROG=$OUT/progress_100r.log

ts() { date '+%F %T'; }
log() { echo "$(ts) $*" >> "$PROG"; }

log "=== experiment v3 (verifier + best-of-N + canary-tolerant) start (100-round, default red/blue, 3x10 benchmark) ==="

run_cfg() {
  local cfg=$1 inner=$2 outer=$3
  local rpt="$OUT/report_${cfg}.json"

  # Resume: skip if this config already completed.
  if [ -f "$rpt" ] && python3 -c "import json,sys; d=json.load(open('$rpt')); sys.exit(0 if d.get('status')=='complete' else 1)" 2>/dev/null; then
    log "[$cfg] already complete — skip"
    return
  fi

  # Admission gate: only launch when the platform says it can.
  local can
  can=$(curl -s -m 15 "$ARENA/api/battle-readiness" 2>/dev/null | python3 -c "import sys,json
try: print('yes' if json.load(sys.stdin)['verdict']['can_launch'] else 'no')
except: print('no')" 2>/dev/null)
  if [ "$can" != "yes" ]; then
    log "[$cfg] NOT admitted (can_launch=$can) — skipping"
    return
  fi

  local t0 sid
  t0=$(python3 -c 'import time;print(time.time())')
  sid=$(curl -s -m 25 -X POST "$ARENA/api/battles" -H 'Content-Type: application/json' \
    -d "{\"red_service_id\":\"$RED\",\"blue_service_id\":\"$BLUE\",\"max_rounds\":$ROUNDS,\"inner_loop_enabled\":$inner,\"outer_loop_enabled\":$outer}" \
    2>/dev/null | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('session_id',''))
except: print('')" 2>/dev/null)
  if [ -z "$sid" ]; then log "[$cfg] LAUNCH FAILED"; return; fi
  log "[$cfg] launched sid=$sid rounds=$ROUNDS inner=$inner outer=$outer"

  # Poll to terminal — patient (100 rounds x per-round improve can be many hours).
  # Cap ~20h so a hung battle can't block forever; caffeinate keeps the host awake.
  local st=running i=0 maxpolls=2400   # 2400 * 30s = 20h
  until [ $i -ge $maxpolls ]; do
    st=$(curl -s -m 10 "$ARENA/api/battles/$sid" 2>/dev/null | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('status','?'))
except: print('?')" 2>/dev/null)
    case "$st" in complete|stopped|error) break;; esac
    # progress heartbeat every ~10 polls
    if [ $((i % 10)) -eq 0 ]; then
      r=$(curl -s -m 10 "$ARENA/api/battles/$sid" 2>/dev/null | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print('r%s red%s blue%s'%(d['current_round'],d['red_wins'],d['blue_wins']))
except: print('?')" 2>/dev/null)
      log "[$cfg] ...$st $r"
    fi
    i=$((i+1)); sleep 30
  done

  local wall; wall=$(python3 -c "print(round($(python3 -c 'import time;print(time.time())')-$t0,1))")

  # Fetch + persist the platform's full report.json (per-round trajectory + asis).
  python3 - "$sid" "$cfg" "$st" "$wall" "$REPORT" "$rpt" <<'PY'
import sys, json, time, urllib.request
sid, cfg, st, wall, report, rpt = sys.argv[1:7]
def fetch(u):
    try:
        with urllib.request.urlopen(u, timeout=15) as r: return json.load(r)
    except Exception: return None
d=None
for _ in range(8):
    d=fetch(f"{report}/v1/reports/{sid}")
    if d and d.get("rounds") is not None: break
    time.sleep(3)
if d is None: d={}
d["_experiment"]={"config":cfg,"session_id":sid,"final_status":st,"wall_s":float(wall),"max_rounds":100}
if "status" not in d: d["status"]=st
json.dump(d, open(rpt,"w"), indent=1)
PY
  log "[$cfg] DONE status=$st wall=${wall}s → report_${cfg}.json"
}

run_cfg inner true  false
run_cfg outer false true
run_cfg both  true  true

log "=== experiment v3 (verifier + best-of-N + canary-tolerant) DONE ==="
