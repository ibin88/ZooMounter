"""Mount-type geometry table for ZooMounter.

Each entry describes a standard mounting interface as an explicit list of
hole positions (offsets from plate center) rather than assuming a circular
bolt pattern -- that's what lets the same model cover both classic circular
bolt-circle mounts (motors, bearings) and rectangular hole patterns from
real hardware standards (Raspberry Pi, VESA). `circular_bolt_pattern()` is
a helper for building the circular case concisely.

`--mount custom` lets a user supply their own circular bolt pattern for
anything not in the table.

Note on the bearing mount: v1 models the center feature as a plain
through-bore sized to the bearing OD (a prototyping-grade simplification --
a real pillow-block mount would need a shouldered pocket or retaining
feature to actually capture the bearing). Documented here rather than
hidden; flagged again in the README.
"""

import math
from dataclasses import replace
from dataclasses import dataclass


def circular_bolt_pattern(count: int, circle_dia_mm: float) -> tuple[tuple[float, float], ...]:
    """N holes evenly spaced on a circle of the given diameter, centered on
    the origin. The first hole sits on the +X axis."""
    radius = circle_dia_mm / 2
    return tuple(
        (round(radius * math.cos(2 * math.pi * i / count), 4), round(radius * math.sin(2 * math.pi * i / count), 4))
        for i in range(count)
    )


def square_bolt_pattern(spacing_mm: float) -> tuple[tuple[float, float], ...]:
    """Four holes at the corners of a square of the given side length,
    centred on the origin -- i.e. at (+/-s/2, +/-s/2).

    This is how NEMA motor faceplates are actually dimensioned, and getting
    it wrong is subtle: the NEMA spec quotes a *square spacing* between hole
    centres, NOT a bolt-circle diameter. Feeding that number to
    circular_bolt_pattern() puts the holes at the midpoints of the plate
    edges instead of the corners, at a radius of s/2 rather than s/sqrt(2).
    For a NEMA 17 that is a 6.4mm positional error on every hole, and the
    plate will not bolt to the motor.

    ZooMounter shipped exactly that bug until it was caught by comparing
    against a hand-built assembly. Its own verification never flagged it,
    because verification checks the generated part against this table -- so
    a wrong table produces a wrong part that passes every check. Worth
    remembering: self-consistent verification cannot catch a wrong spec.
    """
    half = spacing_mm / 2
    return (
        (round(half, 4), round(half, 4)),
        (round(half, 4), round(-half, 4)),
        (round(-half, 4), round(-half, 4)),
        (round(-half, 4), round(half, 4)),
    )


def rectangular_bolt_pattern(x_spacing_mm: float, y_spacing_mm: float) -> tuple[tuple[float, float], ...]:
    """Four holes at the corners of a rectangle, centred on the origin.

    Distinct from square_bolt_pattern because boards are not square: a
    Raspberry Pi is 58mm across and 49mm deep between hole centres, and
    collapsing that to one number is the same class of mistake as treating a
    square spacing as a bolt circle."""
    hx, hy = x_spacing_mm / 2, y_spacing_mm / 2
    return (
        (round(hx, 4), round(hy, 4)),
        (round(hx, 4), round(-hy, 4)),
        (round(-hx, 4), round(-hy, 4)),
        (round(-hx, 4), round(hy, 4)),
    )


