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

from . import generate, mechanics, verify, zoo_project
from .config import load_environment
from .materials import MATERIALS, get_material
from .mount_specs import MOUNTS, get_mount
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
    p.add_argument("--mount", choices=[*MOUNTS.keys(), "custom"], help="Mount type")
    p.add_argument("--material", choices=[*MATERIALS.keys(), "custom"], help="Plate material")
    p.add_argument("--load-n", type=float, help="Expected load on the mount, in newtons")
    p.add_argument(
        "--load-type",
        choices=mechanics.LOAD_TYPES,
        default=None,
        help="'radial' = side load (e.g. belt/pulley pulling sideways on the shaft), bending-governed. "
        "'axial' = thrust load along the bolt axis (e.g. leadscrew pushing into the motor), bearing-stress-governed. (default: radial)",
    )
    p.add_argument("--safety-factor", type=float, default=2.0, help="Safety factor (default: 2.0)")
    p.add_argument(
        "--overhang-mm",
        type=float,
        default=None,
        help="Lever arm for radial-load bending calc (default: the mount's typical body length if known, else half the plate width). Ignored for axial loads.",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Where to write the generated files (default: a uniquely-named folder under ./output, e.g. output/nema17_aluminum_6061_20260724_143022)",
    )
    p.add_argument(
        "--print-prompt",
        action="store_true",
        help="Run the sizing calc, print the Agent API prompt, and stop. No network calls, no credits, no Zoo CLI needed -- paste the prompt into Design Studio yourself.",
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
    if args.load_n is None:
        args.load_n = 5.0


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
            "Load type ('radial' = side load, 'axial' = thrust along bolt axis)",
            choices=list(mechanics.LOAD_TYPES),
            default="radial",
        )
    if args.load_n is None:
        args.load_n = FloatPrompt.ask("Expected load on the mount (N)", default=5.0)


def print_spec_summary(mount, material, args, thickness: mechanics.ThicknessResult) -> None:
    table = Table(title="Spec", show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[bold]Mount[/bold]", mount.name)
    table.add_row("[bold]Material[/bold]", f"{material.name} ({material.process})")
    table.add_row("[bold]Load type[/bold]", thickness.load_type)
    table.add_row("[bold]External load[/bold]", f"{args.load_n} N")
    if thickness.self_weight_n > 0:
        table.add_row(
            "[bold]+ Component self-weight[/bold]",
            f"{thickness.self_weight_n:.2f} N ({mount.typical_mass_kg} kg) -> effective load {thickness.effective_load_n:.2f} N",
        )
    table.add_row("[bold]Safety factor[/bold]", str(args.safety_factor))
    table.add_row(
        "[bold]Calculated thickness[/bold]",
        f"{thickness.required_thickness_mm:.2f} mm  [dim](governed by {thickness.governing_limit})[/dim]",
    )
    console.print(table)
    for note in thickness.notes:
        style = "yellow" if note.startswith("WARNING") else "dim"
        console.print(f"  [{style}]- {note}[/{style}]")


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


def write_report(
    path: Path,
    mount_name: str,
    material_name: str,
    load_n: float,
    safety_factor: float,
    thickness: mechanics.ThicknessResult,
    result: verify.VerificationResult,
) -> None:
    status = "PASS" if result.passed else "FAIL"
    load_type_note = (
        "Radial (side) load -- modeled as a cantilever beam; bending stress and tip deflection both checked."
        if thickness.load_type == "radial"
        else "Axial (thrust) load -- plate sized against screw-head pull-through (punching shear). For thrust, the plate is usually not the limiting element; see notes."
    )
    self_weight_line = (
        f"- Component self-weight added to external load: {thickness.self_weight_n:.2f} N -> effective load {thickness.effective_load_n:.2f} N\n"
        if thickness.self_weight_n > 0
        else ""
    )
    notes_block = (
        "\n### Engineering notes\n" + "\n".join(f"- {n}" for n in thickness.notes) + "\n"
        if thickness.notes
        else ""
    )

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

    report = f"""# ZooMounter Inspection Report

## Request
- Mount: {mount_name}
- Material: {material_name}
- Load type: {thickness.load_type} ({load_type_note})
- External load: {load_n} N
{self_weight_line}- Safety factor: {safety_factor}

## Calculated requirement (domain rules layer, before generation)
- Lever arm: {thickness.lever_arm_mm:.2f} mm
- Bending moment: {thickness.moment_n_mm:.1f} N*mm
- Allowable stress (yield / safety factor): {thickness.allowable_stress_mpa:.1f} MPa
- Thickness from stress limit: {thickness.thickness_from_stress_mm:.2f} mm
- Thickness from deflection limit (arm/300, radial only): {thickness.thickness_from_deflection_mm:.2f} mm
- Process minimum wall: {thickness.min_wall_mm:.2f} mm
- **Required thickness: {thickness.required_thickness_mm:.2f} mm** (governed by: {thickness.governing_limit})
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
        f"{mount.name} in {material.name}, {args.load_n}N {thickness.load_type} load, "
        f"SF {args.safety_factor} -> {thickness.required_thickness_mm:.2f}mm thick "
        f"(governed by {thickness.governing_limit})"
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
        style = "yellow" if note.startswith("WARNING") else "dim"
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

    fill_in_interactively(args)

    try:
        mount = get_mount(
            args.mount,
            plate_width_mm=args.plate_width_mm,
            bolt_count=args.bolt_count,
            bolt_circle_dia_mm=args.bolt_circle_dia_mm,
            bolt_hole_dia_mm=args.bolt_hole_dia_mm,
            center_hole_dia_mm=args.center_hole_dia_mm or 0,
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

    thickness = mechanics.required_thickness(
        load_n=args.load_n,
        mount=mount,
        material=material,
        safety_factor=args.safety_factor,
        lever_arm_mm=args.overhang_mm,
        load_type=args.load_type,
    )
    console.print()
    print_spec_summary(mount, material, args, thickness)

    prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)

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
    write_report(report_path, mount.name, material.name, args.load_n, args.safety_factor, thickness, result)

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
