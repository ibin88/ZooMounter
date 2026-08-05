"""ZooMounter CLI.

Usage (scripted):
    python -m zoomounter.cli --mount nema17 --material aluminum_6061 \\
        --load-n 5 --safety-factor 2

Usage (interactive): run with no flags and answer the prompts.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from . import generate, kcl_inspect, mechanics, verify, zoo_project
from .config import load_environment
from .materials import MATERIALS, get_material
from .mount_specs import EXTRUSION_SERIES, MOUNTS, get_mount
from .verify import POSITION_TOLERANCE_MM

console = Console()


def default_output_dir(mount_key: str, material_key: str, base: str = "output") -> Path:
    """Each run gets its own uniquely-named subfolder (mount + material +
    timestamp) so repeat runs don't overwrite each other's Zoo Design Studio
    project, and pointing the app at `base` shows a clean list of distinct
    generated parts instead of one folder that keeps getting replaced."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(base) / f"{mount_key}_{material_key}_{stamp}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zoomounter",
        description="Generate a mechanical mount from an engineering spec, then verify it against Zoo's File Format API.",
    )
    p.add_argument("--mount", choices=[*MOUNTS.keys(), "bearing", "custom"], help="Mount type. 'bearing' generates a bearing block sized around a bearing chosen from the load case.")
    p.add_argument("--material", choices=[*MATERIALS.keys(), "custom"], help="Plate material")
    p.add_argument(
        "--shaft-load-n",
        type=float,
        default=None,
        help="Load acting at the SHAFT, in newtons -- belt tension, gear mesh, leadscrew thrust. "
        "This is what gets checked against the component's published shaft rating, and what a "
        "bearing can be added to bypass.",
    )
    p.add_argument(
        "--plate-load-n",
        type=float,
        default=0.0,
        help="Load fastened to the BRACKET rather than applied at the shaft -- a camera, a sensor, "
        "a cable chain. Never reaches the shaft, so it is never compared to a shaft rating. "
        "Reported as unmodelled. (default: 0)",
    )
    p.add_argument(
        "--load-n",
        type=float,
        default=None,
        help=argparse.SUPPRESS,  # deprecated alias for --shaft-load-n
    )
    p.add_argument(
        "--load-type",
        choices=mechanics.LOAD_TYPES,
        default=None,
        help="Direction of the shaft load. 'radial' = side load (belt or pulley pulling sideways on "
        "the shaft). 'axial' = thrust along the shaft axis (leadscrew pushing into the motor). "
        "(default: radial)",
    )
    p.add_argument("--safety-factor", type=float, default=2.0, help="Safety factor, applied to bearing selection")
    p.add_argument(
        "--overhang-mm",
        type=float,
        default=None,
        help="How far from the mounting face the shaft load acts, in mm. This is not cosmetic: a "
        "radial rating is a moment limit quoted at a stated distance, so doubling this doubles the "
        "demand on the motor's front bearing. (default: the mount's shaft_load_offset_mm, 15mm)",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Where to write the generated files (default: a uniquely-named folder under ./output, e.g. output/nema17_aluminum_6061_20260724_143022)",
    )

    host_mount = p.add_argument_group("Host-side mounting options (Tier 1)")
    host_mount.add_argument("--host-mount", choices=["none", *EXTRUSION_SERIES, "corner-holes"], default=None, help="Add host mounting features")
    host_mount.add_argument("--host-slot-dir", choices=["parallel", "perpendicular"], default="parallel", help="Orientation of adjustment slots (default: parallel to Y)")
    host_mount.add_argument("--plate-width", type=float, default=None, help="Override auto-calculated overall plate width")
    p.add_argument(
        "--print-prompt",
        action="store_true",
        help="Run the sizing calc, print the Agent API prompt, and stop. No network calls, no credits, no Zoo CLI needed -- paste the prompt into Design Studio yourself.",
    )
    p.add_argument(
        "--literal-prompt",
        action="store_true",
        help="Ask for the part as fixed coordinates instead of named parameters and relationships. The result has the same dimensions but is not editable -- change one value in Design Studio and nothing else follows. Kept for comparison.",
    )
    p.add_argument(
        "--no-export",
        action="store_true",
        help="Generate the KCL project but skip the STEP export and verification. Removes the dependency on the Zoo CLI binary -- open the output folder in Design Studio and let the app do the rest.",
    )
    p.add_argument(
        "--add",
        metavar="PART_NAME",
        default=None,
        help="Drop the mount straight into the Zoo project you're standing in: writes PART_NAME.kcl "
        "alongside your other parts and adds the import to main.kcl. Finds the project by walking up "
        "from the current directory, so no paths needed. e.g. --add xMotorMount",
    )

    asm = p.add_argument_group("Assembly options")
    asm.add_argument(
        "--mounting-face", choices=["front", "back"], default="front",
        help="Which side of the plate the component body sits on. 'front' puts the body on +Z with the shaft passing down through the plate; 'back' flips it. Affects the assembly only, not the mount geometry.",
    )
    asm.add_argument(
        "--no-assembly", action="store_true",
        help="Write only the mount. By default ZooMounter also emits reference bodies for the component and bearing and an assembly you can open in Design Studio.",
    )

    bearing_group = p.add_argument_group("--mount bearing options")
    bearing_group.add_argument(
        "--shaft-dia-mm", type=float, default=None,
        help="Shaft the bearing must take. Defaults to the shaft on the named mount (NEMA 17 = 5mm, NEMA 23 = 8mm). Not the same as the centre hole, which clears the motor's pilot boss.",
    )
    bearing_group.add_argument(
        "--bearing", default=None,
        help="Force a specific bearing by designation (e.g. 608, 51101) instead of selecting one from the load case. A mismatch with the load type is warned about, not silently accepted.",
    )

    custom_mount = p.add_argument_group("--mount custom options")
    custom_mount.add_argument("--plate-width-mm", type=float)
    custom_mount.add_argument("--bolt-count", type=int)
    custom_mount.add_argument("--bolt-circle-dia-mm", type=float)
    custom_mount.add_argument("--bolt-hole-dia-mm", type=float)
    custom_mount.add_argument("--center-hole-dia-mm", type=float, default=None)

    custom_material = p.add_argument_group("--material custom options")
    custom_material.add_argument("--density-kg-m3", type=float)
    custom_material.add_argument("--youngs-modulus-gpa", type=float)
    custom_material.add_argument("--yield-mpa", type=float)
    custom_material.add_argument("--process", choices=["3d_print", "machined"])

    return p


