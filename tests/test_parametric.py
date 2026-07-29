"""Tests for the parametric prompt and the KCL inspection behind it.

Runs offline. The negative fixture is real: probes/item5/2020-slots/main.kcl
was generated from the old literal prompt, so it is the actual output this
redesign exists to improve on -- not a mock of it.
"""

from pathlib import Path

import pytest

from zoomounter import generate
from zoomounter.kcl_inspect import check_parametric, inspect_kcl
from zoomounter.materials import get_material
from zoomounter.mount_specs import apply_host_mount, get_mount

ALUMINIUM = get_material("aluminum_6061")
THICKNESS = 1.05

REPO = Path(__file__).parent.parent
LITERAL_KCL = REPO / "probes" / "item5" / "2020-slots" / "main.kcl"


def _slotted_nema17():
    return apply_host_mount(get_mount("nema17"), "2020-slots")


# ---------------------------------------------------------------------------
# The measurement itself. If this cannot tell a relationship from a constant,
# every claim built on it is worthless.
# ---------------------------------------------------------------------------


def test_distinguishes_a_relationship_from_a_constant():
    model = inspect_kcl(
        "plateWidth = 80.0mm\n"
        "slotSpacing = 60.0mm\n"
        "edgeMargin = 10.0mm\n"
    )
    assert len(model.parameters) == 3
    assert len(model.derived) == 0
    assert model.derived_ratio == 0.0

    model = inspect_kcl(
        "slotSpacing = 60.0mm\n"
        "edgeMargin = 10.0mm\n"
        "plateWidth = slotSpacing + 2 * edgeMargin\n"
    )
    assert len(model.derived) == 1
    assert model.get("plateWidth").references == ("slotSpacing", "edgeMargin")


def test_forward_references_are_not_counted_as_relationships():
    """A name used before it is declared is not a dependency we can trust."""
    model = inspect_kcl(
        "plateWidth = slotSpacing + 2 * edgeMargin\n"
        "slotSpacing = 60.0mm\n"
        "edgeMargin = 10.0mm\n"
    )
    assert len(model.derived) == 0


def test_geometry_calls_are_not_parameters():
    model = inspect_kcl(
        "plateThickness = 6mm\n"
        "plateSketch = sketch(on = XY) {\n"
        "plateRegion = region(\n"
        "plateBlank = extrude(plateRegion, length = plateThickness)\n"
    )
    assert model.names() == {"plateThickness"}


def test_counts_constraints_and_construction_geometry():
    model = inspect_kcl(
        "a = line(start = [0mm, 0mm], end = [1mm, 0mm], construction = true)\n"
        "radius(leftArc) == slotRadius\n"
        "horizontalDistance([ORIGIN, leftArc.center]) == 5mm\n"
    )
    assert model.constraint_count == 2
    assert model.construction_count == 1


# ---------------------------------------------------------------------------
# The real literal-prompt output is the negative case.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LITERAL_KCL.exists(), reason="fixture not generated")
def test_the_old_literal_output_is_not_parametric():
    """This is the thing being fixed. The old prompt produced ten nicely
    named parameters and exactly one derived value -- cutLength, an internal
    extrude detail. None of the design intent survived."""
    model = inspect_kcl(LITERAL_KCL.read_text(encoding="utf-8"))
    assert len(model.parameters) >= 8, "expected a well-named but rigid model"
    assert len(model.derived) == 1
    assert model.get("cutLength") is not None
    # The relationships that matter are all absent.
    for name in ("slotSpacing", "edgeMargin", "boltSpacing"):
        assert model.get(name) is None


@pytest.mark.skipif(not LITERAL_KCL.exists(), reason="fixture not generated")
def test_check_parametric_fails_on_the_literal_output():
    """The check must reject the old output. A check that passes both the
    before and the after measures nothing."""
    _, scheme = generate.build_parametric_prompt(
        _slotted_nema17(), ALUMINIUM, THICKNESS
    )
    report = check_parametric(
        LITERAL_KCL.read_text(encoding="utf-8"), scheme.names, scheme.relations
    )
    assert not report.ok
    assert report.missing_names
    assert set(report.broken_relations) == set(scheme.relations)


