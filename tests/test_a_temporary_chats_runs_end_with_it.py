"""A Temporary chat's workflow runs end with it, and are removed.

A Temporary chat is forgotten when its session ends: its transcript, its working folder and the
files attached to it are deleted. A workflow run it starts is its own work: it keeps the chat's mode
and runs on the chat's model, and its record holds the chat's words (the inputs it was started
with, what its steps produced, its journal). That record outlived the chat, where the agents of
your other chats read it (``workflow_status``, ``workflow_output``) and so did you.

Now the workflow supervisor stops a run whose Temporary chat has ended, saying so, and then deletes
it with what it produced, however the chat ended: deleted, evicted as inactive, or ended with the
gateway that ran it. An ordinary chat's runs, and an Incognito chat's, are kept, as their
transcripts are.

Driven as a person drives it: the real gateway with its workflow supervisor, the real tool server
an agent CLI runs starting each run as its chat's, and the chat ending as the dashboard ends it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    CLI_MODEL,
    TEMPORARY_KEY,
    TWO_STEPS,
    WAITS_LONG,
    _body,
    _ends,
    _state,
    _tool_server,
    _until,
    gateway,
)

from personalclaw import session_restrictions
from personalclaw.cancellation import cancel_and_wait
from personalclaw.dashboard.chat_forget import forget_temporary_chat
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import ownership, private_runs, store
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

#: An ordinary chat and an Incognito chat on the agent CLI, beside the Temporary one.
ORDINARY_KEY = "dashboard:chat-runs-ordinary"
INCOGNITO_KEY = "dashboard:chat-runs-incognito"

#: What the person asked the run to work on, which its record holds.
WORDS = "the guest list for my sister's surprise party"

#: A workflow whose one step starts the two-step workflow as a run of its own and leaves it.
LAUNCHES = {
    "name": "starts-two-steps",
    "description": "Starts the two-step workflow and leaves it running.",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {
                "kind": "action",
                "id": "start-it",
                "config": {"provider": "run-workflow", "with": {"workflow": TWO_STEPS["name"]}},
            }
        ],
    },
}


class _Launching(defs_mod.WorkflowDefProvider):
    """The workflow whose step starts another, beside the gateway's own."""

    @property
    def name(self) -> str:
        return "starts-a-run"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [LAUNCHES], 1

    async def get_def(self, name: str):
        return LAUNCHES if name == LAUNCHES["name"] else None


def _open(gw: SimpleNamespace, key: str, mode: str) -> None:
    """A chat live in the gateway, its turn on the CLI's model, as the turn engine records it."""
    gw.state.get_or_create_session(key.removeprefix("dashboard:"), memory_mode=mode)
    session_restrictions.mark_own_model(key, CLI_MODEL)


def _end(gw: SimpleNamespace, key: str) -> None:
    """The chat's session ends as the dashboard ends it: a Temporary chat is forgotten, any other
    is let go (deleted, or archived as inactive)."""
    name = key.removeprefix("dashboard:")
    session = gw.state._sessions.get(name)
    if session is not None and session.memory_mode == "temporary":
        forget_temporary_chat(gw.state, session, why="it was evicted as inactive")
    else:
        gw.state._sessions.pop(name, None)


async def _started(gw: SimpleNamespace, key: str, workflow: str) -> str:
    """A run the chat's agent starts through the tool server, as the chat's own."""
    [(ok, text)] = await _tool_server(gw, ("workflow_start", {"name": workflow}), key=key)
    assert ok, text
    return str(_body(text)["run_id"])


def _gone(run_id: str) -> bool:
    return store.get(run_id) is None and not store.run_dir(run_id).exists()


async def _polled_by_hand(gw: SimpleNamespace) -> SimpleNamespace:
    """The Temporary chat live in the gateway, and the supervisor's own poll stopped: each poll the
    test makes is then the only one, so nothing removes a run between two of its reads. Runs are
    still started and driven, which no poll does."""
    _open(gw, TEMPORARY_KEY, "temporary")
    await cancel_and_wait([gw.supervisor._task], what="the supervisor's own poll")
    return gw


@pytest.fixture
def chats(gateway):  # noqa: F811 - the imported fixture
    yield gateway
    for key in (ORDINARY_KEY, INCOGNITO_KEY):
        session_restrictions.clear(key)


@pytest.mark.asyncio
async def test_a_temporary_chats_run_is_removed_once_the_chat_ends(chats):
    """🔴 Before: the finished run, its inputs and its steps' outputs stayed in your runs after the
    chat was forgotten, for the agents of your other chats to read."""
    gw = await _polled_by_hand(chats)
    run_id = await _started(gw, TEMPORARY_KEY, TWO_STEPS["name"])
    assert await _ends(run_id) == RunStatus.COMPLETE

    await gw.supervisor._poll_once()
    assert store.get(run_id) is not None, "kept while its chat runs"
    assert ownership.run_mode(store.get(run_id)) is ownership.MemoryMode.TEMPORARY

    _end(gw, TEMPORARY_KEY)
    await gw.supervisor._poll_once()

    assert _gone(run_id)


