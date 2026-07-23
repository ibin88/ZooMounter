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

from . import generate, mechanics, verify
from .materials import MATERIALS, get_material
from .mount_specs import MOUNTS, get_mount


def _prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


def _prompt_float(question: str, default: float | None = None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{question}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  please enter a number")


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
    print("ZooMounter -- a few questions to size and generate your mount.\n")

    if not args.mount:
        print(f"Mount types: {', '.join(MOUNTS.keys())}, or 'custom'")
        args.mount = _prompt("Mount type", default="nema17")
    if args.mount == "custom":
        args.plate_width_mm = args.plate_width_mm or _prompt_float("Plate width (mm)")
        args.bolt_count = args.bolt_count or int(_prompt_float("Bolt count", default=4))
        args.bolt_circle_dia_mm = args.bolt_circle_dia_mm or _prompt_float("Bolt circle diameter (mm)")
        args.bolt_hole_dia_mm = args.bolt_hole_dia_mm or _prompt_float("Bolt hole diameter (mm)")
        if args.center_hole_dia_mm is None:
            args.center_hole_dia_mm = _prompt_float("Center hole diameter (mm, 0 for none)", default=0)

    if not args.material:
        print(f"\nMaterials: {', '.join(MATERIALS.keys())}, or 'custom'")
        args.material = _prompt("Material", default="aluminum_6061")
    if args.material == "custom":
        args.process = args.process or _prompt("Process (3d_print / machined)", default="machined")
        args.density_kg_m3 = args.density_kg_m3 or _prompt_float("Density (kg/m3)")
        args.youngs_modulus_gpa = args.youngs_modulus_gpa or _prompt_float("Young's modulus (GPa)")
        args.yield_mpa = args.yield_mpa or _prompt_float("Yield strength (MPa)")

    if args.load_n is None:
        args.load_n = _prompt_float("\nExpected load on the mount (N)", default=5.0)


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
- Expected mass (hand calc from requested geometry): {result.expected_mass_g:.2f} g
- Actual mass (measured on generated STEP): {result.actual_mass_g:.2f} g
- Difference: {result.percent_diff:.1f}%
- Tolerance: {int(verify.TOLERANCE_FRACTION * 100)}%

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
        print(f"error: {e}", file=sys.stderr)
        return 1

    thickness = mechanics.required_thickness(
        load_n=args.load_n,
        plate_width_mm=mount.plate_width_mm,
        material=material,
        safety_factor=args.safety_factor,
        lever_arm_mm=args.overhang_mm,
    )
    print(f"\nCalculated required thickness: {thickness.required_thickness_mm:.2f} mm")

    prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)
    print(f"\nGenerating via Zoo Agent API:\n  {prompt}\n(this can take a few minutes)")

    try:
        kcl_code = generate.generate_kcl(prompt)
        output_dir = Path(args.output_dir)
        step_path = generate.export_step(kcl_code, output_dir)
        print(f"Generated: {step_path}")

        print("\nVerifying against Zoo File Format API...")
        result = verify.verify(step_path, mount, material, thickness.required_thickness_mm)
    except (generate.GenerationError, verify.VerificationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    report_path = output_dir / "inspection_report.md"
    write_report(report_path, mount.name, material.name, args.load_n, args.safety_factor, thickness, result)

    status = "PASS" if result.passed else "FAIL"
    print(f"\nVerification: {status} ({result.percent_diff:.1f}% mass difference, {int(verify.TOLERANCE_FRACTION*100)}% tolerance)")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
