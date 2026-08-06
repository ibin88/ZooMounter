

https://github.com/user-attachments/assets/405e8449-3164-4445-88a8-19eff0985d94

# ZooMounter



https://github.com/user-attachments/assets/687351ed-9f30-4ac0-83ad-7552d90ce1d5



**An AI-generated number and a verified one look identical once they're
written down.** That's not a prompting problem, and you can't test your way
out of it — a test written from the same wrong assumption as the code will
agree with it.

ZooMounter is a mount generator built on [Zoo's](https://zoo.dev) text-to-CAD
Agent API, and it's really an argument about that: **provenance has to be a
required field, not a review step.** Every engineering claim it makes carries
where it came from and how far it can be trusted.

The evidence is four bugs this project shipped. Not one was a coding error.
All four were wrong or missing *numbers*, and all four passed a green test
suite **and** a passing geometry verifier:

| The bug | Why nothing caught it |
|---|---|
| A NEMA square bolt spacing fed to a circular bolt-circle function | 6.4 mm out on every hole. Verification compares the part to the spec table — and the table was what was wrong. |
| One axial limit (67 N) applied to every motor | It was a NEMA 23 figure. A NEMA 17 is rated 10 N, so it passed silently at 6.7× its own limit. |
| Two vendors publishing "15" in different units | 15 N vs 15 lb. A 4.45× disagreement, invisible in either source. |
| A rating measured at 20 mm compared to a load applied at 15 mm | The number travelled; its conditions didn't. |

[`tests/test_the_four_bugs.py`](tests/test_the_four_bugs.py) replays all four
against the tool as it stands today and scores it: **3 of 4 are now
structurally impossible**, 1 is documented rather than solved. The most
important test in that file asserts a *failure* — transpose a limit from 28 N
to 82 N, keep the citation and the units and the measurement distance, and the
tool cheerfully flips "this destroys your motor" to "you're fine". Provenance
constrains a number's *conditions*. It cannot tell you the number is right.

Which is why the registry reports **0 of 29 rules verified against a physical
part**. Nothing here has ever been built and loaded. The column exists so that
gap is visible instead of assumed — see [RULES.md](RULES.md).

---

> **New to this?** A mounting plate is the flat bracket that bolts a motor to a
> machine. Get its hole pattern wrong by two millimetres and the screws don't
> line up — the part is scrap. That specific failure is what this tool is built
> to catch. The second one it catches is subtler: a stepper's shaft can take
> far less side load and thrust than people assume, and no amount of bracket
> makes up for it.

Built on the **Agent API** (to generate) and the **File Format API** (to
measure). It generates a mounting plate from an engineering spec, then checks
the geometry that comes back actually matches it — including *where every hole
ended up*, not just how much material came back. A worked example, generated
live and verified, is in
[`examples/nema23-bearing-required/`](examples/nema23-bearing-required/).

---

## What got deleted, and why that's the point

On the last day of the build this tool **lost its entire structural layer** —
cantilever bending, an L/300 deflection limit, screw-head punching shear, and a
fastener-tension check. All four, removed.

They never governed. A NEMA 17's published radial limit is 28 N; run that
through a beam calc on a 42 mm plate in any real material and the answer lands
below the minimum wall the process can produce. Every time. A thickness quoted
to two decimals from a named formula was the process floor wearing a
calculation's clothes.

Worse, they answered a question about the wrong object. A motor housing is a
metal shell bolted to a flat plate — it is not the fragile part and never was.
The fragile part is the **shaft**, and the small bearings inside the motor that
support it, whose published limits are an order of magnitude below anything the
bracket notices.

So the headline output is no longer a thickness. It's a verdict:

```
BEARING REQUIRED  (axial shaft load)
  120 N applied vs 15 N published limit — 8.0× over
  Bearing F8-16M would carry this: rated 4990 N static against 240 N required.
```

That same case used to answer *"1.00 mm — process minimum wall"*, which reads
as an all-clear while the motor is being destroyed.

---

## The problem this solves

Text-to-CAD will happily turn a sentence into geometry. Two things it won't do:

1. **Know your engineering constraints.** "A motor mount" doesn't encode the
   NEMA 17 bolt pattern, and it certainly doesn't know that the motor you're
   bolting it to is rated for 28N of side load.
2. **Tell you whether it got it right.** You get geometry back. Whether it
   matches what you asked for is your problem.

ZooMounter closes both, for one narrow, real task.

## How it works

1. **You give an engineering spec** — mount type, material, load *and load
   type*, safety factor.
2. **A domain-rules layer computes the actual numbers.** The hole pattern comes
   from the real hardware standard, and the shaft load is checked against the
   motor's published rating (see [Sizing](#sizing-what-the-numbers-mean)).
3. **Those exact numbers go into the Agent API prompt** — every hole as an
   explicit `(x, y)` coordinate, never "add some mounting holes." This is also
   why square NEMA faceplates, circular bolt circles and rectangular patterns
   need no special-casing: they're all just coordinates by the time the prompt
   is built. What they are *not* is interchangeable upstream of that, which is
   why the catalogue makes each declare its type.
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
zoomounter --mount nema23 --material aluminum_6061 --shaft-load-n 120 --load-type axial --add xMotorMount

# or, from anywhere, by naming the project:
zoomounter --mount nema23 --material aluminum_6061 --shaft-load-n 120 --add xMotorMount --add-to ~/projects/my-gantry
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
pip install -e .            # CLI + GUI
pip install -e ".[mcp]"     # ...and the MCP server
pip install -e ".[dev]"     # ...and pytest, to run the test suite
```

That puts `zoomounter` and `zoomounter-gui` on your PATH, plus
`zoomounter-mcp` with the `mcp` extra. The GUI needs no extra — `customtkinter`
and `pillow` are core dependencies.

**Keep the `-e`.** Without it, pip copies the package into site-packages and
that copy never changes again. You then get the confusing failure where
running from inside the repo picks up your edits and running from anywhere
else silently uses a months-old snapshot — same command, two different
programs, no error either way. If a fix you know you made isn't showing up,
check which copy is loaded:

```bash
python -c "import zoomounter; print(zoomounter.__file__)"
```

It should print a path inside this repo. If it prints something under
`site-packages`, re-run the install above.

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
python -m zoomounter.cli --mount nema17 --material aluminum_6061 --shaft-load-n 150 --safety-factor 2

# interactive — run with no flags and answer the prompts
python -m zoomounter.cli

# desktop GUI
python -m zoomounter.gui
```

The GUI adds a live shaft-load check as you type (instant, no API cost), a
rendered preview of the generated part, and an **"Export STEP + verify"**
toggle — uncheck it for a fast preview-only run that skips the STEP export and
verification.

### Working without credits, or without the Zoo CLI

Two flags exist because not every question needs a generation:

```bash
# Size it and print the text-to-CAD prompt. No network, no credits, no Zoo CLI.
# Paste the result straight into Zoo Design Studio's chat.
python -m zoomounter.cli --mount nema23 --material aluminum_6061 \
  --shaft-load-n 200 --safety-factor 2.5 --print-prompt

# Generate the Zoo project but skip STEP export and verification.
# Removes the Zoo CLI dependency entirely — open the folder in Design Studio.
python -m zoomounter.cli --mount nema17 --material petg --shaft-load-n 40 --no-export
```

### Where runs go, and how you get a part out

Runs go to **`~/.zoomounter/runs/`** (or `$ZOOMOUNTER_HOME/runs`, or
`--runs-dir PATH`) — never to the folder you are standing in. Each gets its own
timestamped subfolder with `main.kcl`, `export/output.step`, `preview.png`,
`inspection_report.md` and an `assembly/` you can open in Design Studio.

Those are working files. To get a part into a real design:

```bash
zoomounter --deliver ~/.zoomounter/runs/nema23_aluminum_6061_20260805_143022 --to ~/projects/my-gantry --name xMotorMount
```

That writes three things into the destination:

| File | For |
|---|---|
| `xMotorMount.kcl` | Zoo — **with the `export` line appended**, which the Agent API does not emit and which `import` requires |
| `xMotorMount.step` | Fusion, SolidWorks, Onshape, FreeCAD, anything |
| `HOW-TO-USE.md` | What to do with them, and what ZooMounter did *not* do |

If the destination is a Zoo project, the `import` line is added to its
`main.kcl` too. Delivering costs nothing and generates nothing, so the same run
can go into as many projects as you like.

**`HOW-TO-USE.md` states two things the tool cannot do**, because an unstated
limitation reads as a solved problem. The part is placed at the **origin** —
ZooMounter has no idea where it belongs in your machine, and KCL has no mate or
constraint system to express that with (finding #11 in
[NOTES-FOR-ZOO.md](NOTES-FOR-ZOO.md)). And the motor and bearing bodies in
`assembly/` are catalogue *context*, not models of your specific hardware. It
also repeats the run's shaft verdict — a delivered part that arrives without its
warnings defeats the point of having computed them.

Old runs are pruned to the most recent 5 (`--keep-runs N`, `--no-prune`), and
the exploded assembly is dropped once its preview exists. Pruning only ever
touches ZooMounter's own workspace — never a `--runs-dir` you supplied, and
never anything inside a git repo.

### Mount types

| Key | Description | Hole pattern |
|---|---|---|
| `nema17` | NEMA 17 stepper motor | **square, 31mm spacing** |
| `nema23` | NEMA 23 stepper motor | **square, 47.14mm spacing** |
| `bearing_608` | 608 (skate) bearing | circular, 34mm bolt circle |
| `custom` | your own circular pattern via `--plate-width-mm`, `--bolt-count`, `--bolt-circle-dia-mm`, `--bolt-hole-dia-mm`, `--center-hole-dia-mm` | circular |

### Materials

| Key | Process |
|---|---|
| `pla`, `petg`, `abs` | 3D printed |
| `aluminum_6061`, `mild_steel` | machined |
| `custom` | your own via `--density-kg-m3`, `--youngs-modulus-gpa`, `--yield-mpa`, `--process` |

## Sizing: what the numbers mean

**The primary answer is not a thickness. It's whether your motor's shaft can
take the load at all.**

A stepper's shaft runs in two small internal bearings, and their published
limits are far lower than people expect — a NEMA 17 is rated **28 N radial and
10 N axial**. Those limits are the real constraint on a motor mount, and no
amount of bracket changes them. So ZooMounter checks the load against them
first and reports one of:

| Verdict | Meaning |
|---|---|
| `SHAFT OK` | Within the published rating, with margin. |
| `BEARING RECOMMENDED` | Above 70% of an absolute maximum quoted with no margin. |
| `BEARING REQUIRED` | Over the rating. The motor is the limit, not the plate. |
| `NOT CHECKED` | No published rating on file. Not a pass. |

When a bearing is needed it names one: *"F8-16M would carry this: rated 4990 N
static against 240 N required"* — and gives you the command to generate it.

> ### The load model is rudimentary. Read this before trusting a verdict.
>
> The comparison above is careful. **What it compares is not.**
>
> `--shaft-load-n` is a number you type. There is no torque, speed, duty cycle
> or transmission geometry to derive it from — deriving it was cut deliberately
> rather than done badly, because turning your honest guess into an
> authoritative-looking number inherits the same failure this whole project is
> about. So the verdict is a check on *the number you supplied*, not on your
> machine.
>
> Three specific limits worth knowing:
>
> - **One load, one direction, one run.** Radial and axial cannot be combined.
>   Run both and take the worse case.
> - **The default offset is 15 mm and no mount overrides it**, while every
>   radial rating on file is measured at 20 mm. Pass `--overhang-mm` with the
>   real distance or the default is a placeholder standing in for a measurement.
> - **`--safety-factor` does not reach the shaft verdict.** It applies to
>   bearing selection only, so a load at 99% of a published maximum reads
>   `SHAFT OK` however high you set it.
>
> This covers a narrow set of cases well and needs substantial work before it is
> usable by everyone. It is declared in the rule registry as
> `load_model_is_rudimentary` and printed in every inspection report, so the
> caveat travels with the output rather than living only here.

### Two loads, not one

- **`--shaft-load-n`** — acts at the shaft (belt, gear, leadscrew). This is what
  gets checked, and what a bearing can bypass.
- **`--plate-load-n`** — bolted to the bracket (a camera, a sensor). It never
  reaches the shaft, so it is never compared against a shaft rating.

They used to be one flag, which meant bolting a camera to the plate reported a
shaft overload that cannot physically happen.

### Radial loads are compared as moments

A radial rating is quoted *at a stated distance from the flange* — 28 N **at
20 mm** for a NEMA 17 — because what it protects is the front bearing, and a
side load's severity there scales with how far out it acts. So both sides are
converted to a moment before being compared. 28 N at 40 mm is twice the rated
demand while a bare force comparison calls it a pass; 30 N at 10 mm is
comfortably inside it while a bare force comparison fails it. Pass
`--overhang-mm` if you know the real distance.

### Putting a bearing in: two topologies, and they are different parts

`--bearing-topology` is how a bearing actually takes the load off the motor.
There are two ways, and only one of them fully works:

| | `stub-shaft` *(recommended)* | `direct` |
|---|---|---|
| What turns in the bearing | its own short stub shaft | the motor's own shaft |
| Motor | stands off on spacers, drives through a flexible coupling | bolts flat to the plate |
| Load reaching the motor | none — torque only | partial at best |
| Thrust | works | **almost nothing** — a plain stepper shaft has no shoulder to push against |
| Plate (NEMA 17 + 625) | 5 mm | 9 mm — boss recess and seat stack on opposite faces |

`direct` is offered because it's what most hobby builds do, but it is
overconstrained against the motor's own front bearing, and on a NEMA 17 the
22 mm pilot boss versus a 16 mm bearing means there's no material gripping the
outer ring concentrically. ZooMounter builds it if you ask and says all three
things while doing it.

```bash
python -m zoomounter.cli --mount nema17 --material aluminum_6061 \
  --shaft-load-n 40 --load-type radial --bearing-topology stub-shaft
```

The standoff is not arbitrary: a standard D18–D19 × L25 aluminium flexible
coupling is 25 mm long, so the motor face sits 30 mm off the plate. The
generated assembly draws the coupling, and you can see the motor's own shaft
stop inside it — never reaching the plate, which is the entire claim.

### Thickness is a manufacturing floor

It comes from two candidates — the minimum wall your process can produce, and
the depth a bearing needs to seat in — and the larger wins. It is not a
structural result, and the report says so.

ZooMounter used to size the plate against the load with a bending-stress calc,
an L/300 deflection limit, screw-head punching shear and a fastener-tension
check. All four were removed, because **none of them ever governed**: for every
part in scope the structural requirement lands below the process floor, so a
thickness quoted to two decimals from a named beam formula was the process floor
wearing a calculation's clothes. Deleting them is the honest result — see
`docs/mechanics.html` for the full reasoning.

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
you open this folder. It calls the `zoomounter-mcp` console script rather than
naming an interpreter, so it works from a clone with no editing — as long as
the environment you installed into is the one on your PATH. If the server
fails to start, that's the thing to check.

**Gemini CLI** — add the same block to `~/.gemini/settings.json`.

Both need `ZOO_API_TOKEN` in `.env` for the tools that call the API. The three
free tools work without it.

## Running it as a `zoo` subcommand

The Zoo CLI has no plugin system, but `zoo alias` supports shell expansions,
which gets you the same thing:

```bash
zoo alias set zoomounter '!cd /path/to/ZooMounter && python -m zoomounter.cli "$@"'
zoo zoomounter --mount nema17 --material petg --shaft-load-n 40
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

Every limitation below is declared in
[`zoomounter/data/rules.toml`](zoomounter/data/rules.toml) with its reasoning,
and printed in every inspection report. See [RULES.md](RULES.md).

- **ZooMounter does not size plates against loads.** It checks shaft ratings and
  sizes for manufacturability and bearing fit. If your case is genuinely
  structural, this is the wrong tool.
- **Reaction torque is not modelled** — and it is present whenever the motor
  runs, at zero external load, reacted as shear in the bolt pattern. A NEMA 23 at
  1.9 N·m puts roughly 28 N on each bolt, fully reversing on a bidirectional axis.
- **Fatigue, impact and belt pretension are not modelled.** A reversing axis
  loads its bracket cyclically at a level a static check calls safe; a hard stop
  can exceed the running load by an order of magnitude; pretension loads the
  shaft before any useful work is done.
- **Thermal creep is not modelled.** A NEMA case runs at 70–80 °C and PLA softens
  below that, so a printed bracket bolted straight to a motor can sag in service
  at a load it held when new.
- **One load type per run.** A real mount often sees radial and axial load
  simultaneously; this doesn't combine them. Run both and take the worse case.
- **Motor mass and shaft offset are representative values** for a typical
  NEMA 17/23, not your specific motor's datasheet. Override the offset with
  `--overhang-mm` if you know the real dimension — for radial loads it directly
  scales the demand.
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
