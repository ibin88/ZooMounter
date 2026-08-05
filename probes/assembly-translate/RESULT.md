# `translate` on an imported module moves only its LAST body

## What was tested

`multi.kcl` defines two solids: `bodyA` (20mm disc) and `bodyB` (8mm boss).
`main.kcl` imports the whole file and translates it:

```kcl
import "multi.kcl" as multi

multi
  |> translate(x = asmX, y = asmY, z = asmZ)   // asmX = 30
```

## What happened

| Body | Defined | X after translate(x = 30) |
|---|---|---|
| `bodyA` | first | **0.0** — did not move |
| `bodyB` | last | 30.0 — moved |

No error. No warning. The export succeeded and produced a solid that looks
deliberate.

## Why it matters

This is the natural way to position a generated assembly, and it half-works,
which is worse than not working. A four-part mount assembly positioned this way
puts three bodies at the origin and one where you asked, and nothing tells you.

ZooMounter therefore drives every body from a shared `parameters.kcl` rather
than translating the module. Verified: with that arrangement all 15 circles in a
real mount assembly shift by exactly 100mm together.

Reproduce with the files here and `zoo kcl export --output-format step main.kcl .`
