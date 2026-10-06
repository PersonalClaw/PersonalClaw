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
* or have its axis decided by a named function whose answers are pinned below (:data:`DECIDED`):
  a chat's turn takes the loops axis when it is a loop's; a finished subagent's announcement takes
  orchestration unless its parent is a channel thread's conversation;
* or be one of the manager's own re-acquisitions, which replay a request someone already made
  (:data:`REACQUIRES`);
* or be a surface a person is working in, named in :data:`INTERACTIVE` with the reason — the chat
  window is outside the cap by design, and the Guardrails page says so.

A new call that is none of these reds here, with its file, function and line.

A model is also reached WITHOUT acquiring a runtime: resolved directly, and called. That census is
every direct call of the resolvers (:data:`RESOLVERS`). Each must resolve a metered axis, resolve
metered (``resolve_metered_model``; the image reader's ``metered=True``), or be listed in
:data:`UNMETERED_RESOLUTIONS` with the reason. The chain walk and one-shot calls resolve metered by
construction, and so does every background chore, each a one-shot call (``chores.run_chore``). A
knowledge node, a loop's judge and a browse step's image reading resolved the chat or image axis
the way a person's turn does, so none of their calls counted.

And an agent CLI makes its own model calls, which no guard wraps: acquired on a metered axis, its
turns are metered themselves (``acp.spend``), so a loop, a subagent or a webhook on one counts too.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections import Counter
from collections.abc import Callable
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
}

#: Calls whose axis a named function decides, keyed like INTERACTIVE: the call passes
#: ``model_axis=<function>(...)``, and the tests below pin what the function answers. A chat's
#: turn is on the chat binding, or on Code & tools when it works in a folder of its own, both of
#: which the cap leaves alone as the Guardrails page says, unless it is a loop's worker or planner.
#: A side question a person asks beside their chat is answered on the chat's own axis, while they
#: wait. A finished subagent's announcement keeps the chat binding in a channel thread, a person's
#: conversation, and is metered in any other parent.
DECIDED: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): "model_axis_for",
    ("dashboard/side.py", "_run_side_turn"): "chat_model_axis",
    ("gateway.py", "GatewayOrchestrator._init_subagents._subagent_done"): "announce_axis",
}

#: The manager's own re-acquisitions: they replay a request someone already made, whole.
REACQUIRES: dict[tuple[str, str], str] = {
    ("session.py", "SessionManager.get_or_create"): (
        "rebuilds a stale runtime with the request its caller has just made, axis included"
    ),
}

#: What hands a caller a model to call directly, with no runtime acquired.
RESOLVERS = frozenset(
    {"resolve_provider_for_use_case", "resolve_metered_model", "resolve_image_reader"}
)

#: The resolvers' own modules: what they call there is the resolution itself, and a runtime's
#: inner model (``_build_native_runtime``) is metered by the axis its acquisition names.
RESOLVER_HOMES = frozenset({"providers/provider_bridge.py", "providers/image_input.py"})

#: Direct resolutions that stay unmetered, keyed like INTERACTIVE, with the reason.
UNMETERED_RESOLUTIONS: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "_describe_screen_frame"): (
        "the reading of the screen a person shared into their own chat turn, for the turn they "
        "are waiting on, and unmetered like it"
    ),
    ("dashboard/handlers/model_check.py", "api_onboarding_model_check"): (
        "onboarding's check that a chat model builds, which a person pressed: it builds the model "
        "and calls nothing"
    ),
}


def guarded_axes() -> frozenset[str]:
    """The axes the guard wraps, as ``provider_bridge`` decides them — and the one line there that
    asks, so a constant nothing reads cannot stand in for the rule."""
    from personalclaw.providers import provider_bridge

    text = Path(provider_bridge.__file__).read_text(encoding="utf-8")
    assert re.search(
        r'if _metered or use_case in METERED_AXES:\n\s+kwargs\["_guard_use_case"\]', text
    ), "the guard's axis test moved: this rail reads it to know which axes are metered"
    axes = frozenset(provider_bridge.METERED_AXES)
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
    """Every call *counted* answers for, with the function it sits in."""

    def __init__(self, path: str, counted: Callable[[ast.Call], bool]) -> None:
        self.path = path
        self.counted = counted
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
        if self.counted(node):
            func = self.funcs[-1] if self.funcs else None
            self.sites.append(Site(self.path, ".".join(self.stack), node.lineno, node, func))
        self.generic_visit(node)


