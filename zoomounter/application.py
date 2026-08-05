"""What the application decides, so the user does not have to.

ZooMounter had a flag for the bearing topology and, briefly, a question about
whether to generate one plate or two. Both were wrong in the same way: they
asked the user to pick a *geometry* when the geometry is a consequence of the
*application*. A stepper turning an idler on a fixed frame and the same stepper
riding a moving axis in a shared workspace do not want the same part, and no
amount of preference makes them interchangeable.

So the inputs here describe the situation, and the outputs are decisions with
the reasoning attached:

    ApplicationContext(service=..., workspace=...)
        -> recommend_mount_class()   open plate / housing
        -> recommend_topology()      stub-shaft / direct / none

Every recommendation returns the `Check` chain that produced it, so the answer
arrives with its argument rather than as an assertion. That is the same
discipline the rule registry applies to numbers, extended to decisions: a
conclusion whose derivation is invisible is indistinguishable from a guess.

## Where the reasoning stops, and why that is stated rather than hidden

ZooMounter generates **open plates**. It does not generate housings, guards or
enclosures, and it will say so rather than handing back a plate for a job that
needs one. A motor on a moving axis in a shared workspace needs containment --
for the cable loom, for finger clearance, for what happens when something
collides with it -- and none of that is a plate with holes in it. Declining is
the honest output; producing an open bracket and staying quiet is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mechanics import Check
from .mount_specs import MountSpec

# Does the mount itself travel?
SERVICE_FIXED = "fixed"  # bolted to a frame that does not move
SERVICE_MOVING = "moving"  # rides a moving axis: gantry, carriage, arm
SERVICES = (SERVICE_FIXED, SERVICE_MOVING)

# Can anything reach the part in service?
WORKSPACE_CLEAR = "clear"  # nothing else occupies the volume it sweeps
WORKSPACE_SHARED = "shared"  # people, workpieces or other axes can reach it
WORKSPACES = (WORKSPACE_CLEAR, WORKSPACE_SHARED)

# What ZooMounter can and cannot produce.
CLASS_OPEN_PLATE = "open-plate"
CLASS_HOUSING = "housing"

# Stable rule codes.
MOUNT_CLASS = "mount_class_from_application"
TOPOLOGY_DERIVED = "topology_is_derived_not_chosen"
DYNAMIC_LOADS = "dynamic_loads_unmodelled"


@dataclass
class ApplicationContext:
    """How the part is used. Not how it is shaped -- that is the output."""

    service: str = SERVICE_FIXED
    workspace: str = WORKSPACE_CLEAR

    def __post_init__(self):
        if self.service not in SERVICES:
            raise ValueError(f"service must be one of {SERVICES}, got {self.service!r}")
        if self.workspace not in WORKSPACES:
            raise ValueError(
                f"workspace must be one of {WORKSPACES}, got {self.workspace!r}"
            )


@dataclass
class Recommendation:
    """A decision plus the argument for it."""

    value: str
    in_scope: bool
    checks: list[Check] = field(default_factory=list)


def recommend_mount_class(context: ApplicationContext) -> Recommendation:
    """Open plate, or something ZooMounter does not make?

    The determining question is whether anything can reach the part while it is
    working. A bracket on a fixed frame in a clear volume is a plate. The same
    bracket riding a gantry through a space people or workpieces share is a
    containment problem wearing a bracket's clothes, and the features that
    matter there -- guarding, a cable path, swept-volume clearance -- are not
    dimensions this tool computes.
    """
    checks: list[Check] = []

    if context.service == SERVICE_MOVING:
        checks.append(
            Check(
                level="WARN",
                message=(
                    "This mount travels. Acceleration loads, cable-loom drag and "
                    "the swept volume are not modelled -- ZooMounter sizes for a "
                    "static installation, and a moving axis adds inertial load on "
                    "every direction change that a static check calls safe."
                ),
                remedy=(
                    "Add your peak acceleration load to the shaft load, plan the "
                    "cable path separately, and check the swept volume yourself."
                ),
                code=DYNAMIC_LOADS,
            )
        )

    if context.workspace == WORKSPACE_SHARED and context.service == SERVICE_MOVING:
        checks.append(
            Check(
                level="LOUD WARN",
                message=(
                    "A moving mount in a shared workspace needs containment, not a "
                    "bracket. What governs it is guarding, finger clearance, the "
                    "cable path and what happens on collision -- none of which is "
                    "a flat plate with holes in it. ZooMounter generates open "
                    "plates and cannot design this part."
                ),
                remedy=(
                    "Design a housing around the motor: enclose the rotating "
                    "elements, route the loom through a drag chain, and give the "
                    "swept volume physical limits. Use ZooMounter's plate only as "
                    "the internal mounting face inside that housing."
                ),
                code=MOUNT_CLASS,
            )
        )
        return Recommendation(CLASS_HOUSING, in_scope=False, checks=checks)

    if context.workspace == WORKSPACE_SHARED:
        checks.append(
            Check(
                level="WARN",
                message=(
                    "The workspace is shared but the mount is fixed, so an open "
                    "plate is buildable -- the rotating parts it carries are still "
                    "exposed. ZooMounter does not model guarding."
                ),
                remedy=(
                    "Guard the shaft, coupling and any pulley separately. The "
                    "plate is not the safety feature."
                ),
                code=MOUNT_CLASS,
            )
        )

    return Recommendation(CLASS_OPEN_PLATE, in_scope=True, checks=checks)


def recommend_topology(mount: MountSpec, bearing, load_type: str) -> Recommendation:
    """Which bearing topology this load case actually calls for.

    This used to be a bare user choice, which was the same error as asking
    which mount class to build: the answer follows from the load and the
    hardware, and a preference cannot override physics.

    `direct` is acceptable only when all three hold:

      - the load is radial, because a plain stepper shaft has no shoulder for a
        thrust bearing to push against;
      - the bearing is larger than the pilot boss it sits concentric with, or
        there is no material gripping its outer ring;
      - the bearing's bore matches the motor's shaft, because that shaft is
        what runs in it.

    Otherwise `stub-shaft` is the answer, and it is also the default when
    `direct` merely happens to be permissible -- it is strictly better, since
    it decouples the two bearings rather than fighting them.
    """
    from .bearings import TOPOLOGY_DIRECT, TOPOLOGY_STUB_SHAFT

    reasons = []
    if load_type == "axial":
        reasons.append(
            "the load is axial and a plain stepper shaft has no shoulder to "
            "transmit thrust into a bearing"
        )
    if bearing.od_mm <= mount.center_hole_dia_mm:
        reasons.append(
            f"the {bearing.designation} is {bearing.od_mm:g}mm across against a "
            f"{mount.center_hole_dia_mm:g}mm pilot boss, so a seat concentric "
            f"with the boss recess would have no material gripping it"
        )
    if bearing.bore_mm != mount.shaft_dia_mm:
        reasons.append(
            f"its {bearing.bore_mm:g}mm bore does not match the "
            f"{mount.shaft_dia_mm:g}mm motor shaft"
        )

    if reasons:
        message = (
            "Stub-shaft topology chosen because " + "; and ".join(reasons) + "."
        )
    else:
        message = (
            "Stub-shaft topology chosen. Running directly on the motor shaft "
            "would be permissible here -- radial load, bore matches, bearing "
            "clears the boss -- but it stays overconstrained against the "
            "motor's own front bearing, and decoupling through a coupling is "
            "strictly better for no extra parts you do not already need."
        )

    return Recommendation(
        TOPOLOGY_STUB_SHAFT,
        in_scope=True,
        checks=[
            Check(
                level="INFO",
                message=message,
                remedy=(
                    f"Override with the direct topology if you have a reason to "
                    f"accept it; it is built on request and reports its costs."
                    if not reasons
                    else f"Do not override this with the direct topology -- "
                    f"{reasons[0]}."
                ),
                code=TOPOLOGY_DERIVED,
            )
        ],
    )


def direct_override_checks(mount: MountSpec, bearing, load_type: str) -> list[Check]:
    """What to say when the user overrides the derivation and picks `direct`.

    The override is honoured -- this project warns rather than blocks -- but it
    is reported as an override rather than as a design decision, because the
    derivation above did not choose it.
    """
    recommended = recommend_topology(mount, bearing, load_type)
    from .bearings import TOPOLOGY_DIRECT

    return [
        Check(
            level="WARN",
            message=(
                "Direct topology was selected manually. It is not what this load "
                "case calls for: " + recommended.checks[0].message
            ),
            remedy=(
                "Drop the override to get the derived answer, or read the "
                "warnings below and accept them deliberately."
            ),
            code=TOPOLOGY_DERIVED,
        )
    ]
