"""Each scenario of a self-qa run captures into, and seals, a folder of its own.

🔴 The defect (Backlog, private). The seal (`selfqa-evidence`) builds its bundle from every file
under the folder it is given, and the bundled self-qa gave it the run's whole folder: a run makes
one scenario per impactful commit, and every drive step captured into that same folder. So the
second scenario's evidence also held the first scenario's screenshots, its recording was written
over the first's, and its manifest replaced the first one's. And `bundle_subdir` was joined to the
run's folder with no check, so one naming `..`, an absolute path or a link sealed any folder.

Now the drive prompt names the scenario's own folder, `scenarios/<sha>/` under the run's, and the
seal reads that folder (`bundle_subdir`), which must stay inside the run's.

Driven as the commit watch starts a run: the bundled template by its controller, the real seal and
the real artifact store under a scratch home, a triage that routes two commits forward, and step
helpers that capture where the prompt says.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw.action_providers.base import WORKFLOW_STEP_EVENT, ActionContext
from personalclaw.action_providers.selfqa_evidence_provider import SelfQaEvidenceActionProvider
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

FIRST, SECOND = "5f1c2ab9d0e4", "9a0b7c3e11f2"
_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    monkeypatch.setattr("personalclaw.selfqa.evidence.ffmpeg_available", lambda **_: False)
    return folder


class _Info:
    def __init__(self, agent_id: str, task: str, cwd: str) -> None:
        self.id = agent_id
        self.prompt = task
        self.cwd = cwd
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The scenario step designs one scenario for its commit; the drive step captures a screenshot
    and a recording of its own into the folder its prompt names."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.captured: dict[str, Path] = {}

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(
            f"sub{len(self.infos) + 1}", str(kw.get("task") or ""), str(kw.get("cwd") or "")
        )
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return info
        info.done = True
        if info.prompt.startswith("Design ONE scenario"):
            sha = re.search(r"Commit: (\w+)", info.prompt)
            assert sha, info.prompt[:300]
            payload: dict[str, Any] = {
                "scenario_id": f"scenario-{sha.group(1)}",
                "title": f"Check {sha.group(1)}",
                "entry_surface": "Chat",
                "steps": ["Open a chat", "Rename it"],
                "mutation": "the title changes",
                "backend_check": "GET /api/chat/sessions",
                "fault_injection": "",
                "restart_arc": "",
                "growth_assertion": "",
            }
        else:
            named = re.search(r"`scenarios/(\w+)/`", info.prompt)
            assert named, "vacuity floor: the drive prompt names the scenario's folder"
            sha = named.group(1)
            folder = Path(info.cwd) / "scenarios" / sha
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "screenshots").mkdir(exist_ok=True)
            (folder / "screenshots" / f"{sha}.png").write_bytes(_PNG + sha.encode())
            (folder / "recording.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42 " + sha.encode())
            self.captured[sha] = folder
            payload = {"passed": True, "observed": "ok", "failure_detail": "", "repro_steps": []}
        info.result = json.dumps(payload)
        return info


def _result(output: dict[str, Any]) -> MagicMock:
    return MagicMock(
        success=True,
        stdout=json.dumps(output),
        outcome="",
        error="",
        exit_code=0,
        stderr="",
        agent_error=None,
    )


class _Triage:
    async def execute(self, cfg, ctx, timeout=30):
        return _result(
            {
                "has_impactful": True,
                "impactful": [
                    {"sha": sha, "subject": f"feat: {sha}", "rationale": "r", "paths": ["a.tsx"]}
                    for sha in (FIRST, SECOND)
                ],
            }
        )


def _drive(shared: Path) -> tuple[WorkflowRun, _Helpers]:
    template = read_template("self-qa")
    assert template is not None
    spec = template.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="self-qa",
            inputs={
                "repo": str(shared),
                "commits": [FIRST, SECOND],
                "base_url": "http://127.0.0.1:10000",
                "fix_branch_enabled": False,
            },
        )
    )
    store.write_spec(run.id, spec)
    helpers = _Helpers()
    providers = {"selfqa-triage": _Triage(), "selfqa-evidence": SelfQaEvidenceActionProvider()}
    services = EngineServices(subagents=helpers, get_provider=lambda name: providers[name])
    asyncio.run(RunController(run, spec, services=services).run_to_completion(timeout=30.0))
    got = store.get(run.id)
    assert got is not None
    return got, helpers


def _sealed(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "manifest.json").read_text())


def test_each_scenario_seals_only_what_its_own_drive_captured(shared):
    """🔴 Before: both drives captured into the run's folder, and the second seal held the
    first's screenshot, over-written recording and all."""
    run, helpers = _drive(shared)
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message)
    assert set(helpers.captured) == {FIRST, SECOND}
    for sha, folder in helpers.captured.items():
        files = _sealed(folder)["files"]
        names = sorted(e["name"] for e in files if e["kind"] == "screenshot")
        assert names == [f"screenshots/{sha}.png"], (sha, names)
        assert _sealed(folder)["scenario_id"] == f"scenario-{sha}"
    first, second = (helpers.captured[s] / "recording.mp4" for s in (FIRST, SECOND))
    assert first.read_bytes() != second.read_bytes(), "each recording is its own scenario's"
    root = helpers.captured[FIRST].parent.parent
    assert not (root / "manifest.json").exists(), "nothing sealed the run's whole folder"


