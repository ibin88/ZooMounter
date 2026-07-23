"""ZooMounter CLI.

Usage (scripted):
    python -m zoomounter.cli --mount nema17 --material aluminum_6061 \\
        --load-n 5 --safety-factor 2

Usage (interactive): run with no flags and answer the prompts.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from . import generate, mechanics, verify
from .materials import MATERIALS, get_material
from .mount_specs import MOUNTS, get_mount

console = Console()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zoomounter",
        description="Generate a mechanical mount from an engineering spec, then verify it against Zoo's File Format API.",
    )
    p.add_argument("--mount", choices=[*MOUNTS.keys(), "custom"], help="Mount type")
    p.add_argument("--material", choices=[*MATERIALS.keys(), "custom"], help="Plate material")
    p.add_argument("--load-n", type=float, help="Expected load on the mount, in newtons")
    p.add_argument("--safety-factor", type=float, default=2.0, help="Safety factor (default: 2.0)")
    p.add_argument("--overhang-mm", type=float, default=None, help="Lever arm for the bending calc (default: half the plate width)")
    p.add_argument("--output-dir", default="output", help="Where to write the generated files (default: ./output)")

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


def fill_in_interactively(args: argparse.Namespace) -> None:
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

    if args.load_n is None:
        args.load_n = FloatPrompt.ask("Expected load on the mount (N)", default=5.0)


def print_spec_summary(mount, material, args, thickness: mechanics.ThicknessResult) -> None:
    table = Table(title="Spec", show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[bold]Mount[/bold]", mount.name)
    table.add_row("[bold]Material[/bold]", f"{material.name} ({material.process})")
    table.add_row("[bold]Load[/bold]", f"{args.load_n} N")
    table.add_row("[bold]Safety factor[/bold]", str(args.safety_factor))
    table.add_row("[bold]Calculated thickness[/bold]", f"{thickness.required_thickness_mm:.2f} mm")
    console.print(table)


def print_results_table(result: verify.VerificationResult) -> None:
    table = Table(title="Verification")
    table.add_column("Check")
    table.add_column("Expected", justify="right")
    table.add_column("Actual", justify="right")
    table.add_column("Diff", justify="right")
    table.add_column("Result")

    def row(name: str, unit: str, check: verify.CheckResult) -> None:
        mark = "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]"
        table.add_row(name, f"{check.expected:.2f} {unit}", f"{check.actual:.2f} {unit}", f"{check.percent_diff:.1f}%", mark)

    row("Volume", "mm3", result.volume)
    row("Mass", "g", result.mass)
    console.print(table)


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
    report = f"""# ZooMounter Inspection Report

## Request
- Mount: {mount_name}
- Material: {material_name}
- Load: {load_n} N
- Safety factor: {safety_factor}

## Calculated requirement (domain rules layer, before generation)
- Lever arm: {thickness.lever_arm_mm:.2f} mm
- Bending moment: {thickness.moment_n_mm:.1f} N*mm
- Allowable stress (yield / safety factor): {thickness.allowable_stress_mpa:.1f} MPa
- Thickness from stress limit: {thickness.thickness_from_stress_mm:.2f} mm
- Thickness from deflection limit (arm/300): {thickness.thickness_from_deflection_mm:.2f} mm
- Process minimum wall: {thickness.min_wall_mm:.2f} mm
- **Required thickness: {thickness.required_thickness_mm:.2f} mm**

## Verification (generated part, measured via Zoo File Format API)
Two independent checks -- both must pass:

| Check | Expected (hand calc) | Actual (measured on generated STEP) | Difference | Pass? |
|---|---|---|---|---|
| Volume | {result.volume.expected:.1f} mm3 | {result.volume.actual:.1f} mm3 | {result.volume.percent_diff:.1f}% | {"PASS" if result.volume.passed else "FAIL"} |
| Mass | {result.mass.expected:.2f} g | {result.mass.actual:.2f} g | {result.mass.percent_diff:.1f}% | {"PASS" if result.mass.passed else "FAIL"} |

Tolerance: {int(verify.TOLERANCE_FRACTION * 100)}% on each check.

## Result: {status}
"""
    path.write_text(report, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args(argv)
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
        plate_width_mm=mount.plate_width_mm,
        material=material,
        safety_factor=args.safety_factor,
        lever_arm_mm=args.overhang_mm,
    )
    console.print()
    print_spec_summary(mount, material, args, thickness)

    prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)
    console.print(f"\n[dim]Agent API prompt:[/dim] {prompt}\n")

    def on_status(elapsed: float, status: str) -> None:
        spinner.update(f"[bold cyan]Generating via Zoo Agent API...[/bold cyan] {status} ({elapsed:.0f}s elapsed)")

    try:
        with console.status("[bold cyan]Generating via Zoo Agent API...[/bold cyan] submitting...") as spinner:
            kcl_code = generate.generate_kcl(prompt, on_status=on_status)
            output_dir = Path(args.output_dir)
            spinner.update("[bold cyan]Executing KCL into a STEP file via Zoo CLI...[/bold cyan]")
            step_path = generate.export_step(kcl_code, output_dir)

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
