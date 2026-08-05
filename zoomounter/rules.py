"""The rule registry: engineering claims as data, with provenance.

ZooMounter's rules used to live in three places at once -- validator code in
`catalogue.py`, `Check` objects constructed inline in `mechanics.py` and
`bearings.py`, and prose in `docs/mechanics.html`. The same claim could be
stated in all three and agree with itself in none of them, and a rule that
existed only as prose could not be enforced at all.

They now live in `data/rules.toml`. Python evaluates rules; it does not own
them. That is the same move already made for mounts and bearings, for the same
reason: this project's failures are data failures, and a file has to be loaded,
which is somewhere to put the checks.

## Why `status` is the field that matters

Every rule declares where it came from and how far it can be trusted:

    vendor-datasheet          a manufacturer published it for a specific part
    standard                  a published standard (ISO, NEMA) states it
    derived                   we reasoned it out -- defensible, but ours
    measured-against-api      we measured it against a running API, probe committed
    ai-proposed-unverified    an AI suggested it; nobody has checked it
    verified-against-physical someone built it and measured it

The distinction is not bureaucratic. This project's central finding is that an
AI-generated number is indistinguishable from a verified one once written down:
the NEMA bolt-circle bug and the shaft-end lever-arm bug both survived a green
test suite and a passing verifier, because verification compares a part against
the spec table and the spec table was the thing that was wrong.

So `ai-proposed-unverified` rules are **quarantined** -- `governing_rules()`
will not return them, and `assert_no_unverified_rules_govern()` fails the build
if one ever reaches a generated part. That makes AI collaboration safe rather
than forbidden: a proposal can sit in the registry, visible and labelled, until
somebody checks it against reality and promotes it.

## Why `applies_to` exists

Domain awareness as data rather than as special cases in code. A rule declares
the component class it governs -- `{ kind = "motor" }`, `{ process = "3d_print" }`,
or `{}` for everything. A component that matches no load rules gets told what
actually governs it instead of receiving a number the tool has no model for.
That is the honest handling of the board and panel mounts this catalogue used
to carry: not a thickness, but an explanation of why there isn't one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .catalogue import CatalogueError, _load, _require

VALID_SEVERITIES = {"pass", "info", "warn", "loud-warn"}

VALID_STATUSES = {
    "vendor-datasheet",
    "standard",
    "derived",
    # Empirically measured against a running API, with the probe committed.
    # Distinct from `derived` (which is reasoning) and from
    # `verified-against-physical` (which needs a part someone built). Added
    # because the alternative was labelling a measurement as an opinion, which
    # understates evidence -- the mirror image of the overstatement this
    # registry exists to prevent.
    "measured-against-api",
    "ai-proposed-unverified",
    "verified-against-physical",
}

# Statuses a rule must have before it is allowed to govern a generated part.
# The quarantine is the point of the whole file.
TRUSTED_STATUSES = VALID_STATUSES - {"ai-proposed-unverified"}

# Keys a rule may filter on. Deliberately a closed set: an `applies_to` naming
# a field that does not exist would silently match nothing, which is the
# quietest possible way for a safety rule to stop firing.
VALID_APPLIES_KEYS = {"kind", "process"}

SEVERITY_TO_LEVEL = {
    "pass": "PASS",
    "info": "INFO",
    "warn": "WARN",
    "loud-warn": "LOUD WARN",
}


@dataclass(frozen=True)
class Rule:
    id: str
    statement: str
    source: str
    severity: str
    status: str
    remedy: str = ""
    applies_to: dict[str, Any] = field(default_factory=dict)
    evaluated: bool = True

    @property
    def level(self) -> str:
        """The Check level this rule's violations are reported at."""
        return SEVERITY_TO_LEVEL[self.severity]

    @property
    def trusted(self) -> bool:
        return self.status in TRUSTED_STATUSES

    def applies(self, *, kind: str | None = None, process: str | None = None) -> bool:
        """Whether this rule governs a component of the given class.

        An empty `applies_to` matches everything. Unstated keys are wildcards,
        so a rule filtered on `kind` says nothing about `process`.
        """
        context = {"kind": kind, "process": process}
        return all(context.get(k) == v for k, v in self.applies_to.items())


