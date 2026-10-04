"""What fires an automation names no folder, run or project for its action's work.

A workflow step's dispatch says whose work the step is: the engine stamps the run, the step's node
and instance, the run's project and its folder on it (`engine.RUN_IDENTITY_KEYS`). An automation's
fire, a lifecycle hook, a view's refresh and a Run now hand their action a payload too, and that
payload is what their event carried. A key there named like one of those is the event's data: it
names no folder to seal or write in, no run whose artifacts to read, whose journal to write or whose
mirror to show, and no project to file under. So an action any of them dispatches reads none of
those (`action_providers.base.run_identity`), and a step's still reads its own run's.

🔴 Red before: each provider here read the key from whatever payload it was handed. A fire whose
payload named a folder had selfqa-evidence write its manifest there and copy that folder's files
into an artifact; one naming a run had artifact_inspect read that run's artifacts, selfqa-triage
write its verdicts into that run's journal and browse show its steps on that run's mirror; one
naming a project had knowledge-persist file its item under that project. No door builds a payload
like that today (each fire's has keys of its own), so the rule is held where it is read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.action_providers.artifact_inspect_provider import ArtifactInspectActionProvider
from personalclaw.action_providers.base import WORKFLOW_STEP_EVENT, ActionContext
from personalclaw.action_providers.browse_provider import BrowseActionProvider
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
)
from personalclaw.action_providers.selfqa_evidence_provider import SelfQaEvidenceActionProvider
from personalclaw.action_providers.selfqa_triage_provider import SelfQaTriageActionProvider
from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.journal import MAX_INLINE_OUTPUT_BYTES, Journal
from personalclaw.workflows.models import InstanceState, Node, WorkflowRun

#: Whose work an event's data might say it is, naming somewhere other than the work's own.
ELSEWHERE: dict[str, str] = {
    "run_id": "run-elsewhere",
    "node_id": "elsewhere",
    "instance_path": "root.children[9]",
    "project_id": "project-elsewhere",
}


@pytest.fixture(autouse=True)
def _isolated_runs(tmp_path, monkeypatch) -> Path:
    """The run store, and so every run's journal and artifacts, inside the test's own folder."""
    home = tmp_path / "runs-home"
    home.mkdir()
    monkeypatch.setattr(store, "config_dir", lambda: home)
    return home


def _fire(**payload: Any) -> ActionContext:
    """An automation's fire, whose payload is what its event carried."""
    return ActionContext(event="webhook.fire", trigger_id="webhook:nightly", payload=dict(payload))


def _step(**payload: Any) -> ActionContext:
    """A workflow step's dispatch, carrying what the engine stamped on it."""
    return ActionContext(event=WORKFLOW_STEP_EVENT, payload=dict(payload))


# ── selfqa-evidence: the folder it seals ─────────────────────────────────────────────────────────

#: A seal of a passed scenario whose required proof, a screenshot, the bundle holds.
SEAL = {
    "scenario_id": "nightly-sign-in",
    "sha": "abc1234",
    "passed": True,
    "required_kinds": ["screenshot"],
}


