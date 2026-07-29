"""Item 4: probe what Zoo's Agent API can actually build.

Host-side slots were designed and coded without anyone checking whether
text-to-CAD can produce a slot at all. This script answers that, and the
same question for the other features a mount generator would want.

Method, and the reason it is worth trusting: every prompt states exact
numbers, and every result is checked by parsing the returned STEP file --
never by looking at a render and deciding it seems right. A feature counts
as supported only if the geometry that comes back measures correctly.

That distinction matters here specifically. The project has already shipped
four bugs past a green test suite because the thing being compared against
was itself wrong, so "the API returned something" is not evidence. What
follows compares against arithmetic, not against our own spec table.

Costs credits: one generation per feature, plus a mass-properties call for
the two features whose evidence is volumetric.

    python probes/probe_features.py            # all features
    python probes/probe_features.py slot boss  # named subset
"""

from __future__ import annotations

import json
import math
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomounter import generate, verify  # noqa: E402
from zoomounter.config import load_environment  # noqa: E402
from zoomounter.step_inspect import StepGeometry, parse_step  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "results"

# How close a measurement has to be before we call it correct. The API has
# previously hit stated dimensions to sub-0.001mm, so a 0.5mm tolerance is
# loose on purpose -- we are asking "did it build the feature", not "how
# precise is it".
TOL_MM = 0.5
VOLUME_TOL_PCT = 5.0


@dataclass
class Finding:
    label: str
    expected: str
    actual: str
    ok: bool


@dataclass
class Probe:
    name: str
    question: str
    prompt: str
    needs_volume: bool = False
    findings: list[Finding] = field(default_factory=list)


def _near(a: float, b: float, tol: float = TOL_MM) -> bool:
    return abs(a - b) <= tol


def _circles_near(geo: StepGeometry, x: float, y: float, dia: float) -> bool:
    return any(
        _near(c.x_mm, x) and _near(c.y_mm, y) and _near(c.diameter_mm, dia)
        for c in geo.circles
    )


def _bbox_findings(geo: StepGeometry, w: float, h: float, t: float) -> list[Finding]:
    return [
        Finding("bbox width", f"{w:.2f}mm", f"{geo.bbox.width_mm:.2f}mm", _near(geo.bbox.width_mm, w)),
        Finding("bbox height", f"{h:.2f}mm", f"{geo.bbox.height_mm:.2f}mm", _near(geo.bbox.height_mm, h)),
        Finding("bbox thickness", f"{t:.2f}mm", f"{geo.bbox.thickness_mm:.2f}mm", _near(geo.bbox.thickness_mm, t)),
    ]


# ---------------------------------------------------------------------------
# The probes. One feature each, so a failure names a feature rather than a
# prompt. Every dimension is stated explicitly, because vagueness is what
# produces featureless boxes.
# ---------------------------------------------------------------------------

PROBES: dict[str, Probe] = {}


def probe(name: str, question: str, needs_volume: bool = False):
    def wrap(fn):
        PROBES[name] = Probe(
            name=name, question=question, prompt=fn(), needs_volume=needs_volume
        )
        return fn

    return wrap


def checker_for(name: str):
    """Resolved at call time, not decoration time -- each check_* function is
    defined below the probe it belongs to, so it does not exist yet when the
    decorator runs."""
    fn = globals().get(f"check_{name}")
    if fn is None:
        raise RuntimeError(f"no check_{name}() defined for probe '{name}'")
    return fn


@probe("holes", "Control: plain through-holes, already known to work.")
def _p_holes():
    return (
        "A flat rectangular plate 60mm wide, 60mm deep and 6mm thick, centered "
        "on the origin. It has 4 through holes of 5mm diameter, with centers at "
        "(-20, -20), (20, -20), (20, 20) and (-20, 20). The holes go through the "
        "full 6mm thickness."
    )


def check_holes(geo, vol):
    out = _bbox_findings(geo, 60, 60, 6)
    for x, y in [(-20, -20), (20, -20), (20, 20), (-20, 20)]:
        out.append(
            Finding(
                f"hole at ({x}, {y})", "dia 5.00mm",
                "found" if _circles_near(geo, x, y, 5.0) else "MISSING",
                _circles_near(geo, x, y, 5.0),
            )
        )
    return out


