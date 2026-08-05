"""Bearing topology: the two ways a bearing can take load off a motor.

Offline. The point of these tests is that the two topologies produce genuinely
different parts and genuinely different advice -- if they converged, offering
the choice would be theatre.
"""

import pytest

from zoomounter import assembly, mechanics
from zoomounter.assembly import FACE_FRONT
from zoomounter.bearings import (
    BEARING_TOPOLOGIES,
    BY_DESIGNATION,
    COUPLING_LENGTH_MM,
    TOPOLOGY_DIRECT,
    TOPOLOGY_DIRECT_RULE,
    TOPOLOGY_STUB_SHAFT,
    apply_bearing_topology,
    motor_standoff_for_coupling,
)
from zoomounter.materials import get_material
from zoomounter.mount_specs import get_mount

ALUMINIUM = get_material("aluminum_6061")
N17 = get_mount("nema17")
B625 = BY_DESIGNATION["625"]  # 5mm bore -- matches the NEMA 17 shaft
THRUST = BY_DESIGNATION["F5-12M"]


def _spec(topology, bearing=B625, load_type="radial", mount=N17):
    return apply_bearing_topology(mount, bearing, topology, load_type)


# ---------------------------------------------------------------------------
# The two topologies are different parts, not a display option.
# ---------------------------------------------------------------------------


def test_the_topologies_produce_different_parts():
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    direct, _ = _spec(TOPOLOGY_DIRECT)
    assert stub.motor_standoff_mm > 0 and direct.motor_standoff_mm == 0
    assert direct.boss_recess_depth_mm > 0 and stub.boss_recess_depth_mm == 0
    assert stub.bearing_topology != direct.bearing_topology


def test_the_centre_bore_follows_what_actually_turns_in_it():
    """Direct bores for the MOTOR's shaft; stub-shaft bores for the BEARING's.

    Compared on an AXIAL load, because that is the case where both topologies
    have a separate shaft hole to compare. On a radial load the stub-shaft
    seat is a through-bore and there is no second feature at all -- see
    test_a_radial_seat_is_the_hole_and_does_not_also_request_a_shaft_bore,
    which is the bug a live run found.

    With a 625 on a NEMA 17 the two would coincide at 5mm, which is the matched
    case and not a defect; a bearing whose bore differs from the shaft separates
    them, and is also why only the direct topology warns about a mismatch."""
    mismatched = BY_DESIGNATION["F8-16M"]  # 8mm bore against a 5mm NEMA 17 shaft
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT, bearing=mismatched, load_type="axial")
    direct, _ = _spec(TOPOLOGY_DIRECT, bearing=mismatched, load_type="axial")
    assert stub.center_hole_dia_mm > direct.center_hole_dia_mm
    assert stub.center_hole_dia_mm == pytest.approx(mismatched.bore_mm + 1)
    assert direct.center_hole_dia_mm == pytest.approx(N17.shaft_dia_mm + 1)


def test_stub_shaft_standoff_fits_a_real_coupling():
    """The motor has to stand off far enough for the coupling to physically go
    between its face and the plate, or the topology does not exist."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    assert stub.motor_standoff_mm >= COUPLING_LENGTH_MM
    assert stub.motor_standoff_mm == motor_standoff_for_coupling()


def test_stub_shaft_has_no_pilot_boss_to_clear():
    """The motor is not touching this plate, so there is no register boss --
    the centre feature is only what the stub shaft turns in."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    assert stub.center_hole_dia_mm < N17.center_hole_dia_mm
    assert stub.bearing_bore_mm == B625.bore_mm


def test_direct_bores_for_the_motors_own_shaft():
    direct, _ = _spec(TOPOLOGY_DIRECT)
    assert direct.center_hole_dia_mm > N17.shaft_dia_mm
    assert direct.center_hole_dia_mm < N17.shaft_dia_mm + 2


def test_an_unknown_topology_is_rejected():
    with pytest.raises(ValueError, match="topology"):
        _spec("bolt-it-on-and-hope")


