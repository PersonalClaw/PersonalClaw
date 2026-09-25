"""`personalclaw doctor` must not fail a fresh install over ffmpeg, and must name a real fix.

Measured on a fresh container (the day-7 validation lane, wheel from `d2edbdefe`): onboard,
skip the model step, run `personalclaw doctor`. It printed

    Speech-to-Text
      status:      ⏹  no STT model configured (install an STT app or bind one in Settings → Models)
      ffmpeg:      ❌ not found
                   Fix: brew install ffmpeg
    ...
    ❌ Fix these issues: credential backend: keychain requested but unavailable, ffmpeg

Two defects in four lines, and the first one is the interesting one because the SAME function
had already decided the opposite two branches earlier:

1. **The fault fired on an install with no STT model at all.** `stt_active` comes from
   ``load_use_case_settings("stt").get("enabled", True)`` — it **defaults to True**, so it is
   true on every fresh home. The ffmpeg branch was gated on `stt_active` alone, while the
   status branch immediately above it says in its own comment *"don't fail the doctor (a fresh
   core is expected to boot without media backends)"* and the faster-whisper probe immediately
   below it is gated on ``stt_active and stt_resolved is not None``. So one check answered
   "unconfigured STT is not a fault" and its neighbour answered "it is", and the neighbour won
   the exit code. Every install without ffmpeg — a slim container, the published image, any
   machine where nobody ran a package manager — exited ❌ demanding a transcoder for a feature
   with nothing to transcode with.

2. **The remedy was macOS-only, printed on Linux.** `brew install ffmpeg` on a
   `python:3.13-slim` container. A fault line whose fix cannot run where it is read sends the
   user to find out for themselves, which is the one thing doctor exists to prevent.

The rails below are properties of the decision, not string equality on the whole block: the
fault must be licensed by a bound model, and the hint must match the platform it is read on.

⚠️  `_doctor()` reads the agent config under `agents_dir()` and its MCP section REWRITES
`personalclaw.json`, so every test here that drives `_doctor()` points `PERSONALCLAW_HOME` at
`tmp_path` first and no test can write the operator's home. The seam is the HOME, not a symbol:
`agents_dir()` resolves `config_dir()` per call (#3463), there is no `AGENTS_DIR` constant to
repoint, and relocating the home after import is the supported way to isolate — see
`tests/test_agent_paths_resolve_at_call_time.py`, which holds that line. `_stt_block` asserts
the redirect landed before it calls `_doctor()`, because an isolation step that silently
no-opped would quietly turn these four rails into a test of the operator's own machine.
"""

from __future__ import annotations

import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

import personalclaw.cli_doctor as cd

# ── The install hint is per-platform ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "brew"),
        ("linux", "apt"),
        ("win32", "winget"),
    ],
)
def test_the_ffmpeg_hint_names_a_manager_that_exists_on_that_platform(
    platform: str, expected: str
) -> None:
    assert expected in cd._ffmpeg_install_hint(platform)


def test_brew_is_offered_on_darwin_ONLY() -> None:
    """The control for the test above: `brew` appearing everywhere would satisfy it too."""
    for plat in ("linux", "linux2", "win32", "cygwin", "freebsd13"):
        assert "brew" not in cd._ffmpeg_install_hint(plat), plat


# ── The fault is licensed by a bound model ───────────────────────────────────


def _stt_block(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stt_enabled: bool,
    stt_model: tuple[object, str] | None,
    have_ffmpeg: bool,
) -> tuple[str, str]:
    """Drive the real `_doctor()`; return (its Speech-to-Text section, its whole output).

    The whole output is returned too because the defect was in the **exit summary**, which
    is printed outside the section and is the line a user acts on.
    """

    def _which(binary: str) -> str | None:
        if binary == "ffmpeg" and not have_ffmpeg:
            return None
        return f"/usr/local/bin/{binary}"

    def _run(cmd, *a, **kw):  # noqa: ANN001 - subprocess.run's signature
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        stdout = "v22.12.0" if argv[:2] == ["node", "-v"] else "Python 3.13.15"
        return subprocess.CompletedProcess(argv, 0, stdout=f"{stdout}\n", stderr="")

    # Relocate the whole home, then prove it moved. `_doctor()` reads — and its MCP section
    # rewrites — `agents_dir() / AGENT_FILENAME`, and `agents_dir()` resolves `config_dir()`
    # on every call, so the env var reaches it while redirecting every other home-derived
    # path `_doctor()` touches as well. The assertion is the guard, not the subject: if the
    # seam ever stops moving, these rails must fail loudly rather than read the real home.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert cd.agents_dir() == tmp_path / "agents"
    with (
        patch.object(cd.shutil, "which", side_effect=_which),
        patch("subprocess.run", side_effect=_run),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch.object(cd, "is_local_bind", return_value=True),
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": stt_enabled},
        ),
        patch("personalclaw.stt.registry.active_stt", return_value=stt_model),
    ):
        try:
            cd._doctor()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "Speech-to-Text\n" in out, out
    return out.split("Speech-to-Text\n", 1)[1].split("\n\n", 1)[0], out


class _Prov:
    name = "faster-whisper"


def test_no_stt_model_and_no_ffmpeg_is_not_a_doctor_failure(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fresh-install case, and the one that was red. `enabled` defaults to True, so
    this is what EVERY new home looks like before a model is bound."""
    block, out = _stt_block(
        capsys, monkeypatch, tmp_path, stt_enabled=True, stt_model=None, have_ffmpeg=False
    )
    assert "no STT model configured" in block, block
    assert "❌ not found" not in block, block
    assert "not needed until an STT model is bound" in block, block
    # The line the user acts on. `ffmpeg` must not be in the issue list...
    assert "Fix these issues" not in out or "ffmpeg" not in out.split("Fix these issues", 1)[1]


def test_a_BOUND_stt_model_with_no_ffmpeg_still_IS_a_failure(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The vacuity control for the test above, and the behaviour that must survive: with a
    model bound, a missing transcoder genuinely breaks transcription and doctor must say so.
    Without this, deleting the branch entirely would pass."""
    block, out = _stt_block(
        capsys,
        monkeypatch,
        tmp_path,
        stt_enabled=True,
        stt_model=(_Prov(), "small"),
        have_ffmpeg=False,
    )
    assert "ffmpeg:      ❌ not found" in block, block
    assert "Fix these issues" in out and "ffmpeg" in out.split("Fix these issues", 1)[1]


def test_the_fault_line_carries_the_platform_hint_not_a_hardcoded_brew(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two halves joined: the row that DOES fault must print the per-platform hint, so a
    future edit cannot re-hardcode one manager into the branch."""
    monkeypatch.setattr(cd.sys, "platform", "linux")
    block, _ = _stt_block(
        capsys,
        monkeypatch,
        tmp_path,
        stt_enabled=True,
        stt_model=(_Prov(), "small"),
        have_ffmpeg=False,
    )
    assert "apt install ffmpeg" in block, block
    assert "brew" not in block, block


def test_stt_disabled_outright_reports_ffmpeg_as_simply_not_needed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The third state stays distinguishable from the second: "turned off" and "on but
    nothing bound" are different facts and must not collapse into one sentence."""
    block, _ = _stt_block(
        capsys, monkeypatch, tmp_path, stt_enabled=False, stt_model=None, have_ffmpeg=False
    )
    assert "not installed (not needed)" in block, block
    assert "until an STT model is bound" not in block, block
