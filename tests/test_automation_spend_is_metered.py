"""Every runtime that does automation's work is metered by the spend guard — a derived rail.

The daily dollar cap counts what ``ModelCallGuard`` charges, and the guard wraps a runtime's model
only when the runtime resolves on one of the guarded axes (``provider_bridge``: the tuple this file
reads out of it). An axis is ALSO a model chain, so a caller that brought its own model had no
reason to name one: a subagent given a model rode the chat binding, which has no guard, and the
cap never counted what it spent. A loop's planner, a webhook's agent turn and a scheduled job's
reading of its subagent's result took the chat binding the same way. Settings → Guardrails says
the cap binds unattended work; every one of those is unattended work.

So the census is every call that acquires a runtime — ``SessionManager.get_or_create`` — DERIVED
by parsing ``src``, never listed. Each one must:

* name a guarded axis (``model_axis="orchestration"``, …);
* or acquire the background session (``BACKGROUND_KEY``, which ``get_or_create`` itself puts on
  the background axis);
* or be one of the manager's own re-acquisitions, which replay a request someone already made
  (:data:`REACQUIRES`);
* or be a surface a person is working in, named in :data:`INTERACTIVE` with the reason — the chat
  window is outside the cap by design, and the Guardrails page says so.

A new call that is none of these reds here, with its file, function and line.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Surfaces a person is working in. The daily cap is for automations; these stay on the chat
#: binding, as a chat does. Keyed by (file under ``src/personalclaw``, function qualname).
INTERACTIVE: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): (
        "a dashboard chat's turn, on the chat binding, which the cap leaves alone as the "
        "Guardrails page says. A loop's worker and planner are the exception, and take the "
        "loops axis in this same call through `model_axis_for` (pinned below)"
    ),
    ("dashboard/side.py", "_run_side_turn"): (
        "a side question a person asked beside their chat, answered while they wait"
    ),
    ("dashboard/handlers/agent_marketplace.py", "api_agent_marketplace_test"): (
        "an agent's test run from its page: a person pressed the button and reads the reply"
    ),
    ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"): (
        "the composer's prompt rewrite: a person pressed the button and waits for the text"
    ),
    ("rooms/turn.py", "member_session"): (
        "a room member answering in a room a person reads; `routing/usage.py` counts a room's "
        "turns as interactive spend"
    ),
    ("gateway.py", "GatewayOrchestrator._init_subagents._subagent_done"): (
        "the channel branch: a finished subagent's result told to the channel thread that "
        "spawned it, a person's conversation, on the binding that thread's own turns use. The "
        "cron branch in the same function names the orchestration axis"
    ),
}

#: The manager's own re-acquisitions: they replay a request someone already made, whole.
REACQUIRES: dict[tuple[str, str], str] = {
    ("session.py", "SessionManager.get_or_create"): (
        "rebuilds a stale runtime with the request its caller has just made, axis included"
    ),
    ("session.py", "SessionManager._eager_respawn"): (
        "rebuilds a hard-stopped runtime with the request that built it "
        "(`_Session.acquired_with`), axis included"
    ),
}


def guarded_axes() -> frozenset[str]:
    """The axes the guard wraps, read from the one line in ``provider_bridge`` that decides."""
    from personalclaw.providers import provider_bridge

    text = Path(provider_bridge.__file__).read_text(encoding="utf-8")
    m = re.search(r'if use_case in \(([^)]*)\):\n\s+kwargs\["_guard_use_case"\]', text)
    assert m, "the guard's axis test moved: this rail reads it to know which axes are metered"
    axes = frozenset(re.findall(r'"([^"]+)"', m.group(1)))
    assert axes and "chat" not in axes, axes
    return axes


@dataclass(frozen=True)
class Site:
    path: str
    qualname: str
    line: int
    call: ast.Call
    func: ast.AST | None


class _Census(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack: list[str] = []
        self.funcs: list[ast.AST] = []
        self.sites: list[Site] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.funcs.append(node)
        self.generic_visit(node)
        self.funcs.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get_or_create":
            func = self.funcs[-1] if self.funcs else None
            self.sites.append(Site(self.path, ".".join(self.stack), node.lineno, node, func))
        self.generic_visit(node)


def census(tree: ast.AST, path: str) -> list[Site]:
    visitor = _Census(path)
    visitor.visit(tree)
    return visitor.sites


def _is_background_key(expr: ast.AST | None, func: ast.AST | None) -> bool:
    """``BACKGROUND_KEY`` itself, or a local name the enclosing function bound to it."""

    def _names_it(node: ast.AST | None) -> bool:
        return (isinstance(node, ast.Name) and node.id == "BACKGROUND_KEY") or (
            isinstance(node, ast.Attribute) and node.attr == "BACKGROUND_KEY"
        )

    if _names_it(expr):
        return True
    if isinstance(expr, ast.Name) and func is not None:
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and _names_it(node.value):
                if any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets):
                    return True
    return False


def verdict(site: Site, guarded: frozenset[str]) -> str:
    """``guarded`` | ``background`` | ``reacquires`` | ``interactive`` | ``""`` (unaccounted)."""
    keywords = {k.arg: k.value for k in site.call.keywords if k.arg}
    axis = keywords.get("model_axis")
    if isinstance(axis, ast.Constant) and axis.value in guarded:
        return "guarded"
    first = site.call.args[0] if site.call.args else keywords.get("key")
    if _is_background_key(first, site.func):
        return "background"
    where = (site.path, site.qualname)
    if where in REACQUIRES and any(k.arg is None for k in site.call.keywords):
        return "reacquires"
    if where in INTERACTIVE:
        return "interactive"
    return ""


def src_sites() -> list[Site]:
    sites: list[Site] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        sites.extend(census(ast.parse(path.read_text(encoding="utf-8")), rel))
    return sites


# ── the rail ───────────────────────────────────────────────────────────────────────────────


def test_every_runtime_acquisition_is_metered_or_is_a_person_s_surface():
    guarded = guarded_axes()
    sites = src_sites()
    unaccounted = [f"{s.path}:{s.line} in {s.qualname}" for s in sites if not verdict(s, guarded)]
    assert not unaccounted, (
        "these calls acquire a runtime on no metered axis, so the daily cap never counts what "
        'they spend. Name the axis the work is (`model_axis="orchestration"` for agent work '
        "nobody typed), or — only for a surface a person is working in — add it to INTERACTIVE "
        "with the reason:\n  " + "\n  ".join(unaccounted)
    )
    # A listing is ONE decision about ONE call. A second unmetered call in the same function is
    # a new decision, not the old one: the scheduled job's reading of its subagent's result sat
    # beside the channel announcement in one function, on no axis, and a per-function allowance
    # would have waved it through.
    unmetered = Counter(
        (s.path, s.qualname) for s in sites if verdict(s, guarded) in ("interactive", "reacquires")
    )
    doubled = {where: n for where, n in unmetered.items() if n > 1}
    assert not doubled, doubled


def test_the_census_is_not_vacuous():
    """A census that parsed nothing would pass the rail above for free."""
    guarded = guarded_axes()
    verdicts = [verdict(s, guarded) for s in src_sites()]
    assert len(verdicts) >= 15, len(verdicts)
    for kind in ("guarded", "background", "reacquires", "interactive"):
        assert kind in verdicts, f"no site reads {kind!r}: the classifier or the census drifted"


def test_every_listed_exemption_still_names_a_real_call():
    """A listed site that no longer exists is an allowance waiting for the next call to use it."""
    guarded = guarded_axes()
    sites = src_sites()
    for listing, kind in ((INTERACTIVE, "interactive"), (REACQUIRES, "reacquires")):
        for where in listing:
            used = [s for s in sites if (s.path, s.qualname) == where]
            assert used, f"{where} is listed but acquires nothing now: drop it"
            assert any(verdict(s, guarded) == kind for s in used), (where, kind)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('await sessions.get_or_create("cron:digest")', ""),
        ('await sessions.get_or_create("cron:digest", model_axis="chat")', ""),
        ('await sessions.get_or_create("cron:digest", model_axis="orchestration")', "guarded"),
        ("await sessions.get_or_create(BACKGROUND_KEY)", "background"),
    ],
    ids=["no axis", "the chat axis", "a guarded axis", "the background session"],
)
def test_the_classifier_on_a_planted_call(snippet: str, expected: str):
    """The rail's teeth, on code that is not in the tree: an unnamed axis and the chat axis are
    both unmetered, and only a guarded one passes."""
    tree = ast.parse(f"async def run_the_nightly_digest(sessions):\n    {snippet}\n")
    (site,) = census(tree, "planted.py")
    assert verdict(site, guarded_axes()) == expected


# ── the loop's planner ─────────────────────────────────────────────────────────────────────


def test_a_loops_worker_and_its_planner_take_the_loops_axis_and_a_chat_does_not(monkeypatch):
    """🔴 Red before the fix: the axis was chosen by `_app == "loop"`, the worker's tag, and the
    planner is tagged differently, so a loop's planning ran on the chat binding, unmetered. The
    planner's tag is read from the call the walkthrough really makes."""
    from personalclaw.dashboard.chat_runner import model_axis_for
    from personalclaw.loop import plan_walkthrough
    from personalclaw.planning import runner

    asked: dict[str, Any] = {}

    async def _planner_pass(state, svc, **kwargs):
        asked.update(kwargs)
        return None

    monkeypatch.setattr(runner, "run_planner_pass", _planner_pass)
    loop = SimpleNamespace(
        id="abcd1234",
        workspace_dir="",
        model="",
        provider="",
        provider_agent="",
        reasoning_effort="",
    )
    walkthrough = SimpleNamespace(planner_agent="personalclaw")
    asyncio.run(plan_walkthrough._run_pass(None, None, loop, walkthrough, brief="b", sentinel="s"))

    assert asked["app"], "vacuity: the planner pass named no app tag"
    assert model_axis_for(SimpleNamespace(_app=asked["app"])) == "loops"
    assert model_axis_for(SimpleNamespace(_app="loop")) == "loops"
    assert model_axis_for(SimpleNamespace(_app="")) == ""
    assert model_axis_for(SimpleNamespace(_app="notes")) == ""