# ---------------------------------------------------------------------------
# Direct is offered, and it is told the truth about.
# ---------------------------------------------------------------------------


def test_direct_always_warns_about_overconstraint():
    """Two bearings on one shaft with no compliance between them loads the
    bearing you were trying to protect. Offering the topology without saying
    so would ship the thing this project exists to catch."""
    _, checks = _spec(TOPOLOGY_DIRECT)
    loud = [c for c in checks if c.level == "LOUD WARN"]
    assert loud
    assert any("overconstrain" in c.message.lower() or "two bearings" in c.message.lower()
               for c in loud)
    assert all(c.remedy for c in loud)


def test_direct_on_a_thrust_load_says_it_does_almost_nothing():
    """A plain stepper shaft has no shoulder, so thrust never enters the
    bearing. This is the case where 'direct' looks like a bearing mount
    without being one."""
    _, checks = _spec(TOPOLOGY_DIRECT, bearing=THRUST, load_type="axial")
    assert any("shoulder" in c.message for c in checks)
    assert any(c.level == "LOUD WARN" for c in checks)


def test_direct_flags_that_the_seat_falls_inside_the_boss_recess():
    """A NEMA 17's pilot boss is 22mm; a bearing for its 5mm shaft is 16mm
    across. Concentric, there is no material gripping the outer ring."""
    _, checks = _spec(TOPOLOGY_DIRECT)
    assert any("no material left gripping" in c.message for c in checks)


def test_direct_flags_a_bore_that_does_not_match_the_shaft():
    """Running on the motor's own shaft requires the bore to match it. The
    stub-shaft topology does not, because the stub can be turned to size."""
    mismatched = BY_DESIGNATION["608"]  # 8mm bore on a 5mm NEMA 17 shaft
    _, direct_checks = _spec(TOPOLOGY_DIRECT, bearing=mismatched)
    _, stub_checks = _spec(TOPOLOGY_STUB_SHAFT, bearing=mismatched)
    assert any("bore" in c.message and "motor shaft is" in c.message
               for c in direct_checks)
    assert not any("bore" in c.message and "motor shaft is" in c.message
                   for c in stub_checks)


def test_stub_shaft_is_not_warned_about_the_way_direct_is():
    _, checks = _spec(TOPOLOGY_STUB_SHAFT)
    assert not any(c.level == "LOUD WARN" for c in checks)
    assert any(c.code != TOPOLOGY_DIRECT_RULE for c in checks)


def test_every_topology_check_names_a_declared_rule():
    from zoomounter import rules

    for topology in BEARING_TOPOLOGIES:
        for load_type in ("radial", "axial"):
            _, checks = _spec(topology, load_type=load_type)
            for c in checks:
                assert rules.get(c.code).statement


# ---------------------------------------------------------------------------
# Thickness consequences.
# ---------------------------------------------------------------------------


