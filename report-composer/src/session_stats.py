"""
Merge PostgreSQL battle_sessions aggregates with execution_traces-derived stats.

arena-core persists red_wins / blue_wins / current_round during the battle; traces
might be missing, or legacy rows might have lost boolean outcomes while the session
ledger stayed correct — we reconcile so PDF / JSON stats are not all zeros.
"""

from typing import Any, Mapping


def merge_trace_statistics_with_session(
    session_row: Mapping[str, Any],
    *,
    traces_count: int,
    trace_statistics: dict[str, Any],
) -> dict[str, Any]:
    """
    Start from trace-derived aggregates, then fill wins / total_rounds / rates from
    battle_sessions when traces are missing or outcomes are absent (NULL / legacy).

    Harm metrics stay from traces when present; session rows do not store harm.
    """
    out = dict(trace_statistics)

    rw_s = int(session_row.get("red_wins") or 0)
    bw_s = int(session_row.get("blue_wins") or 0)
    cur_s = int(session_row.get("current_round") or 0)
    session_adjudicated = rw_s + bw_s

    trace_total = int(out.get("total_rounds") or 0)
    trace_rw = int(out.get("red_wins") or 0)
    trace_bw = int(out.get("blue_wins") or 0)
    trace_adjudicated = trace_rw + trace_bw

    def _apply_session_led_wins(total: int) -> None:
        if total <= 0:
            return
        out["total_rounds"] = total
        out["red_wins"] = rw_s
        out["blue_wins"] = bw_s
        out["attack_success_rate"] = round(rw_s / total, 4) if total else 0.0
        out["defense_rate"] = round(bw_s / total, 4) if total else 0.0

    # ── A) No trace rows — ledger / current_round only
    if traces_count == 0:
        total = session_adjudicated if session_adjudicated > 0 else cur_s
        if total <= 0:
            return out
        _apply_session_led_wins(total)
        return out

    # ── B) Traces exist but every round scored 0/0 wins while Postgres has counts
    # (bad migration, booleans NULL, or pre-fix writer bug).
    if trace_adjudicated == 0 and session_adjudicated > 0:
        denom = max(trace_total, traces_count, session_adjudicated, cur_s)
        _apply_session_led_wins(max(denom, 1))
        return out

    # ── C) Session progressed further than we could aggregate from broken rows
    if trace_total == 0 and traces_count > 0 and session_adjudicated > 0:
        denom = max(traces_count, session_adjudicated, cur_s)
        _apply_session_led_wins(max(denom, 1))
        return out

    return out
