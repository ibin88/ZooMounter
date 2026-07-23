"""Mount-type geometry table for ZooMounter.

Each entry describes a standard mounting interface: plate/flange footprint,
bolt circle, hole sizes, and any center clearance bore. `--mount custom` lets
a user supply their own bolt pattern for anything not in the table.

Note on the bearing mount: v1 models the center feature as a plain
through-bore sized to the bearing OD (a prototyping-grade simplification --
a real pillow-block mount would need a shouldered pocket or retaining
feature to actually capture the bearing). Documented here rather than
hidden; flagged again in the README.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MountSpec:
    name: str
    kind: str  # "motor", "bearing", "flange"
    plate_width_mm: float  # square plate footprint; used as beam width in mechanics calc
    bolt_count: int
    bolt_circle_dia_mm: float
    bolt_hole_dia_mm: float
    center_hole_dia_mm: float  # shaft/bore/press-fit clearance; 0 if none

    def estimate_volume_mm3(self, thickness_mm: float) -> float:
        """Solid plate volume minus all through-holes -- used to sanity-check
        the Agent API's generated geometry against a hand calc before we ever
        call the File Format API."""
        plate_area = self.plate_width_mm**2
        center_area = math.pi * (self.center_hole_dia_mm / 2) ** 2
        bolt_hole_area = self.bolt_count * math.pi * (self.bolt_hole_dia_mm / 2) ** 2
        net_area = plate_area - center_area - bolt_hole_area
        return net_area * thickness_mm


MOUNTS: dict[str, MountSpec] = {
    "nema17": MountSpec(
        name="NEMA 17 stepper motor mount",
        kind="motor",
        plate_width_mm=42.3,
        bolt_count=4,
        bolt_circle_dia_mm=31.0,
        bolt_hole_dia_mm=3.0,
        center_hole_dia_mm=22.0,
    ),
    "nema23": MountSpec(
        name="NEMA 23 stepper motor mount",
        kind="motor",
        plate_width_mm=56.4,
        bolt_count=4,
        bolt_circle_dia_mm=47.14,
        bolt_hole_dia_mm=5.0,
        center_hole_dia_mm=38.1,
    ),
    "bearing_608": MountSpec(
        name="608 bearing (skate bearing) pillow mount",
        kind="bearing",
        plate_width_mm=40.0,
        bolt_count=4,
        bolt_circle_dia_mm=34.0,
        bolt_hole_dia_mm=3.0,
        center_hole_dia_mm=22.0,  # bearing OD, sits in a pocket rather than through-hole
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
    """Look up a built-in mount, or build a custom flange from explicit values."""
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
            bolt_count=bolt_count,
            bolt_circle_dia_mm=bolt_circle_dia_mm,
            bolt_hole_dia_mm=bolt_hole_dia_mm,
            center_hole_dia_mm=center_hole_dia_mm,
        )

    try:
        return MOUNTS[name]
    except KeyError as e:
        raise ValueError(
            f"Unknown mount '{name}'. Choose from {list(MOUNTS)} or use 'custom'."
        ) from e
