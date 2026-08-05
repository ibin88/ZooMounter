"""Offline tests for the geometry parser, verifier and sizing calcs.

Runs with no API calls and no credits -- every fixture is a STEP file already
on disk. Run with:

    python -m pytest tests/ -v

The headline test is `test_volume_is_blind_to_hole_position`, which is the
reason the hole-position check exists at all: it pins down, as an executable
assertion, that a scalar volume check cannot distinguish a correct plate from
one whose bolt hole is 2mm out of place.
"""

import math
from pathlib import Path

import pytest

from zoomounter.materials import get_material
from zoomounter.mechanics import (
    BEARING_SEAT_GOVERNS,
    PROCESS_FLOOR_GOVERNS,
    THERMAL_UNMODELLED,
    required_thickness,
)
from zoomounter.mount_specs import get_mount, square_bolt_pattern
from zoomounter.step_inspect import match_holes, parse_step
from zoomounter.verify import check_bounding_box, check_hole_positions, expected_holes

FIXTURES = Path(__file__).parent
GOOD_STEP = FIXTURES.parent / "examples" / "nema17_aluminum_example.step"
DRIFTED_STEP = FIXTURES / "fixture_nema17_drifted_hole.step"

# The fixtures are a real generated NEMA 17 plate: aluminium, 150N radial
# load, SF 2 -> 4.65mm thick (deflection-governed). Kept as a named constant
# so a regenerated fixture only needs updating in one place.
FIXTURE_THICKNESS_MM = 4.65


# ---- STEP parsing -----------------------------------------------------


def test_parses_nema17_geometry():
    """The parser should recover the exact plate the spec asked for."""
    mount = get_mount("nema17")
    geo = parse_step(GOOD_STEP)
    assert geo.bbox.width_mm == pytest.approx(mount.plate_width_mm, abs=0.01)
    assert geo.bbox.height_mm == pytest.approx(mount.plate_height_mm, abs=0.01)
    assert geo.bbox.thickness_mm == pytest.approx(FIXTURE_THICKNESS_MM, abs=0.01)


def test_merges_coincident_circles_into_one_hole_each():
    """A NEMA17 plate has 4 bolt holes + 1 centre bore. Each shows up as
    several circular edges in the file; we should end up with 5 holes, not 20."""
    mount = get_mount("nema17")
    geo = parse_step(GOOD_STEP)
    assert len(geo.circles) == 5
    bolt_holes = [
        c for c in geo.circles if c.diameter_mm == pytest.approx(mount.bolt_hole_dia_mm, abs=0.01)
    ]
    centre = [
        c for c in geo.circles if c.diameter_mm == pytest.approx(mount.center_hole_dia_mm, abs=0.01)
    ]
    assert len(bolt_holes) == 4
    assert len(centre) == 1


def test_bolt_holes_sit_at_the_square_corners():
    """The generated part must match the real NEMA square pattern -- holes on
    the diagonal at s/sqrt(2), not on the axes at s/2."""
    mount = get_mount("nema17")
    geo = parse_step(GOOD_STEP)
    bolt_holes = [
        c for c in geo.circles if c.diameter_mm == pytest.approx(mount.bolt_hole_dia_mm, abs=0.01)
    ]
    assert len(bolt_holes) == 4
    for hole in bolt_holes:
        assert abs(hole.x_mm) == pytest.approx(15.5, abs=0.01)
        assert abs(hole.y_mm) == pytest.approx(15.5, abs=0.01)
        assert math.hypot(hole.x_mm, hole.y_mm) == pytest.approx(21.92, abs=0.02)


# ---- the verifier catching real problems -------------------------------


def test_good_part_passes_hole_check():
    geo = parse_step(GOOD_STEP)
    check, matches = check_hole_positions(geo, get_mount("nema17"))
    assert check.passed
    assert all(m.found for m in matches)
    assert max(m.position_error_mm for m in matches) < 0.01


def test_drifted_hole_is_caught():
    """One bolt hole displaced 2mm must fail the position check."""
    geo = parse_step(DRIFTED_STEP)
    check, matches = check_hole_positions(geo, get_mount("nema17"))
    assert not check.passed
    worst = max(m.position_error_mm for m in matches if m.found)
    assert worst == pytest.approx(2.0, abs=0.05)