def apply_defaults_noninteractive(args: argparse.Namespace) -> None:
    """Fill in anything the user didn't pass, using the documented defaults.

    Used when there's no terminal to prompt at -- a script, a CI job, or the
    MCP server. Without this, any omitted flag sends the CLI into an
    interactive prompt that immediately dies on EOF.
    """
    args.mount = args.mount or "nema17"
    args.material = args.material or "aluminum_6061"
    args.load_type = args.load_type or "radial"
    if args.shaft_load_n is None:
        args.shaft_load_n = 5.0
    args.host_mount = args.host_mount or "none"


def resolve_load_aliases(args: argparse.Namespace) -> None:
    """Fold the deprecated --load-n into --shaft-load-n.

    --load-n meant "expected load on the mount" and was then compared against
    the component's SHAFT rating -- one flag carrying two physical meanings.
    It maps to the shaft load because that is what it was actually measured
    against, so old invocations keep their old behaviour. The warning is not
    decoration: anyone who passed a bracket load to it was getting a shaft
    check they did not ask for, and needs to move it to --plate-load-n.
    """
    if args.load_n is None:
        return
    if args.shaft_load_n is not None:
        console.print(
            "[red]error:[/red] pass either --shaft-load-n or the deprecated "
            "--load-n, not both."
        )
        raise SystemExit(2)
    args.shaft_load_n = args.load_n
    console.print(
        "[yellow]--load-n is deprecated.[/yellow] It has been read as "
        "[bold]--shaft-load-n[/bold], which is what it was always compared "
        "against. If this load is bolted to the bracket rather than acting at "
        "the shaft, it belongs in --plate-load-n and is not a shaft check."
    )


def fill_in_interactively(args: argparse.Namespace) -> None:
    """Prompt for anything missing -- but never block a non-interactive run.

    Deliberately does NOT gate on `isatty()`. That check is unreliable (some
    shells hand over a tty with nothing readable behind it) and it would also
    throw away piped answers, which are a legitimate way to script this.
    Instead, just try to prompt: a terminal answers, a pipe answers, and
    anything with no input at all raises EOFError, which means "use the
    documented defaults" rather than "crash".
    """
    try:
        _prompt_for_missing(args)
    except EOFError:
        console.print("[dim]No interactive input available; using defaults.[/dim]")
        apply_defaults_noninteractive(args)