def _acquires(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "get_or_create"


def _resolves(call: ast.Call) -> bool:
    return _called(call) in RESOLVERS


def census(tree: ast.AST, path: str, counted: Callable[[ast.Call], bool] = _acquires) -> list[Site]:
    visitor = _Census(path, counted)
    visitor.visit(tree)
    return visitor.sites


def _called(expr: ast.AST | None) -> str:
    """The bare name *expr* calls, for ``f(...)`` and ``mod.f(...)``; ``""`` for anything else."""
    if not isinstance(expr, ast.Call):
        return ""
    func = expr.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def verdict(site: Site, guarded: frozenset[str]) -> str:
    """``guarded`` | ``decided`` | ``reacquires`` | ``interactive`` | ``""`` (unaccounted)."""
    keywords = {k.arg: k.value for k in site.call.keywords if k.arg}
    axis = keywords.get("model_axis")
    if isinstance(axis, ast.Constant) and axis.value in guarded:
        return "guarded"
    decider = DECIDED.get((site.path, site.qualname))
    if decider and _called(axis) == decider:
        return "decided"
    where = (site.path, site.qualname)
    if where in REACQUIRES and any(k.arg is None for k in site.call.keywords):
        return "reacquires"
    if where in INTERACTIVE:
        return "interactive"
    return ""


def resolution_verdict(site: Site, guarded: frozenset[str]) -> str:
    """``guarded`` | ``metered`` | ``listed`` | ``""`` (unaccounted), for a direct resolution."""
    keywords = {k.arg: k.value for k in site.call.keywords if k.arg}
    resolver = _called(site.call)
    if resolver == "resolve_metered_model":
        return "metered"
    if resolver == "resolve_image_reader":
        asked = keywords.get("metered")
        if isinstance(asked, ast.Constant) and asked.value is True:
            return "metered"
    if resolver == "resolve_provider_for_use_case":
        axis = site.call.args[0] if site.call.args else keywords.get("use_case")
        if isinstance(axis, ast.Constant) and axis.value in guarded:
            return "guarded"
    if (site.path, site.qualname) in UNMETERED_RESOLUTIONS:
        return "listed"
    return ""


def src_sites(counted: Callable[[ast.Call], bool] = _acquires) -> list[Site]:
    sites: list[Site] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if counted is _resolves and rel in RESOLVER_HOMES:
            continue
        sites.extend(census(ast.parse(path.read_text(encoding="utf-8")), rel, counted))
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
    assert len(verdicts) >= 10, len(verdicts)
    for kind in ("guarded", "decided", "reacquires", "interactive"):
        assert kind in verdicts, f"no site reads {kind!r}: the classifier or the census drifted"


def test_every_listed_exemption_still_names_a_real_call():
    """A listed site that no longer exists is an allowance waiting for the next call to use it."""
    guarded = guarded_axes()
    sites = src_sites()
    for listing, kind in (
        (INTERACTIVE, "interactive"),
        (REACQUIRES, "reacquires"),
        (DECIDED, "decided"),
    ):
        for where in listing:
            used = [s for s in sites if (s.path, s.qualname) == where]
            assert used, f"{where} is listed but acquires nothing now: drop it"
            assert any(verdict(s, guarded) == kind for s in used), (where, kind)


# ── the direct resolutions ─────────────────────────────────────────────────────────────────


def test_every_direct_model_resolution_is_metered_or_is_a_person_s_surface():
    guarded = guarded_axes()
    sites = src_sites(_resolves)
    unaccounted = [
        f"{s.path}:{s.line} in {s.qualname}" for s in sites if not resolution_verdict(s, guarded)
    ]
    assert not unaccounted, (
        "these calls resolve a model on no metered axis and call it, so the daily cap never counts "
        "what they spend. Resolve it with `resolve_metered_model` (the image reader with "
        "`metered=True`), or, only for a surface a person is working in, add it to "
        "UNMETERED_RESOLUTIONS with the reason:\n  " + "\n  ".join(unaccounted)
    )
    listed = Counter(
        (s.path, s.qualname) for s in sites if resolution_verdict(s, guarded) == "listed"
    )
    doubled = {where: n for where, n in listed.items() if n > 1}
    assert not doubled, doubled


def test_the_resolution_census_is_not_vacuous():
    """The tree holds both kinds a direct resolution may be. None resolves a guarded axis through
    the general resolver any more: every call automation makes is resolved as the model itself
    (``resolve_metered_model``), which never builds an agent CLI. That verdict's classifier is
    still read, by the snippets below."""
    guarded = guarded_axes()
    verdicts = [resolution_verdict(s, guarded) for s in src_sites(_resolves)]
    assert len(verdicts) >= 8, len(verdicts)
    for kind in ("metered", "listed"):
        assert kind in verdicts, f"no resolution reads {kind!r}: the classifier or census drifted"


def test_every_listed_unmetered_resolution_still_names_a_real_call():
    guarded = guarded_axes()
    sites = src_sites(_resolves)
    for where in UNMETERED_RESOLUTIONS:
        used = [s for s in sites if (s.path, s.qualname) == where]
        assert used, f"{where} is listed but resolves nothing now: drop it"
        assert any(resolution_verdict(s, guarded) == "listed" for s in used), where


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('resolve_provider_for_use_case("chat")', ""),
        ("resolve_provider_for_use_case(use_case)", ""),
        ('resolve_provider_for_use_case("loops")', "guarded"),
        ('resolve_metered_model("chat")', "metered"),
        ("await resolve_image_reader(metered=False)", ""),
        ("await resolve_image_reader(metered=True)", "metered"),
    ],
    ids=[
        "the chat axis",
        "an axis read at run time",
        "a guarded axis",
        "metered",
        "an image reading left unmetered",
        "a metered image reading",
    ],
)
def test_the_resolution_classifier_on_a_planted_call(snippet: str, expected: str):
    """An axis only known at run time is not known to be metered: it must resolve metered."""
    tree = ast.parse(f"async def index_the_library(use_case):\n    {snippet}\n")
    (site,) = census(tree, "planted.py", _resolves)
    assert resolution_verdict(site, guarded_axes()) == expected


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('await sessions.get_or_create("cron:digest")', ""),
        ('await sessions.get_or_create("cron:digest", model_axis="chat")', ""),
        ('await sessions.get_or_create("cron:digest", model_axis="orchestration")', "guarded"),
    ],
    ids=["no axis", "the chat axis", "a guarded axis"],
)
def test_the_classifier_on_a_planted_call(snippet: str, expected: str):
    """The rail's teeth, on code that is not in the tree: an unnamed axis and the chat axis are
    both unmetered, and only a guarded one passes."""
    tree = ast.parse(f"async def run_the_nightly_digest(sessions):\n    {snippet}\n")
    (site,) = census(tree, "planted.py")
    assert verdict(site, guarded_axes()) == expected


