# Why ZooMounter checks hole positions, not just volume

This document exists because ZooMounter's verification used to be wrong, in a
way that looked convincing. It's kept in the repo because the mistake is an
easy one to make in any "verify the AI's output" tool, and the measurement
that disproves it is more useful than the claim that replaced it.

## The mistake

The first version ran two checks — volume and mass — and the README claimed
that "checking both catches more than either alone." Every example report
showed both agreeing closely:

```
| Volume | 11198.2 mm3 | 11178.8 mm3 | 0.2% | PASS |
| Mass   | 87.91 g     | 87.75 g     | 0.2% | PASS |
```

That agreement isn't corroboration. It's arithmetic:

```
expected_mass = expected_volume × density     ← our hand calc
actual_mass   = actual_volume   × density     ← what /file/mass computes
```

Both sides of the mass check are the volume check times the same constant, so
the two percentages are algebraically forced to match. Two checks, one signal.

## The bigger problem

Both checks are scalars. Volume cannot encode *where* material is. Move a bolt
hole and you remove exactly the same amount of material — so a plate that will
not bolt onto its motor passes a volume check cleanly.

That's an argument. Here's the measurement.

## The experiment

Two STEP files. One is a real ZooMounter-generated NEMA 17 plate. The other is
byte-identical except that **one bolt hole is displaced 2mm** — enough that an
M3 screw no longer lines up with the motor's tapped hole. The part is scrap.

Both were measured with Zoo's File Format API (`POST /file/volume`):

| Part | Measured volume | vs. hand calc (1380.88 mm³) | Volume check |
|---|---|---|---|
| Correct plate | 1381.22 mm³ | 0.02% | **PASS** |
| Bolt hole 2mm out of place | 1381.17 mm³ | 0.02% | **PASS** |

The two parts differ by **0.05 mm³** — 0.004%. No volume tolerance that
tolerates normal modelling variation could ever separate them. The same is
true of mass, which is that number times a density. It's also true of the
bounding box: the outline is unchanged.

Now the check ZooMounter actually runs, reading hole centres back out of the
STEP file:

| Part | Hole position check |
|---|---|
| Correct plate | **PASS** — all 5 holes within 0.5mm (worst: 0.000mm) |
| Bolt hole 2mm out of place | **FAIL** — 1 of 5 holes further than 0.5mm from spec (worst: 2.000mm) |

## What ZooMounter does now

Three checks, and they fail for genuinely different reasons:

1. **Hole positions** — every hole's centre and diameter parsed out of the
   STEP file and compared against the exact coordinates sent to the Agent API.
   This is the check that catches geometric drift. It's local text parsing, so
   it costs nothing to run.
2. **Bounding box** — outer envelope and thickness, also parsed locally.
   Catches a part generated at the wrong scale.
3. **Volume** — measured by the File Format API against a plate-minus-holes
   hand calc. Catches material that's present but shouldn't be (an
   unrequested pocket, boss, or fillet) — something neither of the above sees.

Mass is still reported, because it's a thing you want to know about a part.
It is *not* counted as a check, because it isn't an independent one.

## Reproducing this

Both fixtures and the assertions above are in the test suite, which runs
offline with no API calls:

```bash
python -m pytest tests/ -v
```

The relevant tests are `test_volume_is_blind_to_hole_position`,
`test_bounding_box_is_blind_to_hole_position`, and `test_drifted_hole_is_caught`.
