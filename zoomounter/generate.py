"""Generation step: build a constrained prompt, call Zoo's Agent API for
KCL source, then execute that KCL into a STEP file via the Zoo CLI.

The Agent API (POST /ai/text-to-cad/{output_format}) returns parametric KCL
source rather than a ready binary file. Turning KCL into an actual file
requires Zoo's real-time Engine API (a websocket protocol) -- rather than
reimplementing that protocol, we shell out to Zoo's own official CLI
(`zoo kcl export`), which already wraps it.
"""

import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import zoo_cli_path
from .materials import Material
from .mount_specs import MountSpec

API_BASE = "https://api.zoo.dev"
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 300


class GenerationError(RuntimeError):
    pass


def _seat_clause(mount: MountSpec) -> str:
    """Describe the bearing seat in words the Agent API builds accurately.

    Two shapes, because the load case needs two different bearings: a radial
    block gets a plain through-bore at the outer-ring diameter, a thrust block
    gets a blind counterbore with the shaft passing through the floor of it.
    Both feature types were verified against the live API in probes/."""
    if mount.bearing_seat_dia_mm <= 0:
        return ""
    if mount.bearing_seat_depth_mm > 0:
        return (
            f" It has a {_fmt(mount.bearing_seat_dia_mm)}mm diameter counterbore "
            f"{_fmt(mount.bearing_seat_depth_mm)}mm deep, centered on the plate and "
            f"opening from the top face, to seat a {mount.bearing_designation} "
            f"thrust bearing."
        )
    return (
        f" It has a {_fmt(mount.bearing_seat_dia_mm)}mm diameter through-hole "
        f"centered on the plate, sized to seat a {mount.bearing_designation} "
        f"bearing."
    )


def build_prompt(mount: MountSpec, material: Material, thickness_mm: float) -> str:
    """Turn a fully-solved engineering spec into an unambiguous, numbers-only
    prompt for the Agent API. Every dimension is stated explicitly -- holes
    are given as exact (x, y) offsets from the plate center rather than a
    vague "bolt circle" description, so the model has nothing to guess and
    both circular and rectangular hole patterns come out the same way.
    That's what makes the verify step meaningful."""
    hole_list = "; ".join(f"({x}mm, {y}mm)" for x, y in mount.hole_positions)

    center_clause = ""
    if mount.center_hole_dia_mm > 0:
        center_clause = (
            f" It also has a {mount.center_hole_dia_mm}mm diameter through-hole centered on the plate."
        )

    prompt = (
        f"A flat rectangular mounting plate, {mount.plate_width_mm}mm wide (x-axis) x "
        f"{mount.plate_height_mm}mm tall (y-axis) x {round(thickness_mm, 2)}mm thick, centered at the "
        f"origin. It has {len(mount.hole_positions)} through-holes, each "
        f"{mount.bolt_hole_dia_mm}mm in diameter, centered at these (x, y) coordinates relative to the "
        f"plate center: {hole_list}.{center_clause}"
    )

    if getattr(mount, 'host_holes', ()):
        host_h_list = "; ".join(f"({x}mm, {y}mm) diameter {dia}mm" for x, y, dia in mount.host_holes)
        prompt += f" It has {len(mount.host_holes)} additional mounting holes at: {host_h_list}."

    if getattr(mount, 'host_slots', ()):
        host_s_list = "; ".join(f"({x}mm, {y}mm) with length {l}mm along {dir_char}-axis and width {w}mm" for x, y, l, w, dir_char in mount.host_slots)
        prompt += f" It also has {len(mount.host_slots)} adjustment slots centered at: {host_s_list}."

    prompt += _seat_clause(mount)
    prompt += (
        " All holes and slots go through the full thickness of the plate, "
        "except any counterbore, which is blind to its stated depth."
        if mount.bearing_seat_depth_mm > 0
        else " All holes and slots go through the full thickness of the plate."
    )
    return prompt


@dataclass
class ParameterScheme:
    """The named parameters a parametric prompt asks for, and the
    relationships between them.

    Carried alongside the prompt so verification can check the model actually
    honoured the scheme, rather than trusting that it did."""

    declarations: list[tuple[str, str]]  # ordered (name, expression)
    relations: dict[str, list[str]]  # name -> names its expression must use

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.declarations]

    def block(self) -> str:
        width = max((len(n) for n, _ in self.declarations), default=0)
        return "\n".join(f"  {n:<{width}} = {e}" for n, e in self.declarations)


