"""Small retry helper for outbound adapter calls.

Kept dependency-free (asyncio only) so it is unit-testable in isolation and can
wrap any async call that may hit a transient network/adapter blip. A persistent
failure re-raises the last exception, which the caller treats as a disconnect.
"""
import asyncio


async def retry_call(fn, *, attempts=3, backoff=2.0):
    """Await `fn()`, retrying on ANY exception up to `attempts` times with linear
    backoff (`backoff * attempt_index` seconds between tries). Returns fn()'s
    result on success; re-raises the last exception if every attempt fails.

    `attempts` is clamped to >= 1. `backoff=0` disables the delay (used in tests).
    """
    attempts = max(1, int(attempts))
    last = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — deliberately broad; caller decides
            last = exc
            if i < attempts - 1 and backoff:
                await asyncio.sleep(backoff * (i + 1))
    raise last