# ── a subagent given a model ───────────────────────────────────────────────────────────────

NAMED, NAMED_REF = "named-oai", "named-oai:gpt-4o"
HEAD, HEAD_REF = "head-oai", "head-oai:gpt-4o-mini"
TOKENS_IN, TOKENS_OUT = 100_000, 10_000


def _price(model: str) -> float:
    from personalclaw.pricing import estimate_cost

    return estimate_cost(model, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)


class _Scripted:
    supports_tools = False

    def __init__(self, entry: str, model: str, calls: list[str]) -> None:
        self.entry = entry
        self.model = model
        self.calls = calls
        self.served_ref = f"{entry}:{model}"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **kw: Any):
        self.calls.append(self.entry)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="blue")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    """A registry of two scripted models behind the real resolution, native builder and guard.
    The orchestration chain's head is NOT the model the spawn names, so a turn that answered on
    the head would show here. The named model is one of the chat models set up in Settings →
    Models, as a model someone names must be (`named_model_problem`)."""
    made: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Scripted(entry.name, str(kwargs.get("model") or entry.model), made)

    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=200_000,
        ),
        _factory,
    )
    for name, ref in ((NAMED, NAMED_REF), (HEAD, HEAD_REF)):
        registry.register_entry(ProviderEntry(name=name, type="scripted", model=ref.split(":")[1]))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [HEAD_REF, NAMED_REF], "orchestration": [HEAD_REF]},
    )
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    return made


