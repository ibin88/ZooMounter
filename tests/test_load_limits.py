"""Regression tests for the four defects found in the items 1-6 review.

Each test here exists because something shipped past a green suite. The point
of the file is not coverage -- it is that these four specific mistakes cannot
come back silently.

Runs offline, no API calls, no credits.
"""

import argparse
import dataclasses
from unittest import mock

import pytest

from zoomounter import cli
from zoomounter.materials import get_material
from zoomounter.mechanics import (
    BEARING_REQUIRED,
    SHAFT_LIMIT,
    SHAFT_LIMIT_UNKNOWN,
    SHAFT_OK,
    SHAFT_UNKNOWN,
    shaft_support,
)
from zoomounter.mount_specs import (
    EXTRUSION_SERIES,
    MOUNTS,
    MountSpec,
    apply_host_mount,
    get_mount,
)

ALUMINIUM = get_material("aluminum_6061")
SF = 2.0


def _limit_check(mount_name, load_n, load_type, offset_mm=None):
    """The single Check that reports on the component's own shaft limit.

    Selected by code, not by message text -- an earlier version of this
    helper matched on the word "limit" and caught two unrelated notes
    ("your limiting element", "not structurally limited")."""
    decision = shaft_support(
        mount=get_mount(mount_name),
        shaft_load_n=load_n,
        load_type=load_type,
        offset_mm=offset_mm,
    )
    hits = [c for c in decision.checks if c.code in (SHAFT_LIMIT, SHAFT_LIMIT_UNKNOWN)]
    assert len(hits) == 1, f"expected exactly one limit check, got {len(hits)}"
    return hits[0]


# ---------------------------------------------------------------------------
# Defect 1: the axial limit was hardcoded at 67N for every motor, but 67N is
# a NEMA 23 figure. A NEMA 17 -- a physically smaller motor -- passed silently
# at loads many times its own rating.
# ---------------------------------------------------------------------------


def test_nema17_axial_limit_is_not_the_nema23_limit():
    """The regression itself: 60N is under the old hardcoded 67N, so this
    case used to produce no warning at all."""
    check = _limit_check("nema17", 60, "axial")
    assert check.level == "LOUD WARN"
    assert "10N" in check.message


def test_each_motor_uses_its_own_limit():
    """Two different frames must not share a limit."""
    n17 = MOUNTS["nema17"]
    n23 = MOUNTS["nema23"]
    assert n17.max_axial_n != n23.max_axial_n
    assert n17.max_radial_n != n23.max_radial_n
    # The smaller frame must not tolerate more than the larger one.
    assert n17.max_axial_n < n23.max_axial_n
    assert n17.max_radial_n < n23.max_radial_n


def test_load_within_limit_passes():
    assert _limit_check("nema17", 5, "axial").level == "PASS"


def test_radial_loads_are_checked_too():
    """Radial had no component check at all -- only the plate was sized."""
    check = _limit_check("nema17", 120, "radial")
    assert check.level == "LOUD WARN"
    assert "28N" in check.message


def test_unknown_limit_warns_rather_than_passing():
    """A mount with no published figure must read as unknown, never as safe.
    Silence here is what let the original bug hide."""
    check = _limit_check("bearing_608", 50, "axial")
    assert check.level == "WARN"
    assert "NOT been checked" in check.message


def test_limit_warning_is_independent_of_the_plate():
    """No plate thickness can raise the motor's own bearing rating. The shaft
    decision is therefore computed without reference to the plate at all --
    this used to be a note attached to a thickness result, which is what made
    it read as a caveat on the answer rather than as the answer."""
    check = _limit_check("nema23", 500, "axial")
    assert check.level == "LOUD WARN"


# ---------------------------------------------------------------------------
# A radial rating is a moment limit. Both sides must be converted before being
# compared, or the same load reads as safe at one offset and not at another
# while the tool reports no difference.
# ---------------------------------------------------------------------------


def test_radial_comparison_uses_moments_not_forces():
    """28N is exactly the NEMA 17's rated force, but the rating is quoted at
    20mm. Applied at 40mm it is twice the rated moment, and comparing the bare
    forces would call that a pass."""
    at_rated_distance = shaft_support(get_mount("nema17"), 28, "radial", offset_mm=20)
    assert at_rated_distance.verdict != BEARING_REQUIRED
    assert at_rated_distance.utilisation == pytest.approx(1.0)

    further_out = shaft_support(get_mount("nema17"), 28, "radial", offset_mm=40)
    assert further_out.verdict == BEARING_REQUIRED
    assert further_out.utilisation == pytest.approx(2.0)