@pytest.mark.asyncio
async def test_a_temporary_chats_live_run_is_stopped_then_removed_when_the_chat_ends(chats):
    """A run still working when its chat ends is stopped first, saying why, so it closes what it
    holds, and is removed once it has ended. 🔴 Before: it went on, on the chat's model, after the
    chat was gone, and its record stayed."""
    gw = await _polled_by_hand(chats)
    run_id = await _started(gw, TEMPORARY_KEY, WAITS_LONG["name"])
    await _until(lambda: _state(run_id, "root.children[1]") == "waiting")

    _end(gw, TEMPORARY_KEY)
    await gw.supervisor._poll_once()
    assert await _ends(run_id) == RunStatus.CANCELLED
    ending = store.get(run_id).error_message
    await gw.supervisor._poll_once()

    assert ending == "Stopped because its Temporary chat ended.", ending
    assert private_runs.ENDED[ownership.MemoryMode.TEMPORARY] == "its Temporary chat ended"
    assert _gone(run_id)


def _left_by_an_earlier_gateway(status: RunStatus, *, mode: str) -> str:
    """A run a chat on a channel started before this gateway did, with its record and folder."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TWO_STEPS["name"],
            status=status,
            inputs={"topic": WORDS},
            origin=RunOrigin(kind=OriginKind.CHAT, session_key="slack:T0:C0:1700000000.000100"),
            created_at="2026-01-01T00:00:00Z",
            extra=ownership.stamp_run_mode({}, ownership.MemoryMode(mode)),
        )
    )
    store.write_spec(run.id, TWO_STEPS)
    return run.id


@pytest.mark.asyncio
async def test_a_temporary_run_an_earlier_gateway_left_is_removed(chats):
    """Every session an earlier gateway ran ended with it, so a Temporary run it left, ended or
    still marked working, is stopped and removed; an Incognito one is kept. 🔴 Before: a working
    one was taken on again and driven on, and both stayed."""
    gw = await _polled_by_hand(chats)
    ended = _left_by_an_earlier_gateway(RunStatus.COMPLETE, mode="temporary")
    working = _left_by_an_earlier_gateway(RunStatus.RUNNING, mode="temporary")
    incognito = _left_by_an_earlier_gateway(RunStatus.COMPLETE, mode="incognito")

    await gw.supervisor._poll_once()
    await _until(lambda: store.get(working) is None or store.get(working).is_terminal)
    await gw.supervisor._poll_once()

    assert _gone(ended)
    assert _gone(working)
    assert store.get(incognito) is not None


@pytest.mark.asyncio
async def test_an_ordinary_or_incognito_chats_run_is_kept_after_the_chat(chats):
    """An ordinary chat's runs and an Incognito chat's are kept when the chat ends, as their
    transcripts are; so is a Temporary chat's while the chat runs."""
    gw = await _polled_by_hand(chats)
    _open(gw, ORDINARY_KEY, "persistent")
    _open(gw, INCOGNITO_KEY, "incognito")
    ordinary = await _started(gw, ORDINARY_KEY, TWO_STEPS["name"])
    incognito = await _started(gw, INCOGNITO_KEY, TWO_STEPS["name"])
    temporary = await _started(gw, TEMPORARY_KEY, TWO_STEPS["name"])
    for run_id in (ordinary, incognito, temporary):
        assert await _ends(run_id) == RunStatus.COMPLETE

    _end(gw, ORDINARY_KEY)
    _end(gw, INCOGNITO_KEY)
    await gw.supervisor._poll_once()

    for run_id in (ordinary, incognito, temporary):
        assert store.get(run_id) is not None, run_id
        assert store.read_output(run_id, "root.children[1]") == "saw 1"


@pytest.mark.asyncio
async def test_the_run_a_temporary_chats_run_started_ends_with_the_chat_too(chats):
    """A run a step of the chat's run started is that chat's work too, one tree with it: it keeps
    nothing as the chat does and goes when the chat ends. 🔴 Before: it was an ordinary run of its
    own, kept with everything it was given, after the chat and its run were gone."""
    gw = await _polled_by_hand(chats)
    defs_mod.register_provider(_Launching())
    run_id = await _started(gw, TEMPORARY_KEY, LAUNCHES["name"])
    await _ends(run_id)
    await _until(lambda: len(store.list_runs(root_run_id=run_id)[0]) == 2)
    [child] = [run for run in store.list_runs(root_run_id=run_id)[0] if run.id != run_id]
    assert await _ends(child.id) == RunStatus.COMPLETE
    assert ownership.run_mode(child) is ownership.MemoryMode.TEMPORARY
    assert child.parent_run_id == run_id

    _end(gw, TEMPORARY_KEY)
    await gw.supervisor._poll_once()

    assert _gone(run_id)
    assert _gone(child.id)