def _fmt(value: float) -> str:
    """Trim float noise so 15.500000000000002 doesn't reach the prompt."""
    return f"{round(value, 4):g}"


def _square_pattern_spacing(mount: MountSpec) -> float | None:
    """If the bolt holes sit at (+/-k, +/-k) for a single k, return the full
    spacing 2k. That is the NEMA case, and it is the one where the hole
    positions are genuinely derivable from one number rather than being four
    independent coordinates."""
    positions = mount.hole_positions
    if len(positions) != 4:
        return None
    magnitudes = {(round(abs(x), 4), round(abs(y), 4)) for x, y in positions}
    if len(magnitudes) != 1:
        return None
    kx, ky = magnitudes.pop()
    if kx == 0 or abs(kx - ky) > 1e-6:
        return None
    return round(kx * 2, 4)


def _slot_spacing(mount: MountSpec) -> float | None:
    """Two slots mirrored across X at y=0 -> their centre-to-centre spacing."""
    slots = mount.host_slots
    if len(slots) != 2:
        return None
    (x1, y1, l1, w1, d1), (x2, y2, l2, w2, d2) = slots
    if abs(y1) > 1e-6 or abs(y2) > 1e-6:
        return None
    if abs(x1 + x2) > 1e-6 or (l1, w1, d1) != (l2, w2, d2):
        return None
    return round(abs(x1) * 2, 4)


def build_parameter_scheme(mount: MountSpec, thickness_mm: float) -> ParameterScheme:
    """Express the part as parameters plus the relationships between them.

    The point is the relationships. The Agent API names its parameters no
    matter how you prompt, but ask with literal coordinates and every one is
    a standalone constant -- editing `slotCenterOffset` in Design Studio then
    leaves the plate width behind, because nothing records that the plate is
    sized *from* the slots. Stating the derivation is what makes the returned
    model survive being edited."""
    decl: list[tuple[str, str]] = []
    rel: dict[str, list[str]] = {}

    # 2dp to match build_prompt. The calc carries more precision than that,
    # but stating 1.0499mm when the literal path states 1.05mm makes the two
    # prompts describe different parts, and no process holds 0.1um anyway.
    decl.append(("plateThickness", f"{round(thickness_mm, 2):g}mm"))
    decl.append(("plateHeight", f"{_fmt(mount.plate_height_mm)}mm"))
    decl.append(("boltHoleDia", f"{_fmt(mount.bolt_hole_dia_mm)}mm"))

    bolt_spacing = _square_pattern_spacing(mount)
    if bolt_spacing is not None:
        decl.append(("boltSpacing", f"{_fmt(bolt_spacing)}mm"))
        decl.append(("boltHoleOffset", "boltSpacing / 2"))
        rel["boltHoleOffset"] = ["boltSpacing"]

    if mount.center_hole_dia_mm > 0:
        decl.append(("centerHoleDia", f"{_fmt(mount.center_hole_dia_mm)}mm"))

    slot_spacing = _slot_spacing(mount)
    if slot_spacing is not None:
        _, _, length, width, _ = mount.host_slots[0]
        # The plate is sized *from* the slots: spacing plus a margin each
        # side. That is the actual design intent, and the relationship a user
        # would want to hold when they widen the slot spacing.
        margin = round((mount.plate_width_mm - slot_spacing) / 2, 4)
        decl.append(("slotSpacing", f"{_fmt(slot_spacing)}mm"))
        decl.append(("slotLength", f"{_fmt(length)}mm"))
        decl.append(("slotWidth", f"{_fmt(width)}mm"))
        decl.append(("edgeMargin", f"{_fmt(margin)}mm"))
        decl.append(("slotCenterOffset", "slotSpacing / 2"))
        decl.append(("plateWidth", "slotSpacing + 2 * edgeMargin"))
        rel["slotCenterOffset"] = ["slotSpacing"]
        rel["plateWidth"] = ["slotSpacing", "edgeMargin"]
    else:
        decl.append(("plateWidth", f"{_fmt(mount.plate_width_mm)}mm"))

    if mount.bearing_seat_dia_mm > 0:
        decl.append(("bearingSeatDia", f"{_fmt(mount.bearing_seat_dia_mm)}mm"))
        if mount.bearing_seat_depth_mm > 0:
            decl.append(("bearingSeatDepth", f"{_fmt(mount.bearing_seat_depth_mm)}mm"))

    return ParameterScheme(declarations=decl, relations=rel)


