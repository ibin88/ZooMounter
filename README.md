# ZooMounter

Generate a mechanical mount from an engineering spec — not a vague prompt — and get it back verified, not just generated.

Built for [Zoo's API Makeathon](https://zoo.dev), using the **Agent API** (to generate) and the **File Format API** (to verify).

## The idea

Text-to-CAD tools are great at turning a sentence into geometry, but they don't know engineering standards, and there's no check that what comes back actually matches what you asked for. ZooMounter closes that gap for one common prototyping task — sizing and generating a mount plate:

1. **You give it an engineering spec** — mount type (e.g. NEMA 17 motor, Raspberry Pi, VESA panel), material, expected load, safety factor.
2. **A domain-rules layer calculates the real numbers** — the mount's exact hole pattern (from a real hardware standard, not guessed), and the plate thickness required to survive the load (cantilever bending stress + deflection checks) in the material you chose.
3. **Those exact numbers go into the Agent API prompt** — every hole given as an exact (x, y) coordinate, not "add some mounting holes." This is also what lets the same pipeline handle circular bolt-circle patterns (motors, bearings) and rectangular hardware patterns (Raspberry Pi, VESA) with no special-casing.
4. **The generated KCL is executed into a real STEP file** via Zoo's CLI.
5. **The STEP file is measured independently** through the File Format API — **two separate checks**, volume (pure geometry) and mass (geometry × density), each compared against what the requested spec should produce. Both have to pass. A hole in the wrong place doesn't always move the mass much, but it usually moves the volume — checking both catches more than either alone.

You get a STEP file and a report showing the calculation, the generation, and both pass/fail checks — so "AI-generated CAD" comes with a receipt.

## Install

```bash
pip install -r requirements.txt
```

You also need:
- A [Zoo API token](https://zoo.dev/signup) (free tier covers plenty of test runs) — copy `.env.example` to `.env` and paste it in.
- The [Zoo CLI](https://github.com/KittyCAD/cli/releases) on your PATH (or set `ZOO_CLI_PATH` in `.env` to its location). It's used to execute the generated KCL into a STEP file.

## Usage

Scripted:
```bash
python -m zoomounter.cli --mount nema17 --material aluminum_6061 --load-n 5 --safety-factor 2
```

Interactive — just run it with no flags and answer the prompts:
```bash
python -m zoomounter.cli
```

Output goes to `./output/` by default: `mount.kcl`, `export/output.step`, and `inspection_report.md`. The terminal shows a spec summary, live status while the Agent API generates (this step usually takes 1-3 minutes), and a color-coded pass/fail results table at the end.

### Built-in mount types
| Key | Description | Hole pattern |
|---|---|---|
| `nema17` | NEMA 17 stepper motor mount | circular bolt circle |
| `nema23` | NEMA 23 stepper motor mount | circular bolt circle |
| `bearing_608` | 608 (skate) bearing mount | circular bolt circle |
| `raspberry_pi` | Raspberry Pi mounting plate (Model B+/2/3/4) | rectangular, real Pi hole spacing |
| `vesa_75` | VESA 75 mount (screen/panel bracket) | rectangular, VESA standard |
| `custom` | supply your own circular bolt pattern via `--plate-width-mm`, `--bolt-count`, `--bolt-circle-dia-mm`, `--bolt-hole-dia-mm`, `--center-hole-dia-mm` | circular bolt circle |

### Built-in materials
| Key | Process |
|---|---|
| `pla`, `petg`, `abs` | 3D printed |
| `aluminum_6061`, `mild_steel` | machined |
| `custom` | supply your own via `--density-kg-m3`, `--youngs-modulus-gpa`, `--yield-mpa`, `--process` |

## Why this, not just Zookeeper

Zoo's own web app already does prompt-to-CAD out of the box — that's not something to rebuild. What it doesn't do is *check its own work* against an engineering requirement. ZooMounter is deliberately narrow (one part type: mounting plates) so that the domain-rules and verification layers are real, not hand-waved — and it's structured so adding a new mount family or material is a table entry, not a rewrite, for anyone who wants to extend it.

## Known limitations (prototyping-grade, not certified)

- The bending calc is a hand-calc-grade cantilever approximation (rectangular section, static load, no stress concentration at holes) — good for a sanity check, not a substitute for real FEA on a load-bearing part.
- The `bearing_608` mount models the center feature as a plain through-bore sized to the bearing OD. A real pillow-block mount needs a shouldered pocket or retaining feature to actually capture the bearing — this is a v1 simplification, flagged here on purpose.
- Verification checks volume and mass, both derived from the same rectangular-minus-holes area model -- it doesn't independently confirm hole *positions* are correct, only that the total material removed matches. A 15% tolerance is applied to allow for modeling differences that don't affect fit or function.
- `--mount custom` only supports circular bolt patterns for now; built-in rectangular patterns (Raspberry Pi, VESA) aren't yet exposable as arbitrary custom coordinates from the CLI.

## License

MIT — see [LICENSE](LICENSE).
