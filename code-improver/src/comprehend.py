"""
Pre-battle project comprehension (READ-ONLY).

Before a battle, the platform reads a plugged-in project's ASAP /health
capabilities and — when its source is mounted — a bounded, read-only sample of
its code, then asks an LLM to summarise the project's architecture and propose a
strategy SUITED TO THAT PROJECT. This lets the arena work WITH whatever the
project actually does instead of assuming a fixed approach.

Guarantees:
  * Purely analytical. Never writes, patches, or rebuilds anything.
  * The produced strategy is ADVISORY — it is surfaced as hints + shown in the
    report; it never forces the adapter to change what it does.
  * Works for URL-only external adapters too: if no source is mounted, the
    profile is built from the protocol-declared capabilities alone.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
import litellm

log = logging.getLogger(__name__)

# Bounded read: which files are worth sampling, and how much.
_READ_EXTS = (".py", ".md", ".toml", ".txt", ".yaml", ".yml", ".json", ".cfg")
_SKIP_DIRS = ("__pycache__", ".git", "node_modules", ".venv", "venv", "models", "assets", "tests")
_MAX_FILES = 12
_MAX_FILE_CHARS = 6000
_MAX_TOTAL_CHARS = 40000


def _sample_source(project_root: str) -> list[tuple[str, str]]:
    """Return [(relpath, content)] for a bounded, read-only sample of the project.
    Prioritises adapter entry points and docs; caps count + size."""
    if not project_root or not os.path.isdir(project_root):
        return []
    picked: list[tuple[str, str]] = []
    total = 0

    def _priority(name: str) -> int:
        n = name.lower()
        if "arena_adapter" in n:            return 0
        if n in ("readme.md", "readme.txt"): return 1
        if n.endswith(".toml") or n.endswith(".cfg"): return 2
        if n.endswith(".py"):               return 3
        return 4

    candidates: list[str] = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.startswith(".") or not f.endswith(_READ_EXTS):
                continue
            candidates.append(os.path.join(root, f))
    candidates.sort(key=lambda p: (_priority(os.path.basename(p)), p))

    for abs_p in candidates:
        if len(picked) >= _MAX_FILES or total >= _MAX_TOTAL_CHARS:
            break
        try:
            with open(abs_p, encoding="utf-8") as fh:
                content = fh.read(_MAX_FILE_CHARS)
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(abs_p, project_root)
        picked.append((rel, content))
        total += len(content)
    return picked


async def _fetch_health(adapter_url: str) -> dict[str, Any]:
    if not adapter_url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{adapter_url.rstrip('/')}/health")
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.info("comprehend: health fetch failed for %s: %s", adapter_url, exc)
    return {}


_SCHEMA_HINT = """Return ONLY a JSON object with these keys:
{
  "architecture_summary": "2-3 sentences: how this project appears to work",
  "declared_capabilities": "one sentence summarising its ASAP /health capabilities",
  "inferred_approach": "a short free-form label for its apparent method (you choose the words; do not pick from a fixed list)",
  "suggested_strategy": "2-3 sentences of advisory strategy SUITED to how THIS project works — for the arena to leverage, never to force the project to change",
  "focus_areas": ["short", "advisory", "focus", "points"],
  "notes": "any caveats, unknowns, or risks (one sentence)"
}"""


async def comprehend_project(
    team: str,
    project_root: str,
    adapter_url: str,
    model: str,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    """Produce a read-only strategy profile for one side's plugged-in project."""
    health = await _fetch_health(adapter_url)
    caps = health.get("capabilities") if isinstance(health.get("capabilities"), dict) else {}
    source = _sample_source(project_root)
    source_present = bool(source)

    src_block = "\n\n".join(f"### {rel}\n```\n{content}\n```" for rel, content in source) \
        if source_present else "(no source mounted — analyse from declared capabilities only)"

    system_prompt = (
        f"You are the platform's pre-battle analyst for the {team.upper()} side. "
        "You READ a plugged-in adversarial project and summarise how it works so the "
        "arena can craft a strategy SUITED to it. You never modify the project and you "
        "never assume it must work a particular way — describe what it ACTUALLY does. "
        "Your strategy output is advisory only."
    )
    user_prompt = (
        f"ASAP /health of the {team} adapter:\n{json.dumps(health, indent=2)[:2000]}\n\n"
        f"Read-only source sample:\n{src_block[:30000]}\n\n{_SCHEMA_HINT}"
    )

    effective = f"openai/{model}" if base_url else model
    kwargs: dict[str, Any] = {}
    # Analysing adversarial red/blue code trips default content filters on some
    # providers (empty response). Disable safety for Gemini/Gemma — this is a
    # controlled research analysis, not generation of harmful content.
    ml = model.lower()
    if "gemini" in ml or "gemma" in ml:
        kwargs["safety_settings"] = [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
    try:
        resp = await litellm.acompletion(
            model=effective,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
            api_base=base_url or None,
            api_key=api_key or None,
            **kwargs,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip code fences if present.
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw
            raw = raw[raw.find("{"):]
        start, end = raw.find("{"), raw.rfind("}")
        profile = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
    except Exception as exc:
        log.warning("comprehend: LLM analysis failed for %s: %s", team, exc)
        profile = {}

    # Fallback: if the LLM returned nothing usable (empty / filtered / truncated),
    # synthesise a minimal profile from the protocol-declared capabilities so the
    # comprehension is never blank and the battle still gets an advisory nudge.
    if not profile.get("architecture_summary") and not profile.get("suggested_strategy"):
        cap_keys = ", ".join(k for k in caps.keys()) or "none declared"
        approach = caps.get("attack_type") or caps.get("defense_type") or "unspecified"
        role_word = "attack" if team == "red" else "defense"
        profile = {
            "architecture_summary": (
                f"Analysed from ASAP capabilities only (declared: {cap_keys})."
                if not source_present else
                f"Source sampled; declared capabilities: {cap_keys}."
            ),
            "declared_capabilities": cap_keys,
            "inferred_approach": str(approach),
            "suggested_strategy": (
                f"Work with this project's own {role_word} approach ('{approach}'); "
                f"probe along its declared capabilities and adapt from round feedback."
            ),
            "focus_areas": [k for k in caps.keys() if k != "is_platform_default"],
            "notes": "LLM profile unavailable; using capability-derived summary.",
        }

    return {
        "team": team,
        "source_analyzed": source_present,
        "capabilities": caps,
        "architecture_summary": profile.get("architecture_summary", ""),
        "declared_capabilities": profile.get("declared_capabilities", ""),
        "inferred_approach": profile.get("inferred_approach", ""),
        "suggested_strategy": profile.get("suggested_strategy", ""),
        "focus_areas": profile.get("focus_areas", []) if isinstance(profile.get("focus_areas"), list) else [],
        "notes": profile.get("notes", ""),
    }