def test_the_evidence_of_each_scenario_is_registered_apart(shared):
    from personalclaw.artifacts.registry import get_provider

    # The registry's store outlives a test in its worker, so what this run registered is what it
    # added.
    before = len(get_provider().list())
    _drive(shared)
    assert len(get_provider().list()) - before == 2, "one evidence artifact for each scenario"


def _step(workspace: Path) -> ActionContext:
    return ActionContext(event=WORKFLOW_STEP_EVENT, payload={"workspace": str(workspace)})


@pytest.mark.parametrize("named", ["../other-run", "/etc", "into-elsewhere"])
def test_a_bundle_subdir_that_leads_out_of_the_runs_folder_is_refused(tmp_path, named):
    """🔴 Before: `Path(workspace) / subdir` with no check sealed any folder it reached."""
    workspace = tmp_path / "run"
    workspace.mkdir()
    elsewhere = tmp_path / "other-run"
    elsewhere.mkdir()
    (elsewhere / "secret.png").write_bytes(_PNG)
    os.symlink(elsewhere, workspace / "into-elsewhere")
    out = asyncio.run(
        SelfQaEvidenceActionProvider().execute({"bundle_subdir": named}, _step(workspace))
    )
    assert out.success is False and "leads out of it" in out.error, out.error
    assert out.failure_class == "user"
    assert not (elsewhere / "manifest.json").exists(), "nothing was sealed there"


def test_a_scenario_that_captured_nothing_is_sealed_empty_and_named_incomplete(tmp_path, shared):
    """The control on the scenario's folder: one its drive never made is made and sealed, and the
    gate names what is missing, as it does for an empty run folder."""
    workspace = tmp_path / "run"
    workspace.mkdir()
    out = asyncio.run(
        SelfQaEvidenceActionProvider().execute(
            {"bundle_subdir": f"scenarios/{FIRST}", "scenario_id": "s"}, _step(workspace)
        )
    )
    assert out.success is False and "required artifacts missing" in out.error, out.error
    assert (workspace / "scenarios" / FIRST / "manifest.json").is_file()


@pytest.mark.parametrize(
    "opens_through_no_link", [True, False], ids=["here", "where no open refuses a link"]
)
def test_a_file_link_in_a_scenarios_folder_is_neither_sealed_nor_copied(
    tmp_path, shared, monkeypatch, opens_through_no_link
):
    """🔴 Before: the manifest walk followed a link to a file (`is_file()`), and the copy into the
    evidence artifact read it (`read_bytes()`): a drive whose own shell could not read a file left
    a link to it, and the seal, unsandboxed in the gateway, stored its bytes where the drive could
    read them back. A recording that is a link is no recording to derive from either.

    The path check (`sealable`) and the open through no link (`O_NOFOLLOW`, `dir_fd`) each hold on
    their own: where no open refuses a link, the path check alone keeps every link out, a link to
    a file of the bundle too, since it can be pointed elsewhere after the check."""
    from personalclaw.artifacts.registry import get_provider
    from personalclaw.selfqa import evidence

    if not opens_through_no_link:
        monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
        monkeypatch.setattr(os, "supports_dir_fd", set())
    outside = tmp_path / "secret.txt"
    outside.write_text("the contents a sandboxed drive cannot read")
    scenario = tmp_path / "run" / "scenarios" / FIRST
    (scenario / "screenshots").mkdir(parents=True)
    (scenario / "screenshots" / "1.png").write_bytes(_PNG)
    os.symlink(outside, scenario / "screenshots" / "2.png")
    os.symlink(scenario / "screenshots" / "1.png", scenario / "screenshots" / "3.png")
    os.symlink(outside, scenario / evidence.RECORDING_NAME)

    manifest = evidence.build_manifest(scenario)
    assert [e.name for e in manifest.files] == ["screenshots/1.png"], manifest.files
    assert not evidence.sealable(scenario / evidence.RECORDING_NAME, scenario)
    sheet = evidence.derive_contact_sheet(scenario)
    assert not sheet.produced and "no recording.mp4" in sheet.degraded_reason, sheet

    forged = evidence.build_manifest(scenario)
    forged.files.append(
        evidence.ManifestEntry(kind="screenshot", name="screenshots/2.png", size=1, sha256="0" * 64)
    )
    provider = get_provider()
    stored: dict[str, bytes] = {}
    keep = provider.store_version_file

    def store(slug: str, filename: str, data: bytes) -> bool:
        stored[filename] = data
        return keep(slug, filename, data)

    monkeypatch.setattr(provider, "store_version_file", store)
    evidence.register_bundle(scenario, manifest=forged, sha=FIRST)
    assert stored, "positive control: the bundle's own file was copied"
    assert all(b"sandboxed drive cannot read" not in data for data in stored.values()), stored


