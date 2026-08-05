"""What actually governs a mount, and what never did.

This module used to size a plate against bending stress, tip deflection and
screw-head pull-through. All of that is gone. The reasoning is worth keeping,
because deleting a calculation is a stronger claim than adding one.

## Why the structural layer was removed

**It never governed.** A NEMA 17's published radial limit is 28N. Run that
through a cantilever bending calc on a 42mm plate in any real material and the
answer lands below the minimum wall thickness the process can produce. The
same is true for every part in scope. Four calculations -- bending, deflection,
punching shear, fastener tension -- and on every in-scope case the process
floor won. The module's own docstring already conceded the point for the axial
path: "for axial thrust the plate is usually not the limiting element at all."

**It answered a question about the wrong object.** The motor's housing is a
cast or extruded metal shell bolted to a flat plate. It is not the fragile
part of the assembly and never was. What is fragile is the shaft, and more
precisely the small bearings inside the motor that support it. Those have
published limits an order of magnitude below anything the bracket cares about.

**It made a wrong answer look rigorous.** A thickness quoted to two decimals,
derived from a named beam formula, reads as an engineering result. When it is
really the process floor wearing a beam calc's clothes, that presentation is
the problem -- it is the same failure this project already documented twice,
where a number that looked authoritative answered to nothing.

The deflection limit deserves its own note. It was L/300, a generic bracket
stiffness rule. For a printed bracket it does not model the real failure mode
at all: a NEMA 23 case runs at 70-80 C and PLA creeps well below that, so a
part that passes L/300 on day one can sag in service without the load ever
changing. Computing around that is worse than stating it, so it is now stated
as a limitation rather than approximated by an unrelated rule.

`materials.py` stays. Minimum wall, density and material naming are all still
needed. It simply no longer feeds a stress equation.

## What replaced it

The entry point is now `shaft_support()`, which asks the question that has a
real answer: **does this load exceed what the component's own shaft can take,
and therefore does it need to bypass the motor into a bearing?**

Thickness demotes to a consequence, computed by `required_thickness()` from
two floors: what the process can make, and what the bearing needs to seat in.
Neither is a load calculation, and the result no longer pretends to be one.

## Two loads, not one

The old API took a single `load_n` and compared it against the component's
SHAFT ratings, while the CLI prompted for it as "expected load on the mount".
One variable carried two physical meanings, so bolting a camera to the plate
reported a shaft overload that cannot physically happen. They are now separate:

- `shaft_load_n` acts at the shaft, is checked against the component's
  published rating, and is what a bearing can bypass.
- `plate_load_n` is anything else fastened to the bracket. It never reaches
  the shaft, is never compared to a shaft rating, and is reported as
  explicitly unmodelled rather than silently folded into a check it fails.
"""

import math
from dataclasses import dataclass, field

from .materials import MIN_WALL_MM, Material
from .mount_specs import MountSpec

LOAD_TYPES = ("radial", "axial")

# Shaft-support verdicts.
SHAFT_OK = "SHAFT_OK"
SHAFT_UNKNOWN = "SHAFT_UNKNOWN"
BEARING_RECOMMENDED = "BEARING_RECOMMENDED"
BEARING_REQUIRED = "BEARING_REQUIRED"

# Utilisation above which a bearing is advised while not yet mandatory.
#
# PROVENANCE: this is a judgement call, not a datasheet figure, and it is
# named rather than inlined so it reads as one. Published shaft ratings are
# absolute maxima with no stated margin, quoted for a load case tidier than
# any real machine -- belt tension varies with temperature and wear, and a
# hard stop delivers a transient far above the nominal figure. Sitting at 85%
# of an absolute maximum is not a passing grade. 0.70 is where the tool starts
# saying so.
BEARING_ADVISORY_UTILISATION = 0.70

# Material left under a blind counterbore so the seat has a floor to press
# against rather than opening into a through-hole.
BEARING_SEAT_FLOOR_MM = 2.0

# Stable codes. Prefer these over matching on message text.
SHAFT_LIMIT = "shaft_limit"
SHAFT_LIMIT_UNKNOWN = "shaft_limit_unknown"
PLATE_LOAD_UNMODELLED = "plate_load_unmodelled"
BEARING_SEAT_GOVERNS = "bearing_seat_governs"
PROCESS_FLOOR_GOVERNS = "process_floor_governs"
THERMAL_UNMODELLED = "thermal_unmodelled"