# ---------------------------------------------------------------------------
# The prompt itself.
# ---------------------------------------------------------------------------


def test_scheme_expressions_evaluate_to_the_literal_dimensions():
    """The redesign must not move the part. Every derived expression has to
    resolve to exactly the number the literal prompt would have stated,
    otherwise editability was bought with dimensional drift."""
    spec = _slotted_nema17()
    scheme = generate.build_parameter_scheme(spec, THICKNESS)

    env: dict[str, float] = {}
    for name, expr in scheme.declarations:
        env[name] = eval(expr.replace("mm", ""), {"__builtins__": {}}, env)

    assert env["plateWidth"] == pytest.approx(spec.plate_width_mm)
    assert env["plateThickness"] == pytest.approx(THICKNESS)
    assert env["plateHeight"] == pytest.approx(spec.plate_height_mm)
    assert env["boltHoleOffset"] == pytest.approx(abs(spec.hole_positions[0][0]))
    assert env["slotCenterOffset"] == pytest.approx(abs(spec.host_slots[0][0]))
    assert env["centerHoleDia"] == pytest.approx(spec.center_hole_dia_mm)


def test_prompt_states_relationships_not_precomputed_numbers():
    prompt, scheme = generate.build_parametric_prompt(
        _slotted_nema17(), ALUMINIUM, THICKNESS
    )
    assert "plateWidth       = slotSpacing + 2 * edgeMargin" in prompt
    assert "slotCenterOffset = slotSpacing / 2" in prompt
    assert "do not replace it with a pre-computed number" in prompt
    # The plate width must not appear as a bare literal anywhere.
    assert "80mm wide" not in prompt and "80.0mm wide" not in prompt
    assert scheme.relations["plateWidth"] == ["slotSpacing", "edgeMargin"]


def test_square_bolt_pattern_becomes_one_parameter():
    scheme = generate.build_parameter_scheme(get_mount("nema17"), THICKNESS)
    assert ("boltSpacing", "31mm") in scheme.declarations
    assert ("boltHoleOffset", "boltSpacing / 2") in scheme.declarations


def test_non_square_pattern_falls_back_to_coordinates():
    """A Raspberry Pi's holes are 58 x 49mm -- not derivable from a single
    spacing, so inventing one would be wrong. It must degrade to explicit
    coordinates rather than fabricate a relationship."""
    scheme = generate.build_parameter_scheme(get_mount("raspberry_pi"), THICKNESS)
    assert scheme.relations == {}
    prompt, _ = generate.build_parametric_prompt(
        get_mount("raspberry_pi"), ALUMINIUM, THICKNESS
    )
    assert "(-29mm, -24.5mm)" in prompt


def test_mount_without_slots_keeps_plate_width_literal():
    scheme = generate.build_parameter_scheme(get_mount("nema17"), THICKNESS)
    assert ("plateWidth", "42.3mm") in scheme.declarations
    assert "plateWidth" not in scheme.relations


def test_no_float_noise_reaches_the_prompt():
    for name in ("nema17", "nema23", "bearing_608", "raspberry_pi", "vesa_75"):
        for option in ("none", "2020-slots", "4040-slots"):
            spec = apply_host_mount(get_mount(name), option)
            prompt, _ = generate.build_parametric_prompt(spec, ALUMINIUM, 2.3456789)
            assert "0000000" not in prompt and "9999999" not in prompt


def test_every_builtin_mount_produces_a_usable_prompt():
    for name in ("nema17", "nema23", "bearing_608", "raspberry_pi", "vesa_75"):
        for option in ("none", "2020-slots", "4040-slots", "corner-holes"):
            spec = apply_host_mount(get_mount(name), option)
            prompt, scheme = generate.build_parametric_prompt(spec, ALUMINIUM, 3.0)
            assert "plateWidth" in scheme.names
            assert "plateThickness" in scheme.names
            assert prompt.strip().endswith("full thickness of the plate.")
            # Anything declared as an expression must be listed as a relation.
            for pname, expr in scheme.declarations:
                if not expr[0].isdigit() and not expr[0] == "-":
                    assert pname in scheme.relations
