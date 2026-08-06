"""WSL support: _is_wsl detection, WSL-aware dashboard auto-open, doctor note.

Covers PR-7 (PLATFORM-REACH Track B). The auto-open helper lives in
``personalclaw.gateway`` and is exercised in isolation with ``webbrowser.open``,
``subprocess.run`` and ``_is_wsl`` monkeypatched; the gateway wiring around it
(the ``_no_open`` / ``_skip_open`` short-circuits) is covered by the existing
gateway tests, which is why they are re-run as part of this PR's gate.
"""

from unittest.mock import patch

from personalclaw import env, gateway

# ── _is_wsl ──────────────────────────────────────────────────────────────────


def test_is_wsl_true_when_microsoft_in_proc_version(tmp_path, monkeypatch):
    pv = tmp_path / "version"
    pv.write_text("Linux version 5.15.0-microsoft-standard-WSL2 (...)\n")
    monkeypatch.setattr(env, "_PROC_VERSION", str(pv))
    assert env._is_wsl() is True


def test_is_wsl_case_insensitive(tmp_path, monkeypatch):
    pv = tmp_path / "version"
    pv.write_text("Linux version 4.4.0-19041-Microsoft\n")
    monkeypatch.setattr(env, "_PROC_VERSION", str(pv))
    assert env._is_wsl() is True


def test_is_wsl_false_on_normal_linux(tmp_path, monkeypatch):
    pv = tmp_path / "version"
    pv.write_text("Linux version 6.1.0-generic (gcc ...)\n")
    monkeypatch.setattr(env, "_PROC_VERSION", str(pv))
    assert env._is_wsl() is False


def test_is_wsl_false_when_proc_version_absent(tmp_path, monkeypatch):
    # Non-Linux (macOS/Windows): /proc/version does not exist → False, no raise.
    monkeypatch.setattr(env, "_PROC_VERSION", str(tmp_path / "does-not-exist"))
    assert env._is_wsl() is False


# ── _open_dashboard / _wslview_open ─────────────────────────────────────────

URL = "http://localhost:10000/?token=abc"


def test_normal_linux_open_does_not_call_wslview(monkeypatch, capsys):
    """Not WSL + webbrowser.open succeeds → webbrowser only, no wslview."""
    calls = {"web": 0, "wsl": 0}

    def fake_web_open(url):
        calls["web"] += 1
        assert url == URL
        return True

    def fake_run(*args, **kwargs):  # pragma: no cover - must not be reached
        calls["wsl"] += 1

    monkeypatch.setattr(gateway, "_is_wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", fake_web_open)
    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)

    assert calls == {"web": 1, "wsl": 0}
    # URL is always printed prominently.
    assert URL in capsys.readouterr().out


def test_wsl_goes_straight_to_wslview(monkeypatch, capsys):
    """WSL → wslview directly (no Linux browser to launch)."""
    calls = {"web": 0, "wsl": 0}

    def fake_web_open(url):  # pragma: no cover - must not be reached on WSL
        calls["web"] += 1
        return True

    def fake_run(argv, **kwargs):
        calls["wsl"] += 1
        assert argv == ["wslview", URL]

    monkeypatch.setattr(gateway, "_is_wsl", lambda: True)
    monkeypatch.setattr("webbrowser.open", fake_web_open)
    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)

    assert calls == {"web": 0, "wsl": 1}
    assert URL in capsys.readouterr().out


def test_webbrowser_returns_false_falls_back_to_wslview(monkeypatch):
    """Not WSL but webbrowser.open() reports failure → wslview fallback."""
    calls = {"wsl": 0}

    monkeypatch.setattr(gateway, "_is_wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", lambda url: False)

    def fake_run(argv, **kwargs):
        calls["wsl"] += 1
        assert argv == ["wslview", URL]

    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)

    assert calls["wsl"] == 1


def test_webbrowser_raises_falls_back_to_wslview(monkeypatch):
    """webbrowser.open() raising is treated as failure → wslview fallback."""
    calls = {"wsl": 0}

    def boom(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(gateway, "_is_wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", boom)

    def fake_run(argv, **kwargs):
        calls["wsl"] += 1

    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)  # must not raise
    assert calls["wsl"] == 1


def test_missing_wslview_does_not_raise(monkeypatch, capsys):
    """A missing wslview (FileNotFoundError) must never crash the gateway."""
    monkeypatch.setattr(gateway, "_is_wsl", lambda: True)
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("wslview")

    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)  # must not raise
    assert URL in capsys.readouterr().out


def test_url_always_printed_even_when_everything_fails(monkeypatch, capsys):
    monkeypatch.setattr(gateway, "_is_wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", lambda url: False)

    def fake_run(argv, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr("subprocess.run", fake_run)

    gateway._open_dashboard(URL)
    assert URL in capsys.readouterr().out


def test_wslview_open_returns_true_on_success(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: None)
    assert gateway._wslview_open(URL) is True


def test_wslview_open_returns_false_when_missing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("subprocess.run", fake_run)
    assert gateway._wslview_open(URL) is False


# ── doctor WSL note ──────────────────────────────────────────────────────────


def _run_doctor_capture(capsys):
    """Run _doctor() with everything but the WSL branch stubbed, return stdout."""
    import urllib.error

    from personalclaw.cli_doctor import _doctor

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": "v20.0.0",
                    "stderr": "",
                    "check_returncode": lambda self: None,
                },
            )(),
        ),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out


def test_doctor_shows_wsl_note_when_wsl(monkeypatch, capsys):
    from personalclaw.service.common import Platform

    monkeypatch.setattr("personalclaw.env._is_wsl", lambda: True)
    monkeypatch.setattr("personalclaw.service.common.current_platform", lambda: Platform.SYSTEMD)
    out = _run_doctor_capture(capsys)
    assert "WSL detected" in out
    assert "systemd active" in out


def test_doctor_wsl_note_warns_without_systemd(monkeypatch, capsys):
    from personalclaw.service.common import Platform

    monkeypatch.setattr("personalclaw.env._is_wsl", lambda: True)
    monkeypatch.setattr(
        "personalclaw.service.common.current_platform", lambda: Platform.UNSUPPORTED
    )
    out = _run_doctor_capture(capsys)
    assert "WSL detected" in out
    assert "systemd not active" in out
    assert "wsl.conf" in out


def test_doctor_no_wsl_note_on_normal_linux(monkeypatch, capsys):
    monkeypatch.setattr("personalclaw.env._is_wsl", lambda: False)
    out = _run_doctor_capture(capsys)
    assert "WSL detected" not in out
