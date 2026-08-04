"""Bearing selection, block geometry, and the seat checks.

Offline. No API calls, no credits.

The load-bearing test in here is `test_block_reproduces_the_handwritten_608`:
the block proportions were reverse-engineered from the bearing_608 mount a
person drew by hand, so if the rule ever stops reproducing that mount exactly,
the rule has drifted away from the thing that validated it.
"""

import math

import pytest

from zoomounter import bearings
from zoomounter.bearings import (
    BEARING_FIT,
    BEARING_SEAT,
    BEARING_SELECTION,
    BEARINGS,
    BY_DESIGNATION,
    RADIAL,
    THRUST,
    auto_bearing_mount,
    bearing_block,
    check_seat_depth,
    select_bearing,
)
from zoomounter.generate import build_parametric_prompt, build_prompt
from zoomounter.materials import get_material
from zoomounter.mount_specs import MOUNTS
from pathlib import Path

from zoomounter.step_inspect import parse_step
from zoomounter.verify import check_hole_positions, expected_holes

ALUMINIUM = get_material("aluminum_6061")


def _codes(notes, code):
    return [n for n in notes if n.code == code]


# ---------------------------------------------------------------------------
# The table itself. Wrong data here defeats everything downstream, which is
# this project's signature failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("b", BEARINGS, ids=lambda b: b.designation)
def test_every_bearing_carries_its_source(b):
    assert b.source, f"{b.designation} has no citation"
    assert "catalogue" in b.source.lower()


@pytest.mark.parametrize("b", BEARINGS, ids=lambda b: b.designation)
def test_dimensions_are_physically_ordered(b):
    assert 0 < b.bore_mm < b.od_mm, f"{b.designation}: bore must be inside the OD"
    assert b.width_mm > 0
    assert b.static_c0_n > 0 and b.dynamic_c_n > 0


def test_designations_are_unique():
    assert len(BY_DESIGNATION) == len(BEARINGS)


def test_bigger_radial_bearings_carry_more():
    """A monotonic sanity check across the series. A transposed digit in one
    row would break the ordering without looking wrong on its own line."""
    radial = sorted((b for b in BEARINGS if b.kind == RADIAL), key=lambda b: b.od_mm)
    ratings = [b.static_c0_n for b in radial]
    assert ratings == sorted(ratings), f"non-monotonic static ratings: {ratings}"


# ---------------------------------------------------------------------------
# Selection follows the use case. This is the feature.
# ---------------------------------------------------------------------------


def test_radial_load_picks_a_deep_groove_bearing():
    sel = select_bearing("radial", 200, 8.0, 2.0)
    assert sel.bearing.kind == RADIAL
    assert sel.bearing.designation == "608"


def test_axial_load_picks_a_thrust_bearing():
    sel = select_bearing("axial", 400, 12.0, 2.0)
    assert sel.bearing.kind == THRUST
    assert sel.bearing.designation == "51101"


def test_same_shaft_and_load_different_type_gives_a_different_bearing():
    """The whole point: the load case, not just the shaft, drives the part."""
    radial = select_bearing("radial", 300, 12.0, 2.0).bearing
    axial = select_bearing("axial", 300, 12.0, 2.0).bearing
    assert radial.designation != axial.designation
    assert radial.kind == RADIAL and axial.kind == THRUST


def test_picks_the_smallest_bearing_that_carries_the_load():
    """Not the strongest available -- the cheapest that works."""
    sel = select_bearing("radial", 100, 5.0, 2.0)
    assert sel.bearing.designation == "625"
    assert sel.bearing.static_c0_n >= 100 * 2.0


def test_heavier_load_on_the_same_shaft_steps_up():
    light = select_bearing("radial", 100, 5.0, 2.0).bearing
    heavy = select_bearing("radial", 400, 5.0, 2.0).bearing
    assert heavy.od_mm > light.od_mm, "a 4x load must not pick the same bearing"


# ---------------------------------------------------------------------------
# Refusals. Saying "none fits" is a real answer and has to be reachable.
# ---------------------------------------------------------------------------


def test_no_thrust_bearing_fits_a_nema17_shaft():
    """The 511xx series starts at a 10mm bore; a NEMA 17 shaft is 5mm. The
    tool must say so rather than fitting something four sizes too big."""
    sel = select_bearing("axial", 120, 5.0, 2.0)
    assert sel.bearing is None
    note = _codes(sel.notes, BEARING_SELECTION)[0]
    assert note.level == "LOUD WARN"
    assert "51100" in note.message and "10mm" in note.message