# ── the one session a person answers on a metered axis ─────────────────────────────────────

#: The one call that may acquire a runtime the cap does not count on a metered axis, and the one
#: function that decides it: an Attended loop's planner and workers are answered by a person, so
#: their spend is their owner's, as a chat's is (``loop.posture``). The guard still wraps them.
UNMETERED_DECIDED: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): "spend_metered",
}


def test_only_a_loops_mode_takes_a_session_off_the_cap():
    """Every acquisition that says ``unmetered=`` is the one listed, and it says it through the
    posture function, negated: nothing hands a runtime an unmetered flag of its own."""
    saying = [s for s in src_sites() if any(k.arg == "unmetered" for k in s.call.keywords)]
    assert [(s.path, s.qualname) for s in saying] == list(UNMETERED_DECIDED), saying
    for site in saying:
        value = next(k.value for k in site.call.keywords if k.arg == "unmetered")
        assert isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not), ast.dump(value)
        assert _called(value.operand) == UNMETERED_DECIDED[(site.path, site.qualname)]


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


def test_a_persons_own_chat_axis_is_never_a_metered_one(tmp_path):
    """A chat's turn and the side question beside it take Code & tools when the chat works in a
    folder of its own, and the chat binding otherwise: a person's own turns, which the cap leaves
    alone whichever of the two they run on."""
    from personalclaw.dashboard.chat_runner import model_axis_for
    from personalclaw.dashboard.chat_utils import chat_model_axis

    guarded = guarded_axes()
    folder = tmp_path / "repo"
    folder.mkdir()
    for chat in (
        SimpleNamespace(_app="", workspace_dir=str(folder)),
        SimpleNamespace(_app="slack", workspace_dir=str(folder)),
        SimpleNamespace(_app="", workspace_dir=""),
    ):
        assert chat_model_axis(chat) in ("", "code_tools")
        assert chat_model_axis(chat) not in guarded
        assert model_axis_for(chat) == chat_model_axis(chat)
    assert (
        chat_model_axis(SimpleNamespace(workspace_dir=str(folder))) == "code_tools"
    ), "vacuity: no chat took Code & tools"