def test_the_folder_the_seal_reads_is_the_resolved_one(tmp_path):
    """Resolved where it is checked, so a link put in its place after the check is not the
    folder read; and the folder of every scenario is no one scenario's to seal."""
    from personalclaw.action_providers.selfqa_evidence_provider import _bundle_dir

    workspace = tmp_path / "run"
    (workspace / "real").mkdir(parents=True)
    os.symlink(workspace / "real", workspace / "scenarios")
    assert _bundle_dir(str(workspace), f"scenarios/{FIRST}") == Path(
        os.path.realpath(workspace / "real" / FIRST)
    )
    for every in ("scenarios", "scenarios/", "./scenarios/."):
        assert _bundle_dir(str(workspace), every) is None, every


@pytest.mark.parametrize(
    "named",
    ["absolute", "alias", "", ".", f"scenarios/{FIRST}/../.."],
    ids=["an absolute path to it", "a link to it", "none", "the run's folder", "climbing to it"],
)
def test_no_spelling_of_every_scenarios_folder_is_one_scenarios(tmp_path, named):
    """🔴 CORE on 48d0943dc: the check compared text, so an absolute path to `scenarios`, or a
    link to it in the run's folder, sealed every scenario as one. Now the resolved folders are
    compared, and the run's folder itself, while it holds the scenarios' folder, is refused too:
    it holds every scenario's captures."""
    from personalclaw.action_providers.selfqa_evidence_provider import _bundle_dir

    workspace = tmp_path / "run"
    (workspace / "scenarios" / FIRST).mkdir(parents=True)
    os.symlink(workspace / "scenarios", workspace / "alias")
    subdir = {"absolute": str(os.path.realpath(workspace / "scenarios"))}.get(named, named)
    assert _bundle_dir(str(workspace), subdir) is None, subdir


def test_the_runs_folder_is_sealed_while_it_holds_no_scenarios(tmp_path):
    """The control: a workflow whose run has one bundle seals its run's folder, as before, and a
    scenario's own folder is still that one's."""
    from personalclaw.action_providers.selfqa_evidence_provider import _bundle_dir

    workspace = tmp_path / "run"
    workspace.mkdir()
    assert _bundle_dir(str(workspace), "") == Path(os.path.realpath(workspace))
    (workspace / "scenarios" / FIRST).mkdir(parents=True)
    assert _bundle_dir(str(workspace), f"scenarios/{FIRST}") == Path(
        os.path.realpath(workspace / "scenarios" / FIRST)
    )


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="no dir_fd opens here")
def test_a_folder_swapped_for_a_link_after_the_check_is_not_followed(tmp_path):
    """CORE's hardening: `O_NOFOLLOW` guards only a path's last name, so a folder of the bundle a
    drive still running swaps for a link, between `sealable` and the open, was followed. Each
    folder is opened through no link from the bundle's root down."""
    from personalclaw.selfqa import evidence

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "1.png").write_bytes(b"the contents a sandboxed drive cannot read")
    scenario = tmp_path / "run" / "scenarios" / FIRST
    (scenario / "screenshots").mkdir(parents=True)
    (scenario / "screenshots" / "1.png").write_bytes(_PNG)
    shot = scenario / "screenshots" / "1.png"
    assert evidence.sealable(shot, scenario), "vacuity: it is a file of the bundle"
    assert evidence.sha256_file(shot, scenario), "the control: read from the root down"
    (scenario / "screenshots" / "1.png").unlink()
    (scenario / "screenshots").rmdir()
    os.symlink(outside, scenario / "screenshots")  # the swap, after the check
    with pytest.raises(OSError):
        evidence.sha256_file(shot, scenario)