def build_parametric_prompt(
    mount: MountSpec, material: Material, thickness_mm: float
) -> tuple[str, ParameterScheme]:
    """Prompt for a model that stays editable, rather than one that merely
    has the right dimensions today.

    Returns the prompt and the scheme it asks for, so `verify` can confirm
    the relationships actually survived into the KCL."""
    scheme = build_parameter_scheme(mount, thickness_mm)
    names = set(scheme.names)

    body: list[str] = [
        "Define these named parameters at the top of the file, using exactly "
        "these names. Where an expression is given, write the expression -- "
        "do not replace it with a pre-computed number:",
        "",
        scheme.block(),
        "",
        "Then build the part from those parameters, referring to them by name "
        "rather than repeating their values:",
        "",
        "A flat rectangular mounting plate, plateWidth wide (x-axis) by "
        "plateHeight tall (y-axis) by plateThickness thick, centered at the "
        "origin.",
    ]

    if "boltHoleOffset" in names:
        body.append(
            f"It has {len(mount.hole_positions)} through-holes of boltHoleDia "
            "diameter, centered at (-boltHoleOffset, -boltHoleOffset), "
            "(boltHoleOffset, -boltHoleOffset), (boltHoleOffset, "
            "boltHoleOffset) and (-boltHoleOffset, boltHoleOffset)."
        )
    else:
        # Positions fall back to literals -- a circular or irregular pattern
        # is not derivable from one number -- but the diameter is still a
        # declared parameter and has to be referenced by name. Writing the
        # number here left boltHoleDia declared and unused, which is how the
        # bearing seat clause went missing too.
        hole_list = "; ".join(
            f"({_fmt(x)}mm, {_fmt(y)}mm)" for x, y in mount.hole_positions
        )
        body.append(
            f"It has {len(mount.hole_positions)} through-holes of boltHoleDia "
            f"diameter, centered at these (x, y) coordinates relative to the "
            f"plate center: {hole_list}."
        )

    if "centerHoleDia" in names:
        body.append(
            "It also has a through-hole of centerHoleDia diameter centered on "
            "the plate."
        )

    if "slotCenterOffset" in names:
        _, _, _, _, direction = mount.host_slots[0]
        body.append(
            "It also has 2 adjustment slots cut through the full thickness, "
            "each slotLength long along the "
            f"{direction}-axis and slotWidth wide with semicircular ends, "
            "centered at (-slotCenterOffset, 0) and (slotCenterOffset, 0)."
        )
    elif mount.host_slots:
        slot_list = "; ".join(
            f"({_fmt(x)}mm, {_fmt(y)}mm) with length {_fmt(l)}mm along "
            f"{d}-axis and width {_fmt(w)}mm"
            for x, y, l, w, d in mount.host_slots
        )
        body.append(
            f"It also has {len(mount.host_slots)} adjustment slots centered "
            f"at: {slot_list}."
        )

    if mount.host_holes:
        host_list = "; ".join(
            f"({_fmt(x)}mm, {_fmt(y)}mm) diameter {_fmt(d)}mm"
            for x, y, d in mount.host_holes
        )
        body.append(
            f"It has {len(mount.host_holes)} additional mounting holes at: "
            f"{host_list}."
        )

    if mount.bearing_seat_dia_mm > 0:
        if mount.bearing_seat_depth_mm > 0:
            body.append(
                "It has a counterbore of bearingSeatDia diameter and "
                "bearingSeatDepth depth, centered on the plate and opening "
                f"from the top face, to seat a {mount.bearing_designation} thrust "
                "bearing. The counterbore is blind -- it does not go through "
                "the plate."
            )
        else:
            body.append(
                "It has a through-hole of bearingSeatDia diameter centered on "
                f"the plate, sized to seat a {mount.bearing_designation} bearing."
            )

    if mount.bearing_seat_depth_mm > 0:
        body.append(
            "All holes go through the full thickness of the plate, except the "
            "counterbore, which is blind to its stated depth."
        )
    else:
        body.append("All holes and slots go through the full thickness of the plate.")
    return "\n".join(body), scheme


def _api_token() -> str:
    token = os.environ.get("ZOO_API_TOKEN")
    if not token:
        raise GenerationError(
            "ZOO_API_TOKEN is not set. Copy .env.example to .env and add your token."
        )
    return token


