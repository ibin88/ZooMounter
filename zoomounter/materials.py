"""Material property table for ZooMounter.

Values are representative engineering estimates for prototyping use, not
certified material-spec data. Anyone needing production-grade numbers should
substitute their own via `--material custom`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    name: str
    process: str  # "3d_print" or "machined"
    density_kg_m3: float
    youngs_modulus_gpa: float
    yield_mpa: float


# Process-based manufacturing constraints: minimum printable/machinable wall
# thickness, in mm. 3D printing can't reliably produce very thin solid walls;
# machined/CNC parts have their own (much smaller) practical minimum.
MIN_WALL_MM = {
    "3d_print": 1.5,
    "machined": 1.0,
}

MATERIALS: dict[str, Material] = {
    "pla": Material("PLA", "3d_print", density_kg_m3=1240, youngs_modulus_gpa=3.5, yield_mpa=50),
    "petg": Material("PETG", "3d_print", density_kg_m3=1270, youngs_modulus_gpa=2.1, yield_mpa=50),
    "abs": Material("ABS", "3d_print", density_kg_m3=1040, youngs_modulus_gpa=2.3, yield_mpa=40),
    "aluminum_6061": Material(
        "Aluminum 6061-T6", "machined", density_kg_m3=2700, youngs_modulus_gpa=69, yield_mpa=240
    ),
    "mild_steel": Material(
        "Mild Steel (A36)", "machined", density_kg_m3=7850, youngs_modulus_gpa=200, yield_mpa=250
    ),
}


def get_material(
    name: str,
    density_kg_m3: float | None = None,
    youngs_modulus_gpa: float | None = None,
    yield_mpa: float | None = None,
    process: str | None = None,
) -> Material:
    """Look up a built-in material, or build a custom one from explicit values."""
    if name == "custom":
        missing = [
            n
            for n, v in [
                ("density", density_kg_m3),
                ("youngs-modulus", youngs_modulus_gpa),
                ("yield", yield_mpa),
                ("process", process),
            ]
            if v is None
        ]
        if missing:
            raise ValueError(
                f"--material custom requires all of: {', '.join(missing)}"
            )
        if process not in MIN_WALL_MM:
            raise ValueError(f"--process must be one of {list(MIN_WALL_MM)}")
        return Material("Custom", process, density_kg_m3, youngs_modulus_gpa, yield_mpa)

    try:
        return MATERIALS[name]
    except KeyError as e:
        raise ValueError(
            f"Unknown material '{name}'. Choose from {list(MATERIALS)} or use 'custom'."
        ) from e
