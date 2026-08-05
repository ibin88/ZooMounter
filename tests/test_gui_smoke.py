"""Does the window actually open, and do its controls actually work?

This file exists because it should have existed sooner. Removing the
"Integrate bearing" checkbox left a reference to it in `_on_mount_change`, so
`python -m zoomounter.gui` crashed on startup with

    AttributeError: '_tkinter.tkapp' object has no attribute 'integrate_bearing_cb'

with 222 other tests passing. Every one of them exercised the domain layer and
not one of them constructed the window, so a dead widget reference was
invisible to the suite and instant to a user.

These tests build the real App and drive its callbacks the way a click does.
They are deliberately shallow -- no assertions about layout or appearance,
which would be brittle and would not have caught this. What they check is that
the code paths a user takes in the first ten seconds do not raise.

Skipped when there is no display to build a window on.
"""

import pytest

ctk = pytest.importorskip("customtkinter", reason="GUI extra not installed")
tkinter = pytest.importorskip("tkinter")

from zoomounter.bearings import BEARING_TOPOLOGIES  # noqa: E402
from zoomounter.gui import TOPOLOGY_NONE, App  # noqa: E402
from zoomounter.mount_specs import MOUNTS  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    """One window for the whole module.

    Deliberately module-scoped. Building and tearing down a Tk root per test
    made these skip intermittently -- roughly two of seventeen, and only when
    the full suite ran ahead of them, which is the worst kind of flake: it
    looks like a display problem and is actually resource churn.
    """
    try:
        instance = App()
    except tkinter.TclError as e:  # pragma: no cover -- headless machine
        pytest.skip(f"no display available: {e}")
    instance.withdraw()  # build it, don't show it
    try:
        yield instance
    finally:
        instance.destroy()


@pytest.fixture
def app(_app):
    """The shared window, returned to its startup state.

    Sharing a window means one test's selections leak into the next unless
    something puts them back. Resetting through the same _on_*_change handlers
    the widgets call keeps the reset honest -- if a handler breaks, this breaks
    with it rather than papering over it.
    """
    _app.mount_var.set("nema17")
    _app.material_var.set("aluminum_6061")
    _app.topology_var.set(TOPOLOGY_NONE)
    _app.host_mount_var.set("none")
    _app.mounting_face_var.set("front")
    _app.load_var.set("5")
    _app.plate_load_var.set("0")
    _app.safety_var.set("2.0")
    _app.overhang_var.set("")
    _app.bearing_var.set("auto")
    _app._on_mount_change("nema17")
    _app._on_material_change("aluminum_6061")
    _app._on_topology_change(TOPOLOGY_NONE)
    _app._on_host_mount_change("none")
    return _app


def test_the_window_builds(app):
    """The regression. Constructing App() ran _on_mount_change, which called a
    widget that no longer existed."""
    assert app.title() == "ZooMounter"


@pytest.mark.parametrize("mount_key", [*MOUNTS, "bearing", "custom"])
def test_every_mount_type_can_be_selected(app, mount_key):
    """_on_mount_change shows and hides several panels. A stale reference in
    any branch crashes the app for that selection only, which is exactly how
    this one hid."""
    app.mount_var.set(mount_key)
    app._on_mount_change(mount_key)


@pytest.mark.parametrize("topology", [TOPOLOGY_NONE, *BEARING_TOPOLOGIES])
def test_every_topology_can_be_selected(app, topology):
    app.topology_var.set(topology)
    app._on_topology_change(topology)
    assert app.topology_help.cget("text"), "each topology needs its help text"


@pytest.mark.parametrize(
    "host_mount", ["none", "2020-slots", "4040-slots", "corner-holes"]
)
def test_every_host_mount_can_be_selected(app, host_mount):
    app.host_mount_var.set(host_mount)
    app._on_host_mount_change(host_mount)


def test_the_topology_picker_replaced_the_checkbox(app):
    """The old checkbox is gone, not merely hidden. A leftover attribute would
    mean two controls claiming to own the same decision."""
    assert not hasattr(app, "integrate_bearing_var")
    assert not hasattr(app, "integrate_bearing_cb")
    assert app.topology_var.get() == TOPOLOGY_NONE


