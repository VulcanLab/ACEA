# adapter-sdk/src/acea_sdk/server.py
import uvicorn
from collections.abc import Callable
from fastapi import FastAPI
from .schemas import (
    AttackRequest, AttackResponse,
    DefenseRequest, DefenseResponse,
    FilterOutputRequest, FilterOutputResponse,
    HealthResponse,
)


class ASAPServer:
    """
    ASAP (Adversarial Self-Improvement Protocol) adapter server.

    Registers the three ACEA adapter endpoints on a FastAPI app:
      - POST /v1/generate-attack      (red team only)
      - POST /v1/evaluate-defense     (blue team only)
      - POST /v1/filter-output        (blue team only, optional)

    Usage:
        server = ASAPServer(team="red", service_name="my-red-adapter")

        @server.on_generate_attack
        async def handle(req: AttackRequest) -> AttackResponse:
            ...

        server.run()
    """

    def __init__(self, team: str, service_name: str, port: int = 9001,
                 version: str = "1.0.0", capabilities: list[str] | None = None):
        if team not in ("red", "blue"):
            raise ValueError("team must be 'red' or 'blue'")
        self.team = team
        self.port = port
        self.app = FastAPI(title=f"ASAP {team} adapter: {service_name}")
        self._service_name = service_name
        self._version = version
        self._capabilities = capabilities or []
        self._attack_handler: Callable | None = None
        self._defense_handler: Callable | None = None
        self._filter_handler: Callable | None = None
        self._register_health()

    def _register_health(self) -> None:
        @self.app.get("/health", response_model=HealthResponse)
        async def health() -> HealthResponse:
            return HealthResponse(
                service=self._service_name,
                version=self._version,
                capabilities=self._capabilities,
            )

    def on_generate_attack(self, fn: Callable) -> Callable:
        """Decorator — register the red-team attack generator (POST /v1/generate-attack)."""
        self._attack_handler = fn

        @self.app.post("/v1/generate-attack", response_model=AttackResponse)
        async def generate_attack(req: AttackRequest) -> AttackResponse:
            return await fn(req)

        return fn

    def on_evaluate_defense(self, fn: Callable) -> Callable:
        """Decorator — register the blue-team input evaluator (POST /v1/evaluate-defense)."""
        self._defense_handler = fn

        @self.app.post("/v1/evaluate-defense", response_model=DefenseResponse)
        async def evaluate_defense(req: DefenseRequest) -> DefenseResponse:
            return await fn(req)

        return fn

    def on_filter_output(self, fn: Callable) -> Callable:
        """
        Decorator — register the blue-team output filter (POST /v1/filter-output).

        Optional but recommended for blue adapters. Called after Target AI responds;
        lets blue inspect and sanitize the raw AI output before it reaches red.
        If this endpoint is absent (returns HTTP 404), the arena passes through
        the raw Target AI response unchanged.

        Example:
            @server.on_filter_output
            async def filter(req: FilterOutputRequest) -> FilterOutputResponse:
                safe = redact_pii(req.raw_response)
                return FilterOutputResponse(
                    final_response=safe,
                    was_modified=(safe != req.raw_response),
                    modification_reason="PII redacted" if safe != req.raw_response else "",
                )
        """
        self._filter_handler = fn

        @self.app.post("/v1/filter-output", response_model=FilterOutputResponse)
        async def filter_output(req: FilterOutputRequest) -> FilterOutputResponse:
            return await fn(req)

        return fn

    def run(self) -> None:
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)
