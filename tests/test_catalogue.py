"""Catalogue loading and its validation guards.

Every guard here exists because the corresponding mistake actually shipped.
Each test feeds the loader a file with that mistake in it and requires a
rejection -- a validator that has never been shown to reject anything is the
same as no validator.
"""

import textwrap

import pytest

from zoomounter import catalogue
from zoomounter.bearings import BEARINGS
from zoomounter.catalogue import CatalogueError, expand_bolt_pattern
from zoomounter.mount_specs import MOUNTS


@pytest.fixture
def catalogue_dir(tmp_path, monkeypatch):
    """Point the loader at a scratch directory so bad files can be tried."""
    monkeypatch.setattr(catalogue, "DATA_DIR", tmp_path)
    return tmp_path


def _bearing(**over):
    row = {
        "designation": "TEST1", "kind": "radial", "bore_mm": 8, "od_mm": 22,
        "width_mm": 7, "static_c0_n": 1370, "dynamic_c_n": 3450,
        "source": "test catalogue",
    }
    row.update(over)
    body = "\n".join(
        f'{k} = {v!r}' if isinstance(v, str) else f"{k} = {v}"
        for k, v in row.items() if v is not None
    )
    return "[[bearing]]\n" + body.replace("'", '"') + "\n"


def _write(d, name, text):
    (d / name).write_text(textwrap.dedent(text), encoding="utf-8")


# ---------------------------------------------------------------------------
# The real files load and stay consistent.
# ---------------------------------------------------------------------------


def test_shipped_catalogue_loads():
    assert len(catalogue.load_mounts()) == len(MOUNTS)
    assert len(catalogue.load_bearings()) == len(BEARINGS)


def test_every_shipped_bearing_row_has_a_source():
    assert all(r["source"] for r in catalogue.load_bearings())


def test_every_shipped_limit_has_a_citation():
    for row in catalogue.load_mounts():
        if row.get("max_axial_n") is not None or row.get("max_radial_n") is not None:
            assert row.get("load_limit_source"), f"{row['key']} publishes an uncited limit"


# ---------------------------------------------------------------------------
# Bearing guards.
# ---------------------------------------------------------------------------


def test_missing_source_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", _bearing(source=None))
    with pytest.raises(CatalogueError, match="source"):
        catalogue.load_bearings()


def test_bore_outside_od_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", _bearing(bore_mm=30, od_mm=22))
    with pytest.raises(CatalogueError, match="not inside OD"):
        catalogue.load_bearings()


def test_thrust_row_with_static_below_dynamic_is_rejected(catalogue_dir):
    """How a unit mix-up presents, and exactly the shape of F5-10M's
    published figures -- 950N dynamic against 830N static."""
    _write(catalogue_dir, "bearings.toml",
           _bearing(designation="F5-10M", kind="thrust", bore_mm=5, od_mm=10,
                    width_mm=4, static_c0_n=830, dynamic_c_n=950))
    with pytest.raises(CatalogueError, match="unit mix-up"):
        catalogue.load_bearings()


def test_the_same_row_is_accepted_as_radial(catalogue_dir):
    """Guards the guard: the rejection must be about the thrust invariant,
    not about those two numbers in general."""
    _write(catalogue_dir, "bearings.toml",
           _bearing(kind="radial", static_c0_n=830, dynamic_c_n=950))
    assert len(catalogue.load_bearings()) == 1


def test_duplicate_designation_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", _bearing() + _bearing())
    with pytest.raises(CatalogueError, match="duplicate"):
        catalogue.load_bearings()


def test_unknown_kind_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", _bearing(kind="magnetic"))
    with pytest.raises(CatalogueError, match="kind"):
        catalogue.load_bearings()


def test_negative_dimension_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", _bearing(width_mm=-7))
    with pytest.raises(CatalogueError, match="positive"):
        catalogue.load_bearings()


def test_empty_file_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", "")
    with pytest.raises(CatalogueError, match="no \\[\\[bearing\\]\\]"):
        catalogue.load_bearings()


def test_broken_toml_is_rejected(catalogue_dir):
    _write(catalogue_dir, "bearings.toml", "[[bearing]]\ndesignation = ")
    with pytest.raises(CatalogueError, match="not valid TOML"):
        catalogue.load_bearings()


