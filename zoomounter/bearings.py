"""Bearing selection for ZooMounter.

## Why this exists

mechanics.py has always told you to fit a bearing. For an over-limit radial
load it says *"support the shaft in its own bearing block"*; for axial thrust,
*"add a thrust bearing so the load bypasses the motor"*. Useful advice, and
then the tool did nothing about it -- it sized a plate that still routed the
whole load through the motor's own bearings.

This module picks the bearing, and mount_specs puts a seat for it in the
plate. The choice is driven by the load case, because the two cases need
physically different bearings:

**Radial** (belt, pulley, gear pulling sideways) -> deep groove ball bearing,
pressed into a through-bore in the plate. The shaft runs in the bearing, so
the side load reacts into the plate and the frame instead of into the motor's
front bearing.

**Axial** (leadscrew thrusting back along the shaft) -> thrust ball bearing,
seated in a counterbore on the plate face with the shaft passing through.
Deep groove bearings take some axial load but are not the right part for
sustained thrust, and a stepper's own bearings are far worse.

Thrust covers two families: the miniature F-series from a 5mm bore, and the
standard 511xx metric series from 10mm. Both are needed. Holding only the
511xx made the tool refuse every thrust bearing on a NEMA 17 shaft and
announce that none existed, which was a statement about the table, not about
bearings.

## What is checked, and what is deliberately not

Selection compares the load against the **basic static load rating C0**, not
against C with an L10 life calculation. An L10 figure needs shaft speed and
duty cycle, and this tool asks for neither -- computing one from assumed
values would produce an authoritative-looking number resting on a guess,
which is the failure mode this project has repeatedly shipped. So: static
capacity only, stated as such, and the selection says out loud that fatigue
life is not evaluated.

Bore must accommodate the shaft. Where no bearing in the table has a bore
matching the shaft exactly, the next size up is offered *with a warning* that
it needs a step or sleeve -- never silently, because a 5mm shaft spinning in
an 8mm bore is not a bearing, it is a rattle.

## Sources

Every figure below is from a manufacturer catalogue, recorded per row
including which manufacturer. The 15 N vs 15 lb collision in mount_specs is
the reason this is per-row rather than a single blanket citation.
"""

from __future__ import annotations

from dataclasses import dataclass

RADIAL = "radial"
THRUST = "thrust"

_SKF = "SKF catalogue (bearingsize.info)"
_NSK = "NSK catalogue (bearingsize.info)"
_AUBURN = "Auburn Bearing & Manufacturing, F-series miniature thrust catalogue"


@dataclass(frozen=True)
class Bearing:
    designation: str
    kind: str  # RADIAL | THRUST
    bore_mm: float  # d -- the shaft goes in here
    od_mm: float  # D -- the plate seat is bored to this
    width_mm: float  # B (radial) or H (thrust) -- how deep a seat must be
    static_c0_n: float  # basic static load rating, newtons
    dynamic_c_n: float  # basic dynamic load rating, newtons (reported, not used)
    source: str

    @property
    def label(self) -> str:
        return f"{self.designation} ({self.bore_mm:g}x{self.od_mm:g}x{self.width_mm:g}mm)"


# Deep groove ball bearings -- radial. Ordered by bore.
#
# Thrust comes in two families, and having only one of them was a real bug.
# An earlier version of this table held only the 511xx metric series, which
# starts at a 10mm bore, and so refused to fit any thrust bearing to a NEMA
# 17's 5mm shaft. The tool reported that as "no thrust bearing fits a 5mm
# shaft". It is not true: the miniature F-series goes down to a 5mm bore and
# is exactly what small mechatronics uses. The table was the limit, and it
# got described as though it were physics.
#
#   511xx  standard metric thrust ball bearings, 10mm bore and up
#   Fx-yyM miniature thrust ball bearings, 5mm bore and up, 3-piece separable
#
# F-series figures are Auburn Bearing's published ratings, taken from one
# manufacturer across all three sizes so the numbers share a basis. F5-10M is
# deliberately absent: it is the most widely sold of the family, but the
# ratings published for it run dynamic 950N / static 830N -- dynamic above
# static, backwards from every other thrust bearing here and from Auburn's own
# F5-12M. Rather than import a row that contradicts the rest of the table, it
# is left out until the figure can be confirmed from a primary source.
def _build_bearings() -> tuple[Bearing, ...]:
    """Load data/bearings.toml. catalogue.load_bearings() validates each row
    before it gets here -- including that a thrust bearing's static rating is
    not below its dynamic one, which is how F5-10M's published figures fail
    and why that row is absent."""
    from .catalogue import load_bearings

    return tuple(
        Bearing(
            designation=r["designation"],
            kind=r["kind"],
            bore_mm=r["bore_mm"],
            od_mm=r["od_mm"],
            width_mm=r["width_mm"],
            static_c0_n=r["static_c0_n"],
            dynamic_c_n=r["dynamic_c_n"],
            source=r["source"],
        )
        for r in load_bearings()
    )


