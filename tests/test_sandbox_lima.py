"""Tests for the ``lima`` sandbox provider (EXECUTION-ISOLATION EI-4).

Two layers:

* **Pure-unit** (run everywhere, no Lima): command construction, host↔guest path translation,
  declared-env baking, the app-provider registration contract (``lima`` is NOT a core builtin),
  and the typed, reasoned no-Lima refusal. These are the SC3 command-construction + failure-
  honesty guarantees, asserted without a VM.
* **Integration** (skipped unless ``limactl`` + a Running instance are present): a real
  ``limactl shell`` proving path translation end to end.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest

from personalclaw.sandbox_providers import (
    SandboxUnavailableError,
    get_provider,
    list_providers,
    register_builtin_providers,
    register_provider,
    resolve_provider,
    unregister_provider,
)
from personalclaw.sandbox_providers.base import SandboxSpec
from personalclaw.sandbox_providers.lima import (
    LIMA_PROVIDER_NAME,
    LimaSandboxProvider,
    build_lima_argv,
    create_provider,
    lima_available,
    lima_instance,
    translate_path,
)


def _argv(spec: SandboxSpec, inner=("echo", "hi"), *, workspace="/ws", instance="testvm", **kw):
    return build_lima_argv(list(inner), workspace_dir=workspace, spec=spec, instance=instance, **kw)


# ── registration contract: lima is an APP provider, not a core builtin ──────────


def test_lima_is_not_a_core_builtin():
    """Unlike ``docker``, ``lima`` is registered by ``SandboxTypeHandler`` on app enable — the
    core boot registration must NOT include it (the registry docstring reserves that lifecycle)."""
    unregister_provider(LIMA_PROVIDER_NAME)
    try:
        register_builtin_providers()  # idempotent; registers none + docker only
        assert LIMA_PROVIDER_NAME not in list_providers()
        # An unresolved lima name fails open to ``none`` (never blocks a spawn).
        assert resolve_provider(LIMA_PROVIDER_NAME).name == "none"
    finally:
        unregister_provider(LIMA_PROVIDER_NAME)


def test_factory_registers_a_resolvable_provider():
    """``create_provider`` (the manifest factory) yields a provider the registry resolves by
    name — the enable path ``SandboxTypeHandler`` drives."""
    provider = create_provider()
    assert isinstance(provider, LimaSandboxProvider)
    assert provider.name == LIMA_PROVIDER_NAME
    register_provider(provider)
    try:
        assert LIMA_PROVIDER_NAME in list_providers()
        assert get_provider(LIMA_PROVIDER_NAME) is provider
        assert resolve_provider(LIMA_PROVIDER_NAME) is provider
    finally:
        unregister_provider(LIMA_PROVIDER_NAME)


# ── command construction (SC3) ──────────────────────────────────────────────────


def test_argv_is_limactl_shell_with_workdir_and_instance():
    argv = _argv(SandboxSpec(), workspace="/ws", instance="myvm")
    assert argv[:2] == ["limactl", "shell"]
    assert argv[argv.index("--workdir") + 1] == "/ws"  # identity translation by default
    # The instance name precedes the guest command.
    assert argv[argv.index("myvm") + 1 :] == ["echo", "hi"]


def test_argv_omits_workdir_when_no_workspace():
    argv = _argv(SandboxSpec(), workspace="", instance="myvm")
    assert "--workdir" not in argv
    assert argv == ["limactl", "shell", "myvm", "echo", "hi"]


def test_argv_translates_workspace_to_guest_mount():
    """SC3: the guest ``--workdir`` is the host path mapped through the mount, so a terminal
    'inside the run's sandbox' opens at the right guest location."""
    argv = _argv(
        SandboxSpec(),
        workspace="/Users/dev/proj/run1",
        host_mount="/Users/dev",
        guest_mount="/home/dev.linux",
    )
    assert argv[argv.index("--workdir") + 1] == "/home/dev.linux/proj/run1"


