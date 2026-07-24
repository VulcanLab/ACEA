#!/usr/bin/env python3
"""
Usage: python run_experiment.py --config config/exp1_asr_vs_rounds.json
Runs a multi-seed battle experiment and saves per-seed results to output_dir.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
import httpx

ARENA_URL = os.environ.get("ARENA_URL", "http://localhost:8800")
REPORT_URL = os.environ.get("REPORT_URL", "http://localhost:8005")


async def run_one_battle(client: httpx.AsyncClient, config: dict, seed: int) -> dict:
    battle_cfg = {**config["battle_config"], "seed": seed}
    r = await client.post(f"{ARENA_URL}/api/battles", json=battle_cfg)
    r.raise_for_status()
    session_id = r.json()["session_id"]
    print(f"  Started session {session_id} (seed={seed})")

    while True:
        await asyncio.sleep(3)
        r2 = await client.get(f"{ARENA_URL}/api/battles/{session_id}")
        state = r2.json()
        status = state.get("status", "")
        current_round = state.get("current_round", 0)
        max_rounds = state.get("max_rounds", "?")
        if status in ("complete", "error"):
            break
        print(f"    Round {current_round}/{max_rounds} ...")

    r3 = await client.get(f"{REPORT_URL}/v1/reports/{session_id}")
    report = r3.json()
    return {"seed": seed, "session_id": session_id, "report": report}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to experiment config JSON")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    print(f"Running experiment: {config['name']}")
    print(f"Seeds: {config['seeds']}")
    out_dir = Path(config["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    async with httpx.AsyncClient(timeout=300.0) as client:
        for seed in config["seeds"]:
            print(f"\nSeed {seed}...")
            try:
                result = await run_one_battle(client, config, seed)
                results.append(result)
                out_file = out_dir / f"seed_{seed}.json"
                out_file.write_text(json.dumps(result, indent=2))
                stats = result["report"].get("statistics", {})
                asr = stats.get("attack_success_rate", 0)
                print(f"  Done. ASR={asr:.2%}")
            except Exception as exc:
                print(f"  ERROR: {exc}")

    if not results:
        print("No results collected.")
        return

    summary = {
        "experiment": config["name"],
        "n_seeds": len(results),
        "mean_asr": sum(r["report"]["statistics"]["attack_success_rate"] for r in results) / len(results),
        "mean_dr": sum(r["report"]["statistics"]["defense_rate"] for r in results) / len(results),
        "mean_harm": sum(r["report"]["statistics"]["avg_harmfulness_score"] for r in results) / len(results),
        "sessions": [r["session_id"] for r in results],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSummary: mean_asr={summary['mean_asr']:.2%}, mean_dr={summary['mean_dr']:.2%}")
    print(f"Results saved to {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