def test_resolving_a_spec_from_the_default_form(app):
    """The default form must produce a usable spec without the user touching
    anything -- this is the path 'Preview' takes."""
    mount, material, load_n, sf, thickness, decision = app._resolve_spec()
    assert thickness.required_thickness_mm > 0
    assert decision is not None and decision.verdict


@pytest.mark.parametrize("topology", list(BEARING_TOPOLOGIES))
def test_resolving_a_spec_with_each_topology(app, topology):
    """Both topologies have to survive the full GUI resolution path, including
    bearing selection and the host-mount transform after it."""
    app.topology_var.set(topology)
    app.shaft_dia_var.set("5")
    mount, _material, _load, _sf, thickness, decision = app._resolve_spec()
    assert mount.bearing_topology == topology
    assert thickness.required_thickness_mm > 0
    assert any(c.code for c in decision.checks)


# ---------------------------------------------------------------------------
# The form has to fit, and its inputs have to mean what they say.
# ---------------------------------------------------------------------------


def test_the_form_scrolls(app):
    """The form is taller than the window and grows as options appear. In a
    plain frame the load fields and the Generate button went off the bottom
    edge with no way to reach them."""
    assert isinstance(app._form, ctk.CTkScrollableFrame)


def test_shaft_diameter_follows_the_selected_mount(app):
    """It defaulted to 8mm for everything. Selecting a NEMA 17 -- a 5mm shaft
    -- and asking for a bearing therefore sized one for 8mm, picked a 608, and
    reported an 11mm plate. Wrong three steps before it was visible."""
    for key, spec in MOUNTS.items():
        if not spec.shaft_dia_mm:
            continue
        app.mount_var.set(key)
        app._on_mount_change(key)
        assert float(app.shaft_dia_var.get()) == spec.shaft_dia_mm


def test_direct_topology_selects_for_the_motors_shaft_not_the_field(app):
    """Direct runs on the MOTOR's shaft, so the picker field is not a free
    choice there. Typing 8 against a NEMA 17 must not pick an 8mm bearing."""
    app.mount_var.set("nema17")
    app._on_mount_change("nema17")
    app.topology_var.set("direct")
    app.shaft_dia_var.set("8")  # deliberately wrong for this motor
    mount, *_ = app._resolve_spec()
    assert mount.bearing_bore_mm == MOUNTS["nema17"].shaft_dia_mm


# ---------------------------------------------------------------------------
# The schematic has to answer to the controls above it.
# ---------------------------------------------------------------------------


def _schematic_geometry(app):
    """Every drawn coordinate, as a flat tuple -- enough to tell whether two
    renders differ without asserting anything about how it looks."""
    canvas = app.schematic_canvas
    return tuple(
        tuple(canvas.coords(item)) for item in canvas.find_all()
    )


def test_the_mounting_face_changes_the_schematic(app):
    """The face control redrew the canvas and changed nothing: the motor body
    was always drawn on +Z. The control looked live and was inert.

    The mount GEOMETRY legitimately does not change with the face -- only the
    assembly does -- but a picture that ignores the setting is describing a
    different build to the one that will be generated."""
    app.mounting_face_var.set("front")
    front = _schematic_geometry_after(app)
    app.mounting_face_var.set("back")
    back = _schematic_geometry_after(app)
    assert front, "schematic drew nothing at all"
    assert front != back, (
        "front and back produced an identical schematic -- the mounting face "
        "control is not reaching the drawing"
    )


def _schematic_geometry_after(app):
    app.host_mount_var.set("2020-slots")
    app._on_host_mount_change("2020-slots")
    app._draw_schematic()
    app.update_idletasks()
    return _schematic_geometry(app)


def test_the_stub_shaft_standoff_reaches_the_schematic(app):
    """The standoff is real geometry -- the motor genuinely sits 30mm off the
    plate -- so a schematic that omits it is drawing the direct topology while
    the report describes the stub-shaft one."""
    app.mount_var.set("nema17")
    app._on_mount_change("nema17")
    app.topology_var.set("none")
    plain = _schematic_geometry_after(app)
    app.topology_var.set("stub-shaft")
    app._on_topology_change("stub-shaft")
    stubbed = _schematic_geometry_after(app)
    assert plain != stubbed
