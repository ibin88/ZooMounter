"""Plate-thickness sizing for ZooMounter.

Two load types, because a mount plate reacts them completely differently:

- "radial" (default): an external load acting perpendicular to the plate
  (e.g. a belt/pulley/gear pulling sideways on a motor shaft) at some lever
  arm from the mount face. Modeled as a cantilever beam. Two independent
  limits are checked and the governing (larger) thickness wins: bending
  stress under yield/safety_factor, and tip deflection under lever_arm/300
  (a common stiffness rule of thumb for machine-element brackets).
- "axial": a load acting along the bolt axis, straight into or out of the
  plate (e.g. leadscrew thrust pulling back into a motor). This barely
  bends a flat plate at all -- it's governed by bearing stress at the bolt
  holes instead, which is a much less demanding failure mode for the same
  force. Modeling it as a bending load (the old, single-load-type version
  of this calc did) would badly overestimate the required thickness for
  this case.

For "radial" loads on motor mounts, the component's own weight is added to
the user-specified external load (worst case: shaft mounted horizontal, so
gravity acts as a side load too), and the default lever arm comes from the
mount's typical body length rather than an arbitrary half-plate-width guess,
when that's known (see mount_specs.MountSpec.typical_body_length_mm).

This is a hand-calc-grade engineering approximation (rectangular
cross-section, static load, no stress concentration at the holes) meant for
prototyping sanity-checks, not a certified structural analysis.

The process-based minimum wall thickness (see materials.MIN_WALL_MM) is
applied as a floor under whichever load-type-specific thickness comes out
of the above.
"""

from dataclasses import dataclass

from .materials import MIN_WALL_MM, Material
from .mount_specs import MountSpec

GRAVITY_M_S2 = 9.81
LOAD_TYPES = ("radial", "axial")


@dataclass
class ThicknessResult:
    load_type: str
    self_weight_n: float
    effective_load_n: float  # user load + self-weight (radial only)
    lever_arm_mm: float  # 0 for axial loads (not applicable)
    moment_n_mm: float  # 0 for axial loads
    allowable_stress_mpa: float
    thickness_from_stress_mm: float
    thickness_from_deflection_mm: float  # 0 for axial loads (not a governing mode)
    min_wall_mm: float
    required_thickness_mm: float


def required_thickness(
    load_n: float,
    mount: MountSpec,
    material: Material,
    safety_factor: float,
    lever_arm_mm: float | None = None,
    load_type: str = "radial",
) -> ThicknessResult:
    if load_n <= 0:
        raise ValueError("load_n must be positive")
    if safety_factor < 1:
        raise ValueError("safety_factor must be >= 1")
    if load_type not in LOAD_TYPES:
        raise ValueError(f"load_type must be one of {LOAD_TYPES}")

    allowable_stress_mpa = material.yield_mpa / safety_factor
    min_wall = MIN_WALL_MM[material.process]

    if load_type == "axial":
        # Bearing stress at the bolt holes: sigma = F / (n_bolts * hole_dia * t)
        self_weight_n = 0.0  # gravity doesn't add to an axial thrust load
        effective_load_n = load_n
        bolt_count = len(mount.hole_positions)
        t_stress = effective_load_n / (bolt_count * mount.bolt_hole_dia_mm * allowable_stress_mpa)
        t_deflection = 0.0
        arm = 0.0
        moment_n_mm = 0.0
    else:
        self_weight_n = mount.typical_mass_kg * GRAVITY_M_S2
        effective_load_n = load_n + self_weight_n

        # Default lever arm: the mount's typical body length (e.g. motor
        # length) if known, since that's roughly where an external side load
        # would apply; otherwise fall back to half the plate width.
        if lever_arm_mm is not None:
            arm = lever_arm_mm
        elif mount.typical_body_length_mm > 0:
            arm = mount.typical_body_length_mm
        else:
            arm = mount.plate_width_mm / 2

        moment_n_mm = effective_load_n * arm

        # Rectangular section, stress-limited: sigma = 6M / (w*t^2)
        t_stress = (6 * moment_n_mm / (mount.plate_width_mm * allowable_stress_mpa)) ** 0.5

        # Rectangular section, deflection-limited to arm/300:
        # delta = F*L^3 / (3*E*I), I = w*t^3/12
        e_mpa = material.youngs_modulus_gpa * 1000  # GPa -> MPa (N/mm^2)
        max_deflection_mm = arm / 300
        t_deflection_cubed = (effective_load_n * arm**3 * 12) / (3 * e_mpa * mount.plate_width_mm * max_deflection_mm)
        t_deflection = t_deflection_cubed ** (1 / 3)

    required = max(t_stress, t_deflection, min_wall)

    return ThicknessResult(
        load_type=load_type,
        self_weight_n=self_weight_n,
        effective_load_n=effective_load_n,
        lever_arm_mm=arm,
        moment_n_mm=moment_n_mm,
        allowable_stress_mpa=allowable_stress_mpa,
        thickness_from_stress_mm=t_stress,
        thickness_from_deflection_mm=t_deflection,
        min_wall_mm=min_wall,
        required_thickness_mm=required,
    )
