"""Catalogue loading and validation.

Mount and bearing data live in `data/*.toml` rather than in Python literals.
Two reasons, and the second is the one that matters.

The obvious reason is growth: the tables were five mounts, then five plus an
extrusion series, then thirteen bearings, and each addition meant editing
executable code to add a row of numbers.

The real reason is that **this project's failures are data failures**. The
NEMA bolt pattern, the 67N axial limit, the unit collision on the NEMA 23
figure, the 4040 series that duplicated 2020, the thrust series missing its
small sizes -- not one of those was a wrong calculation. Every one was a
wrong or absent number sitting in a table that nothing validated, because a
Python literal is checked only for syntax.

So the point of moving to data files is not the file format. It is that a
file has to be *loaded*, and loading is a place to put the checks. Every
invariant that used to live only in a test now runs on import:

  - every row carries a source, or the file is rejected
  - a bearing's bore is inside its OD
  - a thrust bearing's static rating is not below its dynamic rating, which
    is how a unit mix-up presents
  - a mount that publishes a load limit also publishes where it came from
  - bolt patterns declare their TYPE, so `square` and `circular` cannot be
    confused -- that confusion is the bolt-pattern bug, and it is now
    unrepresentable rather than merely tested for

A malformed catalogue raises CatalogueError at import. That is deliberate:
this tool's whole claim is that generated geometry matches a spec, and a
tool that cannot trust its own spec table should refuse to start rather than
produce confidently wrong parts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

VALID_MOUNT_KINDS = {"motor", "bearing", "board", "flange"}
VALID_BEARING_KINDS = {"radial", "thrust"}
VALID_PATTERNS = {"square", "rectangular", "circular", "explicit"}


class CatalogueError(RuntimeError):
    """Raised when a data file is malformed, incomplete, or self-inconsistent."""


def _load(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        raise CatalogueError(f"Catalogue file missing: {path}")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise CatalogueError(f"{path.name} is not valid TOML: {e}") from e


def _require(row: dict, keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if row.get(k) in (None, "")]
    if missing:
        raise CatalogueError(f"{where}: missing required field(s) {', '.join(missing)}")


def _positive(row: dict, keys: tuple[str, ...], where: str) -> None:
    for k in keys:
        v = row.get(k)
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            raise CatalogueError(f"{where}: {k} must be a positive number, got {v!r}")


# ---------------------------------------------------------------------------
# Bearings
# ---------------------------------------------------------------------------


def load_bearings() -> list[dict[str, Any]]:
    rows = _load("bearings.toml").get("bearing", [])
    if not rows:
        raise CatalogueError("bearings.toml contains no [[bearing]] entries")

    seen: set[str] = set()
    for row in rows:
        where = f"bearings.toml [{row.get('designation', '?')}]"
        _require(row, ("designation", "kind", "source"), where)
        _positive(
            row, ("bore_mm", "od_mm", "width_mm", "static_c0_n", "dynamic_c_n"), where
        )

        if row["kind"] not in VALID_BEARING_KINDS:
            raise CatalogueError(
                f"{where}: kind {row['kind']!r} not in {sorted(VALID_BEARING_KINDS)}"
            )
        if row["designation"] in seen:
            raise CatalogueError(f"{where}: duplicate designation")
        seen.add(row["designation"])

        if row["bore_mm"] >= row["od_mm"]:
            raise CatalogueError(
                f"{where}: bore {row['bore_mm']}mm is not inside OD {row['od_mm']}mm"
            )

        # A thrust bearing's static capacity exceeds its dynamic capacity. A row
        # the other way round is a transcription slip or a unit mix-up, which is
        # this project's most expensive recurring bug.
        if row["kind"] == "thrust" and row["static_c0_n"] < row["dynamic_c_n"]:
            raise CatalogueError(
                f"{where}: static rating {row['static_c0_n']}N is below dynamic "
                f"{row['dynamic_c_n']}N. Thrust bearings are the other way round -- "
                f"check for a unit mix-up before adding this row."
            )
    return rows


# ---------------------------------------------------------------------------
# Mounts
# ---------------------------------------------------------------------------


def expand_bolt_pattern(spec: dict[str, Any], where: str) -> tuple[tuple[float, float], ...]:
    """Turn a declared pattern into coordinates.

    The `type` field is the whole point. `square` and `circular` take
    superficially similar inputs and produce completely different holes, and
    picking the wrong one is invisible until the part will not bolt on.
    Declaring the type makes the two unmixable."""
    from .mount_specs import (
        circular_bolt_pattern,
        rectangular_bolt_pattern,
        square_bolt_pattern,
    )

    kind = spec.get("type")
    if kind not in VALID_PATTERNS:
        raise CatalogueError(
            f"{where}: bolt_pattern.type {kind!r} not in {sorted(VALID_PATTERNS)}"
        )

    try:
        if kind == "square":
            return square_bolt_pattern(float(spec["spacing_mm"]))
        if kind == "rectangular":
            return rectangular_bolt_pattern(
                float(spec["x_spacing_mm"]), float(spec["y_spacing_mm"])
            )
        if kind == "circular":
            return circular_bolt_pattern(
                int(spec["count"]), float(spec["circle_dia_mm"])
            )
        positions = spec["positions"]
        if not positions:
            raise CatalogueError(f"{where}: explicit pattern has no positions")
        return tuple((float(x), float(y)) for x, y in positions)
    except KeyError as e:
        raise CatalogueError(
            f"{where}: bolt_pattern type {kind!r} needs field {e}"
        ) from e


def load_mounts() -> list[dict[str, Any]]:
    rows = _load("mounts.toml").get("mount", [])
    if not rows:
        raise CatalogueError("mounts.toml contains no [[mount]] entries")

    seen: set[str] = set()
    for row in rows:
        where = f"mounts.toml [{row.get('key', '?')}]"
        _require(row, ("key", "name", "kind"), where)
        _positive(row, ("plate_width_mm", "plate_height_mm", "bolt_hole_dia_mm"), where)

        if row["kind"] not in VALID_MOUNT_KINDS:
            raise CatalogueError(
                f"{where}: kind {row['kind']!r} not in {sorted(VALID_MOUNT_KINDS)}"
            )
        if row["key"] in seen:
            raise CatalogueError(f"{where}: duplicate key")
        seen.add(row["key"])

        if "bolt_pattern" not in row:
            raise CatalogueError(f"{where}: no [mount.bolt_pattern] section")

        # A published limit without a citation is the circular-provenance bug
        # again: a number that looks authoritative and answers to nothing.
        publishes_limit = (
            row.get("max_axial_n") is not None or row.get("max_radial_n") is not None
        )
        if publishes_limit and not row.get("load_limit_source"):
            raise CatalogueError(
                f"{where}: publishes a load limit but has no load_limit_source. "
                f"An uncited limit is not evidence."
            )

        # A citation is necessary and not sufficient. A radial rating is a
        # moment limit on the motor's front bearing, quoted at a stated
        # distance from the flange, so the same numeral means different things
        # at different offsets. Without the distance there is nothing to
        # compare a load case against -- and comparing anyway is how this
        # project's recurring defect works: 15N vs 15lb, a square spacing read
        # as a bolt circle, and now a rating measured at 20mm checked against a
        # load applied at 15mm. In each case the number survived and its
        # conditions did not.
        if row.get("max_radial_n") is not None and row.get("max_radial_at_mm") is None:
            raise CatalogueError(
                f"{where}: publishes max_radial_n but no max_radial_at_mm. A radial "
                f"rating is quoted at a distance from the mounting flange and means "
                f"nothing without it -- find the distance in the datasheet, or drop "
                f"the rating rather than comparing loads to it."
            )
        _positive(row, ("max_radial_at_mm",), where)
    return rows
