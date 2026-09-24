"""Container workspace mode (WF2WOR-12 / WORK-R20) — §4.4's four load-bearing claims.

* **The typed manifest refuses ambiguity at parse time** — image XOR build, the engine-owned
  mount point protected, `privileged` refused — because a malformed manifest discovered inside
  a provisioning subprocess is an error the author never sees at save time.
* **No hard backend dependency** — a machine with no docker/nerdctl/Apple CLI keeps the
  graceful degradation (isolated scratch dir, reason recorded); the run stays STARTABLE.
* **Snapshots anchor fork-from-checkpoint to workspace state** — the ref committed at
  checkpoint time travels checkpoint → fork_run → forked_from → provision(from_snapshot),
  and the backend provisions the child FROM it. Driven end to end with a fake backend,
  because the chain is the feature and CI has no Docker.
* **Strictly opt-in** — defaults unchanged, and the mode enum carries no remote/cloud member.

Fakes stand in for the container CLIs only; everything else (parse, provisioning, checkpoint
files on disk, fork records) is the real machinery against a tmp home — the same discipline
as `test_workflows_provisioning.py`.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import checkpoints as CP
from personalclaw.workflows import container_env, provisioning
from personalclaw.workflows.container_env import (
    WORKSPACE_MOUNT,
    AppleContainerBackend,
    BackendResult,
    CliContainerBackend,
    detect_backend,
    parse_manifest,
)
from personalclaw.workflows.models import WorkflowRun
from personalclaw.workflows.workspace import Mode, WorkspaceSpec, parse_workspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: h)
    return h


def _codes(issues):
    return {i.code for i in issues}


def _fatal_codes(issues):
    return {i.code for i in issues if i.fatal}


# ── the typed manifest ──


class TestManifest:
    def test_image_alone_is_a_valid_environment(self):
        manifest, issues = parse_manifest({"image": "python:3.12"})
        assert manifest.image == "python:3.12" and manifest.declared
        assert not issues

    def test_image_xor_build_is_fatal_when_both_declared(self):
        _, issues = parse_manifest({"image": "a", "build": {"dockerfile": "Dockerfile"}})
        assert "container_image_xor_build" in _fatal_codes(issues)

    def test_neither_image_nor_build_is_fatal(self):
        _, issues = parse_manifest({"user": "worker"})
        assert "container_no_environment" in _fatal_codes(issues)

    def test_build_without_a_dockerfile_is_fatal(self):
        _, issues = parse_manifest({"build": {"context": "."}})
        assert "container_build_no_dockerfile" in _fatal_codes(issues)

    def test_the_engine_owned_mount_point_is_protected(self):
        """Shadowing the workspace mount would make every stage write into a directory
        nobody reads back — refused at parse, where the author can still see it."""
        for target in (WORKSPACE_MOUNT, WORKSPACE_MOUNT + "/", "/"):
            _, issues = parse_manifest(
                {"image": "x", "mounts": [{"source": "/tmp/data", "target": target}]}
            )
            assert "container_mount_reserved" in _fatal_codes(issues), target

    def test_an_ordinary_mount_parses_with_its_readonly_flag(self):
        manifest, issues = parse_manifest(
            {"image": "x", "mounts": [{"source": "/tmp/d", "target": "/data", "readonly": True}]}
        )
        assert not _fatal_codes(issues)
        assert manifest.mounts == [{"source": "/tmp/d", "target": "/data", "readonly": True}]

    def test_a_mount_missing_either_end_is_fatal(self):
        _, issues = parse_manifest({"image": "x", "mounts": [{"source": "/tmp/d"}]})
        assert "container_mount_incomplete" in _fatal_codes(issues)

    def test_privileged_is_refused_as_a_capability(self):
        """A privileged container is the isolation switched off, not a capability grant."""
        for cap in ("privileged", "ALL"):
            _, issues = parse_manifest({"image": "x", "capabilities": [cap]})
            assert "container_privileged_refused" in _fatal_codes(issues), cap

    def test_ordinary_capabilities_pass_through(self):
        manifest, issues = parse_manifest({"image": "x", "capabilities": ["NET_ADMIN"]})
        assert manifest.capabilities == ["NET_ADMIN"] and not _fatal_codes(issues)

    def test_unknown_fields_pass_silently(self):
        """Tolerant reader — the 23-of-25-dropped-memories bug class."""
        _, issues = parse_manifest({"image": "x", "future_field": {"anything": 1}})
        assert not issues


# ── workspace-block integration ──


class TestWorkspaceParse:
    def test_container_mode_validates_its_manifest_at_save_time(self):
        _, issues = parse_workspace(
            {"mode": "container", "container": {"image": "a", "build": {"dockerfile": "D"}}}
        )
        assert "container_image_xor_build" in _fatal_codes(issues)

    def test_bare_container_mode_warns_but_is_not_fatal(self):
        """The shipped posture: a bare `mode: container` still RUNS (isolated scratch) —
        the author hears about the missing environment at save time, as advice."""
        spec, issues = parse_workspace({"mode": "container"})
        assert spec.mode is Mode.CONTAINER
        assert "container_no_manifest" in _codes(issues)
        assert not _fatal_codes(issues)

    def test_a_manifest_on_a_non_container_mode_is_flagged(self):
        _, issues = parse_workspace({"mode": "worktree", "container": {"image": "x"}})
        assert "container_block_ignored" in _codes(issues)

    def test_the_manifest_rides_the_spec_and_its_dict(self):
        spec, _ = parse_workspace({"mode": "container", "container": {"image": "py:3"}})
        assert spec.container == {"image": "py:3"}
        assert spec.to_dict()["container"] == {"image": "py:3"}

    def test_opt_in_posture_defaults_unchanged_and_no_remote_modes(self):
        """done_when's negative space: in_place/worktree/scratch stay the defaults, container
        is declaration-only, and the enum carries NO remote/cloud member."""
        spec, _ = parse_workspace(None)
        assert spec.mode is Mode.SCRATCH
        assert {m.value for m in Mode} == {"scratch", "worktree", "in_place", "container"}


# ── backend detection ──


class TestBackendDetection:
    def test_no_cli_installed_means_no_backend(self, monkeypatch):
        monkeypatch.setattr(container_env.shutil, "which", lambda _b: None)
        assert detect_backend() is None

    def test_docker_wins_when_present(self, monkeypatch):
        monkeypatch.setattr(container_env.shutil, "which", lambda b: "/bin/x")
        backend = detect_backend()
        assert backend is not None and backend.name == "docker"

    def test_nerdctl_covers_containerd_when_docker_is_absent(self, monkeypatch):
        monkeypatch.setattr(
            container_env.shutil, "which", lambda b: "/bin/x" if b == "nerdctl" else None
        )
        backend = detect_backend()
        assert backend is not None and backend.name == "nerdctl"

    def test_apple_cli_is_the_no_docker_macos_path(self, monkeypatch):
        monkeypatch.setattr(
            container_env.shutil, "which", lambda b: "/bin/x" if b == "container" else None
        )
        backend = detect_backend()
        assert isinstance(backend, AppleContainerBackend)

    async def test_apple_backend_cannot_snapshot_and_says_so(self):
        backend = AppleContainerBackend()
        assert backend.can_snapshot is False
        result = await backend.snapshot("c1", tag="t")
        assert result.ok is False and "no commit verb" in result.reason


# ── CLI argv construction (the engine-owned runtime semantics) ──


class TestProvisionArgv:
    async def _argv_for(self, monkeypatch, manifest, *, from_snapshot=""):
        calls: list[list[str]] = []

        async def fake_run_cli(argv, *, timeout=0, cwd=""):
            calls.append(list(argv))
            return BackendResult(True, value="cid")

        monkeypatch.setattr(container_env, "_run_cli", fake_run_cli)
        backend = CliContainerBackend("docker")
        result = await backend.provision(
            manifest, workspace_dir="/ws", run_id="r7", from_snapshot=from_snapshot
        )
        assert result.ok
        return calls, result

    async def test_run_argv_carries_the_engine_owned_semantics(self, monkeypatch):
        manifest, _ = parse_manifest(
            {
                "image": "py:3",
                "user": "worker",
                "mounts": [{"source": "/tmp/d", "target": "/data", "readonly": True}],
                "capabilities": ["NET_ADMIN"],
            }
        )
        calls, result = await self._argv_for(monkeypatch, manifest)
        (argv,) = calls
        joined = " ".join(argv)
        assert "--entrypoint sleep" in joined, "entrypoint must be engine-overridden"
        assert f"--volume /ws:{WORKSPACE_MOUNT}" in joined
        assert f"--workdir {WORKSPACE_MOUNT}" in joined
        assert "--user worker" in joined
        assert "--volume /tmp/d:/data:ro" in joined
        assert "--cap-add NET_ADMIN" in joined
        # The NAME is the returned handle, so teardown can never orphan on a truncated id.
        assert result.value == "pclaw-run-r7" and "--name pclaw-run-r7" in joined

    async def test_a_snapshot_ref_wins_over_the_manifest_image(self, monkeypatch):
        """The fork anchor's whole point: the child starts from the PARENT's committed state."""
        manifest, _ = parse_manifest({"image": "py:3"})
        calls, _ = await self._argv_for(monkeypatch, manifest, from_snapshot="pclaw/run-r1:cp-2")
        (argv,) = calls
        assert "pclaw/run-r1:cp-2" in argv and "py:3" not in argv

    async def test_a_build_manifest_builds_then_runs_the_built_tag(self, monkeypatch):
        manifest, _ = parse_manifest({"build": {"dockerfile": "Dockerfile", "context": "."}})
        calls, _ = await self._argv_for(monkeypatch, manifest)
        build_argv, run_argv = calls
        assert build_argv[:2] == ["docker", "build"] and "-f" in build_argv
        assert "pclaw/run-r7:build" in run_argv


# ── the provisioning chain, driven with a fake backend ──


class _FakeBackend:
    """The CliContainerBackend interface, recording what reached it."""

    name = "fakectl"
    can_snapshot = True

    def __init__(self, *, provision_ok=True):
        self.provision_ok = provision_ok
        self.provision_calls: list[dict] = []
        self.removed: list[str] = []

    def available(self) -> bool:
        return True

    async def provision(self, manifest, *, workspace_dir, run_id, from_snapshot="", context_dir=""):
        self.provision_calls.append(
            {"workspace_dir": workspace_dir, "run_id": run_id, "from_snapshot": from_snapshot}
        )
        if not self.provision_ok:
            return BackendResult(False, reason="image pull refused by fake")
        return BackendResult(True, value=f"pclaw-run-{run_id}")

    async def snapshot(self, container_id, *, tag):
        return BackendResult(True, value=tag)

    async def remove(self, container_id):
        self.removed.append(container_id)
        return BackendResult(True)


def _container_spec() -> WorkspaceSpec:
    spec, issues = parse_workspace({"mode": "container", "container": {"image": "py:3"}})
    assert not [i for i in issues if i.fatal]
    return spec


class TestProvisioningChain:
    async def test_a_declared_container_provisions_and_reads_clean(self, home, monkeypatch):
        fake = _FakeBackend()
        monkeypatch.setattr(container_env, "detect_backend", lambda: fake)
        result = await provisioning.provision(_container_spec(), run_id="r1")
        assert result.ok and result.path
        assert result.container_id == "pclaw-run-r1" and result.container_backend == "fakectl"
        assert result.degraded_reason == "" and result.isolated is True
        # The host-side scratch dir is what got mounted.
        assert fake.provision_calls[0]["workspace_dir"] == result.path

    async def test_no_backend_degrades_with_the_reason_recorded(self, home, monkeypatch):
        monkeypatch.setattr(container_env, "detect_backend", lambda: None)
        result = await provisioning.provision(_container_spec(), run_id="r2")
        assert result.ok and result.path, "the run must stay startable"
        assert "no container backend" in result.degraded_reason
        assert result.container_id == "" and result.isolated is False

    async def test_backend_failure_degrades_with_the_backend_reason(self, home, monkeypatch):
        fake = _FakeBackend(provision_ok=False)
        monkeypatch.setattr(container_env, "detect_backend", lambda: fake)
        result = await provisioning.provision(_container_spec(), run_id="r3")
        assert result.ok and result.path
        assert "image pull refused by fake" in result.degraded_reason
        assert result.container_id == ""

    async def test_the_fork_snapshot_reaches_the_backend(self, home, monkeypatch):
        fake = _FakeBackend()
        monkeypatch.setattr(container_env, "detect_backend", lambda: fake)
        await provisioning.provision(
            _container_spec(), run_id="r4", from_snapshot="pclaw/run-r1:cp-2"
        )
        assert fake.provision_calls[0]["from_snapshot"] == "pclaw/run-r1:cp-2"

    async def test_container_fields_ride_the_run_record_dict(self, home, monkeypatch):
        fake = _FakeBackend()
        monkeypatch.setattr(container_env, "detect_backend", lambda: fake)
        result = await provisioning.provision(_container_spec(), run_id="r5")
        d = result.to_dict()
        assert d["container_id"] == "pclaw-run-r5" and d["container_backend"] == "fakectl"


# ── snapshot → checkpoint → fork: the anchor, end to end ──


class TestForkAnchor:
    def test_checkpoint_round_trips_the_anchor_and_tolerates_old_rows(self, home):
        cp = CP.Checkpoint(
            id="cp-1", run_id="r1", spec_version=1, workspace_snapshot="pclaw/run-r1:cp-1"
        )
        assert CP.Checkpoint.from_dict(cp.to_dict()).workspace_snapshot == "pclaw/run-r1:cp-1"
        old = CP.Checkpoint.from_dict({"id": "c0", "run_id": "r0", "spec_version": 1})
        assert old.workspace_snapshot == ""

    def test_fork_from_checkpoint_threads_the_anchor_into_forked_from(self, home):
        from personalclaw.workflows import store

        parent = store.create(WorkflowRun(id="", workflow_name="w", inputs={}))
        cp = CP.save_checkpoint(parent, {}, workspace_snapshot="pclaw/run-x:cp-3")
        result = CP.fork_run(parent, {"name": "w", "root": {}}, {}, checkpoint_id=cp.id)
        assert result.child.forked_from["workspace_snapshot"] == "pclaw/run-x:cp-3"

    def test_a_fork_from_head_carries_no_anchor(self, home):
        from personalclaw.workflows import store

        parent = store.create(WorkflowRun(id="", workflow_name="w", inputs={}))
        result = CP.fork_run(parent, {"name": "w", "root": {}}, {})
        assert result.child.forked_from["workspace_snapshot"] == ""

    async def test_snapshot_workspace_reads_the_run_state_and_commits(self, home, monkeypatch):
        committed = {}

        async def fake_snapshot(self, container_id, *, tag):
            committed["container"] = container_id
            committed["tag"] = tag
            return BackendResult(True, value=tag)

        monkeypatch.setattr(CliContainerBackend, "snapshot", fake_snapshot)
        monkeypatch.setattr(CliContainerBackend, "available", lambda self: True)

        run = WorkflowRun(id="r9", workflow_name="w", inputs={})
        run.extra[provisioning.WORKSPACE_KEY] = {
            "container_id": "pclaw-run-r9",
            "container_backend": "docker",
        }
        ref = await provisioning.snapshot_workspace(run, tag_suffix="cp-1")
        assert ref == "pclaw/run-r9:cp-1"
        assert committed == {"container": "pclaw-run-r9", "tag": "pclaw/run-r9:cp-1"}

    async def test_snapshot_workspace_is_empty_for_uncontainerized_runs(self, home):
        run = WorkflowRun(id="r10", workflow_name="w", inputs={})
        assert await provisioning.snapshot_workspace(run, tag_suffix="cp-1") == ""


# ── teardown removes the container before the directory ──


class TestTeardown:
    async def test_teardown_removes_the_container(self, home, tmp_path, monkeypatch):
        removed = []

        async def fake_remove(self, container_id):
            removed.append((self.binary, container_id))
            return BackendResult(True)

        monkeypatch.setattr(CliContainerBackend, "remove", fake_remove)

        ws = tmp_path / "ws"
        ws.mkdir()
        run = WorkflowRun(id="r11", workflow_name="w", inputs={})
        run.extra[provisioning.WORKSPACE_KEY] = {
            "path": str(ws),
            "isolated": True,
            "container_id": "pclaw-run-r11",
            "container_backend": "docker",
        }
        out = await provisioning.teardown(run)
        assert ("docker", "pclaw-run-r11") in removed
        assert any("remove container" in step for step in out.ran)

    async def test_a_failed_removal_is_recorded_not_raised(self, home, tmp_path, monkeypatch):
        async def fake_remove(self, container_id):
            return BackendResult(False, reason="daemon gone")

        monkeypatch.setattr(CliContainerBackend, "remove", fake_remove)
        ws = tmp_path / "ws2"
        ws.mkdir()
        run = WorkflowRun(id="r12", workflow_name="w", inputs={})
        run.extra[provisioning.WORKSPACE_KEY] = {
            "path": str(ws),
            "isolated": True,
            "container_id": "pclaw-run-r12",
            "container_backend": "docker",
        }
        out = await provisioning.teardown(run)
        assert any("daemon gone" in step for step in out.failed)
