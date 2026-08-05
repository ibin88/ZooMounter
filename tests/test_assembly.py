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
from zoomounter.mount_specs import (
    MOUNTS,
    MountSpec,
    apply_host_mount,
    get_mount,
    rectangular_bolt_pattern,
    square_bolt_pattern,
)

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


# These three tests replace an earlier set that encoded the wrong meaning of
# the mounting face. That version asserted the body moved to the other SIDE of
# the plate and the shaft's extrude flipped sign with it -- which is not a
# physical choice at all. Putting the motor below the plate rather than above
# it is the same assembly viewed from underneath, so the control mirrored the
# picture and changed nothing about the build. The old assertions passed the
# whole time and were describing the defect.
#
# What the face actually selects is WHICH END of the motor is bolted down, and
# the observable consequence is where the shaft goes.


def test_the_body_sits_on_the_plate_whichever_face_is_bolted():
    """The motor is fastened to the plate, so it is on the same side of it
    either way. Only which of its own ends is against the plate changes."""
    base = get_mount("nema17")
    front = _nums(component_kcl(base, THICK, FACE_FRONT), "motorBodyPlane")
    back = _nums(component_kcl(base, THICK, FACE_BACK), "motorBodyPlane")
    assert front[-1] == pytest.approx(THICK / 2)
    assert back[-1] == pytest.approx(THICK / 2)


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
    for face in (FACE_FRONT, FACE_BACK):
        kcl = component_kcl(base, THICK, face)
        assert _extrude_length(kcl, "motorBodyRegion") == pytest.approx(body_len)


def _shaft_span(kcl):
    start = _nums(kcl, "shaftPlane")[-1]
    return start, start + _extrude_length(kcl, "shaftRegion")


def test_a_front_mounted_shaft_passes_through_the_plate():
    """Bolting by the shaft-end faceplate is what puts the shaft through the
    plate -- and is why a NEMA plate has a pilot bore at all."""
    base = get_mount("nema17")
    _start, end = _shaft_span(component_kcl(base, THICK, FACE_FRONT))
    assert end < -THICK / 2, "front-mounted shaft must come out the far side"


def test_a_rear_mounted_shaft_never_touches_the_plate():
    """Bolting by the rear face points the shaft the other way. It leaves the
    far end of the motor and runs away from the plate, so the plate needs no
    shaft clearance at all.

    This is the distinction the old tests missed: with the wrong model, `back`
    still ran the shaft through the plate and merely drew the whole thing
    upside down."""
    base = get_mount("nema17")
    body_len = base.body_cg_offset_mm * 2
    start, end = _shaft_span(component_kcl(base, THICK, FACE_BACK))
    assert start == pytest.approx(THICK / 2 + body_len), (
        "the shaft must leave the motor's far end, not the bolted end"
    )
    assert end > start, "it must run away from the plate"
    assert min(start, end) > THICK / 2, "it must never enter the plate"


def test_the_two_faces_put_the_shaft_in_genuinely_different_places():
    base = get_mount("nema17")
    front = _shaft_span(component_kcl(base, THICK, FACE_FRONT))
    back = _shaft_span(component_kcl(base, THICK, FACE_BACK))
    assert front != back
    # Not a mirror: front spans the plate, back sits entirely beyond the motor.
    assert front[1] < 0 < back[0]


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
    """A flange is a bracket, not a component. Inventing a body for it would
    put geometry on screen that answers to no data.

    Built here rather than taken from the catalogue: the catalogue ships no
    flange row, because ZooMounter's scope is parts governed by a shaft load.
    The branch still has to behave, so the test supplies its own spec."""
    flange = MountSpec(
        name="Bare flange",
        kind="flange",
        plate_width_mm=90,
        plate_height_mm=90,
        bolt_hole_dia_mm=4.3,
        hole_positions=square_bolt_pattern(75),
    )
    assert component_kcl(flange, THICK, FACE_FRONT) is None


def test_a_board_gets_a_pcb_not_a_motor():
    """Same reasoning as the flange above -- no board ships, but a board must
    not be drawn as a motor if one is ever supplied."""
    board = MountSpec(
        name="Generic board",
        kind="board",
        plate_width_mm=65,
        plate_height_mm=56,
        bolt_hole_dia_mm=2.7,
        hole_positions=rectangular_bolt_pattern(58, 49),
    )
    kcl = component_kcl(board, THICK, FACE_FRONT)
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