def test_overload_refuses_and_names_the_strongest_option():
    sel = select_bearing("radial", 5000, 8.0, 2.0)
    assert sel.bearing is None
    note = _codes(sel.notes, BEARING_SELECTION)[0]
    assert note.level == "LOUD WARN"
    assert "6001" in note.message  # strongest that fits an 8mm shaft


def test_unknown_shaft_blocks_selection():
    sel = select_bearing("radial", 50, 0.0, 2.0)
    assert sel.bearing is None
    assert _codes(sel.notes, BEARING_SELECTION)[0].level == "WARN"


def test_oversized_bore_is_offered_but_warned_about():
    """An 8mm shaft in a 10mm-bore thrust bearing is the only option, so it is
    offered -- loudly. Silence here would be a rattling shaft."""
    sel = select_bearing("axial", 100, 8.0, 2.0)
    assert sel.bearing.designation == "51100"
    fit = _codes(sel.notes, BEARING_FIT)[0]
    assert fit.level == "LOUD WARN"
    assert "10mm bore" in fit.message and "8mm" in fit.message


def test_exact_bore_match_does_not_warn():
    fit = _codes(select_bearing("radial", 100, 8.0, 2.0).notes, BEARING_FIT)[0]
    assert fit.level == "PASS"


def test_absurdly_undersized_shaft_is_not_padded_up():
    """A 5mm shaft must not be handed a 20mm-bore bearing just because it is
    strong enough -- past a few mm you are designing a different part."""
    sel = select_bearing("radial", 3000, 5.0, 2.0)
    assert sel.bearing is None


# ---------------------------------------------------------------------------
# Block geometry.
# ---------------------------------------------------------------------------


def test_block_reproduces_the_handwritten_608():
    """The proportions rule was derived from the bearing_608 mount someone
    drew by hand. Reproducing it exactly is the evidence the rule is real
    rather than tidy-looking numbers."""
    ref = MOUNTS["bearing_608"]
    gen = bearing_block(BY_DESIGNATION["608"], "radial")
    assert gen.plate_width_mm == ref.plate_width_mm
    assert gen.plate_height_mm == ref.plate_height_mm
    assert gen.bolt_hole_dia_mm == ref.bolt_hole_dia_mm
    assert gen.hole_positions == ref.hole_positions
    assert gen.bearing_seat_dia_mm == ref.center_hole_dia_mm


def test_radial_block_is_a_through_bore():
    gen = bearing_block(BY_DESIGNATION["608"], "radial")
    assert gen.bearing_seat_depth_mm == 0
    assert gen.center_hole_dia_mm == 0  # the seat is the hole


def test_thrust_block_is_a_counterbore_over_a_shaft_hole():
    b = BY_DESIGNATION["51101"]
    gen = bearing_block(b, "axial")
    assert gen.bearing_seat_depth_mm == b.width_mm
    assert 0 < gen.center_hole_dia_mm < gen.bearing_seat_dia_mm, (
        "the shaft hole must pass through the floor of the counterbore"
    )


def test_block_limit_comes_from_the_bearing_rating():
    """Fitting a bearing should visibly raise the limit, not report
    'not checked'. A 608 block takes 1370N where the NEMA 17 it relieves
    took 28N."""
    gen = bearing_block(BY_DESIGNATION["608"], "radial")
    assert gen.max_radial_n == 1370
    assert gen.max_radial_n > MOUNTS["nema17"].max_radial_n
    assert "C0" in gen.load_limit_source and "SKF" in gen.load_limit_source


def test_thrust_block_limits_the_axial_direction_only():
    gen = bearing_block(BY_DESIGNATION["51101"], "axial")
    assert gen.max_axial_n == 16600
    assert gen.max_radial_n is None, "a thrust bearing takes no radial load"


def test_big_bearings_get_bigger_bolts():
    small = bearing_block(BY_DESIGNATION["608"], "radial")
    large = bearing_block(BY_DESIGNATION["6004"], "radial")
    assert large.bolt_hole_dia_mm > small.bolt_hole_dia_mm


