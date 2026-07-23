"""Verify step: run the generated STEP file back through Zoo's File Format
API and compare *actual* measured properties against what the requested
geometry should produce. This is the check that catches the Agent API
quietly drifting from the spec it was given.

Two independent checks, not one:
- Volume (pure geometry -- doesn't depend on material data at all)
- Mass (geometry x density -- also exercises that the material was applied
  correctly)

Both have to pass for the part to pass. A geometry bug that happens not to
move the mass much (e.g. a hole in the wrong place but the same size) would
still likely show up as a volume difference, and vice versa for a density
mixup -- so checking both catches more than either alone.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import requests

from .materials import Material
from .mount_specs import MountSpec

API_BASE = "https://api.zoo.dev"

# Generation isn't pixel-perfect and our hand-calc volume ignores fillets /
# minor modeling choices the Agent API might make, so allow some slack
# before calling it a fail.
TOLERANCE_FRACTION = 0.15


class VerificationError(RuntimeError):
    pass


@dataclass
class CheckResult:
    expected: float
    actual: float
    percent_diff: float
    passed: bool


@dataclass
class VerificationResult:
    volume: CheckResult
    mass: CheckResult

    @property
    def passed(self) -> bool:
        return self.volume.passed and self.mass.passed


def _api_token() -> str:
    token = os.environ.get("ZOO_API_TOKEN")
    if not token:
        raise VerificationError(
            "ZOO_API_TOKEN is not set. Copy .env.example to .env and add your token."
        )
    return token


def _check(expected: float, actual: float) -> CheckResult:
    percent_diff = abs(actual - expected) / expected * 100
    return CheckResult(expected, actual, percent_diff, (percent_diff / 100) <= TOLERANCE_FRACTION)


def measure_actual_volume_mm3(step_path: Path) -> float:
    headers = {
        "Authorization": f"Bearer {_api_token()}",
        "Content-Type": "application/octet-stream",
    }
    resp = requests.post(
        f"{API_BASE}/file/volume",
        headers=headers,
        params={"src_format": "step", "output_unit": "mm3"},
        data=step_path.read_bytes(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "completed" or "volume" not in data:
        raise VerificationError(f"File Format API did not return a volume: {data}")
    return data["volume"]


def measure_actual_mass_g(step_path: Path, material: Material) -> float:
    headers = {
        "Authorization": f"Bearer {_api_token()}",
        "Content-Type": "application/octet-stream",
    }
    params = {
        "src_format": "step",
        "material_density": material.density_kg_m3,
        "material_density_unit": "kg:m3",
        "output_unit": "g",
    }
    resp = requests.post(
        f"{API_BASE}/file/mass",
        headers=headers,
        params=params,
        data=step_path.read_bytes(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "completed" or "mass" not in data:
        raise VerificationError(f"File Format API did not return a mass: {data}")
    return data["mass"]


def verify(step_path: Path, mount: MountSpec, material: Material, thickness_mm: float) -> VerificationResult:
    expected_volume_mm3 = mount.estimate_volume_mm3(thickness_mm)
    expected_mass_g = expected_volume_mm3 * material.density_kg_m3 * 1e-6

    actual_volume_mm3 = measure_actual_volume_mm3(step_path)
    actual_mass_g = measure_actual_mass_g(step_path, material)

    return VerificationResult(
        volume=_check(expected_volume_mm3, actual_volume_mm3),
        mass=_check(expected_mass_g, actual_mass_g),
    )