@dataclass(frozen=True)
class MountSpec:
    name: str
    kind: str  # "motor", "bearing", "board", "flange"
    plate_width_mm: float  # X extent; also used as beam width in mechanics calc
    plate_height_mm: float  # Y extent
    bolt_hole_dia_mm: float  # uniform diameter for all mounting holes
    hole_positions: tuple[tuple[float, float], ...]  # (x, y) offsets from plate center
    host_holes: tuple[tuple[float, float, float], ...] = ()  # (x, y, dia)
    host_slots: tuple[tuple[float, float, float, float, str], ...] = ()  # (x, y, length, width, direction='x' or 'y')
    center_hole_dia_mm: float = 0  # shaft/bore/press-fit clearance; 0 if none
    typical_mass_kg: float = 0  # approximate mass of the component itself (motor, etc); 0 if negligible/not applicable
    shaft_load_offset_mm: float = 15.0  # distance from mount face to where external side load acts (forward)
    body_cg_offset_mm: float = 0  # distance from mount face to component CG (backward). 0 if negligible.

    # Published shaft load limits for the COMPONENT itself, not the plate. These
    # bound what the motor/bearing can survive regardless of how thick we make
    # the bracket. None means "no published figure known" -- which must read as
    # unknown, never as unlimited.
    #
    # These are per-mount and NOT interchangeable between frame sizes. A single
    # limit applied across every motor is the same defect class as the NEMA
    # bolt-pattern bug: one constant used where it does not belong.
    max_axial_n: float | None = None
    max_radial_n: float | None = None
    load_limit_source: str = ""  # primary source for the two figures above

    # The SHAFT diameter, which is not center_hole_dia_mm. On a NEMA 17 the
    # centre hole is 22mm because that is the pilot boss the plate registers
    # on; the shaft running through it is 5mm. Bearing selection needs the
    # shaft, so confusing the two would pick a bearing four sizes too big.
    shaft_dia_mm: float = 0  # 0 = unknown, which blocks bearing selection

    # Bearing seat, filled in by bearings.apply_bearing(). Kept as plain
    # scalars so mount_specs stays a leaf module with no imports of its own.
    bearing_designation: str = ""
    bearing_seat_dia_mm: float = 0  # bearing OD; the plate is bored to this
    bearing_seat_depth_mm: float = 0  # 0 = through-bore, >0 = blind counterbore
    bearing_width_mm: float = 0  # how much plate the bearing needs to sit in

    def estimate_volume_mm3(self, thickness_mm: float) -> float:
        """Solid plate volume minus all holes -- used to sanity-check the
        Agent API's generated geometry against a hand calc before we ever
        call the File Format API."""
        plate_area = self.plate_width_mm * self.plate_height_mm
        center_area = math.pi * (self.center_hole_dia_mm / 2) ** 2
        bolt_hole_area = len(self.hole_positions) * math.pi * (self.bolt_hole_dia_mm / 2) ** 2
        host_hole_area = sum(math.pi * (dia / 2) ** 2 for _, _, dia in self.host_holes)
        host_slot_area = sum(length * width for _, _, length, width, _ in self.host_slots) # rough rect area
        net_area = plate_area - center_area - bolt_hole_area - host_hole_area - host_slot_area

        seat_area = math.pi * (self.bearing_seat_dia_mm / 2) ** 2
        if self.bearing_seat_depth_mm > 0:
            # Blind counterbore: removes material only to its own depth, and
            # the shaft hole already counted in center_area runs through the
            # rest. Subtracting the full-thickness column here would
            # double-count and under-report the part by the shaft volume.
            annulus = max(seat_area - center_area, 0.0)
            depth = min(self.bearing_seat_depth_mm, thickness_mm)
            return net_area * thickness_mm - annulus * depth
        # Through-bore: the seat is a hole like any other.
        return (net_area - seat_area) * thickness_mm


def _build_mounts() -> dict[str, MountSpec]:
    """Build the mount table from data/mounts.toml.

    Validation happens in catalogue.load_mounts(), which raises rather than
    returning a half-trusted row. A malformed catalogue stops the import: a
    tool whose whole claim is "the geometry matches the spec" has no business
    running on a spec it could not check.
    """
    from .catalogue import expand_bolt_pattern, load_mounts

    built: dict[str, MountSpec] = {}
    for row in load_mounts():
        key = row["key"]
        built[key] = MountSpec(
            name=row["name"],
            kind=row["kind"],
            plate_width_mm=row["plate_width_mm"],
            plate_height_mm=row["plate_height_mm"],
            bolt_hole_dia_mm=row["bolt_hole_dia_mm"],
            hole_positions=expand_bolt_pattern(
                row["bolt_pattern"], f"mounts.toml [{key}]"
            ),
            center_hole_dia_mm=row.get("center_hole_dia_mm", 0),
            typical_mass_kg=row.get("typical_mass_kg", 0),
            shaft_load_offset_mm=row.get("shaft_load_offset_mm", 15.0),
            body_cg_offset_mm=row.get("body_cg_offset_mm", 0),
            max_axial_n=row.get("max_axial_n"),
            max_radial_n=row.get("max_radial_n"),
            load_limit_source=row.get("load_limit_source", ""),
            shaft_dia_mm=row.get("shaft_dia_mm", 0),
        )
    return built


MOUNTS: dict[str, MountSpec] = _build_mounts()

