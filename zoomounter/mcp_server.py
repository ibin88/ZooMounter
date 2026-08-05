"""ZooMounter as an MCP server.

Zoo Design Studio has no plugin system and its chat box is Zookeeper, a CAD
agent with a fixed toolset -- there's no way to teach either of them about an
external tool. This is the next best thing: expose ZooMounter over the Model
Context Protocol so any MCP-capable assistant (Claude Code, Gemini CLI, and
so on) can drive it conversationally.

Tools are deliberately split by cost, because "generate a mount" is a
multi-minute, credit-spending operation and an assistant shouldn't reach for
it when the user only wanted a number:

    size_mount          pure arithmetic, instant, free
    build_prompt        pure arithmetic, instant, free
    generate_mount      calls the Agent API + Zoo CLI, minutes, costs credits
    verify_step_file    local geometry parse + one File Format API call
    inspect_step_file   pure local parse, instant, free
    list_options        static tables, instant, free

Run it with:  python -m zoomounter.mcp_server
"""

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import generate, mechanics, verify
from .config import load_environment
from .cli import default_output_dir, write_report
from .materials import MATERIALS, get_material
from .mount_specs import MOUNTS, get_mount
from .step_inspect import StepParseError, parse_step

load_environment()

mcp = FastMCP("zoomounter")


def _resolve(mount_key: str, material_key: str, **kwargs) -> tuple:
    """Shared spec resolution so every tool interprets arguments identically."""
    mount = get_mount(
        mount_key,
        plate_width_mm=kwargs.get("plate_width_mm"),
        bolt_count=kwargs.get("bolt_count"),
        bolt_circle_dia_mm=kwargs.get("bolt_circle_dia_mm"),
        bolt_hole_dia_mm=kwargs.get("bolt_hole_dia_mm"),
        center_hole_dia_mm=kwargs.get("center_hole_dia_mm") or 0,
    )
    material = get_material(
        material_key,
        density_kg_m3=kwargs.get("density_kg_m3"),
        youngs_modulus_gpa=kwargs.get("youngs_modulus_gpa"),
        yield_mpa=kwargs.get("yield_mpa"),
        process=kwargs.get("process"),
    )
    return mount, material


@mcp.tool()
def list_options() -> dict[str, Any]:
    """List the mount types and materials ZooMounter knows about.

    Free and instant. Call this first if unsure what to pass to the other
    tools.
    """
    return {
        "mounts": {
            key: {
                "name": spec.name,
                "kind": spec.kind,
                "plate_mm": [spec.plate_width_mm, spec.plate_height_mm],
                "bolt_hole_dia_mm": spec.bolt_hole_dia_mm,
                "hole_count": len(spec.hole_positions),
                "center_hole_dia_mm": spec.center_hole_dia_mm,
                "typical_mass_kg": spec.typical_mass_kg or None,
            }
            for key, spec in MOUNTS.items()
        },
        "materials": {
            key: {
                "name": m.name,
                "process": m.process,
                "density_kg_m3": m.density_kg_m3,
                "youngs_modulus_gpa": m.youngs_modulus_gpa,
                "yield_mpa": m.yield_mpa,
            }
            for key, m in MATERIALS.items()
        },
        "load_types": {
            "radial": "Side load perpendicular to the bolt axis (belt, pulley, gear). "
            "Bending-governed -- this is the case where plate thickness actually matters.",
            "axial": "Thrust along the bolt axis (leadscrew pushing into a motor). "
            "Usually NOT plate-limited; fasteners and the motor's own bearing govern first.",
        },
        "custom_escape_hatch": (
            "Pass mount='custom' with plate_width_mm/bolt_count/bolt_circle_dia_mm/"
            "bolt_hole_dia_mm, or material='custom' with density_kg_m3/"
            "youngs_modulus_gpa/yield_mpa/process."
        ),
    }


