"""Tests for sandbox._probe_sandbox_exec — 5 distinct paths."""

import subprocess
from unittest.mock import patch

from personalclaw.sandbox import _probe_sandbox_exec

_MAC_VER_BELOW_26 = ("15.0.0", ("", "", ""), "")
_MAC_VER_26_PLUS = ("26.4.1", ("", "", ""), "")


@patch("personalclaw.sandbox.sys")
def test_non_darwin_returns_false(mock_sys):
    mock_sys.platform = "linux"
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.platform.mac_ver", return_value=_MAC_VER_26_PLUS)
@patch("personalclaw.sandbox.sys")
def test_macos_26_plus_returns_false(mock_sys, mock_mac_ver):
    mock_sys.platform = "darwin"
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.platform.mac_ver", return_value=_MAC_VER_BELOW_26)
@patch("personalclaw.sandbox.sys")
@patch("personalclaw.sandbox.shutil.which", return_value=None)
def test_which_not_found_returns_false(mock_which, mock_sys, mock_mac_ver):
    mock_sys.platform = "darwin"
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.platform.mac_ver", return_value=_MAC_VER_BELOW_26)
@patch("personalclaw.sandbox.sys")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run")
def test_sandbox_exec_works(mock_run, mock_which, mock_sys, mock_mac_ver):
    mock_sys.platform = "darwin"
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    assert _probe_sandbox_exec() is True


@patch("personalclaw.sandbox.platform.mac_ver", return_value=_MAC_VER_BELOW_26)
@patch("personalclaw.sandbox.sys")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run")
def test_sandbox_exec_fails_returns_false(mock_run, mock_which, mock_sys, mock_mac_ver):
    mock_sys.platform = "darwin"
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    assert _probe_sandbox_exec() is False


@patch("personalclaw.sandbox.platform.mac_ver", return_value=_MAC_VER_BELOW_26)
@patch("personalclaw.sandbox.sys")
@patch("personalclaw.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("personalclaw.sandbox.subprocess.run", side_effect=OSError("timeout"))
def test_subprocess_exception_returns_false(mock_run, mock_which, mock_sys, mock_mac_ver):
    mock_sys.platform = "darwin"
    assert _probe_sandbox_exec() is False