def test_a_load_closer_in_is_not_judged_harshly():
    """The error runs both ways. 30N at 10mm exceeds the rated FORCE of 28N,
    but sits at 54% of the rated MOMENT -- comparing forces would fail a load
    the motor comfortably takes."""
    decision = shaft_support(get_mount("nema17"), 30, "radial", offset_mm=10)
    assert decision.shaft_load_n > decision.limit_n  # above the rated force
    assert decision.utilisation == pytest.approx(300 / 560)
    assert decision.verdict == SHAFT_OK


def test_axial_is_not_scaled_by_offset():
    """Thrust loads the shaft along its axis wherever it comes from, so the
    offset must not enter the axial comparison."""
    near = shaft_support(get_mount("nema17"), 8, "axial", offset_mm=5)
    far = shaft_support(get_mount("nema17"), 8, "axial", offset_mm=500)
    assert near.utilisation == far.utilisation == pytest.approx(0.8)


def test_every_radial_rating_ships_its_measurement_distance():
    """The catalogue loader enforces this, but assert it here too: a rating
    without its distance cannot be compared to anything, and the comparison
    above would silently divide by a missing number."""
    for key, spec in MOUNTS.items():
        if spec.max_radial_n is not None:
            assert spec.max_radial_at_mm, (
                f"{key}: publishes max_radial_n with no max_radial_at_mm"
            )


# ---------------------------------------------------------------------------
# A shaft load and a bracket load are different physical quantities. Feeding a
# bracket load to the shaft check reports an overload that cannot happen.
# ---------------------------------------------------------------------------


def test_a_bracket_load_is_not_a_shaft_load():
    """The CLI prompted for "expected load on the mount" and compared the
    answer against the motor's SHAFT rating. Bolt a 50N camera to the plate
    and it reported a shaft overload -- the camera's weight never reaches the
    shaft. The two are now separate inputs, and only one is checked."""
    from zoomounter.mechanics import PLATE_LOAD_UNMODELLED, required_thickness

    result = required_thickness(
        mount=get_mount("nema17"), material=ALUMINIUM, plate_load_n=50
    )
    codes = [c.code for c in result.notes]
    assert PLATE_LOAD_UNMODELLED in codes
    assert SHAFT_LIMIT not in codes, "a bracket load must not reach the shaft check"


# ---------------------------------------------------------------------------
# Defect 2: the citation pointed at docs/mechanics.html -- our own document.
# Provenance that cites us proves nothing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mount_name", ["nema17", "nema23"])
def test_limit_citation_is_external(mount_name):
    source = MOUNTS[mount_name].load_limit_source
    assert source, "a published limit must carry its source"
    assert "docs/" not in source, "citing our own docs is circular"
    assert "mechanics.html" not in source
    assert "datasheet" in source.lower()


@pytest.mark.parametrize("mount_name", ["nema17", "nema23"])
def test_citation_reaches_the_user(mount_name):
    """The source has to be on the Check the user actually sees, not just
    parked in the spec table."""
    check = _limit_check(mount_name, 1000, "axial")
    assert check.source
    assert "datasheet" in check.source.lower()


def test_nema23_records_the_unit_conflict():
    """Two vendors publish '15' for NEMA 23 axial load in different units --
    15 N (ATO) vs 15 lb (Same Sky), a 4.45x spread. We take the conservative
    value, and the disagreement must stay visible rather than being quietly
    resolved."""
    source = MOUNTS["nema23"].load_limit_source
    assert "15N" in source
    assert "lb" in source, "the conflicting figure must remain on the record"
    assert MOUNTS["nema23"].max_axial_n == 15.0


# ---------------------------------------------------------------------------
# Defect 3: --host-mount 4040-slots was byte-identical to 2020-slots. Both
# rounded spacing to 20mm multiples, so the option parsed, generated and
# verified while doing nothing.
# ---------------------------------------------------------------------------


def test_4040_differs_from_2020():
    base = get_mount("nema17")
    a = apply_host_mount(base, "2020-slots")
    b = apply_host_mount(base, "4040-slots")
    assert a.host_slots != b.host_slots
    assert a.plate_width_mm != b.plate_width_mm


def test_slot_spacing_lands_on_the_series_pitch():
    """Bolts have to fall in real T-slots, so spacing must be a whole
    multiple of that profile's pitch."""
    base = get_mount("nema17")
    for option, series in EXTRUSION_SERIES.items():
        spec = apply_host_mount(base, option)
        xs = sorted(x for x, _, _, _, _ in spec.host_slots)
        spacing = xs[-1] - xs[0]
        pitch = series["pitch_mm"]
        assert spacing % pitch == pytest.approx(0), (
            f"{option}: {spacing}mm spacing is not a multiple of {pitch}mm pitch"
        )


