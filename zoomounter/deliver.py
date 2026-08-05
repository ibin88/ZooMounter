"""Hand a finished run to the user in a form they can actually use.

Generating a part and leaving it in a folder is not delivery. The user still has
to find it, work out which of the four KCL files is the mount, append an
`export` line the Agent API never wrote, and then guess where the part goes in
their assembly. The GUI did not even offer `--add`; it generated a part and
stopped.

So: two targets, because Zoo is not the only CAD tool anyone uses.

    <name>.kcl    the mount, with its `export` line, importable as a module
    <name>.step   for Fusion, SolidWorks, Onshape, FreeCAD, anything
    HOW-TO-USE.md what to do with them, and what this tool did NOT do

## Why HOW-TO-USE.md is the part that matters

A delivered part that arrives without its warnings defeats the point of having
computed them. If the run said BEARING REQUIRED, that has to travel with the
file -- otherwise the tool's whole contribution is a plate someone bolts on
while the finding sits in a terminal they have closed.

It also states two things the tool cannot do, because an unstated limitation
reads as a solved problem:

**The part is at the origin.** ZooMounter does not know where it goes in your
assembly, and KCL has no mate or constraint system to express it with --
positioning is manual `translate`/`rotate` arithmetic. That is a platform gap,
not a ZooMounter one, and it is written up as finding #11 in NOTES-FOR-ZOO.md.

**The reference bodies are not your hardware.** The motor and bearing drawn in
`assembly/` come from catalogue dimensions so the fit can be seen. They are not
models of anyone's specific part and must not end up in a real design.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import assembly as assembly_mod
from . import zoo_project
from .mechanics import Check

# Rule codes, declared in data/rules.toml.
DELIVERED_AT_ORIGIN = "delivered_at_origin"
REFERENCE_GEOMETRY = "reference_geometry_is_not_your_hardware"


class DeliveryError(RuntimeError):
    pass


@dataclass
class Delivered:
    kcl_path: Path
    step_path: Path | None
    guide_path: Path
    project: zoo_project.ZooProject | None  # set when the destination was a Zoo project
    entry_modified: bool = False
    checks: list[Check] = None  # warnings carried from the run

    def __post_init__(self):
        if self.checks is None:
            self.checks = []


def find_mount_kcl(run_dir: Path) -> Path:
    """The mount is the designed part; everything else in a run is context.

    `assembly/` holds four KCL files and only one of them is the thing that was
    verified, so guessing by glob would sometimes deliver a reference body as
    the deliverable. The run root's main.kcl is the mount, by construction.
    """
    candidate = run_dir / "main.kcl"
    if candidate.exists():
        return candidate
    raise DeliveryError(
        f"No main.kcl in {run_dir}. That folder does not look like a ZooMounter "
        f"run -- pass the run directory itself, not its parent or its assembly/."
    )


def find_step(run_dir: Path) -> Path | None:
    step = run_dir / "export" / "output.step"
    return step if step.exists() else None


def origin_placement_check() -> Check:
    """Stated on every delivery, not only when something is wrong.

    A limitation only mentioned in failure cases reads, in every other case, as
    a problem that does not apply.
    """
    return Check(
        level="WARN",
        message=(
            "The part is placed at the ORIGIN. ZooMounter does not know where it "
            "belongs in your assembly, and KCL has no mate or constraint system "
            "to express that with -- assembly positioning is manual translate/"
            "rotate arithmetic."
        ),
        source=(
            "Zoo KCL has sketch constraints but no assembly mates; it is an open "
            "roadmap item with a public spec."
        ),
        remedy=(
            "Position it yourself with |> translate(x, y, z) and |> rotate(...) "
            "in main.kcl. The import line ZooMounter adds is deliberately "
            "un-positioned rather than guessing."
        ),
        code=DELIVERED_AT_ORIGIN,
    )


def reference_geometry_check() -> Check:
    return Check(
        level="WARN",
        message=(
            "The motor and bearing bodies in assembly/ are drawn from catalogue "
            "dimensions for context only. They are NOT models of your specific "
            "hardware and must not be imported into a real design."
        ),
        source=(
            "They are derived entirely from fields in data/mounts.toml and "
            "data/bearings.toml -- frame size, shaft diameter, bore and width. "
            "Nothing else about anyone's motor is known to this tool."
        ),
        remedy=(
            "Get the manufacturer's STEP for the actual part. Only the mount "
            "itself is verified against its spec."
        ),
        code=REFERENCE_GEOMETRY,
    )


def _guide_markdown(
    name: str,
    project: zoo_project.ZooProject | None,
    entry_modified: bool,
    step_path: Path | None,
    checks: list[Check],
    assembly_files: list[str] | None = None,
    params_name: str | None = None,
) -> str:
    is_assembly = bool(assembly_files)
    what = "assembly" if is_assembly else "part"
    import_line = (
        f'import "{name}.kcl" as {name}' if is_assembly
        else f'import {name} from "{name}.kcl"'
    )

    lines = [
        f"# How to use `{name}`",
        "",
        f"Generated by ZooMounter. This is a whole {what}, not just a plate — "
        f"the run worked out the bearing, the standoff height and the coupling, "
        f"so all of it comes across together."
        if is_assembly
        else "Generated by ZooMounter.",
        "",
    ]

    if is_assembly:
        lines += [
            "## What is here",
            "",
            f"| File | What it is |",
            "|---|---|",
            f"| `{name}.kcl` | The assembly. This is the one you import. |",
            f"| `{params_name}` | **Where it sits.** Three numbers; change them and everything moves together. |",
        ]
        for f in sorted(assembly_files):
            if f == params_name:
                continue
            role = (
                "The designed part — generated and verified against its spec."
                if "mount" in f.lower()
                else "Reference geometry, from catalogue dimensions. **Not your hardware.**"
            )
            lines.append(f"| `{f}` | {role} |")
        if step_path is not None:
            lines.append(
                f"| `{step_path.name}` | The verified mount, for non-Zoo CAD. |"
            )
        lines.append("")

    lines += ["## In a Zoo project", ""]
    if project is not None and entry_modified:
        lines += [
            f"Already done. The {what} was written into `{project.name}` and "
            f"imported from `{project.entry.name}` — reload the project in "
            f"Design Studio and it is there.",
            "",
        ]
    elif project is not None:
        lines += [
            f"`{name}.kcl` is in `{project.name}` and `{project.entry.name}` "
            f"already imports it, so nothing was changed. Reload to pick up the "
            f"new geometry.",
            "",
        ]
    else:
        lines += [
            "The destination is not a Zoo project, so nothing was wired up. Copy "
            f"{'these files' if is_assembly else f'`{name}.kcl`'} to your project "
            "root and add this to `main.kcl`:",
            "",
            "```kcl",
            import_line,
            "",
            name,
            "```",
            "",
        ]

    if step_path is not None:
        lines += [
            "## In any other CAD tool",
            "",
            f"Import `{step_path.name}`. STEP opens in Fusion, SolidWorks, "
            "Onshape, FreeCAD, Inventor and everything else that reads a solid.",
            "",
        ]

    lines += ["## Where it goes", ""]
    if is_assembly:
        lines += [
            f"**Everything is at the origin.** Open `{params_name}` and change "
            "three numbers:",
            "",
            "```kcl",
            "export asmX = 120",
            "export asmY = 0",
            "export asmZ = 45",
            "```",
            "",
            "Every body reads those, so the whole assembly moves together — "
            "mount, motor, bearing, standoffs and coupling stay in the right "
            "relationship to each other. That is as close to a mate as KCL "
            "currently gets.",
            "",
            "**Do not try to move it by translating the import.** "
            f"`{name} |> translate(...)` moves only the LAST body in the module "
            "and silently leaves the rest at the origin — no error, no warning. "
            "That is measured, not assumed, and it is finding #12 in "
            "`NOTES-FOR-ZOO.md`.",
            "",
        ]
    else:
        lines += [
            "**The part is at the origin.** ZooMounter sized it and verified its "
            "geometry; it has no idea where it sits in your machine. KCL has no "
            "mate or constraint system, so positioning is manual arithmetic:",
            "",
            "```kcl",
            f"{name}",
            "  |> translate(x = 0, y = 0, z = 0)",
            "```",
            "",
        ]
    lines += [
        "ZooMounter does not guess a transform. It knows the plate's bolt "
        "pattern and thickness; it does not know your machine. A guessed "
        "position is harder to find and fix than an obvious zero. The missing "
        "mate system is a Zoo platform gap rather than a ZooMounter one — see "
        "finding #11 in `NOTES-FOR-ZOO.md`.",
        "",
        "## What is NOT your hardware",
        "",
        "The motor and bearing bodies are drawn from catalogue dimensions — "
        "frame size, shaft diameter, bore and width — so you can see the fit and "
        "check the bolt patterns line up. They are **not** models of your "
        "specific parts, and nothing else about your motor is known to this "
        "tool. Only the mount was verified against a spec.",
        "",
        "When you move from checking fit to building the real thing, delete "
        "those files and import the manufacturer's STEP instead. The assembly "
        "file will still render; it just loses the context bodies.",
        "",
    ]

    warnings = [c for c in checks if c.level in ("WARN", "LOUD WARN")]
    if warnings:
        lines += [
            "## Warnings from this run",
            "",
            "These were produced when the part was sized. They travel with the "
            "file because a delivered part that arrives without them is exactly "
            "the failure this tool exists to prevent.",
            "",
        ]
        for c in warnings:
            lines.append(f"- **[{c.level}]** {c.message}")
            if c.source:
                lines.append(f"  - *Source*: {c.source}")
            if c.remedy:
                lines.append(f"  - *What to do*: {c.remedy}")
        lines.append("")

    return "\n".join(lines)


def _prefixed(name: str, part: str) -> str:
    """`xMotorMount` + `mount` -> `xMotorMountMount.kcl`, flat at the root.

    Flat and prefixed rather than a subfolder, because Zoo projects put every
    part in a file at the project root and nothing documents whether a
    subdirectory import resolves. Prefixing is the arrangement that is certain
    to work AND lets two delivered assemblies coexist -- an unprefixed
    `parameters.kcl` from a second delivery would silently overwrite the first
    and move an assembly nobody touched.
    """
    return f"{name}{part[:1].upper()}{part[1:]}.kcl"


def deliver_assembly(
    run_dir: Path,
    dest: Path,
    name: str,
    checks: list[Check] | None = None,
    comment: str = "",
) -> Delivered:
    """Hand over the whole assembly, not just the plate.

    The mount on its own is the part ZooMounter designed, but it is not the
    thing the user was working on. They asked for a motor mounted somewhere,
    and the run already worked out the bearing, the standoff height, the
    coupling and where the shaft ends up. Delivering one plate out of that
    throws away every decision except the outline.

    So the whole set goes across, sharing a parameters file that positions all
    of it. Change three numbers and the assembly moves together -- which is as
    close to a mate as KCL currently gets.
    """
    src = run_dir / "assembly"
    if not src.is_dir():
        raise DeliveryError(
            f"No assembly/ in {run_dir}. Deliver the mount alone with "
            f"assembly=False, or re-run without --no-assembly."
        )

    dest.mkdir(parents=True, exist_ok=True)
    params_name = _prefixed(name, "parameters")

    # Part files first, with the shared-parameter import repointed at the
    # prefixed copy so two deliveries into one project cannot collide.
    written = []
    for part_file in sorted(src.glob("*.kcl")):
        if part_file.name in ("main.kcl", assembly_mod.PARAMS_FILE):
            continue
        text = part_file.read_text(encoding="utf-8").replace(
            f'"{assembly_mod.PARAMS_FILE}"', f'"{params_name}"'
        )
        target = dest / _prefixed(name, part_file.stem)
        target.write_text(text, encoding="utf-8")
        written.append((part_file.stem, target))

    (dest / params_name).write_text(
        (src / assembly_mod.PARAMS_FILE).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # The assembly file, rewritten to import the prefixed parts.
    asm_text = (src / "main.kcl").read_text(encoding="utf-8")
    for stem, target in written:
        asm_text = asm_text.replace(f'"{stem}.kcl"', f'"{target.name}"')
    asm_path = dest / f"{name}.kcl"
    asm_path.write_text(asm_text, encoding="utf-8")

    step_src = find_step(run_dir)
    step_path = None
    if step_src is not None:
        step_path = dest / f"{name}.step"
        step_path.write_bytes(step_src.read_bytes())

    carried = list(checks or [])
    carried.append(origin_placement_check())
    carried.append(reference_geometry_check())

    project = zoo_project.find_project(dest)
    entry_modified = False
    if project is not None:
        entry_modified = _import_assembly(project, name, comment)

    guide_path = dest / "HOW-TO-USE.md"
    guide_path.write_text(
        _guide_markdown(
            name, project, entry_modified, step_path, carried,
            assembly_files=[p.name for _, p in written] + [params_name],
            params_name=params_name,
        ),
        encoding="utf-8",
    )
    return Delivered(
        kcl_path=asm_path,
        step_path=step_path,
        guide_path=guide_path,
        project=project,
        entry_modified=entry_modified,
        checks=carried,
    )


def _import_assembly(project, name: str, comment: str) -> bool:
    """Whole-file import, which is how a multi-body assembly gets instantiated.

    `import x from "x.kcl"` needs a single exported body and would bring in one
    part out of five. Zoo's own axial-fan sample uses the whole-file form for
    exactly this reason.
    """
    entry = project.entry
    if not entry.exists():
        raise zoo_project.ProjectError(
            f"{entry.name} not found in the project, so there is nothing to "
            f"wire the assembly into. The files were still written."
        )
    text, newline = zoo_project._read_preserving_newlines(entry)
    text = text.replace("\r\n", "\n")
    import_line = f'import "{name}.kcl" as {name}'
    if import_line in text:
        return False

    import re

    imports = list(re.finditer(r"^import\s+.*$", text, re.MULTILINE))
    if imports:
        at = imports[-1].end()
        text = text[:at] + "\n" + import_line + text[at:]
    else:
        settings = re.search(r"^@settings\([^)]*\)\s*$", text, re.MULTILINE)
        at = settings.end() if settings else 0
        text = text[:at] + "\n\n" + import_line + text[at:]

    block = f"\n\n// {comment}\n" if comment else "\n\n"
    block += (
        f"// Generated by ZooMounter. Position the whole assembly by editing\n"
        f"// {name}Parameters.kcl -- every body reads it.\n{name}\n"
    )
    text = text.rstrip() + block
    zoo_project._write_preserving_newlines(entry, text, newline)
    return True


def deliver(
    run_dir: Path,
    dest: Path,
    name: str = "zooMount",
    checks: list[Check] | None = None,
    comment: str = "",
    assembly: bool = True,
) -> Delivered:
    """Write the assembly, the STEP and the guide into `dest`.

    Defaults to the whole assembly rather than the bare plate. The run already
    decided the bearing, the standoff height and the coupling; handing over one
    plate out of that throws away every decision except the outline, and leaves
    the user to reconstruct the context the tool just computed.

    `assembly=False` delivers the mount alone, for when the plate genuinely is
    the whole job.

    If `dest` is inside a Zoo project, it is imported from that project's entry
    file -- the same thing `--add` has always done, now reachable from anywhere
    and from the GUI.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    dest = Path(dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    if assembly and (run_dir / "assembly").is_dir():
        return deliver_assembly(run_dir, dest, name, checks=checks, comment=comment)

    kcl_code = find_mount_kcl(run_dir).read_text(encoding="utf-8")
    step_src = find_step(run_dir)

    carried = list(checks or [])
    carried.append(origin_placement_check())
    carried.append(reference_geometry_check())

    project = zoo_project.find_project(dest)
    entry_modified = False

    if project is not None:
        # Reuse add_part rather than reimplementing the export convention --
        # it already knows how to name the final assignment and how to leave
        # someone else's line endings alone.
        kcl_path, entry_modified = zoo_project.add_part(
            project, name, kcl_code, comment=comment
        )
    else:
        kcl_path = dest / f"{name}.kcl"
        kcl_path.write_text(
            zoo_project.wrap_as_module(kcl_code, name), encoding="utf-8"
        )

    step_path = None
    if step_src is not None:
        step_path = dest / f"{name}.step"
        step_path.write_bytes(step_src.read_bytes())

    guide_path = dest / "HOW-TO-USE.md"
    guide_path.write_text(
        _guide_markdown(name, project, entry_modified, step_path, carried),
        encoding="utf-8",
    )

    return Delivered(
        kcl_path=kcl_path,
        step_path=step_path,
        guide_path=guide_path,
        project=project,
        entry_modified=entry_modified,
        checks=carried,
    )


def write_run_guide(run_dir: Path, checks: list[Check] | None = None) -> Path:
    """A guide inside the run folder itself.

    The delivery guide cannot exist until you pick a destination -- the part
    name comes from --name, and "already imported into main.kcl" depends on
    whether the destination turned out to be a Zoo project. But that left a
    browsable folder of runs with nothing explaining what any of them were,
    which is the same dead end the deliver step was built to remove, moved one
    step earlier.

    So: a generic guide here, a specific one at delivery.
    """
    run_dir = Path(run_dir)
    warnings = [c for c in (checks or []) if c.level in ("WARN", "LOUD WARN")]

    lines = [
        f"# {run_dir.name}",
        "",
        "A finished ZooMounter run. These are **working files** — the part is "
        "here, but nothing has been delivered anywhere yet.",
        "",
        "## Get it into a project",
        "",
        "```bash",
        f"zoomounter --deliver {run_dir} --to <your-project> --name xMotorMount",
        "```",
        "",
        "That writes the whole assembly — mount, motor, bearing and any "
        "standoffs — plus a STEP of the verified mount and a HOW-TO-USE.md "
        "written for that destination. If the destination is a Zoo project it "
        "is imported into `main.kcl` too. Delivering costs nothing and "
        "generates nothing, so the same run can go into as many projects as "
        "you like.",
        "",
        "## What is in here",
        "",
        "| Path | What it is |",
        "|---|---|",
        "| `main.kcl` | The mount, exactly as generated and verified. |",
        "| `export/output.step` | The same part, for non-Zoo CAD. |",
        "| `inspection_report.md` | The sizing decisions and every check. **Start here.** |",
        "| `preview.png` | Rendered from the generated KCL — evidence, not an illustration. |",
        "| `assembly/` | Mount plus context bodies, to scale, openable in Design Studio. |",
        "",
        "## Two things this run does NOT tell you",
        "",
        "**Where it goes.** Everything sits at the origin. ZooMounter sized and "
        "verified the mount; it has no idea where the assembly belongs in your "
        "machine, and KCL has no mate or constraint system to express that "
        "with. After delivery, the position lives in one parameters file.",
        "",
        "**Which bodies are real.** Only the mount was verified against a spec. "
        "The motor and bearing in `assembly/` are drawn from catalogue "
        "dimensions so you can check the fit — they are not models of your "
        "specific hardware. Use the manufacturer's STEP for those.",
        "",
    ]

    if warnings:
        lines += [
            "## Warnings from this run",
            "",
        ]
        for c in warnings:
            lines.append(f"- **[{c.level}]** {c.message}")
            if c.remedy:
                lines.append(f"  - *What to do*: {c.remedy}")
        lines.append("")

    path = run_dir / "HOW-TO-USE.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def paste_instructions(name: str, has_assembly: bool = True) -> str:
    """What to do with what was just copied, shown in the GUI rather than only
    in a file the user has not opened.

    Says plainly that this is the MOUNT ALONE. The clipboard is a single-file
    channel and Design Studio's editor edits one file at a time, so the
    assembly -- five files sharing a parameters file -- cannot travel this way.
    A button that quietly hands over a fifth of the deliverable next to one
    that hands over all of it is the kind of gap someone only notices after
    wiring the wrong thing into their machine.
    """
    lines = [
        "COPIED: the mount plate only.",
        "",
    ]
    if has_assembly:
        lines += [
            "This run also produced a motor, a bearing and their positions.",
            "None of that is on the clipboard -- an assembly is five files",
            "sharing a parameters file, and the clipboard carries one.",
            "For the whole thing, use \"Add to project...\" instead.",
            "",
        ]
    lines += [
        "-" * 58,
        "TO USE WHAT WAS COPIED",
        "-" * 58,
        "",
        f"1. In Design Studio, create a new file called  {name}.kcl",
        "2. Paste. The export line is already in it -- that is the part",
        "   the Agent API never writes and the import below needs.",
        "3. Save.",
        "4. Add these two lines to your project's main.kcl:",
        "",
        f'      import {name} from "{name}.kcl"',
        "",
        f"      {name}",
        "",
        "5. Reload the project.",
        "",
        "The part lands at the ORIGIN. ZooMounter sized and verified it but",
        "does not know where it belongs in your machine, so position it",
        "yourself:",
        "",
        f"      {name}",
        "        |> translate(x = 0, y = 0, z = 0)",
        "",
    ]
    return "\n".join(lines)


def kcl_for_clipboard(run_dir: Path, name: str = "zooMount") -> str:
    """The mount's KCL with its export line, ready to paste into Design Studio.

    Deliberately the GENERATED KCL rather than the prompt. Pasting a prompt into
    Zookeeper regenerates the part: different geometry, unverified, and not
    repeatable. Avoiding exactly that is why this tool exists.
    """
    return zoo_project.wrap_as_module(
        find_mount_kcl(Path(run_dir)).read_text(encoding="utf-8"), name
    )