BEARINGS: tuple[Bearing, ...] = _build_bearings()

BY_DESIGNATION = {b.designation: b for b in BEARINGS}

# A bearing whose bore is more than this above the shaft is not offered even
# with a warning -- past a couple of millimetres you are not adapting a shaft,
# you are designing a different part.
MAX_BORE_OVERSIZE_MM = 4.0


def kind_for_load(load_type: str) -> str:
    return THRUST if load_type == "axial" else RADIAL


@dataclass
class BearingSelection:
    """The chosen bearing plus every reason the choice is or isn't sound.

    `bearing` is None when nothing in the table fits. That is a real answer,
    not a failure -- it means the load or the shaft is outside what a plate
    with a pressed-in bearing can do, and the notes say which."""

    bearing: Bearing | None
    load_type: str
    load_n: float
    shaft_dia_mm: float
    safety_factor: float
    notes: list = None  # list[mechanics.Check], typed loosely to avoid a cycle

    def __post_init__(self):
        if self.notes is None:
            self.notes = []

    @property
    def required_capacity_n(self) -> float:
        return self.load_n * self.safety_factor


def candidates(kind: str, shaft_dia_mm: float) -> list[Bearing]:
    """Bearings of the right type whose bore can take this shaft, smallest
    first. Smallest-first matters: the cheapest bearing that carries the load
    is the right answer, not the strongest one in the table."""
    return sorted(
        (
            b
            for b in BEARINGS
            if b.kind == kind
            and b.bore_mm >= shaft_dia_mm - 1e-9
            and b.bore_mm <= shaft_dia_mm + MAX_BORE_OVERSIZE_MM
        ),
        key=lambda b: (b.od_mm, b.width_mm),
    )


# Stable Check codes, so consumers select by identifier rather than by prose.
BEARING_SELECTION = "bearing_selection"
BEARING_FIT = "bearing_fit"
BEARING_SEAT = "bearing_seat"
BEARING_LIFE = "bearing_life"


