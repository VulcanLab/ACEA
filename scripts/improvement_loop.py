#!/usr/bin/env python3
"""
End-to-end ASIS uplift verification against external adapters.

Flow:
  1. Take baseline benchmark — run N-round battle with original code.
  2. Trigger ASIS improvement on the losing team.
  3. Wait for ASIS to complete its pipeline (LLM agent edits, docker
     rebuild, canary, benchmark, promote / rollback).
  4. Take post-improvement benchmark — same N rounds.
  5. Report ASR/DR delta + show the cumulative gen_0 -> active-gen diff.

Pass: ASIS produced ANY gen_N row in adapter_generations + benchmark
attempted. (Whether ASR improved or rolled back is signal, not failure.)
"""
import asyncio
import json
import os
import subprocess
import sys
import time

import httpx

ARENA  = os.getenv("ARENA_CORE_URL", "http://localhost:8800")
ASIS   = os.getenv("ASIS_URL",       "http://localhost:8010")
REPORT = os.getenv("REPORT_URL",     "http://localhost:8005")
ROUNDS = int(os.getenv("BENCH_ROUNDS", "5"))


async def list_services(c: httpx.AsyncClient) -> list[dict]:
    r = await c.get(f"{ARENA}/api/services", timeout=10.0)
    return r.json()


async def run_battle(c: httpx.AsyncClient, red_sid: str, blue_sid: str,
                     max_rounds: int = ROUNDS, **extra) -> dict:
    body = {"red_service_id": red_sid, "blue_service_id": blue_sid,
            "max_rounds": max_rounds, **extra}
    r = await c.post(f"{ARENA}/api/battles", json=body, timeout=30.0)
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"    battle session: {sid[:8]}")
    while True:
        await asyncio.sleep(5.0)
        s = (await c.get(f"{ARENA}/api/battles/{sid}", timeout=10.0)).json()
        if s.get("status") in ("complete", "error", "stopped"):
            break
    s = (await c.get(f"{ARENA}/api/battles/{sid}", timeout=10.0)).json()
    return s


def fmt_battle(s: dict) -> str:
    total = (s.get("red_wins", 0) or 0) + (s.get("blue_wins", 0) or 0)
    asr = (s["red_wins"] / total) if total else 0.0
    dr  = (s["blue_wins"] / total) if total else 0.0
    return f"red={s.get('red_wins',0)} blue={s.get('blue_wins',0)} rounds={total} ASR={asr:.0%} DR={dr:.0%}"


async def get_generations(c: httpx.AsyncClient, adapter_id: str) -> list[dict]:
    r = await c.get(f"{ASIS}/v1/generations/{adapter_id}", timeout=10.0)
    return r.json().get("generations", []) if r.status_code == 200 else []


