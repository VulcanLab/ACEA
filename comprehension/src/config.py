"""Comprehension-service settings.

Model names and project paths come from the environment (.env). Nothing here is
hardcoded: the platform must work with whatever red/blue project is plugged in.
"""
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LiteLLM ───────────────────────────────────────────────────────────────
    litellm_base_url: str    = ""
    litellm_api_key: str     = ""

    # Recon Analyst models (per side) — the third assisting model per team.
    # Either may be empty, in which case RECON_MODEL is used for both.
    red_recon_model: str     = ""
    blue_recon_model: str    = ""
    recon_model: str         = Field("", alias="RECON_MODEL")

    # ── In-container mount points (fixed by docker-compose) ───────────────────
    # Both are mounted READ-ONLY. This service only ever reads a bounded sample
    # of a project's source; it never writes to one.
    red_project_path: str    = "/projects/red"
    blue_project_path: str   = "/projects/blue"

    # Direct ASAP URLs (in-network service names), used to read each side's
    # declared /health capabilities. A project reachable at neither is still
    # profiled, from whatever the protocol reported.
    red_adapter_direct_url: str  = Field("", alias="RED_ADAPTER_DIRECT_URL")
    blue_adapter_direct_url: str = Field("", alias="BLUE_ADAPTER_DIRECT_URL")

    service_port: int        = 8010

    class Config:
        env_file = ".env"
        extra = "ignore"
        populate_by_name = True


settings = Settings()