async def _named_model_runtime(*, model_axis: str):
    """The runtime a spawn that names a model is given — built as `SessionManager` builds it."""
    from personalclaw.providers.provider_bridge import create_provider_factory

    runtime = create_provider_factory()(
        "subagent:metered", model_override=NAMED_REF, model_axis=model_axis
    )
    await runtime.start()
    return runtime


async def _turn(runtime) -> None:
    async for event in runtime.stream("Name a colour."):
        if event.kind == EVENT_COMPLETE:
            break


def _daily_dollar_cap(dollars: float) -> None:
    """The ceiling as the owner sets it in Settings → Guardrails."""
    import json

    from personalclaw.config.loader import config_dir

    (config_dir() / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": dollars}}}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_every_spawn_rides_the_orchestration_axis_one_that_names_a_model_too():
    """🔴 Red before the fix: a spawn that named a model passed the model INSTEAD of the axis."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    sessions = _mock_sessions()
    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        named = manager.spawn("Name a colour.", parent_session_key="dashboard:p", model=NAMED_REF)
        await manager._tasks[named.id]
        asked_named = sessions.get_or_create.await_args.kwargs
        plain = manager.spawn("Name a colour.", parent_session_key="dashboard:p")
        await manager._tasks[plain.id]
        asked_plain = sessions.get_or_create.await_args.kwargs

    assert (asked_named.get("model"), asked_named.get("model_axis")) == (NAMED_REF, "orchestration")
    assert (asked_plain.get("model"), asked_plain.get("model_axis")) == (None, "orchestration")


@pytest.mark.asyncio
async def test_a_named_model_on_the_orchestration_axis_is_metered_and_still_the_one_that_runs(
    calls,
):
    """The axis is what meters the call; the model the spawn named is still the one that answers.
    The chat axis — where a named spawn used to land — charges nothing: that is the hole."""
    from personalclaw.guardrails.budgets import get_meter

    on_chat = await _named_model_runtime(model_axis="")
    await _turn(on_chat)
    assert calls == [NAMED]
    assert get_meter().day_totals().dollars == 0.0, "the chat binding is unmetered by design"

    metered = await _named_model_runtime(model_axis="orchestration")
    await _turn(metered)
    assert calls == [NAMED, NAMED], "the named model answered, not the orchestration chain's head"
    assert get_meter().day_totals().dollars == pytest.approx(_price("gpt-4o"))


@pytest.mark.asyncio
async def test_the_daily_dollar_cap_refuses_a_named_spawn_once_the_day_is_spent(calls):
    """The cap as the week drives it: set a small daily ceiling, let one spawn that names a model
    spend past it, and the next call AND the next spawn are both refused, each saying which
    ceiling stopped it."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.guardrails.failure import BudgetExceededError
    from personalclaw.subagent import SubagentManager

    _daily_dollar_cap(0.01)
    runtime = await _named_model_runtime(model_axis="orchestration")
    await _turn(runtime)  # $0.35 of gpt-4o against a $0.01 ceiling: allowed, and charged

    with pytest.raises(BudgetExceededError) as refused:
        await _turn(runtime)
    assert refused.value.sentence() == (
        f"The daily dollar budget is spent (${_price('gpt-4o'):.2f} of $0.01): it resets "
        "tomorrow, or raise it in Settings → Guardrails."
    )

    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        spawn = manager.spawn("Name a colour.", parent_session_key="dashboard:p", model=NAMED_REF)
    assert spawn is not None and spawn.done
    assert "day dollar budget exceeded" in spawn.error, spawn.error


# ── the day counts each child once ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_metered_child_is_counted_against_the_day_once(calls, monkeypatch):
    """🔴 Red before the fix: with a per-run ceiling set, a child's completion charged the day AGAIN
    for spend its guard had already charged call by call, so the cap bit at half the real spend."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.guardrails.budgets import Budget, get_meter
    from personalclaw.subagent import SubagentManager

    monkeypatch.setattr(
        "personalclaw.guardrails.budgets.run_budget_from_config",
        lambda: Budget(max_tokens=10**9),
    )
    # The fan-out's run total is dropped once its last child is done (`_maybe_clear_fanout`),
    # so what it was charged is read as it is charged.
    meter, run_charges = get_meter(), []
    charge_run = meter.charge_run

    def _charge_run(key: str, tokens: int, dollars: float) -> None:
        run_charges.append((key, dollars))
        charge_run(key, tokens, dollars)

    monkeypatch.setattr(meter, "charge_run", _charge_run)
    runtime = await _named_model_runtime(model_axis="orchestration")
    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("Name a colour.", parent_session_key="dashboard:p", model=NAMED_REF)
        await manager._tasks[info.id]

    assert info._spend_metered is True, "its completion named the guarded calls it came from"
    assert meter.day_totals().dollars == pytest.approx(_price("gpt-4o"))
    assert run_charges == [("dashboard:p", pytest.approx(info.cost_usd))]


def test_a_child_no_guard_saw_is_counted_against_the_day_at_completion():
    """🔴 Red before the fix: an agent CLI's child (its calls are the CLI's own, so no guard sees
    them) reached the day only when a per-run ceiling happened to be set, and never otherwise."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.guardrails.budgets import get_meter
    from personalclaw.subagent import SubagentInfo, SubagentManager

    manager = SubagentManager(sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder())
    child = SubagentInfo(id="c1", task="t", agent="", parent_session_key="dashboard:p")
    child.input_tokens, child.output_tokens, child.cost_usd = 1_000, 100, 0.25
    with patch("personalclaw.subagent.sel"):
        manager._charge_child_and_check_budget(child)
    assert get_meter().day_totals().dollars == pytest.approx(0.25)
    assert get_meter().day_totals().tokens == 1_100


