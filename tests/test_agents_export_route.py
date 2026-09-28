"""``POST /api/agents/export`` — putting your agents into Claude Code's own agents folder.

The outbound export (``packs/external_formats.py``) had every rail and no caller: nothing in the
UI, the CLI or the API reached it, so there was no way to export an agent at all. This route is
that caller, and these tests drive it the way the Agents page does: a dry run, then a confirm
that names the folder the dry run showed.

Every test here runs against a scratch home AND a scratch Claude Code folder:
``CLAUDE_CONFIG_DIR`` points into ``tmp_path``, so does ``Path.home()``, and a test checks the
folder the route resolved lies inside ``tmp_path`` before it confirms a write. No test can reach
a real ``~/.claude``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.dashboard.handlers.agent_export import api_agents_export
from personalclaw.packs.external_formats import PROVENANCE_MARKER

#: AWS-key-shaped, so the content scan the export shares with the pack exporter fires on it.
CANARY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def claude_dir(tmp_path, monkeypatch) -> Path:
    """The scratch Claude Code config folder — the only one any test here can resolve."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    folder = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folder))
    return folder


def _seed(profiles: dict[str, AgentProfile]) -> None:
    cfg = AppConfig.load()
    cfg.agents.update(profiles)
    cfg.save()


def _app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/agents/export", api_agents_export)
    return app


async def _post(body: dict) -> tuple[int, dict]:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/agents/export", json=body)
        return resp.status, await resp.json()


def _audit_rows() -> list[dict]:
    from personalclaw.config.loader import config_dir

    path = config_dir() / "security_events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r.get("operation") == "agent.export"]


def _in_scratch(dest: str, tmp_path: Path) -> str:
    """The guard every confirming test takes before it writes: the folder is the scratch one."""
    assert Path(dest).is_relative_to(tmp_path), f"the export resolved a folder outside: {dest}"
    return dest


LEDGER = AgentProfile(description="Keeps the books", system_prompt="Reconcile every Friday.")
TRIPS = AgentProfile(description="Plans trips", system_prompt="Ask the budget first.")


@pytest.mark.asyncio
async def test_the_dry_run_names_claude_codes_folder_and_writes_nothing(claude_dir, tmp_path):
    _seed({"ledger-keeper": LEDGER})
    status, plan = await _post({"agents": ["ledger-keeper"]})
    assert status == 200
    assert plan["dest"] == _in_scratch(str(claude_dir / "agents"), tmp_path)
    assert plan["files"] == [
        {"path": "ledger-keeper.md", "state": "new", "entities": ["ledger-keeper"]}
    ]
    assert plan["blocked"] == [] and plan["refusal"] is None
    assert not claude_dir.exists(), "a dry run writes nothing, not even the folder"
    assert _audit_rows() == [], "only a write is an export"


@pytest.mark.asyncio
async def test_the_folder_follows_claude_config_dir(claude_dir, tmp_path, monkeypatch):
    """Claude Code reads its agents from ``$CLAUDE_CONFIG_DIR`` when that is set, else ~/.claude."""
    _seed({"ledger-keeper": LEDGER})
    elsewhere = tmp_path / "another-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
    assert (await _post({"agents": ["ledger-keeper"]}))[1]["dest"] == str(elsewhere / "agents")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert (await _post({"agents": ["ledger-keeper"]}))[1]["dest"] == str(
        tmp_path / "home" / ".claude" / "agents"
    )


@pytest.mark.asyncio
async def test_a_confirmed_export_writes_files_that_carry_the_marker(claude_dir, tmp_path):
    _seed({"ledger-keeper": LEDGER, "trip-planner": TRIPS})
    names = ["ledger-keeper", "trip-planner"]
    _, plan = await _post({"agents": names})
    dest = _in_scratch(plan["dest"], tmp_path)

    status, done = await _post({"agents": names, "confirm": True, "dest": dest})
    assert status == 200
    agents = claude_dir / "agents"
    assert done["written"] == [str(agents / "ledger-keeper.md"), str(agents / "trip-planner.md")]
    assert done["unchanged"] == []
    assert done["message"] == (f"Exported 2 agents to {agents}: ledger-keeper.md, trip-planner.md.")
    ledger = (agents / "ledger-keeper.md").read_text(encoding="utf-8")
    assert ledger.startswith("---\nname: ledger-keeper\ndescription: Keeps the books\n---\n")
    assert "Reconcile every Friday." in ledger
    assert ledger.endswith(PROVENANCE_MARKER + "\n")
    assert (
        (agents / "trip-planner.md").read_text(encoding="utf-8").endswith(PROVENANCE_MARKER + "\n")
    )
    (row,) = _audit_rows()
    assert row["outcome"] == "ok"
    assert row["resources"] == f"{agents}: ledger-keeper.md, trip-planner.md"

    # The same export again changes nothing, and says so.
    status, again = await _post({"agents": names, "confirm": True, "dest": dest})
    assert status == 200 and again["written"] == []
    assert again["message"] == (
        f"Nothing changed in {agents}: every agent was already there as exported "
        "(ledger-keeper.md, trip-planner.md)."
    )