def _prompt_for_missing(args: argparse.Namespace) -> None:
    console.print(Panel.fit("[bold]ZooMounter[/bold]\nA few questions to size and generate your mount.", border_style="cyan"))

    if not args.mount:
        console.print(f"[dim]Mount types: {', '.join(MOUNTS.keys())}, or 'custom'[/dim]")
        args.mount = Prompt.ask("Mount type", default="nema17")
    if args.mount == "custom":
        args.plate_width_mm = args.plate_width_mm or FloatPrompt.ask("Plate width (mm)")
        args.bolt_count = args.bolt_count or IntPrompt.ask("Bolt count", default=4)
        args.bolt_circle_dia_mm = args.bolt_circle_dia_mm or FloatPrompt.ask("Bolt circle diameter (mm)")
        args.bolt_hole_dia_mm = args.bolt_hole_dia_mm or FloatPrompt.ask("Bolt hole diameter (mm)")

    if args.host_mount is None:
        console.print("[dim]Host mount options: none, 2020-slots, 4040-slots, corner-holes[/dim]")
        args.host_mount = Prompt.ask("Host mounting features", default="none")
        if args.host_mount in EXTRUSION_SERIES:
            args.host_slot_dir = Prompt.ask("Slot direction", choices=["parallel", "perpendicular"], default="parallel")
            override = Prompt.ask("Plate width override (mm) [leave blank to auto-size based on motor]", default="")
            if override:
                try:
                    args.plate_width = float(override)
                except ValueError:
                    args.plate_width = None
            else:
                args.plate_width = None

    # Deliberately OUTSIDE the host-mount block. Nested one level deeper, this
    # prompt was skipped whenever --host-mount was passed on the command line,
    # silently leaving the centre hole at 0.
    if args.center_hole_dia_mm is None:
        args.center_hole_dia_mm = FloatPrompt.ask("Center hole diameter (mm, 0 for none)", default=0)

    if not args.material:
        console.print(f"[dim]Materials: {', '.join(MATERIALS.keys())}, or 'custom'[/dim]")
        args.material = Prompt.ask("Material", default="aluminum_6061")
    if args.material == "custom":
        args.process = args.process or Prompt.ask("Process", choices=["3d_print", "machined"], default="machined")
        args.density_kg_m3 = args.density_kg_m3 or FloatPrompt.ask("Density (kg/m3)")
        args.youngs_modulus_gpa = args.youngs_modulus_gpa or FloatPrompt.ask("Young's modulus (GPa)")
        args.yield_mpa = args.yield_mpa or FloatPrompt.ask("Yield strength (MPa)")

    if not args.load_type:
        args.load_type = Prompt.ask(
            "Shaft load direction ('radial' = side load, 'axial' = thrust along the shaft)",
            choices=list(mechanics.LOAD_TYPES),
            default="radial",
        )
    if args.shaft_load_n is None:
        # Asked as a SHAFT load, because that is what it is compared against.
        # The old wording was "expected load on the mount", which invited a
        # bracket load and then measured it against the motor's shaft rating.
        console.print(
            "[dim]The load at the shaft -- belt tension, gear mesh, or leadscrew "
            "thrust. Anything bolted to the bracket instead goes in --plate-load-n."
            "[/dim]"
        )
        args.shaft_load_n = FloatPrompt.ask("Shaft load (N)", default=5.0)


def _write_assembly(output_dir, mount, base_mount, thickness_mm, mount_kcl,
                    bearing_selection, face):
    """Write the assembly beside the mount, plus an exploded copy for the
    render.

    Two folders on purpose. `assembly/` is the real thing, to scale, openable
    in Design Studio. `assembly-exploded/` exists only to be photographed --
    a 1mm plate under a 40mm motor is correct and completely unreadable. The
    exploded copy is never verified, so a deliberately-not-to-scale view can
    never be mistaken for the part.
    """
    from . import assembly as asm

    bearing = bearing_selection.bearing if bearing_selection else None
    try:
        main, parts = asm.write_assembly(
            output_dir / "assembly", mount, thickness_mm, mount_kcl,
            bearing=bearing, face=face, base_mount=base_mount,
        )
        generate._write_project_toml(output_dir / "assembly")

        exploded, _ = asm.write_assembly(
            output_dir / "assembly-exploded", mount, thickness_mm, mount_kcl,
            bearing=bearing, face=face, base_mount=base_mount,
            explode_mm=asm.DEFAULT_EXPLODE_MM,
        )
        generate._write_project_toml(output_dir / "assembly-exploded")

        roles = ", ".join(p.role for p in parts)
        console.print(f"[green]Assembly:[/green] {main}  [dim]({roles})[/dim]")
        return exploded
    except Exception as e:
        console.print(f"[dim]Assembly skipped: {e}[/dim]")
        return None