# ── a finished subagent's announcement ─────────────────────────────────────────────────────


def test_an_announcement_is_metered_unless_its_parent_is_a_channel_thread():
    """🔴 Red before the fix: every announcement outside a dashboard chat took the chat binding, so
    a subagent a webhook's turn or an app's run spawned was metered, and its report back to that
    parent was not. Only a channel thread's announcement is a person's conversation."""
    from personalclaw.gateway import announce_axis

    assert announce_axis("C0123456789") == ""
    assert announce_axis(None) == "orchestration"
    assert announce_axis("") == "orchestration"
    assert announce_axis(None) in guarded_axes()


# ── a subagent given a model ───────────────────────────────────────────────────────────────

NAMED, NAMED_REF = "named-oai", "named-oai:gpt-4o"
HEAD, HEAD_REF = "head-oai", "head-oai:gpt-4o-mini"
TOKENS_IN, TOKENS_OUT = 100_000, 10_000


def _price(model: str) -> float:
    """What the shipped table's row for *model* bills the call's tokens at."""
    from personalclaw.pricing import price_row

    row = price_row(model)
    assert row is not None, f"premise: the shipped table prices {model}"
    return round((TOKENS_IN * row.fields["in"] + TOKENS_OUT * row.fields["out"]) / 1e6, 6)


class _Scripted:
    #: It uses tools, as a subagent's model must: one that can't is refused before it runs.
    supports_tools = True

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

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


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
async def test_the_daily_dollar_cap_refuses_a_named_spawns_next_call_once_the_day_is_spent(calls):
    """The cap as the week drives it: set a small daily ceiling, let one spawn that names a model
    spend past it, and its next call is refused, saying which ceiling stopped it. A next spawn is
    not refused before its model is known: its paid calls are, where they are made, and one on a
    model that costs nothing runs."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.guardrails.failure import BudgetExceededError
    from personalclaw.subagent import SubagentManager

    _daily_dollar_cap(0.01)
    runtime = await _named_model_runtime(model_axis="orchestration")
    # $0.35 of gpt-4o against a $0.01 ceiling: allowed, as the model's first call today, when
    # nothing knew yet what one costs, and charged.
    await _turn(runtime)

    with pytest.raises(BudgetExceededError) as refused:
        await _turn(runtime)
    assert refused.value.sentence() == (
        f"The daily dollar budget is spent (${_price('gpt-4o'):.2f} of $0.01): raise Max "
        "dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at "
        "midnight."
    )

    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        spawn = manager.spawn("Name a colour.", parent_session_key="dashboard:p", model=NAMED_REF)
        assert spawn is not None and "budget" not in (spawn.error or ""), spawn.error
        await manager._tasks[spawn.id]


# ── a knowledge node, a loop's judge and a one-shot call, whatever the axis ────────────────


def _bound(monkeypatch, **chains: list[str]) -> None:
    """What Settings → Models binds, axis by axis, for this test."""
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: dict(chains))


@pytest.mark.asyncio
async def test_a_video_is_consolidated_by_the_chat_model_and_the_call_is_metered(
    calls, monkeypatch
):
    """🔴 Red before the fix: a knowledge node on the chat axis was handed the native agent, which
    has no `complete()`, so a video's consolidation always fell back to the raw transcript, and
    none of a node's calls counted against the daily cap."""
    from personalclaw.guardrails.budgets import get_meter
    from personalclaw.knowledge.pipeline.nodes.media_nodes import VideoConsolidateNode
    from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

    _bound(monkeypatch, chat=[HEAD_REF])
    transcript = NodeOutput(node_type="transcription", text="a heron lands on the pier")
    out = await VideoConsolidateNode().run(
        {"transcription": transcript}, NodeContext(item_id="v1", item_type="video")
    )

    assert out.text == "blue", f"the raw signals came back instead: {out.text!r}"
    assert calls == [HEAD]
    assert get_meter().day_totals().dollars == pytest.approx(_price("gpt-4o-mini"))


