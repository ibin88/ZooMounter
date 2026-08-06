"""Zoo's engine drops the modeling websocket, and says so in the user's voice.

Offline. These tests exist because a run failed on deadline day with an
`EngineHangup`, formatted as a KCL diagnostic underlining an `import` line of
the generated file. Nothing was wrong with the file -- the identical bytes
rendered on the next attempt, seconds later, untouched.

The retry is narrow on purpose. A transport failure should not be handed to
the user as their own mistake; a real geometry error should not cost them
three round trips before they hear about it.
"""

import subprocess

import pytest

from zoomounter import generate
from zoomounter.generate import GenerationError

HANGUP = """KCL EngineHangup error

  x engine hangup: modeling connection interrupted; please reconnect and retry
  | (API call ID: 5b71a635-6f2c-4142-89ee-7d244f938364)
   ,-[8:1]
 8 | import "mount.kcl" as mount
   `----
"""

REAL_KCL_ERROR = """KCL Semantic error

  x Expected a number, found a string
   ,-[12:9]
"""


class FakeCLI:
    """Stands in for `zoo`, returning a scripted sequence of results."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, args, **kwargs):
        self.calls += 1
        code, output = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        return subprocess.CompletedProcess(args, code, stdout="", stderr=output)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """The backoff is real in production and pointless in a test."""
    monkeypatch.setattr(generate.time, "sleep", lambda _s: None)


def test_hangup_then_success_is_not_shown_to_the_user(monkeypatch):
    """The observed case: fails once, succeeds immediately after."""
    cli = FakeCLI((1, HANGUP), (0, ""))
    monkeypatch.setattr(subprocess, "run", cli)

    generate._run_zoo_cli(["kcl", "snapshot"], "zoo kcl snapshot")

    assert cli.calls == 2


def test_a_persistent_hangup_still_fails_and_says_how_hard_it_tried(monkeypatch):
    cli = FakeCLI((1, HANGUP))
    monkeypatch.setattr(subprocess, "run", cli)

    with pytest.raises(GenerationError) as exc:
        generate._run_zoo_cli(["kcl", "export"], "zoo kcl export", attempts=3)

    assert cli.calls == 3
    # Without the attempt count, three minutes of retrying looks like one
    # slow failure, and "retry it" is the wrong advice to give next.
    assert "after 3 attempts" in str(exc.value)
    assert "EngineHangup" in str(exc.value)


def test_a_real_kcl_error_is_not_retried(monkeypatch):
    """Broken geometry is broken on every attempt. Retrying it only makes the
    user wait longer to hear the same thing."""
    cli = FakeCLI((1, REAL_KCL_ERROR))
    monkeypatch.setattr(subprocess, "run", cli)

    with pytest.raises(GenerationError) as exc:
        generate._run_zoo_cli(["kcl", "export"], "zoo kcl export")

    assert cli.calls == 1
    assert "after" not in str(exc.value).split(":")[0]


def test_a_missing_cli_is_not_a_hangup(monkeypatch):
    """FileNotFoundError means `zoo` isn't installed. Retrying cannot install
    it, and the message has to keep naming the real fix."""
    def missing(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(GenerationError) as exc:
        generate._run_zoo_cli(["kcl", "export"], "zoo kcl export")

    assert "not found" in str(exc.value)
    assert "PATH" in str(exc.value)


def test_snapshot_still_reports_a_missing_image_after_a_clean_exit(monkeypatch, tmp_path):
    """Exit code 0 is not proof the file landed -- the check that catches a
    silently empty render has to survive the retry refactor."""
    monkeypatch.setattr(subprocess, "run", FakeCLI((0, "")))

    with pytest.raises(GenerationError, match="Expected preview image not found"):
        generate.snapshot_preview(tmp_path / "in.kcl", tmp_path / "out.png")


def test_export_still_reports_a_missing_step_after_a_clean_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeCLI((0, "")))

    with pytest.raises(GenerationError, match="Expected output STEP file not found"):
        generate.export_step(tmp_path / "in.kcl", tmp_path)
