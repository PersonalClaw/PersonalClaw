"""The onboarding "Start a loop" card's note is written where the loop's tools reach, and shown.

Measured on a fresh install: the card's first loop drafted its note in the worker's reply and wrote
no file, its step output held only a summary, the run's Document panel said "This kind produces no
document … Nothing is missing here", and the judge could not decide — the task had sent the worker
looking around the home folder, which the file tools do not reach until Allowed working directories
are set. The note was on no surface of the run.

So the card's loop is told to save the note as a named file in the folder it works in (the
workspace, which its file tools reach on a fresh install), its judge is held to that file, and the
run states that file as its document, so the run page she opens shows the note. Asserted on the
card's OWN seed (read from `tryOneFlows.ts`), through the real `POST /api/loops` route and a real
`RunController`; only the subagent manager is scripted.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.workflows import loop_view, run_cockpit, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus

RUN_TIMEOUT = 20.0
JUDGE_PROMPT = "You are verifying work you did not do"
FLOWS = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "app" / "onboarding" / "tryOneFlows.ts"
)
NOTE = "# This week\n\n- Draft the release notes.\n- Sort the trip bookings.\n"


def _seed() -> dict[str, Any]:
    """The card's `LOOP_SEED`, read from its source: what the card really sends."""
    text = FLOWS.read_text(encoding="utf-8")
    block = text.split("export const LOOP_SEED = {", 1)[1].split("} as const", 1)[0]
    seed: dict[str, Any] = {}
    for key in ("kind", "task", "success_criteria", "document"):
        found = re.search(rf"\b{key}:\s*((?:'[^']*'\s*\+?\s*)+),", block)
        assert found, f"LOOP_SEED has no {key} the card sends"
        seed[key] = "".join(re.findall(r"'([^']*)'", found.group(1)))
    cycles = re.search(r"\bmax_cycles:\s*(\d+)", block)
    assert cycles, "LOOP_SEED has no max_cycles"
    seed["max_cycles"] = int(cycles.group(1))
    return seed


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


class _Supervisor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> None:
        self.launched.append(run.id)


class _State:
    def __init__(self) -> None:
        self.workflows = _Supervisor()
        self._sessions: dict[str, Any] = {}
        self._sse = None

    def push_refresh(self, *kinds: str) -> None:
        pass


class _OkPreflight:
    ok = True
    errors: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": True}


def _create(body: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    from personalclaw.workflows import preflight as preflight_mod
    from personalclaw.workflows.bundled_defs import PROVIDER_NAME, register_bundled_provider
    from personalclaw.workflows.defs import get_provider, unregister_provider

    app = web.Application()
    app["state"] = _State()
    request = make_mocked_request("POST", "/api/loops", app=app)

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[assignment]
    preexisting = get_provider(PROVIDER_NAME) is not None
    register_bundled_provider()
    monkeypatch.setattr(preflight_mod, "preflight", lambda _spec: _OkPreflight())
    try:
        response = asyncio.run(H.api_loop_create(request))
    finally:
        if not preexisting:
            unregister_provider(PROVIDER_NAME)
    return response.status, json.loads(response.body.decode())


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Worker:
    """The worker does what its prompt says: it saves the note under the name the task gives, in
    the folder it works in (*folder*). The judge reads it there and accepts it."""

    def __init__(self, folder: Path, document: str) -> None:
        self.folder, self.document = folder, document
        self.prompts: list[str] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        if JUDGE_PROMPT in prompt:
            note = (self.folder / self.document).read_text(encoding="utf-8")
            result = {
                "reasoning": "read the note in the folder the loop works in",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": [self.document],
                "proof": f"{self.document} holds {len(note.splitlines())} lines",
                "cannot_judge": "",
            }
        else:
            (self.folder / self.document).write_text(NOTE, encoding="utf-8")
            result = {
                "summary": f"saved the note as {self.document}",
                "meaningful_progress": True,
                "evidence": f"{self.document} in the folder the loop works in",
            }
        info = _Info(f"sub{len(self.prompts)}", json.dumps(result))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def test_the_cards_loop_names_its_note_and_the_run_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: the card sent the task alone, the run stated no document, and its panel said
    "Nothing is missing here" over a note that was nowhere on the run."""
    seed = _seed()
    status, body = _create(seed, monkeypatch)
    assert status == 202, body
    run_id = body["run_id"]

    # The run carries the card's document, and the loop row says so (what the card reads back).
    row = loop_view.get_loop_view(run_id)
    assert row is not None and row["document"] == seed["document"], row
    run = store.get(run_id)
    assert run is not None

    # The task tells the worker where the note goes, and it is a folder the file tools reach: the
    # one the loop works in, not the home folder.
    assert seed["document"] in seed["task"] and "the folder you work in" in seed["task"]
    assert "~" not in seed["task"] and "home" not in seed["task"].lower()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = _Worker(workspace, seed["document"])
    spec = store.read_spec(run_id)
    assert spec is not None
    controller = RunController(
        run, spec, services=EngineServices(subagents=worker, cwd=str(workspace))
    )
    final = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    assert final is RunStatus.COMPLETE, (final, controller.run.error_message)
    work = [p for p in worker.prompts if JUDGE_PROMPT not in p]
    assert work and seed["document"] in work[0], "the worker was never told where the note goes"
    judge = [p for p in worker.prompts if JUDGE_PROMPT in p]
    assert judge and seed["document"] in judge[0], "the judge was not held to the note's file"

    # The run's Document panel shows the note, as the run's own copy of what a step wrote in the
    # folder the loop works in. (Which step kept the copy is not asserted: a step's start is kept
    # to the second, and these scripted steps both start within one.)
    got = run_cockpit.run_deliverable(run_id)
    assert got["ok"], got
    report = got["report"]
    assert report["present"] is True, got
    assert report["name"] == seed["document"]
    assert report["content"] == NOTE
    assert report["found_in"] == "kept", report
    assert report["kept_from"] == str(workspace.resolve()), report
    assert got["derivation"]["declared_by"] == {"run": True, "name": seed["document"]}, got
    assert got["instructed"] is True


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("notes/agent-ideas.md", id="in-a-subfolder"),
        pytest.param("../agent-ideas.md", id="outside-the-folder"),
        pytest.param(".agent-ideas.md", id="hidden"),
        pytest.param("agent-ideas.sh", id="not-a-document"),
    ],
)
def test_a_document_that_is_not_one_plain_file_name_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is copied out of a folder other runs share, so it is one plain document name in
    that folder: a `.md`, `.markdown` or `.txt` file, not hidden, with no folder in it."""
    status, body = _create({**_seed(), "document": name}, monkeypatch)
    assert status == 400, (name, body)
    assert body["error"]["code"] == "invalid_request", body


def test_a_plain_document_name_is_kept_as_the_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    status, body = _create({**_seed(), "document": "weekly-plan.txt"}, monkeypatch)
    assert status == 202, body
    row = loop_view.get_loop_view(body["run_id"])
    assert row is not None and row["document"] == "weekly-plan.txt", row
