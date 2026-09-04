"""Tests for the ``backend.sandbox`` manifest field and the permission→confinement mapping
the app launcher applies (EXECUTION-ISOLATION EI-4 §1.3(4))."""

from __future__ import annotations

from personalclaw.apps.backend_runtime import build_backend_sandbox_spec
from personalclaw.apps.manifest import BackendConfig
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
