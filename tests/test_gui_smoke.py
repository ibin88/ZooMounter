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


@pytest.fixture
def app():
    try:
        instance = App()
    except tkinter.TclError as e:  # pragma: no cover -- headless machine
        pytest.skip(f"no display available: {e}")
    instance.withdraw()  # build it, don't show it
    try:
        yield instance
    finally:
        instance.destroy()


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
