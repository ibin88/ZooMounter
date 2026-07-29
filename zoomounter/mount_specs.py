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
        return net_area * thickness_mm


MOUNTS: dict[str, MountSpec] = {
    "nema17": MountSpec(
        name="NEMA 17 stepper motor mount",
        kind="motor",
        plate_width_mm=42.3,
        plate_height_mm=42.3,
        # M3 fasteners. 3.4mm is ISO 273 "normal" clearance -- a 3.0mm hole
        # would be an interference fit on an M3 screw, not a clearance hole.
        bolt_hole_dia_mm=3.4,
        # 31mm SQUARE spacing (NEMA standard), not a bolt circle. See
        # square_bolt_pattern() for why that distinction matters.
        hole_positions=square_bolt_pattern(31.0),
        center_hole_dia_mm=22.0,
        typical_mass_kg=0.28,  # representative mid-length NEMA17 (~40mm body); varies ~0.2-0.4kg by length
        shaft_load_offset_mm=15.0,
        body_cg_offset_mm=20.0,
    ),
    "nema23": MountSpec(
        name="NEMA 23 stepper motor mount",
        kind="motor",
        plate_width_mm=56.4,
        plate_height_mm=56.4,
        bolt_hole_dia_mm=5.5,  # M5 fasteners, ISO 273 normal clearance
        hole_positions=square_bolt_pattern(47.14),  # 47.14mm square spacing, not a bolt circle
        center_hole_dia_mm=38.1,
        typical_mass_kg=0.7,  # representative mid-length NEMA23 (~56mm body); varies ~0.5-1.0kg by length
        shaft_load_offset_mm=15.0,
        body_cg_offset_mm=28.0,
    ),
    "bearing_608": MountSpec(
        name="608 bearing (skate bearing) pillow mount",
        kind="bearing",
        plate_width_mm=40.0,
        plate_height_mm=40.0,
        # M3 normal clearance (ISO 273). Was 3.0mm, which is smaller than an M3
        # screw -- the same interference bug that was fixed on the NEMA mounts,
        # missed here because the regression test only covered NEMA.
        bolt_hole_dia_mm=3.4,
        # Unlike NEMA, a 608 pillow mount has no published bolt pattern -- this
        # is a chosen 34mm bolt circle, and genuinely circular, so
        # circular_bolt_pattern is correct here rather than square_bolt_pattern.
        hole_positions=circular_bolt_pattern(4, 34.0),
        center_hole_dia_mm=22.0,  # bearing OD -- see module docstring
    ),
    "raspberry_pi": MountSpec(
        name="Raspberry Pi mounting plate (Model B+/2/3/4 hole pattern)",
        kind="board",
        plate_width_mm=65.0,
        plate_height_mm=56.0,
        bolt_hole_dia_mm=2.7,
        hole_positions=((-29.0, -24.5), (29.0, -24.5), (-29.0, 24.5), (29.0, 24.5)),  # 58mm x 49mm spacing
        center_hole_dia_mm=0,
    ),
    "vesa_75": MountSpec(
        name="VESA 75 mount (screen/panel bracket)",
        kind="flange",
        plate_width_mm=90.0,
        plate_height_mm=90.0,
        bolt_hole_dia_mm=4.3,  # M4 clearance
        hole_positions=((-37.5, -37.5), (37.5, -37.5), (-37.5, 37.5), (37.5, 37.5)),
        center_hole_dia_mm=0,
    ),
}


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

    if host_mount in ("2020-slots", "4040-slots"):
        # Auto-calculate wing spacing (round up to nearest 20mm multiple)
        spacing = math.ceil((base.plate_width_mm + 15) / 20) * 20
        
        # If user didn't override, set width to encompass the slots with 10mm margins
        if not plate_width_override:
            width = spacing + 20
            
        slot_len = 15.0
        slot_wid = 5.5  # M5 clearance
        
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

    # Return a new MountSpec using object.__setattr__ to bypass frozen=True
    new_spec = MountSpec(
        name=f"{base.name} (with {host_mount})",
        kind=base.kind,
        plate_width_mm=width,
        plate_height_mm=base.plate_height_mm,
        bolt_hole_dia_mm=base.bolt_hole_dia_mm,
        hole_positions=base.hole_positions,
        host_holes=tuple(holes),
        host_slots=tuple(slots),
        center_hole_dia_mm=base.center_hole_dia_mm,
        typical_mass_kg=base.typical_mass_kg,
        shaft_load_offset_mm=base.shaft_load_offset_mm,
        body_cg_offset_mm=base.body_cg_offset_mm,
    )
    return new_spec