def load_rules() -> list[dict[str, Any]]:
    """Read and validate data/rules.toml.

    Raises rather than returning a half-trusted registry. A tool whose claim is
    that its output is traceable has no business running on a rule file it could
    not check.
    """
    rows = _load("rules.toml").get("rule", [])
    if not rows:
        raise CatalogueError("rules.toml contains no [[rule]] entries")

    seen: set[str] = set()
    for row in rows:
        where = f"rules.toml [{row.get('id', '?')}]"
        _require(row, ("id", "statement", "source", "severity", "status"), where)

        if row["id"] in seen:
            raise CatalogueError(f"{where}: duplicate rule id")
        seen.add(row["id"])

        if row["severity"] not in VALID_SEVERITIES:
            raise CatalogueError(
                f"{where}: severity {row['severity']!r} not in {sorted(VALID_SEVERITIES)}"
            )
        if row["status"] not in VALID_STATUSES:
            raise CatalogueError(
                f"{where}: status {row['status']!r} not in {sorted(VALID_STATUSES)}"
            )

        # A rule that cites one of our own documents is the circular-provenance
        # defect: the axial limit once cited docs/mechanics.html, which is us.
        lowered = row["source"].lower()
        if "docs/" in lowered or "mechanics.html" in lowered:
            raise CatalogueError(
                f"{where}: source cites one of our own documents. Provenance that "
                f"points back at this project is not evidence -- cite the "
                f"datasheet or standard, or mark the rule 'derived' and say so."
            )

        # A rule that can fire must say what to do about it. A warning with no
        # remedy is a dead end, and the user's ruling was that warnings have to
        # reach the person and be actionable.
        if row["severity"] in ("warn", "loud-warn") and not row.get("remedy"):
            raise CatalogueError(
                f"{where}: severity {row['severity']!r} requires a remedy. A "
                f"warning the user cannot act on is noise."
            )

        applies = row.get("applies_to", {})
        if not isinstance(applies, dict):
            raise CatalogueError(f"{where}: applies_to must be a table")
        unknown = set(applies) - VALID_APPLIES_KEYS
        if unknown:
            raise CatalogueError(
                f"{where}: applies_to has unknown key(s) {sorted(unknown)}. Valid "
                f"keys are {sorted(VALID_APPLIES_KEYS)} -- an unknown key matches "
                f"nothing, which would silently disable this rule."
            )
    return rows


def _build() -> dict[str, Rule]:
    return {
        row["id"]: Rule(
            id=row["id"],
            statement=row["statement"],
            source=row["source"],
            severity=row["severity"],
            status=row["status"],
            remedy=row.get("remedy", ""),
            applies_to=row.get("applies_to", {}),
            evaluated=row.get("evaluated", True),
        )
        for row in load_rules()
    }


RULES: dict[str, Rule] = _build()


def get(rule_id: str) -> Rule:
    try:
        return RULES[rule_id]
    except KeyError as e:
        raise CatalogueError(
            f"Unknown rule id {rule_id!r}. Every Check.code must name a rule in "
            f"data/rules.toml, so a message the user sees can be traced back to "
            f"a claim with a source."
        ) from e


def governing_rules(*, kind: str | None = None, process: str | None = None) -> list[Rule]:
    """The trusted rules that govern this component class.

    Excludes `ai-proposed-unverified` by construction. A rule cannot govern a
    generated part just because somebody wrote it down convincingly.
    """
    return [r for r in RULES.values() if r.trusted and r.applies(kind=kind, process=process)]


def quarantined_rules() -> list[Rule]:
    """Rules that exist but are not allowed to govern anything yet."""
    return [r for r in RULES.values() if not r.trusted]


def limitations(*, kind: str | None = None, process: str | None = None) -> list[Rule]:
    """Rules with no evaluator: things the tool explicitly does NOT check.

    Surfaced rather than omitted. An unmodelled load case that nobody mentions
    is indistinguishable from one that passed.
    """
    return [
        r
        for r in RULES.values()
        if not r.evaluated and r.applies(kind=kind, process=process)
    ]


def assert_no_unverified_rules_govern(*, kind: str | None = None, process: str | None = None) -> None:
    """Fail loudly if a quarantined rule would govern a real part.

    Called from the test suite rather than at runtime, because the right time
    to catch this is before shipping, not while a user waits on a generation.
    """
    leaked = [
        r for r in RULES.values()
        if not r.trusted and r.evaluated and r.applies(kind=kind, process=process)
    ]
    if leaked:
        raise CatalogueError(
            "Unverified rules would govern a generated part: "
            + ", ".join(r.id for r in leaked)
            + ". Verify them against a physical part and promote the status, or "
            "set evaluated = false."
        )