def generate_kcl(prompt: str, on_status=None) -> str:
    """Call the Agent API's text-to-CAD endpoint and poll until it returns
    KCL source for the requested geometry.

    `on_status`, if given, is called as `on_status(elapsed_seconds, status)`
    after each poll -- this generation step routinely takes 1-3 minutes, so
    giving real feedback tied to the actual job status (rather than staying
    silent) matters for the CLI not feeling hung."""
    headers = {"Authorization": f"Bearer {_api_token()}", "Content-Type": "application/json"}

    start = time.time()
    resp = requests.post(
        f"{API_BASE}/ai/text-to-cad/step",
        headers=headers,
        json={"prompt": prompt},
        timeout=30,
    )
    resp.raise_for_status()
    job = resp.json()
    job_id = job["id"]

    deadline = start + POLL_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        poll = requests.get(
            f"{API_BASE}/user/text-to-cad/{job_id}",
            headers=headers,
            timeout=30,
        )
        poll.raise_for_status()
        data = poll.json()
        status = data.get("status")
        if on_status:
            on_status(time.time() - start, status)
        if status == "completed":
            code = data.get("code")
            if not code:
                raise GenerationError("Agent API reported completed but returned no KCL code.")
            return code
        if status == "failed":
            raise GenerationError(f"Agent API generation failed: {data.get('error')}")

    raise GenerationError(f"Timed out after {POLL_TIMEOUT_S}s waiting for generation job {job_id}.")


def _write_project_toml(output_dir: Path) -> None:
    """Write a minimal project.toml so output_dir is a ready-made Zoo Design
    Studio project -- openable directly, without the app having to
    auto-scaffold one the first time it's pointed at the folder."""
    project_toml = output_dir / "project.toml"
    if project_toml.exists():
        return
    project_id = uuid.uuid4()
    project_toml.write_text(
        f'[settings.meta]\nid = "{project_id}"\n\n[settings.app]\n\n[settings.modeling]\n',
        encoding="utf-8",
    )


def _zoo_cli() -> str:
    return zoo_cli_path()


def write_kcl_project(kcl_code: str, output_dir: Path) -> Path:
    """Write KCL source + a project.toml to output_dir. This alone is
    enough for the folder to open directly as a Zoo Design Studio project --
    no STEP export or verification required. Returns the path to main.kcl.

    The KCL is written as main.kcl (the filename Zoo Design Studio expects
    for a project's entry point -- confirmed by observing what the app
    itself names files when it scaffolds a folder)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    kcl_path = output_dir / "main.kcl"
    kcl_path.write_text(kcl_code, encoding="utf-8")
    _write_project_toml(output_dir)
    return kcl_path


def snapshot_preview(kcl_path: Path, image_path: Path, angle: str = "iso") -> Path:
    """Render a quick preview image of a KCL file via `zoo kcl snapshot`.
    Writes to whatever `image_path` you give it -- pass a temp-directory
    path if you don't want a preview image left behind in the project
    folder. Returns image_path on success."""
    image_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [_zoo_cli(), "kcl", "snapshot", "--angle", angle, str(kcl_path), str(image_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as e:
        raise GenerationError(
            f"Zoo CLI not found at '{_zoo_cli()}'. Install it and ensure it's on PATH, "
            f"or set ZOO_CLI_PATH to its location."
        ) from e

    if result.returncode != 0:
        raise GenerationError(f"zoo kcl snapshot failed:\n{result.stderr or result.stdout}")
    if not image_path.exists():
        raise GenerationError(f"Expected preview image not found at {image_path}")
    return image_path


def export_step(kcl_path: Path, output_dir: Path) -> Path:
    """Execute the KCL at kcl_path via the Zoo CLI to produce a STEP file
    under output_dir/export/. Requires the `zoo` CLI to be installed and
    authenticated (same ZOO_API_TOKEN env var works for both)."""
    export_dir = output_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [_zoo_cli(), "kcl", "export", "--output-format", "step", str(kcl_path), str(export_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as e:
        raise GenerationError(
            f"Zoo CLI not found at '{_zoo_cli()}'. Install it and ensure it's on PATH, "
            f"or set ZOO_CLI_PATH to its location."
        ) from e

    if result.returncode != 0:
        raise GenerationError(f"zoo kcl export failed:\n{result.stderr or result.stdout}")

    step_path = export_dir / "output.step"
    if not step_path.exists():
        raise GenerationError(f"Expected output STEP file not found at {step_path}")
    return step_path
