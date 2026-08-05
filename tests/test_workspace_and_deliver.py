"""The workspace, the retention policy, and delivering a finished run.

Offline. Every run folder here is a fake built from files on disk -- none of
this needs the Agent API.

Pruning is the only destructive thing ZooMounter does, so most of this file is
about what it REFUSES to touch. A retention policy that is 99% right deletes
someone's work the other 1% of the time.
"""

import os
from pathlib import Path

import pytest

from zoomounter import deliver as deliver_mod
from zoomounter import workspace, zoo_project
from zoomounter.mechanics import Check

KCL = """@settings(defaultLengthUnit = mm, kclVersion = 2.0)
plateSketch = startSketchOn(XY)
finishedPlate = extrude(plateSketch, length = 7)
  |> appearance(color = "#aabbcc")
"""


def _fake_run(root: Path, name: str = "nema17_pla_20260805_120000") -> Path:
    """A run folder shaped like a real one, without spending credits.

    The assembly is built by the real writer rather than faked, so these tests
    exercise the file set delivery actually sees -- including parameters.kcl,
    which a hand-stubbed assembly/ silently lacked and which delivery needs.
    """
    from zoomounter import assembly as assembly_mod
    from zoomounter.mount_specs import get_mount

    run = root / name
    (run / "export").mkdir(parents=True, exist_ok=True)
    (run / "main.kcl").write_text(KCL, encoding="utf-8")
    (run / "project.toml").write_text('default_file = "main.kcl"\n', encoding="utf-8")
    (run / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "inspection_report.md").write_text(
        "# Report\n\n![Rendered part](preview.png)\n", encoding="utf-8"
    )
    (run / "export" / "output.step").write_text("ISO-10303-21;\n", encoding="utf-8")

    assembly_mod.write_assembly(
        run / "assembly", get_mount("nema17"), 4.0, KCL, base_mount=get_mount("nema17")
    )
    (run / "assembly-exploded").mkdir(exist_ok=True)
    (run / "assembly-exploded" / "main.kcl").write_text(KCL, encoding="utf-8")
    return run


# ---------------------------------------------------------------------------
# Where runs go. Never the current directory.
# ---------------------------------------------------------------------------


def test_runs_go_to_the_workspace_not_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv(workspace.ENV_HOME, str(tmp_path / "zmhome"))
    assert workspace.runs_dir() == (tmp_path / "zmhome" / "runs").resolve()


def test_the_home_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(workspace.ENV_HOME, str(tmp_path / "elsewhere"))
    assert workspace.home() == (tmp_path / "elsewhere").resolve()


def test_an_explicit_runs_dir_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(workspace.ENV_HOME, str(tmp_path / "ignored"))
    assert workspace.runs_dir(tmp_path / "chosen") == (tmp_path / "chosen").resolve()


def test_the_default_output_dir_is_not_relative(monkeypatch, tmp_path):
    """The regression this feature exists for: `./output/` relative to wherever
    you were standing littered whatever folder you happened to be in."""
    from zoomounter.cli import default_output_dir

    monkeypatch.setenv(workspace.ENV_HOME, str(tmp_path / "zmhome"))
    out = default_output_dir("nema17", "pla")
    assert out.is_absolute()
    assert Path.cwd() not in out.parents


# ---------------------------------------------------------------------------
# What a run keeps.
# ---------------------------------------------------------------------------


def test_trimming_drops_the_exploded_assembly(tmp_path):
    run = _fake_run(tmp_path)
    workspace.trim_run(run)
    assert not (run / "assembly-exploded").exists()
    assert (run / "assembly").exists(), "the to-scale assembly is the useful one"


def test_trimming_keeps_the_report_and_its_image_together(tmp_path):
    """inspection_report.md embeds preview.png by RELATIVE path. Deleting the
    image to save 90KB silently breaks every report in the folder -- a saving
    nobody asked for at a cost nobody would accept."""
    run = _fake_run(tmp_path)
    workspace.trim_run(run)
    report = run / "inspection_report.md"
    assert report.exists()
    assert (run / "preview.png").exists()
    assert "preview.png" in report.read_text(encoding="utf-8")


def test_trimming_keeps_what_a_person_would_open(tmp_path):
    run = _fake_run(tmp_path)
    workspace.trim_run(run)
    for name in ("main.kcl", "preview.png", "inspection_report.md"):
        assert (run / name).exists()
    assert (run / "export" / "output.step").exists()


