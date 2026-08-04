"""Verify step: check the generated part against the spec it was asked for.

## Why the checks are what they are

An earlier version of this file ran two checks -- volume and mass -- and the
README claimed that "checking both catches more than either alone." That was
wrong, and it's worth recording why, because it's an easy trap:

    expected_mass = expected_volume * density        (our hand calc)
    actual_mass   = actual_volume   * density        (what /file/mass does)

Both sides of the mass check are the volume check multiplied by the same
constant, so the two percentage errors are algebraically identical. Every
example report we'd generated showed exactly that (0.2%/0.2%, 0.1%/0.1%) and
it read as reassuring agreement rather than as the tautology it was.

Worse, *both* were blind to the failure that actually matters here. Volume is
a scalar. Move a bolt hole 5mm sideways and you remove precisely the same
amount of material, so a volume check stays green on a plate that will not
bolt onto the motor it was designed for. A tool whose entire pitch is
"verification you can trust" cannot be blind to a part being the right size
and the wrong shape.

## The checks now

1. **Hole positions** -- every hole's centre and diameter, read back out of
   the STEP file and compared to the exact (x, y) coordinates we asked the
   Agent API for. This is the one that catches geometric drift, and it is
   local text parsing (see step_inspect), so it costs no API credits.
2. **Bounding box** -- the plate's outer envelope and thickness, also read
   from the STEP file.
3. **Volume** -- measured by Zoo's File Format API, against a hand-calc of
   plate-minus-holes. Catches material distributed wrongly in ways the
   first two checks might not (an unrequested pocket, a boss, a fillet).

Mass is still reported, because it's what a user actually wants to know
about a part, but it is presented as a derived *property*, not as an
independent check -- because it isn't one.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .materials import Material
from .mount_specs import MountSpec
from .step_inspect import StepGeometry, StepParseError, match_holes, parse_step

API_BASE = "https://api.zoo.dev"

# Generation isn't pixel-perfect and our hand-calc volume ignores fillets and
# other minor modelling choices, so allow some slack on the bulk checks.
TOLERANCE_FRACTION = 0.15

# Position tolerance is far tighter, and deliberately absolute rather than a
# percentage: a bolt hole is either where the bolt is or it isn't. 0.5mm on a
# 3mm hole already means the fastener is fouling the edge.
POSITION_TOLERANCE_MM = 0.5

# How far a hole's diameter may differ before we refuse to consider it "that
# hole" at all when pairing up expected against measured.
DIAMETER_MATCH_TOLERANCE_MM = 0.5


class VerificationError(RuntimeError):
    pass


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    expected: float | None = None
    actual: float | None = None
    percent_diff: float | None = None


@dataclass
class VerificationResult:
    checks: list[CheckResult] = field(default_factory=list)
    mass_g: float | None = None  # reported property, not a check -- see module docstring
    hole_details: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def _api_token() -> str:
    token = os.environ.get("ZOO_API_TOKEN")
    if not token:
        raise VerificationError(
            "ZOO_API_TOKEN is not set. Copy .env.example to .env and add your token."
        )
    return token


def _percent_diff(expected: float, actual: float) -> float:
    return abs(actual - expected) / expected * 100


def measure_actual_volume_mm3(step_path: Path) -> float:
    resp = requests.post(
        f"{API_BASE}/file/volume",
        headers={
            "Authorization": f"Bearer {_api_token()}",
            "Content-Type": "application/octet-stream",
        },
        params={"src_format": "step", "output_unit": "mm3"},
        data=step_path.read_bytes(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "completed" or "volume" not in data:
        raise VerificationError(f"File Format API did not return a volume: {data}")
    return data["volume"]


def measure_actual_mass_g(step_path: Path, material: Material) -> float:
    resp = requests.post(
        f"{API_BASE}/file/mass",
        headers={
            "Authorization": f"Bearer {_api_token()}",
            "Content-Type": "application/octet-stream",
        },
        params={
            "src_format": "step",
            "material_density": material.density_kg_m3,
            "material_density_unit": "kg:m3",
            "output_unit": "g",
        },
        data=step_path.read_bytes(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "completed" or "mass" not in data:
        raise VerificationError(f"File Format API did not return a mass: {data}")
    return data["mass"]


def expected_holes(mount: MountSpec) -> list[tuple[float, float, float]]:
    """Every hole we asked for, as (x, y, diameter) in mm."""
    holes = [(x, y, mount.bolt_hole_dia_mm) for x, y in mount.hole_positions]
    if mount.center_hole_dia_mm > 0:
        holes.append((0.0, 0.0, mount.center_hole_dia_mm))
    # The bearing seat. On a thrust block this is a blind counterbore
    # concentric with the shaft hole, so the STEP carries two circles at the
    # origin -- the probe in probes/results/counterbore confirmed the API
    # emits both, which is what makes this checkable.
    if mount.bearing_seat_dia_mm > 0:
        holes.append((0.0, 0.0, mount.bearing_seat_dia_mm))
        
    for x, y, dia in mount.host_holes:
        holes.append((x, y, dia))
        
    for x, y, length, width, direction in mount.host_slots:
        # A slot of length L and width W has semicircular ends.
        # The centers of these semicircles are offset from the slot center.
        offset = (length - width) / 2.0
        if direction == 'y':
            holes.append((x, y - offset, width))
            holes.append((x, y + offset, width))
        else:
            holes.append((x - offset, y, width))
            holes.append((x + offset, y, width))
            
    return holes


def check_hole_positions(geometry: StepGeometry, mount: MountSpec) -> tuple[CheckResult, list]:
    expected = expected_holes(mount)
    matches = match_holes(geometry, expected, diameter_tolerance_mm=DIAMETER_MATCH_TOLERANCE_MM)

    missing = [m for m in matches if not m.found]
    displaced = [
        m for m in matches if m.found and m.position_error_mm > POSITION_TOLERANCE_MM
    ]
    worst = max((m.position_error_mm for m in matches if m.found), default=None)

    if missing:
        detail = (
            f"{len(missing)} of {len(expected)} expected holes not found in the generated "
            f"geometry (no circle of the right diameter within tolerance)"
        )
        passed = False
    elif displaced:
        detail = (
            f"{len(displaced)} of {len(expected)} holes are further than "
            f"{POSITION_TOLERANCE_MM}mm from where they were specified "
            f"(worst: {worst:.3f}mm)"
        )
        passed = False
    else:
        detail = (
            f"all {len(expected)} holes present and within {POSITION_TOLERANCE_MM}mm of spec "
            f"(worst: {worst:.3f}mm)"
        )
        passed = True

    return CheckResult(name="Hole positions", passed=passed, detail=detail), matches


def check_bounding_box(geometry: StepGeometry, mount: MountSpec, thickness_mm: float) -> CheckResult:
    bbox = geometry.bbox
    comparisons = [
        ("width", mount.plate_width_mm, bbox.width_mm),
        ("height", mount.plate_height_mm, bbox.height_mm),
        ("thickness", thickness_mm, bbox.thickness_mm),
    ]
    worst_name, worst_pct = max(
        ((name, _percent_diff(expected, actual)) for name, expected, actual in comparisons),
        key=lambda pair: pair[1],
    )

    passed = (worst_pct / 100) <= TOLERANCE_FRACTION
    detail = (
        f"{bbox.width_mm:.2f} x {bbox.height_mm:.2f} x {bbox.thickness_mm:.2f} mm "
        f"vs specified {mount.plate_width_mm:.2f} x {mount.plate_height_mm:.2f} x {thickness_mm:.2f} mm "
        f"(worst axis: {worst_name}, {worst_pct:.1f}%)"
    )
    return CheckResult(
        name="Bounding box", passed=passed, detail=detail, percent_diff=worst_pct
    )


def check_volume(step_path: Path, mount: MountSpec, thickness_mm: float) -> CheckResult:
    expected = mount.estimate_volume_mm3(thickness_mm)
    actual = measure_actual_volume_mm3(step_path)
    pct = _percent_diff(expected, actual)
    return CheckResult(
        name="Volume",
        passed=(pct / 100) <= TOLERANCE_FRACTION,
        detail=f"{actual:.1f} mm3 measured vs {expected:.1f} mm3 from the requested geometry ({pct:.1f}%)",
        expected=expected,
        actual=actual,
        percent_diff=pct,
    )


def verify(
    step_path: Path,
    mount: MountSpec,
    material: Material,
    thickness_mm: float,
) -> VerificationResult:
    result = VerificationResult()

    try:
        geometry = parse_step(step_path)
    except StepParseError as e:
        # A file we can't read geometry out of is a failed verification, not a
        # crash -- the part exists, we just can't vouch for it.
        result.checks.append(
            CheckResult(
                name="Hole positions",
                passed=False,
                detail=f"could not parse geometry from the STEP file: {e}",
            )
        )
        result.checks.append(
            CheckResult(name="Bounding box", passed=False, detail="skipped (STEP parse failed)")
        )
    else:
        hole_check, matches = check_hole_positions(geometry, mount)
        result.checks.append(hole_check)
        result.hole_details = matches
        result.checks.append(check_bounding_box(geometry, mount, thickness_mm))

    result.checks.append(check_volume(step_path, mount, thickness_mm))
    result.mass_g = measure_actual_mass_g(step_path, material)

    return result