def select_bearing(
    load_type: str,
    load_n: float,
    shaft_dia_mm: float,
    safety_factor: float = 2.0,
) -> BearingSelection:
    """Pick the smallest bearing of the right type that fits the shaft and
    carries the load, or explain why none does."""
    from .mechanics import Check  # local import: mechanics imports mount_specs

    kind = kind_for_load(load_type)
    required = load_n * safety_factor
    sel = BearingSelection(
        bearing=None, load_type=load_type, load_n=load_n,
        shaft_dia_mm=shaft_dia_mm, safety_factor=safety_factor,
    )

    if shaft_dia_mm <= 0:
        sel.notes.append(Check(
            level="WARN",
            message=(
                "No shaft diameter is on file for this mount, so no bearing "
                "can be selected -- bore size is the first thing that has to "
                "match."
            ),
            remedy="Pass the shaft diameter explicitly to fit a bearing.",
            code=BEARING_SELECTION,
        ))
        return sel

    pool = candidates(kind, shaft_dia_mm)
    if not pool:
        of_kind = [b for b in BEARINGS if b.kind == kind]
        smallest = min(of_kind, key=lambda b: b.bore_mm) if of_kind else None
        detail = (
            f"the smallest {kind} bearing on file is {smallest.designation} "
            f"with a {smallest.bore_mm:g}mm bore"
            if smallest else f"no {kind} bearings are on file"
        )
        sel.notes.append(Check(
            level="LOUD WARN",
            message=(
                f"No {kind} bearing fits a {shaft_dia_mm:g}mm shaft -- {detail}."
            ),
            remedy=(
                "Step the shaft up to the bearing bore, or use a separate "
                "bearing block sized for this shaft rather than seating one "
                "in the plate."
            ),
            code=BEARING_SELECTION,
        ))
        return sel

    strong = [b for b in pool if b.static_c0_n >= required]
    if not strong:
        best = max(pool, key=lambda b: b.static_c0_n)
        sel.notes.append(Check(
            level="LOUD WARN",
            message=(
                f"No {kind} bearing that fits a {shaft_dia_mm:g}mm shaft carries "
                f"{required:.0f}N ({load_n:.0f}N at SF {safety_factor:g}). The "
                f"strongest that fits is {best.label} at {best.static_c0_n:.0f}N "
                f"static."
            ),
            source=best.source,
            remedy=(
                "Reduce the load, lower the safety factor if it is "
                "conservative, or move to a larger shaft so a bigger bearing "
                "becomes available."
            ),
            code=BEARING_SELECTION,
        ))
        return sel

    chosen = strong[0]
    sel.bearing = chosen

    sel.notes.append(Check(
        level="PASS",
        message=(
            f"{chosen.label} carries {chosen.static_c0_n:.0f}N static, against "
            f"{required:.0f}N required ({load_n:.0f}N at SF {safety_factor:g})."
        ),
        source=chosen.source,
        code=BEARING_SELECTION,
    ))

    if chosen.bore_mm > shaft_dia_mm + 1e-9:
        sel.notes.append(Check(
            level="LOUD WARN",
            message=(
                f"{chosen.designation} has a {chosen.bore_mm:g}mm bore but the "
                f"shaft is {shaft_dia_mm:g}mm. It will not locate the shaft as "
                f"supplied."
            ),
            remedy=(
                f"Fit a {shaft_dia_mm:g}->{chosen.bore_mm:g}mm sleeve or step "
                f"the shaft up. A shaft turning loose in an oversized bore is "
                f"not a bearing."
            ),
            code=BEARING_FIT,
        ))
    else:
        sel.notes.append(Check(
            level="PASS",
            message=f"{chosen.designation} bore matches the {shaft_dia_mm:g}mm shaft.",
            code=BEARING_FIT,
        ))

    sel.notes.append(Check(
        level="WARN",
        message=(
            "Selected on basic static rating C0 only. No L10 fatigue life has "
            "been calculated -- that needs shaft speed and duty cycle, which "
            "this tool does not ask for."
        ),
        remedy=(
            "For anything running continuously, do an L10 calculation with "
            f"your real speed and duty against C = {chosen.dynamic_c_n:.0f}N."
        ),
        source=chosen.source,
        code=BEARING_LIFE,
    ))

    return sel