def _shaft_payload(decision) -> dict[str, Any]:
    """Serialise the shaft decision.

    This is the primary result, so it is a top-level object rather than a note
    buried in a thickness breakdown. The utilisation and both sides of the
    comparison are included: a caller that only sees a verdict cannot tell 101%
    from 12x, and those warrant very different advice.
    """
    if decision is None:
        return {
            "verdict": "NOT_APPLICABLE",
            "why": (
                "This part is a bearing housing. It carries the shaft load by "
                "design, so there is no motor shaft rating to check against."
            ),
        }
    payload = {
        "verdict": decision.verdict,
        "load_type": decision.load_type,
        "shaft_load_n": decision.shaft_load_n,
        "published_limit_n": decision.limit_n,
        "checks": decision.checks,
    }
    if decision.utilisation is not None:
        payload["utilisation_fraction"] = round(decision.utilisation, 3)
    if decision.load_type == "radial" and decision.limit_n is not None:
        # Radial ratings are moment limits, so report the moments that were
        # actually compared rather than the forces that were not.
        payload["compared_as"] = "moment about the mounting face"
        payload["applied_moment_n_mm"] = round(decision.applied_n_mm, 1)
        payload["rated_moment_n_mm"] = round(decision.limit_n_mm, 1)
        payload["load_offset_mm"] = decision.offset_mm
        payload["rating_measured_at_mm"] = decision.limit_at_mm
    return payload


@mcp.tool()
def size_mount(
    mount: str,
    material: str,
    shaft_load_n: float,
    load_type: str = "radial",
    plate_load_n: float = 0.0,
    safety_factor: float = 2.0,
    overhang_mm: float | None = None,
    plate_width_mm: float | None = None,
    bolt_count: int | None = None,
    bolt_circle_dia_mm: float | None = None,
    bolt_hole_dia_mm: float | None = None,
    center_hole_dia_mm: float | None = None,
    density_kg_m3: float | None = None,
    youngs_modulus_gpa: float | None = None,
    yield_mpa: float | None = None,
    process: str | None = None,
) -> dict[str, Any]:
    """Check whether a shaft load is within the component's rating, and size
    the plate. Free and instant -- no API calls, no credits.

    The primary answer is `shaft`: whether the load exceeds what the motor's
    own bearings can take, and therefore whether a bearing is needed to bypass
    them. No bracket thickness changes that answer.

    `shaft_load_n` acts at the shaft (belt, gear, leadscrew) and is what gets
    checked. `plate_load_n` is anything bolted to the bracket instead -- it
    never reaches the shaft, so it is reported as unmodelled rather than
    compared against a shaft rating it has nothing to do with.

    For radial loads, pass `overhang_mm` if you know how far out the load acts.
    It matters: a radial rating is a moment limit quoted at a stated distance,
    so doubling the offset doubles the demand.
    """
    m, mat = _resolve(
        mount,
        material,
        plate_width_mm=plate_width_mm,
        bolt_count=bolt_count,
        bolt_circle_dia_mm=bolt_circle_dia_mm,
        bolt_hole_dia_mm=bolt_hole_dia_mm,
        center_hole_dia_mm=center_hole_dia_mm,
        density_kg_m3=density_kg_m3,
        youngs_modulus_gpa=youngs_modulus_gpa,
        yield_mpa=yield_mpa,
        process=process,
    )
    decision = (
        mechanics.shaft_support(
            mount=m,
            shaft_load_n=shaft_load_n,
            load_type=load_type,
            offset_mm=overhang_mm,
        )
        if m.kind == "motor"
        else None
    )
    t = mechanics.required_thickness(
        mount=m, material=mat, plate_load_n=plate_load_n
    )
    return {
        "mount": m.name,
        "material": mat.name,
        "shaft": _shaft_payload(decision),
        "thickness": {
            "required_thickness_mm": round(t.required_thickness_mm, 2),
            "set_by": t.governing_limit,
            "process_minimum_wall_mm": t.min_wall_mm,
            "bearing_seat_minimum_mm": round(t.bearing_seat_min_mm, 2),
            "note": (
                "Thickness here is a manufacturing floor, not a structural "
                "result. ZooMounter does not size this plate against a load."
            ),
            "notes": t.notes,
        },
    }


