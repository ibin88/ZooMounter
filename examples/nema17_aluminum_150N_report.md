# ZooMounter Inspection Report

## Request
- Mount: NEMA 17 stepper motor mount
- Material: Aluminum 6061-T6
- Load type: radial (Radial (side) load -- modeled as a cantilever beam; bending stress and tip deflection both checked.)
- External load: 150.0 N
- Component self-weight added to external load: 2.75 N -> effective load 152.75 N
- Safety factor: 2.0

## Calculated requirement (domain rules layer, before generation)
- Lever arm: 40.00 mm
- Bending moment: 6109.9 N*mm
- Allowable stress (yield / safety factor): 120.0 MPa
- Thickness from stress limit: 2.69 mm
- Thickness from deflection limit (arm/300, radial only): 4.65 mm
- Process minimum wall: 1.00 mm
- **Required thickness: 4.65 mm** (governed by: deflection limit (arm/300))

### Engineering notes
- Includes 2.75N of component self-weight (worst case: shaft mounted horizontal, so gravity acts as a side load too).

## Verification (generated part vs. the spec it was asked for)

Hole positions and bounding box are read directly out of the generated STEP
file (local parse, no API calls). Volume is measured by Zoo's File Format API.

| Check | Result | Detail |
|---|---|---|
| Hole positions | PASS | all 5 holes present and within 0.5mm of spec (worst: 0.000mm) |
| Bounding box | PASS | 42.30 x 42.30 x 4.65 mm vs specified 42.30 x 42.30 x 4.65 mm (worst axis: thickness, 0.0%) |
| Volume | PASS | 6422.7 mm3 measured vs 6419.8 mm3 from the requested geometry (0.0%) |

Tolerances: 0.5mm absolute on hole positions, 15% on bulk dimensions and volume.

### Hole-by-hole

| Expected (x, y) mm | Dia mm | Found at (x, y) mm | Position error mm |
|---|---|---|---|
| (15.50, 0.00) | 3.00 | (15.50, 0.00) | 0.000 |
| (0.00, 15.50) | 3.00 | (-0.00, 15.50) | 0.000 |
| (-15.50, 0.00) | 3.00 | (-15.50, 0.00) | 0.000 |
| (-0.00, -15.50) | 3.00 | (-0.00, -15.50) | 0.000 |
| (0.00, 0.00) | 22.00 | (-0.00, -0.00) | 0.000 |

Mass of the generated part: **17.34 g**. This is reported as a property, not counted as a check -- it is the measured volume multiplied by the density you supplied, so it carries no information the volume check doesn't.

## Result: PASS
