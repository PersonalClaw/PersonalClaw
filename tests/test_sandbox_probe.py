"""Tests for sandbox._probe_sandbox_exec."""

import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.sandbox import _probe_sandbox_exec, reset_backend, wrap_argv

_MAC_VER_26_PLUS = ("26.4.1", ("", "", ""), "")


@pytest.fixture(autouse=True)
def _cold_probe():
    """Re-measure the probe in every case below.

    The probe is memoised per process (it spawns to read a host fact), so without this each
    case after the first would assert against the FIRST case's cached answer instead of its
    own mocked host — six tests collapsing into one. Clearing it keeps all six real.
    """
    reset_backend()
    yield
    reset_backend()


@patch("personalclaw.sandbox.sys.platform", "linux")
def test_non_darwin_returns_false():
    assert _probe_sandbox_exec() is False


# Regression input: the old implementation consumed this value and returned False
# before reaching subprocess.run. The fixed implementation must ignore the version.
@patch.object(platform, "mac_ver", return_value=_MAC_VER_26_PLUS)
@patch("personalclaw.sandbox.sys.platform", "darwin")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run")
def test_macos_26_plus_still_probes_and_enables_strict_wrapping(mock_run, mock_which, mock_mac_ver):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"", stderr=b""
    )

    assert _probe_sandbox_exec() is True
    assert mock_mac_ver.call_count == 0
    assert mock_run.call_args.args[0][-3:] == [sys.executable, "-c", "pass"]

    original = [sys.executable, "-c", "pass"]
    wrapped, cleanup = wrap_argv(original, mode="strict")
    try:
        assert wrapped != original
        assert wrapped[-3:] == original
        assert cleanup is not None
    finally:
        if cleanup:
            Path(cleanup).unlink()


@patch("personalclaw.sandbox.sys.platform", "darwin")
@patch("personalclaw.sandbox.shutil.which", return_value=None)
def test_which_not_found_returns_false(mock_which):
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.sys.platform", "darwin")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run")
def test_sandbox_exec_works(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"", stderr=b""
    )
    assert _probe_sandbox_exec() is True


@patch("personalclaw.sandbox.sys.platform", "darwin")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run")
def test_sandbox_exec_fails_returns_false(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=b"", stderr=b"denied"
    )
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.sys.platform", "darwin")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run", side_effect=OSError("timeout"))
def test_subprocess_exception_returns_false(mock_run, mock_which):
    assert _probe_sandbox_exec() is False
