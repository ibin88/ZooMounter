"""Plate-thickness sizing for ZooMounter.

Hand-calc-grade approximations for prototyping sanity checks. Not FEA, not a
substitute for a real structural review on anything load-bearing. Every
assumption below is stated so you can decide whether it matches your case.

## Two load types, because a plate reacts them completely differently

**Radial** (default) -- an external load perpendicular to the bolt axis: a
belt, pulley or gear pulling sideways on a motor shaft. The plate acts as a
cantilever, so this is genuinely thickness-governed, and two limits are
checked with the larger winning:

- bending stress under yield / safety_factor
- tip deflection under lever_arm / 300, a common stiffness rule of thumb for
  machine-element brackets

The component's own weight is added to the external load (worst case: shaft
horizontal, so gravity pulls sideways too), and the default lever arm comes
from the component's typical body length rather than an arbitrary guess.

**Axial** -- thrust along the bolt axis: a leadscrew pushing back into a
motor. This is the interesting case, and an earlier version of this file got
it wrong in a way worth documenting.

That version modelled axial thrust as bearing stress crushing sideways
through the bolt holes -- `F / (n_bolts * hole_dia * t)`. That's the formula
for a clevis pin loaded transversely, and it simply isn't what happens here:
thrust along the bolt axis doesn't push sideways on the hole walls at all.
The number it produced was meaningless.

What actually happens: thrust travels through the motor's internal bearing
into its mounting flange, and from there into the fasteners. The plate's own
realistic failure mode is the screw head **pulling through** the plate --
punching shear on the cylindrical surface under the head. So:

    tau_allow = 0.577 * yield / safety_factor      (von Mises shear yield)
    shear area per bolt = pi * head_dia * t
    t = F_per_bolt / (pi * head_dia * tau_allow)

The honest headline, though, is that **for axial thrust the plate is usually
not the limiting element at all.** The fasteners, the threads they engage in
the motor's tapped holes, and the motor's own axial bearing rating all
typically govern long before plate thickness does. So the axial path also
runs a screw-tension check and reports what governs -- including naming the
things ZooMounter cannot check, so a thin result reads as "the plate isn't
your constraint here, go look at these" rather than as a false all-clear.
"""

import math
from dataclasses import dataclass, field

from .materials import MIN_WALL_MM, Material
from .mount_specs import MountSpec

GRAVITY_M_S2 = 9.81
LOAD_TYPES = ("radial", "axial")

# von Mises: shear yield is ~0.577x tensile yield.
SHEAR_YIELD_FACTOR = 0.577

# A socket-head cap screw's head is roughly 1.8x its thread diameter (M3 ->
# 5.5mm, M5 -> 8.5mm). The hole in the plate is a clearance hole, so thread
# diameter is a little under hole diameter; using hole diameter here is
# slightly conservative on head size, which is the safe direction.
HEAD_DIA_PER_HOLE_DIA = 1.8

# Property-class 8.8 steel fastener, the common default for machine screws.
# Proof stress ~580 MPa; used with the screw's tensile stress area.
FASTENER_PROOF_STRESS_MPA = 580.0

# Nominal screw sizes and their ISO coarse-pitch tensile stress areas (mm^2),
# used to estimate fastener capacity from the plate's clearance hole size.
_TENSILE_STRESS_AREA_MM2 = {
    2.0: 2.07,
    2.5: 3.39,
    3.0: 5.03,
    4.0: 8.78,
    5.0: 14.2,
    6.0: 20.1,
    8.0: 36.6,
    10.0: 58.0,
}


@dataclass
class Check:
    level: str  # "PASS", "WARN", "LOUD WARN"
    message: str
    source: str = ""
    remedy: str = ""

    def __str__(self):
        s = f"[{self.level}] {self.message}"
        if self.source:
            s += f" (Source: {self.source})"
        if self.remedy:
            s += f" -> Remedy: {self.remedy}"
        return s


@dataclass
class ThicknessResult:
    load_type: str
    self_weight_n: float
    effective_load_n: float
    lever_arm_mm: float  # 0 for axial (not applicable)
    moment_n_mm: float  # 0 for axial
    allowable_stress_mpa: float
    thickness_from_stress_mm: float
    thickness_from_deflection_mm: float  # 0 for axial (not a governing mode)
    min_wall_mm: float
    required_thickness_mm: float
    governing_limit: str  # which of the above actually decided the answer
    notes: list[Check] = field(default_factory=list)  # caveats worth surfacing to the user


def _nominal_screw_dia(hole_dia_mm: float) -> float:
    """Closest standard screw size at or below the clearance hole."""
    candidates = [d for d in _TENSILE_STRESS_AREA_MM2 if d <= hole_dia_mm + 0.01]
    return max(candidates) if candidates else min(_TENSILE_STRESS_AREA_MM2)