def _render_preview(kcl_path: Path, output_dir: Path) -> Path | None:
    """Render the generated part to a PNG next to the report.

    A beginner cannot judge a mount they cannot see, and until now the only
    way to look at one was to launch the GUI or open Design Studio. The
    snapshot is of the real generated KCL, so it is evidence rather than an
    illustration.

    Never fatal: a missing or unauthenticated Zoo CLI costs you the picture,
    not the part.
    """
    preview_path = output_dir / "preview.png"
    try:
        generate.snapshot_preview(kcl_path, preview_path)
        console.print(f"[green]Preview:[/green] {preview_path}")
        return preview_path
    except Exception as e:
        console.print(f"[dim]Preview render skipped: {e}[/dim]")
        return None


def print_parametric_report(kcl_code: str, scheme: generate.ParameterScheme) -> None:
    """Report whether the returned model is actually editable.

    Worth printing separately from the geometry checks because it can fail
    while every dimension is still correct -- a model can be exactly right
    and completely rigid, which is what the literal prompt produces."""
    report = kcl_inspect.check_parametric(
        kcl_code, scheme.names, scheme.relations
    )
    model = report.model

    table = Table(title="Parametric model", show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")

    names_ok = not report.missing_names
    table.add_row(
        "Parameter names",
        "[green]PASS[/green]" if names_ok else "[yellow]PARTIAL[/yellow]",
        f"{len(report.found_names)}/{len(report.expected_names)} declared"
        + ("" if names_ok else f"; missing: {', '.join(report.missing_names)}"),
    )

    rel_ok = not report.broken_relations
    table.add_row(
        "Relationships",
        "[green]PASS[/green]" if rel_ok else "[yellow]PARTIAL[/yellow]",
        f"{len(report.honoured_relations)}/{len(report.expected_relations)} derived as asked"
        + ("" if rel_ok else f"; flattened to constants: {', '.join(report.broken_relations)}"),
    )

    table.add_row(
        "Model",
        "[dim]info[/dim]",
        f"{len(model.parameters)} parameters, {len(model.derived)} derived "
        f"({model.derived_ratio:.0%}), {model.constraint_count} constraints",
    )
    console.print()
    console.print(table)

    if report.honoured_relations:
        console.print(
            f"[dim]Editable in Design Studio: change "
            f"{', '.join(sorted(scheme.relations))[:60]} and the dependent "
            f"dimensions follow.[/dim]"
        )
    console.print()


def _bearing_that_would_carry_it(mount, args) -> mechanics.Check:
    """Name the specific bearing that would take this load off the shaft.

    Selection is driven by the load case, not by a flag. "Add a bearing" is
    advice; "add an F8-16M, which carries 4990N static against the 240N you
    need" is an answer, and the difference is most of the tool's value.
    """
    from . import bearings

    if not mount.shaft_dia_mm:
        return mechanics.Check(
            level="INFO",
            message=(
                "No shaft diameter on file for this mount, so no specific bearing "
                "can be suggested."
            ),
            code=mechanics.SHAFT_LIMIT,
        )

    sel = bearings.select_bearing(
        load_type=args.load_type,
        load_n=args.shaft_load_n,
        shaft_dia_mm=mount.shaft_dia_mm,
        safety_factor=args.safety_factor,
    )
    if sel.bearing is None:
        return mechanics.Check(
            level="WARN",
            message=(
                f"No bearing in the catalogue carries {args.shaft_load_n:.0f}N "
                f"{args.load_type} on a {mount.shaft_dia_mm:g}mm shaft at SF "
                f"{args.safety_factor:g}."
            ),
            remedy="Reduce the load, use a larger shaft, or add a bearing to the catalogue.",
            code=mechanics.SHAFT_LIMIT,
        )

    return mechanics.Check(
        level="INFO",
        message=(
            f"Bearing {sel.bearing.label} would carry this: rated "
            f"{sel.bearing.static_c0_n:.0f}N static against "
            f"{args.shaft_load_n * args.safety_factor:.0f}N required "
            f"({args.shaft_load_n:.0f}N at SF {args.safety_factor:g})."
        ),
        source=sel.bearing.source,
        remedy=(
            f"Generate it with: --mount bearing --bearing {sel.bearing.designation} "
            f"--shaft-dia-mm {mount.shaft_dia_mm:g}"
        ),
        code=mechanics.SHAFT_LIMIT,
    )


_LEVEL_STYLE = {
    "LOUD WARN": "bold red",
    "WARN": "yellow",
    "PASS": "green",
    "INFO": "dim",
}

_VERDICT_STYLE = {
    mechanics.BEARING_REQUIRED: ("bold red", "BEARING REQUIRED"),
    mechanics.BEARING_RECOMMENDED: ("yellow", "BEARING RECOMMENDED"),
    mechanics.SHAFT_UNKNOWN: ("yellow", "NOT CHECKED"),
    mechanics.SHAFT_OK: ("green", "SHAFT OK"),
}


def _print_checks(checks) -> None:
    for note in checks:
        console.print(f"  [{_LEVEL_STYLE.get(note.level, 'dim')}]- {note}[/]")


def print_spec_summary(
    mount, material, args, thickness: mechanics.ThicknessResult, decision=None
) -> None:
    # The shaft verdict goes first and on its own, because it is the result.
    # Thickness used to hold this position while being set by the process
    # floor in every real case -- a manufacturing constant presented as the
    # engineering answer.
    if decision is not None:
        style, label = _VERDICT_STYLE[decision.verdict]
        console.print(f"[{style}]{label}[/]  [dim]({decision.load_type} shaft load)[/dim]")
        if decision.utilisation is not None:
            if decision.load_type == "radial":
                basis = (
                    f"{decision.shaft_load_n:.0f} N at {decision.offset_mm:g} mm "
                    f"vs {decision.limit_n:.0f} N at {decision.limit_at_mm:g} mm "
                    f"(compared as moments)"
                )
            else:
                basis = f"{decision.shaft_load_n:.0f} N vs {decision.limit_n:.0f} N rated"
            console.print(
                f"  [dim]{decision.utilisation * 100:.0f}% of published limit -- {basis}[/dim]"
            )
        _print_checks(decision.checks)
        console.print()

    table = Table(title="Spec", show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[bold]Mount[/bold]", mount.name)
    table.add_row("[bold]Material[/bold]", f"{material.name} ({material.process})")
    table.add_row("[bold]Shaft load[/bold]", f"{args.shaft_load_n} N {args.load_type}")
    if args.plate_load_n:
        table.add_row(
            "[bold]Bracket load[/bold]",
            f"{args.plate_load_n} N [dim](not a shaft load; unmodelled)[/dim]",
        )
    table.add_row(
        "[bold]Plate thickness[/bold]",
        f"{thickness.required_thickness_mm:.2f} mm  [dim](set by {thickness.governing_limit})[/dim]",
    )
    console.print(table)
    _print_checks(thickness.notes)


def print_results_table(result: verify.VerificationResult) -> None:
    table = Table(title="Verification")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")

    for check in result.checks:
        mark = "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]"
        table.add_row(check.name, mark, check.detail)

    console.print(table)
    if result.mass_g is not None:
        console.print(
            f"[dim]Mass of the generated part: {result.mass_g:.2f} g "
            f"(reported property, not an independent check -- it is volume x the density you supplied)[/dim]"
        )


def _notes_markdown(checks) -> str:
    out = ""
    for n in checks:
        if n.level in ("WARN", "LOUD WARN"):
            out += f"- **[{n.level}]** {n.message}\n"
            if n.source:
                out += f"  - *Source*: {n.source}\n"
            if n.remedy:
                out += f"  - *Remedy*: {n.remedy}\n"
        else:
            out += f"- {n.message}\n"
    return out


def _shaft_section(decision) -> str:
    """The report's headline section.

    This used to be a table of beam-calc intermediates -- lever arm, bending
    moment, allowable stress, thickness from stress, thickness from deflection
    -- none of which ever governed. What governs is whether the load exceeds
    what the shaft is rated for, so that is what the report leads with now.
    """
    if decision is None:
        return (
            "## Shaft load\n\n"
            "Not applicable: this part is a bearing housing, so it carries the "
            "shaft load by design rather than passing it into a motor.\n"
        )

    _, label = _VERDICT_STYLE[decision.verdict]
    body = f"## Shaft load: **{label}**\n\n"

    if decision.utilisation is None:
        body += (
            f"{decision.shaft_load_n:.0f} N {decision.load_type} was not checked -- "
            f"no published limit is on file for this component.\n\n"
        )
    elif decision.load_type == "radial":
        body += (
            f"A radial rating is a moment limit quoted at a distance from the "
            f"mounting flange, so both sides are converted to a moment before "
            f"being compared. Comparing the bare forces would be wrong in both "
            f"directions.\n\n"
            f"| | Force | Distance | Moment |\n|---|---|---|---|\n"
            f"| Applied | {decision.shaft_load_n:.0f} N | {decision.offset_mm:g} mm | "
            f"{decision.applied_n_mm:.0f} N·mm |\n"
            f"| Published limit | {decision.limit_n:.0f} N | {decision.limit_at_mm:g} mm | "
            f"{decision.limit_n_mm:.0f} N·mm |\n\n"
            f"**Utilisation: {decision.utilisation * 100:.0f}%** of the published limit.\n\n"
        )
    else:
        body += (
            f"Axial thrust loads the shaft along its axis regardless of where it "
            f"originates, so the rating is compared directly.\n\n"
            f"- Applied: **{decision.shaft_load_n:.0f} N**\n"
            f"- Published limit: **{decision.limit_n:.0f} N**\n"
            f"- **Utilisation: {decision.utilisation * 100:.0f}%**\n\n"
        )

    body += _notes_markdown(decision.checks)
    return body


def write_report(
    path: Path,
    mount_name: str,
    material_name: str,
    shaft_load_n: float,
    safety_factor: float,
    thickness: mechanics.ThicknessResult,
    result: verify.VerificationResult,
    preview_path: Path | None = None,
    decision=None,
) -> None:
    status = "PASS" if result.passed else "FAIL"
    notes_block = ""
    if thickness.notes:
        notes_block = "\n### Notes\n"
        for n in thickness.notes:
            if n.level in ("WARN", "LOUD WARN"):
                notes_block += f"- **[{n.level}]** {n.message}\n"
                if n.source:
                    notes_block += f"  - *Source*: {n.source}\n"
                if n.remedy:
                    notes_block += f"  - *Remedy*: {n.remedy}\n"
            else:
                notes_block += f"- {n.message}\n"
        notes_block += "\n"

    check_rows = "\n".join(
        f"| {c.name} | {'PASS' if c.passed else 'FAIL'} | {c.detail} |" for c in result.checks
    )

    hole_rows = ""
    if result.hole_details:
        hole_rows = "\n### Hole-by-hole\n\n| Expected (x, y) mm | Dia mm | Found at (x, y) mm | Position error mm |\n|---|---|---|---|\n"
        for h in result.hole_details:
            if h.found:
                hole_rows += (
                    f"| ({h.expected_x_mm:.2f}, {h.expected_y_mm:.2f}) | {h.expected_dia_mm:.2f} | "
                    f"({h.actual_x_mm:.2f}, {h.actual_y_mm:.2f}) | {h.position_error_mm:.3f} |\n"
                )
            else:
                hole_rows += (
                    f"| ({h.expected_x_mm:.2f}, {h.expected_y_mm:.2f}) | {h.expected_dia_mm:.2f} | "
                    f"**NOT FOUND** | - |\n"
                )

    mass_line = (
        f"\nMass of the generated part: **{result.mass_g:.2f} g**. This is reported as a "
        f"property, not counted as a check -- it is the measured volume multiplied by the "
        f"density you supplied, so it carries no information the volume check doesn't.\n"
        if result.mass_g is not None
        else ""
    )

    preview_block = ""
    if preview_path is not None and preview_path.exists():
        # Relative so the report stays portable if the folder is moved or
        # zipped -- the image sits alongside it.
        preview_block = (
            f"\n![Rendered part]({preview_path.name})\n\n"
            "*Rendered from the generated KCL by `zoo kcl snapshot` -- this is "
            "the actual part, not an illustration of it.*\n"
        )

    report = f"""# ZooMounter Inspection Report
{preview_block}
## Request
- Mount: {mount_name}
- Material: {material_name}
- Shaft load: {shaft_load_n} N {getattr(decision, 'load_type', 'n/a')}
- Bracket load: {thickness.plate_load_n:g} N (not a shaft load; not modelled)
- Safety factor: {safety_factor}

{_shaft_section(decision)}
## Plate thickness

Thickness is a manufacturing answer, not a structural one. The two candidates
are floors -- what the process can produce, and what the bearing needs to seat
in -- and the larger wins. ZooMounter does not size this plate against a load,
because for every part in its scope the structural requirement lands below the
process floor; see `docs/mechanics.html` for why that layer was removed rather
than kept as a sanity check.

- Process minimum wall: {thickness.min_wall_mm:.2f} mm
- Bearing seat requirement: {thickness.bearing_seat_min_mm:.2f} mm
- **Required thickness: {thickness.required_thickness_mm:.2f} mm** (set by: {thickness.governing_limit})
{notes_block}
## Verification (generated part vs. the spec it was asked for)

Hole positions and bounding box are read directly out of the generated STEP
file (local parse, no API calls). Volume is measured by Zoo's File Format API.

| Check | Result | Detail |
|---|---|---|
{check_rows}

Tolerances: {POSITION_TOLERANCE_MM}mm absolute on hole positions, {int(verify.TOLERANCE_FRACTION * 100)}% on bulk dimensions and volume.
{hole_rows}{mass_line}
## Result: {status}
"""
    path.write_text(report, encoding="utf-8")


def _add_to_project(args, mount, material, thickness, kcl_code: str) -> int:
    """Write the generated part into the surrounding Zoo project and wire it in."""
    project = zoo_project.find_project()
    if project is None:  # pragma: no cover -- guarded earlier in main()
        console.print("[red]error:[/red] not inside a Zoo project.")
        return 1

    comment = (
        f"{mount.name} in {material.name}, {args.shaft_load_n}N {args.load_type} shaft load, "
        f"SF {args.safety_factor} -> {thickness.required_thickness_mm:.2f}mm thick "
        f"(set by {thickness.governing_limit})"
    )
    try:
        part_path, entry_modified = zoo_project.add_part(
            project, args.add, kcl_code, comment=comment
        )
    except zoo_project.ProjectError as e:
        console.print(f"[red]error:[/red] {e}")
        return 1

    console.print(f"[green]Added to project '{project.name}':[/green] {part_path.name}")
    if entry_modified:
        console.print(f"  [dim]imported from {project.entry.name} and instantiated[/dim]")
    else:
        console.print(
            f"  [dim]{project.entry.name} already imported it -- part file updated in place[/dim]"
        )
    console.print(f"  [dim]{comment}[/dim]")
    for note in thickness.notes:
        if note.level == "LOUD WARN":
            style = "bold red"
        elif note.level == "WARN":
            style = "yellow"
        elif note.level == "PASS":
            style = "green"
        else:
            style = "dim"
        console.print(f"  [{style}]- {note}[/{style}]")
    console.print(
        f"\n[dim]Reload the project in Design Studio to see it, or:[/dim] zoo app {project.root}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    load_environment()

    parser = build_parser()
    args = parser.parse_args(argv)

    # Check this before generating: --add is a 1-3 minute, credit-spending
    # round trip, and discovering afterwards that there was nowhere to put the
    # result is a rotten way to find out.
    if args.add and zoo_project.find_project() is None:
        console.print(
            "[red]error:[/red] --add needs to be run from inside a Zoo project "
            "(a folder with a project.toml, or any subfolder of one)."
        )
        console.print(f"[dim]Looked upward from:[/dim] {Path.cwd()}")
        console.print(
            "[dim]Either cd into your project, or drop --add to write to ./output instead.[/dim]"
        )
        return 1

    resolve_load_aliases(args)
    fill_in_interactively(args)

    try:
        bearing_selection = None
        if args.mount == "bearing":
            from . import bearings
            shaft = args.shaft_dia_mm
            if shaft is None:
                console.print(
                    "[red]error:[/red] --mount bearing needs --shaft-dia-mm "
                    "(the shaft the bearing carries, not the plate's centre hole)."
                )
                return 2
            mount, bearing_selection = bearings.auto_bearing_mount(
                load_type=args.load_type,
                load_n=args.shaft_load_n,
                shaft_dia_mm=shaft,
                safety_factor=args.safety_factor,
                designation=args.bearing,
            )
            if mount is None:
                console.print("[red]No bearing fits this case.[/red]")
                for n in bearing_selection.notes:
                    console.print(f"  [{n.level}] {n.message}")
                    if n.remedy:
                        console.print(f"    [dim]Remedy: {n.remedy}[/dim]")
                return 1
        else:
            mount = get_mount(
                args.mount,
                plate_width_mm=args.plate_width_mm,
                bolt_count=args.bolt_count,
                bolt_circle_dia_mm=args.bolt_circle_dia_mm,
                bolt_hole_dia_mm=args.bolt_hole_dia_mm,
                center_hole_dia_mm=args.center_hole_dia_mm or 0,
            )
        
        from .mount_specs import apply_host_mount
        base_mount = mount
        mount = apply_host_mount(
            mount,
            host_mount=args.host_mount,
            host_slot_direction=args.host_slot_dir,
            plate_width_override=args.plate_width
        )
        material = get_material(
            args.material,
            density_kg_m3=args.density_kg_m3,
            youngs_modulus_gpa=args.youngs_modulus_gpa,
            yield_mpa=args.yield_mpa,
            process=args.process,
        )
    except ValueError as e:
        console.print(f"[red]error:[/red] {e}")
        return 1

    # The shaft question first, because it is the one with a real answer. A
    # bearing block carries the load by design, so there is no motor shaft to
    # protect and the check does not apply to it.
    decision = None
    if mount.kind == "motor":
        decision = mechanics.shaft_support(
            mount=base_mount,
            shaft_load_n=args.shaft_load_n,
            load_type=args.load_type,
            offset_mm=args.overhang_mm,
        )
        if decision.needs_bearing:
            decision.checks.append(_bearing_that_would_carry_it(base_mount, args))

    thickness = mechanics.required_thickness(
        mount=mount,
        material=material,
        plate_load_n=args.plate_load_n,
    )
    if bearing_selection is not None and bearing_selection.bearing is not None:
        from . import bearings as _b
        # Appended to thickness.notes so they travel through the existing
        # console summary and the markdown report with no extra plumbing.
        thickness.notes.extend(bearing_selection.notes)
        thickness.notes.append(
            _b.check_seat_depth(
                bearing_selection.bearing,
                thickness.required_thickness_mm,
                args.load_type,
            )
        )

    console.print()
    print_spec_summary(mount, material, args, thickness, decision)

    scheme = None
    if args.literal_prompt:
        prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)
    else:
        prompt, scheme = generate.build_parametric_prompt(
            mount, material, thickness.required_thickness_mm
        )

    if args.print_prompt:
        # Everything above this line is local arithmetic -- no token, no
        # credits, no Zoo CLI. Paste the result into Design Studio's chat.
        console.print(Panel(prompt, title="Agent API prompt", border_style="cyan"))
        return 0

    console.print(f"\n[dim]Agent API prompt:[/dim] {prompt}\n")

    def on_status(elapsed: float, status: str) -> None:
        spinner.update(f"[bold cyan]Generating via Zoo Agent API...[/bold cyan] {status} ({elapsed:.0f}s elapsed)")

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(args.mount, args.material)
    console.print(f"[dim]Output folder:[/dim] {output_dir}\n")

    try:
        with console.status("[bold cyan]Generating via Zoo Agent API...[/bold cyan] submitting...") as spinner:
            kcl_code = generate.generate_kcl(prompt, on_status=on_status)

            if args.add:
                spinner.stop()
                return _add_to_project(args, mount, material, thickness, kcl_code)

            kcl_path = generate.write_kcl_project(kcl_code, output_dir)

            if scheme is not None:
                spinner.stop()
                print_parametric_report(kcl_code, scheme)

            assembly_main = None
            if not args.no_assembly:
                assembly_main = _write_assembly(
                    output_dir, mount, base_mount, thickness.required_thickness_mm,
                    kcl_code, bearing_selection, args.mounting_face,
                )
            preview_path = _render_preview(assembly_main or kcl_path, output_dir)

            if args.no_export:
                spinner.stop()
                console.print(f"[green]Zoo project written:[/green] {kcl_path.parent}")
                console.print(
                    "[dim]Skipped STEP export and verification (--no-export). Open that folder "
                    "in Zoo Design Studio, or run:[/dim]"
                )
                console.print(f"[dim]  zoo app {kcl_path.parent}[/dim]")
                return 0

            spinner.update("[bold cyan]Executing KCL into a STEP file via Zoo CLI...[/bold cyan]")
            step_path = generate.export_step(kcl_path, output_dir)

        console.print(f"[green]Generated:[/green] {step_path}\n")

        with console.status("[bold cyan]Verifying against Zoo File Format API...[/bold cyan]"):
            result = verify.verify(step_path, mount, material, thickness.required_thickness_mm)
    except (generate.GenerationError, verify.VerificationError) as e:
        console.print(f"[red]error:[/red] {e}")
        return 1

    report_path = output_dir / "inspection_report.md"
    write_report(
        report_path, mount.name, material.name, args.shaft_load_n,
        args.safety_factor, thickness, result, preview_path=preview_path,
        decision=decision,
    )

    console.print()
    print_results_table(result)

    if result.passed:
        console.print(Panel.fit("[bold green]PASS[/bold green]", border_style="green"))
    else:
        console.print(Panel.fit("[bold red]FAIL[/bold red]", border_style="red"))
    console.print(f"[dim]Report:[/dim] {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
