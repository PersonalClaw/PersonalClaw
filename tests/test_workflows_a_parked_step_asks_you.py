"""A step that parks on a sign-in page asks you, and answering it continues the run.

The browse action parks on a sign-in page with `outcome="needs_input"`, and the engine mapped that
to a WAITING instance — which `_apply` returned early from, keeping no output, and which `_step`
never asked about, because only a GATE node got a continuation. So the run ended `needs_input`
with no question anywhere: no resume token, no Inbox row, nothing on the run page, and the
needs-input card `request_login` had composed (and #3651 had fixed the wording of) was thrown away.
Measured on a dev gateway before this change: the run's journal read `step_started`, then
`run_finished needs_input`, and the Inbox was empty.

What these pin, driven through the real browse provider where it matters:

* the park keeps the step's output, mints an answerable continuation, and raises ONE Inbox row
  carrying the card's own wording and the live token;
* approving runs the step again with your answer on the dispatch it starts, and the run carries
  on; denying ends the step as declined;
* the browse step you confirmed a sign-in for does not park on the same pre-run check again;
* a step that parked is never waved through by the gate policy: nobody can auto-approve a
  sign-in, and "don't ask again" is not kept for one;
* an expired session inside a run asks once (the run's answerable card), not twice;
* an action that asks a question in its own output is answerable too — the same missing
  continuation, reached by the other path an action can wait by.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.action_providers.browse_provider import OUTCOME_NEEDS_INPUT, BrowseActionProvider
from personalclaw.browse.handoff import mark_expired, record_login, site_slug
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    FailureClass,
    InstanceState,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.needs_input import from_refs

pytestmark = pytest.mark.anyio

# Port 9 is discard: nothing is ever fetched, because the pre-run check parks before a browser or a
# model is touched. That is also what makes every run here zero-token.
LOGIN_URL = "http://127.0.0.1:9/login"
ACCOUNT_URL = "http://127.0.0.1:9/account"
STEP = "root.children[0]"
AFTER = "root.children[1]"
NEVER_SIGNED_IN = "has never been signed in on this machine"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # The browse profiles read the home per call, the way the browse suites isolate them.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


class _State:
    """A dashboard state whose Inbox is a REAL `InboxStore`, read the way the live service is,
    and which records the WS frames it is asked to broadcast."""

    def __init__(self) -> None:
        from personalclaw.inbox import InboxStore

        class Svc:
            def __init__(self) -> None:
                self.inbox = InboxStore()

        self._inbox_svc = Svc()
        self.frames: list[tuple[str, dict[str, Any]]] = []

    def notify(self, *a: Any, **k: Any) -> None:
        pass

    def broadcast_ws(self, kind: str, payload: dict[str, Any]) -> None:
        self.frames.append((kind, payload))


def _open_rows(state: _State, run_id: str = "") -> list[Any]:
    from personalclaw.inbox import OPEN_STATUSES

    return [
        i
        for i in state._inbox_svc.inbox.items.values()
        if i.status in OPEN_STATUSES and (not run_id or (i.refs or {}).get("workflow") == run_id)
    ]


def _spec(start_url: str = LOGIN_URL, **node_cfg: Any) -> dict[str, Any]:
    return {
        "name": "sign-in-park",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "fetch",
                    "config": {
                        "provider": "browse",
                        "with": {"goal": "read my balance", "start_url": start_url},
                        **node_cfg,
                    },
                },
                {"kind": "transform", "id": "after", "config": {"expr": "carried on"}},
            ],
        },
    }


class _Browse:
    """The real browse provider, with every context it was handed recorded."""

    def __init__(self) -> None:
        self.contexts: list[ActionContext] = []
        self._inner = BrowseActionProvider()

    async def execute(self, cfg: dict[str, Any], ctx: ActionContext, timeout: int = 30):
        self.contexts.append(ctx)
        return await self._inner.execute(cfg, ctx, timeout=timeout)


class _ParksOnce:
    """Parks the first time; finishes on the dispatch your answer starts."""

    def __init__(self) -> None:
        self.contexts: list[ActionContext] = []

    async def execute(self, cfg: dict[str, Any], ctx: ActionContext, timeout: int = 30):
        self.contexts.append(ctx)
        if len(self.contexts) == 1:
            return ActionResult(
                success=True,
                outcome=OUTCOME_NEEDS_INPUT,
                stdout=json.dumps({"notes": ["half way there"]}),
                stderr="The step stopped because its model budget is spent; 1 note kept.",
            )
        return ActionResult(success=True, stdout=json.dumps({"finished": True}))


async def _run(
    spec: dict[str, Any], provider: Any, *, state: _State, origin: RunOrigin | None = None
) -> tuple[RunController, RunStatus]:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], origin=origin or RunOrigin()))
    store.write_spec(run.id, spec)
    c = RunController(
        run,
        spec,
        services=EngineServices(get_provider=lambda name: provider, attention_state=state),
    )
    return c, await c.run_to_completion(timeout=20)


def _only_token(run_id: str) -> str:
    pending = HI.list_continuations(run_id)
    assert len(pending) == 1, f"expected one open question, found {len(pending)}"
    return pending[0].token


async def test_a_step_parked_on_a_sign_in_page_asks_you() -> None:
    state = _State()
    c, status = await _run(_spec(), _Browse(), state=state)
    assert status == RunStatus.NEEDS_INPUT
    slug = site_slug(LOGIN_URL)
    tried = [f"opened {slug} — it {NEVER_SIGNED_IN}"]

    inst = c.instances[STEP]
    assert inst.state == InstanceState.WAITING
    # The output: what the step produced before it stopped, kept on the step.
    assert inst.output_ref, "the park kept no output"
    kept = store.read_output(c.run.id, STEP)
    assert kept["site"] == slug and kept["reason"] == "no_session"
    # Kept, the handoff's sentence is on the run (a declined step's Inspect shows it), so it says
    # where to sign in — the browser the step drives — and not that a window was opened: nothing
    # launches one (`headful_launch_args` is handed back and never run).
    assert kept["sentence"] == (
        f"Browse needs you to sign in to {slug}, in the browser this step drives: authenticate "
        "there (password, 2FA, whatever it asks), then answer this item and the run continues "
        "with the session you created. PersonalClaw never sees what you type."
    )

    # The continuation: an answerable question, worded by the step's own card.
    pending = HI.list_continuations(c.run.id)
    assert [p.node_id for p in pending] == [
        "fetch"
    ], "the park minted no continuation, so nothing anywhere can answer it"
    ask = pending[0].ask
    assert ask["kind"] == "approval"
    assert ask["prompt"] == (
        f"Sign in to {slug}, then confirm — the browse run resumes with that session."
    )
    assert ask["rerun"] is True
    assert pending[0].handoff["attempted"] == tried

    # The attention item: ONE Inbox row, carrying the live token and #3651's wording.
    rows = _open_rows(state, c.run.id)
    assert len(rows) == 1, [r.message for r in rows]
    row = rows[0]
    assert row.refs["resume_token"] == pending[0].token
    assert row.refs["workflow_node"] == "fetch"
    assert NEVER_SIGNED_IN in row.message
    card = from_refs(row.refs)
    assert card is not None
    assert card.attempted == tried
    assert card.evidence["reason"] == "no_session"
    # Every choice a card offers must be an answer its continuation accepts. An approval takes
    # yes or no, so the handoff's labelled choices are not offered as buttons that would fail.
    assert card.choices == []


async def test_approving_a_parked_step_runs_it_again_and_the_run_carries_on() -> None:
    state = _State()
    step = _ParksOnce()
    c, status = await _run(_spec(), step, state=state)
    assert status == RunStatus.NEEDS_INPUT

    result = c.resume(_only_token(c.run.id), True)
    assert result["ok"] and result["approved"], result
    await asyncio.wait_for(c._terminal.wait(), timeout=10)

    assert c.run.status == RunStatus.COMPLETE
    assert len(step.contexts) == 2, "the step was not run again"
    assert step.contexts[0].answer is None
    assert step.contexts[1].answer is True, "the dispatch your answer started must carry it"
    assert store.read_output(c.run.id, STEP) == {"finished": True}
    assert c.instances[AFTER].state == InstanceState.DONE
    # The parked attempt's partial result is archived, not overwritten.
    attic = store.run_dir(c.run.id) / "outputs" / "attic"
    assert list(attic.rglob("*.json")), "the parked step's notes were discarded"
    assert _open_rows(state, c.run.id) == []
    assert HI.list_continuations(c.run.id) == []


async def test_denying_a_parked_step_ends_it_as_declined() -> None:
    state = _State()
    step = _ParksOnce()
    c, _status = await _run(_spec(), step, state=state)

    result = c.resume(_only_token(c.run.id), False)
    assert result["ok"] and result["approved"] is False, result
    await asyncio.wait_for(c._terminal.wait(), timeout=10)

    assert c.run.status == RunStatus.FAILED
    inst = c.instances[STEP]
    assert inst.state == InstanceState.FAILED
    assert inst.failure is not None and inst.failure.failure_class == FailureClass.USER
    assert len(step.contexts) == 1, "a declined step must not run again"
    assert _open_rows(state, c.run.id) == []


async def test_once_you_confirm_the_sign_in_the_browse_step_does_not_ask_again() -> None:
    """Plan §5.2: "on user confirmation … the run resumes with the now-authenticated session". The
    pre-run check reads the profile's own `.meta.json`, which a human signing in never writes, so
    re-running it after your answer parked on the same check by construction. The dispatch your
    answer starts goes on to the run, which OBSERVES the session (`record_login` on a completed run,
    a credential-field park if the page still asks for a password)."""
    state = _State()
    browse = _Browse()
    c, _status = await _run(_spec(), browse, state=state)

    assert c.resume(_only_token(c.run.id), True)["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)

    assert len(browse.contexts) == 2
    assert browse.contexts[1].answer is True
    assert HI.list_continuations(c.run.id) == [], "the confirmed sign-in parked on the same check"
    # No browser is configured here, so the step stops at the NEXT real obstacle — which is the
    # point: it got past the sign-in check your answer settled.
    failures = [
        e
        for e in J.ledger(c.run.id)
        if e.get("kind") == J.STEP_FAILED and e.get("node_id") == "fetch"
    ]
    assert failures, "the second dispatch did not reach the browser step at all"
    assert "cdp_url" in json.dumps(failures[-1]), failures[-1]


async def test_the_browse_provider_goes_on_to_the_run_on_the_dispatch_your_answer_started() -> None:
    confirmed = await BrowseActionProvider().execute(
        {"goal": "read my balance", "start_url": LOGIN_URL},
        ActionContext(event="workflow_node", answer=True),
    )
    assert confirmed.outcome != OUTCOME_NEEDS_INPUT
    assert confirmed.agent_error is not None
    assert confirmed.agent_error.code == "ERR_BROWSE_NO_TARGET"
    # The control: with no answer, the same call parks — the check still runs.
    unanswered = await BrowseActionProvider().execute(
        {"goal": "read my balance", "start_url": LOGIN_URL},
        ActionContext(event="workflow_node"),
    )
    assert unanswered.outcome == OUTCOME_NEEDS_INPUT


async def test_an_unattended_run_never_waves_a_parked_step_through() -> None:
    """The gate policy auto-approves safe and caution GATES in an unattended run. It was applied to
    every WAITING result, so a browse step declared `risk: caution` in a scheduled run was marked
    done with `{"approved": true}` for a sign-in nobody made, and the run completed on it."""
    state = _State()
    c, status = await _run(
        _spec(risk="caution"),
        _Browse(),
        state=state,
        origin=RunOrigin(kind=OriginKind.SCHEDULE),
    )
    assert status == RunStatus.NEEDS_INPUT, "the gate policy approved a sign-in nobody made"
    assert c.instances[STEP].state == InstanceState.WAITING
    assert AFTER not in c.instances or c.instances[AFTER].state == InstanceState.PENDING
    assert len(_open_rows(state, c.run.id)) == 1


async def test_dont_ask_again_is_not_kept_for_a_parked_step() -> None:
    """Remembering an allow for a sign-in would promise something no later park honours."""
    state = _State()
    c, _status = await _run(_spec(), _ParksOnce(), state=state)
    assert c.resume(_only_token(c.run.id), True, always_allow=True)["ok"]
    assert len(c._allow_memory) == 0


async def test_an_expired_session_inside_a_run_asks_once(monkeypatch) -> None:
    """A stale session raises the banner and, for a dispatch the engine does not own (a trigger,
    a hook), its own Inbox row. Inside a run the engine raises the run's answerable row, so the
    site row would be the same question asked a second time, with no way to answer it."""
    state = _State()
    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", lambda: state
    )
    record_login(ACCOUNT_URL)
    mark_expired(ACCOUNT_URL)

    c, status = await _run(_spec(start_url=ACCOUNT_URL), _Browse(), state=state)
    assert status == RunStatus.NEEDS_INPUT

    rows = [r for r in _open_rows(state) if r.item_kind == "needs_input"]
    assert len(rows) == 1, [(r.message, (r.refs or {}).get("workflow")) for r in rows]
    assert rows[0].refs.get("workflow") == c.run.id, "the one row must be the answerable one"
    assert rows[0].refs.get("resume_token") == _only_token(c.run.id)
    # The banner is not a question, so it still goes up.
    assert ("browse_auth_expired", {"site": site_slug(ACCOUNT_URL)}) in state.frames


async def test_an_action_that_asks_a_question_in_its_output_can_be_answered() -> None:
    class _Asks:
        async def execute(self, cfg: dict[str, Any], ctx: ActionContext, timeout: int = 30):
            return ActionResult(
                success=True,
                stdout=json.dumps(
                    {
                        "needs_input": {
                            "kind": "choice",
                            "prompt": "Which environment?",
                            "choices": ["dev", "prod"],
                        }
                    }
                ),
            )

    state = _State()
    c, status = await _run(_spec(), _Asks(), state=state)
    assert status == RunStatus.NEEDS_INPUT
    pending = HI.list_continuations(c.run.id)
    assert len(pending) == 1, "the question was asked with no way to answer it"
    assert pending[0].ask["choices"] == ["dev", "prod"]
    assert len(_open_rows(state, c.run.id)) == 1

    assert c.resume(pending[0].token, "prod")["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE
    assert store.read_output(c.run.id, STEP)["answer"] == "prod"
