import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_http_client():
    """Context manager that patches httpx.AsyncClient with a configurable mock."""
    return AsyncMock


def _make_response(status_code: int, json_data: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_validate_adapter_red_success():
    from asap_validator import validate_adapter

    health_resp = _make_response(200, {"status": "ok", "service": "t", "asap_version": "1.0"})
    canary_resp = _make_response(200, {"attack_payload": "test", "attack_type": "indirect", "confidence": 0.5})
    malformed_resp = _make_response(422, {})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=health_resp)
    mock_client.post = AsyncMock(side_effect=[canary_resp, malformed_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("asap_validator.httpx.AsyncClient", return_value=mock_client):
        ok, err = await validate_adapter("http://fake-red:9001", "red")

    assert ok is True
    assert err == ""


@pytest.mark.asyncio
async def test_validate_adapter_blue_success():
    from asap_validator import validate_adapter

    health_resp = _make_response(200, {"status": "ok", "service": "t", "asap_version": "1.0"})
    canary_resp = _make_response(200, {"decision": "block", "reason": "test block", "confidence": 0.9})
    malformed_resp = _make_response(422, {})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=health_resp)
    mock_client.post = AsyncMock(side_effect=[canary_resp, malformed_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("asap_validator.httpx.AsyncClient", return_value=mock_client):
        ok, err = await validate_adapter("http://fake-blue:9002", "blue")

    assert ok is True
    assert err == ""


@pytest.mark.asyncio
async def test_validate_adapter_wrong_asap_version():
    from asap_validator import validate_adapter

    health_resp = _make_response(200, {"status": "ok", "service": "t", "asap_version": "0.9"})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=health_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("asap_validator.httpx.AsyncClient", return_value=mock_client):
        ok, err = await validate_adapter("http://fake:9001", "red")

    assert ok is False
    assert "asap_version" in err


@pytest.mark.asyncio
async def test_validate_adapter_health_failure():
    from asap_validator import validate_adapter

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("asap_validator.httpx.AsyncClient", return_value=mock_client):
        ok, err = await validate_adapter("http://dead:9999", "red")

    assert ok is False
    assert "health check failed" in err
