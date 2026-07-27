# ZooMounter

Generate a mounting plate from an engineering spec, then check the generated
geometry actually matches that spec — including *where every hole ended up*,
not just how much material came back.

Built for [Zoo's API Makeathon](https://zoo.dev) on the **Agent API** (to
generate) and the **File Format API** (to measure).

> **New to this?** A mounting plate is the flat bracket that bolts a motor (or
> a Raspberry Pi, or a monitor arm) to a machine. Get its hole pattern wrong by
> two millimetres and the screws don't line up — the part is scrap. That
> specific failure is what this tool is built to catch.

---

## The problem this solves

Text-to-CAD will happily turn a sentence into geometry. Two things it won't do:

1. **Know your engineering constraints.** "A motor mount" doesn't encode the
   NEMA 17 bolt circle, or how thick the plate has to be so it doesn't visibly
   deflect under a 150N belt tension in PETG.
2. **Tell you whether it got it right.** You get geometry back. Whether it
   matches what you asked for is your problem.

ZooMounter closes both, for one narrow, real task.

## How it works

1. **You give an engineering spec** — mount type, material, load *and load
   type*, safety factor.
2. **A domain-rules layer computes the actual numbers.** The hole pattern comes
   from the real hardware standard. The thickness comes from a beam calc
   (see [Sizing](#sizing-what-the-numbers-mean)).
3. **Those exact numbers go into the Agent API prompt** — every hole as an
   explicit `(x, y)` coordinate, never "add some mounting holes." This is also
   why circular bolt circles (motors, bearings) and rectangular patterns
   (Raspberry Pi, VESA) need no special-casing: they're all just coordinates.
4. **The KCL is executed into a real STEP file** via Zoo's CLI.
5. **The result is checked against the spec** — three checks, below.

You get a Zoo Design Studio project, a STEP file, and a report showing the
calculation, the generation, and every check.

## Verification: what's actually checked

| Check | How | Catches |
|---|---|---|
| **Hole positions** | STEP file parsed locally — every hole centre and diameter compared to the coordinates we asked for | A hole in the wrong place, a missing hole, the wrong bolt pattern |
| **Bounding box** | Parsed locally from the same file | A part generated at the wrong scale |
| **Volume** | Measured by Zoo's File Format API vs. a plate-minus-holes hand calc | Material that shouldn't be there — an unrequested pocket, boss or fillet |

Tolerances: **0.5mm absolute** on hole positions (a bolt hole is either where
the bolt is or it isn't), **15%** on bulk dimensions and volume (to allow for
modelling choices that don't affect fit).

Mass is reported because it's useful to know, but it is **not** counted as a
check — it's the measured volume times the density you supplied, so it carries
no information the volume check doesn't.

> **An earlier version of this tool got that wrong**, and counted volume and
> mass as "two independent checks." They're the same check. More importantly,
> both are blind to a hole being in the wrong place. The measurement that
> disproves it — two parts differing by 0.004% in volume, one of them scrap —
> is written up in
> [examples/WHY-POSITION-CHECKING-MATTERS.md](examples/WHY-POSITION-CHECKING-MATTERS.md).
> That's the reason the hole-position check exists.

## Already mid-project? Start here.

The common case isn't "I want to make a mount" — it's *"I'm halfway through a
design and just realised I need one."* For that:

```bash
cd my-zoo-project
zoomounter --mount nema23 --material aluminum_6061 --load-n 120 --add xMotorMount
```

That's the whole thing. ZooMounter finds your Zoo project by walking up from
wherever you are (like `git` finds `.git`), writes `xMotorMount.kcl` alongside
your other parts, and adds the `import` to `main.kcl`. Reload Design Studio
and it's in your assembly.

No paths to type, no folder to pick, nothing to wire up by hand. The generated
file follows your project's own conventions — a flat `.kcl` at the project
root ending in `export xMotorMount = ...` — so it's indistinguishable from a
part you wrote yourself, and you can edit it freely afterwards.

It won't touch anything it doesn't have to: your line endings are preserved,
the import is only added once (re-run to regenerate with new numbers), and if
you're not inside a Zoo project it says so immediately rather than spending
three minutes generating something with nowhere to go.

## Install

```bash
pip install -e ".[all]"     # or just ".[mcp]" / ".[gui]" for less
```

That puts `zoomounter`, `zoomounter-gui` and `zoomounter-mcp` on your PATH.

Also required:
- A [Zoo API token](https://zoo.dev/signup) — copy `.env.example` to `.env` and paste it in.
  ZooMounter finds this file wherever you run it from, so a project-local
  `.env` works too if you'd rather keep tokens per project.
- The [Zoo CLI](https://github.com/KittyCAD/cli/releases) on your PATH, *only*
  for STEP export and verification — `--add`, `--no-export` and `--print-prompt`
  don't need it. See [NOTES-FOR-ZOO.md](NOTES-FOR-ZOO.md) §2 for why it's a
  separate binary.

## Usage

```bash
# scripted
python -m zoomounter.cli --mount nema17 --material aluminum_6061 --load-n 150 --safety-factor 2

# interactive — run with no flags and answer the prompts
python -m zoomounter.cli

# desktop GUI
python -m zoomounter.gui
```

The GUI adds a live thickness calculation as you type (instant, no API cost), a
rendered preview of the generated part, and an **"Export STEP + verify"**
toggle — uncheck it for a fast preview-only run that skips the STEP export and
verification.

### Working without credits, or without the Zoo CLI

Two flags exist because not every question needs a generation:

```bash
# Size it and print the text-to-CAD prompt. No network, no credits, no Zoo CLI.
# Paste the result straight into Zoo Design Studio's chat.
python -m zoomounter.cli --mount nema23 --material aluminum_6061 \
  --load-n 200 --safety-factor 2.5 --print-prompt

# Generate the Zoo project but skip STEP export and verification.
# Removes the Zoo CLI dependency entirely — open the folder in Design Studio.
python -m zoomounter.cli --mount nema17 --material petg --load-n 40 --no-export
```

Every run writes to its own timestamped folder under `./output/`, containing
`main.kcl` + `project.toml` (**open the `output/` folder in Zoo Design Studio**
— not the repo root — to see each generated part as its own project),
`export/output.step`, and `inspection_report.md`.

### Mount types

| Key | Description | Hole pattern |
|---|---|---|
| `nema17` | NEMA 17 stepper motor | circular, 31mm bolt circle |
| `nema23` | NEMA 23 stepper motor | circular, 47.14mm bolt circle |
| `bearing_608` | 608 (skate) bearing | circular |
| `raspberry_pi` | Raspberry Pi B+/2/3/4 | rectangular, 58 × 49mm |
| `vesa_75` | VESA 75 display bracket | rectangular, 75 × 75mm |
| `custom` | your own circular pattern via `--plate-width-mm`, `--bolt-count`, `--bolt-circle-dia-mm`, `--bolt-hole-dia-mm`, `--center-hole-dia-mm` | circular |

### Materials

| Key | Process |
|---|---|
| `pla`, `petg`, `abs` | 3D printed |
| `aluminum_6061`, `mild_steel` | machined |
| `custom` | your own via `--density-kg-m3`, `--youngs-modulus-gpa`, `--yield-mpa`, `--process` |

## Sizing: what the numbers mean

Load type matters, because a plate reacts the two cases completely differently.

**`--load-type radial`** (default) — a side load: a belt, pulley or gear pulling
perpendicular to the shaft. The plate acts as a cantilever, so this is genuinely
thickness-governed. Checked against bending stress (yield ÷ safety factor) and
tip deflection (limited to arm/300, a common bracket stiffness rule of thumb),
with the larger winning. For motor mounts the motor's own weight is added
(worst case: shaft horizontal, so gravity pulls sideways too) and the lever arm
defaults to the motor's body length.

**`--load-type axial`** — thrust along the bolt axis, e.g. a leadscrew pushing
back into a motor. The plate's own failure mode here is the screw head punching
through it, which needs very little thickness. **For axial loads the plate is
usually not the limiting element at all** — the fasteners, their thread
engagement in the motor's tapped holes, and the motor's own axial bearing
rating typically govern first. ZooMounter runs a screw-tension estimate and
warns when the fasteners are the constraint, and explicitly names what it
*doesn't* check, so a thin result reads as "look elsewhere" rather than as an
all-clear.

Every report names which limit actually governed — including when it's just the
minimum manufacturable wall thickness, which means the part isn't structurally
limited at that load at all.

## Use it from an AI assistant (MCP)

ZooMounter ships an MCP server, so any MCP-capable assistant can drive it in
conversation — *"how thick does a NEMA 23 mount need to be for a 200N belt
load in aluminium?"* runs the real calc rather than guessing.

```bash
python -m zoomounter.mcp_server
```

| Tool | Cost |
|---|---|
| `list_options` | free, instant |
| `size_mount` | free, instant — the sizing calc |
| `build_prompt` | free, instant — the text-to-CAD prompt |
| `inspect_step_file` | free, instant — reads holes/bbox out of any STEP file |
| `verify_step_file` | one API call |
| `generate_mount` | **slow, costs credits** — full generate + verify |

The split is deliberate: an assistant asked for a number shouldn't burn three
minutes and API credits generating a part. The tool descriptions say so
explicitly.

**Claude Code** — a `.mcp.json` is included; it's picked up automatically when
you open this folder.

**Gemini CLI** — add the same block to `~/.gemini/settings.json`.

Both need `ZOO_API_TOKEN` in `.env` for the tools that call the API. The three
free tools work without it.

## Running it as a `zoo` subcommand

The Zoo CLI has no plugin system, but `zoo alias` supports shell expansions,
which gets you the same thing:

```bash
zoo alias set zoomounter '!cd /path/to/ZooMounter && python -m zoomounter.cli "$@"'
zoo zoomounter --mount nema17 --material petg --load-n 40
```

And to open any generated project straight in the desktop app:

```bash
zoo app output/nema17_petg_20260725_143022
```

## Tests

```bash
python -m pytest tests/ -v
```

16 tests, fully offline — no API calls, no credits. They cover the STEP parser,
the hole matcher, the sizing calcs, and the verifier catching a deliberately
corrupted part.

## Honest limitations

- **The calcs are hand-calc grade** — rectangular section, static load, no
  stress concentration at holes. A sanity check for prototyping, not a
  substitute for FEA on anything load-bearing.
- **One load type per run.** A real mount often sees radial and axial load
  simultaneously; this doesn't combine them. Run both and take the worse case.
- **Motor mass and body length are representative values** for a typical
  NEMA 17/23, not your specific motor's datasheet. Override the lever arm with
  `--overhang-mm` if you know the real dimension.
- **`bearing_608` models the bore as a plain through-hole** sized to the bearing
  OD. A real pillow block needs a shouldered pocket or retaining feature to
  actually capture the bearing.
- **Verification can't see features it wasn't told about.** It checks the holes
  and envelope it asked for; a chamfer or fillet the model added on its own
  shows up (if at all) only as a small volume difference.
- **Verification cannot check its own spec.** It proves the AI built what was
  asked for, not that the ask was right. ZooMounter shipped with the NEMA bolt
  patterns wrong (square spacing misread as a bolt-circle diameter — holes
  6.4mm out of position) and *every check passed*, because the generated part
  faithfully matched a wrong table. It took an independently-modelled assembly
  to catch. See
  [WHY-POSITION-CHECKING-MATTERS.md](examples/WHY-POSITION-CHECKING-MATTERS.md).
- **`--mount custom` is circular-pattern only.** The built-in rectangular
  patterns aren't yet expressible as arbitrary custom coordinates from the CLI.
- **The axial screw-tension estimate assumes class-8.8 steel fasteners** at the
  largest standard size that fits the clearance hole. If you're using something
  else, treat it as indicative.

## For the Zoo team

[NOTES-FOR-ZOO.md](NOTES-FOR-ZOO.md) — API findings from building this: an
undocumented response shape, two credit pools where the docs describe one, the
websocket-only KCL execution path, and some things that worked notably well.

## License

MIT — see [LICENSE](LICENSE).