def test_trimming_removes_design_studio_drift(tmp_path):
    """Zoo scaffolds thumbnail.png into any folder it opens. ZooMounter never
    writes one, so any that appear are drift rather than output."""
    run = _fake_run(tmp_path)
    (run / "thumbnail.png").write_bytes(b"x")
    workspace.trim_run(run)
    assert not (run / "thumbnail.png").exists()


# ---------------------------------------------------------------------------
# Pruning old runs, and everything it must refuse.
# ---------------------------------------------------------------------------


def test_pruning_keeps_the_most_recent_n(tmp_path):
    runs = []
    for i in range(7):
        run = _fake_run(tmp_path, f"run{i}")
        os.utime(run, (1_700_000_000 + i, 1_700_000_000 + i))
        runs.append(run)
    result = workspace.prune_runs(tmp_path, keep=3)
    assert len(result.removed) == 4
    assert all(r.exists() for r in runs[-3:])
    assert not any(r.exists() for r in runs[:4])


def test_pruning_refuses_a_user_supplied_runs_dir(tmp_path):
    """A folder someone pointed us at is theirs. Pruning only ever touches the
    workspace ZooMounter owns."""
    for i in range(4):
        _fake_run(tmp_path, f"run{i}")
    result = workspace.prune_runs(tmp_path, keep=1, user_supplied=True)
    assert result.did_nothing
    assert "--runs-dir" in result.refused
    assert len(list(tmp_path.iterdir())) == 4


def test_pruning_refuses_anything_under_a_git_repo(tmp_path):
    """A run folder someone version-controls is not a cache, whatever it looks
    like."""
    (tmp_path / ".git").mkdir()
    runs = tmp_path / "runs"
    for i in range(4):
        _fake_run(runs, f"run{i}")
    result = workspace.prune_runs(runs, keep=1)
    assert result.did_nothing
    assert "git" in result.refused
    assert len(list(runs.iterdir())) == 4


@pytest.mark.parametrize("protected", ["tests", "examples", "probes"])
def test_pruning_never_touches_protected_folders(tmp_path, protected):
    root = tmp_path / protected
    for i in range(4):
        _fake_run(root, f"run{i}")
    result = workspace.prune_runs(root, keep=1)
    assert result.did_nothing
    assert len(list(root.iterdir())) == 4


def test_a_refusal_is_stated_rather_than_silent(tmp_path):
    """The repo convention: anything the tool declines to do is said out loud."""
    result = workspace.prune_runs(tmp_path, keep=1, user_supplied=True)
    assert result.refused, "a refusal with no reason is indistinguishable from a bug"


def test_keep_zero_is_refused_rather_than_deleting_everything(tmp_path):
    _fake_run(tmp_path, "run0")
    result = workspace.prune_runs(tmp_path, keep=0)
    assert result.did_nothing
    assert (tmp_path / "run0").exists()


def test_listing_runs_ignores_protected_names(tmp_path):
    _fake_run(tmp_path, "run0")
    (tmp_path / "examples").mkdir()
    (tmp_path / ".hidden").mkdir()
    names = {p.name for p in workspace.list_runs(tmp_path)}
    assert names == {"run0"}


# ---------------------------------------------------------------------------
# Delivery: the export line, the STEP, and the guide.
# ---------------------------------------------------------------------------


def test_part_only_delivery_carries_its_export_line(tmp_path):
    """The step users cannot reliably do by hand. Agent API output is a
    standalone script that exports nothing, so `import x from "x.kcl"` against
    it simply fails.

    assembly=False, because a whole assembly is imported whole-file and needs
    no export -- see the assembly tests below."""
    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(
        run, tmp_path / "dest", name="xMotorMount", assembly=False
    )
    body = result.kcl_path.read_text(encoding="utf-8")
    assert "export xMotorMount = finishedPlate" in body


def test_delivering_writes_a_step_for_other_cad(tmp_path):
    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(run, tmp_path / "dest", name="m", assembly=False)
    assert result.step_path is not None
    assert result.step_path.read_text(encoding="utf-8").startswith("ISO-10303-21")