_BYPASS_REMEDY = {
    "axial": (
        "Add a thrust bearing so the load bypasses the motor "
        "(screw -> bearing -> housing -> frame), and drive through a flexible "
        "coupling that tolerates float."
    ),
    "radial": (
        "Support the shaft in its own bearing and drive through a coupling, so "
        "the side load reacts into the frame rather than the motor's front "
        "bearing."
    ),
}


@dataclass
class Check:
    level: str  # "PASS", "INFO", "WARN", "LOUD WARN"
    message: str
    source: str = ""
    remedy: str = ""
    code: str = ""  # stable identifier; prefer this over matching on message text

    def __str__(self):
        s = f"[{self.level}] {self.message}"
        if self.source:
            s += f" (Source: {self.source})"
        if self.remedy:
            s += f" -> Remedy: {self.remedy}"
        return s


@dataclass
class ShaftDecision:
    """Whether the component's own shaft can take the load, or needs help.

    This is the tool's primary result. Everything else is downstream of it.
    """

    verdict: str
    load_type: str
    shaft_load_n: float
    offset_mm: float  # where the load acts, from the mounting face
    limit_n: float | None  # published rating, None if not on file
    limit_at_mm: float | None  # distance that rating was measured at (radial)
    applied_n_mm: float | None  # applied demand, as a moment for radial
    limit_n_mm: float | None  # rated demand, same units as applied
    utilisation: float | None  # applied / limit; None if not checkable
    checks: list[Check] = field(default_factory=list)

    @property
    def needs_bearing(self) -> bool:
        return self.verdict in (BEARING_REQUIRED, BEARING_RECOMMENDED)


@dataclass
class ThicknessResult:
    """Plate thickness, which is now a manufacturing answer rather than a
    structural one. Both candidates are floors, not load calculations."""

    required_thickness_mm: float
    governing_limit: str
    min_wall_mm: float
    bearing_seat_min_mm: float  # 0 when no bearing is seated in this plate
    plate_load_n: float
    notes: list[Check] = field(default_factory=list)


