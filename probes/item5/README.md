# Item 5 evidence — host-side slots, generated and verified

Item 5 (host-side mounting features) was written, tested and marked done
without the slot geometry ever being generated. These two parts close that:
both were produced end-to-end through the real pipeline — Agent API →
KCL → STEP → verification — and both passed.

| | `2020-slots` | `4040-slots` |
|---|---|---|
| Plate width | 80.00 mm | 110.00 mm |
| Slot centres | ±30.0 mm | ±40.0 mm |
| Slot size | 15.0 × 5.5 mm (M5) | 20.0 × 9.0 mm (M8) |
| Hole positions | PASS, worst 0.000 mm | PASS, worst 0.000 mm |
| Bounding box | PASS, 0.0% | PASS, 0.0% |
| Volume | PASS, 0.5% | PASS, 0.9% |

Reproduce:

```
python -m zoomounter.cli --mount nema17 --material aluminum_6061 \
  --load-n 20 --load-type radial --host-mount 2020-slots \
  --center-hole-dia-mm 22 --output-dir probes/item5/2020-slots
```

## What "all 9 holes" is actually proving

9 = 4 bolt holes + 1 centre bore + **4 slot ends**.

An obround slot of length L and width W reads in the STEP as two
semicircular ends of diameter W, centred ±(L−W)/2 either side of the slot
centre. Finding four such arcs at the computed offsets is what distinguishes
a real slot from a plain round hole sitting where the slot should be — which
is the failure mode that would otherwise look like success.

## Why two files, not one

Before the fix in this branch, `--host-mount 4040-slots` was byte-identical
to `2020-slots`: both rounded spacing to 20 mm and used M5 clearance, so the
option parsed, generated and verified while silently doing nothing. The 30 mm
difference in plate width and the different slot sizes above are the evidence
that the two options now produce genuinely different parts.

## One caveat on the inspection reports

`inspection_report.md` in each folder was written **before** the
`apply_host_mount` field-dropping fix, so both carry the line:

> No published radial shaft-load limit is on file for NEMA 17...

That is wrong — NEMA 17 has a 28 N radial limit, and the transform was
discarding it. Fixed in the following commit; regenerating the reports would
cost credits for a text-only change, so they are kept as-is.

**The geometry is unaffected.** The limits bug changed a warning string, not
a dimension, so the STEP files and the verification results above stand.