# ── a webhook's agent turn ─────────────────────────────────────────────────────────────────


class _HookSessions:
    def __init__(self, stream_raises: BaseException | None = None) -> None:
        self.asked: list[dict[str, Any]] = []
        self.stream_raises = stream_raises

    async def get_or_create(self, key: str, **kwargs: Any):
        self.asked.append(kwargs)
        raises = self.stream_raises

        async def _stream(_message: str):
            if raises is not None:
                raise raises
            yield LLMEvent(kind=EVENT_COMPLETE)

        return SimpleNamespace(stream=_stream), False, False

    def record_success(self, _key: str) -> None:
        return None

    def release(self, _key: str) -> None:
        return None

    async def reset(self, _key: str) -> None:
        return None

    async def record_failure(self, _key: str) -> None:
        return None


def test_a_webhook_turn_rides_the_metered_axis_and_says_a_refusal_for_what_it_is(monkeypatch):
    """🔴 Red before the fix: the turn took the chat binding, unmetered; and the words a refusal now
    produces must be the refusal, not the "internal failure" every other error reads as."""
    from personalclaw.dashboard.handlers import hooks as hooks_mod
    from personalclaw.guardrails.failure import BudgetExceededError

    notes: list[str] = []
    audit: list[str] = []
    state = SimpleNamespace(
        sessions=_HookSessions(stream_raises=BudgetExceededError("day", "dollars", 5.0, 5.25)),
        context_builder=None,
        notify=lambda _kind, _title, body, meta=None: notes.append(body),
    )
    monkeypatch.setattr(
        hooks_mod,
        "_sel",
        lambda: SimpleNamespace(log_tool_invocation=lambda **kw: audit.append(kw["outcome"])),
    )
    monkeypatch.setattr("personalclaw.channel_delivery.deliver_to_owner", AsyncMock())

    async def _fire() -> None:
        await hooks_mod._hook_semaphore.acquire()
        await hooks_mod._run_hook_agent(
            state, "hook:nightly", "summarise the night", "Nightly", None, True, 60
        )

    asyncio.run(_fire())

    assert state.sessions.asked == [{"agent": None, "model_axis": "orchestration"}]
    assert audit == ["refused_budget_exceeded"]
    assert notes == [
        "Hook agent stopped: The daily dollar budget is spent ($5.25 of $5.00): it resets "
        "tomorrow, or raise it in Settings → Guardrails."
    ]