@pytest.mark.asyncio
async def test_a_knowledge_node_walking_its_chain_is_metered(calls, monkeypatch):
    from personalclaw.guardrails.budgets import get_meter
    from personalclaw.knowledge.pipeline.nodes._llm import complete_text

    _bound(monkeypatch, chat=[HEAD_REF, NAMED_REF])
    assert await complete_text("chat", "Describe the video.") == "blue"
    assert calls == [HEAD]
    assert get_meter().day_totals().dollars == pytest.approx(_price("gpt-4o-mini"))


@pytest.mark.asyncio
async def test_a_loop_judge_bound_to_chat_is_the_model_itself_and_is_metered(calls, monkeypatch):
    """🔴 Red before the fix: a judge the owner put on Chat or Code was handed the native agent,
    with tools, and its calls counted nowhere."""
    from personalclaw.guardrails.budgets import get_meter
    from personalclaw.loop import gates

    _bound(monkeypatch, chat=[HEAD_REF])
    monkeypatch.setattr("personalclaw.loop.judge.judge_use_case", lambda: "chat")

    assert await gates.judge_verdict("Is the task done?", loop_id="abcd1234") == "blue"
    assert calls == [HEAD]
    assert get_meter().day_totals().dollars == pytest.approx(_price("gpt-4o-mini"))


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

    assert meter.day_totals().dollars == pytest.approx(_price("gpt-4o"))
    assert run_charges == [("dashboard:p", pytest.approx(info.cost_usd))]


def test_a_child_s_completion_charges_the_fan_out_and_never_the_day():
    """The day is charged where each call is made. A completion that charged it too counted an
    agent CLI's child twice now that its own turns are metered (below)."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.guardrails.budgets import Budget, get_meter
    from personalclaw.subagent import SubagentInfo, SubagentManager

    manager = SubagentManager(sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder())
    child = SubagentInfo(id="c1", task="t", agent="", parent_session_key="dashboard:p")
    child.input_tokens, child.output_tokens, child.cost_usd = 1_000, 100, 0.25
    with (
        patch("personalclaw.subagent.sel"),
        patch(
            "personalclaw.guardrails.budgets.run_budget_from_config",
            lambda: Budget(max_tokens=10**9),
        ),
    ):
        manager._charge_child_and_check_budget(child)
    assert get_meter().day_totals().dollars == 0.0
    assert get_meter().run_totals("dashboard:p").dollars == pytest.approx(0.25)


# ── an agent CLI's own turns ───────────────────────────────────────────────────────────────

CLI_MODEL = "gpt-4o"


class _CliClient:
    """What an ACP client streams for one turn of a scripted agent CLI, and what it was sent."""

    _model = CLI_MODEL
    _agent = ""
    _session_id = ""
    resumed = False

    def __init__(self, *, cost_usd: float = 0.0) -> None:
        self.sent: list[str] = []
        self.cost_usd = cost_usd

    async def stream_events(self, message: str):
        from personalclaw.acp.types import AcpEvent

        self.sent.append(message)
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AcpEvent(
            kind=EVENT_COMPLETE,
            input_tokens=TOKENS_IN,
            output_tokens=TOKENS_OUT,
            cost_usd=self.cost_usd,
        )


def _agent_cli(client: _CliClient):
    """The real ACP provider, its process never spawned: *client* stands in for the connection."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    provider = AcpAgentProvider(
        command=["scripted-cli"], runtime_id="acp:scripted-cli", model=CLI_MODEL
    )
    provider._client = client
    return provider


async def _cli_turn(provider) -> list[LLMEvent]:
    return [event async for event in provider.stream("Tidy the notes.")]


