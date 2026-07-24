"""Read/write adapter_generations rows."""
import asyncpg
from typing import Optional
from config import settings

_pool: Optional[asyncpg.Pool] = None


def _pg_safe_text(value: Optional[str]) -> Optional[str]:
    """Strip NUL bytes (0x00) from text bound for a Postgres TEXT column.

    Postgres cannot store 0x00 in a text/varchar value and raises
    CharacterNotInRepertoireError. Generated diffs can pick up NUL bytes when
    the connected project contains binary or NUL-bearing files, so scrub them
    before insert rather than aborting the whole improvement cycle.
    """
    if value is None:
        return None
    return value.replace("\x00", "")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.postgres_uri, min_size=1, max_size=4)
    return _pool


async def get_active_gen(adapter_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM adapter_generations WHERE adapter_id=$1 AND is_active=TRUE",
            adapter_id)
    return dict(row) if row else None


async def get_gen_history(adapter_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, gen_number, benchmark_asr, benchmark_dr, ast_valid, "
            "canary_passed, is_active, rollback_reason, created_at "
            "FROM adapter_generations WHERE adapter_id=$1 ORDER BY gen_number ASC",
            adapter_id)
    return [dict(r) for r in rows]


async def insert_gen0(
    adapter_id: str, team: str,
    benchmark_asr: Optional[float] = None,
    benchmark_dr: Optional[float] = None,
    benchmark_pss: Optional[float] = None,
    trigger_session_id: Optional[str] = None,
) -> str:
    """Record the original (pre-improvement) adapter as generation 0.

    Stamps gen_0 with the triggering battle's measured ASR/DR/PSS so the
    report has a true 'before' datapoint to compare improved generations
    against. If gen_0 already exists but was created without a benchmark
    (older runs), backfill it once.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, benchmark_pss FROM adapter_generations "
            "WHERE adapter_id=$1 AND gen_number=0", adapter_id)
        if existing:
            # Backfill when the stored baseline is missing OR a non-informative
            # 0.0 (a failed/empty benchmark, or blue's harm-reduction collapsing
            # to 0) and we now have a positive measurement. Treating 0.0 as
            # "unmeasured" stops a transient empty first run from pinning the
            # report's 'before' baseline at zero forever.
            stored = existing["benchmark_pss"]
            if (stored is None or stored == 0) and benchmark_pss:
                await conn.execute(
                    "UPDATE adapter_generations SET benchmark_asr=$2, benchmark_dr=$3, "
                    "benchmark_pss=$4, benchmark_session_id=$5 WHERE id=$1",
                    existing["id"], benchmark_asr, benchmark_dr, benchmark_pss,
                    trigger_session_id)
            return str(existing["id"])
        row_id = await conn.fetchval(
            "INSERT INTO adapter_generations "
            "(adapter_id,team,gen_number,ast_valid,canary_passed,is_active,"
            " benchmark_asr,benchmark_dr,benchmark_pss,benchmark_session_id)"
            " VALUES ($1,$2,0,TRUE,TRUE,TRUE,$3,$4,$5,$6) RETURNING id",
            adapter_id, team, benchmark_asr, benchmark_dr, benchmark_pss,
            trigger_session_id)
    return str(row_id)


async def insert_candidate_gen(
    adapter_id: str, team: str, gen_number: int,
    parent_gen_id: Optional[str], patch_diff: str,
    trigger_session_id: Optional[str],
) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row_id = await conn.fetchval(
            "INSERT INTO adapter_generations "
            "(adapter_id,team,gen_number,parent_gen_id,patch_diff,ast_valid,canary_passed,is_active,trigger_session_id)"
            " VALUES ($1,$2,$3,$4,$5,FALSE,FALSE,FALSE,$6) RETURNING id",
            adapter_id, team, gen_number, parent_gen_id,
            _pg_safe_text(patch_diff), trigger_session_id)
    return str(row_id)


async def mark_validated(gen_id: str, ast_valid: bool, canary_passed: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE adapter_generations SET ast_valid=$1, canary_passed=$2 WHERE id=$3",
            ast_valid, canary_passed, gen_id)


async def promote_gen(
    gen_id: str, adapter_id: str,
    benchmark_asr: float, benchmark_dr: float, benchmark_session_id: str,
    benchmark_pss: float = 0.0, benchmark_pss_std: float = 0.0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE adapter_generations SET is_active=FALSE WHERE adapter_id=$1 AND is_active=TRUE",
                adapter_id)
            await conn.execute(
                "UPDATE adapter_generations "
                "SET is_active=TRUE,benchmark_asr=$1,benchmark_dr=$2,benchmark_session_id=$3,"
                "benchmark_pss=$4,benchmark_pss_std=$5 "
                "WHERE id=$6",
                benchmark_asr, benchmark_dr, benchmark_session_id, benchmark_pss,
                benchmark_pss_std, gen_id)


async def rollback_gen(gen_id: str, reason: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE adapter_generations SET rollback_reason=$1 WHERE id=$2",
            _pg_safe_text(reason), gen_id)
