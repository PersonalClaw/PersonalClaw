"""Tests for the ``backend.sandbox`` manifest field and the permission→confinement mapping
the app launcher applies."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from personalclaw.apps.backend_runtime import BackendSupervisor, build_backend_sandbox_spec
from personalclaw.apps.manifest import AppManifest, BackendConfig
from personalclaw.sandbox import PROFILE_TOOL

# ── backend.sandbox manifest field ──────────────────────────────────────────────


def test_backend_sandbox_field_round_trips():
    cfg = BackendConfig.from_dict(
        {"entryPoint": "backend/app.py", "sandbox": "lima", "port": "8123"}
    )
    assert cfg.sandbox == "lima"
    assert cfg.to_dict()["sandbox"] == "lima"


def test_backend_sandbox_absent_by_default_and_omitted_from_dict():
    cfg = BackendConfig.from_dict({"entryPoint": "backend/app.py"})
    assert cfg.sandbox == ""
    # Default (host) is not serialized — same convention as the other optional fields.
    assert "sandbox" not in cfg.to_dict()


# ── permissions → SandboxSpec mapping ───────────────────────────────────────────


def test_network_permission_maps_to_egress_tier():
    with_net = build_backend_sandbox_spec(
        workspace_dir="/apps/x", data_dir=None, env={}, port=8000, can_network=True
    )
    without_net = build_backend_sandbox_spec(
        workspace_dir="/apps/x", data_dir=None, env={}, port=8000, can_network=False
    )
    assert with_net.egress_tier == "all"
    assert without_net.egress_tier == "off"


def test_storage_permission_maps_to_allowed_write_paths():
    granted = build_backend_sandbox_spec(
        workspace_dir="/apps/x",
        data_dir="/data/apps/x",
        env={},
        port=8000,
        can_network=False,
    )
    ungranted = build_backend_sandbox_spec(
        workspace_dir="/apps/x", data_dir=None, env={}, port=8000, can_network=False
    )
    assert granted.allowed_write_paths == ("/data/apps/x",)
    # No storage grant → no writable host path beyond the workspace boundary.
    assert ungranted.allowed_write_paths == ()


def test_port_env_profile_and_workspace_are_threaded():
    spec = build_backend_sandbox_spec(
        workspace_dir="/apps/x",
        data_dir=None,
        env={"PORT": "8000", "PERSONALCLAW_APP_NAME": "x"},
        port=8000,
        can_network=True,
    )
    assert spec.expose_ports == (8000,)
    assert spec.profile == PROFILE_TOOL
    assert spec.workspace_dir == "/apps/x"
    # env is the container/guest environment verbatim (a copy, not a shared reference).
    assert spec.env == {"PORT": "8000", "PERSONALCLAW_APP_NAME": "x"}


def test_env_is_copied_not_aliased():
    src = {"A": "1"}
    spec = build_backend_sandbox_spec(
        workspace_dir="/w", data_dir=None, env=src, port=1, can_network=False
    )
    src["B"] = "2"
    assert spec.env == {"A": "1"}  # later host-side mutation does not leak into the spec


# ── a backend started through a sandbox tier leaves nothing behind ────────────────────────────
#
# A tier's wrap can leave temp state for the launch (the `none` tier's seatbelt profile or
# Linux launcher script), and the handle that owns it is to be cleaned up once the child
# exits. On `main` the supervisor dropped the handle the moment it had the argv, so an app
# declaring `backend.sandbox: "none"` left one `personalclaw_sandbox_*` file in the system
# temp folder every time its backend started.


@pytest.fixture
def boxed_backend(tmp_path, monkeypatch):
    """An app whose backend runs through the `none` tier, and the temp folder its wrap uses.

    The wrap is stood in for by one that makes the same kind of file, so what is under test
    is the supervisor's handling of it on every platform, including one with no OS sandbox.
    """
    from personalclaw import sandbox
    from personalclaw.apps import manager

    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))

    def wrap_that_leaves_a_file(argv, mode="auto"):
        fd, path = tempfile.mkstemp(prefix="personalclaw_sandbox_", suffix=".sb")
        os.close(fd)
        return list(argv), path

    monkeypatch.setattr(sandbox, "wrap_program_argv", wrap_that_leaves_a_file)  # the `none` tier
    entry = manager.app_dir("boxed") / "backend" / "server.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")
    manifest = AppManifest.from_dict(
        {
            "name": "boxed",
            "version": "1.0.0",
            "displayName": "Boxed",
            "description": "fixture",
            "backend": {"entryPoint": "backend/server.py", "type": "python", "sandbox": "none"},
        }
    )
    supervisor = BackendSupervisor()
    yield supervisor, manifest, system_temp
    supervisor.stop_all()


def _wrap_files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("personalclaw_sandbox_"))


def test_stopping_a_sandboxed_backend_removes_what_its_wrap_left(boxed_backend):
    supervisor, manifest, system_temp = boxed_backend

    running = supervisor.start(manifest)
    assert running is not None and running.is_alive()
    assert len(_wrap_files(system_temp)) == 1, "the launch keeps its wrap while it runs"

    assert supervisor.stop("boxed")
    assert _wrap_files(system_temp) == []


def test_a_backend_that_died_and_is_started_again_leaves_one_wrap_not_two(boxed_backend):
    supervisor, manifest, system_temp = boxed_backend

    first = supervisor.start(manifest)
    assert first is not None and first.proc is not None
    first.proc.kill()
    first.proc.wait(timeout=10)
    deadline = time.monotonic() + 10
    while first.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)

    second = supervisor.start(manifest)
    assert second is not None and second.pid != first.pid
    assert len(_wrap_files(system_temp)) == 1, _wrap_files(system_temp)

    supervisor.stop("boxed")
    assert _wrap_files(system_temp) == []
