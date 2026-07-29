# ZooMounter Inspection Report

## Request
- Mount: NEMA 17 stepper motor mount (with 4040-slots)
- Material: Aluminum 6061-T6
- Load type: radial (Radial (side) load -- modeled as a cantilever beam; bending stress and tip deflection both checked.)
- External load: 20.0 N
- Component self-weight added to external load: 2.75 N -> effective load 23.66 N
- Safety factor: 2.0

## Calculated requirement (domain rules layer, before generation)
- Lever arm: 15.00 mm
- Bending moment: 354.9 N*mm
- Allowable stress (yield / safety factor): 120.0 MPa
- Thickness from stress limit: 0.40 mm
- Thickness from deflection limit (arm/300, radial only): 0.94 mm
- Process minimum wall: 1.00 mm
- **Required thickness: 1.00 mm** (governed by: process minimum wall (machined))

### Engineering notes
- Includes 2.75N of component self-weight (worst case: shaft mounted horizontal, so gravity acts as a side load too).
- **[WARN]** No published radial shaft-load limit is on file for NEMA 17 stepper motor mount (with 4040-slots), so this load has NOT been checked against the component itself -- only against the plate.
  - *Remedy*: Look up the radial load rating in your part's datasheet and confirm 20N is within it.
- The engineering calcs came out below the minimum manufacturable wall thickness, so the process floor sets the answer -- this part is not structurally limited at this load.


## Verification (generated part vs. the spec it was asked for)

Hole positions and bounding box are read directly out of the generated STEP
file (local parse, no API calls). Volume is measured by Zoo's File Format API.

| Check | Result | Detail |
|---|---|---|
| Hole positions | PASS | all 9 holes present and within 0.5mm of spec (worst: 0.000mm) |
| Bounding box | PASS | 110.00 x 42.30 x 1.00 mm vs specified 110.00 x 42.30 x 1.00 mm (worst axis: width, 0.0%) |
| Volume | PASS | 3912.4 mm3 measured vs 3876.6 mm3 from the requested geometry (0.9%) |

Tolerances: 0.5mm absolute on hole positions, 15% on bulk dimensions and volume.

### Hole-by-hole

| Expected (x, y) mm | Dia mm | Found at (x, y) mm | Position error mm |
|---|---|---|---|
| (15.50, 15.50) | 3.40 | (15.50, 15.50) | 0.000 |
| (15.50, -15.50) | 3.40 | (15.50, -15.50) | 0.000 |
| (-15.50, -15.50) | 3.40 | (-15.50, -15.50) | 0.000 |
| (-15.50, 15.50) | 3.40 | (-15.50, 15.50) | 0.000 |
| (0.00, 0.00) | 22.00 | (0.00, 0.00) | 0.000 |
| (-40.00, -5.50) | 9.00 | (-40.00, -5.50) | 0.000 |
| (-40.00, 5.50) | 9.00 | (-40.00, 5.50) | 0.000 |
| (40.00, -5.50) | 9.00 | (40.00, -5.50) | 0.000 |
| (40.00, 5.50) | 9.00 | (40.00, 5.50) | 0.000 |

Mass of the generated part: **10.56 g**. This is reported as a property, not counted as a check -- it is the measured volume multiplied by the density you supplied, so it carries no information the volume check doesn't.

## Result: PASS
