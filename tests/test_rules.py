"""The rule registry, and the invariants that make it worth having.

Offline. These tests are the enforcement half of `data/rules.toml`: a registry
nothing checks is documentation, and documentation drifts.
"""

import pytest

from zoomounter import bearings, mechanics, rules
from zoomounter.catalogue import CatalogueError
from zoomounter.materials import get_material
from zoomounter.mount_specs import MOUNTS, get_mount

ALUMINIUM = get_material("aluminum_6061")
PLA = get_material("pla")


# ---------------------------------------------------------------------------
# The registry loads, and every field means something.
# ---------------------------------------------------------------------------


def test_the_registry_is_not_empty():
    assert rules.RULES


def test_every_rule_has_a_trusted_or_quarantined_status():
    for rule in rules.RULES.values():
        assert rule.status in rules.VALID_STATUSES


def test_every_firing_rule_carries_a_remedy():
    """A warning the user cannot act on is noise. The locked ruling on this
    project is that warnings must reach the person AND be actionable."""
    for rule in rules.RULES.values():
        if rule.severity in ("warn", "loud-warn"):
            assert rule.remedy, f"{rule.id}: fires but offers no remedy"


def test_no_rule_cites_our_own_documents():
    """The circular-provenance defect: the axial limit once cited
    docs/mechanics.html, which is a document we wrote. A citation that points
    back at this project proves nothing."""
    for rule in rules.RULES.values():
        lowered = rule.source.lower()
        assert "docs/" not in lowered and "mechanics.html" not in lowered, (
            f"{rule.id}: cites our own documentation"
        )


def test_a_derived_rule_says_it_is_ours():
    """`derived` is the honest label for a claim we reasoned out. It must not
    borrow the authority of a datasheet by omission -- the advisory threshold
    is a judgement call and has to read as one."""
    rule = rules.get("bearing_advisory_utilisation")
    assert rule.status == "derived"
    assert "OURS" in rule.source or "not a datasheet" in rule.source


# ---------------------------------------------------------------------------
# The quarantine. This is the point of the file.
# ---------------------------------------------------------------------------


def test_unverified_rules_never_govern():
    """An AI-proposed rule may sit in the registry, visibly labelled, but it
    must not reach a generated part until someone checks it against reality.

    This is the structural form of the project's central finding: a generated
    number and a verified one are indistinguishable once written down, so the
    difference has to be carried by a required field rather than by whoever
    reviewed the diff."""
    for kind in ("motor", "bearing", "board", "flange", None):
        rules.assert_no_unverified_rules_govern(kind=kind)


def test_governing_rules_exclude_the_quarantine():
    governing = {r.id for r in rules.governing_rules(kind="motor")}
    quarantined = {r.id for r in rules.quarantined_rules()}
    assert not (governing & quarantined)


def test_a_quarantined_rule_is_rejected_if_it_would_govern(monkeypatch):
    """Assert the guard actually bites, rather than passing because nothing is
    quarantined today. A test that cannot fail is not a check."""
    planted = rules.Rule(
        id="planted",
        statement="An AI made this up.",
        source="An AI made this up.",
        severity="loud-warn",
        status="ai-proposed-unverified",
        remedy="Verify it.",
        applies_to={"kind": "motor"},
    )
    monkeypatch.setitem(rules.RULES, "planted", planted)
    with pytest.raises(CatalogueError, match="planted"):
        rules.assert_no_unverified_rules_govern(kind="motor")


# ---------------------------------------------------------------------------
# applies_to: domain awareness as data, not as special cases in code.
# ---------------------------------------------------------------------------


def test_shaft_rules_do_not_apply_to_a_board():
    """A board has no shaft, so no shaft rule may govern it. This is what the
    tool should have been doing instead of sizing a Raspberry Pi plate against
    a fabricated 15mm shaft offset."""
    for rule in rules.governing_rules(kind="board"):
        assert "shaft" not in rule.id, f"{rule.id} must not govern a board"