def _bundle(folder: Path) -> Path:
    """A folder as a Self-QA run's execute stage leaves it: one screenshot."""
    (folder / "screenshots").mkdir(parents=True)
    (folder / "screenshots" / "step-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return folder


def _files(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file())


@pytest.fixture
def sealed(monkeypatch) -> list[tuple[str, str]]:
    """The bundles registered as an artifact, by folder and project. Nothing is stored."""
    from personalclaw.selfqa import evidence

    registered: list[tuple[str, str]] = []

    def _register(bundle_dir: Any, *, manifest: Any = None, project_id: str = "", **_kw: Any):
        registered.append((str(bundle_dir), project_id))
        return evidence.RegisteredBundle(
            slug="evidence", ref="artifact:evidence", kinds=[], file_count=0, stored_files=0
        )

    monkeypatch.setattr(evidence, "register_bundle", _register)
    return registered


@pytest.mark.asyncio
async def test_a_fire_whose_payload_names_a_folder_seals_nothing_there(tmp_path, sealed):
    """🔴 Red before: it wrote its manifest into the folder the payload named, and copied that
    folder's files into an artifact filed under the project the payload named."""
    elsewhere = _bundle(tmp_path / "elsewhere")
    before = _files(elsewhere)

    result = await SelfQaEvidenceActionProvider().execute(
        dict(SEAL), _fire(workspace=str(elsewhere), **ELSEWHERE)
    )

    assert result.success is False and result.failure_class == "user"
    assert "runs only as a step of a run" in result.error
    assert _files(elsewhere) == before
    assert sealed == []


@pytest.mark.asyncio
async def test_a_step_seals_its_own_runs_folder(tmp_path, sealed):
    """Control, the same before and after: the folder the engine gives the step is the one sealed,
    and the bundle is filed under the run's project."""
    own = _bundle(tmp_path / "own")

    result = await dispatch_action(
        Node.from_dict(
            {
                "kind": "action",
                "id": "evidence",
                "config": {"provider": "selfqa-evidence", "with": dict(SEAL)},
            }
        ),
        BindingContext(),
        get_provider=lambda name: SelfQaEvidenceActionProvider(),
        run_id="run-own",
        project_id="project-own",
        instance_path="root.children[2]",
        cwd=str(own),
    )

    assert result.state == InstanceState.DONE, result.failure
    assert "manifest.json" in _files(own)
    assert sealed == [(str(own), "project-own")]


# ── artifact_inspect: the run whose artifacts it reads ───────────────────────────────────────────


def _offloaded(body: str) -> tuple[str, str]:
    """A run whose step left *body* offloaded to its `artifacts/`: (run id, ref)."""
    run = store.create(WorkflowRun(id="", workflow_name="nightly-notes"))
    ref, _preview = Journal(run.id).store_output("root.children[0]", body)
    assert ref.startswith("artifacts/"), "the fixture must produce an offloaded body"
    return run.id, ref


@pytest.mark.asyncio
async def test_a_fire_whose_payload_names_a_run_reads_none_of_its_artifacts():
    """🔴 Red before: it read the artifact of the run the payload named."""
    run_id, ref = _offloaded("NOTES OF A RUN " + "x" * (MAX_INLINE_OUTPUT_BYTES + 10))

    result = await ArtifactInspectActionProvider().execute({"ref": ref}, _fire(run_id=run_id))

    assert result.success is False
    assert "runs only inside a workflow run" in result.error
    assert "NOTES OF A RUN" not in result.stdout


@pytest.mark.asyncio
async def test_a_step_of_that_run_reads_its_artifact():
    """Control: the run's own step reads it."""
    run_id, ref = _offloaded("NOTES OF A RUN " + "x" * (MAX_INLINE_OUTPUT_BYTES + 10))

    result = await ArtifactInspectActionProvider().execute({"ref": ref}, _step(run_id=run_id))

    assert result.success is True, result.error
    assert json.loads(result.stdout)["content"].startswith("NOTES OF A RUN ")


# ── selfqa-triage: the run whose journal takes its verdicts ──────────────────────────────────────


@pytest.fixture
def journaled(monkeypatch) -> list[str]:
    """The run journals a verdict was written into, by run id. Each commit's verdict is fixed, so
    no repository is read."""
    from personalclaw.selfqa import ledger, triage

    runs: list[str] = []
    monkeypatch.setattr(
        triage,
        "triage_commits",
        lambda repo, shas: [
            triage.CommitTriage(sha, triage.IMPACT_TEST, "tests only") for sha in shas
        ],
    )
    monkeypatch.setattr(
        ledger, "record_triage", lambda journal, verdict, **_kw: runs.append(journal.run_id)
    )
    return runs


@pytest.mark.asyncio
async def test_a_fire_whose_payload_names_a_run_writes_nothing_into_its_journal(
    journaled, tmp_path
):
    """🔴 Red before: the verdicts were written into the journal of the run the payload named."""
    result = await SelfQaTriageActionProvider().execute(
        {"repo": str(tmp_path), "commits": ["abc1234"]}, _fire(**ELSEWHERE)
    )

    assert result.success is True, result.error
    assert json.loads(result.stdout)["recorded"] == 0
    assert journaled == []


@pytest.mark.asyncio
async def test_a_step_writes_its_verdicts_into_its_own_runs_journal(journaled, tmp_path):
    """Control: a step's verdicts go into its own run's journal."""
    result = await SelfQaTriageActionProvider().execute(
        {"repo": str(tmp_path), "commits": ["abc1234"]},
        _step(run_id="run-own", instance_path="root.children[0]"),
    )

    assert json.loads(result.stdout)["recorded"] == 1
    assert journaled == ["run-own"]


# ── browse: the run whose live mirror shows its steps ────────────────────────────────────────────


def _relayed(monkeypatch) -> list[dict[str, Any]]:
    from personalclaw.browse import mirror

    relayed: list[dict[str, Any]] = []
    monkeypatch.setattr(mirror, "broadcast_browse_step", lambda step, **_kw: relayed.append(step))
    return relayed


def _relay_one_step(ctx: ActionContext) -> None:
    from personalclaw.browse.loop import BrowseStep

    BrowseActionProvider()._mirror_sink(ctx)(
        BrowseStep(index=1, url="https://example.com/", action="CLICK ab12", fenced=True, note=""),
        "step-1.png",
    )


def test_a_fires_browse_steps_show_on_no_runs_mirror(monkeypatch):
    """🔴 Red before: they were relayed as steps of the run the payload named."""
    relayed = _relayed(monkeypatch)

    _relay_one_step(_fire(**ELSEWHERE))

    assert [step["run_id"] for step in relayed] == [""]


def test_a_steps_browse_steps_show_on_its_own_runs_mirror(monkeypatch):
    """Control: a step's are its run's."""
    relayed = _relayed(monkeypatch)

    _relay_one_step(_step(run_id="run-own"))

    assert [step["run_id"] for step in relayed] == ["run-own"]


# ── knowledge-persist: the project an item is filed under ────────────────────────────────────────

FACT = {"title": "The nightly build", "content": "The nightly build starts at two.", "kind": "fact"}


@pytest.fixture
def knowledge_home(tmp_path, monkeypatch) -> Path:
    """The knowledge store, and the task hierarchy a project's tag reads, inside the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    return home


def _filed_under(stdout: str) -> dict[str, Any]:
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    item_id = json.loads(stdout)["item_id"]
    rows = list(
        KnowledgeStore(db_path=str(knowledge_db_path())).db.execute(
            "SELECT file_metadata FROM items WHERE id = ?", (item_id,)
        )
    )
    assert rows, f"item {item_id} was not filed"
    metadata = json.loads(rows[0]["file_metadata"] or "{}")
    return {key: metadata.get(key) for key in ("project_id", "run_id")}


@pytest.mark.asyncio
async def test_a_fire_whose_payload_names_a_project_files_its_item_under_none(knowledge_home):
    """🔴 Red before: the item was filed under the project and the run the payload named. The fire
    still files its item, under no project, as any fire's is."""
    result = await KnowledgePersistActionProvider().execute(dict(FACT), _fire(**ELSEWHERE))

    assert result.success is True, result.error
    assert _filed_under(result.stdout) == {"project_id": None, "run_id": None}


@pytest.mark.asyncio
async def test_a_step_files_its_item_under_its_runs_project(knowledge_home):
    """Control: a step's item is filed under its run's project, as its run."""
    result = await KnowledgePersistActionProvider().execute(
        dict(FACT), _step(run_id="run-own", node_id="persist", project_id="project-own")
    )

    assert result.success is True, result.error
    assert _filed_under(result.stdout) == {"project_id": "project-own", "run_id": "run-own"}
