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
from pathlib import Path

import requests

from .materials import Material
from .mount_specs import MountSpec

API_BASE = "https://api.zoo.dev"
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 300


class GenerationError(RuntimeError):
    pass


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

    return (
        f"A flat rectangular mounting plate, {mount.plate_width_mm}mm wide (x-axis) x "
        f"{mount.plate_height_mm}mm tall (y-axis) x {round(thickness_mm, 2)}mm thick, centered at the "
        f"origin. It has {len(mount.hole_positions)} through-holes, each "
        f"{mount.bolt_hole_dia_mm}mm in diameter, centered at these (x, y) coordinates relative to the "
        f"plate center: {hole_list}.{center_clause} All holes go through the full thickness of the plate."
    )


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
    return os.environ.get("ZOO_CLI_PATH", "zoo")


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