@mcp.tool()
def build_prompt(
    mount: str,
    material: str,
    plate_load_n: float = 0.0,
) -> dict[str, Any]:
    """Produce the fully-constrained text-to-CAD prompt for this spec, without
    generating anything. Free and instant.

    The returned prompt states every hole as an explicit (x, y) coordinate,
    which is what makes the geometry come back exact rather than approximate.
    Paste it into Zoo Design Studio's chat, or feed it to the Agent API
    yourself.

    Takes no shaft load, because the prompt does not depend on one: thickness
    comes from the process floor and the bearing seat. Use `size_mount` to
    check whether the shaft can take your load -- that is a separate question
    from what geometry to build, and this tool no longer implies otherwise by
    accepting a load it would not use.
    """
    m, mat = _resolve(mount, material)
    t = mechanics.required_thickness(mount=m, material=mat, plate_load_n=plate_load_n)
    return {
        "prompt": generate.build_prompt(m, mat, t.required_thickness_mm),
        "required_thickness_mm": round(t.required_thickness_mm, 2),
        "set_by": t.governing_limit,
    }


@mcp.tool()
def inspect_step_file(step_path: str) -> dict[str, Any]:
    """Read hole positions, diameters and the bounding box out of a STEP file.

    Pure local parsing -- free, instant, works on any STEP file, not just ones
    ZooMounter made. Useful for checking what a part actually is.
    """
    try:
        geo = parse_step(Path(step_path))
    except (StepParseError, OSError) as e:
        return {"error": str(e)}
    return {
        "bounding_box_mm": {
            "width": round(geo.bbox.width_mm, 3),
            "height": round(geo.bbox.height_mm, 3),
            "thickness": round(geo.bbox.thickness_mm, 3),
        },
        "holes": [
            {
                "x_mm": round(c.x_mm, 3),
                "y_mm": round(c.y_mm, 3),
                "diameter_mm": round(c.diameter_mm, 3),
            }
            for c in sorted(geo.circles, key=lambda c: (-c.radius_mm, c.x_mm, c.y_mm))
        ],
    }


@mcp.tool()
def verify_step_file(
    step_path: str,
    mount: str,
    material: str,
    thickness_mm: float,
    plate_width_mm: float | None = None,
    bolt_count: int | None = None,
    bolt_circle_dia_mm: float | None = None,
    bolt_hole_dia_mm: float | None = None,
    center_hole_dia_mm: float | None = None,
) -> dict[str, Any]:
    """Check an existing STEP file against a mount spec.

    Runs three checks: hole positions and bounding box (local parse, free) and
    volume (one File Format API call, minimal cost). Reports each separately
    so you can see which one failed and why.
    """
    m, mat = _resolve(
        mount,
        material,
        plate_width_mm=plate_width_mm,
        bolt_count=bolt_count,
        bolt_circle_dia_mm=bolt_circle_dia_mm,
        bolt_hole_dia_mm=bolt_hole_dia_mm,
        center_hole_dia_mm=center_hole_dia_mm,
    )
    try:
        result = verify.verify(Path(step_path), m, mat, thickness_mm)
    except (verify.VerificationError, OSError) as e:
        return {"error": str(e)}

    return {
        "passed": result.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks
        ],
        "mass_g": round(result.mass_g, 2) if result.mass_g is not None else None,
        "mass_note": (
            "Reported as a property, not an independent check -- it is the measured "
            "volume times the supplied density."
        ),
        "holes": [
            {
                "expected_xy_mm": [round(h.expected_x_mm, 2), round(h.expected_y_mm, 2)],
                "found": h.found,
                "actual_xy_mm": (
                    [round(h.actual_x_mm, 2), round(h.actual_y_mm, 2)] if h.found else None
                ),
                "position_error_mm": (
                    round(h.position_error_mm, 4) if h.found else None
                ),
            }
            for h in result.hole_details
        ],
    }


