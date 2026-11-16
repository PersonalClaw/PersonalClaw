"""A proposed template installs: accepting it saves the workflow it names.

Three producers file a ``template`` proposal whose Accept should save a workflow:
- the after-turn reviewer and the agent's save tool, both through ``learning.template_gate``;
- trace mining's recurring successful path.

Nothing installed the kind before, so Accept answered 409, and the save tool's promise ("Accept it
in the Learning review queue to create the definition") was untrue. Each producer now files the
template's name as a single-valued ``name:`` tag, and the ``template`` branch of
``learning.installers`` saves the steps under it.

A name a workflow already has is refused with 409 ``learning_proposal_not_installed``. Nothing is
written and the row stays pending, because accepting never replaces a workflow you have.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw.learning import installers, proposals, template_install
from personalclaw.learning.detectors import Action, Candidate, GateDecision

STEPS = [
    "Fetch the changes merged in {{week}}",
    "Group the result by project",
    "Draft the status email from the groups",
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import native_defs

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(proposals, "_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(proposals, "_decisions_path", lambda: tmp_path / "decisions.json")
    (tmp_path / "proposals").mkdir()
    registered = defs_mod.get_provider("native") is None
    if registered:
        defs_mod.register_provider(native_defs.NativeWorkflowDefProvider())
    yield tmp_path
    if registered:
        defs_mod.unregister_provider("native")


def _saved(home, name: str) -> dict:
    return json.loads((home / "workflows" / "defs" / name / "workflow.json").read_text())


def _gate_filed(
    *, run_id: str, session_key: str, name: str = "", title: str = ""
) -> proposals.Proposal:
    """File a template the way ``template_gate.evaluate`` does for an accepted candidate."""
    from personalclaw.learning import template_gate

    decision = GateDecision(action=Action.AUTO_FILE.value)
    pid = template_gate._file_template(
        Candidate(run_id=run_id, steps=STEPS, intent="Draft the Friday status email"),
        decision,
        session_key=session_key,
        title=title or "Draft the Friday status email from the week's merged changes",
        body="\n".join(f"- {s}" for s in STEPS),
        name=name,
    )
    assert pid, "the gate filed nothing"
    return proposals.get(pid)


# ── each producer's template installs under its name ─────────────────────────────────────────────


def test_the_save_tools_template_installs_under_its_name(home):
    prop = _gate_filed(
        run_id="weekly-status-report", session_key="dashboard:c1", name="weekly-status-report"
    )
    assert "name:weekly-status-report" in prop.tags

    proposals.accept(prop.id, actor="user")

    spec = _saved(home, "weekly-status-report")
    prompts = [c["config"]["prompt"] for c in spec["root"]["children"]]
    assert prompts == STEPS
    assert template_install.SPEC_TAG in spec["tags"]
    assert "prompt-card" not in spec["tags"], "a learned template is no pasted card"
    assert proposals.get(prop.id) is None or proposals.get(prop.id).status == "accepted"


def test_the_reviewers_template_installs_under_its_name_not_the_chats_key(home):
    prop = _gate_filed(run_id="dashboard:c1", session_key="dashboard:c1", name="incident-writeup")
    proposals.accept(prop.id, actor="user")
    assert _saved(home, "incident-writeup")["root"]["children"]


def test_a_mined_path_installs_as_its_workflows_path(home):
    from personalclaw.learning import mining

    trace = SimpleNamespace(
        frequency=3,
        workflow_name="weekly-digest",
        steps=["fetch_feeds", "summarize", "send_digest"],
        run_ids=["r1", "r2", "r3"],
        signature="fetch_feeds>summarize>send_digest",
    )
    pid = mining.file_positive_trace(trace, session_key="")
    prop = proposals.get(pid)
    assert "workflow:weekly-digest" in prop.tags and "name:weekly-digest-path" in prop.tags

    proposals.accept(pid, actor="user")

    prompts = [
        c["config"]["prompt"] for c in _saved(home, "weekly-digest-path")["root"]["children"]
    ]
    assert prompts == ["fetch_feeds", "summarize", "send_digest"]


# ── a row filed before the name tag ─────────────────────────────────────────────────────────────


def _old_row(
    *, run_id: str, session_key: str, title: str, steps: list[str] = STEPS
) -> proposals.Proposal:
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE.value,
        title=title,
        body="\n".join(f"- {s}" for s in steps),
        provenance="inferred",
        session_key=session_key,
        run_id=run_id,
        tags=["ad_hoc_to_template", "auto_file"],
    )
    return prop


def test_an_old_save_tool_row_installs_under_its_run_id(home):
    prop = _old_row(run_id="weekly-status-report", session_key="dashboard:c1", title="Draft it")
    assert template_install.template_name(prop.to_dict()) == "weekly-status-report"
    proposals.accept(prop.id, actor="user")
    assert _saved(home, "weekly-status-report")


def test_an_old_reviewer_row_installs_under_its_template_title(home):
    prop = _old_row(
        run_id="dashboard:c1", session_key="dashboard:c1", title="Template: incident-writeup"
    )
    assert template_install.template_name(prop.to_dict()) == "incident-writeup"
    proposals.accept(prop.id, actor="user")
    assert _saved(home, "incident-writeup")


def test_an_old_row_with_no_name_offers_no_install_and_stays_pending(home):
    prop = _old_row(run_id="dashboard:c1", session_key="dashboard:c1", title="Draft the email")
    assert template_install.template_name(prop.to_dict()) == ""
    assert installers.branch_for(prop.to_dict()) == ""
    with pytest.raises(proposals.NoProposalInstallerError, match="no name"):
        proposals.accept(prop.id, actor="user")
    assert proposals.get(prop.id).status == "pending"


# ── what is refused ─────────────────────────────────────────────────────────────────────────────


def test_a_taken_name_is_refused_and_the_row_stays_pending(home):
    first = _gate_filed(
        run_id="weekly-status-report", session_key="dashboard:c1", name="weekly-status-report"
    )
    proposals.accept(first.id, actor="user")
    before = (home / "workflows" / "defs" / "weekly-status-report" / "workflow.json").read_text()

    # Other steps under the same name: a proposal of its own, which the decision on the first
    # doesn't answer.
    again = _old_row(
        run_id="dashboard:c2",
        session_key="dashboard:c2",
        title="Template: weekly-status-report",
        steps=["List the week's merged changes", "Write the email"],
    )
    with pytest.raises(proposals.NotInstalledError, match="already"):
        proposals.accept(again.id, actor="user")
    assert proposals.get(again.id).status == "pending"
    after = (home / "workflows" / "defs" / "weekly-status-report" / "workflow.json").read_text()
    assert after == before, "accepting replaced a workflow you have"


def test_a_taken_name_answers_409_not_installed_through_the_route(home):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import learning as L

    first = _gate_filed(
        run_id="weekly-status-report", session_key="dashboard:c1", name="weekly-status-report"
    )
    proposals.accept(first.id, actor="user")
    # Other steps under the same name: a proposal of its own, which the decision on the first
    # doesn't answer.
    again = _old_row(
        run_id="dashboard:c2",
        session_key="dashboard:c2",
        title="Template: weekly-status-report",
        steps=["List the week's merged changes", "Write the email"],
    )
    request = make_mocked_request(
        "POST",
        f"/api/learning/proposals/{again.id}/accept",
        match_info={"id": again.id},
        app=web.Application(),
    )
    request["user"] = "me"
    import asyncio

    resp = asyncio.run(L.api_learning_proposal_accept(request))
    body = json.loads(resp.body.decode())
    assert resp.status == 409
    assert body["error"]["code"] == "learning_proposal_not_installed"
    assert "already" in body["error"]["message"]


def test_a_plan_shape_stays_uninstallable_and_says_why(home):
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE.value,
        title="Repeated plan shape in release-notes",
        body="4 recent runs built this same plan.",
        provenance="inferred",
        session_key="",
        run_id="r9",
        tags=["plan_similarity", "run_end"],
    )
    assert installers.branch_for(prop.to_dict()) == ""
    with pytest.raises(proposals.NoProposalInstallerError, match="no steps to save"):
        proposals.accept(prop.id, actor="user")
    assert proposals.get(prop.id).status == "pending"


# ── the name and the fingerprint ────────────────────────────────────────────────────────────────


def test_a_reinforce_keeps_the_name_it_was_filed_with(home):
    first = _gate_filed(
        run_id="weekly-status-report", session_key="dashboard:c1", name="weekly-status-report"
    )
    again = _gate_filed(run_id="friday-email", session_key="dashboard:c1", name="friday-email")
    assert again.id == first.id, "the same steps were filed as a second proposal"
    names = [t for t in proposals.get(first.id).tags if t.startswith("name:")]
    assert names == ["name:weekly-status-report"]


def test_the_name_tag_leaves_the_fingerprint_as_it_was(home):
    tagged = _gate_filed(
        run_id="weekly-status-report", session_key="dashboard:c1", name="weekly-status-report"
    )
    assert tagged.fingerprint == proposals.content_fingerprint(
        proposals.Kind.TEMPLATE.value, tagged.target, tagged.body
    )