def get_mount(
    name: str,
    plate_width_mm: float | None = None,
    bolt_count: int | None = None,
    bolt_circle_dia_mm: float | None = None,
    bolt_hole_dia_mm: float | None = None,
    center_hole_dia_mm: float = 0,
) -> MountSpec:
    """Look up a built-in mount, or build a custom circular-bolt-pattern
    flange from explicit values."""
    if name == "custom":
        missing = [
            n
            for n, v in [
                ("plate-width-mm", plate_width_mm),
                ("bolt-count", bolt_count),
                ("bolt-circle-dia-mm", bolt_circle_dia_mm),
                ("bolt-hole-dia-mm", bolt_hole_dia_mm),
            ]
            if v is None
        ]
        if missing:
            raise ValueError(f"--mount custom requires all of: {', '.join(missing)}")
        return MountSpec(
            name="Custom flange mount",
            kind="flange",
            plate_width_mm=plate_width_mm,
            plate_height_mm=plate_width_mm,
            bolt_hole_dia_mm=bolt_hole_dia_mm,
            hole_positions=circular_bolt_pattern(bolt_count, bolt_circle_dia_mm),
            center_hole_dia_mm=center_hole_dia_mm,
        )

    try:
        return MOUNTS[name]
    except KeyError as e:
        raise ValueError(
            f"Unknown mount '{name}'. Choose from {list(MOUNTS)} or use 'custom'."
        ) from e


# T-slot extrusion families, keyed by the --host-mount option name.
#
# The two series differ in slot pitch AND fastener size. An earlier revision
# rounded both to a 20mm pitch with M5 clearance, which made --host-mount
# 4040-slots byte-identical to 2020-slots: the option parsed, generated, and
# verified, while silently doing nothing.
EXTRUSION_SERIES: dict[str, dict[str, float]] = {
    "2020-slots": {
        "pitch_mm": 20.0,        # 20-series profile, 20mm slot pitch
        "bolt_clearance_mm": 5.5,  # M5 T-nut, ISO 273 normal clearance
        "slot_length_mm": 15.0,
        "edge_margin_mm": 10.0,
        "clearance_mm": 15.0,    # gap past the motor body before the first slot
    },
    "4040-slots": {
        "pitch_mm": 40.0,        # 40-series profile, 40mm slot pitch
        "bolt_clearance_mm": 9.0,  # M8 T-nut, ISO 273 normal clearance
        "slot_length_mm": 20.0,
        "edge_margin_mm": 15.0,
        "clearance_mm": 20.0,
    },
}


def apply_host_mount(
    base: MountSpec,
    host_mount: str,
    host_slot_direction: str = "parallel",
    plate_width_override: float | None = None
) -> MountSpec:
    """Return a new MountSpec modified with host-side mounting features."""
    if host_mount == "none":
        return base
        
    width = plate_width_override or base.plate_width_mm
    slots = []
    holes = []

    if host_mount in EXTRUSION_SERIES:
        series = EXTRUSION_SERIES[host_mount]
        pitch = series["pitch_mm"]

        # Wing spacing snaps to the extrusion's own slot pitch, so the bolts
        # land in adjacent T-slots. 20-series and 40-series have DIFFERENT
        # pitches -- rounding both to 20mm made 4040 a silent alias of 2020.
        spacing = math.ceil((base.plate_width_mm + series["clearance_mm"]) / pitch) * pitch

        # If the user didn't override, widen the plate to carry the slots with
        # an edge margin scaled to the fastener size.
        if not plate_width_override:
            width = spacing + 2 * series["edge_margin_mm"]

        slot_len = series["slot_length_mm"]
        slot_wid = series["bolt_clearance_mm"]

        dir_char = "y" if host_slot_direction in ("parallel", "y") else "x"
        slots = [
            (-spacing/2, 0, slot_len, slot_wid, dir_char),
            (spacing/2, 0, slot_len, slot_wid, dir_char)
        ]

    elif host_mount == "corner-holes":
        # 10mm inset from corners
        inset = 10
        if not plate_width_override:
            width = base.plate_width_mm + 30 # arbitrary expansion
        
        h = base.plate_height_mm
        hx, hy = width/2 - inset, h/2 - inset
        dia = 5.5
        holes = [
            (hx, hy, dia), (hx, -hy, dia),
            (-hx, hy, dia), (-hx, -hy, dia)
        ]

    # dataclasses.replace, NOT a hand-written MountSpec(...) call. The previous
    # version listed every field to copy, and silently dropped the three load
    # limit fields when they were added -- so any part with host-side features
    # reported "no published limit on file" and lost its safety warning
    # entirely. replace() carries every field forward by construction, so a
    # future field cannot go missing the same way.
    return replace(
        base,
        name=f"{base.name} (with {host_mount})",
        plate_width_mm=width,
        host_holes=tuple(holes),
        host_slots=tuple(slots),
    )
