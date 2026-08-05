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
) -> str:
    lines = [
        f"# How to use `{name}`",
        "",
        "Generated by ZooMounter. Two files, because Zoo is not the only CAD "
        "tool you might be opening this in.",
        "",
    ]

    lines += ["## In a Zoo project", ""]
    if project is not None and entry_modified:
        lines += [
            f"Already done. `{name}.kcl` was written into `{project.name}` and "
            f"imported from `{project.entry.name}` — reload the project in "
            f"Design Studio and it is in your assembly.",
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
            f"`{name}.kcl` to your project root and add this line to `main.kcl`:",
            "",
            "```kcl",
            f'import {name} from "{name}.kcl"',
            "",
            name,
            "```",
            "",
            "The `export` line the import needs is already in the file.",
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

    lines += [
        "## Where it goes",
        "",
        "**The part is at the origin.** ZooMounter sized it and verified its "
        "geometry; it has no idea where it sits in your machine. KCL has no mate "
        "or constraint system, so assembly positioning is manual arithmetic:",
        "",
        "```kcl",
        f"{name}",
        "  |> translate(x = 0, y = 0, z = 0)",
        "```",
        "",
        "This is a Zoo platform gap rather than a ZooMounter one — see finding "
        "#11 in `NOTES-FOR-ZOO.md`. The import is left un-positioned on purpose: "
        "a guessed transform is harder to find and fix than an obvious zero.",
        "",
        "## What is NOT your hardware",
        "",
        "If you also opened `assembly/`, the motor and bearing bodies in it are "
        "drawn from catalogue dimensions so you can see the fit. They are **not** "
        "models of your specific parts. Only the mount was verified against a "
        "spec. Use the manufacturer's STEP for the real components.",
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


def deliver(
    run_dir: Path,
    dest: Path,
    name: str = "zooMount",
    checks: list[Check] | None = None,
    comment: str = "",
) -> Delivered:
    """Write the mount, the STEP and the guide into `dest`.

    If `dest` is inside a Zoo project, the part is also imported from that
    project's entry file -- the same thing `--add` has always done, now reachable
    from anywhere and from the GUI.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    dest = Path(dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

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


def kcl_for_clipboard(run_dir: Path, name: str = "zooMount") -> str:
    """The mount's KCL with its export line, ready to paste into Design Studio.

    Deliberately the GENERATED KCL rather than the prompt. Pasting a prompt into
    Zookeeper regenerates the part: different geometry, unverified, and not
    repeatable. Avoiding exactly that is why this tool exists.
    """
    return zoo_project.wrap_as_module(
        find_mount_kcl(Path(run_dir)).read_text(encoding="utf-8"), name
    )