def _fastener_capacity_n(hole_dia_mm: float, bolt_count: int, safety_factor: float) -> tuple[float, float]:
    """Allowable total axial load carried by the fastener group, and the
    nominal screw size that assumes. Returns (capacity_N, screw_dia_mm)."""
    screw_dia = _nominal_screw_dia(hole_dia_mm)
    area = _TENSILE_STRESS_AREA_MM2[screw_dia]
    per_screw = area * FASTENER_PROOF_STRESS_MPA / safety_factor
    return per_screw * bolt_count, screw_dia


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
    notes: list[Check] = []

    if load_type == "axial":
        self_weight_n = 0.0  # gravity acts perpendicular to an axial thrust, not with it
        effective_load_n = load_n
        bolt_count = len(mount.hole_positions)

        # Plate's own failure mode: screw head punching through.
        head_dia = mount.bolt_hole_dia_mm * HEAD_DIA_PER_HOLE_DIA
        tau_allow = SHEAR_YIELD_FACTOR * allowable_stress_mpa
        load_per_bolt = effective_load_n / bolt_count
        t_stress = load_per_bolt / (math.pi * head_dia * tau_allow)
        t_deflection = 0.0
        arm = 0.0
        moment_n_mm = 0.0

        # What actually governs is usually elsewhere -- say so explicitly.
        fastener_capacity, screw_dia = _fastener_capacity_n(
            mount.bolt_hole_dia_mm, bolt_count, safety_factor
        )
        notes.append(
            Check(
                level="INFO",
                message=f"Plate thickness is sized against screw-head pull-through (punching shear on a ~{head_dia:.1f}mm head), which calls for {t_stress:.2f}mm here."
            )
        )
        if effective_load_n > fastener_capacity:
            notes.append(
                Check(
                    level="LOUD WARN",
                    message=f"{effective_load_n:.0f}N exceeds the estimated capacity of {bolt_count}x M{screw_dia:g} class-8.8 screws in tension ({fastener_capacity:.0f}N at SF {safety_factor}).",
                    remedy="The fasteners, not the plate, are your limiting element -- size them up or add bolts."
                )
            )
        else:
            notes.append(
                Check(
                    level="PASS",
                    message=f"{bolt_count}x M{screw_dia:g} class-8.8 screws carry an estimated {fastener_capacity:.0f}N in tension at SF {safety_factor}, well above this load."
                )
            )
        notes.append(
            Check(
                level="WARN",
                message="NOT CHECKED for axial loads: thread engagement depth in the motor's tapped holes, and the motor's own axial bearing rating.",
                remedy="For thrust applications one of those is usually the real limit -- a thin result here means the plate is not your constraint, not that the assembly is safe."
            )
        )
        
        if mount.kind == "motor" and load_n > 67:
            notes.append(
                Check(
                    level="LOUD WARN",
                    message=f"Axial load ({load_n:.0f}N) exceeds the published limit for typical NEMA motors (NEMA 23 limit is ~67 N / 15 lb). Steppers have no thrust bearings.",
                    source="docs/mechanics.html",
                    remedy="Add a thrust bearing so the load bypasses the motor (screw -> bearing -> housing -> frame), and use a flexible coupling that tolerates float."
                )
            )
    else:
        self_weight_n = mount.typical_mass_kg * GRAVITY_M_S2
        effective_load_n = load_n + self_weight_n

        if lever_arm_mm is not None:
            arm = lever_arm_mm
        elif getattr(mount, 'shaft_load_offset_mm', 0) > 0:
            arm = mount.shaft_load_offset_mm
        else:
            arm = mount.plate_width_mm / 2

        cg_arm = getattr(mount, 'body_cg_offset_mm', 0)
        
        # Worst-case moment magnitude: external load and component weight moment add together
        moment_n_mm = (load_n * arm) + (self_weight_n * cg_arm)
        
        # For deflection calc, keep the simple cubic formula by finding an effective load at the shaft end
        effective_load_n = moment_n_mm / arm if arm > 0 else load_n + self_weight_n

        # Rectangular section, stress-limited: sigma = 6M / (w*t^2)
        t_stress = (6 * moment_n_mm / (mount.plate_width_mm * allowable_stress_mpa)) ** 0.5

        # Rectangular section, deflection-limited to arm/300:
        # delta = F*L^3 / (3*E*I), I = w*t^3/12
        e_mpa = material.youngs_modulus_gpa * 1000  # GPa -> MPa (N/mm^2)
        max_deflection_mm = arm / 300
        t_deflection_cubed = (effective_load_n * arm**3 * 12) / (
            3 * e_mpa * mount.plate_width_mm * max_deflection_mm
        )
        t_deflection = t_deflection_cubed ** (1 / 3)

        if self_weight_n > 0:
            notes.append(
                Check(
                    level="INFO",
                    message=f"Includes {self_weight_n:.2f}N of component self-weight (worst case: shaft mounted horizontal, so gravity acts as a side load too)."
                )
            )

    candidates = {
        "bending stress" if load_type == "radial" else "screw-head pull-through": t_stress,
        "deflection limit (arm/300)": t_deflection,
        f"process minimum wall ({material.process})": min_wall,
    }
    governing_limit = max(candidates, key=lambda k: candidates[k])
    required = candidates[governing_limit]

    if governing_limit.startswith("process minimum"):
        notes.append(
            Check(
                level="INFO",
                message="The engineering calcs came out below the minimum manufacturable wall thickness, so the process floor sets the answer -- this part is not structurally limited at this load."
            )
        )

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
        governing_limit=governing_limit,
        notes=notes,
    )
