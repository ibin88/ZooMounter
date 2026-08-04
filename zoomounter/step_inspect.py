"""Local STEP geometry inspection -- no API calls, no credits.

This exists because scalar checks (volume, mass) fundamentally cannot see
*where* geometry is. A hole displaced 5mm sideways removes exactly the same
amount of material, so a volume check stays green on a part that won't bolt
onto anything. To actually verify a generated mount matches the spec it was
given, you have to read the positions back out of the geometry.

STEP (ISO-10303-21) is a plain-text format, so that's a local parse of a
file we already have on disk:

    #199 = CARTESIAN_POINT('', (0.0155, 0., -0.0005));
    #200 = AXIS2_PLACEMENT_3D('', #199, #198, #197);
    #201 = CIRCLE('', #200, 0.0015);

A CIRCLE points at a placement, which points at the cartesian point that is
the circle's center. Resolve that chain for every circle and you have every
hole center and radius in the part.

Caveats, stated plainly:
- Only circles are extracted. A hole modeled as anything other than a
  circular edge (a splined bore, a slot) is invisible to this.
- Each physical hole produces several circles (top face, bottom face, and
  often split arcs), so coincident circles are merged.
- Cylindrical *surfaces* are ignored in favour of circular edges; both
  describe the same holes, and edges carry the z-extent we want to ignore
  anyway.
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path

# Entity forms we care about. STEP allows whitespace/newlines fairly freely,
# so these are matched against the file with newlines collapsed.
_CARTESIAN_POINT_RE = re.compile(
    r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)", re.IGNORECASE
)

# The CARTESIAN_POINT a VERTEX_POINT resolves to. These are the only points
# that are corners of the finished solid; everything else in the file may be
# construction or tool geometry that the booleans consumed.
_VERTEX_POINT_RE = re.compile(
    r"VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*\)", re.IGNORECASE
)
_AXIS_PLACEMENT_RE = re.compile(
    r"#(\d+)\s*=\s*AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)", re.IGNORECASE
)
_CIRCLE_RE = re.compile(
    r"#(\d+)\s*=\s*CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([0-9eE.+-]+)\s*\)", re.IGNORECASE
)
_LENGTH_UNIT_RE = re.compile(
    r"LENGTH_UNIT\s*\(\s*\).*?SI_UNIT\s*\(\s*(\.\w+\.|\$)\s*,\s*\.METRE\.", re.IGNORECASE | re.DOTALL
)

# SI prefixes that can appear on a STEP length unit, as multipliers to metres.
_SI_PREFIX_TO_METRES = {
    "$": 1.0,  # no prefix -> plain metres
    ".MILLI.": 1e-3,
    ".CENTI.": 1e-2,
    ".DECI.": 1e-1,
    ".MICRO.": 1e-6,
    ".KILO.": 1e3,
}

# Two circles closer than this (in mm, after unit conversion) with the same
# radius are treated as the same physical hole seen from a different face.
_COINCIDENT_TOLERANCE_MM = 0.01


class StepParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Circle:
    """A circular edge found in the file, in mm, projected onto XY."""

    x_mm: float
    y_mm: float
    radius_mm: float

    @property
    def diameter_mm(self) -> float:
        return self.radius_mm * 2


@dataclass
class BoundingBox:
    min_x_mm: float
    max_x_mm: float
    min_y_mm: float
    max_y_mm: float
    min_z_mm: float
    max_z_mm: float

    @property
    def width_mm(self) -> float:
        return self.max_x_mm - self.min_x_mm

    @property
    def height_mm(self) -> float:
        return self.max_y_mm - self.min_y_mm

    @property
    def thickness_mm(self) -> float:
        return self.max_z_mm - self.min_z_mm


@dataclass
class StepGeometry:
    circles: tuple[Circle, ...]
    bbox: BoundingBox


def _scale_to_mm(text: str) -> float:
    """Figure out what one length unit in this file is worth in mm."""
    match = _LENGTH_UNIT_RE.search(text)
    if not match:
        # Nothing to key off. Metres is what Zoo's exporter emits, and STEP's
        # default is metres, but this is a guess -- say so rather than
        # silently returning numbers that could be off by 1000x.
        raise StepParseError(
            "Could not find a LENGTH_UNIT declaration in the STEP file, so the "
            "unit scale is unknown. Refusing to report dimensions that might be "
            "off by a factor of 1000."
        )
    prefix = match.group(1).upper()
    try:
        metres_per_unit = _SI_PREFIX_TO_METRES[prefix]
    except KeyError as e:
        raise StepParseError(f"Unsupported STEP length unit prefix: {prefix}") from e
    return metres_per_unit * 1000.0  # -> mm


def _parse_coords(raw: str) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in raw.split(",") if part.strip())


def parse_step(step_path: Path) -> StepGeometry:
    """Extract circular edges and the overall bounding box from a STEP file."""
    text = step_path.read_text(encoding="utf-8", errors="replace")
    scale = _scale_to_mm(text)

    points: dict[str, tuple[float, ...]] = {}
    for entity_id, raw_coords in _CARTESIAN_POINT_RE.findall(text):
        coords = _parse_coords(raw_coords)
        if len(coords) == 3:
            points[entity_id] = coords

    if not points:
        raise StepParseError(f"No CARTESIAN_POINT entities found in {step_path}")

    placements: dict[str, str] = dict(_AXIS_PLACEMENT_RE.findall(text))

    circles: list[Circle] = []
    for _circle_id, placement_id, raw_radius in _CIRCLE_RE.findall(text):
        point_id = placements.get(placement_id)
        if point_id is None:
            continue
        point = points.get(point_id)
        if point is None:
            continue
        circles.append(
            Circle(
                x_mm=point[0] * scale,
                y_mm=point[1] * scale,
                radius_mm=float(raw_radius) * scale,
            )
        )

    # Bound the SOLID, using only points that are vertices of it.
    #
    # Every CARTESIAN_POINT in the file is not the part. A subtract leaves
    # behind the geometry that defined the cutting tool, and a tool is
    # deliberately larger than the body it cuts. On a thrust-bearing block --
    # counterbore revolved from a profile spanning +/-11mm, subtracted from an
    # 11mm plate -- measuring every point gave a 16.50mm thickness for a part
    # that is exactly 11.00mm, and the verifier failed a correct part by 50%.
    #
    # VERTEX_POINT entities are the corners of the trimmed result, so they
    # describe what survived the boolean rather than what went into it.
    vertex_ids = set(_VERTEX_POINT_RE.findall(text))
    bounded = [points[i] for i in vertex_ids if i in points]
    if not bounded:
        # Nothing vertex-referenced (a purely cylindrical body, or a writer
        # that names entities differently). Fall back rather than crash, since
        # an over-large box is still more useful than no answer.
        bounded = list(points.values())

    xs = [p[0] * scale for p in bounded]
    ys = [p[1] * scale for p in bounded]
    zs = [p[2] * scale for p in bounded]
    bbox = BoundingBox(min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

    return StepGeometry(circles=_merge_coincident(circles), bbox=bbox)


def _merge_coincident(circles: list[Circle]) -> tuple[Circle, ...]:
    """One physical hole shows up as several circular edges (top face, bottom
    face, split arcs). Collapse those into one entry each."""
    unique: list[Circle] = []
    for circle in circles:
        for existing in unique:
            same_place = (
                math.hypot(circle.x_mm - existing.x_mm, circle.y_mm - existing.y_mm)
                <= _COINCIDENT_TOLERANCE_MM
            )
            same_size = abs(circle.radius_mm - existing.radius_mm) <= _COINCIDENT_TOLERANCE_MM
            if same_place and same_size:
                break
        else:
            unique.append(circle)
    return tuple(unique)


@dataclass
class HoleMatch:
    """One expected hole, paired with the closest circle actually found."""

    expected_x_mm: float
    expected_y_mm: float
    expected_dia_mm: float
    found: bool
    actual_x_mm: float | None = None
    actual_y_mm: float | None = None
    actual_dia_mm: float | None = None
    position_error_mm: float | None = None
    diameter_error_mm: float | None = None


def match_holes(
    geometry: StepGeometry,
    expected: list[tuple[float, float, float]],
    diameter_tolerance_mm: float = 0.5,
) -> list[HoleMatch]:
    """Pair each expected (x, y, diameter) against the nearest unclaimed
    circle of roughly the right size.

    Size is matched first and position second, deliberately: a hole of the
    right diameter in the wrong place is the failure we're hunting for, and
    matching on position first would happily pair a bolt hole with the big
    central bore if the part came out scrambled.
    """
    remaining = list(geometry.circles)
    matches: list[HoleMatch] = []

    for exp_x, exp_y, exp_dia in expected:
        candidates = [
            c for c in remaining if abs(c.diameter_mm - exp_dia) <= diameter_tolerance_mm
        ]
        if not candidates:
            matches.append(
                HoleMatch(exp_x, exp_y, exp_dia, found=False)
            )
            continue

        best = min(candidates, key=lambda c: math.hypot(c.x_mm - exp_x, c.y_mm - exp_y))
        remaining.remove(best)
        matches.append(
            HoleMatch(
                expected_x_mm=exp_x,
                expected_y_mm=exp_y,
                expected_dia_mm=exp_dia,
                found=True,
                actual_x_mm=best.x_mm,
                actual_y_mm=best.y_mm,
                actual_dia_mm=best.diameter_mm,
                position_error_mm=math.hypot(best.x_mm - exp_x, best.y_mm - exp_y),
                diameter_error_mm=abs(best.diameter_mm - exp_dia),
            )
        )

    return matches
