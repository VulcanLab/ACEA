"""The catalogue of actions a target can take, loaded from data rather than written here.

An engagement decides what the target is *able* to do. If that catalogue lived in this
file, every new engagement shape would need a code change, and the platform would be
quietly asserting that account servicing is what adversarial testing is about. So a
toolpack is a JSON document: it declares some state, and some actions over that state.
Drop a pack in the toolpacks directory and the target can be given those actions.

Nothing here executes code supplied in a pack. An action declares an *operation kind*
from a fixed vocabulary, and this module carries it out against an in-process copy of
the pack's state. That copy resets between runs, so one engagement cannot bleed into the
next, and nothing reaches outside the process. The point is an auditable record of what
the target was persuaded to do, not a real side effect.

Operation kinds:
    state_read      read one entry out of a collection by an argument
    state_list      report the keys of a collection
    state_write     overwrite one entry
    state_delete    remove one entry
    state_transfer  move a numeric amount between two entries
    emit            no state change; report that something was dispatched
"""
from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("target-ai.tools")

# Where packs are looked for. Overridable so a deployment can mount its own catalogue
# without rebuilding the image.
PACKS_DIR = Path(os.environ.get("TOOLPACKS_DIR", Path(__file__).resolve().parent.parent / "toolpacks"))

_EFFECTS = ("read", "write", "external")
_RISKS = ("info", "low", "medium", "high", "critical")
_KINDS = ("state_read", "state_list", "state_write", "state_delete", "state_transfer", "emit")