def shaft_support(
    mount: MountSpec,
    shaft_load_n: float,
    load_type: str = "radial",
    offset_mm: float | None = None,
) -> ShaftDecision:
    """Compare a shaft load against the component's published rating.

    ## Radial loads are compared as moments, not forces

    A published radial rating is quoted at a stated distance from the mounting
    flange -- 28N at 20mm for a NEMA 17. That is not incidental. What the
    rating protects is the motor's front bearing, and a side load's severity
    there scales with how far out it acts, so the figure is really a moment
    limit expressed as a force plus a distance.

    Comparing a bare force against it is therefore wrong in both directions: a
    load at 40mm is twice as damaging as the rating allows while appearing to
    pass, and a load at 10mm is judged harshly for no reason. Both sides are
    converted to a moment about the mounting face before being compared, which
    is why `max_radial_at_mm` is mandatory in the catalogue.

    Axial thrust needs no such treatment. It loads the shaft along its axis
    regardless of where it originates, so the rating stands alone.
    """
    if shaft_load_n < 0:
        raise ValueError("shaft_load_n must not be negative")
    if load_type not in LOAD_TYPES:
        raise ValueError(f"load_type must be one of {LOAD_TYPES}")

    offset = offset_mm if offset_mm is not None else mount.shaft_load_offset_mm
    checks: list[Check] = []

    limit_n = mount.max_axial_n if load_type == "axial" else mount.max_radial_n
    limit_at = None if load_type == "axial" else mount.max_radial_at_mm

    if limit_n is None:
        checks.append(
            Check(
                level="WARN",
                message=(
                    f"No published {load_type} shaft limit is on file for "
                    f"{mount.name}, so {shaft_load_n:.0f}N has NOT been checked "
                    f"against the component. Absence of a limit is not a pass."
                ),
                remedy=(
                    f"Find the {load_type} rating in your part's datasheet and "
                    f"confirm this load is within it. If the rating is radial, "
                    f"note the distance it is quoted at -- it is a moment limit."
                ),
                code=SHAFT_LIMIT_UNKNOWN,
            )
        )
        return ShaftDecision(
            verdict=SHAFT_UNKNOWN,
            load_type=load_type,
            shaft_load_n=shaft_load_n,
            offset_mm=offset,
            limit_n=None,
            limit_at_mm=None,
            applied_n_mm=None,
            limit_n_mm=None,
            utilisation=None,
            checks=checks,
        )

    if load_type == "radial":
        applied = shaft_load_n * offset
        rated = limit_n * limit_at
        applied_desc = f"{shaft_load_n:.0f}N at {offset:g}mm ({applied:.0f}N.mm)"
        rated_desc = f"{limit_n:.0f}N at {limit_at:g}mm ({rated:.0f}N.mm)"
    else:
        applied = shaft_load_n
        rated = limit_n
        applied_desc = f"{shaft_load_n:.0f}N"
        rated_desc = f"{limit_n:.0f}N"

    utilisation = applied / rated if rated > 0 else math.inf

    if utilisation > 1.0:
        verdict = BEARING_REQUIRED
        checks.append(
            Check(
                level="LOUD WARN",
                message=(
                    f"{load_type.capitalize()} shaft load {applied_desc} exceeds the "
                    f"published limit {rated_desc} for {mount.name} "
                    f"({utilisation:.1f}x over). No bracket thickness fixes this -- "
                    f"the limit is the motor's own bearings, not the plate."
                ),
                source=mount.load_limit_source,
                remedy=_BYPASS_REMEDY[load_type],
                code=SHAFT_LIMIT,
            )
        )
    elif utilisation > BEARING_ADVISORY_UTILISATION:
        verdict = BEARING_RECOMMENDED
        checks.append(
            Check(
                level="WARN",
                message=(
                    f"{load_type.capitalize()} shaft load {applied_desc} is at "
                    f"{utilisation * 100:.0f}% of the published limit {rated_desc} "
                    f"for {mount.name}. Published ratings are absolute maxima with "
                    f"no margin, quoted for a steadier load than a real machine "
                    f"delivers."
                ),
                source=mount.load_limit_source,
                remedy=_BYPASS_REMEDY[load_type],
                code=SHAFT_LIMIT,
            )
        )
    else:
        verdict = SHAFT_OK
        checks.append(
            Check(
                level="PASS",
                message=(
                    f"{load_type.capitalize()} shaft load {applied_desc} is within "
                    f"the published limit {rated_desc} for {mount.name} "
                    f"({utilisation * 100:.0f}% utilised)."
                ),
                source=mount.load_limit_source,
                code=SHAFT_LIMIT,
            )
        )

    return ShaftDecision(
        verdict=verdict,
        load_type=load_type,
        shaft_load_n=shaft_load_n,
        offset_mm=offset,
        limit_n=limit_n,
        limit_at_mm=limit_at,
        applied_n_mm=applied,
        limit_n_mm=rated,
        utilisation=utilisation,
        checks=checks,
    )


REAR_FACE_MOUNTING = "rear_face_mounting"


def face_checks(mount: MountSpec, face: str) -> list[Check]:
    """What follows from bolting the motor by one face rather than the other.

    The mounting face is not a viewpoint. Front-mounted, the plate bolts to the
    shaft-end faceplate and the shaft passes through it. Rear-mounted, the
    shaft leaves the far end of the motor and never touches the plate. Those
    are different builds with different consequences, and the tool used to
    treat the choice as a display option -- which is why it produced identical
    output for both.
    """
    if mount.kind != "motor" or face != "back":
        return []

    checks = [
        Check(
            level="WARN",
            message=(
                f"Rear-face mounting: the shaft points away from this plate and "
                f"never passes through it. Confirm your {mount.name} actually has "
                f"rear tapped holes -- the NEMA standard puts the bolt pattern, "
                f"pilot boss and shaft all on the FRONT face, and rear holes are "
                f"a per-model extra that many frames do not have."
            ),
            remedy=(
                "Check the datasheet for your specific motor. If it has no rear "
                "holes, mount by the front face instead."
            ),
            code=REAR_FACE_MOUNTING,
        )
    ]
    if mount.center_hole_dia_mm > 0:
        checks.append(
            Check(
                level="INFO",
                message=(
                    f"The {mount.center_hole_dia_mm:g}mm centre bore clears a pilot "
                    f"boss that is on the other end of the motor in this "
                    f"configuration. Nothing registers in it, though it is still "
                    f"useful as a cable pass-through."
                ),
                code=REAR_FACE_MOUNTING,
            )
        )
    return checks