def check_seat_depth(bearing: Bearing, plate_thickness_mm: float, load_type: str):
    """Can this plate physically hold this bearing?

    Kept separate from select_bearing because plate thickness is not known
    until the sizing calc has run, and the honest answer is often no: a
    thrust bearing is ~9mm tall and these plates come out at 1-3mm. Saying so
    is the useful output, not a problem to hide.
    """
    from .mechanics import Check

    if load_type == "axial":
        # Blind counterbore: needs the bearing height plus material under it.
        floor = 2.0
        needed = bearing.width_mm + floor
        if plate_thickness_mm + 1e-9 >= needed:
            return Check(
                level="PASS",
                message=(
                    f"{plate_thickness_mm:.2f}mm plate seats the {bearing.width_mm:g}mm "
                    f"{bearing.designation} in a counterbore with {floor:g}mm of "
                    f"material beneath."
                ),
                code=BEARING_SEAT,
            )
        return Check(
            level="LOUD WARN",
            message=(
                f"A {plate_thickness_mm:.2f}mm plate cannot seat the "
                f"{bearing.designation}: the bearing is {bearing.width_mm:g}mm tall and a "
                f"blind counterbore needs about {needed:g}mm of plate."
            ),
            remedy=(
                f"Set the plate to at least {needed:g}mm, or house the thrust "
                f"bearing in a separate block and bolt that to this plate."
            ),
            code=BEARING_SEAT,
        )

    # Radial: pressed into a through-bore. Thinner than the bearing is width
    # means the outer ring is only partly gripped.
    if plate_thickness_mm + 1e-9 >= bearing.width_mm:
        return Check(
            level="PASS",
            message=(
                f"{plate_thickness_mm:.2f}mm plate fully supports the "
                f"{bearing.width_mm:g}mm wide {bearing.designation} outer ring."
            ),
            code=BEARING_SEAT,
        )
    return Check(
        level="LOUD WARN",
        message=(
            f"{plate_thickness_mm:.2f}mm plate is thinner than the "
            f"{bearing.designation}'s {bearing.width_mm:g}mm width, so the press fit grips "
            f"only {plate_thickness_mm / bearing.width_mm:.0%} of the outer ring. "
            f"It will cock under load."
        ),
        remedy=(
            f"Thicken the plate to at least {bearing.width_mm:g}mm, or add a "
            f"boss around the bore to make up the depth."
        ),
        code=BEARING_SEAT,
    )


# Bearing-block proportions. These reproduce the hand-written bearing_608
# mount exactly (22mm OD -> 40mm plate, 34mm bolt circle), which is the
# evidence that the rule matches what a person actually drew rather than
# being a number invented to look tidy. test_bearings pins that.
SEAT_WALL_MM = 9.0  # material either side of the seat bore
BOLT_CIRCLE_MARGIN_MM = 6.0  # bolt circle radius beyond the seat radius
SHAFT_CLEARANCE_MM = 1.0  # added to bore dia for the shaft through-hole

# Bolt size steps up once the block gets big. M3 through a 60mm plate would
# be the limiting element long before the plate was.
_SMALL_BOLT_MAX_OD_MM = 30.0
_M3_CLEARANCE_MM = 3.4  # ISO 273 normal
_M4_CLEARANCE_MM = 4.5


def bolt_hole_for(bearing: Bearing) -> float:
    return _M3_CLEARANCE_MM if bearing.od_mm <= _SMALL_BOLT_MAX_OD_MM else _M4_CLEARANCE_MM


def bearing_block(bearing: Bearing, load_type: str):
    """Build a MountSpec for a block that houses this bearing.

    This is the physically sound answer to "put a bearing in the mount". You
    cannot press one into a motor faceplate concentric with the pilot bore --
    on a NEMA 17 the pilot hole is 22mm and the bearing a 5mm shaft needs is
    16mm across, so there is no material to grip. The bearing belongs in its
    own block, which is what this generates.

    Radial: through-bore at the bearing OD; the outer ring is gripped by the
    full plate thickness.

    Axial: a blind counterbore at the OD to seat the thrust bearing, with a
    smaller through-hole so the shaft can pass. The probe in probes/ confirmed
    the Agent API builds both features accurately.
    """
    from .mount_specs import MountSpec, circular_bolt_pattern

    plate = bearing.od_mm + 2 * SEAT_WALL_MM
    bolt_circle = bearing.od_mm + 2 * BOLT_CIRCLE_MARGIN_MM
    bolt_dia = bolt_hole_for(bearing)

    if load_type == "axial":
        seat_depth = bearing.width_mm
        centre_hole = bearing.bore_mm + SHAFT_CLEARANCE_MM  # shaft passes through
    else:
        seat_depth = 0.0  # through-bore
        centre_hole = 0.0  # the seat *is* the hole

    # The block's shaft-load limit is the bearing's own static rating, in the
    # direction the bearing is there to take. Feeding it into the same fields
    # a motor uses means the existing component-load check reports against the
    # bearing automatically -- and, more usefully, that fitting a bearing
    # visibly *raises* the limit rather than the tool just saying "not
    # checked". A 608 block takes 1370N radial where the NEMA 17 it relieves
    # took 28N.
    limits = (
        {"max_axial_n": bearing.static_c0_n}
        if load_type == "axial"
        else {"max_radial_n": bearing.static_c0_n}
    )

    return MountSpec(
        name=f"{bearing.designation} {'thrust' if load_type == 'axial' else 'radial'} bearing block",
        kind="bearing",
        plate_width_mm=plate,
        plate_height_mm=plate,
        bolt_hole_dia_mm=bolt_dia,
        hole_positions=circular_bolt_pattern(4, bolt_circle),
        center_hole_dia_mm=centre_hole,
        shaft_dia_mm=bearing.bore_mm,
        bearing_designation=bearing.designation,
        bearing_seat_dia_mm=bearing.od_mm,
        bearing_seat_depth_mm=seat_depth,
        bearing_width_mm=bearing.width_mm,
        load_limit_source=(
            f"{bearing.designation} basic static load rating C0 = "
            f"{bearing.static_c0_n:.0f}N -- {bearing.source}"
        ),
        **limits,
    )


