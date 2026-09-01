import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import retry_util as ru


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_succeeds_first_try():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    assert _run(ru.retry_call(fn, attempts=3, backoff=0)) == "ok"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return "recovered"

    assert _run(ru.retry_call(fn, attempts=3, backoff=0)) == "recovered"
    assert calls["n"] == 3


def test_raises_after_exhausting_attempts():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ConnectionError("gone")

    try:
        _run(ru.retry_call(fn, attempts=3, backoff=0))
        assert False, "should have raised"
    except ConnectionError as exc:
        assert str(exc) == "gone"
    assert calls["n"] == 3


def test_attempts_clamped_to_one():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise RuntimeError("x")

    try:
        _run(ru.retry_call(fn, attempts=0, backoff=0))
    except RuntimeError:
        pass
    assert calls["n"] == 1
