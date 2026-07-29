"""Local inspection of generated KCL -- no API calls, no credits.

`step_inspect` answers "is the geometry right". This answers a different
question: **is the model still editable**.

The Agent API returns KCL with named top-level parameters whatever you ask
for. That is not the same as returning a parametric model. Ask for a plate
with literal coordinates and you get:

    plateWidth = 80.0mm
    slotCenterOffset = 30.0mm

which is a named constant, not a relationship. Change `slotCenterOffset` in
Design Studio and the plate width does not follow, because nothing records
that one depends on the other. Ask in terms of relationships and you can get:

    plateWidth = slotSpacing + 2 * edgeMargin

which survives editing.

So the measure that matters is not "how many parameters" but "how many
parameters are *derived from other parameters*". That is what this module
counts, and it is the number the parametric prompt has to move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A top-level assignment: an identifier at column 0, then '='. Indented lines
# are sketch-internal bindings (segment names), not model parameters.
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", re.MULTILINE)

# A bare quantity: 31mm, 3.4mm, 0.5, -12.75deg. Nothing else on the line.
_LITERAL_RE = re.compile(r"^-?\d+(?:\.\d+)?\s*[A-Za-z]*$")

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Function calls that build geometry rather than declare a value -- these are
# assignments, but they are the model, not its parameters.
_GEOMETRY_CALLS = {
    "sketch", "region", "extrude", "subtract", "union", "intersect",
    "patternLinear3d", "patternCircular3d", "hide", "revolve", "loft",
    "sweep", "shell", "chamfer", "fillet", "translate", "rotate", "scale",
    "startSketchOn", "startProfile", "close", "appearance", "clone", "helix",
    "offsetPlane", "mirror2d", "patternLinear2d", "patternCircular2d",
}

_CONSTRAINT_CALLS = {
    "horizontalDistance", "verticalDistance", "distance", "radius", "diameter",
    "angle", "parallel", "perpendicular", "coincident", "equalLength",
    "horizontal", "vertical", "tangent", "symmetric", "midpoint",
}


@dataclass(frozen=True)
class Parameter:
    name: str
    expression: str
    derived: bool  # True when the right-hand side references another parameter
    references: tuple[str, ...] = ()


@dataclass
class KclModel:
    parameters: tuple[Parameter, ...] = ()
    constraint_count: int = 0
    construction_count: int = 0
    _source: str = field(default="", repr=False)

    @property
    def literals(self) -> tuple[Parameter, ...]:
        return tuple(p for p in self.parameters if not p.derived)

    @property
    def derived(self) -> tuple[Parameter, ...]:
        return tuple(p for p in self.parameters if p.derived)

    @property
    def derived_ratio(self) -> float:
        """Share of parameters that carry a relationship. 0.0 means every
        value is a standalone constant -- a model that looks parametric and
        edits like a fixed one."""
        if not self.parameters:
            return 0.0
        return len(self.derived) / len(self.parameters)

    def names(self) -> set[str]:
        return {p.name for p in self.parameters}

    def get(self, name: str) -> Parameter | None:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def summary(self) -> str:
        return (
            f"{len(self.parameters)} parameters "
            f"({len(self.derived)} derived, {len(self.literals)} literal), "
            f"{self.constraint_count} constraints"
        )


def _first_call(expr: str) -> str | None:
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr)
    return m.group(1) if m else None


def strip_comments(source: str) -> str:
    """Remove /* */ and // comments, preserving line structure.

    Not optional hygiene -- the Agent API echoes the submitted prompt into a
    /* */ header at the top of every file it returns, and our prompt now
    contains parameter declarations verbatim. Parsing the raw text would
    count those echoed lines as real declarations and report a model as
    parametric on the strength of its own prompt. The declarations happen to
    be indented today, which is the only reason the naive parser was right.
    """
    def blank(m: re.Match) -> str:
        return "".join("\n" for ch in m.group(0) if ch == "\n")

    without_blocks = _BLOCK_COMMENT_RE.sub(blank, source)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def inspect_kcl(source: str) -> KclModel:
    """Parse KCL source into the parameters it declares and how they relate."""
    source = strip_comments(source)
    params: list[Parameter] = []
    declared: set[str] = set()

    for match in _ASSIGN_RE.finditer(source):
        name, expr = match.group(1), match.group(2)

        call = _first_call(expr)
        if call in _GEOMETRY_CALLS:
            continue
        # Multi-line geometry blocks open with '{' or '(' and are not values.
        if expr.endswith(("{", "(", "[")):
            continue

        refs = tuple(
            i for i in _IDENT_RE.findall(expr)
            if i in declared and i != name
        )
        is_literal = bool(_LITERAL_RE.match(expr))
        params.append(
            Parameter(
                name=name,
                expression=expr,
                derived=bool(refs) and not is_literal,
                references=refs,
            )
        )
        declared.add(name)

    constraints = 0
    for cname in _CONSTRAINT_CALLS:
        constraints += len(re.findall(rf"\b{cname}\s*\(", source))

    return KclModel(
        parameters=tuple(params),
        constraint_count=constraints,
        construction_count=len(re.findall(r"construction\s*=\s*true", source)),
        _source=source,
    )


@dataclass
class ParametricReport:
    """How well a generated model honoured a requested parameter scheme."""

    expected_names: tuple[str, ...]
    found_names: tuple[str, ...]
    missing_names: tuple[str, ...]
    expected_relations: dict[str, str]
    honoured_relations: tuple[str, ...]
    broken_relations: tuple[str, ...]
    model: KclModel

    @property
    def name_coverage(self) -> float:
        if not self.expected_names:
            return 1.0
        return len(self.found_names) / len(self.expected_names)

    @property
    def relation_coverage(self) -> float:
        if not self.expected_relations:
            return 1.0
        return len(self.honoured_relations) / len(self.expected_relations)

    @property
    def ok(self) -> bool:
        return not self.missing_names and not self.broken_relations


def check_parametric(
    source: str,
    expected_names: list[str],
    expected_relations: dict[str, list[str]],
) -> ParametricReport:
    """Did the model declare the parameters we asked for, and did the derived
    ones actually reference what they were supposed to?

    `expected_relations` maps a parameter name to the names its expression
    must mention -- e.g. {"plateWidth": ["slotSpacing", "edgeMargin"]}. We
    check the *dependency*, not the exact spelling of the expression, because
    `a + 2 * b` and `2 * b + a` are the same relationship.
    """
    model = inspect_kcl(source)
    names = model.names()

    found = tuple(n for n in expected_names if n in names)
    missing = tuple(n for n in expected_names if n not in names)

    honoured: list[str] = []
    broken: list[str] = []
    for target, must_reference in expected_relations.items():
        p = model.get(target)
        if p is None:
            broken.append(target)
            continue
        if all(ref in p.references for ref in must_reference):
            honoured.append(target)
        else:
            broken.append(target)

    return ParametricReport(
        expected_names=tuple(expected_names),
        found_names=found,
        missing_names=missing,
        expected_relations={k: ", ".join(v) for k, v in expected_relations.items()},
        honoured_relations=tuple(honoured),
        broken_relations=tuple(broken),
        model=model,
    )