def seat_fit_note(bearing: Bearing):
    """The seat is bored to the nominal OD with no fit allowance applied.

    Stated as a Check rather than buried in a docstring, because it is the
    difference between a bearing that stays put and one that spins in its
    housing. The existing bearing_608 mount has always done this; the only
    change is that the tool now says so."""
    from .mechanics import Check

    return Check(
        level="WARN",
        message=(
            f"The seat is bored to {bearing.od_mm:g}mm, the bearing's nominal OD, "
            f"with no interference allowance applied."
        ),
        remedy=(
            "For a machined block, tighten the bore to an H7 press fit before "
            "cutting. A 3D-printed block will not hold that tolerance -- expect "
            "to shim or retain the outer ring."
        ),
        code=BEARING_SEAT,
    )


def auto_bearing_mount(
    load_type: str,
    load_n: float,
    shaft_dia_mm: float,
    safety_factor: float = 2.0,
    designation: str | None = None,
):
    """Resolve `--mount bearing` into a concrete block.

    Returns (MountSpec or None, BearingSelection). None means no bearing in
    the table fits, and the selection's notes say why -- the caller should
    surface those rather than falling back to some default block, because a
    block built around the wrong bearing is worse than no block.
    """
    if designation:
        bearing = BY_DESIGNATION.get(designation)
        if bearing is None:
            raise ValueError(
                f"Unknown bearing '{designation}'. Choose from "
                f"{sorted(BY_DESIGNATION)}."
            )
        sel = BearingSelection(
            bearing=bearing, load_type=load_type, load_n=load_n,
            shaft_dia_mm=shaft_dia_mm or bearing.bore_mm,
            safety_factor=safety_factor,
        )
        from .mechanics import Check
        wanted = kind_for_load(load_type)
        if bearing.kind != wanted:
            sel.notes.append(Check(
                level="LOUD WARN",
                message=(
                    f"{bearing.designation} is a {bearing.kind} bearing but the load "
                    f"is {load_type}, which calls for a {wanted} bearing."
                ),
                remedy=(
                    "Deep groove bearings take limited thrust and thrust "
                    "bearings take no radial load at all. Pick the type that "
                    "matches the load, or let selection choose."
                ),
                code=BEARING_SELECTION,
            ))
        required = load_n * safety_factor
        if bearing.static_c0_n < required:
            sel.notes.append(Check(
                level="LOUD WARN",
                message=(
                    f"{bearing.label} is rated {bearing.static_c0_n:.0f}N static but "
                    f"{required:.0f}N is required ({load_n:.0f}N at SF "
                    f"{safety_factor:g})."
                ),
                source=bearing.source,
                remedy="Choose a larger bearing or reduce the load.",
                code=BEARING_SELECTION,
            ))
        else:
            sel.notes.append(Check(
                level="PASS",
                message=(
                    f"{bearing.label} carries {bearing.static_c0_n:.0f}N static, "
                    f"against {required:.0f}N required."
                ),
                source=bearing.source,
                code=BEARING_SELECTION,
            ))
    else:
        sel = select_bearing(load_type, load_n, shaft_dia_mm, safety_factor)

    if sel.bearing is None:
        return None, sel

    sel.notes.append(seat_fit_note(sel.bearing))
    return bearing_block(sel.bearing, load_type), sel