def _audited(audit_id: str) -> list[dict[str, Any]]:
    import json

    from personalclaw.config.loader import config_dir

    path = config_dir() / "model_calls.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [row for row in rows if row.get("audit_id") == audit_id]


@pytest.mark.asyncio
async def test_an_agent_cli_s_turn_on_a_metered_axis_is_charged_audited_and_named():
    """🔴 Red before the fix: an agent CLI makes its own model calls, which no guard wraps, so a
    loop, a subagent or a webhook on one spent money the daily cap never counted."""
    from personalclaw.guardrails.budgets import get_meter

    provider = _agent_cli(_CliClient())
    provider.set_spend_axis("orchestration")
    events = await _cli_turn(provider)

    assert get_meter().day_totals().dollars == pytest.approx(_price(CLI_MODEL))
    assert get_meter().day_totals().tokens == TOKENS_IN + TOKENS_OUT
    (complete,) = [e for e in events if e.kind == EVENT_COMPLETE]
    (audit_id,) = complete.audit_ids
    (row,) = _audited(audit_id)
    assert (row["use_case"], row["provider"], row["model"]) == (
        "orchestration",
        "acp:scripted-cli",
        CLI_MODEL,
    )
    assert row["estimated"] is True, "priced from its tokens: the CLI reported no cost"


@pytest.mark.asyncio
async def test_an_agent_cli_s_reported_cost_is_what_is_charged():
    from personalclaw.guardrails.budgets import get_meter

    provider = _agent_cli(_CliClient(cost_usd=0.42))
    provider.set_spend_axis("loops")
    await _cli_turn(provider)

    assert get_meter().day_totals().dollars == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_an_agent_cli_s_turn_is_refused_before_it_is_sent_once_the_day_is_spent():
    from personalclaw.guardrails.failure import BudgetExceededError

    _daily_dollar_cap(0.01)
    client = _CliClient()
    provider = _agent_cli(client)
    provider.set_spend_axis("orchestration")
    await _cli_turn(provider)  # $0.35 of gpt-4o against a $0.01 ceiling: allowed, and charged

    with pytest.raises(BudgetExceededError) as refused:
        await _cli_turn(provider)
    assert client.sent == ["Tidy the notes."], "the refused turn's prompt was never sent"
    assert "daily dollar budget is spent" in refused.value.sentence()


@pytest.mark.asyncio
async def test_a_person_s_turn_on_an_agent_cli_is_left_alone():
    """The control: on no metered axis (a person's chat) the turn is not charged, as the chat
    binding is not, so the charges above are the axis's doing."""
    from personalclaw.guardrails.budgets import get_meter

    provider = _agent_cli(_CliClient())
    provider.set_spend_axis("chat")
    events = await _cli_turn(provider)

    assert provider.spend_axis == ""
    assert get_meter().day_totals().dollars == 0.0
    (complete,) = [e for e in events if e.kind == EVENT_COMPLETE]
    assert not complete.audit_ids


@pytest.mark.asyncio
async def test_the_session_manager_hands_an_agent_cli_the_axis_it_was_acquired_on(tmp_path):
    """Every acquisition names its axis; the manager passes it to a runtime that makes its own
    model calls, so the rail above covers agent CLIs too."""
    from personalclaw.config import AppConfig
    from personalclaw.session import SessionManager

    built: list[Any] = []

    def factory(_key: Any = None, **_kw: Any):
        provider = _agent_cli(_CliClient())
        provider.start = AsyncMock()
        built.append(provider)
        return provider

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    await sessions.get_or_create("hook:axis", model_axis="orchestration")
    await sessions.get_or_create("dashboard:axis")
    assert [p.spend_axis for p in built] == ["orchestration", ""]


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

        # A native runtime's shape: its tool grants can be held (`_run_hook_inner`).
        return SimpleNamespace(stream=_stream, set_tool_grants=lambda _denial: None), False, False

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

    assert state.sessions.asked == [
        {"agent": None, "model_axis": "orchestration", "unattended": True}
    ]
    assert audit == ["refused_budget_exceeded"]
    assert notes == [
        "Hook agent stopped: The daily dollar budget is spent ($5.25 of $5.00): raise Max "
        "dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at "
        "midnight."
    ]
