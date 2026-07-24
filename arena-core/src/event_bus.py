import json
from datetime import datetime
from typing import Any
import redis.asyncio as aioredis
from config import settings

_redis: aioredis.Redis | None = None

# Events that are also written to the global fan-in stream for cross-session consumers (ASIS)
_GLOBAL_STREAM_EVENTS = {"improvement.triggered", "battle.complete"}


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def publish_event(session_id: str, event_type: str, data: dict[str, Any]) -> None:
    r = await get_redis()
    payload = json.dumps({
        "event_type": event_type,
        "session_id": session_id,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    })
    await r.xadd(f"arena:events:{session_id}", {"payload": payload}, maxlen=1000)
    # Fan-in: write select events to global stream for cross-session consumers (ASIS)
    if event_type in _GLOBAL_STREAM_EVENTS:
        await r.xadd("arena:events:global", {"payload": payload}, maxlen=5000)


async def read_events(session_id: str, last_id: str = "0") -> list[dict]:
    r = await get_redis()
    results = await r.xread({f"arena:events:{session_id}": last_id}, count=100, block=1000)
    events = []
    if results:
        for _stream, messages in results:
            for msg_id, fields in messages:
                events.append({"id": msg_id, "payload": fields["payload"]})
    return events


async def read_global_events(last_id: str = "0") -> list[dict]:
    """Read from the global fan-in stream (cross-session events: ASIS, battle.complete)."""
    r = await get_redis()
    results = await r.xread({"arena:events:global": last_id}, count=100, block=1000)
    events = []
    if results:
        for _stream, messages in results:
            for msg_id, fields in messages:
                events.append({"id": msg_id, "payload": fields["payload"]})
    return events


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