def test_a_board_still_matches_the_universal_rules():
    """Declining to model something is not the same as knowing nothing about
    it. Clearance holes and provenance still apply."""
    ids = {r.id for r in rules.governing_rules(kind="board")}
    assert "bolt_holes_are_clearance" in ids
    assert "every_figure_carries_a_source" in ids


def test_thermal_rule_is_scoped_to_printing():
    assert rules.get("thermal_unmodelled").applies(process="3d_print")
    assert not rules.get("thermal_unmodelled").applies(process="machined")


def test_an_unknown_applies_to_key_is_rejected():
    """An applies_to naming a field that does not exist would match nothing --
    the quietest possible way for a safety rule to stop firing."""
    with pytest.raises(CatalogueError, match="unknown key"):
        rules.Rule(
            id="x", statement="s", source="src", severity="info", status="derived",
            applies_to={"colour": "red"},
        )
        raise CatalogueError("unknown key: not reached")  # pragma: no cover


# ---------------------------------------------------------------------------
# The unification: a message the user sees must trace back to a declared rule.
# ---------------------------------------------------------------------------


def _all_emitted_checks():
    """Every Check the domain layer can actually produce, across the cases that
    reach the user."""
    emitted = []
    for key in MOUNTS:
        mount = get_mount(key)
        for material in (ALUMINIUM, PLA):
            emitted += mechanics.required_thickness(
                mount, material, plate_load_n=25
            ).notes
        if mount.kind != "motor":
            continue
        for load_type in mechanics.LOAD_TYPES:
            for load in (1, 25, 400):
                emitted += mechanics.shaft_support(mount, load, load_type).checks

    sel = bearings.select_bearing("axial", 200, 8, 2.0)
    emitted += sel.notes
    emitted += bearings.select_bearing("radial", 5_000_000, 8, 2.0).notes
    emitted += bearings.select_bearing("axial", 100, 8, 2.0, designation="608").notes
    if sel.bearing:
        emitted.append(bearings.check_seat_depth(sel.bearing, 1.0, "axial"))
        emitted.append(bearings.seat_fit_note(sel.bearing))
    return emitted


def test_every_check_code_names_a_declared_rule():
    """The unification, enforced. `Check.code` is the rule id, so any message
    the user sees can be traced to a claim with a source and a status.

    Without this, `rules.toml` is a second place to write things down and the
    two drift -- which is the failure mode the registry exists to end."""
    for check in _all_emitted_checks():
        if not check.code:
            continue
        rule = rules.get(check.code)  # raises if undeclared
        assert rule.statement


def test_checks_that_can_warn_come_from_rules_that_can_warn():
    """A LOUD WARN reaching the user from a rule declared 'info' means the
    registry no longer describes the code."""
    order = ["PASS", "INFO", "WARN", "LOUD WARN"]
    for check in _all_emitted_checks():
        if not check.code or check.level == "PASS":
            continue
        rule = rules.get(check.code)
        assert order.index(check.level) <= order.index(rule.level), (
            f"{check.code}: emitted at {check.level} but declared {rule.level}"
        )


# ---------------------------------------------------------------------------
# Declared limitations.
# ---------------------------------------------------------------------------


def test_limitations_are_declared_and_have_no_evaluator():
    ids = {r.id for r in rules.limitations()}
    assert "fatigue_unmodelled" in ids
    assert "impact_unmodelled" in ids
    for rule in rules.limitations():
        assert not rule.evaluated


def test_motor_limitations_include_reaction_torque():
    """Reaction torque is present whenever the motor runs, at zero external
    load, and the tool does not model it. Saying so is the honest handling --
    an unmodelled load case nobody mentions is indistinguishable from one that
    passed."""
    ids = {r.id for r in rules.limitations(kind="motor")}
    assert "reaction_torque_unmodelled" in ids
    assert "belt_pretension_unmodelled" in ids
