"""
ASAP adapter validation.

validate_adapter() is called at registration time. Returns:
    (is_valid: bool, error_message: str, capabilities: dict)

Capabilities come from the adapter's /health response. Platform uses them
to skip endpoints the adapter declares it doesn't implement (e.g. blue
adapters without output_guard).
"""
import logging
from typing import Literal

import httpx

log = logging.getLogger(__name__)

REQUIRED_ASAP_VERSION = "1.0"
HEALTH_TIMEOUT = 10.0
CANARY_TIMEOUT = 30.0


async def validate_adapter(
    url: str,
    team: Literal["red", "blue"],
) -> tuple[bool, str, dict]:
    capabilities: dict = {}
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
        # Step 1 — health check + capability discovery
        try:
            r = await client.get(f"{url}/health")
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return False, f"health check failed: {exc}", capabilities

        if data.get("status") != "ok":
            return False, f"health status is not 'ok': {data.get('status')}", capabilities

        if data.get("asap_version") != REQUIRED_ASAP_VERSION:
            return False, (
                f"asap_version mismatch: expected {REQUIRED_ASAP_VERSION!r}, "
                f"got {data.get('asap_version')!r}"
            ), capabilities

        # Capabilities (optional — adapter may not declare any)
        if isinstance(data.get("capabilities"), dict):
            capabilities = data["capabilities"]

        # Step 2 — canary request
        try:
            if team == "red":
                if capabilities.get("supports_attack_generation", True):
                    r2 = await client.post(
                        f"{url}/v1/generate-attack",
                        json={"session_id": "__validator__", "round": 1},
                        timeout=CANARY_TIMEOUT,
                    )
                    if r2.status_code != 200:
                        return False, f"generate-attack canary returned {r2.status_code}: {r2.text[:200]}", capabilities
                    resp = r2.json()
                    if not resp.get("attack_payload"):
                        return False, "generate-attack response missing attack_payload", capabilities
                    if not resp.get("attack_type"):
                        return False, "generate-attack response missing attack_type", capabilities
            else:
                # Blue: probe whichever guard(s) the adapter declares.
                # By default ASAP requires evaluate-defense, but a pure
                # output-guard adapter may declare supports_input_guard=False.
                supports_input  = capabilities.get("supports_input_guard", True)
                supports_output = capabilities.get("supports_output_guard", False)
                if not supports_input and not supports_output:
                    return False, "blue adapter declares neither input nor output guard — must support at least one", capabilities

                if supports_input:
                    r2 = await client.post(
                        f"{url}/v1/evaluate-defense",
                        json={"session_id": "__validator__", "round": 1, "attack_payload": "test"},
                        timeout=CANARY_TIMEOUT,
                    )
                    if r2.status_code != 200:
                        return False, f"evaluate-defense canary returned {r2.status_code}: {r2.text[:200]}", capabilities
                    resp = r2.json()
                    if resp.get("decision") not in ("block", "allow"):
                        return False, f"invalid decision: {resp.get('decision')!r}", capabilities
                    if not resp.get("reason"):
                        return False, "evaluate-defense response missing reason", capabilities

                if supports_output:
                    r3 = await client.post(
                        f"{url}/v1/filter-output",
                        json={
                            "session_id": "__validator__", "round": 1,
                            "attack_payload": "test", "raw_response": "hello",
                        },
                        timeout=CANARY_TIMEOUT,
                    )
                    if r3.status_code != 200:
                        return False, f"filter-output canary returned {r3.status_code}: {r3.text[:200]}", capabilities
                    resp3 = r3.json()
                    if "final_response" not in resp3:
                        return False, "filter-output response missing final_response", capabilities
        except Exception as exc:
            return False, f"canary request failed: {exc}", capabilities

    return True, "", capabilities
