"""Plate-thickness sizing for ZooMounter.

Models the mount plate as a cantilever beam reacting to an overhung load at
the given lever arm. This is a hand-calc-grade engineering approximation
(rectangular cross-section, static load, no stress concentration at the
holes) meant for prototyping sanity-checks, not a certified structural
analysis. Two independent limits are checked and the governing (larger)
thickness wins:

- Bending stress must stay under yield / safety_factor.
- Tip deflection must stay under lever_arm / 300 (a common stiffness
  rule of thumb for machine-element brackets).

The process-based minimum wall thickness (see materials.MIN_WALL_MM) is
then applied as a floor on top of whichever of those is larger.
"""

from dataclasses import dataclass

from .materials import MIN_WALL_MM, Material


@dataclass
class ThicknessResult:
    lever_arm_mm: float
    moment_n_mm: float
    allowable_stress_mpa: float
    thickness_from_stress_mm: float
    thickness_from_deflection_mm: float
    min_wall_mm: float
    required_thickness_mm: float


def required_thickness(
    load_n: float,
    plate_width_mm: float,
    material: Material,
    safety_factor: float,
    lever_arm_mm: float | None = None,
) -> ThicknessResult:
    if load_n <= 0:
        raise ValueError("load_n must be positive")
    if safety_factor < 1:
        raise ValueError("safety_factor must be >= 1")

    # Default lever arm: half the plate width, i.e. assume the load acts at
    # roughly the plate's outer edge. Override with an explicit value when
    # the real overhang (e.g. motor shaft length) is known.
    arm = lever_arm_mm if lever_arm_mm is not None else plate_width_mm / 2

    moment_n_mm = load_n * arm
    allowable_stress_mpa = material.yield_mpa / safety_factor

    # Rectangular section, stress-limited: sigma = 6M / (w*t^2)
    t_stress = (6 * moment_n_mm / (plate_width_mm * allowable_stress_mpa)) ** 0.5

    # Rectangular section, deflection-limited to arm/300:
    # delta = F*L^3 / (3*E*I), I = w*t^3/12
    e_mpa = material.youngs_modulus_gpa * 1000  # GPa -> MPa (N/mm^2)
    max_deflection_mm = arm / 300
    t_deflection_cubed = (load_n * arm**3 * 12) / (3 * e_mpa * plate_width_mm * max_deflection_mm)
    t_deflection = t_deflection_cubed ** (1 / 3)

    min_wall = MIN_WALL_MM[material.process]
    required = max(t_stress, t_deflection, min_wall)

    return ThicknessResult(
        lever_arm_mm=arm,
        moment_n_mm=moment_n_mm,
        allowable_stress_mpa=allowable_stress_mpa,
        thickness_from_stress_mm=t_stress,
        thickness_from_deflection_mm=t_deflection,
        min_wall_mm=min_wall,
        required_thickness_mm=required,
    )