def required_thickness(
    mount: MountSpec,
    material: Material,
    plate_load_n: float = 0.0,
) -> ThicknessResult:
    """How thick the plate has to be, from manufacturing floors only.

    Two candidates, and the larger wins:

    - the minimum wall the chosen process can actually produce
    - the depth the bearing needs to seat in, when one is seated here

    Neither is a load calculation, and that is the honest answer rather than a
    reduced one -- see the module docstring for why the structural layer that
    used to sit here was removed rather than kept as a sanity check.
    """
    notes: list[Check] = []
    min_wall = MIN_WALL_MM[material.process]

    candidates = {f"process minimum wall ({material.process})": min_wall}

    # A plate that cannot hold the bearing is not a lighter plate, it is a
    # different part. Cutting a 9mm counterbore into a 1mm plate produces
    # geometry that cannot exist, so the seat depth belongs here rather than as
    # a warning issued after the number is already fixed.
    seat_min = 0.0
    if mount.bearing_width_mm > 0:
        if mount.bearing_seat_depth_mm > 0:
            seat_min = mount.bearing_seat_depth_mm + BEARING_SEAT_FLOOR_MM
            label = f"bearing seat ({mount.bearing_designation} counterbore + floor)"
        else:
            seat_min = mount.bearing_width_mm
            label = f"bearing seat ({mount.bearing_designation} outer-ring width)"

        # The direct topology stacks two features through the plate: the motor's
        # pilot boss needs a recess on the motor face, and the bearing seat opens
        # from the far face. Concentric and overlapping, they would leave nothing
        # gripping the outer ring, so the plate has to be deep enough for both
        # plus a floor between them.
        if mount.boss_recess_depth_mm > 0:
            stacked = (
                mount.boss_recess_depth_mm
                + mount.bearing_width_mm
                + BEARING_SEAT_FLOOR_MM
            )
            if stacked > seat_min:
                seat_min = stacked
                label = (
                    f"boss recess + {mount.bearing_designation} seat + floor "
                    f"(direct topology, features on opposite faces)"
                )
        candidates[label] = seat_min

    governing_limit = max(candidates, key=lambda k: candidates[k])
    required = candidates[governing_limit]

    if governing_limit.startswith("bearing seat"):
        notes.append(
            Check(
                level="INFO",
                message=(
                    f"Thickness is set by the bearing: the "
                    f"{mount.bearing_designation} needs {required:.2f}mm of plate to "
                    f"seat in, against a {min_wall:.2f}mm process floor."
                ),
                code=BEARING_SEAT_GOVERNS,
            )
        )
    else:
        notes.append(
            Check(
                level="INFO",
                message=(
                    f"Thickness is set by what {material.process} can produce "
                    f"({min_wall:.2f}mm), not by the load. This part is not "
                    f"structurally limited at this scale."
                ),
                code=PROCESS_FLOOR_GOVERNS,
            )
        )

    if plate_load_n > 0:
        notes.append(
            Check(
                level="WARN",
                message=(
                    f"{plate_load_n:.0f}N is fastened to the bracket rather than "
                    f"applied at the shaft, so it is NOT checked against the "
                    f"component's shaft rating -- that rating protects the motor's "
                    f"bearings, which this load never reaches. It is also not "
                    f"modelled structurally."
                ),
                remedy=(
                    "For a bracket load this size, check the plate and its "
                    "fasteners yourself. ZooMounter sizes for manufacturability "
                    "and bearing fit, not for arbitrary bracket loads."
                ),
                code=PLATE_LOAD_UNMODELLED,
            )
        )

    if material.process == "3d_print":
        notes.append(
            Check(
                level="WARN",
                message=(
                    "Thermal creep is not modelled. A NEMA case runs at 70-80 C and "
                    "PLA softens well below that, so a printed bracket bolted "
                    "directly to a hot motor can sag in service at a load it held "
                    "when new."
                ),
                remedy=(
                    "Print in PETG or ABS, add a thermal break or standoffs "
                    "between motor and bracket, or machine the part in metal."
                ),
                code=THERMAL_UNMODELLED,
            )
        )

    return ThicknessResult(
        required_thickness_mm=required,
        governing_limit=governing_limit,
        min_wall_mm=min_wall,
        bearing_seat_min_mm=seat_min,
        plate_load_n=plate_load_n,
        notes=notes,
    )