def test_wrong_bolt_pattern_entirely_is_caught():
    """A NEMA17 plate checked against a NEMA23 spec should find nothing."""
    geo = parse_step(GOOD_STEP)
    check, matches = check_hole_positions(geo, get_mount("nema23"))
    assert not check.passed
    assert not any(m.found for m in matches)


def test_bounding_box_is_blind_to_hole_position():
    """Documents a real limitation: the drifted part has the same outline, so
    the bbox check cannot see the problem. Only the hole check can."""
    geo = parse_step(DRIFTED_STEP)
    assert check_bounding_box(geo, get_mount("nema17"), FIXTURE_THICKNESS_MM).passed


def test_volume_is_blind_to_hole_position():
    """The reason the hole-position check exists.

    Moving a hole removes exactly the same amount of material, so the
    hand-calc volume is byte-identical between a correct part and a broken
    one. Measured volumes for these two fixtures (via Zoo's File Format API)
    were 1381.22 and 1381.17 mm3 -- a 0.004% difference on a part that will
    not bolt onto its motor. Volume can never be the check that catches this.
    """
    mount = get_mount("nema17")
    good = parse_step(GOOD_STEP)
    drifted = parse_step(DRIFTED_STEP)

    # Same holes, same sizes, same plate -- so identical expected volume...
    assert len(good.circles) == len(drifted.circles)
    assert sorted(round(c.diameter_mm, 3) for c in good.circles) == sorted(
        round(c.diameter_mm, 3) for c in drifted.circles
    )
    # ...but the positions differ, and only the hole check notices.
    assert check_hole_positions(good, mount)[0].passed
    assert not check_hole_positions(drifted, mount)[0].passed


# ---- hole matching behaviour -------------------------------------------


def test_matching_pairs_on_size_before_position():
    """A bolt hole must never be paired with the centre bore just because it
    happens to be nearer -- diameter has to match first."""
    geo = parse_step(GOOD_STEP)
    mount = get_mount("nema17")
    matches = match_holes(geo, expected_holes(mount))
    for m in matches:
        assert m.found
        assert m.diameter_error_mm < 0.01


# ---- NEMA bolt patterns (regression) -------------------------------------
# ZooMounter originally built these with circular_bolt_pattern(4, spacing),
# which put the holes at the midpoints of the plate edges instead of the
# corners -- a 6.4mm error per hole on a NEMA 17, and a plate that will not
# bolt to a motor. Caught by comparing against a hand-built gantry assembly,
# NOT by ZooMounter's own verification, which checks generated parts against
# this same table and so happily confirmed the wrong geometry.


@pytest.mark.parametrize(
    "mount_key, spacing_mm",
    [("nema17", 31.0), ("nema23", 47.14)],
)
def test_nema_holes_are_at_square_corners_not_on_a_bolt_circle(mount_key, spacing_mm):
    """NEMA quotes a square spacing between hole centres, not a bolt-circle
    diameter. Holes must sit at (+/-s/2, +/-s/2)."""
    mount = get_mount(mount_key)
    half = spacing_mm / 2
    expected = {(half, half), (half, -half), (-half, -half), (-half, half)}
    actual = {(round(x, 4), round(y, 4)) for x, y in mount.hole_positions}
    assert actual == {(round(x, 4), round(y, 4)) for x, y in expected}

    # Every hole is on the diagonal, at s/sqrt(2) -- NOT at s/2, which is what
    # the old bug produced.
    for x, y in mount.hole_positions:
        assert math.hypot(x, y) == pytest.approx(half * math.sqrt(2), abs=0.01)
        assert math.hypot(x, y) != pytest.approx(half, abs=0.01)


# ISO 273 clearance holes. A screw cannot physically pass through anything
# smaller than the "close" figure, so these are hard minimums, not preferences.
_ISO273_CLOSE_MM = {"M2.5": 2.7, "M3": 3.2, "M4": 4.3, "M5": 5.3}

# What screw each mount is designed around.
_MOUNT_SCREW = {
    "nema17": "M3",
    "nema23": "M5",
    "bearing_608": "M3",
}


