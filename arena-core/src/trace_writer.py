import logging
import asyncpg
from config import settings

_pool: asyncpg.Pool | None = None
_log = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.postgres_uri, min_size=2, max_size=10)
    return _pool


async def write_trace(
    session_id: str,
    round_n: int,
    attack_payload: str,
    attack_type: str,
    attack_confidence: float,
    defense_decision: str,
    defense_confidence: float,
    defense_reason: str,
    *,
    final_payload: str,
    raw_target_response: str | None,
    final_target_response: str | None,
    output_was_modified: bool = False,
    output_modification_reason: str = "",
    red_success: bool,
    blue_success: bool,
    harm_score: float,
    judge_reasoning: str,
    raw_harm_score: float | None = None,
    attacker_refused: bool = False,
) -> None:
    """Persist one round trace.

    - final_payload          — prompt actually sent INTO Target AI
    - raw_target_response    — model output before blue output filter
    - final_target_response  — text after filter (what red-facing pipeline uses)
      Stored in column `target_response` for backwards compatibility with reports.
    """
    pool = await get_pool()
    _log.debug("write_trace session=%s round=%d attack_type=%s", session_id, round_n, attack_type)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO execution_traces
              (session_id, round, attack_payload, attack_type, attack_confidence,
               defense_decision, defense_confidence, defense_reason,
               final_payload, raw_target_response, target_response,
               output_was_modified, output_modification_reason,
               red_success, blue_success, harmfulness_score,
               judge_reasoning, tokens_used, raw_harmfulness_score,
               attacker_refused)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
            """,
            session_id,
            round_n,
            attack_payload,
            attack_type,
            attack_confidence,
            defense_decision,
            defense_confidence,
            defense_reason,
            final_payload,
            raw_target_response,
            final_target_response,
            output_was_modified,
            output_modification_reason or None,
            red_success,
            blue_success,
            harm_score,
            judge_reasoning,
            0,
            raw_harm_score if raw_harm_score is not None else harm_score,
            attacker_refused,
        )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