# ---------------------------------------------------------------------------
# The seat has to physically fit the plate.
# ---------------------------------------------------------------------------


def test_thin_plate_cannot_grip_a_radial_bearing():
    """The sizing calc and the bearing width are independent constraints. A
    2.69mm plate is structurally fine and still cannot hold a 7mm bearing."""
    chk = check_seat_depth(BY_DESIGNATION["608"], 2.69, "radial")
    assert chk.level == "LOUD WARN"
    assert "38%" in chk.message


def test_thick_enough_plate_passes():
    assert check_seat_depth(BY_DESIGNATION["608"], 8.0, "radial").level == "PASS"


def test_thrust_seat_needs_material_under_the_counterbore():
    """A blind pocket the full depth of the plate is a through-hole."""
    b = BY_DESIGNATION["51101"]
    assert check_seat_depth(b, b.width_mm, "axial").level == "LOUD WARN"
    assert check_seat_depth(b, b.width_mm + 2.0, "axial").level == "PASS"


# ---------------------------------------------------------------------------
# Geometry reaches verification and the prompt.
# ---------------------------------------------------------------------------


def test_seat_is_verified_against_the_step():
    gen = bearing_block(BY_DESIGNATION["608"], "radial")
    dias = {round(d, 2) for _, _, d in expected_holes(gen)}
    assert 22.0 in dias, "the seat bore must be checked, not assumed"


def test_counterbore_gives_two_concentric_circles():
    """Same shape the counterbore probe confirmed the API emits."""
    gen = bearing_block(BY_DESIGNATION["51101"], "axial")
    at_origin = sorted(d for x, y, d in expected_holes(gen) if x == 0 and y == 0)
    assert at_origin == [13.0, 26.0]


def test_counterbore_volume_does_not_double_count_the_shaft_hole():
    """A blind counterbore removes material to its own depth only. Treating
    it as a through-hole under-reports the part, and the volume check would
    then fail a part that is actually correct."""
    b = BY_DESIGNATION["51101"]
    gen = bearing_block(b, "axial")
    t = 12.0

    plate = gen.plate_width_mm * gen.plate_height_mm
    centre = math.pi * (gen.center_hole_dia_mm / 2) ** 2
    bolts = len(gen.hole_positions) * math.pi * (gen.bolt_hole_dia_mm / 2) ** 2
    seat = math.pi * (gen.bearing_seat_dia_mm / 2) ** 2
    expected = (plate - centre - bolts) * t - (seat - centre) * gen.bearing_seat_depth_mm

    assert gen.estimate_volume_mm3(t) == pytest.approx(expected)

    naive_through = (plate - centre - bolts - seat) * t
    assert gen.estimate_volume_mm3(t) > naive_through


def _literal(mount, thickness):
    return build_prompt(mount, ALUMINIUM, thickness)


def _parametric(mount, thickness):
    return build_parametric_prompt(mount, ALUMINIUM, thickness)[0]


# Both builders, always. The seat clause was added to the literal prompt and
# silently missed on the parametric one, which is the default path -- so the
# tool declared bearingSeatDia and bearingSeatDepth as parameters and then
# never mentioned the counterbore in the body. The generated block would have
# had no seat at all. Testing only one builder is what let that through.
BUILDERS = [
    pytest.param(_literal, id="literal"),
    pytest.param(_parametric, id="parametric"),
]


@pytest.mark.parametrize("build", BUILDERS)
def test_prompt_describes_a_through_bore_for_radial(build):
    prompt = build(bearing_block(BY_DESIGNATION["608"], "radial"), 8.0)
    assert "608" in prompt
    assert "counterbore" not in prompt.lower()


@pytest.mark.parametrize("build", BUILDERS)
def test_prompt_describes_a_blind_counterbore_for_thrust(build):
    prompt = build(bearing_block(BY_DESIGNATION["51101"], "axial"), 12.0)
    assert "counterbore" in prompt.lower()
    assert "blind" in prompt.lower()
    assert "51101" in prompt


@pytest.mark.parametrize("build", BUILDERS)
def test_thrust_prompt_never_claims_the_seat_goes_all_the_way_through(build):
    """A blind counterbore and 'all holes go through the full thickness' in
    the same prompt is a contradiction, and the model resolves it by drilling
    straight through."""
    prompt = build(bearing_block(BY_DESIGNATION["51101"], "axial"), 12.0)
    assert "All holes and slots go through the full thickness of the plate." not in prompt