def test_delivering_into_a_zoo_project_wires_up_main_kcl(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.toml").write_text('default_file = "main.kcl"\n', encoding="utf-8")
    (project / "main.kcl").write_text("@settings(defaultLengthUnit = mm)\n", encoding="utf-8")

    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(run, project, name="xMotorMount", assembly=False)

    assert result.project is not None
    assert result.entry_modified
    entry = (project / "main.kcl").read_text(encoding="utf-8")
    assert 'import xMotorMount from "xMotorMount.kcl"' in entry


def test_delivering_somewhere_that_is_not_a_project_says_so(tmp_path):
    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(run, tmp_path / "plain", name="m", assembly=False)
    assert result.project is None
    guide = result.guide_path.read_text(encoding="utf-8")
    assert "not a Zoo project" in guide
    assert 'import m from "m.kcl"' in guide, "it must hand over the line to paste"


def test_a_folder_that_is_not_a_run_is_rejected_clearly(tmp_path):
    (tmp_path / "random").mkdir()
    with pytest.raises(deliver_mod.DeliveryError, match="does not look like"):
        deliver_mod.deliver(tmp_path / "random", tmp_path / "dest")


# ---------------------------------------------------------------------------
# HOW-TO-USE.md: what the tool did NOT do.
# ---------------------------------------------------------------------------


def test_the_guide_states_the_origin_limitation(tmp_path):
    """An unstated limitation reads as a solved problem. The part is at the
    origin and ZooMounter has no idea where it belongs."""
    run = _fake_run(tmp_path / "runs")
    guide = deliver_mod.deliver(
        run, tmp_path / "dest", assembly=False
    ).guide_path.read_text("utf-8")
    assert "ORIGIN" in guide
    assert "mate or constraint system" in guide
    assert "translate" in guide


def test_the_guide_warns_the_reference_bodies_are_not_your_hardware(tmp_path):
    run = _fake_run(tmp_path / "runs")
    guide = deliver_mod.deliver(run, tmp_path / "dest").guide_path.read_text("utf-8")
    assert "not**" in guide or "NOT" in guide
    assert "manufacturer" in guide


def test_the_guide_carries_the_runs_warnings(tmp_path):
    """A delivered part that arrives without its warnings defeats the point of
    having computed them."""
    run = _fake_run(tmp_path / "runs")
    verdict = Check(
        level="LOUD WARN",
        message="Radial shaft load 200N exceeds the published limit 75N.",
        remedy="Support the shaft in its own bearing.",
        code="shaft_limit",
    )
    guide = deliver_mod.deliver(
        run, tmp_path / "dest", checks=[verdict]
    ).guide_path.read_text("utf-8")
    assert "exceeds the published limit" in guide
    assert "Support the shaft in its own bearing" in guide


def test_every_delivery_check_names_a_declared_rule():
    from zoomounter import rules

    for check in (deliver_mod.origin_placement_check(), deliver_mod.reference_geometry_check()):
        assert rules.get(check.code).statement


def test_clipboard_kcl_is_the_generated_part_not_a_prompt(tmp_path):
    """Pasting a PROMPT into Design Studio regenerates the part in Zookeeper --
    different geometry, unverified, not repeatable. Avoiding exactly that is
    why this tool exists, so the clipboard has to hold the verified KCL."""
    run = _fake_run(tmp_path / "runs")
    text = deliver_mod.kcl_for_clipboard(run, name="m")
    assert "export m = finishedPlate" in text
    assert "@settings" in text
    assert "Generate a flat rectangular" not in text


# ---------------------------------------------------------------------------
# Delivering the ASSEMBLY, which is the default.
#
# A mount plate on its own is the part ZooMounter designed but not the thing
# the user was working on. The run already decided the bearing, the standoff
# height and the coupling; handing over one plate throws all of that away.
# ---------------------------------------------------------------------------


def test_delivery_ships_the_whole_assembly_by_default(tmp_path):
    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(run, tmp_path / "dest", name="xMotorMount")
    names = {p.name for p in (tmp_path / "dest").iterdir()}
    assert "xMotorMount.kcl" in names, "the assembly file"
    assert "xMotorMountParameters.kcl" in names, "where it sits"
    assert "xMotorMountMount.kcl" in names, "the verified part"
    assert "xMotorMountComponent.kcl" in names, "the motor, for context"


def test_the_assembly_position_lives_in_one_file(tmp_path):
    """Three numbers, one place. Every body reads them, so the assembly moves
    as a unit -- which is as close to a mate as KCL currently gets."""
    run = _fake_run(tmp_path / "runs")
    deliver_mod.deliver(run, tmp_path / "dest", name="m")
    params = (tmp_path / "dest" / "mParameters.kcl").read_text(encoding="utf-8")
    for axis in ("asmX", "asmY", "asmZ"):
        assert f"export {axis} = 0" in params


def test_every_body_translates_individually(tmp_path):
    """Not redundancy -- necessity. Translating a whole imported module moves
    only its LAST body and silently leaves the rest at the origin. Measured in
    probes/, and finding #12 in NOTES-FOR-ZOO."""
    run = _fake_run(tmp_path / "runs")
    deliver_mod.deliver(run, tmp_path / "dest", name="m")
    for part in ("mMount.kcl", "mComponent.kcl"):
        text = (tmp_path / "dest" / part).read_text(encoding="utf-8")
        assert 'import * from "mParameters.kcl"' in text, f"{part} cannot see the position"
        assert "translate(x = asmX, y = asmY, z = asmZ)" in text


def test_delivered_parts_are_prefixed_so_two_assemblies_can_coexist(tmp_path):
    """An unprefixed parameters.kcl from a second delivery would overwrite the
    first and silently move an assembly nobody touched."""
    run = _fake_run(tmp_path / "runs")
    dest = tmp_path / "dest"
    deliver_mod.deliver(run, dest, name="xAxis")
    deliver_mod.deliver(run, dest, name="yAxis")
    names = {p.name for p in dest.iterdir()}
    assert {"xAxisParameters.kcl", "yAxisParameters.kcl"} <= names
    assert "parameters.kcl" not in names
    x = (dest / "xAxisMount.kcl").read_text(encoding="utf-8")
    assert 'import * from "xAxisParameters.kcl"' in x


def test_an_assembly_is_imported_whole_file_not_by_export(tmp_path):
    """`import x from "x.kcl"` needs one exported body and would bring in one
    part out of four. Zoo's own axial-fan sample uses the whole-file form for
    exactly this reason."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.toml").write_text('default_file = "main.kcl"\n', encoding="utf-8")
    (project / "main.kcl").write_text("@settings(defaultLengthUnit = mm)\n", encoding="utf-8")

    run = _fake_run(tmp_path / "runs")
    result = deliver_mod.deliver(run, project, name="xMotorMount")

    assert result.entry_modified
    entry = (project / "main.kcl").read_text(encoding="utf-8")
    assert 'import "xMotorMount.kcl" as xMotorMount' in entry
    assert "xMotorMount" in entry.splitlines()[-1] or "xMotorMount" in entry


def test_the_assembly_file_points_at_the_prefixed_parts(tmp_path):
    run = _fake_run(tmp_path / "runs")
    deliver_mod.deliver(run, tmp_path / "dest", name="m")
    asm = (tmp_path / "dest" / "m.kcl").read_text(encoding="utf-8")
    assert '"mMount.kcl"' in asm
    assert '"mount.kcl"' not in asm, "an unprefixed reference would not resolve"


def test_the_guide_tells_you_where_to_change_the_position(tmp_path):
    run = _fake_run(tmp_path / "runs")
    guide = deliver_mod.deliver(
        run, tmp_path / "dest", name="m"
    ).guide_path.read_text(encoding="utf-8")
    assert "mParameters.kcl" in guide
    assert "asmX" in guide


def test_the_guide_warns_against_translating_the_import(tmp_path):
    """The trap that would otherwise cost someone an afternoon: it looks like
    it worked, and only one body actually moved."""
    run = _fake_run(tmp_path / "runs")
    guide = deliver_mod.deliver(
        run, tmp_path / "dest", name="m"
    ).guide_path.read_text(encoding="utf-8")
    assert "LAST body" in guide
    assert "#12" in guide


def test_the_guide_separates_the_verified_part_from_the_context(tmp_path):
    """The mount was checked against a spec. The motor was not, and shipping
    both without saying which is which is how reference geometry ends up in
    someone's real design."""
    run = _fake_run(tmp_path / "runs")
    guide = deliver_mod.deliver(
        run, tmp_path / "dest", name="m"
    ).guide_path.read_text(encoding="utf-8")
    assert "verified against its spec" in guide
    assert "Not your hardware" in guide


def test_the_mount_geometry_is_not_edited_only_positioned(tmp_path):
    """The delivered mount must still be the geometry that was verified. The
    translate is additive and defaults to zero, so at rest it is identical."""
    run = _fake_run(tmp_path / "runs")
    deliver_mod.deliver(run, tmp_path / "dest", name="m")
    delivered = (tmp_path / "dest" / "mMount.kcl").read_text(encoding="utf-8")
    for line in KCL.strip().splitlines():
        assert line in delivered, f"generated line lost: {line}"
