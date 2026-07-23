# ZooMounter Inspection Report

## Request
- Mount: VESA 75 mount (screen/panel bracket)
- Material: Mild Steel (A36)
- Load: 20.0 N
- Safety factor: 2.5

## Calculated requirement (domain rules layer, before generation)
- Lever arm: 45.00 mm
- Bending moment: 900.0 N*mm
- Allowable stress (yield / safety factor): 100.0 MPa
- Thickness from stress limit: 0.77 mm
- Thickness from deflection limit (arm/300): 1.39 mm
- Process minimum wall: 1.00 mm
- **Required thickness: 1.39 mm**

## Verification (generated part, measured via Zoo File Format API)
Two independent checks -- both must pass:

| Check | Expected (hand calc) | Actual (measured on generated STEP) | Difference | Pass? |
|---|---|---|---|---|
| Volume | 11198.2 mm3 | 11178.8 mm3 | 0.2% | PASS |
| Mass | 87.91 g | 87.75 g | 0.2% | PASS |

Tolerance: 15% on each check.

## Result: PASS