def test_parametric_prompt_uses_every_parameter_it_declares():
    """Declaring a parameter and never referring to it produced exactly this
    bug: bearingSeatDia and bearingSeatDepth were defined at the top of the
    prompt and absent from the body, so the seat was never built."""
    for designation, load_type in [("608", "radial"), ("51101", "axial")]:
        mount = bearing_block(BY_DESIGNATION[designation], load_type)
        prompt, scheme = build_parametric_prompt(mount, ALUMINIUM, 12.0)
        body = prompt.split("referring to them by name", 1)[1]
        for name in scheme.names:
            assert name in body, (
                f"{designation}: '{name}' is declared but never used in the "
                f"prompt body"
            )


# ---------------------------------------------------------------------------
# Forcing a designation.
# ---------------------------------------------------------------------------


def test_forcing_the_wrong_bearing_type_warns():
    _, sel = auto_bearing_mount("radial", 200, 12.0, 2.0, designation="51101")
    loud = [n for n in sel.notes if n.level == "LOUD WARN"]
    assert any("thrust bearing but the load is radial" in n.message for n in loud)


def test_forcing_an_undersized_bearing_warns():
    _, sel = auto_bearing_mount("radial", 900, 5.0, 2.0, designation="625")
    loud = [n for n in sel.notes if n.level == "LOUD WARN"]
    assert any("380N static" in n.message for n in loud)


def test_unknown_designation_is_an_error():
    with pytest.raises(ValueError, match="Unknown bearing"):
        auto_bearing_mount("radial", 100, 8.0, 2.0, designation="not-a-bearing")


def test_auto_mount_returns_none_when_nothing_fits():
    spec, sel = auto_bearing_mount("axial", 120, 5.0, 2.0)
    assert spec is None and sel.bearing is None


# ---------------------------------------------------------------------------
# Bounding box must measure the solid, not the tools that cut it.
# ---------------------------------------------------------------------------

THRUST_STEP = (
    Path(__file__).parent.parent
    / "probes" / "bearing" / "51101-thrust" / "export" / "output.step"
)


@pytest.mark.skipif(not THRUST_STEP.exists(), reason="fixture not generated")
def test_bbox_ignores_the_geometry_of_consumed_cutting_tools():
    """The thrust block is 11mm thick and was cut by a revolved profile
    spanning +/-11mm. Measuring every CARTESIAN_POINT in the file gave 16.50mm
    and failed a part that is exactly right by 50% -- a false FAIL, which
    costs more trust than a false pass.

    VERTEX_POINTs are the corners of what survived the boolean."""
    geo = parse_step(THRUST_STEP)
    assert geo.bbox.thickness_mm == pytest.approx(11.0, abs=0.01)
    assert geo.bbox.width_mm == pytest.approx(44.0, abs=0.01)


@pytest.mark.skipif(not THRUST_STEP.exists(), reason="fixture not generated")
def test_the_fixture_really_does_contain_points_outside_the_solid():
    """Guards the test above. If this file ever stops carrying stray tool
    geometry, the test proves nothing and should be pointed at one that
    does."""
    from zoomounter.step_inspect import (
        _CARTESIAN_POINT_RE,
        _parse_coords,
        _scale_to_mm,
    )

    text = THRUST_STEP.read_text(errors="replace")
    scale = _scale_to_mm(text)
    zs = [
        _parse_coords(c)[2] * scale
        for _, c in _CARTESIAN_POINT_RE.findall(text)
        if len(_parse_coords(c)) == 3
    ]
    naive_thickness = max(zs) - min(zs)
    assert naive_thickness > 11.0 + 1.0, (
        "fixture no longer exercises the bug this test exists for"
    )


@pytest.mark.skipif(not THRUST_STEP.exists(), reason="fixture not generated")
def test_the_generated_thrust_block_verifies():
    """End-to-end: the counterbore geometry the API built matches what was
    asked for, checked hole by hole out of the STEP."""
    mount = bearing_block(BY_DESIGNATION["51101"], "axial")
    geo = parse_step(THRUST_STEP)
    result, details = check_hole_positions(geo, mount)
    assert result.passed, result.detail
    assert len(details) == 6  # 4 bolt + shaft bore + counterbore
