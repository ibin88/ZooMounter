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
    the origin."""
    radius = circle_dia_mm / 2
    return tuple(
        (round(radius * math.cos(2 * math.pi * i / count), 4), round(radius * math.sin(2 * math.pi * i / count), 4))
        for i in range(count)
    )


@dataclass(frozen=True)
class MountSpec:
    name: str
    kind: str  # "motor", "bearing", "board", "flange"
    plate_width_mm: float  # X extent; also used as beam width in mechanics calc
    plate_height_mm: float  # Y extent
    bolt_hole_dia_mm: float  # uniform diameter for all mounting holes
    hole_positions: tuple[tuple[float, float], ...]  # (x, y) offsets from plate center
    center_hole_dia_mm: float = 0  # shaft/bore/press-fit clearance; 0 if none
    typical_mass_kg: float = 0  # approximate mass of the component itself (motor, etc); 0 if negligible/not applicable
    typical_body_length_mm: float = 0  # approximate distance from mount face to where an external side load
    # (belt, pulley, gear) would typically apply -- used as the default lever arm for radial-load mounts
    # instead of an arbitrary half-plate-width guess. 0 means "use half plate width" (no better default known).

    def estimate_volume_mm3(self, thickness_mm: float) -> float:
        """Solid plate volume minus all holes -- used to sanity-check the
        Agent API's generated geometry against a hand calc before we ever
        call the File Format API."""
        plate_area = self.plate_width_mm * self.plate_height_mm
        center_area = math.pi * (self.center_hole_dia_mm / 2) ** 2
        bolt_hole_area = len(self.hole_positions) * math.pi * (self.bolt_hole_dia_mm / 2) ** 2
        net_area = plate_area - center_area - bolt_hole_area
        return net_area * thickness_mm


MOUNTS: dict[str, MountSpec] = {
    "nema17": MountSpec(
        name="NEMA 17 stepper motor mount",
        kind="motor",
        plate_width_mm=42.3,
        plate_height_mm=42.3,
        bolt_hole_dia_mm=3.0,
        hole_positions=circular_bolt_pattern(4, 31.0),
        center_hole_dia_mm=22.0,
        typical_mass_kg=0.28,  # representative mid-length NEMA17 (~40mm body); varies ~0.2-0.4kg by length
        typical_body_length_mm=40.0,
    ),
    "nema23": MountSpec(
        name="NEMA 23 stepper motor mount",
        kind="motor",
        plate_width_mm=56.4,
        plate_height_mm=56.4,
        bolt_hole_dia_mm=5.0,
        hole_positions=circular_bolt_pattern(4, 47.14),
        center_hole_dia_mm=38.1,
        typical_mass_kg=0.7,  # representative mid-length NEMA23 (~56mm body); varies ~0.5-1.0kg by length
        typical_body_length_mm=56.0,
    ),
    "bearing_608": MountSpec(
        name="608 bearing (skate bearing) pillow mount",
        kind="bearing",
        plate_width_mm=40.0,
        plate_height_mm=40.0,
        bolt_hole_dia_mm=3.0,
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
