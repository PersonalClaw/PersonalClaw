"""Additional tests for personalclaw.sandbox — wrap_argv, profiles, env scrubbing."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.sandbox import (
    _CC_FILES,
    _SENSITIVE_ENV_PREFIXES,
    _STRICT_DIRS,
    _build_launcher_script,
    _build_seatbelt_profile,
    _resolve_real_agent_bin,
    _ssh_supports_accept_new,
    detect_backend,
    namespace_argv,
    reset_backend,
    sandbox_exec_argv,
    wrap_argv,
)


@pytest.fixture(autouse=True)
def clean_backend():
    """Reset cached backend between tests."""
    reset_backend()
    yield
    reset_backend()


class TestDetectBackend:
    def test_off_mode(self):
        result = detect_backend(config_mode="off")
        assert result == "none"

    @patch("personalclaw.sandbox._probe_unshare", return_value=False)
    @patch("personalclaw.sandbox._probe_sandbox_exec", return_value=False)
    def test_no_backend_available(self, mock_sb, mock_ns):
        result = detect_backend(config_mode="auto")
        assert result == "none"

    @patch("personalclaw.sandbox._probe_unshare", return_value=True)
    def test_linux_namespace(self, mock_ns):
        result = detect_backend(config_mode="auto")
        assert result == "namespace"

    @patch("personalclaw.sandbox._probe_unshare", return_value=False)
    @patch("personalclaw.sandbox._probe_sandbox_exec", return_value=True)
    def test_macos_sandbox_exec(self, mock_sb, mock_ns):
        result = detect_backend(config_mode="auto")
        assert result == "sandbox-exec"

    @patch("personalclaw.sandbox._probe_unshare", return_value=True)
    def test_caches_result(self, mock_ns):
        detect_backend(config_mode="auto")
        detect_backend(config_mode="auto")
        # Only probed once due to caching
        assert mock_ns.call_count == 1

    @patch("personalclaw.sandbox._probe_unshare", return_value=True)
    def test_invalidates_on_mode_change(self, mock_ns):
        detect_backend(config_mode="auto")
        detect_backend(config_mode="off")
        # Second call with different mode should re-evaluate
        assert mock_ns.call_count == 1  # off doesn't probe


class TestWrapArgv:
    @patch("personalclaw.sandbox.detect_backend", return_value="none")
    def test_no_sandbox_returns_original(self, mock_detect):
        argv = ["personalclaw", "acp"]
        result, cleanup = wrap_argv(argv, mode="auto")
        assert result == argv
        assert cleanup is None

    def test_off_mode_returns_original(self):
        argv = ["personalclaw", "acp"]
        result, cleanup = wrap_argv(argv, mode="off")
        assert result == argv
        assert cleanup is None

    @patch("personalclaw.sandbox.detect_backend", return_value="namespace")
    @patch("personalclaw.sandbox.namespace_argv")
    def test_namespace_backend(self, mock_ns_argv, mock_detect):
        mock_ns_argv.return_value = [sys.executable, "/tmp/launcher.py", "personalclaw"]
        result, cleanup = wrap_argv(["personalclaw"], mode="strict")
        mock_ns_argv.assert_called_once_with(["personalclaw"], "strict")

    @patch("personalclaw.sandbox.detect_backend", return_value="sandbox-exec")
    @patch("personalclaw.sandbox.sandbox_exec_argv")
    def test_sandbox_exec_backend(self, mock_sb_argv, mock_detect):
        mock_sb_argv.return_value = (["sandbox-exec", "-f", "/tmp/p.sb", "personalclaw"], "/tmp/p.sb")
        result, cleanup = wrap_argv(["personalclaw"], mode="strict")
        mock_sb_argv.assert_called_once_with(["personalclaw"], "strict")


class TestBuildSeatbeltProfile:
    def test_strict_denies_all_dirs(self):
        profile = _build_seatbelt_profile("strict")
        assert "(version 1)" in profile
        assert "(deny file-read*" in profile
        home = str(Path.home())
        for d in _STRICT_DIRS:
            assert os.path.join(home, d) in profile

    def test_strict_denies_ssh_write(self):
        profile = _build_seatbelt_profile("strict")
        assert "(deny file-write*" in profile
        assert ".ssh" in profile

    def test_standard_does_not_deny_aws(self):
        profile = _build_seatbelt_profile("standard")
        home = str(Path.home())
        # Standard mode doesn't hide .aws
        assert f'(subpath "{home}/.aws")' not in profile

    def test_cc_mode_skips_aws_on_macos(self):
        profile = _build_seatbelt_profile("cc")
        home = str(Path.home())
        # CC mode on macOS doesn't hide .aws (credential_process needs it)
        assert f'(subpath "{home}/.aws")' not in profile

    def test_cc_mode_denies_individual_files(self):
        profile = _build_seatbelt_profile("cc")
        home = str(Path.home())
        for f in _CC_FILES:
            assert os.path.join(home, f) in profile

    def test_cc_mode_skips_aws_dir(self):
        """CC mode does NOT deny .aws as a directory (credential_process needs it)."""
        profile = _build_seatbelt_profile("cc")
        home = str(Path.home())
        # .aws should not appear as a subpath deny
        assert f'(subpath "{home}/.aws")' not in profile


class TestBuildLauncherScript:
    def test_strict_script_contains_dirs(self):
        script = _build_launcher_script("strict")
        assert "SENSITIVE_DIRS" in script
        assert ".aws" in script
        assert ".gnupg" in script

    def test_standard_script_excludes_aws(self):
        script = _build_launcher_script("standard")
        # Standard dirs don't include .aws
        assert "HIDE_SSH = False" in script

    def test_cc_script_exposes_aws_config(self):
        script = _build_launcher_script("cc")
        assert ".aws/config" in script
        assert "EXPOSE_FILES" in script

    def test_script_scrubs_env_vars(self):
        script = _build_launcher_script("strict")
        for prefix in _SENSITIVE_ENV_PREFIXES:
            assert prefix in script


class TestSandboxExecArgv:
    @patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "fake", "SSH_AUTH_SOCK": "/tmp/ssh"})
    def test_includes_env_unset_flags(self):
        argv, profile_path = sandbox_exec_argv(["personalclaw", "acp"], "strict")
        try:
            assert "env" == argv[0]
            assert "-u" in argv
            assert "AWS_SECRET_ACCESS_KEY" in argv
            assert "SSH_AUTH_SOCK" in argv
            assert "sandbox-exec" in argv
            assert "-f" in argv
            assert profile_path is not None
            assert os.path.exists(profile_path)
        finally:
            if profile_path:
                os.unlink(profile_path)

    def test_creates_temp_profile(self):
        argv, profile_path = sandbox_exec_argv(["echo", "hi"], "strict")
        try:
            assert profile_path is not None
            content = Path(profile_path).read_text()
            assert "(version 1)" in content
        finally:
            if profile_path:
                os.unlink(profile_path)


class TestNamespaceArgv:
    @patch("personalclaw.sandbox._resolve_real_agent_bin", return_value="/usr/local/bin/personalclaw")
    def test_wraps_with_python_launcher(self, mock_resolve):
        result = namespace_argv(["personalclaw", "acp"], "strict")
        assert result[0] == sys.executable
        assert result[1].endswith(".py")
        assert result[2] == "/usr/local/bin/personalclaw"
        assert result[3] == "acp"
        # Cleanup temp file
        os.unlink(result[1])

    @patch("personalclaw.sandbox._resolve_real_agent_bin", return_value="/usr/local/bin/personalclaw")
    def test_launcher_script_is_executable(self, mock_resolve):
        result = namespace_argv(["personalclaw"], "strict")
        launcher_path = result[1]
        mode = os.stat(launcher_path).st_mode
        assert mode & 0o700 == 0o700
        os.unlink(launcher_path)


class TestSshSupportsAcceptNew:
    def test_modern_ssh(self):
        _ssh_supports_accept_new.cache_clear()
        mock_result = MagicMock(stderr=b"OpenSSH_9.2p1 Debian-2, OpenSSL 3.0.8")
        with patch("subprocess.run", return_value=mock_result):
            assert _ssh_supports_accept_new() is True
        _ssh_supports_accept_new.cache_clear()

    def test_old_ssh(self):
        _ssh_supports_accept_new.cache_clear()
        mock_result = MagicMock(stderr=b"OpenSSH_7.4p1, OpenSSL 1.0.2k")
        with patch("subprocess.run", return_value=mock_result):
            assert _ssh_supports_accept_new() is False
        _ssh_supports_accept_new.cache_clear()

    def test_ssh_not_found(self):
        _ssh_supports_accept_new.cache_clear()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _ssh_supports_accept_new() is False
        _ssh_supports_accept_new.cache_clear()


class TestResolveRealAgentBin:
    def test_non_agent_binary_returns_unchanged(self):
        assert _resolve_real_agent_bin("/usr/bin/python3") == "/usr/bin/python3"

    def test_agent_fallback_when_no_real_binary(self):
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")):
            result = _resolve_real_agent_bin("/usr/local/bin/personalclaw")
        assert result == "/usr/local/bin/personalclaw"
