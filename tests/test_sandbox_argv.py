"""Additional tests for personalclaw.sandbox — wrap_argv, profiles, env scrubbing."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.sandbox import (
    _CC_FILES,
    _SENSITIVE_ENV_PREFIXES,
    _STRICT_DIRS,
    SandboxEnforcementUnavailable,
    _build_launcher_script,
    _build_seatbelt_profile,
    _resolve_enforcement_bin,
    _resolve_real_agent_bin,
    _ssh_supports_accept_new,
    _system_utility_path,
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
        mock_sb_argv.return_value = (
            ["sandbox-exec", "-f", "/tmp/p.sb", "personalclaw"],
            "/tmp/p.sb",
        )
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
    # `sandbox_exec_argv` is the macOS backend, and it now REFUSES to build an argv whose own
    # enforcement binaries it cannot resolve, so a direct call on a Linux host (where
    # `sandbox-exec` does not exist) raises rather than returning a wrap that cannot enforce.
    # The cases below are about the argv it COMPOSES — the `env -u` scrub, the profile file —
    # which is platform-independent and worth running on every CI leg, so they stub the
    # resolution to fixed absolute paths and assert those literally. The real resolution is
    # measured separately, and only where it can be, by
    # `test_the_wrappers_own_binaries_are_absolute_not_bare_names`.

    @patch(
        "personalclaw.sandbox._resolve_enforcement_bin", side_effect=lambda name: f"/usr/bin/{name}"
    )
    @patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "fake", "SSH_AUTH_SOCK": "/tmp/ssh"})
    def test_includes_env_unset_flags(self, _stub_bins):
        argv, profile_path = sandbox_exec_argv(["personalclaw", "acp"], "strict")
        try:
            assert argv[0] == "/usr/bin/env"
            assert "-u" in argv
            assert "AWS_SECRET_ACCESS_KEY" in argv
            assert "SSH_AUTH_SOCK" in argv
            assert "/usr/bin/sandbox-exec" in argv
            assert "-f" in argv
            assert profile_path is not None
            assert os.path.exists(profile_path)
        finally:
            if profile_path:
                os.unlink(profile_path)

    def test_the_wrappers_own_binaries_are_absolute_not_bare_names(self):
        """Regression: both of the wrapper's own binaries were emitted as BARE NAMES.

        The process that resolves them is the ceiling shim's ``os.execvp`` running in the
        CHILD, so the wrapper was resolving its own enforcement binaries through the
        environment of the process it was about to confine. A child with a narrowed PATH
        therefore died at exec (``_spawn_exec_shim: cannot exec 'env'``) *before* any
        enforcement applied — and a PATH an agent could influence could shadow either
        binary and defeat the confinement outright.

        Asserting ``isabs`` alone would pass on a hardcoded path that does not exist, so
        this also asserts both resolve to real files on the system utility path.

        Gated on the CAPABILITY, not on ``sys.platform``: a darwin host that genuinely has no
        ``sandbox-exec`` has nothing to measure here, and the platform string does not say so.
        """
        for name in ("env", "sandbox-exec"):
            if _resolve_enforcement_bin(name) is None:
                pytest.skip(f"host has no {name} on the system utility path — nothing to resolve")
        argv, profile_path = sandbox_exec_argv(["/bin/echo", "hi"], "strict")
        try:
            wrapper_bins = [argv[0], argv[argv.index("-f") - 1]]
            assert [os.path.basename(b) for b in wrapper_bins] == ["env", "sandbox-exec"]
            for b in wrapper_bins:
                assert os.path.isabs(b), f"{b!r} is a bare name the child's PATH would resolve"
                assert os.path.exists(b), f"{b!r} does not exist on this host"
                assert b.startswith(tuple(_system_utility_path().split(os.pathsep)))
        finally:
            if profile_path:
                os.unlink(profile_path)

    def test_an_unresolvable_enforcement_binary_refuses_before_writing_a_profile(self):
        """A wrap that cannot enforce refuses, and leaves no temp profile behind.

        The alternative — emitting an argv naming a binary that is not there — is what made
        the original defect illegible: from the child's side a wrap that dies at ``exec`` is
        indistinguishable from one that never confined anything.

        Measured on what ``sandbox_exec_argv`` ITSELF did, by recording every profile path the
        module's own ``mkstemp`` hands out. A before/after census of ``gettempdir()`` cannot
        make this claim: that directory is shared with every co-scheduled xdist worker and
        with any gateway running on the host, so it cannot tell this function's leak from
        somebody else's file — it reds on a profile this code provably never created (the
        refusal happens before ``mkstemp``). Filtering on the prefix narrows it further, to
        profile creations only.

        Carries its own positive control: an instrument that records nothing on a SUCCESSFUL
        wrap would make the refusal assertion vacuous, so the successful case must be seen
        first.
        """
        handed_out: list[str] = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            if kwargs.get("prefix") == "personalclaw_sandbox_":
                handed_out.append(path)
            return fd, path

        with patch("personalclaw.sandbox.tempfile.mkstemp", spy_mkstemp):
            # Positive control for the instrument: a wrap that DOES build records its profile.
            with patch(
                "personalclaw.sandbox._resolve_enforcement_bin",
                side_effect=lambda name: f"/usr/bin/{name}",
            ):
                _argv, built = sandbox_exec_argv(["/bin/echo", "hi"], "strict")
            assert handed_out == [built], (
                "the mkstemp spy cannot see a profile being created, so it cannot witness a "
                f"leak either: recorded {handed_out}, built {built!r}"
            )
            os.unlink(built)
            handed_out.clear()

            # The measurement: the refusal must return before any profile exists.
            with patch("personalclaw.sandbox._resolve_enforcement_bin", return_value=None):
                with pytest.raises(SandboxEnforcementUnavailable) as exc:
                    sandbox_exec_argv(["/bin/echo", "hi"], "strict")

        assert "env" in str(exc.value) and "sandbox-exec" in str(exc.value)
        assert handed_out == [], (
            "the refusal reached mkstemp — it must resolve its binaries first: "
            f"created {handed_out}, of which these survive: "
            f"{[p for p in handed_out if os.path.exists(p)]}"
        )

    @patch(
        "personalclaw.sandbox._resolve_enforcement_bin", side_effect=lambda name: f"/usr/bin/{name}"
    )
    def test_creates_temp_profile(self, _stub_bins):
        argv, profile_path = sandbox_exec_argv(["echo", "hi"], "strict")
        try:
            assert profile_path is not None
            content = Path(profile_path).read_text()
            assert "(version 1)" in content
        finally:
            if profile_path:
                os.unlink(profile_path)


class TestNamespaceArgv:
    @patch(
        "personalclaw.sandbox._resolve_real_agent_bin", return_value="/usr/local/bin/personalclaw"
    )
    def test_wraps_with_python_launcher(self, mock_resolve):
        result = namespace_argv(["personalclaw", "acp"], "strict")
        assert result[0] == sys.executable
        assert result[1].endswith(".py")
        assert result[2] == "/usr/local/bin/personalclaw"
        assert result[3] == "acp"
        # Cleanup temp file
        os.unlink(result[1])

    @patch(
        "personalclaw.sandbox._resolve_real_agent_bin", return_value="/usr/local/bin/personalclaw"
    )
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
