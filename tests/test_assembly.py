"""Assembly composition and mounting face.

Offline. The KCL emitted here is hand-authored rather than generated, so it
cannot be checked against a spec the way the mount is -- these tests check
the arithmetic that places parts, and that nothing is drawn from a dimension
the catalogue does not have.
"""

import pytest

from zoomounter import assembly
from zoomounter.assembly import (
    FACE_BACK,
    FACE_FRONT,
    bearing_kcl,
    component_kcl,
    main_kcl,
    write_assembly,
)
from zoomounter.bearings import BY_DESIGNATION, bearing_block
from zoomounter.mount_specs import MOUNTS, apply_host_mount, get_mount

THICK = 4.0


def _nums(kcl, marker):
    """Every number on the line after a marker, for checking placement."""
    import re

    for line in kcl.splitlines():
        if marker in line:
            return [float(x) for x in re.findall(r"-?\d+\.?\d*", line)]
    return []


# ---------------------------------------------------------------------------
# The component must be drawn from the component, not from the bracket.
# ---------------------------------------------------------------------------


def test_component_uses_the_base_mount_not_the_widened_one():
    """apply_host_mount widens plate_width_mm to carry the slots. Drawing the
    body from that gave a NEMA 17 an 80mm-wide motor -- the component silently
    inheriting the bracket's size."""
    base = get_mount("nema17")
    widened = apply_host_mount(base, "2020-slots")
    assert widened.plate_width_mm > base.plate_width_mm

    kcl = component_kcl(base, THICK, FACE_FRONT)
    half = base.plate_width_mm / 2
    assert f"{half:g}mm" in kcl
    assert f"{widened.plate_width_mm / 2:g}mm" not in kcl


def test_write_assembly_requires_the_base_to_size_the_component(tmp_path):
    base = get_mount("nema17")
    widened = apply_host_mount(base, "2020-slots")
    _, parts = write_assembly(tmp_path, widened, THICK, "// mount",
                              base_mount=base)
    body = next(p for p in parts if p.role == "component")
    assert f"{base.plate_width_mm / 2:g}mm" in body.kcl


# ---------------------------------------------------------------------------
# Mounting face.
# ---------------------------------------------------------------------------


def test_front_and_back_put_the_body_on_opposite_sides():
    base = get_mount("nema17")
    front = _nums(component_kcl(base, THICK, FACE_FRONT), "motorBodyPlane")
    back = _nums(component_kcl(base, THICK, FACE_BACK), "motorBodyPlane")
    assert front[-1] == pytest.approx(THICK / 2)
    assert back[-1] == pytest.approx(-THICK / 2)


def _extrude_length(kcl, region):
    """The length given to extrude() for a named region.

    Matched by regex rather than by splitting on the region name -- the name
    appears twice, once defining the region and once in the extrude, so a
    split lands between them."""
    import re

    m = re.search(rf"extrude\({region},\s*length\s*=\s*(-?\d+\.?\d*)\)", kcl)
    assert m, f"no extrude found for {region}"
    return float(m.group(1))


def test_the_body_extends_away_from_the_plate_on_both_faces():
    """A sign error here buries the motor inside the plate instead of
    standing it on the face."""
    base = get_mount("nema17")
    body_len = base.body_cg_offset_mm * 2
    for face, expect in ((FACE_FRONT, body_len), (FACE_BACK, -body_len)):
        kcl = component_kcl(base, THICK, face)
        assert _extrude_length(kcl, "motorBodyRegion") == pytest.approx(expect)


def test_the_shaft_runs_through_the_plate_opposite_the_body():
    """The shaft has to come out the far side, so its extrude is signed
    against the body's."""
    base = get_mount("nema17")
    for face in (FACE_FRONT, FACE_BACK):
        kcl = component_kcl(base, THICK, face)
        body = _extrude_length(kcl, "motorBodyRegion")
        shaft = _extrude_length(kcl, "shaftRegion")
        assert body * shaft < 0, "shaft and body must extend opposite ways"
        assert abs(shaft) == pytest.approx(THICK + assembly.SHAFT_STUB_MM)


def test_unknown_face_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="mounting face"):
        write_assembly(tmp_path, get_mount("nema17"), THICK, "// mount", face="sideways")


# ---------------------------------------------------------------------------
# What gets drawn, and what deliberately does not.
# ---------------------------------------------------------------------------


