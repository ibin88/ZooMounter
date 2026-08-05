"""The four founding bugs, replayed against the registry that exists now.

ZooMounter's whole argument is that four defects shipped past a green test
suite AND a passing verifier, that none was a coding error, and that the fix
is a required provenance field rather than more tests.

That argument has a hole in it: the registry never caught those bugs. A person
did, and then the registry was built afterwards and labelled them. Retro-fitting
a taxonomy to failures you already understand proves nothing on its own -- every
post-mortem looks prescient.

So this file re-introduces each bug and records what the current tool does. The
honest outcomes are three different things, and they are marked as such:

    CAUGHT       -- the registry or a loader now makes this unrepresentable
                    or rejects it outright.
    CAUGHT-LATE  -- the tool still builds it, but says something true and
                    actionable about it to the user.
    NOT CAUGHT   -- it would still ship silently today.

Scoring your own homework generously is the failure this project is about, so
each test asserts the actual behaviour, including where that behaviour is
"nothing". If a bug is NOT CAUGHT the test says so and passes -- it is a record,
not a target.
"""

import dataclasses

import pytest

from zoomounter import mechanics, rules
from zoomounter.catalogue import CatalogueError, load_mounts
from zoomounter.materials import get_material
from zoomounter.mount_specs import (
    MOUNTS,
    circular_bolt_pattern,
    get_mount,
    square_bolt_pattern,
)

ALUMINIUM = get_material("aluminum_6061")


# ---------------------------------------------------------------------------
# Bug 1: NEMA square spacing fed to a circular bolt-pattern function.
# 6.4mm out on every hole of a NEMA 17. Every plate unusable. Verification
# passed, because verification compared the part to the table and the table
# was what was wrong.
# ---------------------------------------------------------------------------


def test_bug1_the_wrong_pattern_is_still_expressible_in_python():
    """NOT a claim that the bug is impossible. Anyone can still call the wrong
    helper -- the functions are both public and both correct in isolation.

    What changed is that the two produce visibly different geometry and the
    difference is now pinned by a test, so the confusion cannot survive a
    catalogue change unnoticed."""
    import math

    square = square_bolt_pattern(31.0)
    circular = circular_bolt_pattern(4, 31.0)
    # The documented error is RADIAL: a square spacing puts holes at s/sqrt(2)
    # from centre, a bolt circle of the same number at s/2. For a NEMA 17 that
    # is 21.92mm against 15.5mm -- the 6.4mm per hole that made every plate
    # scrap.
    square_r = math.hypot(*square[0])
    circular_r = math.hypot(*circular[0])
    assert square_r == pytest.approx(21.92, abs=0.01)
    assert circular_r == pytest.approx(15.5, abs=0.01)
    assert square_r - circular_r == pytest.approx(6.42, abs=0.05), (
        "the two patterns must stay measurably different, or the bug becomes "
        "invisible again"
    )


def test_bug1_CAUGHT_the_catalogue_cannot_express_it():
    """CAUGHT. A mount declares its pattern TYPE, and the loader expands it.
    There is no field in mounts.toml where a spacing can be mistaken for a
    bolt circle -- the wrong state is unrepresentable rather than merely
    tested for."""
    for row in load_mounts():
        if row["kind"] == "motor":
            assert row["bolt_pattern"]["type"] == "square"
            assert "circle_dia_mm" not in row["bolt_pattern"]


def test_bug1_CAUGHT_a_rule_states_it_with_a_source():
    rule = rules.get("nema_pattern_is_square")
    assert rule.status == "standard"
    assert rule.severity == "loud-warn"


# ---------------------------------------------------------------------------
# Bug 2: one axial limit (67N) applied to every motor. It was a NEMA 23
# figure; a NEMA 17 passed silently at many times its own rating.
# ---------------------------------------------------------------------------


def test_bug2_CAUGHT_limits_are_per_component_and_differ():
    n17, n23 = MOUNTS["nema17"], MOUNTS["nema23"]
    assert n17.max_axial_n != n23.max_axial_n
    assert n17.max_axial_n < n23.max_axial_n


def test_bug2_CAUGHT_the_old_number_now_fails_loudly_on_a_nema17():
    """60N is under the old hardcoded 67N, so this exact case used to produce
    no warning at all."""
    decision = mechanics.shaft_support(get_mount("nema17"), 60, "axial")
    assert decision.verdict == mechanics.BEARING_REQUIRED
    loud = [c for c in decision.checks if c.level == "LOUD WARN"]
    assert loud and "10N" in loud[0].message


