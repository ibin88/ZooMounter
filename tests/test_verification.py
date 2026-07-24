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
from zoomounter.mechanics import required_thickness
from zoomounter.mount_specs import get_mount
from zoomounter.step_inspect import match_holes, parse_step
from zoomounter.verify import check_bounding_box, check_hole_positions, expected_holes

FIXTURES = Path(__file__).parent
GOOD_STEP = FIXTURES.parent / "examples" / "nema17_aluminum_example.step"
DRIFTED_STEP = FIXTURES / "fixture_nema17_drifted_hole.step"


# ---- STEP parsing -----------------------------------------------------


def test_parses_nema17_geometry():
    """The parser should recover the exact plate the spec asked for."""
    geo = parse_step(GOOD_STEP)
    assert geo.bbox.width_mm == pytest.approx(42.3, abs=0.01)
    assert geo.bbox.height_mm == pytest.approx(42.3, abs=0.01)
    assert geo.bbox.thickness_mm == pytest.approx(1.0, abs=0.01)


def test_merges_coincident_circles_into_one_hole_each():
    """A NEMA17 plate has 4 bolt holes + 1 centre bore. Each shows up as
    several circular edges in the file; we should end up with 5 holes, not 20."""
    geo = parse_step(GOOD_STEP)
    assert len(geo.circles) == 5
    bolt_holes = [c for c in geo.circles if c.diameter_mm == pytest.approx(3.0, abs=0.01)]
    centre = [c for c in geo.circles if c.diameter_mm == pytest.approx(22.0, abs=0.01)]
    assert len(bolt_holes) == 4
    assert len(centre) == 1


def test_bolt_holes_sit_on_the_specified_bolt_circle():
    geo = parse_step(GOOD_STEP)
    bolt_holes = [c for c in geo.circles if c.diameter_mm == pytest.approx(3.0, abs=0.01)]
    for hole in bolt_holes:
        radius = math.hypot(hole.x_mm, hole.y_mm)
        assert radius == pytest.approx(15.5, abs=0.01)  # 31mm bolt circle


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
    assert check_bounding_box(geo, get_mount("nema17"), 1.0).passed


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


# ---- sizing calcs -------------------------------------------------------


def test_radial_load_is_thickness_governed():
    """A side load bends the plate, so more load must mean more material."""
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    light = required_thickness(50, mount, material, 2.0, load_type="radial")
    heavy = required_thickness(500, mount, material, 2.0, load_type="radial")
    assert heavy.required_thickness_mm > light.required_thickness_mm


def test_self_weight_included_for_radial_motor_mounts():
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    result = required_thickness(10, mount, material, 2.0, load_type="radial")
    assert result.self_weight_n > 0
    assert result.effective_load_n == pytest.approx(10 + result.self_weight_n)


def test_axial_self_weight_not_added():
    """Gravity acts perpendicular to an axial thrust, so it must not be
    summed into it."""
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    result = required_thickness(100, mount, material, 2.0, load_type="axial")
    assert result.self_weight_n == 0
    assert result.effective_load_n == 100


def test_axial_warns_when_fasteners_are_the_real_limit():
    """A load past the screws' tensile capacity must say so, rather than
    quietly returning a thin plate that reads as an all-clear."""
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    result = required_thickness(20000, mount, material, 2.0, load_type="axial")
    assert any(n.startswith("WARNING") for n in result.notes)


def test_axial_always_flags_what_it_does_not_check():
    mount, material = get_mount("nema17"), get_material("aluminum_6061")
    result = required_thickness(100, mount, material, 2.0, load_type="axial")
    assert any("NOT CHECKED" in n for n in result.notes)


def test_governing_limit_is_reported_honestly():
    """At a trivial load the process floor decides, and the result should
    admit that rather than implying the engineering calc drove it."""
    mount, material = get_mount("nema17"), get_material("pla")
    result = required_thickness(1, mount, material, 2.0, load_type="axial")
    assert "process minimum" in result.governing_limit


def test_thicker_material_requirement_scales_with_safety_factor():
    mount, material = get_mount("nema23"), get_material("mild_steel")
    low = required_thickness(400, mount, material, 1.5, load_type="radial")
    high = required_thickness(400, mount, material, 4.0, load_type="radial")
    assert high.required_thickness_mm > low.required_thickness_mm