@probe("slot", "THE BLOCKER for item 5: can it build an obround adjustment slot?")
def _p_slot():
    return (
        "A flat rectangular plate 80mm wide, 60mm deep and 6mm thick, centered "
        "on the origin. It has 2 slots cut through the full thickness. Each slot "
        "is 20mm long and 6mm wide with semicircular ends, with its long axis "
        "along the X direction. The first slot is centered at (-25, 0) and the "
        "second at (25, 0)."
    )


def check_slot(geo, vol):
    # A slot of length L and width W reads as two semicircles of diameter W,
    # their centers offset +/-(L-W)/2 from the slot center. Same rule verify.py
    # already uses, so a pass here means the existing verifier would work.
    off = (20 - 6) / 2  # 7.0
    out = _bbox_findings(geo, 80, 60, 6)
    for cx in (-25, 25):
        for sign in (-1, 1):
            x = cx + sign * off
            ok = _circles_near(geo, x, 0, 6.0)
            out.append(
                Finding(
                    f"slot end at ({x:.1f}, 0)", "dia 6.00mm",
                    "found" if ok else "MISSING", ok,
                )
            )
    return out


@probe("counterbore", "Can it build a counterbored hole (two concentric diameters)?")
def _p_counterbore():
    return (
        "A flat rectangular plate 60mm wide, 60mm deep and 10mm thick, centered "
        "on the origin. It has one counterbored hole at the center: a 5mm "
        "diameter hole through the full 10mm thickness, opening into a 10mm "
        "diameter counterbore 4mm deep from the top face."
    )


def check_counterbore(geo, vol):
    out = _bbox_findings(geo, 60, 60, 10)
    for dia in (5.0, 10.0):
        ok = _circles_near(geo, 0, 0, dia)
        out.append(
            Finding(
                f"concentric circle dia {dia}mm", f"dia {dia:.2f}mm at (0, 0)",
                "found" if ok else "MISSING", ok,
            )
        )
    return out


@probe("pocket", "Can it build a blind rectangular pocket?", needs_volume=True)
def _p_pocket():
    return (
        "A flat rectangular plate 60mm wide, 60mm deep and 10mm thick, centered "
        "on the origin. A rectangular pocket 30mm by 30mm and 4mm deep is cut "
        "into the top face, centered on the origin. The pocket is blind -- it "
        "does not go through the plate."
    )


def check_pocket(geo, vol):
    out = _bbox_findings(geo, 60, 60, 10)
    solid = 60 * 60 * 10
    expected = solid - (30 * 30 * 4)  # 36000 - 3600 = 32400
    if vol is None:
        out.append(Finding("volume", f"{expected:.0f}mm^3", "not measured", False))
        return out
    pct = abs(vol - expected) / expected * 100
    out.append(
        Finding(
            "volume (proves the pocket is cut and blind)",
            f"{expected:.0f}mm^3",
            f"{vol:.0f}mm^3 ({pct:.1f}% off; solid block would be {solid:.0f})",
            pct <= VOLUME_TOL_PCT,
        )
    )
    return out


@probe("boss", "Can it build a raised boss (added material, not a cut)?")
def _p_boss():
    return (
        "A flat rectangular plate 60mm wide, 60mm deep and 6mm thick, centered "
        "on the origin. A cylindrical boss 20mm in diameter and 5mm tall rises "
        "from the center of the top face, so the overall height of the part is "
        "11mm."
    )


def check_boss(geo, vol):
    out = _bbox_findings(geo, 60, 60, 11)
    ok = any(_near(c.diameter_mm, 20.0) for c in geo.circles)
    out.append(
        Finding("boss diameter", "dia 20.00mm circle present",
                "found" if ok else "MISSING", ok)
    )
    return out


@probe("chamfer", "Can it apply a chamfer to named edges?", needs_volume=True)
def _p_chamfer():
    # 15mm, not the 3mm you would actually use. A 3mm chamfer on a 60mm plate
    # removes 0.5% of the volume, which is inside the measurement tolerance --
    # the check would have passed an unchamfered block. Sized so that "chamfer
    # missing" and "chamfer present" are far apart.
    return (
        "A flat rectangular plate 60mm wide, 60mm deep and 6mm thick, centered "
        "on the origin. Each of the 4 vertical corner edges has a 15mm by 15mm "
        "chamfer running the full 6mm height."
    )


