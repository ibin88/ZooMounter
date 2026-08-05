# Agent brief — ZooMounter

Short, structured orientation for an AI agent working in this repo. Human-facing docs are `README.md`,
`docs/mechanics.html` and `NOTES-FOR-ZOO.md`. The full project handoff (including work queue and
decisions) lives one directory up, in `../HANDOFF.md`.

## What this repo does

Generates mounting plates for parts governed by a **shaft load** — motors, bearing housings, idlers,
tensioners. Spec in → domain-rules layer answers the shaft question and computes the bolt pattern →
exact numbers into Zoo's text-to-CAD Agent API → returned KCL executed to STEP via Zoo's CLI → verified
by parsing hole coordinates back out of the STEP file locally.

**The primary output is not a thickness.** It is whether the load exceeds what the component's shaft is
rated for, and therefore whether it needs to bypass the motor into a bearing. Thickness is a
manufacturing floor (process minimum wall, or bearing seat depth) and is reported as such.

## Before changing anything

**Read `RULES.md`.** Every engineering claim the tool makes lives in `zoomounter/data/rules.toml` with a
source and a provenance `status`. Python evaluates rules; it does not own them. If you are adding a
claim, it goes in the registry — and if you are an AI agent, it goes in as `ai-proposed-unverified`,
which is quarantined and cannot govern a generated part. You do not promote your own status.

**`docs/mechanics.html` is history, not current behaviour.** It documents a structural load model that
has been removed; it opens with a banner saying so. Read it for the reasoning and for two of the four
original bugs, not for what the code does now.

**Understand the failure mode this project has.** Four bugs shipped past a green test suite *and* a
passing verifier. All were wrong domain assumptions, not broken code. The verifier compares the generated
part against the spec table — so when the table is wrong, the part is faithfully, verifiably wrong.
A passing test suite here is weaker evidence than usual.

## Rules

1. **Cite every domain constant, and its conditions.** New physical numbers must carry a source. An
   unsourced number is indistinguishable from a wrong one — that is literally how the NEMA bolt-pattern
   bug survived. A citation is necessary and not sufficient: a rating must also ship with the conditions
   it was measured under. A radial rating is a moment limit quoted at a distance, and the catalogue
   loader rejects one without `max_radial_at_mm`.
2. **Warn, never block.** When a check fails, the tool warns loudly and prominently — in the CLI, the
   GUI, the written report and the MCP payload — but still produces output. Locked product decision.
3. **A warning must carry a remedy.** "You are 1.8× over the motor's axial limit" is only half an
   answer; say what to do instead (here: add a thrust bearing so the load bypasses the motor).
4. **Keep magic numbers editable.** Defaults are fine; baked-in constants the user cannot override
   are not.
5. **Prefer the free paths while iterating.** Generation takes 1–3 minutes and costs credits.
   `--print-prompt`, `size_mount`, `build_prompt`, `inspect_step_file` and the test suite are free
   and instant.
6. **Preserve file formatting when editing user projects.** `zoo_project.py` reads and writes with
   `newline=''` specifically so adding one import doesn't rewrite every line of someone's file.
7. **A `Check.code` must name a rule in `rules.toml`.** This is enforced by `tests/test_rules.py`, so a
   message the user sees can always be traced back to a claim with a source and a status.
8. **Deleting a calculation is a valid change.** The structural layer was removed because it never
   governed. Do not re-add a calculation on the grounds that a tool ought to compute something.

## Commands

```bash
python -m pytest tests/ -q                     # 200 offline tests, no API, no credits
python -m zoomounter.cli --print-prompt ...    # size + prompt only, free, no Zoo CLI needed
python -m zoomounter.cli --no-export ...       # KCL project, skip STEP export and verification
python -m zoomounter.cli --add NAME ...        # write into the surrounding Zoo project
python -m zoomounter.gui
python -m zoomounter.mcp_server
```

## Module map

| File | Responsibility |
|---|---|
| `mount_specs.py` | mount table + hole-pattern helpers. **NEMA uses `square_bolt_pattern`, not circular** |
| `materials.py` | material properties, process minimum wall thickness |
| `mechanics.py` | `shaft_support()` — the primary answer. Thickness = manufacturing floors only |
| `rules.py` | rule registry: loads `data/rules.toml`, enforces the provenance quarantine |
| `catalogue.py` | loads and validates `data/*.toml`; a malformed row stops the import |
| `bearings.py` | bearing catalogue, load-driven selection, seat geometry |
| `assembly.py` | component + mount + bearing assembly KCL |
| `generate.py` | Agent API call, KCL project writing, STEP export, snapshot |
| `verify.py` | three checks — hole positions, bounding box, volume |
| `step_inspect.py` | local STEP parser: `CIRCLE → AXIS2_PLACEMENT_3D → CARTESIAN_POINT` |
| `zoo_project.py` | find surrounding Zoo project, wire parts into `main.kcl` |
| `cli.py` | argparse entry point, report writer |
| `gui.py` | customtkinter desktop app |
| `mcp_server.py` | 6 MCP tools, deliberately split by cost |
| `config.py` | `.env` discovery that works from any working directory |

## Zoo API facts worth not rediscovering

- `POST /ai/text-to-cad/{output_format}` returns **KCL source in `code`**, not a file. The documented
  `outputs` field never populates.
- Executing KCL into a file requires the **websocket** Engine API; we shell out to `zoo kcl export`.
- Generation takes 1–3 minutes; poll `GET /user/text-to-cad/{id}` until `status: completed`.
- Given exact numeric coordinates, the Agent API hits them **exactly** (sub-0.001 mm, verified). This is
  the core technique — never prompt vaguely.
- KCL assemblies: `import name from "name.kcl"`, each part file ends `export name = body`, flat files at
  the project root.
- A KCL file with no geometry fails with `engine: Nothing to export`.

## Non-goals

Do not add electronics/board/panel mounts, or load-derivation from torque/pulley geometry. Both were
considered and cut. The Raspberry Pi and VESA rows shipped for a while anyway and were actively harmful:
neither is governed by a shaft load, and the VESA row published neither a mass nor an offset, so the
loader's defaults sized a monitor bracket as though the screen weighed nothing. See `../HANDOFF.md` and
the note at the bottom of `data/mounts.toml`.

Do not re-add the structural sizing layer. See `mechanics.py`'s module docstring.
