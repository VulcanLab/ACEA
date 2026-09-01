"""Actions the target can take, so an engagement can be about more than disclosure.

With retrieval alone there is exactly one thing an attacker can attempt: get the target
to say something it holds. Real assistants also *do* things, and an engagement may be
about making one act outside its authority. These actions give that a surface.

Three pieces, kept apart on purpose:

    registry    what actions exist, loaded from JSON toolpacks rather than written here
    guard       whether a given call may take effect, declared by the engagement
    this module the turn-scoped record of what was attempted and what took effect

The record is the output that matters. It distinguishes a call the target *made* from a
call that was *permitted*, because those are different failures: a target that tries to
act outside its authority has already failed, even when the boundary stops it. Scoring
only the final state would score that as a clean round.

Nothing here reaches outside the process. State is an in-memory copy of what a pack
declares and resets between runs.
"""
from __future__ import annotations

from . import guard, registry
from .guard import Policy
from .registry import Catalogue, Pack, PackError, Tool, load_catalogue, parse_pack

__all__ = [
    "Catalogue", "Pack", "PackError", "Policy", "Tool",
    "available", "catalogue", "drain_invocations", "invoke", "load_catalogue",
    "parse_pack", "reload_catalogue", "reset_state", "specifications",
]

# The live catalogue. Loaded lazily so importing this package never depends on a
# directory being present, which keeps the unit tests independent of deployment layout.
_CATALOGUE: Catalogue | None = None

# Everything attempted this turn, newest last. Drained per turn so a round reports
# exactly what it provoked.
_ATTEMPTS: list[dict] = []


def catalogue() -> Catalogue:
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = load_catalogue()
    return _CATALOGUE


def reload_catalogue(packs_dir=None) -> Catalogue:
    """Re-read the packs. Used by the tests, and by a deployment that mounts its own
    catalogue after the service is already up."""
    global _CATALOGUE
    _CATALOGUE = load_catalogue(packs_dir)
    return _CATALOGUE


def available() -> list[str]:
    """Every action name the catalogue holds.

    The platform asks rather than assuming: no component outside this service should
    carry a list of action names, or adding a toolpack would mean editing the platform.
    """
    return catalogue().names()


def specifications(enabled=None):
    """Action definitions in the shape a model expects, for the enabled subset.

    `enabled=None` offers everything, which is what an engagement means when it turns
    the capability on without naming actions.
    """
    cat = catalogue()
    names = cat.names() if enabled is None else [n for n in enabled if n in cat.tools]
    return [
        {"type": "function",
         "function": {"name": cat.tools[n].name,
                      "description": cat.tools[n].description,
                      "parameters": cat.tools[n].parameters}}
        for n in names
    ]


def invoke(name: str, arguments: dict, policy=None) -> str:
    """Attempt one action; return the text the target reads back.

    Records the attempt either way. An unknown action is reported rather than raised so
    the target can recover in-conversation instead of the round dying on a hallucinated
    action name.
    """
    cat = catalogue()
    tool = cat.tools.get(name)
    if tool is None:
        _ATTEMPTS.append({"name": name, "arguments": dict(arguments or {}),
                          "verdict": "unknown", "executed": False,
                          "result": f"No action named {name!r} is available.",
                          "mutating": False, "effect": "", "risk": "",
                          "rule": "", "pack": ""})
        return f"No action named {name!r} is available."

    pol = policy if isinstance(policy, Policy) else Policy.parse(policy)
    decision = guard.evaluate(pol, tool, arguments or {})

    if decision.allowed:
        result = registry.execute(cat, tool, arguments or {})
    else:
        result = decision.reason

    _ATTEMPTS.append({
        "name": tool.name,
        "arguments": dict(arguments or {}),
        "verdict": decision.verdict,
        "executed": decision.allowed,
        "result": result,
        "mutating": tool.mutating,
        "effect": tool.effect,
        "risk": tool.risk,
        "rule": decision.rule,
        "pack": tool.pack,
    })
    return result


def drain_invocations():
    """Return and clear the actions attempted since the last call.

    The name is kept from before the boundary existed; callers that only look at
    executed actions can filter on the `executed` flag.
    """
    global _ATTEMPTS
    done, _ATTEMPTS = _ATTEMPTS, []
    return done


def reset_state():
    """Restore every pack's declared starting state and clear the record, so one battle
    cannot bleed into the next."""
    catalogue().reset()
    drain_invocations()