def test_a_motor_gets_a_body_a_boss_and_a_shaft():
    kcl = component_kcl(get_mount("nema17"), THICK, FACE_FRONT)
    assert "motorBody" in kcl and "pilotBoss" in kcl and "shaft" in kcl
    assert "diameter(profile) == 5mm" in kcl  # the shaft, not the 22mm bore


def test_a_flange_has_no_component_body():
    """vesa_75 is a bracket, not a component. Inventing a body for it would
    put geometry on screen that answers to no data."""
    assert component_kcl(get_mount("vesa_75"), THICK, FACE_FRONT) is None


def test_a_board_gets_a_pcb_not_a_motor():
    kcl = component_kcl(get_mount("raspberry_pi"), THICK, FACE_FRONT)
    assert kcl is not None
    assert "board" in kcl and "motorBody" not in kcl and "shaft" not in kcl


def test_assembly_omits_the_bearing_when_there_is_none(tmp_path):
    _, parts = write_assembly(tmp_path, get_mount("nema17"), THICK, "// mount")
    assert {p.role for p in parts} == {"mount", "component"}


def test_assembly_includes_the_bearing_when_there_is_one(tmp_path):
    mount = bearing_block(BY_DESIGNATION["608"], "radial")
    _, parts = write_assembly(tmp_path, mount, 7.0, "// mount",
                              bearing=BY_DESIGNATION["608"])
    assert "bearing" in {p.role for p in parts}


# ---------------------------------------------------------------------------
# Bearing placement follows the seat.
# ---------------------------------------------------------------------------


def test_thrust_bearing_sits_down_in_its_counterbore():
    b = BY_DESIGNATION["51101"]
    mount = bearing_block(b, "axial")
    t = 11.0
    z = _nums(bearing_kcl(b, mount, t), "bearingOuterPlane")[-1]
    assert z == pytest.approx(t / 2 - b.width_mm)


def test_radial_bearing_sits_centred_in_its_through_bore():
    b = BY_DESIGNATION["608"]
    mount = bearing_block(b, "radial")
    t = 7.0
    z = _nums(bearing_kcl(b, mount, t), "bearingOuterPlane")[-1]
    assert z == pytest.approx(-t / 2 + (t - b.width_mm) / 2)


def test_exploding_moves_parts_apart_without_changing_the_mount(tmp_path):
    base = get_mount("nema17")
    _, tight = write_assembly(tmp_path / "a", base, THICK, "// mount", base_mount=base)
    _, apart = write_assembly(tmp_path / "b", base, THICK, "// mount",
                              base_mount=base, explode_mm=25.0)
    tight_body = next(p for p in tight if p.role == "component").kcl
    apart_body = next(p for p in apart if p.role == "component").kcl
    assert _nums(apart_body, "motorBodyPlane")[-1] > _nums(tight_body, "motorBodyPlane")[-1]
    # The mount is byte-identical -- exploding is a view, not a change to the
    # part being verified.
    assert next(p for p in tight if p.role == "mount").kcl == \
           next(p for p in apart if p.role == "mount").kcl


# ---------------------------------------------------------------------------
# The assembly file.
# ---------------------------------------------------------------------------


def test_main_imports_every_part_and_nothing_else(tmp_path):
    mount = bearing_block(BY_DESIGNATION["608"], "radial")
    main_path, parts = write_assembly(tmp_path, mount, 7.0, "// mount",
                                      bearing=BY_DESIGNATION["608"])
    text = main_path.read_text(encoding="utf-8")
    for p in parts:
        assert f'import "{p.filename}" as {p.name}' in text
        assert (tmp_path / p.filename).exists()
    # No transforms: each part places itself, so there is nothing here to get
    # wrong in a language whose transform semantics we would be guessing at.
    assert "translate" not in text and "rotate" not in text


def test_every_part_file_declares_units():
    """A KCL file without @settings inherits a default that may not be mm,
    which would silently scale reference geometry."""
    base = get_mount("nema17")
    for kcl in (component_kcl(base, THICK, FACE_FRONT),
                bearing_kcl(BY_DESIGNATION["608"],
                            bearing_block(BY_DESIGNATION["608"], "radial"), 7.0),
                main_kcl([])):
        assert "defaultLengthUnit = mm" in kcl


@pytest.mark.parametrize("key", sorted(MOUNTS))
def test_every_catalogue_mount_assembles(tmp_path, key):
    write_assembly(tmp_path / key, get_mount(key), THICK, "// mount")