def test_argv_bakes_declared_env_onto_guest_command():
    argv = _argv(SandboxSpec(env={"B": "2", "A": "1"}))
    # ``env`` prefix, keys sorted, before the inner command; the guest runs with exactly these.
    i = argv.index("env")
    assert argv[i : i + 3] == ["env", "A=1", "B=2"]
    assert argv[-2:] == ["echo", "hi"]


def test_argv_no_env_prefix_when_env_empty():
    argv = _argv(SandboxSpec())
    assert "env" not in argv[argv.index("testvm") :]  # inner command is verbatim


def test_host_env_is_never_baked_in():
    """The guest command carries spec.env ONLY — a secret in the host process must not appear."""
    os.environ["PCLAW_TEST_SECRET_LIMA"] = "leaked"
    try:
        argv = _argv(SandboxSpec(env={"SAFE": "1"}))
        assert "leaked" not in " ".join(argv)
        assert "PCLAW_TEST_SECRET_LIMA" not in " ".join(argv)
    finally:
        os.environ.pop("PCLAW_TEST_SECRET_LIMA", None)


# ── path translation (pure) ─────────────────────────────────────────────────────


def test_translate_path_identity_when_guest_mount_unset():
    p = "/Users/dev/proj/x"
    assert translate_path(p, host_mount="/Users/dev") == p


def test_translate_path_maps_mount_root_and_children():
    assert translate_path("/Users/dev", host_mount="/Users/dev", guest_mount="/g") == "/g"
    assert translate_path("/Users/dev/a/b", host_mount="/Users/dev", guest_mount="/g") == "/g/a/b"


def test_translate_path_leaves_paths_outside_the_mount_unchanged():
    assert translate_path("/etc/passwd", host_mount="/Users/dev", guest_mount="/g") == "/etc/passwd"


def test_translate_path_empty_is_empty():
    assert translate_path("", host_mount="/Users/dev", guest_mount="/g") == ""


# ── failure honesty (SC3: greyed-out-with-reason, no silent host downgrade) ──────


def test_wrap_refuses_with_typed_reasoned_error_when_unavailable(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.sandbox_providers.lima._cached_probe",
        lambda instance, *, refresh: (
            False,
            "the Lima instance 'personalclaw' is 'Stopped', " "not Running.",
        ),
    )
    provider = LimaSandboxProvider()
    assert provider.available() is False
    with pytest.raises(SandboxUnavailableError) as ei:
        provider.wrap(SandboxSpec(), ["echo", "hi"])
    err = ei.value
    assert err.what and err.why and err.fix
    assert "Lima" in str(err) and "Fix:" in str(err)
    # The WHY carries the actual cause so the degradation dialog names it.
    assert "Stopped" in err.why


def test_available_flips_with_the_probe(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.sandbox_providers.lima._cached_probe",
        lambda instance, *, refresh: (True, ""),
    )
    assert LimaSandboxProvider().available() is True


# ── integration (real limactl + Running instance) ───────────────────────────────

_HAS_LIMA = shutil.which("limactl") is not None and lima_available(refresh=True)
_lima_only = pytest.mark.skipif(not _HAS_LIMA, reason="limactl / Running instance unavailable")


@_lima_only
@pytest.mark.asyncio
async def test_guest_runs_with_translated_workdir(tmp_path):
    """A real ``limactl shell`` runs in the (identity-mapped) guest workdir and returns output."""
    provider = create_provider()
    handle = provider.wrap(SandboxSpec(workspace_dir=str(tmp_path)), ["pwd"])
    proc = await handle.exec(
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    handle.cleanup()
    assert proc.returncode == 0, out.decode()
    assert translate_path(str(tmp_path)) in out.decode()


def test_instance_env_override(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_SANDBOX_LIMA_INSTANCE", "custom-vm")
    assert lima_instance() == "custom-vm"