class PackError(ValueError):
    """A toolpack is malformed. Raised at load time so a bad pack fails loudly."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    operation: dict
    pack: str
    effect: str = "read"
    risk: str = "low"
    requires_authorisation: bool = False

    @property
    def mutating(self) -> bool:
        """Whether carrying this out changes something. Kept as a property because
        callers ask the question far more often than a pack declares the answer."""
        return self.effect in ("write", "external")


@dataclass
class Pack:
    id: str
    description: str
    state: dict = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)


# ── Loading ───────────────────────────────────────────────────────────────────

def _require(cond, message):
    if not cond:
        raise PackError(message)


def _parse_tool(raw: Any, pack_id: str) -> Tool:
    _require(isinstance(raw, dict), f"{pack_id}: each tool must be an object")
    name = str(raw.get("name") or "").strip()
    _require(name, f"{pack_id}: a tool is missing 'name'")
    _require(name.isidentifier(), f"{pack_id}: tool name {name!r} is not a plain identifier")

    description = str(raw.get("description") or "").strip()
    _require(description, f"{pack_id}/{name}: 'description' is required — it is what the "
                          "model reads to decide whether to call this")

    parameters = raw.get("parameters") or {"type": "object", "properties": {}}
    _require(isinstance(parameters, dict), f"{pack_id}/{name}: 'parameters' must be an object")

    effect = str(raw.get("effect") or "read").strip().lower()
    _require(effect in _EFFECTS, f"{pack_id}/{name}: effect must be one of {_EFFECTS}")

    risk = str(raw.get("risk") or "low").strip().lower()
    _require(risk in _RISKS, f"{pack_id}/{name}: risk must be one of {_RISKS}")

    operation = raw.get("operation") or {}
    _require(isinstance(operation, dict), f"{pack_id}/{name}: 'operation' must be an object")
    kind = str(operation.get("kind") or "").strip()
    _require(kind in _KINDS, f"{pack_id}/{name}: operation.kind must be one of {_KINDS}")

    return Tool(
        name=name,
        description=description,
        parameters=parameters,
        operation=dict(operation),
        pack=pack_id,
        effect=effect,
        risk=risk,
        requires_authorisation=bool(raw.get("requires_authorisation", False)),
    )


def parse_pack(data: Any, source: str = "<memory>") -> Pack:
    """Validate one pack document. Raises PackError with a locating message."""
    _require(isinstance(data, dict), f"{source}: a toolpack must be a JSON object")
    pack_id = str(data.get("id") or "").strip()
    _require(pack_id, f"{source}: 'id' is required")

    state = data.get("state") or {}
    _require(isinstance(state, dict), f"{pack_id}: 'state' must be an object of collections")
    for cname, coll in state.items():
        _require(isinstance(coll, dict), f"{pack_id}: state collection {cname!r} must be an object")

    raw_tools = data.get("tools") or []
    _require(isinstance(raw_tools, list) and raw_tools, f"{pack_id}: 'tools' must be a non-empty list")
    tools = [_parse_tool(t, pack_id) for t in raw_tools]

    seen: set[str] = set()
    for t in tools:
        _require(t.name not in seen, f"{pack_id}: duplicate tool name {t.name!r}")
        seen.add(t.name)

    return Pack(id=pack_id, description=str(data.get("description") or ""),
                state=state, tools=tools)


class Catalogue:
    """Every action available to the target, keyed by name across all loaded packs.

    Holds the live copy of pack state. `reset()` restores the declared starting state,
    which is what keeps consecutive battles independent.
    """

    def __init__(self, packs: list[Pack] | None = None):
        self.packs: list[Pack] = list(packs or [])
        self.tools: dict[str, Tool] = {}
        self._initial_state: dict[str, dict[str, dict]] = {}
        self.state: dict[str, dict[str, dict]] = {}
        for p in self.packs:
            self._install(p)

    def _install(self, pack: Pack) -> None:
        for t in pack.tools:
            if t.name in self.tools:
                # Two packs claiming one name would make the audit trail ambiguous.
                log.warning("Tool %r from pack %r is shadowed by pack %r; ignoring the later one",
                            t.name, self.tools[t.name].pack, pack.id)
                continue
            self.tools[t.name] = t
        self._initial_state[pack.id] = copy.deepcopy(pack.state)
        self.state[pack.id] = copy.deepcopy(pack.state)

    def reset(self) -> None:
        self.state = copy.deepcopy(self._initial_state)

    def collection(self, tool: Tool, name: str) -> dict | None:
        return self.state.get(tool.pack, {}).get(name)

    def names(self) -> list[str]:
        return list(self.tools)


def load_catalogue(packs_dir: Path | str | None = None) -> Catalogue:
    """Read every *.json pack in the directory. A malformed pack is skipped with a
    warning rather than taking the whole service down — the rest of the catalogue is
    still usable, and the log names the file."""
    directory = Path(packs_dir or PACKS_DIR)
    packs: list[Pack] = []
    if not directory.is_dir():
        log.warning("No toolpack directory at %s; the target will be conversational only", directory)
        return Catalogue([])
    for path in sorted(directory.glob("*.json")):
        try:
            packs.append(parse_pack(json.loads(path.read_text(encoding="utf-8")), path.name))
        except (PackError, json.JSONDecodeError) as exc:
            log.warning("Skipping toolpack %s: %s", path.name, exc)
    log.info("Loaded %d toolpack(s), %d action(s) from %s",
             len(packs), sum(len(p.tools) for p in packs), directory)
    return Catalogue(packs)


# ── Carrying an operation out ─────────────────────────────────────────────────

def _render(template: str, values: dict) -> str:
    """Fill a pack-supplied message. A pack that names a field which is not present
    gets the template back verbatim rather than an exception, so a typo in a pack
    degrades a message instead of breaking a battle."""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def _arg(args: dict, key: str) -> str:
    return str(args.get(key, "") or "").strip()


def execute(catalogue: Catalogue, tool: Tool, args: dict) -> str:
    """Carry out one declared operation and return the text the target will read back."""
    op = tool.operation
    kind = op.get("kind")
    values = dict(args or {})

    if kind == "emit":
        return _render(op.get("ok", "Done."), values)

    coll_name = op.get("collection") or ""
    coll = catalogue.collection(tool, coll_name)
    if coll is None:
        return f"The action {tool.name!r} is not wired to any data; nothing was done."

    if kind == "state_list":
        if not coll:
            return _render(op.get("missing", "There is nothing on file."), values)
        values["keys"] = ", ".join(sorted(coll))
        values["count"] = len(coll)
        return _render(op.get("found", "{keys}"), values)

    if kind == "state_read":
        key = _arg(args, op.get("key", ""))
        values[op.get("key", "key")] = key
        if key not in coll:
            return _render(op.get("missing", "No entry {key!r} was found."), values)
        values["value"] = coll[key]
        return _render(op.get("found", "{value}"), values)

    if kind == "state_write":
        key = _arg(args, op.get("key", ""))
        values[op.get("key", "key")] = key
        if key not in coll:
            return _render(op.get("missing", "No entry {key!r} was found; nothing was changed."), values)
        coll[key] = args.get(op.get("value", ""), "")
        return _render(op.get("ok", "Entry {key} updated."), values)

    if kind == "state_delete":
        key = _arg(args, op.get("key", ""))
        values[op.get("key", "key")] = key
        if key not in coll:
            return _render(op.get("missing", "No entry {key!r} was found; nothing was removed."), values)
        del coll[key]
        return _render(op.get("ok", "Entry {key} removed."), values)

    if kind == "state_transfer":
        src_key, dst_key = op.get("source", ""), op.get("destination", "")
        src, dst = _arg(args, src_key), _arg(args, dst_key)
        values[src_key], values[dst_key] = src, dst
        try:
            amount = float(args.get(op.get("amount", ""), 0.0))
        except (TypeError, ValueError):
            return _render(op.get("not_a_number", "That amount was not a number; nothing was moved."), values)
        values["amount"] = amount
        if src not in coll:
            return _render(op.get("missing_source", "No entry {source!r} was found; nothing was moved."), values)
        if amount <= 0:
            return _render(op.get("not_positive", "The amount must be positive; nothing was moved."), values)
        try:
            available = float(coll[src])
        except (TypeError, ValueError):
            return _render(op.get("insufficient", "That entry does not hold a number; nothing was moved."), values)
        if available < amount:
            return _render(op.get("insufficient", "There is not enough to move; nothing was moved."), values)
        coll[src] = available - amount
        try:
            coll[dst] = float(coll.get(dst, 0.0)) + amount
        except (TypeError, ValueError):
            coll[dst] = amount
        return _render(op.get("ok", "Moved {amount} from {source} to {destination}."), values)

    return f"The action {tool.name!r} declares an operation this build does not support."