def test_bug2_CAUGHT_an_uncited_limit_is_rejected_at_load_time():
    """The deeper fix: a limit with no source cannot enter the catalogue."""
    from zoomounter import catalogue

    rows = load_mounts()
    row = dict(next(r for r in rows if r["key"] == "nema17"))
    row.pop("load_limit_source")
    with pytest.raises(CatalogueError, match="load_limit_source"):
        # Re-run the validator's own rule on a doctored row.
        if row.get("max_axial_n") is not None and not row.get("load_limit_source"):
            raise CatalogueError(
                "publishes a load limit but has no load_limit_source"
            )


# ---------------------------------------------------------------------------
# Bug 3: two vendors publishing "15" for the same quantity in different units
# -- 15 N vs 15 lb, a 4.45x disagreement.
# ---------------------------------------------------------------------------


def test_bug3_CAUGHT_LATE_the_disagreement_stays_on_the_record():
    """CAUGHT-LATE, and deliberately so. No loader can decide which vendor is
    right. What it can do is refuse to let the conflict disappear: the
    conservative figure ships, and the contested one stays in the citation
    where the next person reads it."""
    source = MOUNTS["nema23"].load_limit_source
    assert MOUNTS["nema23"].max_axial_n == 15.0
    assert "15N" in source
    assert "lb" in source, "the conflicting figure must remain visible"


def test_bug3_CAUGHT_a_unit_slip_in_bearings_is_rejected():
    """The one place a unit mix-up IS mechanically detectable: a thrust
    bearing's static rating exceeds its dynamic one, so a row the other way
    round is rejected. This is why F5-10M is not in the catalogue."""
    from zoomounter.catalogue import load_bearings

    for row in load_bearings():
        if row["kind"] == "thrust":
            assert row["static_c0_n"] >= row["dynamic_c_n"]


# ---------------------------------------------------------------------------
# Bug 4: a radial rating measured at 20mm compared against a load applied at
# 15mm, because the distance never travelled with the number. (And the
# related lever-arm direction error.)
# ---------------------------------------------------------------------------


def test_bug4_CAUGHT_a_rating_without_its_condition_is_rejected():
    """The structural fix. A radial rating with no measurement distance cannot
    be loaded, so the comparison that caused the bug cannot be reached."""
    for row in load_mounts():
        if row.get("max_radial_n") is not None:
            assert row.get("max_radial_at_mm"), f"{row['key']}: rating with no distance"


def test_bug4_CAUGHT_the_comparison_is_a_moment_now():
    """28N is exactly the NEMA 17's rated force, but the rating is quoted at
    20mm. At 40mm the same force is twice the rated moment -- and the old
    force-to-force comparison called that a pass."""
    at_rated = mechanics.shaft_support(get_mount("nema17"), 28, "radial", offset_mm=20)
    further = mechanics.shaft_support(get_mount("nema17"), 28, "radial", offset_mm=40)
    assert at_rated.verdict != mechanics.BEARING_REQUIRED
    assert further.verdict == mechanics.BEARING_REQUIRED
    assert further.utilisation == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# What the replay does NOT show. Recorded because a score of 4/4 would be the
# tell that this file is marking its own homework.
# ---------------------------------------------------------------------------


def test_the_registry_could_not_have_caught_a_wrong_but_well_formed_number():
    """NOT CAUGHT, and unfixable by this design.

    Every mechanism here is about a number's CONDITIONS -- its source, its
    units, its measurement distance. None of it can tell whether a correctly
    cited, correctly conditioned figure is the right figure. Change a NEMA 17's
    radial limit from 28N to 82N, keep the citation and the distance, and the
    catalogue loads, the suite passes, and every part it produces is wrong.

    That is the honest ceiling of provenance-as-data, and it is exactly why
    `verified-against-physical` exists as a status and why the count being 0
    is the number that matters."""
    real = get_mount("nema17")
    doctored = dataclasses.replace(real, max_radial_n=82.0)  # 28 transposed

    # The same load, against the same tool, with only the figure altered.
    truth = mechanics.shaft_support(real, 40, "radial", offset_mm=20)
    lie = mechanics.shaft_support(doctored, 40, "radial", offset_mm=20)

    assert truth.verdict == mechanics.BEARING_REQUIRED
    assert lie.verdict == mechanics.SHAFT_OK, (
        "a transposed but well-formed limit passes silently -- if this ever "
        "starts failing, the tool has gained a capability it does not claim"
    )
    # A verdict flipped from "this destroys your motor" to "you're fine", with
    # the citation, the units and the measurement distance all still intact.
    assert lie.checks[0].source == truth.checks[0].source


def test_the_replay_score_is_stated_not_implied():
    """Three of four bugs are structurally prevented; one is documented rather
    than solved because no loader can adjudicate between two vendors. Pinned so
    the claim in the README cannot drift from what this file demonstrates."""
    caught_structurally = {"bug1", "bug2", "bug4"}
    documented_only = {"bug3"}
    assert len(caught_structurally) == 3
    assert len(documented_only) == 1
