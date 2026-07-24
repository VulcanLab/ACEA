#!/usr/bin/env python3
"""
Usage: python analyze_results.py --dir results/exp1
Prints per-round ASR, DR, and harm score stats across seeds.
"""
import argparse
import json
from pathlib import Path
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Directory containing seed_*.json result files")
    args = parser.parse_args()

    result_dir = Path(args.dir)
    seed_files = sorted(result_dir.glob("seed_*.json"))
    if not seed_files:
        print(f"No seed result files found in {result_dir}")
        return

    print(f"Analyzing {len(seed_files)} seed(s) from {result_dir}/")

    round_data: dict[int, list[dict]] = defaultdict(list)
    for f in seed_files:
        data = json.loads(f.read_text())
        for r in data["report"]["rounds"]:
            round_data[r["round"]].append(r)

    print(f"\n{'Round':>6}  {'Seeds':>5}  {'ASR':>6}  {'DR':>6}  {'Avg Harm':>9}")
    print("-" * 38)
    for rn in sorted(round_data.keys()):
        rounds = round_data[rn]
        asr = sum(1 for r in rounds if r.get("red_success")) / len(rounds)
        dr = sum(1 for r in rounds if r.get("blue_success")) / len(rounds)
        harm = sum((r.get("harmfulness_score") or 0) for r in rounds) / len(rounds)
        print(f"{rn:>6}  {len(rounds):>5}  {asr:>6.1%}  {dr:>6.1%}  {harm:>9.4f}")

    # Overall summary
    summary_file = result_dir / "summary.json"
    if summary_file.exists():
        summary = json.loads(summary_file.read_text())
        print(f"\nOverall: mean_asr={summary['mean_asr']:.2%}, mean_dr={summary['mean_dr']:.2%}, mean_harm={summary['mean_harm']:.4f}")


if __name__ == "__main__":
    main()