@mcp.tool()
def generate_mount(
    mount: str,
    material: str,
    shaft_load_n: float,
    load_type: str = "radial",
    plate_load_n: float = 0.0,
    safety_factor: float = 2.0,
    overhang_mm: float | None = None,
    output_base: str = "output",
    export_step: bool = True,
    plate_width_mm: float | None = None,
    bolt_count: int | None = None,
    bolt_circle_dia_mm: float | None = None,
    bolt_hole_dia_mm: float | None = None,
    center_hole_dia_mm: float | None = None,
    density_kg_m3: float | None = None,
    youngs_modulus_gpa: float | None = None,
    yield_mpa: float | None = None,
    process: str | None = None,
) -> dict[str, Any]:
    """Generate an actual mount: size it, call Zoo's Agent API, write a Zoo
    Design Studio project, and (unless export_step=False) export a STEP file
    and verify it.

    SLOW AND NOT FREE. The Agent API generation alone takes 1-3 minutes and
    consumes credits. Use size_mount if the user only wants dimensions, and
    build_prompt if they want to generate it themselves in Design Studio.

    Setting export_step=False skips the STEP export and verification, which
    also removes the need for the Zoo CLI binary -- the output folder still
    opens directly in Design Studio.
    """
    m, mat = _resolve(
        mount,
        material,
        plate_width_mm=plate_width_mm,
        bolt_count=bolt_count,
        bolt_circle_dia_mm=bolt_circle_dia_mm,
        bolt_hole_dia_mm=bolt_hole_dia_mm,
        center_hole_dia_mm=center_hole_dia_mm,
        density_kg_m3=density_kg_m3,
        youngs_modulus_gpa=youngs_modulus_gpa,
        yield_mpa=yield_mpa,
        process=process,
    )
    decision = (
        mechanics.shaft_support(
            mount=m,
            shaft_load_n=shaft_load_n,
            load_type=load_type,
            offset_mm=overhang_mm,
        )
        if m.kind == "motor"
        else None
    )
    t = mechanics.required_thickness(
        mount=m, material=mat, plate_load_n=plate_load_n
    )

    out_dir = default_output_dir(mount, material, base=output_base)
    prompt = generate.build_prompt(m, mat, t.required_thickness_mm)

    try:
        kcl_code = generate.generate_kcl(prompt)
        kcl_path = generate.write_kcl_project(kcl_code, out_dir)
    except generate.GenerationError as e:
        return {"error": f"generation failed: {e}"}

    payload: dict[str, Any] = {
        "project_dir": str(out_dir),
        "kcl_file": str(kcl_path),
        "shaft": _shaft_payload(decision),
        "required_thickness_mm": round(t.required_thickness_mm, 2),
        "set_by": t.governing_limit,
        "engineering_notes": t.notes,
        "open_in_design_studio": f"zoo app {out_dir}",
    }

    if not export_step:
        payload["note"] = (
            "STEP export and verification skipped. The folder is a valid Zoo Design "
            "Studio project -- open it to render or export."
        )
        return payload

    try:
        step_path = generate.export_step(kcl_path, out_dir)
        result = verify.verify(step_path, m, mat, t.required_thickness_mm)
    except (generate.GenerationError, verify.VerificationError) as e:
        payload["error"] = f"generated, but export/verify failed: {e}"
        return payload

    report_path = out_dir / "inspection_report.md"
    write_report(
        report_path, m.name, mat.name, shaft_load_n, safety_factor, t, result,
        decision=decision, mount_kind=m.kind, process=mat.process,
    )

    payload.update(
        {
            "step_file": str(step_path),
            "report": str(report_path),
            "verification_passed": result.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in result.checks
            ],
        }
    )
    return payload


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
