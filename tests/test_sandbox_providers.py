"""Tests for the sandbox-provider seam + the ``none`` builtin (EXECUTION-ISOLATION EI-1).

Covers: the ABC contract, ``none`` argv composition (OS sandbox at wrap, ceilings at exec), a
REAL child whose ``ulimit -n`` reports the configured NOFILE ceiling, the registry
resolve/fail-open, and the ``sandbox`` PROVIDER_TYPES/handler pairing.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.sandbox import PROFILE_TOOL, ResourceCeilings
from personalclaw.sandbox_providers import (
    get_provider,
    list_providers,
    register_provider,
    resolve_provider,
    unregister_provider,
)
from personalclaw.sandbox_providers.base import SandboxHandle, SandboxProvider, SandboxSpec
from personalclaw.sandbox_providers.none import NONE_PROVIDER_NAME, NoneSandboxProvider


def test_none_provider_is_registered_and_available():
    """The ``none`` builtin self-registers on import and is always available."""
    assert NONE_PROVIDER_NAME in list_providers()
    p = get_provider(NONE_PROVIDER_NAME)
    assert isinstance(p, NoneSandboxProvider)
    assert p.available() is True
    assert p.name == "none"


def test_none_wrap_off_mode_is_argv_identity():
    """mode='off' applies no OS sandbox: the wrapped argv equals the input."""
    p = NoneSandboxProvider()
    handle = p.wrap(SandboxSpec(mode="off", profile="none"), ["echo", "hi"])
    assert isinstance(handle, SandboxHandle)
    assert handle.argv == ["echo", "hi"]


def test_none_handle_cleanup_is_idempotent_when_no_temp(tmp_path):
    """cleanup() with no temp file (off mode) is a safe no-op, callable twice."""
    p = NoneSandboxProvider()
    handle = p.wrap(SandboxSpec(mode="off", profile="none"), ["echo", "hi"])
    handle.cleanup()
    handle.cleanup()  # idempotent


def test_resolve_provider_fails_open_to_none():
    """An unknown provider name resolves to the ``none`` builtin — never blocks a spawn."""
    resolved = resolve_provider("does-not-exist")
    assert resolved.name == "none"
    # Empty name also resolves to none.
    assert resolve_provider("").name == "none"


def test_registry_register_unregister_roundtrip():
    """A provider can be registered + removed (the SandboxTypeHandler lifecycle)."""

    class _Fake(SandboxProvider):
        name = "fake-tier"
        display_name = "Fake"

        def available(self):
            return True

        def wrap(self, spec, argv):
            raise NotImplementedError

    register_provider(_Fake())
    try:
        assert "fake-tier" in list_providers()
        assert resolve_provider("fake-tier").name == "fake-tier"
    finally:
        unregister_provider("fake-tier")
    assert "fake-tier" not in list_providers()


def _ulimit_argv() -> list[str]:
    # `sh -c 'ulimit -n'` prints the child's SOFT NOFILE limit to stdout.
    return ["/bin/sh", "-c", "ulimit -n"]


@pytest.mark.asyncio
async def test_none_exec_child_ulimit_reports_nofile_ceiling():
    """A real child launched through handle.exec reports the configured NOFILE soft ceiling.

    This is the end-to-end proof that the ``none`` provider delivers ResourceCeilings via the
    post-exec shim: the child's own ``ulimit -n`` must equal the ceiling we set (and differ from
    the process default, so the test cannot pass by accident).
    """
    ceilings = ResourceCeilings(nofile=256, max_pids=0, max_rss_mb=0)
    p = NoneSandboxProvider()
    # profile=tool applies the configured NOFILE soft cap verbatim (session_host would raise it to
    # the hard limit, which is not a fixed number to assert against).
    handle = p.wrap(
        SandboxSpec(mode="off", profile=PROFILE_TOOL, ceilings=ceilings), _ulimit_argv()
    )
    proc = await handle.exec(
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    handle.cleanup()
    reported = out.decode().strip()
    assert reported == "256", f"child ulimit -n reported {reported!r}, expected 256"


@pytest.mark.asyncio
async def test_none_exec_without_ceilings_is_unwrapped_but_runs():
    """profile='none' delivers no shim; the child still runs and produces output."""
    p = NoneSandboxProvider()
    handle = p.wrap(SandboxSpec(mode="off", profile="none"), ["/bin/sh", "-c", "echo ok"])
    proc = await handle.exec(
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    handle.cleanup()
    assert out.decode().strip() == "ok"


def test_sandbox_type_is_in_provider_types_with_a_handler():
    """The #47 rule: the ``sandbox`` type is both in PROVIDER_TYPES and has a live handler."""
    import re
    from pathlib import Path

    from personalclaw.apps.manifest import PROVIDER_TYPES

    assert "sandbox" in PROVIDER_TYPES
    registry_py = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "personalclaw"
        / "providers"
        / "registry.py"
    )
    handlers = set(re.findall(r'register_type_handler\("([a-z_]+)"', registry_py.read_text()))
    assert "sandbox" in handlers, "sandbox type has no register_type_handler call"


@pytest.mark.asyncio
async def test_subagent_spawn_threads_sandbox_to_worker_launch():
    """SubagentManager.spawn(sandbox=...) records it on the info and forwards it to the ACP
    worker launch via get_or_create's factory kwargs (the seam consumers read as ``sandbox``)."""
    from unittest.mock import patch

    from personalclaw.subagent import SubagentManager
    from tests.test_subagent import _mock_ctx_builder_auto_spawn, _mock_sessions

    sessions = _mock_sessions()
    manager = SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder_auto_spawn())
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("do work", sandbox="container-tier")
        assert info is not None
        assert info.sandbox == "container-tier"
        await manager._tasks[info.id]

    # A non-default sandbox is forwarded to the session factory as the ``sandbox`` kwarg.
    assert sessions.get_or_create.await_count >= 1
    kwargs = sessions.get_or_create.await_args.kwargs
    assert kwargs.get("sandbox") == "container-tier"


@pytest.mark.asyncio
async def test_subagent_spawn_default_sandbox_not_forwarded():
    """The default ``none`` is NOT forwarded, so the chat/native paths stay untouched."""
    from unittest.mock import patch

    from personalclaw.subagent import SubagentManager
    from tests.test_subagent import _mock_ctx_builder_auto_spawn, _mock_sessions

    sessions = _mock_sessions()
    manager = SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder_auto_spawn())
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("do work")
        assert info is not None
        assert info.sandbox == "none"
        await manager._tasks[info.id]

    kwargs = sessions.get_or_create.await_args.kwargs
    assert "sandbox" not in kwargs


def test_sandbox_type_handler_registers_into_sandbox_registry():
    """SandboxTypeHandler.register/deregister drives the sandbox_providers registry."""
    from personalclaw.providers.registry import SandboxTypeHandler

    class _Tier(SandboxProvider):
        name = "container-tier"
        display_name = "Container"

        def available(self):
            return True

        def wrap(self, spec, argv):
            raise NotImplementedError

    handler = SandboxTypeHandler()
    inst = _Tier()
    handler.register(None, inst)  # type: ignore[arg-type]
    try:
        assert get_provider("container-tier") is inst
    finally:
        handler.deregister(None, inst)  # type: ignore[arg-type]
    assert get_provider("container-tier") is None
