"""Does the Agent API return the same geometry twice?

Nobody had checked. Everything ZooMounter claims rests on the Agent API
hitting the coordinates it is given, and that claim was tested once per shape
-- never twice on the same shape. A generative model that is accurate on
average but not repeatable would still pass every verification run and still
break a demo, because the part you showed yesterday is not the part you get
today.

This runs one fixed spec N times and diffs the parsed geometry, not the text.
Comparing KCL source would answer a different and less interesting question:
two files can differ in variable order or whitespace and describe the same
solid. What matters is whether the HOLES land in the same places.

Costs credits and takes N x 1-3 minutes. Not part of the test suite.

    python -m probes.determinism --runs 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zoomounter import generate
from zoomounter.config import load_environment
from zoomounter.materials import get_material
from zoomounter.mount_specs import apply_host_mount, get_mount
from zoomounter.step_inspect import parse_step

OUT = Path(__file__).parent / "determinism"


def _fingerprint(step_path: Path) -> dict:
    """Geometry reduced to what a user would notice if it changed.

    Sorted, because hole ORDER in the file is an implementation detail and a
    reordering is not a difference anyone building the part would see.
    """
    geo = parse_step(step_path)
    return {
        "bbox_mm": [
            round(geo.bbox.width_mm, 3),
            round(geo.bbox.height_mm, 3),
            round(geo.bbox.thickness_mm, 3),
        ],
        "holes": sorted(
            [round(c.x_mm, 3), round(c.y_mm, 3), round(c.diameter_mm, 3)]
            for c in geo.circles
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="determinism")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mount", default="nema23")
    ap.add_argument("--material", default="aluminum_6061")
    ap.add_argument("--thickness-mm", type=float, default=7.0)
    args = ap.parse_args(argv)

    load_environment()
    OUT.mkdir(parents=True, exist_ok=True)

    # One spec, fixed. Any drift between runs is the API, not the input --
    # which is why this cannot be reconstructed from ordinary output folders
    # whose specs differed.
    mount = apply_host_mount(get_mount(args.mount), "2020-slots")
    material = get_material(args.material)
    prompt, _scheme = generate.build_parametric_prompt(
        mount, material, args.thickness_mm
    )
    (OUT / "prompt.txt").write_text(prompt, encoding="utf-8")

    fingerprints = []
    for i in range(1, args.runs + 1):
        run_dir = OUT / f"run{i}"
        print(f"[{i}/{args.runs}] generating...", flush=True)
        kcl = generate.generate_kcl(prompt)
        kcl_path = generate.write_kcl_project(kcl, run_dir)
        step = generate.export_step(kcl_path, run_dir)
        fp = _fingerprint(step)
        fingerprints.append(fp)
        (run_dir / "fingerprint.json").write_text(
            json.dumps(fp, indent=2), encoding="utf-8"
        )
        print(f"      {len(fp['holes'])} holes, bbox {fp['bbox_mm']}", flush=True)

    identical = all(f == fingerprints[0] for f in fingerprints)
    summary = {
        "runs": args.runs,
        "spec": f"{args.mount} / {args.material} / {args.thickness_mm}mm / 2020-slots",
        "geometry_identical": identical,
        "fingerprints": fingerprints,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    if identical:
        print(f"IDENTICAL across {args.runs} runs: same holes, same bounding box.")
    else:
        print(f"DIFFERENT across {args.runs} runs. Diffs:")
        base = fingerprints[0]
        for i, f in enumerate(fingerprints[1:], start=2):
            if f["bbox_mm"] != base["bbox_mm"]:
                print(f"  run{i} bbox {f['bbox_mm']} vs run1 {base['bbox_mm']}")
            only_new = [h for h in f["holes"] if h not in base["holes"]]
            only_old = [h for h in base["holes"] if h not in f["holes"]]
            for h in only_old:
                print(f"  run{i} MISSING hole {h}")
            for h in only_new:
                print(f"  run{i} EXTRA   hole {h}")
    print(f"\nWritten to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