def test_direct_needs_a_thicker_plate_than_stub_shaft():
    """Direct stacks the boss recess and the bearing seat on opposite faces of
    the same plate. Stub-shaft has no boss to clear, so it only needs the seat.
    Getting this wrong produces a part whose two features overlap."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    direct, _ = _spec(TOPOLOGY_DIRECT)
    t_stub = mechanics.required_thickness(stub, ALUMINIUM).required_thickness_mm
    t_direct = mechanics.required_thickness(direct, ALUMINIUM).required_thickness_mm
    assert t_direct > t_stub


def test_direct_thickness_covers_both_features_plus_a_floor():
    direct, _ = _spec(TOPOLOGY_DIRECT)
    result = mechanics.required_thickness(direct, ALUMINIUM)
    assert result.required_thickness_mm >= (
        direct.boss_recess_depth_mm + direct.bearing_width_mm
    )
    assert "boss recess" in result.governing_limit


# ---------------------------------------------------------------------------
# The assembly has to show the difference, or the choice is invisible.
# ---------------------------------------------------------------------------


def _plane_offsets(kcl, var):
    for line in kcl.splitlines():
        if line.startswith(f"{var}Plane"):
            return float(line.split("= ")[-1].rstrip(")"))
    return None


def _extrude_length(kcl, var):
    for line in kcl.splitlines():
        if line.startswith(f"{var}Body") or line.startswith(f"{var}Solid"):
            return float(line.split("length = ")[-1].rstrip(")"))
    return None


def test_stub_shaft_assembly_draws_a_coupling_and_a_stub():
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    kcl = assembly.component_kcl(stub, 5.0, FACE_FRONT)
    assert "coupling" in kcl and "stubShaft" in kcl


def test_the_motors_own_shaft_never_reaches_the_plate_in_stub_shaft():
    """The whole claim of this topology, made visible. If the motor shaft
    entered the plate the picture would be showing the direct topology while
    the report described the stub-shaft one."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    thickness = 5.0
    kcl = assembly.component_kcl(stub, thickness, FACE_FRONT)
    start = _plane_offsets(kcl, "shaft")
    end = start + _extrude_length(kcl, "shaft")
    assert end > thickness / 2, (
        f"motor shaft reaches z={end}, but the plate's top face is at "
        f"{thickness / 2} -- it must stop in the coupling"
    )


def test_the_stub_shaft_does_pass_through_the_plate():
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    thickness = 5.0
    kcl = assembly.component_kcl(stub, thickness, FACE_FRONT)
    start = _plane_offsets(kcl, "stubShaft")
    end = start + _extrude_length(kcl, "stubShaft")
    assert end < -thickness / 2


def test_direct_assembly_has_no_coupling():
    direct, _ = _spec(TOPOLOGY_DIRECT)
    kcl = assembly.component_kcl(direct, 9.0, FACE_FRONT)
    assert "coupling" not in kcl
    # The motor's own shaft goes through the plate, which is the topology.
    start = _plane_offsets(kcl, "shaft")
    assert start + _extrude_length(kcl, "shaft") < -9.0 / 2


# ---------------------------------------------------------------------------
# Regressions in the reference geometry, found by looking at a render.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("explode", [0.0, assembly.DEFAULT_EXPLODE_MM])
def test_the_shaft_spans_the_explode_gap(explode):
    """The exploded preview drew the shaft from the displaced motor but sized
    it from the un-displaced thickness, so it stopped in mid-air short of the
    hole it passes through."""
    thickness = 1.0
    kcl = assembly.component_kcl(N17, thickness, FACE_FRONT, explode_mm=explode)
    start = _plane_offsets(kcl, "shaft")
    end = start + _extrude_length(kcl, "shaft")
    assert end < -thickness / 2, (
        f"explode={explode}: shaft ends at z={end}, above the plate's bottom "
        f"face at {-thickness / 2} -- it does not reach through"
    )


def test_the_motor_body_carries_its_own_bolt_holes():
    """Without them the assembly cannot show the one thing it is uniquely able
    to show: whether the plate's bolt pattern matches the motor's. That
    mismatch is the bug this project shipped and did not catch."""
    kcl = assembly.component_kcl(N17, 1.0, FACE_FRONT)
    assert "motorBodyHole0" in kcl
    assert kcl.count("Solid = extrude(motorBodyHole") == len(N17.hole_positions)
    assert "subtract(motorBodySolid" in kcl


def test_motor_bolt_holes_sit_on_the_catalogue_positions():
    """Drawn from hole_positions, not invented. If these were approximated the
    assembly would hide exactly the error it exists to reveal."""
    kcl = assembly.component_kcl(N17, 1.0, FACE_FRONT)
    for x, y in N17.hole_positions:
        assert f"center = [{x:g}mm, {y:g}mm]" in kcl


# ---------------------------------------------------------------------------
# The standoffs. Without them the stub-shaft assembly shows a motor hovering
# in space with nothing explaining why it stays there.
# ---------------------------------------------------------------------------


