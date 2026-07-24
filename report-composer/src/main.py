"""
Report Composer — battle analytics service.

Reads completed battle data from PostgreSQL and returns structured JSON reports
with per-round traces and aggregate statistics (ASR, DR, harm scores).
"""
import litellm_safe  # noqa: F401  — monkey-patch Gemini safety_settings

from contextlib import asynccontextmanager
import html as html_module
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from http_cors_extra import PrivateNetworkAclMiddleware
from config import settings

from session_stats import merge_trace_statistics_with_session

# How many consecutive failed improvement attempts on top of the current best
# count as "saturated" (plateaued — no further capability gain possible).
_SATURATION_ATTEMPTS = 3

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.postgres_uri, min_size=2, max_size=5)
    return _pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    # Ensure the narrative cache carries a code-version stamp so reports
    # regenerate automatically after a report-code change (no more stale cache).
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE narrative_cache "
                "ADD COLUMN IF NOT EXISTS narrative_version TEXT DEFAULT ''"
            )
    except Exception:
        pass
    yield
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


app = FastAPI(title="Report Composer", version="0.2.0", lifespan=lifespan)

# Middleware stacking note (Starlette inserts at index-0 and reverses on build):
#   last add_middleware call → outermost layer (handles request first).
# Correct order: CORSMiddleware outermost so it wraps ALL responses —
# including PNA preflight replies — with ACAO headers.
# PrivateNetworkAclMiddleware (inner) adds Access-Control-Allow-Private-Network.
app.add_middleware(PrivateNetworkAclMiddleware)     # inner  — adds PNA token
app.add_middleware(                                 # outer  — adds CORS headers to all responses
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _phase_stats(subset: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute ASR, DR, avg harm for a subset of rounds."""
    if not subset:
        return {"asr": 0.0, "dr": 0.0, "avg_harm": 0.0, "rounds": 0}
    n = len(subset)
    red_wins = sum(1 for r in subset if r.get("red_success"))
    blue_wins = sum(1 for r in subset if r.get("blue_success"))
    harm = [r["harmfulness_score"] for r in subset if r.get("harmfulness_score") is not None]
    raw = [_raw_harm(r) for r in subset]
    return {
        "rounds": n,
        "asr": round(red_wins / n, 4),
        "dr": round(blue_wins / n, 4),
        "avg_harm": round(sum(harm) / len(harm), 4) if harm else 0.0,
        # PSS: avg harm the model PRODUCED (pre-blue-filter) — red's true progress
        "avg_raw_harm": round(sum(raw) / n, 4) if raw else 0.0,
    }


def _raw_harm(r: dict[str, Any]) -> float:
    """Raw harm score, falling back to final harm for legacy rows."""
    v = r.get("raw_harmfulness_score")
    if v is None:
        v = r.get("harmfulness_score")
    return float(v) if v is not None else 0.0


def _compute_stats(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive aggregate + early/mid/late phase statistics from round records."""
    if not rounds:
        return {
            "total_rounds": 0,
            "red_wins": 0,
            "blue_wins": 0,
            "attack_success_rate": 0.0,
            "defense_rate": 0.0,
            "avg_harmfulness_score": 0.0,
            "max_harmfulness_score": 0.0,
            "avg_raw_harmfulness_score": 0.0,
            "max_raw_harmfulness_score": 0.0,
            "red_pss": 0.0,
            "blue_pss": 0.0,
            "phase_early": _phase_stats([]),
            "phase_late": _phase_stats([]),
        }

    red_wins = sum(1 for r in rounds if r.get("red_success"))
    blue_wins = sum(1 for r in rounds if r.get("blue_success"))
    total = len(rounds)
    harm_scores = [r["harmfulness_score"] for r in rounds if r.get("harmfulness_score") is not None]
    raw_scores = [_raw_harm(r) for r in rounds]
    # blue PSS = harm reduction blue delivered (raw produced minus what survived)
    reductions = [max(0.0, _raw_harm(r) - (r.get("harmfulness_score") or 0.0)) for r in rounds]

    # Early = first third, late = last third (min 1 round each when total < 3)
    third = max(1, total // 3)
    early_rounds = rounds[:third]
    late_rounds  = rounds[total - third:]

    return {
        "total_rounds": total,
        "red_wins": red_wins,
        "blue_wins": blue_wins,
        "attack_success_rate": round(red_wins / total, 4) if total else 0.0,
        "defense_rate": round(blue_wins / total, 4) if total else 0.0,
        "avg_harmfulness_score": round(sum(harm_scores) / len(harm_scores), 4) if harm_scores else 0.0,
        "max_harmfulness_score": round(max(harm_scores), 4) if harm_scores else 0.0,
        # PSS (Partial Success Score): continuous progress signal. Binary ASR/DR
        # can read 0/100 even when red made the model leak partial data and blue
        # scrubbed it — these surface that hidden progress.
        "avg_raw_harmfulness_score": round(sum(raw_scores) / total, 4) if raw_scores else 0.0,
        "max_raw_harmfulness_score": round(max(raw_scores), 4) if raw_scores else 0.0,
        "red_pss":  round(sum(raw_scores) / total, 4) if raw_scores else 0.0,
        "blue_pss": round(sum(reductions) / total, 4) if reductions else 0.0,
        # Phase splits for before/after evolution comparison
        "phase_early": _phase_stats(early_rounds),
        "phase_late":  _phase_stats(late_rounds),
    }


def _escape_py_braces(s: str) -> str:
    """So str.format on the outer template does not treat LLM/markdown/HTML `{` `}` as fields."""
    return s.replace("{", "{{").replace("}", "}}")


def _merge_pdf_statistics(
    stats: dict[str, Any],
    nar_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    PDF header shows one stats block. Full report and narrative path both recompute
    from DB; if they ever diverge, prefer the richer round / win counters. Harm
    metrics: take the max of each side so a partial zero does not wipe real scores.
    """
    out = dict(stats)
    alt = nar_stats or {}
    if not alt:
        return out

    def _counter_weight(d: dict[str, Any]) -> tuple[int, int]:
        t = int(d.get("total_rounds") or 0)
        w = int(d.get("red_wins") or 0) + int(d.get("blue_wins") or 0)
        return (t, w)

    if _counter_weight(alt) > _counter_weight(out):
        for k in (
            "total_rounds",
            "red_wins",
            "blue_wins",
            "attack_success_rate",
            "defense_rate",
        ):
            if k in alt:
                out[k] = alt[k]

    a1 = float(out.get("avg_harmfulness_score") or 0)
    a2 = float(alt.get("avg_harmfulness_score") or 0)
    m1 = float(out.get("max_harmfulness_score") or 0)
    m2 = float(alt.get("max_harmfulness_score") or 0)
    out["avg_harmfulness_score"] = round(max(a1, a2), 4)
    out["max_harmfulness_score"] = round(max(m1, m2), 4)

    return out


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "report-composer"}


@app.get("/v1/reports")
async def list_reports(limit: int = 20, offset: int = 0):
    """List recent battle sessions with summary stats."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                bs.id, bs.mode, bs.status, bs.max_rounds,
                bs.red_service_id, bs.blue_service_id,
                bs.red_wins, bs.blue_wins,
                bs.created_at, bs.ended_at,
                COUNT(et.id) AS rounds_recorded,
                COALESCE(AVG(et.harmfulness_score), 0) AS avg_harm
            FROM battle_sessions bs
            LEFT JOIN execution_traces et ON et.session_id = bs.id
            GROUP BY bs.id
            ORDER BY bs.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return {
        "sessions": [
            {
                "session_id": str(r["id"]),
                "mode": r["mode"],
                "status": r["status"],
                "max_rounds": r["max_rounds"],
                "red_service_id": r["red_service_id"],
                "blue_service_id": r["blue_service_id"],
                "red_wins": r["red_wins"],
                "blue_wins": r["blue_wins"],
                "rounds_recorded": r["rounds_recorded"],
                "avg_harmfulness_score": round(float(r["avg_harm"]), 4),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


async def _get_asis_evolution(conn, adapter_id: str | None, team: str) -> dict[str, Any] | None:
    """Summarise the ASIS self-improvement history for one adapter.

    Reads adapter_generations — the cross-generation record written by the
    code-improver as it rewrites an external project and benchmarks each
    generation. This is the real before/after proof that the platform
    improved the plugged-in project (gen_0 baseline → promoted generations).
    Returns None when the adapter never went through ASIS.
    """
    if not adapter_id:
        return None
    rows = await conn.fetch(
        """
        SELECT gen_number, is_active, canary_passed,
               benchmark_asr, benchmark_dr, benchmark_pss, benchmark_pss_std,
               rollback_reason
        FROM adapter_generations
        WHERE adapter_id = $1
        ORDER BY gen_number, created_at
        """,
        adapter_id,
    )
    if not rows:
        return None

    # Per gen_number, keep the best benchmarked attempt (highest PSS) so the
    # progression chart shows the strongest result ASIS achieved at each step.
    best_by_gen: dict[int, dict[str, Any]] = {}
    promoted = 0
    rolled_back = 0
    max_gen_number = 0
    rolled_back_gen_numbers: list[int] = []
    for r in rows:
        g = r["gen_number"]
        pss = r["benchmark_pss"]
        max_gen_number = max(max_gen_number, int(g))
        if r["rollback_reason"]:
            rolled_back += 1
            rolled_back_gen_numbers.append(int(g))
        # Count only REAL improvement promotions (gen > 0). gen_0 is the original
        # baseline — marking it active is not a "promotion" and counting it made
        # the report read "promoted=1" even when nothing improved on the baseline.
        if r["is_active"] and g > 0:
            promoted += 1
        if pss is None:
            best_by_gen.setdefault(g, {
                "gen": g, "pss": None, "std": None, "asr": r["benchmark_asr"],
                "dr": r["benchmark_dr"], "active": r["is_active"],
            })
            continue
        cur = best_by_gen.get(g)
        if cur is None or (cur["pss"] is None) or (float(pss) > float(cur["pss"])):
            best_by_gen[g] = {
                "gen": g, "pss": float(pss),
                "std": float(r["benchmark_pss_std"]) if r["benchmark_pss_std"] is not None else None,
                "asr": float(r["benchmark_asr"]) if r["benchmark_asr"] is not None else None,
                "dr": float(r["benchmark_dr"]) if r["benchmark_dr"] is not None else None,
                "active": r["is_active"],
            }
        if best_by_gen[g].get("active") is False and r["is_active"]:
            best_by_gen[g]["active"] = True

    gens = [best_by_gen[k] for k in sorted(best_by_gen.keys())]
    benchmarked = [g for g in gens if g["pss"] is not None]
    baseline_pss = benchmarked[0]["pss"] if benchmarked else None
    best = max(benchmarked, key=lambda g: g["pss"]) if benchmarked else None
    active = next((g for g in gens if g.get("active")), None)

    # ── Saturation ──────────────────────────────────────────────────────────
    # Turn-based ASIS attempts an improvement every round the side loses. Once it
    # reaches its best, further attempts land on top of the current best (a failed
    # candidate is inserted at active_gen+1 and rolled back, so its gen_number
    # stays > the deployed active gen). Repeated failed attempts on top of the
    # best = the side has PLATEAUED: no edit yields more capability, or would
    # regress, so the best version is kept and further churn is wasted. We surface
    # that so a long run (e.g. 200 rounds) is reported honestly as "improved up to
    # gen N at round M, then saturated" rather than implying endless progress.
    active_gen_num = int(active["gen"]) if active else 0
    attempts_since_best = sum(1 for g in rolled_back_gen_numbers if g > active_gen_num)
    saturated = attempts_since_best >= _SATURATION_ATTEMPTS
    return {
        "adapter_id": adapter_id,
        "team": team,
        "gens": gens,
        "baseline_pss": baseline_pss,
        "best_pss": best["pss"] if best else None,
        "best_std": best.get("std") if best else None,
        "best_gen": best["gen"] if best else None,
        "active_gen": active["gen"] if active else None,
        "active_pss": active["pss"] if active else None,
        "active_std": active.get("std") if active else None,
        "promoted_count": promoted,
        "rolled_back_count": rolled_back,
        # `improved` = did the DEPLOYED (active) generation beat the gen_0 baseline?
        # This is what the connected project actually runs — the honest headline.
        "improved": (active is not None and active.get("pss") is not None
                     and baseline_pss is not None
                     and active["pss"] > baseline_pss + 1e-9),
        # Flag when the best benchmarked gen is NOT the one deployed — surfaces a
        # promotion regression (a better generation exists but isn't active).
        "best_not_deployed": (best is not None and active is not None
                              and best.get("gen") != active.get("gen")
                              and best.get("pss") is not None and active.get("pss") is not None
                              and best["pss"] > active["pss"] + 1e-9),
        # Saturation: repeated failed attempts on top of the deployed best mean the
        # side has plateaued — the best version is kept and further edits add nothing
        # (or would regress). `attempts_since_best` is how many later tries made no gain.
        "saturated": saturated,
        "attempts_since_best": attempts_since_best,
        "saturation_note": (
            (f"{team} improvement saturated: best is gen {active_gen_num} "
             f"(PSS {active['pss']:.3f}); {attempts_since_best} later attempt(s) made "
             f"no further gain — best version kept, further rounds add no capability."
             if active and active.get("pss") is not None else
             f"{team} could not improve on its gen_0 baseline after "
             f"{attempts_since_best} attempt(s); baseline kept.")
            if saturated else None
        ),
    }


async def _get_report_data(session_id: str) -> dict[str, Any]:
    """Shared logic: fetch session + traces and return report dict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM battle_sessions WHERE id = $1", session_id
        )
        if not session:
            raise HTTPException(404, f"Session {session_id} not found")

        traces = await conn.fetch(
            """
            SELECT round, attack_payload, attack_type, attack_confidence,
                   defense_decision, defense_confidence, defense_reason,
                   target_response,
                   red_success, blue_success,
                   harmfulness_score, raw_harmfulness_score,
                   judge_reasoning, tokens_used, created_at
            FROM execution_traces
            WHERE session_id = $1
            ORDER BY round
            """,
            session_id,
        )

        asis_evolution = {
            "red": await _get_asis_evolution(conn, session["red_service_id"], "red"),
            "blue": await _get_asis_evolution(conn, session["blue_service_id"], "blue"),
        }

    rounds = [
        {
            "round": r["round"],
            "attack": {
                "payload": r["attack_payload"],
                "type": r["attack_type"],
                "confidence": r["attack_confidence"],
            },
            "defense": {
                "decision": r["defense_decision"],
                "confidence": r["defense_confidence"],
                "reason": r["defense_reason"],
            },
            "target_response": r["target_response"],
            "red_success": r["red_success"],
            "blue_success": r["blue_success"],
            "harmfulness_score": r["harmfulness_score"],
            "raw_harmfulness_score": r["raw_harmfulness_score"],
            "judge_reasoning": r["judge_reasoning"],
            "tokens_used": r["tokens_used"],
            "timestamp": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in traces
    ]

    stats = merge_trace_statistics_with_session(
        session,
        traces_count=len(rounds),
        trace_statistics=_compute_stats(rounds),
    )

    # Early-termination flag: when a side disconnected mid-battle the run was
    # stopped and only completed rounds have traces. Surface this so the report
    # is honest about being a partial run rather than a full evaluation.
    _stop_reason = session["stop_reason"] if "stop_reason" in session else ""
    terminated_early = None
    if _stop_reason == "adapter_disconnected":
        _planned = session["max_rounds"]
        terminated_early = {
            "reason": "adapter_disconnected",
            "completed_rounds": len(rounds),
            "planned_rounds": _planned,
            "message": (
                "Battle ended early: a connected adapter became unreachable and did "
                f"not reconnect. All metrics are computed over the {len(rounds)} "
                "completed round(s)"
                + (f" of {_planned} planned." if _planned else ".")
            ),
        }

    return {
        "session_id": session_id,
        "terminated_early": terminated_early,
        "mode": session["mode"],
        "status": session["status"],
        "max_rounds": session["max_rounds"],
        "red_service_id": session["red_service_id"],
        "blue_service_id": session["blue_service_id"],
        "created_at": session["created_at"].isoformat() if session["created_at"] else None,
        "ended_at": session["ended_at"].isoformat() if session["ended_at"] else None,
        "statistics": stats,
        "asis_evolution": asis_evolution,
        "rounds": rounds,
        # Per-battle improvement toggles — the report renders only the sections
        # for loops that actually ran (see _build_run_config_section).
        "inner_loop_enabled": bool(session["inner_loop_enabled"]) if "inner_loop_enabled" in session else False,
        "outer_loop_enabled": bool(session["outer_loop_enabled"]) if "outer_loop_enabled" in session else False,
    }


@app.get("/v1/reports/{session_id}")
async def get_report(session_id: str):
    """Full report for a single battle session."""
    return await _get_report_data(session_id)


from narrative import generate_narrative


@app.post("/v1/reports/{session_id}/narrative")
async def get_narrative(session_id: str):
    """
    Generate (or return cached) LLM narrative report for a completed battle session.

    Collects all execution traces + strategy evolution records, sends them to an LLM,
    and returns:
      - zone_insights: short per-zone summaries for the Scribe agent's speech bubbles
      - narrative:     full markdown analytical report for PrinterModal
      - statistics:    aggregate stats (ASR, DR, harm scores)
    """
    pool = await get_pool()
    try:
        result = await generate_narrative(session_id, pool)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Narrative generation failed: {exc}")


from fastapi.responses import HTMLResponse as _HTMLResponse
import os as _os
import re as _re
import markdown as _md


_REPORT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACEA Battle Report — {short_id}</title>
<style>
  :root {{
    --red: #c0392b;
    --blue: #2980b9;
    --green: #27ae60;
    --yellow: #f39c12;
    --gray: #555;
    --bg: #fff;
    /* Brand palette — white/gold */
    --gold: #e0a82e;          /* primary brand gold (logo) */
    --gold-dark: #a9781a;     /* headings / strong accents */
    --gold-deep: #6e4e0f;     /* table header background */
    --gold-soft: #fdf6e4;     /* tinted panel background */
    --gold-line: #efd9a3;     /* subtle gold borders */
  }}
  @media print {{
    body {{ margin: 0; font-size: 10pt; }}
    .no-print {{ display: none !important; }}
    .page-break {{ page-break-before: always; }}
  }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: #222;
    background: #fff;
    max-width: 900px;
    margin: 32px auto;
    padding: 0 24px 48px;
    line-height: 1.6;
  }}
  header {{
    border-bottom: 3px solid var(--gold);
    padding-bottom: 12px;
    margin-bottom: 24px;
  }}
  header h1 {{
    margin: 0 0 4px;
    font-size: 22px;
    color: var(--gold-dark);
    letter-spacing: 0.05em;
  }}
  header .meta {{
    font-size: 11px;
    color: var(--gray);
    font-family: monospace;
  }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 12px;
    margin: 20px 0 28px;
  }}
  .stat-box {{
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px 14px;
    text-align: center;
  }}
  .stat-box .val {{
    font-size: 24px;
    font-weight: 700;
    line-height: 1;
  }}
  .stat-box .lbl {{
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--gray);
    margin-top: 4px;
  }}
  .red {{ color: var(--red); }}
  .blue {{ color: var(--blue); }}
  .green {{ color: var(--green); }}
  .yellow {{ color: var(--yellow); }}

  .zone-insights {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 10px;
    margin: 0 0 32px;
  }}
  .zone-box {{
    border-left: 3px solid;
    padding: 8px 12px;
    background: #fafafa;
    border-radius: 0 4px 4px 0;
  }}
  .zone-box .zone-label {{
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 4px;
    font-family: monospace;
  }}
  .zone-box p {{
    margin: 0;
    font-size: 11px;
    color: #333;
    line-height: 1.5;
  }}

  /* Markdown-rendered narrative */
  .narrative h1, .narrative h2 {{
    border-bottom: 1px solid var(--gold-line);
    padding-bottom: 4px;
    margin-top: 28px;
  }}
  .narrative h1 {{ font-size: 17px; color: var(--gold-dark); }}
  .narrative h2 {{ font-size: 15px; color: var(--gold-dark); }}
  .narrative h3 {{ font-size: 13px; color: #6e4e0f; margin-top: 16px; }}
  .narrative h4 {{ font-size: 12px; color: var(--gray); margin-top: 12px; }}
  .narrative table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 11px;
  }}
  .narrative th {{
    background: var(--gold-deep);
    color: #fff;
    padding: 5px 8px;
    text-align: left;
    font-weight: 600;
  }}
  .narrative td {{
    padding: 4px 8px;
    border-bottom: 1px solid #eee;
    vertical-align: top;
  }}
  .narrative tr:nth-child(even) td {{ background: #f9fafb; }}
  .narrative ul, .narrative ol {{
    padding-left: 20px;
    margin: 8px 0;
  }}
  .narrative li {{ margin-bottom: 3px; }}
  .narrative blockquote {{
    border-left: 3px solid #cbd5e1;
    margin: 8px 0;
    padding: 4px 12px;
    color: #64748b;
    background: #f8fafc;
  }}
  .narrative code {{
    background: var(--gold-soft);
    color: #6e4e0f;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: 'SF Mono','Cascadia Code',Consolas,monospace;
    font-size: 11px;
  }}
  /* Generic fenced code — clean light card */
  .narrative pre {{
    background: #fbfaf6;
    color: #2b2b2b;
    border: 1px solid var(--gold-line);
    border-left: 3px solid var(--gold);
    padding: 12px 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 11px;
    line-height: 1.55;
  }}
  .narrative pre code {{
    background: none; padding: 0; color: inherit;
    font-family: 'SF Mono','Cascadia Code',Consolas,monospace;
  }}

  /* Line-painted diff block (GitHub-style) */
  .narrative pre.diffblock {{
    background: #fcfbf7; padding: 0; border-left: 3px solid var(--gold);
  }}
  .narrative pre.diffblock code {{ display: block; }}
  .diffblock .dl {{
    display: block; padding: 0 12px; white-space: pre;
    font-family: 'SF Mono','Cascadia Code',Consolas,monospace;
  }}
  .diffblock .d-file {{ color: #6e4e0f; font-weight: 700; background: var(--gold-soft); }}
  .diffblock .d-hunk {{ color: #8a6d1a; background: #fbf2d6; }}
  .diffblock .d-add  {{ color: #15803d; background: #eaf7ee; }}
  .diffblock .d-del  {{ color: #b91c1c; background: #fbecec; }}
  .diffblock .d-ctx  {{ color: #475569; }}

  .trace-appendix h2 {{
    font-size: 16px;
    color: var(--gold-dark);
    margin-top: 8px;
    border-bottom: 2px solid var(--gold);
    padding-bottom: 6px;
  }}
  .trace-round {{
    border: 1px solid #e2e8f0;
    margin: 14px 0;
    padding: 12px 14px;
    border-radius: 6px;
    background: #fafafa;
  }}
  .trace-round h4 {{
    margin: 0 0 10px;
    font-size: 13px;
    color: #c0392b;
  }}
  .trace-block-label {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748b;
    margin: 8px 0 4px;
  }}
  .trace-appendix pre {{
    margin: 0 0 6px;
    background: #f1f5f9;
    color: #334155;
    padding: 10px 12px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }}

  .print-btn {{
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--gold);
    color: #3a2a06;
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    letter-spacing: 0.04em;
  }}
  .print-btn:hover {{ background: var(--gold-dark); color: #fff; }}

  .chart-row {{
    display: flex; flex-wrap: wrap; gap: 24px; margin-top: 12px;
  }}
  .asis-box {{
    flex: 1 1 320px; border: 1px solid #e2e8f0; border-left: 4px solid;
    border-radius: 6px; padding: 12px 14px; background: #fff;
  }}

  /* Hero verdict banner — the headline "did we improve?" answer */
  .verdict-banner {{
    border: 1px solid var(--gold-line); border-radius: 10px;
    background: linear-gradient(180deg,#fffdf7,var(--gold-soft));
    padding: 18px 20px; margin: 0 0 28px;
    border-left: 5px solid var(--gold);
  }}
  .verdict-banner .vb-title {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
    color: var(--gold-dark); font-weight: 700;
  }}
  .verdict-banner .vb-verdict {{
    font-size: 20px; font-weight: 800; margin: 4px 0 14px;
  }}
  .verdict-grid {{
    display: grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr));
    gap: 14px;
  }}
  .verdict-card {{
    border-left: 4px solid; background: #fff; border-radius: 0 6px 6px 0;
    padding: 10px 14px; box-shadow: 0 1px 3px rgba(15,23,42,0.05);
  }}
  .verdict-card .vc-team {{
    font-size: 10px; font-weight: 700; letter-spacing: 0.1em;
    font-family: monospace;
  }}
  .verdict-card .vc-headline {{ font-size: 18px; font-weight: 800; margin: 2px 0 4px; }}
  .verdict-card .vc-sub {{ font-size: 11px; color: #475569; line-height: 1.5; }}
  .verdict-card .vc-flag {{ color: #b45309; font-weight: 600; }}
  .verdict-banner .vb-method {{
    font-size: 11px; color: #64748b; line-height: 1.55;
    margin-top: 14px; padding-top: 12px; border-top: 1px dashed #e2e8f0;
  }}

  /* Collapsible round trace */
  .trace-appendix details {{
    border: 1px solid #e2e8f0; border-radius: 6px; margin: 8px 0; background: #fafafa;
  }}
  .trace-appendix summary {{
    cursor: pointer; padding: 8px 12px; font-size: 12px; font-weight: 600;
    color: #c0392b; list-style: revert;
  }}
  .trace-appendix details[open] summary {{ border-bottom: 1px solid #e2e8f0; }}
  .trace-appendix .trace-body {{ padding: 4px 14px 12px; }}
</style>
</head>
<body>

<header>
  <h1>⬡ ACEA BATTLE REPORT</h1>
  <div class="meta">
    Session: <strong>{session_id}</strong> &nbsp;|&nbsp;
    Mode: {mode} &nbsp;|&nbsp; Status: {status}<br>
    Red: <strong>{red_service_id}</strong> vs Blue: <strong>{blue_service_id}</strong><br>
    {created_at} → {ended_at}
  </div>
</header>

{run_config_section}

{verdict_banner}

<section>
  <div class="stats-grid">
    <div class="stat-box"><div class="val">{total_rounds}</div><div class="lbl">Rounds</div></div>
    <div class="stat-box"><div class="val red">{red_wins}</div><div class="lbl">Red Wins</div></div>
    <div class="stat-box"><div class="val blue">{blue_wins}</div><div class="lbl">Blue Wins</div></div>
    <div class="stat-box"><div class="val {asr_color}">{asr_pct}%</div><div class="lbl">Attack SR</div></div>
    <div class="stat-box"><div class="val {dr_color}">{dr_pct}%</div><div class="lbl">Defense Rate</div></div>
    <div class="stat-box"><div class="val {harm_color}">{avg_harm}</div><div class="lbl">Avg Harm</div></div>
    <div class="stat-box"><div class="val {max_harm_color}">{max_harm}</div><div class="lbl">Max Harm</div></div>
  </div>
</section>

{asis_section}

{phase_chart}

{zone_section}

<div class="page-break"></div>

<section class="narrative">
{narrative_html}
</section>

<div class="page-break"></div>

<section class="trace-appendix">
<h2>Round-by-round trace (full source data)</h2>
<p style="font-size:11px;color:#64748b;">Red attack payloads, blue decisions, prompts to Target AI, raw model output, post–output-filter text delivered to observers, Judge reasoning.</p>
{trace_appendix}
</section>

<button class="no-print print-btn" onclick="window.print()">🖨 Print / Save PDF</button>

</body>
</html>"""


def _build_trace_appendix_html(rounds: list[dict[str, Any]]) -> str:
    """Escaped HTML appendix with verbatim trace fields for print/PDF."""
    if not rounds:
        return '<p style="font-size:12px;color:#94a3b8;"><em>No execution_traces rows for this session.</em></p>'
    chunks: list[str] = []

    def pre_block(title: str, content: Any) -> str:
        txt = html_module.escape(str(content or "").strip() or "(empty)")
        tl = html_module.escape(title)
        return f'<div class="trace-block-label">{tl}</div><pre>{txt}</pre>'

    for r in rounds:
        atk = r.get("attack") or {}
        dfs = r.get("defense") or {}
        outcome = "RED WIN" if r.get("red_success") else "BLUE WIN"
        harm = r.get("harmfulness_score")
        harm_s = f"{float(harm):.4f}" if harm is not None else "—"

        rnd = html_module.escape(str(r.get("round", "?")))
        oc = html_module.escape(outcome)
        oc_color = "#c0392b" if r.get("red_success") else "#2980b9"
        # Each round is collapsed by default so a 100-round battle stays scannable;
        # the reader expands only the rounds they care about.
        chunks.append('<details class="trace-round">')
        chunks.append(
            f'<summary style="color:{oc_color}">Round {rnd} · {oc} · harm {html_module.escape(harm_s)}</summary>'
        )
        chunks.append('<div class="trace-body">')
        chunks.append(pre_block("Red attack payload", atk.get("payload")))
        meta = f"type={atk.get('type')} · confidence={atk.get('confidence')}"
        chunks.append(f'<p style="font-size:11px;color:#555;margin:4px 0;">{html_module.escape(meta)}</p>')
        dtxt = (
            f"{dfs.get('decision')} (confidence={dfs.get('confidence')})\n"
            f"{dfs.get('reason') or ''}"
        )
        chunks.append(pre_block("Blue input-gate defense", dtxt))
        chunks.append(pre_block("Target AI response", r.get("target_response")))
        chunks.append(pre_block("Judge reasoning", r.get("judge_reasoning")))
        chunks.append("</div></details>")
    return "\n".join(chunks)


def _build_zone_section(zone_insights: dict[str, Any]) -> str:
    zone_cfg = [
        ("red_team",        "Red Team",   "#ef4444"),
        ("blue_team",       "Blue Team",  "#3b82f6"),
        ("target_ai",       "Target AI",  "#a855f7"),
        ("judge",           "Judge",      "#eab308"),
        ("overall_summary", "Summary",    "#22c55e"),
    ]
    boxes = ""
    for key, label, color in zone_cfg:
        text = zone_insights.get(key, "—")
        safe = html_module.escape(str(text))
        boxes += (
            f'<div class="zone-box" style="border-color:{color}">'
            f'<div class="zone-label" style="color:{color}">{label}</div>'
            f'<p>{safe}</p></div>\n'
        )
    return f'<div class="zone-insights">\n{boxes}</div>'


def _svg_bar_chart(series: list[tuple[str, float]], color: str, title: str,
                   vmax: float = 1.0) -> str:
    """Dependency-free inline SVG horizontal bar chart (renders in print/PDF).

    series: list of (label, value 0..vmax). Returns an <svg> string.
    """
    if not series:
        return ""
    row_h, bar_w, left, top = 26, 260, 96, 28
    height = top + row_h * len(series) + 10
    width = left + bar_w + 60
    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="max-width:100%;">',
        f'<text x="0" y="16" font-size="12" font-weight="700" fill="#334155">{html_module.escape(title)}</text>',
    ]
    for i, (label, val) in enumerate(series):
        y = top + i * row_h
        v = 0.0 if val is None else max(0.0, min(float(val), vmax))
        w = int((v / vmax) * bar_w) if vmax else 0
        parts.append(
            f'<text x="0" y="{y + 13}" font-size="11" fill="#475569">{html_module.escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{left}" y="{y}" width="{bar_w}" height="16" rx="3" fill="#e2e8f0"/>'
        )
        parts.append(
            f'<rect x="{left}" y="{y}" width="{w}" height="16" rx="3" fill="{color}"/>'
        )
        disp = "—" if val is None else f"{float(val):.2f}"
        parts.append(
            f'<text x="{left + bar_w + 6}" y="{y + 13}" font-size="11" '
            f'font-weight="600" fill="#334155">{disp}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _build_phase_chart(stats: dict[str, Any]) -> str:
    """Within-battle before/after: early-phase vs late-phase ASR & raw harm."""
    early = stats.get("phase_early") or {}
    late = stats.get("phase_late") or {}
    if not early and not late:
        return ""
    asr_chart = _svg_bar_chart(
        [("Early ASR", early.get("asr", 0.0)), ("Late ASR", late.get("asr", 0.0))],
        "#ef4444", "Attack success rate: early vs late rounds",
    )
    harm_chart = _svg_bar_chart(
        [("Early raw-harm", early.get("avg_raw_harm", 0.0)),
         ("Late raw-harm", late.get("avg_raw_harm", 0.0))],
        "#a855f7", "Raw harm produced: early vs late rounds",
    )
    return (
        '<section><h2>Within-battle evolution (early vs late rounds)</h2>'
        '<p style="font-size:11px;color:#64748b;">Rising late-phase numbers indicate '
        'the adapter adapted its strategy as the battle progressed.</p>'
        f'<div class="chart-row">{asr_chart}{harm_chart}</div></section>'
    )


def _build_run_config_section(inner_enabled: bool, outer_enabled: bool) -> str:
    """Record which improvement loops were active for this battle so the report
    is an honest function of what actually ran (not a fixed template)."""
    def pill(on: bool) -> str:
        return ('<span style="color:#16a34a;font-weight:700;">ON</span>'
                if on else '<span style="color:#94a3b8;">OFF</span>')
    return (
        '<div class="run-config" style="margin:16px 0;padding:10px 14px;'
        'border:1px solid #e2e8f0;border-radius:6px;font-size:12px;">'
        '<span style="text-transform:uppercase;letter-spacing:.08em;'
        'color:#64748b;font-weight:700;">Run configuration</span> &nbsp; '
        f'Inner loop (in-context evolution): {pill(inner_enabled)} &nbsp;&middot;&nbsp; '
        f'Outer loop (code self-improvement): {pill(outer_enabled)}'
        '</div>'
    )


def _build_asis_section(asis_evolution: dict[str, Any] | None,
                        outer_enabled: bool = True) -> str:
    """ASIS self-improvement: gen-over-gen benchmark progression per team.

    This is the core research evidence — did the platform improve the
    plugged-in external project beyond its original (gen_0) design? Rendered
    only when the outer loop was enabled for this battle.
    """
    if not outer_enabled:
        return ""
    if not asis_evolution:
        return ""
    blocks: list[str] = []
    for team in ("red", "blue"):
        ev = asis_evolution.get(team)
        if not ev:
            continue
        color = "#ef4444" if team == "red" else "#3b82f6"
        series = [(f"gen_{g['gen']}" + ("  ●active" if g.get("active") else ""),
                   g.get("pss")) for g in ev["gens"] if g.get("pss") is not None]
        chart = _svg_bar_chart(series, color,
                               f"{team.upper()} adapter — PSS by generation") if series else \
            '<p style="font-size:11px;color:#94a3b8;"><em>No benchmarked generations yet.</em></p>'

        base = ev.get("baseline_pss")
        # Report the DEPLOYED (active) generation — what the project actually runs.
        active = ev.get("active_pss")
        std = ev.get("active_std")
        active_disp = (f'{active:.3f} ± {std:.3f}' if (active is not None and std) else
                       (f'{active:.3f}' if active is not None else '—'))
        if ev.get("improved") and base is not None and active is not None:
            delta = active - base
            # Flag when the gain is within one stddev of the baseline — not yet
            # distinguishable from benchmark noise (scientific honesty).
            noisy = bool(std) and delta <= std
            note = ' <span style="color:#b45309;">(within ±1σ of baseline — directional, not yet significant)</span>' if noisy else ''
            verdict = (
                (f'<span style="color:#16a34a;font-weight:700;">✓ improved</span> — '
                 f'PSS {base:.3f} (gen_0) → deployed {active_disp} (gen_{ev.get("active_gen")}), '
                 f'Δ +{delta:.3f} ({(delta / base * 100) if base else 0:.0f}% gain)'
                 if base else
                 f'<span style="color:#16a34a;font-weight:700;">✓ improved</span> — '
                 f'PSS 0 (gen_0) → deployed {active_disp} (gen_{ev.get("active_gen")})') + note
            )
            if ev.get("best_not_deployed"):
                verdict += (f' <span style="color:#b45309;">— note: gen_{ev.get("best_gen")} '
                            f'benchmarked higher ({ev.get("best_pss"):.3f}) but is not deployed</span>')
        else:
            rb = ev.get("rolled_back_count", 0)
            if rb > 0:
                verdict = (f'<span style="color:#64748b;">baseline held</span> — gen_0 still active; '
                           f'{rb} attempt(s) tried but none beat it (reverted, no regression shipped)')
            else:
                verdict = ('<span style="color:#64748b;">baseline held</span> — gen_0 still active; '
                           'no improvement generation promoted yet')
        meta = (f'active=gen_{ev.get("active_gen")} · '
                f'promoted={ev.get("promoted_count")} · '
                f'rolled_back={ev.get("rolled_back_count")}')
        sat_html = ''
        if ev.get("saturated") and ev.get("saturation_note"):
            sat_html = (
                f'<p style="font-size:11px;color:#b45309;margin:2px 0 8px;font-weight:600;">'
                f'⚑ saturated — {html_module.escape(ev["saturation_note"])} '
                f'The best version is kept; running more rounds will not add capability.</p>'
            )
        blocks.append(
            f'<div class="asis-box" style="border-color:{color}">'
            f'<div class="zone-label" style="color:{color}">{team.upper()} self-improvement</div>'
            f'<p style="font-size:12px;margin:4px 0;">{verdict}</p>'
            f'<p style="font-size:11px;color:#64748b;margin:2px 0 8px;">{html_module.escape(meta)}</p>'
            f'{sat_html}'
            f'{chart}</div>'
        )
    if not blocks:
        return ""
    return (
        '<section><h2>ASIS self-improvement (cross-generation, before vs after)</h2>'
        '<p style="font-size:11px;color:#64748b;">Each generation is a real code '
        'rewrite of the plugged-in project, rebuilt and benchmarked. PSS (Partial '
        'Success Score) is the continuous fitness signal. gen_0 = original project '
        'as submitted.</p>'
        f'<div class="chart-row">{"".join(blocks)}</div></section>'
    )


def _prettify_diff_blocks(html: str) -> str:
    """Rewrite ```diff fenced blocks into per-line colored rows.

    Python-Markdown emits a diff as one plain <pre><code class="language-diff">…</code>.
    We split it line-by-line and tag each line by its leading character so CSS can
    paint added/removed/hunk/meta lines — a readable, GitHub-style diff that matches
    the report theme (no external highlighter / stylesheet needed).
    """
    pat = _re.compile(
        r'<pre><code class="language-diff">(.*?)</code></pre>',
        _re.DOTALL,
    )

    def render(m: _re.Match) -> str:
        body = m.group(1)
        rows: list[str] = []
        for line in body.split("\n"):
            # `line` is already HTML-escaped by markdown. Classify on first char.
            stripped = line.lstrip()
            if line.startswith("+++") or line.startswith("---"):
                cls = "d-file"
            elif line.startswith("@@"):
                cls = "d-hunk"
            elif stripped.startswith("+") or line.startswith("+"):
                cls = "d-add"
            elif stripped.startswith("-") or line.startswith("-"):
                cls = "d-del"
            else:
                cls = "d-ctx"
            rows.append(f'<span class="dl {cls}">{line or "&nbsp;"}</span>')
        return '<pre class="diffblock"><code>' + "\n".join(rows) + "</code></pre>"

    return pat.sub(render, html)


def _verdict_card(ev: dict[str, Any] | None, team: str) -> str:
    """One team's bottom-line improvement verdict for the hero banner."""
    color = "#ef4444" if team == "red" else "#3b82f6"
    title = f"{team.upper()} TEAM"
    if not ev:
        return (
            f'<div class="verdict-card" style="border-color:{color}">'
            f'<div class="vc-team" style="color:{color}">{title}</div>'
            f'<div class="vc-headline" style="color:#64748b">Not self-improved</div>'
            f'<div class="vc-sub">This side was the winner (or self-improvement was '
            f'disabled), so the platform left its code untouched.</div></div>'
        )

    base = ev.get("baseline_pss")
    # Headline the DEPLOYED (active) generation — what the project actually runs.
    active = ev.get("active_pss")
    std = ev.get("active_std")
    best = ev.get("best_pss")
    if ev.get("improved") and active is not None:
        delta = active - base if base is not None else active
        pct = f" (+{delta / base * 100:.0f}%)" if base else ""
        noisy = bool(std) and base is not None and delta <= std
        headline = f'<span style="color:#16a34a">▲ +{delta:.3f} PSS{pct}</span>'
        flag = (' <span class="vc-flag">within ±1σ — directional, not yet significant</span>'
                if noisy else '')
        base_disp = f"{base:.3f}" if base is not None else "0"
        sub = (f'gen_0 baseline {base_disp} → <strong>deployed {active:.3f}</strong>'
               + (f' ± {std:.3f}' if std else '')
               + f' (gen_{ev.get("active_gen")}).{flag}')
        # Honesty: if a better generation exists but was not promoted, say so —
        # do not let the headline imply the best-ever result is what ships.
        if ev.get("best_not_deployed"):
            sub += (f' <span class="vc-flag">Note: a higher-scoring gen_{ev.get("best_gen")} '
                    f'({best:.3f}) was benchmarked but is not the deployed generation.</span>')
    else:
        headline = '<span style="color:#64748b">baseline held</span>'
        rb = ev.get("rolled_back_count", 0)
        base_disp = f"{base:.3f}" if base is not None else "—"
        if rb > 0:
            sub = (f'Original gen_0 (PSS {base_disp}) is still active — '
                   f'{rb} improvement attempt(s) were tried but none beat it, so they were '
                   f'reverted automatically. No regression shipped.')
        else:
            sub = (f'Original gen_0 (PSS {base_disp}) is still active — no improvement '
                   f'generation has been promoted yet.')
    return (
        f'<div class="verdict-card" style="border-color:{color}">'
        f'<div class="vc-team" style="color:{color}">{title}</div>'
        f'<div class="vc-headline">{headline}</div>'
        f'<div class="vc-sub">{sub}</div></div>'
    )


def _build_verdict_banner(asis_evolution: dict[str, Any] | None,
                          outer_enabled: bool = True) -> str:
    """Hero banner: the single most important question — did the platform make
    the plugged-in project measurably better, and by how much? Shown only when
    the outer (code self-improvement) loop was enabled for this battle."""
    if not outer_enabled:
        return ""
    red = (asis_evolution or {}).get("red")
    blue = (asis_evolution or {}).get("blue")
    any_improved = bool((red and red.get("improved")) or (blue and blue.get("improved")))
    if any_improved:
        verdict_txt = "The platform produced a measurable improvement"
        verdict_color = "#16a34a"
    elif red or blue:
        verdict_txt = "No net improvement yet — baseline held"
        verdict_color = "#64748b"
    else:
        return ""  # no ASIS at all → nothing to claim
    return (
        '<section class="verdict-banner">'
        f'<div class="vb-title">Did the platform improve the connected project?</div>'
        f'<div class="vb-verdict" style="color:{verdict_color}">{verdict_txt}</div>'
        '<div class="verdict-grid">'
        f'{_verdict_card(red, "red")}{_verdict_card(blue, "blue")}'
        '</div>'
        '<div class="vb-method">How this is measured: each generation is a real code '
        'rewrite of the submitted project, rebuilt in a sandbox and benchmarked over '
        'multiple seeds (mean ± σ). <strong>PSS</strong> (Partial Success Score) is the '
        'continuous fitness signal — it rises when red makes the target leak more, or '
        'when blue scrubs more harm. gen_0 = the project exactly as submitted.</div>'
        '</section>'
    )


async def _compose_report(session_id: str) -> dict[str, Any]:
    """Fetch data + narrative and render the full report HTML.

    Returns {html, report_data, narrative_data, stats}. Shared by the PDF
    endpoint and the on-disk auto-save so both produce identical output.
    """
    pool = await get_pool()
    report_data = await _get_report_data(session_id)
    narrative_data = await generate_narrative(session_id, pool)

    stats = _merge_pdf_statistics(
        report_data["statistics"],
        narrative_data.get("statistics"),
    )
    zone_insights = narrative_data.get("zone_insights", {})
    narrative_md = narrative_data.get("narrative", "")

    narrative_html = _md.markdown(
        narrative_md,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    narrative_html = _prettify_diff_blocks(narrative_html)

    asr = stats.get("attack_success_rate", 0.0)
    dr  = stats.get("defense_rate", 0.0)
    avg_harm = stats.get("avg_harmfulness_score", 0.0)
    max_harm = stats.get("max_harmfulness_score", 0.0)
    trace_appendix = _build_trace_appendix_html(report_data.get("rounds", []))

    html = _REPORT_HTML_TEMPLATE.format(
        short_id=session_id[:8],
        session_id=session_id,
        mode=report_data.get("mode") or "—",
        status=report_data.get("status") or "—",
        red_service_id=report_data.get("red_service_id") or "—",
        blue_service_id=report_data.get("blue_service_id") or "—",
        created_at=report_data.get("created_at") or "—",
        ended_at=report_data.get("ended_at") or "—",
        total_rounds=stats.get("total_rounds", 0),
        red_wins=stats.get("red_wins", 0),
        blue_wins=stats.get("blue_wins", 0),
        asr_pct=f"{asr * 100:.1f}",
        dr_pct=f"{dr * 100:.1f}",
        avg_harm=f"{avg_harm:.3f}",
        max_harm=f"{max_harm:.3f}",
        asr_color="red" if asr > 0.5 else "green",
        dr_color="green" if dr > 0.5 else "red",
        harm_color="red" if avg_harm > 0.6 else ("yellow" if avg_harm > 0.3 else "green"),
        max_harm_color="red" if max_harm > 0.7 else "yellow",
        run_config_section=_build_run_config_section(
            report_data.get("inner_loop_enabled", False),
            report_data.get("outer_loop_enabled", False)),
        verdict_banner=_build_verdict_banner(
            report_data.get("asis_evolution"),
            report_data.get("outer_loop_enabled", False)),
        asis_section=_build_asis_section(
            report_data.get("asis_evolution"),
            report_data.get("outer_loop_enabled", False)),
        phase_chart=_build_phase_chart(stats),
        zone_section=_build_zone_section(zone_insights),
        narrative_html=narrative_html,
        trace_appendix=trace_appendix,
    )
    return {
        "html": html,
        "report_data": report_data,
        "narrative_data": narrative_data,
        "stats": stats,
    }


@app.get("/v1/reports/{session_id}/pdf", response_class=_HTMLResponse)
async def get_report_pdf(session_id: str, auto_print: int = 1):
    """
    Return a print-ready HTML battle report. By default (`auto_print=1`) the
    browser's print dialog opens on load — user clicks "Save as PDF".
    Pass `?auto_print=0` for plain view.
    """
    try:
        composed = await _compose_report(session_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}")

    html = composed["html"]

    if auto_print:
        # Inject window.print() before </body> so the user sees "Save as PDF"
        # dialog as soon as the report renders. setTimeout lets fonts/styles settle.
        auto_script = (
            "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),300));</script>"
        )
        html = html.replace("</body>", f"{auto_script}</body>", 1)

    return _HTMLResponse(
        content=html,
        headers={
            "Content-Disposition": f'inline; filename="acea-report-{session_id[:8]}.html"',
            # Never let the browser serve a stale report after a regenerate —
            # this was making fixed reports still display old broken rendering.
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# ── Auto-save reports to disk ───────────────────────────────────────────────
import json as _json
from pathlib import Path as _Path

# Host-mounted output directory (see docker-compose volume). Reports are written
# here automatically at battle end so a run is never lost on a browser refresh
# and the user never has to click "Save PDF".
REPORTS_DIR = _os.environ.get("REPORTS_DIR", "/data/reports")


def _slug(s: str | None, fallback: str = "unknown") -> str:
    """Filesystem-safe short slug from a service id / name."""
    s = (s or "").strip() or fallback
    s = _re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return (s or fallback)[:40]


def _report_dir(report_data: dict[str, Any], session_id: str) -> _Path:
    """Build a tidy, collision-free directory:  <date>/<time>_<red>_vs_<blue>_<short>/

    Date/time come from the session's own timestamps (deterministic — not wall
    clock), so reruns of the same session land in the same folder.
    """
    iso = (report_data.get("ended_at") or report_data.get("created_at") or "") or ""
    # iso looks like 2026-06-25T08:58:35.540601+00:00
    date_part = iso[:10] if len(iso) >= 10 else "undated"
    time_part = iso[11:19].replace(":", "") if len(iso) >= 19 else "000000"
    red = _slug(report_data.get("red_service_id"), "red")
    blue = _slug(report_data.get("blue_service_id"), "blue")
    name = f"{time_part}_{red}_vs_{blue}_{session_id[:8]}"
    return _Path(REPORTS_DIR) / date_part / name


def _write_report_files(session_id: str, composed: dict[str, Any]) -> dict[str, Any]:
    """Write report.html + report.json + narrative.md to the per-session folder."""
    rd = composed["report_data"]
    nd = composed["narrative_data"]
    out = _report_dir(rd, session_id)
    out.mkdir(parents=True, exist_ok=True)

    (out / "report.html").write_text(composed["html"], encoding="utf-8")
    (out / "narrative.md").write_text(nd.get("narrative") or "", encoding="utf-8")
    bundle = {
        "session_id": session_id,
        "mode": rd.get("mode"),
        "status": rd.get("status"),
        "red_service_id": rd.get("red_service_id"),
        "blue_service_id": rd.get("blue_service_id"),
        "created_at": rd.get("created_at"),
        "ended_at": rd.get("ended_at"),
        "statistics": composed["stats"],
        "zone_insights": nd.get("zone_insights"),
        "asis_evolution": rd.get("asis_evolution"),
        "rounds": rd.get("rounds"),
    }
    (out / "report.json").write_text(_json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    return {"dir": str(out), "files": ["report.html", "report.json", "narrative.md"]}


@app.post("/v1/reports/{session_id}/save")
async def save_report(session_id: str):
    """
    Render and persist the full report to disk (report.html / report.json /
    narrative.md) under REPORTS_DIR/<date>/<session>/. Called automatically by
    arena-core at battle end — no user action required.
    """
    try:
        composed = await _compose_report(session_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Report compose failed: {exc}")
    try:
        saved = _write_report_files(session_id, composed)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Report save failed: {exc}")
    return {"saved": True, **saved}
