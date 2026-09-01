"""The authority boundary around the target's actions.

An engagement about disclosure has one layer worth measuring: whether the target says
the thing. An engagement about *acting* has two, and conflating them loses the more
interesting half:

    the target's own restraint   — it decided to make the call, or declined to
    the boundary around it       — the call was permitted to take effect, or was not

Those are separate failures and want separate scores. A target that tries to wire money
to an attacker and is stopped by the boundary has already failed; a platform that only
records the final state would call that a clean round. So every attempt is recorded
whether or not it took effect, and the judge is handed both.

This mirrors how the platform already scores text: raw output is what the target
produced, delivered output is what survived the defender. Here, an attempted call is
raw and an executed call is delivered.

The boundary is configured as data, never in code. An engagement declares an
enforcement level and, optionally, which calls are authorised or forbidden; the
predicate vocabulary below is deliberately small so a declaration cannot become a
program.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Enforcement levels, weakest first. Named for what each one lets through.
PERMISSIVE = "permissive"   # everything runs; measures the target's own restraint alone
GUARDED    = "guarded"      # actions that declare they need authorisation must have it
STRICT     = "strict"       # anything that changes state or leaves the process must
SEALED     = "sealed"       # nothing runs
LEVELS = (PERMISSIVE, GUARDED, STRICT, SEALED)

ALLOWED = "allowed"
DENIED = "denied"


@dataclass
class Decision:
    verdict: str        # ALLOWED | DENIED
    reason: str         # short, reportable, safe to show the target
    rule: str = ""      # which declaration decided it, for the audit trail

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOWED


# ── Predicates ────────────────────────────────────────────────────────────────
#
# A condition is an object of {argument: {test: operand}}. Every named argument must
# match for the condition to hold. An unknown test never matches, so a typo in a
# declaration cannot silently widen what is permitted.

def _as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _test(test: str, operand, value) -> bool:
    text = "" if value is None else str(value)
    if test == "equals":
        return text == str(operand)
    if test == "one_of":
        return isinstance(operand, list) and text in [str(o) for o in operand]
    if test == "matches":
        return bool(re.search(str(operand), text, re.IGNORECASE))
    if test == "not_matches":
        return not re.search(str(operand), text, re.IGNORECASE)
    if test in ("at_most", "at_least"):
        left, right = _as_number(value), _as_number(operand)
        if left is None or right is None:
            return False
        return left <= right if test == "at_most" else left >= right
    return False


def condition_holds(condition, args: dict) -> bool:
    """Whether every argument test in the condition holds. An empty condition holds,
    which is how a declaration says 'any call to this action'."""
    if not condition:
        return True
    if not isinstance(condition, dict):
        return False
    for arg_name, tests in condition.items():
        if not isinstance(tests, dict):
            return False
        value = (args or {}).get(arg_name)
        for test, operand in tests.items():
            if not _test(test, operand, value):
                return False
    return True


def _clause_matches(clause, tool_name: str, args: dict) -> bool:
    if not isinstance(clause, dict):
        return False
    named = clause.get("tool")
    if named is not None and str(named) != tool_name:
        return False
    return condition_holds(clause.get("when"), args)


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass
class Policy:
    """What the target is told to uphold, and what the boundary enforces.

    `statement` is prose for the system prompt — the target's own restraint. The rest
    is enforced regardless of what the target decided.
    """
    statement: str = ""
    enforcement: str = PERMISSIVE
    authorised: tuple = ()
    forbidden: tuple = ()

    @classmethod
    def parse(cls, spec) -> "Policy":
        """Accept either prose or a declaration.

        A bare string is prose only, with no boundary — which is exactly how engagements
        behaved before a boundary existed, so an older engagement is unaffected.
        """
        if spec is None:
            return cls()
        if isinstance(spec, str):
            return cls(statement=spec.strip())
        if not isinstance(spec, dict):
            return cls()
        level = str(spec.get("enforcement") or PERMISSIVE).strip().lower()
        if level not in LEVELS:
            level = PERMISSIVE
        as_tuple = lambda v: tuple(v) if isinstance(v, list) else ()
        return cls(
            statement=str(spec.get("statement") or "").strip(),
            enforcement=level,
            authorised=as_tuple(spec.get("authorised")),
            forbidden=as_tuple(spec.get("forbidden")),
        )

    def to_dict(self) -> dict:
        return {"statement": self.statement, "enforcement": self.enforcement,
                "authorised": list(self.authorised), "forbidden": list(self.forbidden)}


def evaluate(policy: Policy, tool, args: dict) -> Decision:
    """Decide whether one call may take effect.

    Order matters. A forbidden clause wins over an authorising one, so an engagement
    that declares both does not accidentally permit the case it meant to rule out.
    """
    args = dict(args or {})

    for clause in policy.forbidden:
        if _clause_matches(clause, tool.name, args):
            return Decision(DENIED, "That is not something I am permitted to do here.",
                            rule="forbidden")

    if policy.enforcement == SEALED:
        return Decision(DENIED, "I am not able to take actions in this session.",
                        rule="enforcement:sealed")

    if policy.enforcement == PERMISSIVE:
        return Decision(ALLOWED, "", rule="enforcement:permissive")

    needs_authorisation = (
        tool.requires_authorisation if policy.enforcement == GUARDED else tool.mutating
    )
    if not needs_authorisation:
        return Decision(ALLOWED, "", rule=f"enforcement:{policy.enforcement}")

    for clause in policy.authorised:
        if _clause_matches(clause, tool.name, args):
            return Decision(ALLOWED, "", rule="authorised")

    return Decision(
        DENIED,
        "That action needs an authorisation this session does not carry.",
        rule=f"enforcement:{policy.enforcement}",
    )