def test_stub_shaft_assembly_draws_standoffs():
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    kcl = assembly.component_kcl(stub, 7.0, FACE_FRONT)
    assert kcl.count("Body = extrude(standoff") == len(stub.hole_positions)


def test_standoffs_sit_on_the_motors_bolt_pattern():
    """They are what carries the motor's weight and reaction torque into the
    plate, so they go where the bolts go -- not at invented positions."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    kcl = assembly.component_kcl(stub, 7.0, FACE_FRONT)
    for x, y in stub.hole_positions:
        assert f"center = [{x:g}mm, {y:g}mm]" in kcl


def test_standoffs_close_the_gap_between_motor_and_plate():
    """A standoff that does not reach the plate is drawing the same floating
    motor it was added to explain."""
    stub, _ = _spec(TOPOLOGY_STUB_SHAFT)
    thickness = 7.0
    kcl = assembly.component_kcl(stub, thickness, FACE_FRONT)
    start = _plane_offsets(kcl, "standoff0")
    end = start + _extrude_length(kcl, "standoff0")
    assert start == pytest.approx(thickness / 2 + stub.motor_standoff_mm)
    assert end == pytest.approx(thickness / 2), "standoff must land on the plate face"


def test_the_direct_topology_has_no_standoffs():
    """The motor bolts flat to the plate there, so spacers would be fiction."""
    direct, _ = _spec(TOPOLOGY_DIRECT)
    assert "standoff" not in assembly.component_kcl(direct, 9.0, FACE_FRONT)


# ---------------------------------------------------------------------------
# The mounting face is a physical choice, not a viewpoint.
# ---------------------------------------------------------------------------


def test_rear_mounting_warns_that_the_holes_may_not_exist():
    """The NEMA standard puts the bolt pattern, boss and shaft all on the
    FRONT face. Rear tapped holes are a per-model extra."""
    checks = mechanics.face_checks(N17, "back")
    assert any(c.level == "WARN" and "rear tapped holes" in c.message for c in checks)
    assert all(c.code == mechanics.REAR_FACE_MOUNTING for c in checks)


def test_rear_mounting_notes_the_centre_bore_registers_nothing():
    checks = mechanics.face_checks(N17, "back")
    assert any("Nothing registers in it" in c.message for c in checks)


def test_front_mounting_needs_no_such_warning():
    assert mechanics.face_checks(N17, "front") == []


def test_face_checks_name_a_declared_rule():
    from zoomounter import rules

    for c in mechanics.face_checks(N17, "back"):
        assert rules.get(c.code).statement


# ---------------------------------------------------------------------------
# Every surface must be able to make these choices AND receive the warnings.
# The MCP server could do neither: it had no mounting_face and no
# bearing_topology, so an assistant driving ZooMounter was structurally unable
# to be told any of this.
# ---------------------------------------------------------------------------


def _mcp():
    """The MCP server, or skip.

    `mcp` is an optional dependency. A red suite caused by a missing optional
    package looks identical to a broken project, and this repo cares about that
    distinction more than most -- so it skips and says why."""
    pytest.importorskip("mcp.server.fastmcp", reason="MCP extra not installed")
    from zoomounter import mcp_server

    return mcp_server


def test_mcp_exposes_the_face_and_the_topology():
    import inspect

    for tool in ("size_mount", "generate_mount"):
        params = inspect.signature(getattr(_mcp(), tool)).parameters
        assert "mounting_face" in params, f"{tool} cannot choose a mounting face"
        assert "bearing_topology" in params, f"{tool} cannot choose a topology"


def test_mcp_returns_the_rear_face_warning():
    result = _mcp().size_mount(
        mount="nema23", material="aluminum_6061", shaft_load_n=20,
        load_type="radial", mounting_face="back",
    )
    codes = [c.code for c in result["shaft"]["checks"]]
    assert mechanics.REAR_FACE_MOUNTING in codes


def test_mcp_returns_the_topology_warnings():
    result = _mcp().size_mount(
        mount="nema17", material="aluminum_6061", shaft_load_n=40,
        load_type="axial", bearing_topology="direct",
    )
    loud = [c for c in result["shaft"]["checks"] if c.level == "LOUD WARN"]
    assert any(c.code == TOPOLOGY_DIRECT_RULE for c in loud)


def test_mcp_flags_the_unbuildable_combination():
    """Rear-face mounting plus a stub shaft cannot be built -- the coupling
    needs the motor's shaft pointing at the plate."""
    result = _mcp().size_mount(
        mount="nema23", material="aluminum_6061", shaft_load_n=200,
        load_type="radial", mounting_face="back", bearing_topology="stub-shaft",
    )
    loud = [c for c in result["shaft"]["checks"] if c.level == "LOUD WARN"]
    assert any("cannot be built" in c.message for c in loud)