def test_every_mount_is_covered_by_the_screw_map():
    """The map above is the catalogue written down a second time, so it can
    drift from the catalogue. It already did: it named raspberry_pi and
    vesa_75 after both were removed. Fail here rather than let a new mount
    quietly skip the clearance check below."""
    from zoomounter.mount_specs import MOUNTS

    assert set(_MOUNT_SCREW) == set(MOUNTS), (
        "_MOUNT_SCREW and the mount catalogue disagree; a mount added to one "
        "and not the other is a mount whose bolt holes nothing checks."
    )


@pytest.mark.parametrize("mount_key, screw", sorted(_MOUNT_SCREW.items()))
def test_bolt_holes_are_clearance_not_interference(mount_key, screw):
    """Every mount, not just NEMA.

    The original version of this test only checked nema17/nema23, which is why
    bearing_608 kept a 3.0mm hole for an M3 screw long after the same mistake
    had been found and fixed on the NEMA mounts. A bug class is only really
    fixed once the test covers every case it could occur in.
    """
    hole = get_mount(mount_key).bolt_hole_dia_mm
    minimum = _ISO273_CLOSE_MM[screw]
    assert hole >= minimum, (
        f"{mount_key}: {hole}mm hole is smaller than ISO 273 close clearance "
        f"for {screw} ({minimum}mm) -- the screw would not pass through."
    )


def test_square_bolt_pattern_helper():
    holes = square_bolt_pattern(31.0)
    assert len(holes) == 4
    assert all(abs(abs(x) - 15.5) < 1e-6 and abs(abs(y) - 15.5) < 1e-6 for x, y in holes)


# ---- thickness ----------------------------------------------------------
#
# The tests that used to sit here checked a structural layer that has been
# removed: bending stress, L/300 tip deflection, screw-head punching shear and
# fastener tension. They are gone rather than ported, because they asserted
# behaviour the tool deliberately no longer has.
#
# The reason for the removal is that the layer never governed. For every part
# in scope the structural requirement lands below the minimum wall the process
# can produce, so a thickness quoted to two decimals from a named beam formula
# was the process floor wearing a calculation's clothes. Two of the deleted
# tests are worth naming, because they encoded the confusion directly:
# `test_radial_load_is_thickness_governed` asserted more load means more
# material, which is false here; and `..._scales_with_safety_factor` asserted
# the safety factor moves the thickness, which it now does not -- it applies
# to bearing selection, where there is a rating to divide.
#
# What replaced them asserts the inversion instead: thickness is a
# manufacturing answer, and the load question is answered somewhere else.


def test_thickness_does_not_depend_on_the_load():
    """The inversion, asserted directly. The plate is sized by what the
    process can make and what the bearing needs to seat in -- neither of which
    knows anything about the load."""
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    assert (
        required_thickness(mount, material, plate_load_n=0).required_thickness_mm
        == required_thickness(mount, material, plate_load_n=5000).required_thickness_mm
    )


def test_governing_limit_is_reported_honestly():
    """The result should say the process floor decided, rather than implying
    an engineering calc drove it."""
    result = required_thickness(get_mount("nema17"), get_material("pla"))
    assert "process minimum" in result.governing_limit
    assert any(n.code == PROCESS_FLOOR_GOVERNS for n in result.notes)


def test_a_bearing_seat_can_raise_the_thickness():
    """A plate too thin to hold its bearing is not a lighter plate, it is a
    part that cannot exist. The seat depth is a floor alongside the process
    minimum, not a warning issued after the number is fixed."""
    from zoomounter.bearings import BY_DESIGNATION, bearing_block

    seated = bearing_block(BY_DESIGNATION["51101"], "axial")
    result = required_thickness(seated, get_material("aluminum_6061"))
    assert result.governing_limit.startswith("bearing seat")
    assert result.required_thickness_mm > result.min_wall_mm
    assert any(n.code == BEARING_SEAT_GOVERNS for n in result.notes)


def test_printed_parts_are_warned_about_heat():
    """A NEMA case runs at 70-80 C and PLA softens below that. The old code
    computed an L/300 deflection limit that models none of this; stating the
    limitation is more honest than approximating it with an unrelated rule."""
    printed = required_thickness(get_mount("nema17"), get_material("pla"))
    machined = required_thickness(get_mount("nema17"), get_material("aluminum_6061"))
    assert any(n.code == THERMAL_UNMODELLED for n in printed.notes)
    assert not any(n.code == THERMAL_UNMODELLED for n in machined.notes)
