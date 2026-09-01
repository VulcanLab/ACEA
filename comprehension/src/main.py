"""Pre-battle project comprehension.

This service reads a plugged-in project and summarises it, so the arena can work
with whatever the project actually does instead of assuming a fixed approach. It
is READ-ONLY: nothing here writes to, patches, rebuilds or restarts a
participant.
"""
import litellm_safe  # noqa: F401  — monkey-patch Gemini safety_settings
import logging

from fastapi import FastAPI, HTTPException

from config import settings

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ACEA Project Comprehension", version="3.0.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "comprehension"}


@app.post("/v1/comprehend")
async def comprehend(team: str):
    """READ-ONLY analysis of one side's plugged-in project. Returns a strategy
    profile (architecture summary + advisory strategy). Never modifies the
    project. `team` is 'red' or 'blue'."""
    from comprehend import comprehend_project
    if team == "red":
        project_path, adapter_url = settings.red_project_path, settings.red_adapter_direct_url
        recon_model = settings.red_recon_model or settings.recon_model
    elif team == "blue":
        project_path, adapter_url = settings.blue_project_path, settings.blue_adapter_direct_url
        recon_model = settings.blue_recon_model or settings.recon_model
    else:
        raise HTTPException(400, "team must be 'red' or 'blue'")
    return await comprehend_project(
        team=team,
        project_root=project_path,
        adapter_url=adapter_url,
        model=recon_model,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
    )