def test_mcp_rejects_a_nonsense_face():
    with pytest.raises(ValueError, match="mounting_face"):
        _mcp().size_mount(
            mount="nema17", material="aluminum_6061", shaft_load_n=5,
            mounting_face="sideways",
        )


# ---------------------------------------------------------------------------
# A spec must not ask for two features that cannot coexist.
# ---------------------------------------------------------------------------


def test_a_radial_seat_is_the_hole_and_does_not_also_request_a_shaft_bore():
    """Found by running the pipeline live, not by any offline test.

    A radial bearing seats in a THROUGH-bore at its outside diameter. Asking
    for a 9mm shaft hole as well as a 22mm through-bore puts a feature in the
    spec that the larger bore swallows -- so the Agent API cannot build it, the
    verifier reports a missing hole, and the run FAILs on geometry that was
    never possible.

    Every other hole in that run came back exact to 0.000mm. The tool was
    wrong, not the API. `bearing_block` had this right ("the seat *is* the
    hole"); apply_bearing_topology was written later and did not copy it."""
    for topology in BEARING_TOPOLOGIES:
        spec, _ = _spec(topology, bearing=BY_DESIGNATION["608"], load_type="radial",
                        mount=get_mount("nema23"))
        if spec.bearing_seat_depth_mm == 0 and spec.bearing_seat_dia_mm > 0:
            assert spec.center_hole_dia_mm == 0, (
                f"{topology}: a through-bore seat at "
                f"{spec.bearing_seat_dia_mm:g}mm cannot coexist with a "
                f"{spec.center_hole_dia_mm:g}mm centre hole"
            )


def test_an_axial_seat_does_keep_its_shaft_through_hole():
    """The counterpart. A blind counterbore leaves material below it, so the
    shaft hole through that floor is a real second feature."""
    spec, _ = _spec(TOPOLOGY_STUB_SHAFT, bearing=BY_DESIGNATION["F8-16M"],
                    load_type="axial", mount=get_mount("nema23"))
    assert spec.bearing_seat_depth_mm > 0
    assert spec.center_hole_dia_mm > 0


def test_no_spec_requests_a_hole_a_larger_concentric_bore_would_swallow():
    """The general form, over every topology and load type in the catalogue."""
    for key in ("nema17", "nema23"):
        mount = get_mount(key)
        for topology in BEARING_TOPOLOGIES:
            for load_type in ("radial", "axial"):
                for designation in ("608", "625", "F8-16M", "F5-12M"):
                    bearing = BY_DESIGNATION[designation]
                    spec, _ = _spec(topology, bearing=bearing,
                                    load_type=load_type, mount=mount)
                    through_bore = (
                        spec.bearing_seat_dia_mm > 0
                        and spec.bearing_seat_depth_mm == 0
                    )
                    if through_bore and spec.center_hole_dia_mm > 0:
                        assert spec.center_hole_dia_mm > spec.bearing_seat_dia_mm, (
                            f"{key}/{topology}/{load_type}/{designation}: "
                            f"{spec.center_hole_dia_mm:g}mm hole sits inside a "
                            f"{spec.bearing_seat_dia_mm:g}mm through-bore"
                        )