@pytest.mark.asyncio
async def test_a_confirm_must_name_the_folder_the_dry_run_showed(claude_dir, tmp_path):
    _seed({"ledger-keeper": LEDGER})
    for dest in (None, str(tmp_path / "somewhere-else")):
        body: dict = {"agents": ["ledger-keeper"], "confirm": True}
        if dest is not None:
            body["dest"] = dest
        status, err = await _post(body)
        assert status == 409
        assert err["error"]["code"] == "agent_export_dest_changed"
        assert str(claude_dir / "agents") in err["error"]["message"]
    assert not claude_dir.exists()
    assert not (tmp_path / "somewhere-else").exists()


@pytest.mark.asyncio
async def test_your_own_agent_file_refuses_the_whole_export(claude_dir, tmp_path):
    """A file PersonalClaw did not write is never overwritten, and the export does not go ahead
    around it: it is named, the dry run says so first, and leaving it out exports the rest."""
    _seed(
        {
            "code-reviewer": AgentProfile(description="Reviews diffs", system_prompt="Be exact."),
            "talk-editor": AgentProfile(description="Edits talks", system_prompt="Cut hard."),
        }
    )
    agents = claude_dir / "agents"
    agents.mkdir(parents=True)
    hers = agents / "code-reviewer.md"
    hers.write_text("---\nname: code-reviewer\ndescription: mine\n---\n\nMy own reviewer.\n")
    original = hers.read_bytes()

    _, plan = await _post({"agents": ["code-reviewer", "talk-editor"]})
    dest = _in_scratch(plan["dest"], tmp_path)
    assert [(f["path"], f["state"]) for f in plan["files"]] == [
        ("code-reviewer.md", "theirs"),
        ("talk-editor.md", "new"),
    ]
    refusal = (
        f"Refusing to overwrite {hers} (not written by PersonalClaw). Nothing is exported while "
        "it is there: leave it out to export the rest."
    )
    assert plan["refusal"] == refusal

    status, err = await _post(
        {"agents": ["code-reviewer", "talk-editor"], "confirm": True, "dest": dest}
    )
    assert status == 409
    assert err["error"] == {"code": "agent_export_would_overwrite", "message": refusal}
    assert hers.read_bytes() == original, "her file was touched"
    assert not (agents / "talk-editor.md").exists(), "the export went ahead around her file"
    (row,) = _audit_rows()
    assert row["outcome"] == "denied"

    status, done = await _post({"agents": ["talk-editor"], "confirm": True, "dest": dest})
    assert status == 200
    assert (
        (agents / "talk-editor.md").read_text(encoding="utf-8").endswith(PROVENANCE_MARKER + "\n")
    )
    assert hers.read_bytes() == original


@pytest.mark.asyncio
async def test_only_your_own_agents_can_be_exported(claude_dir):
    _seed({"ledger-keeper": LEDGER})
    for agents, named in (
        (["ledger-keeper", "no-such-agent"], "no-such-agent"),
        (["personalclaw-lite"], "personalclaw-lite"),
        (["PersonalClaw"], "PersonalClaw"),
    ):
        status, err = await _post({"agents": agents})
        assert status == 400, agents
        assert err["error"]["code"] == "agent_export_agents_invalid"
        assert named in err["error"]["message"]
        assert err["error"]["message"].startswith("Nothing was exported: ")
    for body in ({}, {"agents": []}, {"agents": "ledger-keeper"}, {"agents": [3]}):
        status, err = await _post(body)
        assert status == 400 and err["error"]["code"] == "agent_export_agents_invalid", body
    assert not claude_dir.exists()


@pytest.mark.asyncio
async def test_a_credential_in_an_agents_text_blocks_the_export(claude_dir, tmp_path):
    _seed({"api-caller": AgentProfile(description="Calls the API", system_prompt=f"Use {CANARY}")})
    _, plan = await _post({"agents": ["api-caller"]})
    dest = _in_scratch(plan["dest"], tmp_path)
    assert plan["blocked"] == [{"path": "api-caller.md", "categories": ["credential"]}]
    assert plan["refusal"] == (
        "Nothing is exported: api-caller.md holds what looks like a credential. Remove it, or "
        "leave it out to export the rest."
    )
    status, err = await _post({"agents": ["api-caller"], "confirm": True, "dest": dest})
    assert status == 409 and err["error"]["code"] == "agent_export_blocked"
    assert CANARY not in json.dumps(plan) and CANARY not in json.dumps(err)
    assert not claude_dir.exists()


def test_the_route_is_the_owners_alone():
    """It writes instructions another tool runs, so no app may call it."""
    from personalclaw.apps.permissions import OwnerOnly, route_authz

    assert isinstance(route_authz("POST", "/api/agents/export"), OwnerOnly)


def test_the_gateway_registers_the_route():
    """A handler nobody registers is the defect this route was written for."""
    import inspect

    from personalclaw.dashboard import server

    assert '"/api/agents/export", api_agents_export' in inspect.getsource(server)