def test_missing_file_is_rejected(catalogue_dir):
    with pytest.raises(CatalogueError, match="missing"):
        catalogue.load_bearings()


# ---------------------------------------------------------------------------
# Mount guards.
# ---------------------------------------------------------------------------


def _mount(extra_fields: str = "", pattern: str = 'type = "square"\nspacing_mm = 31.0'):
    """Build a mount file.

    `extra_fields` goes before the [mount.bolt_pattern] header on purpose:
    appending after it puts the key inside that sub-table instead of on the
    mount, which is a TOML scoping trap that made an earlier version of this
    test pass a file the validator was right to accept."""
    body = [
        "[[mount]]",
        'key = "t1"',
        'name = "Test mount"',
        'kind = "motor"',
        "plate_width_mm = 42.3",
        "plate_height_mm = 42.3",
        "bolt_hole_dia_mm = 3.4",
    ]
    if extra_fields:
        body.append(extra_fields)
    if pattern:
        body += ["", "[mount.bolt_pattern]", pattern]
    return "\n".join(body) + "\n"


def test_valid_mount_loads(catalogue_dir):
    _write(catalogue_dir, "mounts.toml", _mount())
    assert len(catalogue.load_mounts()) == 1


def test_limit_without_a_citation_is_rejected(catalogue_dir):
    """The circular-provenance bug in its purest form: a number that looks
    authoritative and answers to nothing."""
    _write(catalogue_dir, "mounts.toml", _mount("max_axial_n = 10"))
    with pytest.raises(CatalogueError, match="load_limit_source"):
        catalogue.load_mounts()


def test_a_cited_limit_is_accepted(catalogue_dir):
    """Guards the guard: the rejection is about the missing citation, not
    about publishing a limit at all."""
    _write(catalogue_dir, "mounts.toml",
           _mount('max_axial_n = 10\nload_limit_source = "ATO datasheet"'))
    assert len(catalogue.load_mounts()) == 1


def test_mount_without_a_bolt_pattern_is_rejected(catalogue_dir):
    _write(catalogue_dir, "mounts.toml", _mount(pattern=""))
    with pytest.raises(CatalogueError, match="bolt_pattern"):
        catalogue.load_mounts()


# ---------------------------------------------------------------------------
# Bolt patterns. The type is the guard against the original bug.
# ---------------------------------------------------------------------------


def test_square_and_circular_are_not_interchangeable():
    """31mm as a square spacing and as a bolt circle give different holes --
    6.4mm apart on a NEMA 17, which is the bug that shipped. Declaring the
    type makes them unmixable rather than merely tested for."""
    sq = expand_bolt_pattern({"type": "square", "spacing_mm": 31.0}, "t")
    ci = expand_bolt_pattern({"type": "circular", "count": 4, "circle_dia_mm": 31.0}, "t")
    assert set(sq) != set(ci)
    sq_r = (sq[0][0] ** 2 + sq[0][1] ** 2) ** 0.5
    ci_r = (ci[0][0] ** 2 + ci[0][1] ** 2) ** 0.5
    assert abs(sq_r - ci_r) == pytest.approx(6.4, abs=0.05)


def test_rectangular_keeps_both_dimensions():
    pos = expand_bolt_pattern(
        {"type": "rectangular", "x_spacing_mm": 58.0, "y_spacing_mm": 49.0}, "t"
    )
    assert {abs(x) for x, _ in pos} == {29.0}
    assert {abs(y) for _, y in pos} == {24.5}


def test_unknown_pattern_type_is_rejected():
    with pytest.raises(CatalogueError, match="bolt_pattern.type"):
        expand_bolt_pattern({"type": "hexagonal", "spacing_mm": 31.0}, "t")


def test_pattern_missing_its_field_is_rejected():
    with pytest.raises(CatalogueError, match="needs field"):
        expand_bolt_pattern({"type": "square"}, "t")


def test_nema_patterns_still_land_on_the_corners():
    """The end-to-end assertion the original bug would have failed."""
    for key, spacing in (("nema17", 31.0), ("nema23", 47.14)):
        for x, y in MOUNTS[key].hole_positions:
            assert abs(abs(x) - spacing / 2) < 1e-6
            assert abs(abs(y) - spacing / 2) < 1e-6