async def wait_for_new_gen(c: httpx.AsyncClient, adapter_id: str,
                            baseline_count: int, timeout_s: int = 1200) -> dict | None:
    """Poll adapter_generations until a NEW non-gen-0 row appears."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        gens = await get_generations(c, adapter_id)
        non_zero = [g for g in gens if g.get("gen_number", 0) > 0]
        if len(gens) > baseline_count:
            # Find the latest gen (highest gen_number, most recent created_at)
            latest = sorted(gens, key=lambda g: (g["gen_number"], g.get("created_at", "")))[-1]
            return latest
        await asyncio.sleep(10.0)
    return None


def red_team_metadata(host_path: str, container_path: str) -> dict[str, str]:
    """Return git-info about the project so the report can identify it
    without leaking the project name into code."""
    info = {}
    try:
        cwd = host_path
        info["files_changed_in_volume"] = subprocess.check_output(
            ["git", "-C", cwd, "diff", "--stat"], stderr=subprocess.DEVNULL,
            text=True, timeout=5.0,
        ).strip()
    except Exception:
        pass
    return info


async def main() -> int:
    print("=== ASIS uplift verification (external adapter pair) ===\n")
    async with httpx.AsyncClient() as c:
        # 0. Pick adapter pair
        services = await list_services(c)
        # Prefer Evolved wrappers (they trigger ASIS via improvement.triggered);
        # otherwise fall back to raw red/blue. Evolved wrappers carry the
        # downstream adapter's behaviour via DOWNSTREAM_URL.
        evo_red  = next((s for s in services if s["type"] == "red"
                         and s.get("capabilities", {}).get("evolution_wrapper")), None)
        evo_blue = next((s for s in services if s["type"] == "blue"
                         and s.get("capabilities", {}).get("evolution_wrapper")), None)
        raw_red  = next((s for s in services if s["type"] == "red"
                         and not s.get("capabilities", {}).get("evolution_wrapper")), None)
        raw_blue = next((s for s in services if s["type"] == "blue"
                         and not s.get("capabilities", {}).get("evolution_wrapper")), None)

        red_svc  = evo_red  or raw_red
        blue_svc = evo_blue or raw_blue
        if not red_svc or not blue_svc:
            print("ERROR: no registered red+blue pair")
            return 1
        print(f"  red:  {red_svc['name']:35} ({red_svc['id']})")
        print(f"  blue: {blue_svc['name']:35} ({blue_svc['id']})\n")

        # 1. Baseline benchmark
        print(f"[1/4] Baseline benchmark ({ROUNDS} rounds, original code)")
        baseline = await run_battle(c, red_svc["id"], blue_svc["id"])
        baseline_str = fmt_battle(baseline)
        print(f"    {baseline_str}\n")

        # ASIS improves the LOSING side. Determine which adapter_id ASIS will
        # touch — it uses the evolution wrapper's service_id as adapter_id.
        red_total = (baseline.get("red_wins", 0) or 0)
        blue_total = (baseline.get("blue_wins", 0) or 0)
        n = red_total + blue_total
        asr = red_total / max(n, 1)
        dr  = blue_total / max(n, 1)
        loser = "red" if asr < 0.5 else ("blue" if dr < 0.5 else None)
        if loser is None:
            print(f"  [skip] no clear loser (ASR={asr:.0%} DR={dr:.0%}) — ASIS won't trigger")
            return 0
        loser_adapter_id = red_svc["id"] if loser == "red" else blue_svc["id"]
        print(f"    loser={loser} -> ASIS target adapter={loser_adapter_id}\n")

        # 2. Capture gen history baseline
        before_gens = await get_generations(c, loser_adapter_id)
        baseline_count = len(before_gens)
        print(f"[2/4] gen history before ASIS: {baseline_count} rows")

        # 3. Wait for ASIS to write a new row
        print(f"[3/4] Waiting for ASIS pipeline (LLM agent + docker rebuild + benchmark) ...")
        new_gen = await wait_for_new_gen(c, loser_adapter_id, baseline_count, timeout_s=1500)
        if not new_gen:
            print(f"    [FAIL] ASIS produced no new generation in 25 min")
            return 1
        is_active = new_gen.get("is_active")
        rb = (new_gen.get("rollback_reason") or "").strip()
        status = "PROMOTED" if is_active else ("ROLLED BACK" if rb else "in flight")
        print(f"    [OK] gen_{new_gen['gen_number']} {status}")
        if new_gen.get("benchmark_asr") is not None:
            print(f"    benchmark_asr={new_gen['benchmark_asr']*100:.1f}%  "
                  f"benchmark_dr={new_gen.get('benchmark_dr',0)*100:.1f}%")
        if rb:
            print(f"    rollback_reason: {rb[:120]}")

        # 4. Post-improvement benchmark — replay same setup, see actual delta
        print(f"\n[4/4] Post-improvement benchmark ({ROUNDS} rounds, gen_N code)")
        post = await run_battle(c, red_svc["id"], blue_svc["id"])
        post_str = fmt_battle(post)
        print(f"    {post_str}\n")

        # Delta
        def asr_of(s: dict) -> float:
            t = (s.get("red_wins",0) or 0) + (s.get("blue_wins",0) or 0)
            return (s.get("red_wins",0) or 0) / max(t, 1)
        delta_asr = (asr_of(post) - asr_of(baseline)) * 100.0
        delta_dr  = ((post.get("blue_wins",0) or 0) / max(
                      (post.get("red_wins",0) or 0) + (post.get("blue_wins",0) or 0), 1)
                    - (baseline.get("blue_wins",0) or 0) / max(
                      (baseline.get("red_wins",0) or 0) + (baseline.get("blue_wins",0) or 0), 1)
                    ) * 100.0
        print("=== Uplift summary ===")
        print(f"  Baseline:    {baseline_str}")
        print(f"  After ASIS:  {post_str}")
        sign = "+" if delta_asr >= 0 else ""
        print(f"  ASR delta:   {sign}{delta_asr:.1f} pp")
        sign = "+" if delta_dr >= 0 else ""
        print(f"  DR  delta:   {sign}{delta_dr:.1f} pp")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