def check_chamfer(geo, vol):
    out = _bbox_findings(geo, 60, 60, 6)
    solid = 60 * 60 * 6
    expected = solid - 4 * (0.5 * 15 * 15 * 6)  # 21600 - 2700 = 18900
    if vol is None:
        out.append(Finding("volume", f"{expected:.0f}mm^3", "not measured", False))
        return out
    pct = abs(vol - expected) / expected * 100
    out.append(
        Finding(
            "volume (proves 4 chamfers, not 0)",
            f"{expected:.0f}mm^3",
            f"{vol:.0f}mm^3 ({pct:.1f}% off; unchamfered would be {solid:.0f})",
            pct <= VOLUME_TOL_PCT,
        )
    )
    return out


# ---------------------------------------------------------------------------


def run_probe(p: Probe, outdir: Path) -> dict:
    print(f"\n{'=' * 72}\n{p.name.upper()}  --  {p.question}\n{'=' * 72}")
    print(f"prompt: {p.prompt}\n")
    record: dict = {"name": p.name, "question": p.question, "prompt": p.prompt}
    started = time.time()

    def status(elapsed, st):
        print(f"  [{elapsed:5.1f}s] {st}", flush=True)

    try:
        kcl = generate.generate_kcl(p.prompt, on_status=status)
    except Exception as e:
        record["outcome"] = "generation_failed"
        record["error"] = f"{type(e).__name__}: {e}"
        print(f"  GENERATION FAILED: {e}")
        return record

    record["generation_seconds"] = round(time.time() - started, 1)
    work = outdir / p.name
    kcl_path = generate.write_kcl_project(kcl, work)
    record["kcl_path"] = str(kcl_path.relative_to(RESULTS_DIR.parent))
    record["kcl"] = kcl
    print(f"  KCL written ({len(kcl.splitlines())} lines)")

    try:
        step_path = generate.export_step(kcl_path, work)
    except Exception as e:
        record["outcome"] = "export_failed"
        record["error"] = f"{type(e).__name__}: {e}"
        print(f"  EXPORT FAILED: {e}")
        return record

    geo = parse_step(step_path)
    record["step_path"] = str(step_path.relative_to(RESULTS_DIR.parent))
    record["circles_found"] = len(geo.circles)
    record["bbox"] = {
        "w": round(geo.bbox.width_mm, 3),
        "h": round(geo.bbox.height_mm, 3),
        "t": round(geo.bbox.thickness_mm, 3),
    }

    vol = None
    if p.needs_volume:
        try:
            vol = verify.measure_actual_volume_mm3(step_path)
            record["volume_mm3"] = round(vol, 1)
        except Exception as e:
            record["volume_error"] = f"{type(e).__name__}: {e}"
            print(f"  volume measurement failed: {e}")

    findings = checker_for(p.name)(geo, vol)
    record["findings"] = [
        {"label": f.label, "expected": f.expected, "actual": f.actual, "ok": f.ok}
        for f in findings
    ]
    record["outcome"] = "supported" if all(f.ok for f in findings) else "not_supported"

    print(f"  circles found: {len(geo.circles)}   bbox: "
          f"{geo.bbox.width_mm:.2f} x {geo.bbox.height_mm:.2f} x {geo.bbox.thickness_mm:.2f}")
    for f in findings:
        print(f"    [{'OK ' if f.ok else 'BAD'}] {f.label}: expected {f.expected}, got {f.actual}")
    print(f"  ==> {record['outcome'].upper()}")
    return record


def main(argv: list[str]) -> int:
    load_environment()
    names = argv or list(PROBES)
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        print(f"unknown probe(s): {unknown}. available: {list(PROBES)}")
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for n in names:
        try:
            records.append(run_probe(PROBES[n], RESULTS_DIR))
        except Exception:
            traceback.print_exc()
            records.append({"name": n, "outcome": "probe_crashed",
                            "error": traceback.format_exc()})
        (RESULTS_DIR / "probe_results.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8"
        )

    print(f"\n\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    for r in records:
        print(f"  {r['name']:12} {r.get('outcome','?')}")
    print(f"\nwrote {RESULTS_DIR / 'probe_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
