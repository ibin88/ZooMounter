"""The application decides the geometry, so the user does not pick it.

Offline. These tests exist because the tool used to hand the user a choice
between a stub-shaft and a direct bearing, and briefly asked whether to make
one plate or two. Both were the same error: the situation determines the
answer, and a preference cannot override it.
"""

import pytest

from zoomounter import application, mechanics, rules
from zoomounter.application import (
    CLASS_HOUSING,
    CLASS_OPEN_PLATE,
    SERVICE_FIXED,
    SERVICE_MOVING,
    WORKSPACE_CLEAR,
    WORKSPACE_SHARED,
    ApplicationContext,
    recommend_mount_class,
    recommend_topology,
)
from zoomounter.bearings import BY_DESIGNATION, TOPOLOGY_STUB_SHAFT
from zoomounter.mount_specs import get_mount

N17 = get_mount("nema17")
B625 = BY_DESIGNATION["625"]  # 5mm bore, 16mm OD -- matches the NEMA 17 shaft


def _ctx(service=SERVICE_FIXED, workspace=WORKSPACE_CLEAR):
    return ApplicationContext(service=service, workspace=workspace)


# ---------------------------------------------------------------------------
# Mount class.
# ---------------------------------------------------------------------------


def test_a_fixed_mount_in_a_clear_space_is_a_plate():
    rec = recommend_mount_class(_ctx())
    assert rec.value == CLASS_OPEN_PLATE
    assert rec.in_scope
    assert rec.checks == []


def test_a_moving_mount_in_a_shared_workspace_is_out_of_scope():
    """The case that prompted this: a motor riding an axis where people or
    workpieces can reach it needs containment, and containment is not a plate
    with holes in it. Handing back an open bracket would be the tool answering
    a question it was not asked."""
    rec = recommend_mount_class(_ctx(SERVICE_MOVING, WORKSPACE_SHARED))
    assert rec.value == CLASS_HOUSING
    assert not rec.in_scope
    loud = [c for c in rec.checks if c.level == "LOUD WARN"]
    assert loud and "cannot design this part" in loud[0].message
    assert loud[0].remedy


def test_a_moving_mount_is_warned_about_loads_it_does_not_model():
    """Acceleration and cable drag are real and unmodelled. Silence about them
    is indistinguishable from having checked."""
    rec = recommend_mount_class(_ctx(SERVICE_MOVING))
    assert any(c.code == application.DYNAMIC_LOADS for c in rec.checks)


def test_a_fixed_mount_in_a_shared_space_is_still_buildable():
    """The plate is fine; what it carries is still exposed. That is a guarding
    problem, not a reason to refuse the bracket."""
    rec = recommend_mount_class(_ctx(workspace=WORKSPACE_SHARED))
    assert rec.value == CLASS_OPEN_PLATE
    assert rec.in_scope
    assert any("not the safety feature" in c.remedy for c in rec.checks)


@pytest.mark.parametrize(
    "kwargs", [{"service": "hovering"}, {"workspace": "outdoors"}]
)
def test_a_nonsense_context_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ApplicationContext(**kwargs)


# ---------------------------------------------------------------------------
# Topology derivation.
# ---------------------------------------------------------------------------


def test_the_topology_is_derived_not_asked_for():
    rec = recommend_topology(N17, B625, "radial")
    assert rec.value == TOPOLOGY_STUB_SHAFT
    assert rec.checks and rec.checks[0].message


def test_the_derivation_states_its_reason():
    """A conclusion whose derivation is invisible is indistinguishable from a
    guess -- which is this project's entire thesis, applied to a decision
    rather than to a number."""
    rec = recommend_topology(N17, B625, "radial")
    msg = rec.checks[0].message
    # 625 is 16mm across against a 22mm pilot boss: no material to grip.
    assert "16mm" in msg and "22mm" in msg


def test_an_axial_load_is_reason_enough_on_its_own():
    thrust = BY_DESIGNATION["F5-12M"]
    rec = recommend_topology(N17, thrust, "axial")
    assert "no shoulder" in rec.checks[0].message


def test_a_bore_mismatch_is_reason_enough_on_its_own():
    rec = recommend_topology(N17, BY_DESIGNATION["608"], "radial")
    assert "does not match" in rec.checks[0].message


def test_stub_shaft_is_chosen_even_when_direct_would_be_permissible():
    """Direct is never what the derivation picks. When all three constraints
    pass it is merely allowed, and the reasoning says so plainly rather than
    presenting the two as equivalent."""

    # A mount whose boss is smaller than the bearing and whose shaft matches
    # its bore -- every objection to `direct` removed.
    import dataclasses

    permissive = dataclasses.replace(N17, center_hole_dia_mm=8.0, shaft_dia_mm=5.0)
    rec = recommend_topology(permissive, B625, "radial")
    assert rec.value == TOPOLOGY_STUB_SHAFT
    assert "would be permissible" in rec.checks[0].message
    assert "overconstrained" in rec.checks[0].message


def test_choosing_direct_is_reported_as_an_override():
    checks = application.direct_override_checks(N17, B625, "radial")
    assert checks and checks[0].level == "WARN"
    assert "selected manually" in checks[0].message
    assert "not what this load case calls for" in checks[0].message


# ---------------------------------------------------------------------------
# Provenance.
# ---------------------------------------------------------------------------


def test_every_application_check_names_a_declared_rule():
    emitted = []
    for service in application.SERVICES:
        for workspace in application.WORKSPACES:
            emitted += recommend_mount_class(_ctx(service, workspace)).checks
    emitted += recommend_topology(N17, B625, "radial").checks
    emitted += application.direct_override_checks(N17, B625, "radial")
    for c in emitted:
        assert rules.get(c.code).statement


def test_the_decisions_are_labelled_as_ours():
    """None of this comes from a datasheet. It is reasoning, and the registry
    has to say so rather than borrowing a standard's authority."""
    for rule_id in (
        application.MOUNT_CLASS,
        application.TOPOLOGY_DERIVED,
        application.DYNAMIC_LOADS,
    ):
        assert rules.get(rule_id).status == "derived"