def test_a_hard_link_in_a_scenarios_folder_is_not_sealed(tmp_path):
    """A hard link is no symlink, and its real path is its own, so only its count of names tells
    it from a capture the drive wrote (CORE's nit on 2f37c07b7)."""
    from personalclaw.selfqa import evidence

    outside = tmp_path / "secret.txt"
    outside.write_text("the contents a sandboxed drive cannot read")
    scenario = tmp_path / "run" / "scenarios" / FIRST
    (scenario / "screenshots").mkdir(parents=True)
    (scenario / "screenshots" / "1.png").write_bytes(_PNG)
    os.link(outside, scenario / "screenshots" / "2.png")
    assert not evidence.sealable(scenario / "screenshots" / "2.png", scenario)
    assert [e.name for e in evidence.build_manifest(scenario).files] == ["screenshots/1.png"]
    with pytest.raises(OSError):
        evidence.sha256_file(scenario / "screenshots" / "2.png")


#: What a file outside the bundle holds: what a sandboxed drive could not read itself.
_SECRET = b"the contents a sandboxed drive cannot read"


def _stored(monkeypatch) -> dict[str, bytes]:
    """Every file the registration stores, by name, as it stores it."""
    from personalclaw.artifacts.registry import get_provider

    provider = get_provider()
    stored: dict[str, bytes] = {}
    keep = provider.store_version_file

    def store(slug: str, filename: str, data: bytes) -> bool:
        stored[filename] = data
        return keep(slug, filename, data)

    monkeypatch.setattr(provider, "store_version_file", store)
    return stored


def test_a_folder_link_in_a_scenarios_folder_is_refused_where_no_open_refuses_a_link(
    tmp_path, shared, monkeypatch
):
    """CORE on ae69cf325 (C1): where no open refuses a link (no `O_NOFOLLOW`, no `dir_fd` opens),
    the path check alone keeps out a folder the drive left as a link: a file under it is no link
    itself, has one name, and is a file, so only its real path, outside the bundle, tells it."""
    from personalclaw.selfqa import evidence

    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(os, "supports_dir_fd", set())
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "1.png").write_bytes(_SECRET)
    scenario = tmp_path / "run" / "scenarios" / FIRST
    (scenario / "screenshots").mkdir(parents=True)
    (scenario / "screenshots" / "1.png").write_bytes(_PNG)
    os.symlink(outside, scenario / "sub")
    linked = scenario / "sub" / "1.png"
    assert linked.is_file() and not linked.is_symlink(), "vacuity: only its folder is a link"
    assert not evidence.sealable(linked, scenario)

    manifest = evidence.build_manifest(scenario)
    assert [e.name for e in manifest.files] == ["screenshots/1.png"], manifest.files
    manifest.files.append(
        evidence.ManifestEntry(kind="screenshot", name="sub/1.png", size=1, sha256="0" * 64)
    )
    stored = _stored(monkeypatch)
    evidence.register_bundle(scenario, manifest=manifest, sha=FIRST)
    assert len(stored) == 1, "positive control: the bundle's own file, and only it, was copied"
    assert all(_SECRET not in data for data in stored.values()), stored


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not getattr(os, "O_NOFOLLOW", 0),
    reason="no open here refuses a link",
)
def test_a_capture_swapped_for_a_link_after_the_check_is_neither_read_nor_registered(
    tmp_path, shared, monkeypatch
):
    """CORE on ae69cf325 (C3): a capture the drive, still running, replaces with a link to a file
    outside the bundle between the check (`sealable`) and the open is opened through no link
    (`O_NOFOLLOW` on its own name, from the bundle's root down), so it is not read, and the
    registration that checked it first stores nothing of it."""
    from personalclaw.selfqa import evidence

    outside = tmp_path / "secret.txt"
    outside.write_bytes(_SECRET)
    scenario = tmp_path / "run" / "scenarios" / FIRST
    (scenario / "screenshots").mkdir(parents=True)
    shot = scenario / "screenshots" / "1.png"
    shot.write_bytes(_PNG)
    manifest = evidence.build_manifest(scenario)
    assert [e.name for e in manifest.files] == ["screenshots/1.png"], "vacuity: it was sealed"

    def swap() -> None:
        shot.unlink()
        os.symlink(outside, shot)

    assert evidence.sealable(shot, scenario)
    swap()  # after the check
    with pytest.raises(OSError):
        evidence.sha256_file(shot, scenario)

    shot.unlink()
    shot.write_bytes(_PNG)
    checked = evidence.sealable

    def check_then_swap(path, root):
        passed = checked(path, root)
        if passed and Path(path) == shot:
            swap()  # the drive's swap, between the registration's check and its open
        return passed

    monkeypatch.setattr(evidence, "sealable", check_then_swap)
    stored = _stored(monkeypatch)
    evidence.register_bundle(scenario, manifest=manifest, sha=FIRST)
    assert stored == {}, "the swapped capture is not read into the evidence"