def test_slots_clear_the_motor_body():
    base = get_mount("nema17")
    for option in EXTRUSION_SERIES:
        spec = apply_host_mount(base, option)
        for x, _, _, width, _ in spec.host_slots:
            near_edge = abs(x) - width / 2
            assert near_edge > base.plate_width_mm / 2, (
                f"{option}: slot at x={x} overlaps the motor footprint"
            )


def test_slots_fit_inside_the_plate():
    base = get_mount("nema17")
    for option in EXTRUSION_SERIES:
        spec = apply_host_mount(base, option)
        for x, _, _, width, _ in spec.host_slots:
            assert abs(x) + width / 2 < spec.plate_width_mm / 2, (
                f"{option}: slot at x={x} runs off the plate"
            )


def test_host_mount_preserves_every_unrelated_field():
    """apply_host_mount used to hand-copy a field list, and silently dropped
    the load limits when they were added -- so any part with host features
    reported "no published limit on file" and lost its safety warning.

    Asserted field-by-field over dataclasses.fields rather than over the three
    fields I happen to remember, so a field added tomorrow is covered too."""
    base = get_mount("nema17")
    modified = apply_host_mount(base, "2020-slots")
    intentionally_changed = {"name", "plate_width_mm", "host_holes", "host_slots"}

    for f in dataclasses.fields(MountSpec):
        if f.name in intentionally_changed:
            continue
        assert getattr(modified, f.name) == getattr(base, f.name), (
            f"apply_host_mount dropped or altered '{f.name}'"
        )


@pytest.mark.parametrize("option", ["2020-slots", "4040-slots", "corner-holes"])
def test_host_mounted_parts_still_get_their_limit_checked(option):
    """The user-visible half of the bug: the warning must survive the
    host-mount transform, not degrade to 'not checked'."""
    spec = apply_host_mount(get_mount("nema17"), option)
    decision = shaft_support(mount=spec, shaft_load_n=120, load_type="radial")
    checks = [c for c in decision.checks if c.code in (SHAFT_LIMIT, SHAFT_LIMIT_UNKNOWN)]
    assert len(checks) == 1
    assert checks[0].level == "LOUD WARN", (
        f"{option}: 120N against a 28N limit must warn, got {checks[0].level}"
    )
    assert "28N" in checks[0].message


def test_larger_series_uses_larger_fasteners():
    assert (
        EXTRUSION_SERIES["4040-slots"]["bolt_clearance_mm"]
        > EXTRUSION_SERIES["2020-slots"]["bolt_clearance_mm"]
    )


# ---------------------------------------------------------------------------
# Defect 4: an indentation slip put the centre-hole prompt inside the
# host-mount block, so passing --host-mount on the command line skipped it and
# silently left the centre hole at 0.
# ---------------------------------------------------------------------------


def _run_prompts(host_mount_preset):
    args = argparse.Namespace(
        mount="nema17",
        host_mount=host_mount_preset,
        host_slot_dir="parallel",
        plate_width=None,
        plate_width_mm=None,
        bolt_count=None,
        bolt_circle_dia_mm=None,
        bolt_hole_dia_mm=None,
        center_hole_dia_mm=None,
        material="aluminum_6061",
        process=None,
        density_kg_m3=None,
        youngs_modulus_gpa=None,
        yield_mpa=None,
        load_type="radial",
        shaft_load_n=5.0,
        plate_load_n=0.0,
    )
    asked = []

    def fake_prompt(*a, **k):
        asked.append(a[0])
        return k.get("default", "none")

    def fake_float(*a, **k):
        asked.append(a[0])
        return 22.0

    with mock.patch.object(cli.Prompt, "ask", side_effect=fake_prompt), mock.patch.object(
        cli.FloatPrompt, "ask", side_effect=fake_float
    ), mock.patch.object(cli.console, "print"):
        cli._prompt_for_missing(args)
    return asked, args


@pytest.mark.parametrize(
    "preset", [None, "none", "2020-slots", "4040-slots", "corner-holes"]
)
def test_centre_hole_is_always_prompted(preset):
    """Whatever --host-mount is set to, the centre hole must still be asked
    for. It used to be skipped for every non-None preset."""
    asked, args = _run_prompts(preset)
    assert any("Center hole" in q for q in asked), (
        f"centre-hole prompt skipped for --host-mount={preset}"
    )
    assert args.center_hole_dia_mm == 22.0


def test_centre_hole_not_silently_zero_with_host_mount():
    """The exact failure: a value of 0 that nobody chose."""
    _, args = _run_prompts("2020-slots")
    assert args.center_hole_dia_mm != 0
