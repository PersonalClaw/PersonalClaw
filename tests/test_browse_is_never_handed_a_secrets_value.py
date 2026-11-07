"""Browse is never handed a secret's value for its model to read.

Browse's goal is what its model reads, so a ``{{secret:NAME}}`` there stays the reference, and a
goal that names a secret is refused before anything opens: browse never types a credential, and a
site's sign-in is the owner's. Its start page is filled, to be opened, and every prompt its model
is sent is masked of the value; vision grounding, whose screenshot no mask reaches, is off while
the step holds one.
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

import pytest
from test_browse_loop_and_provider import _Decide, _FakePage, _FakeSession

from personalclaw.config import credentials as cred
from personalclaw.config import loader

pytestmark = pytest.mark.security

NAME = "ORCHARD_BROWSE_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be the VALUE.
VALUE = "bv-9c31f6a0-ORCHARD-BROWSE-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"
INDEX_URL = "https://orchard.example.test/docs"
INDEX_HTML = "<html><body><h1>Docs</h1><p>Version 2.0.</p></body></html>"
REFUSED = "browse's goal can't name a secret"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated home. These tests write a secret, so never the real one."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    # Registered first, so monkeypatch removes whatever a save mirrors in.
    monkeypatch.setenv(NAME, "x")
    monkeypatch.delenv(NAME)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    cred.save_credential(NAME, VALUE)
    os.environ.pop(NAME, None)
    return cfg


class _Browser:
    """Browse's browser and its model, scripted (the browse loop's own fakes): the model keeps
    every prompt and is done, and each time browse opens the browser is counted."""

    def __init__(self) -> None:
        self.page = _FakePage({INDEX_URL: INDEX_HTML}, url=INDEX_URL)
        self.session = _FakeSession(self.page)
        self.model = _Decide("DONE")
        self.opened = 0

    @property
    def prompts(self) -> list[str]:
        return self.model.prompts


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> _Browser:
    from personalclaw.action_providers import browse_provider
    from personalclaw.workflows import controller

    scripted = _Browser()

    async def _opened() -> Any:
        scripted.opened += 1
        return scripted.session, scripted.page, None

    def _open(self: Any, cfg: Any, ctx: Any, *, cdp_url: str = "") -> Any:
        return _opened()

    monkeypatch.setattr(browse_provider, "_decide", scripted.model)
    monkeypatch.setattr(browse_provider.BrowseActionProvider, "_open", _open)
    monkeypatch.setattr(controller, "TICK_WAKE_SECS", 0.02)
    return scripted


async def _browse(
    goal: str,
    start_url: str = INDEX_URL,
    *,
    vision: bool = False,
    holder: str = "with",
    **inputs: Any,
) -> Any:
    """A run whose one step browses toward *goal* from *start_url*, through the real registry,
    with *inputs*, a ``token`` among them handed to the run as a reference."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.workflows import input_secrets, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    _ensure_default_providers_registered()
    step = {"goal": goal, "start_url": start_url, **({"vision_grounding": True} if vision else {})}
    spec = {
        "name": "orchard-browse",
        "inputs": {"token": {"type": "string", "required": False}},
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "look",
                    "config": {
                        "provider": "browse",
                        holder: step,
                    },
                }
            ],
        },
    }
    extra = input_secrets.stamp({}, inputs, [NAME]) if "token" in inputs else {}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, extra=extra))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    await controller.run_to_completion(timeout=30)
    return controller


def _provider(name: str) -> Any:
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    return get_action_provider(name)


def _said(controller: Any) -> str:
    failure = controller.instances["root.children[0]"].failure
    output = controller._outputs.get("look")
    told = f"{failure.cause_plain} {failure.remediation}" if failure else ""
    return f"{told} {output!r}"


def _holders(home: Path) -> list[str]:
    """Every file under the home that holds the value, but the credential store's own."""
    return sorted(
        str(p)
        for p in home.rglob("*")
        if p.is_file() and p.name != ".env" and VALUE.encode() in p.read_bytes()
    )


@pytest.mark.asyncio
async def test_a_browse_goal_that_names_a_secret_is_refused_before_its_model_reads_it(
    home, browser
):
    """🔴 Red before: browse's model read the goal with the value in it."""
    controller = await _browse(f"sign in as {REFERENCE}")

    assert browser.prompts == [], "browse's model is never asked"
    assert browser.opened == 0, "nothing is opened"
    assert REFUSED in _said(controller), _said(controller)
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_secret_input_a_browse_goal_reads_stays_its_reference(home, browser):
    """A run handed a reference in an input, read by browse's goal. 🔴 Red before: browse's model
    read the goal with the value in it."""
    controller = await _browse("sign in as {{inputs.token}}", token=REFERENCE)

    assert browser.prompts == []
    assert REFUSED in _said(controller), _said(controller)
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_browse_goal_with_no_secret_is_browsed(home, browser):
    """A control: a goal that names no secret is pursued."""
    await _browse("read the docs' version")

    assert browser.prompts, "browse's model was asked"
    assert browser.opened == 1


@pytest.mark.asyncio
async def test_the_dispatch_refuses_browse_a_filled_value(home, browser):
    """Whatever chose the step's resolver, browse is never handed a value. 🔴 Red before: it was
    dispatched with it, and its model read it."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import FailureClass, InstanceState, Node, NodeKind

    _ensure_default_providers_registered()
    step = {"goal": f"look up {REFERENCE}", "start_url": INDEX_URL}
    node = Node(kind=NodeKind.ACTION, id="look", config={"provider": "browse", "with": step})
    ctx = BindingContext(secret_resolver=lambda key: VALUE)

    result = await engine.dispatch_action(node, ctx, get_provider=get_action_provider)

    assert result.state == InstanceState.FAILED
    assert result.failure.failure_class == FailureClass.PERMISSION
    assert browser.prompts == [] and browser.opened == 0


# ── its start page: filled, to be opened, and masked out of every prompt ──────────────────────

#: A start page holding the reference in a query parameter, a path segment or its login.
STARTS = {
    "an-unlisted-query-parameter": INDEX_URL + "?k={secret}",
    "a-path-segment": "https://orchard.example.test/docs/{secret}/index",
    "its-userinfo": "https://orchard:{secret}@orchard.example.test/docs",
}


def test_an_automations_fire_keeps_the_goals_reference_and_fills_its_start_page(home):
    """🔴 Red before: the fire filled the goal too, so browse's model read the value."""
    from personalclaw.triggers.secrets import resolve_for

    written = {"goal": f"read the docs for {REFERENCE}", "start_url": f"{INDEX_URL}?k={REFERENCE}"}

    handed, filled = resolve_for(_provider("browse"), written)

    assert handed == {"goal": written["goal"], "start_url": f"{INDEX_URL}?k={VALUE}"}
    assert filled == (VALUE,)


class _Model:
    """Browse's model, behind the model-call guard as `_decide` reaches it: it keeps what it is
    sent, which is what passed the guard."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def stream(self, message: str) -> Any:
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        self.sent.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="DONE")
        yield LLMEvent(kind=EVENT_COMPLETE)


def _opening(monkeypatch: pytest.MonkeyPatch, session: Any, page: Any, decide: Any) -> None:
    from personalclaw.action_providers import browse_provider
    from personalclaw.workflows import controller

    async def _opened() -> Any:
        return session, page, None

    def _open(self: Any, cfg: Any, ctx: Any, *, cdp_url: str = "") -> Any:
        return _opened()

    monkeypatch.setattr(browse_provider, "_decide", decide)
    monkeypatch.setattr(browse_provider.BrowseActionProvider, "_open", _open)
    monkeypatch.setattr(controller, "TICK_WAKE_SECS", 0.02)


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(STARTS))
async def test_a_start_page_naming_a_secret_is_opened_with_it_and_no_prompt_holds_it(
    home, monkeypatch, where
):
    """The page browse opens, which shows its own address, is the filled one; its model is sent
    every prompt masked of the value. 🔴 Red before: the start page kept the reference, or a
    prompt held the value."""
    from personalclaw.guardrails.model_call import ModelCallGuard

    opened = STARTS[where].format(secret=VALUE)
    shown = f"<html><body><h1>Docs</h1><p>You are at {opened}</p></body></html>"
    page = _FakePage({opened: shown}, url="")
    model = _Model()
    guard = ModelCallGuard(model, use_case="browse", provider_name="orchard", model="m")

    async def _decide(prompt: str) -> str:
        async for _ in guard.stream(prompt):
            pass
        return "DONE"

    _opening(monkeypatch, _FakeSession(page), page, _decide)

    await _browse("read the docs' version", start_url=STARTS[where].format(secret=REFERENCE))

    assert page.url == opened, "browse opened the page the value names"
    assert model.sent, "browse's model was asked"
    assert all(VALUE not in sent for sent in model.sent), "a prompt held the value"


# ── vision grounding: off while the step holds a value ────────────────────────────────────────


def _record(controller: Any) -> str:
    from personalclaw.workflows import store

    files = [p for p in store.run_dir(controller.run.id).rglob("*") if p.is_file()]
    return "".join(p.read_text(errors="replace") for p in files)


@pytest.mark.asyncio
async def test_vision_grounding_is_off_while_the_step_holds_a_secrets_value(
    home, monkeypatch, tmp_path
):
    """A screenshot can show what no mask reaches, so a step handed a value sends none: it parks,
    saying why. 🔴 Red before: the screenshot was sent to the vision model."""
    from test_browse_vision_grounding import CANVAS_PAGE, _bind_vision, _decider, _Page, _shot

    from personalclaw.browse import vision

    seen = _bind_vision(monkeypatch, "POINT 0.5 0.25")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    blank = _FakeSession(_FakePage({}, url=""))
    _opening(monkeypatch, blank, page, _decider("CLICK_VISION the seat"))

    controller = await _browse("book a seat", start_url=f"{INDEX_URL}?k={REFERENCE}", vision=True)

    assert seen == [], "no image is sent"
    assert page.coordinate_clicks == []
    assert vision.REASON_HOLDS_A_SECRET in _record(controller), "the park says why"
    assert vision.REASON_NO_VISION_MODEL not in _record(controller)


@pytest.mark.asyncio
async def test_vision_grounding_still_grounds_a_step_that_holds_no_value(
    home, monkeypatch, tmp_path
):
    """A control: the same step, its start page naming no secret, grounds and clicks."""
    from test_browse_vision_grounding import CANVAS_PAGE, _bind_vision, _decider, _Page, _shot

    seen = _bind_vision(monkeypatch, "POINT 0.5 0.25")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    blank = _FakeSession(_FakePage({}, url=""))
    _opening(monkeypatch, blank, page, _decider("CLICK_VISION the seat"))

    await _browse("book a seat", vision=True)

    assert len(seen) == 1


# ── either holder, and a pipe ─────────────────────────────────────────────────────────────────

#: A goal naming the secret, directly or through a pipe.
GOALS = {
    "directly": f"sign in as {REFERENCE}",
    "through-a-pipe": "sign in as {{secret:" + NAME + " | slugify}}",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["with", "config"])
@pytest.mark.parametrize("written", sorted(GOALS))
async def test_a_goal_that_names_a_secret_is_refused_under_either_holder(
    home, browser, holder, written
):
    """However the step holds its arguments and however its goal reads the secret. 🔴 Red before:
    browse's model read the goal, the value in it or the reference piped out of shape."""
    controller = await _browse(GOALS[written], holder=holder)

    assert browser.prompts == [], "browse's model is never asked"
    assert REFUSED in _said(controller), _said(controller)
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_the_dispatch_refuses_browse_a_filled_value_under_config(home, browser):
    """🔴 Red before: it was dispatched with the value in its goal."""
    from personalclaw.action_providers.registry import get_action_provider
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import FailureClass, InstanceState, Node, NodeKind

    _provider("browse")
    step = {"goal": f"look up {REFERENCE}", "start_url": INDEX_URL}
    node = Node(kind=NodeKind.ACTION, id="look", config={"provider": "browse", "config": step})
    ctx = BindingContext(secret_resolver=lambda key: VALUE)

    result = await engine.dispatch_action(node, ctx, get_provider=get_action_provider)

    assert result.state == InstanceState.FAILED
    assert result.failure.failure_class == FailureClass.PERMISSION
    assert browser.prompts == [] and browser.opened == 0


# ── while the step holds a value, every address browse shows is its origin ─────────────────────

#: A stored value with the characters an address writes differently.
#: Its pieces are unlike any other word here, so a piece of it found anywhere is the value's.
ODD = "bv 7q1x/z9k2&WQXZT=PLMKV?JHGRD+y"
ODD_NAME = "ORCHARD_BROWSE_ODD"
ODD_REFERENCE = "{{secret:" + ODD_NAME + "}}"
ORIGIN = "https://orchard.example.test/"


def _forms(value: str) -> list[str]:
    import html
    import re
    from urllib.parse import quote, quote_plus

    twice = quote(quote(value, safe=""), safe="")
    whole = {
        value,
        quote(value),
        quote(value, safe=""),
        quote_plus(value),
        html.escape(value),
        twice,
    }
    whole |= {ODD_IN_A_QUERY} if value == ODD else set()
    return sorted(whole | {piece for piece in re.split(r"[^A-Za-z0-9]+", value) if len(piece) >= 4})


#: :data:`ODD` as a browser writes it into an address's query, recorded from
#: ``new URL("https://orchard.example.test/docs?k=" + ODD).href`` (the WHATWG URL Standard).
ODD_IN_A_QUERY = "bv%207q1x/z9k2&WQXZT=PLMKV?JHGRD+y"


def _shown(home: Path, model: _Model) -> list[str]:
    """Where a form of the value appears: a prompt, or a file under the home but the credential
    store (the run's outputs and the security event log among them)."""
    held = [
        f"prompt {i}" for i, sent in enumerate(model.sent) if any(f in sent for f in _forms(ODD))
    ]
    for p in home.rglob("*"):
        if p.is_file() and p.name != ".env":
            text = p.read_text(errors="replace")
            held += [f"{p.name} holds {f!r}" for f in _forms(ODD) if f in text]
    return held


#: Each start page, and where browse lands: (written, landed on, the page there says).
ADDRESSES = {
    "beside-a-listed-key": (INDEX_URL + "?token=" + ODD_REFERENCE + "&page=2", None, ""),
    "across-a-200-character-cut": (INDEX_URL + "/" + "p" * 150 + "?k=" + ODD_REFERENCE, None, ""),
    "in-a-path": (INDEX_URL + "/" + ODD_REFERENCE + "/index", None, ""),
    "echoed-as-a-browser-writes-it-by-its-page": (
        INDEX_URL + "?k=" + ODD_REFERENCE,
        None,
        "you searched for ECHOED",
    ),
    "carried-on-by-a-redirect": (
        INDEX_URL + "?k=" + ODD_REFERENCE,
        "https://login.example.test/?return=VALUE_TWICE_ENCODED",
        "",
    ),
}


class _Landing(_FakePage):
    """A page that, once opened, is somewhere else: where a redirect took it."""

    def __init__(self, pages: dict[str, str], landing: str | None) -> None:
        super().__init__(pages, url="")
        self._landing = landing

    async def current_url(self) -> str:
        return self._landing or self.url


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(ADDRESSES))
async def test_a_step_holding_a_value_shows_every_address_as_its_origin(home, monkeypatch, where):
    """No prompt, output or security event holds the value in any form: browse shows each address
    as its origin alone. 🔴 Red before: the address, or the page's echo of it, carried the value
    on."""
    from urllib.parse import quote

    from personalclaw.guardrails.model_call import ModelCallGuard

    cred.save_credential(ODD_NAME, ODD)
    os.environ.pop(ODD_NAME, None)
    written, landing, says = ADDRESSES[where]
    opened = written.replace(ODD_REFERENCE, ODD)
    twice = quote(quote(ODD, safe=""), safe="")
    here = landing.replace("VALUE_TWICE_ENCODED", twice) if landing else opened
    # The page echoes the value as a browser wrote it into its query, in its text and in no
    # address, so only the mask's browser form keeps it out (`known_value_forms`).
    text = says.replace("ECHOED", ODD_IN_A_QUERY)
    shown = html.escape(text or "Version 2.0", quote=False)  # as the page writes its own text
    page = _FakePage({here: f"<html><body><h1>Docs</h1><p>{shown}</p></body></html>"}, url="")
    model = _Model()
    guard = ModelCallGuard(model, use_case="browse", provider_name="orchard", model="m")

    async def _decide(prompt: str) -> str:
        async for _ in guard.stream(prompt):
            pass
        return "DONE"

    _opening(monkeypatch, _FakeSession(page), page, _decide)

    await _browse("read the docs' version", start_url=written)

    assert model.sent, "browse's model was asked"
    assert _shown(home, model) == []


# ── a start page that names a secret where its origin shows it, or pipes one ───────────────────

#: Start pages refused, and the words that say why.
REFUSED_PAGES = {
    "in-its-host": ("https://" + REFERENCE + ".example.test/docs", "out of the host"),
    "in-its-port": ("https://orchard.example.test:" + REFERENCE + "/docs", "out of the host"),
    "through-a-pipe": (INDEX_URL + "?k={{secret:" + NAME + " | slugify}}", "out of the pipe"),
}


@pytest.mark.parametrize("page", sorted(REFUSED_PAGES))
def test_a_start_page_naming_a_secret_where_its_origin_shows_is_refused_at_its_save(page):
    """🔴 Red before: the template saved."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    written, fix = REFUSED_PAGES[page]
    step = {"provider": "browse", "with": {"goal": "read the docs", "start_url": written}}
    root = Node.from_dict({"kind": "action", "id": "look", "config": step})

    said = " ".join(i.message for i in validate_node_tree(root).issues)

    assert fix in said, said


@pytest.mark.asyncio
@pytest.mark.parametrize("page", sorted(REFUSED_PAGES))
async def test_a_start_page_naming_a_secret_where_its_origin_shows_is_refused_at_its_dispatch(
    home, browser, page
):
    """🔴 Red before: browse opened it."""
    written, fix = REFUSED_PAGES[page]

    controller = await _browse("read the docs", start_url=written)

    assert browser.opened == 0 and browser.prompts == []
    assert fix in _said(controller), _said(controller)


@pytest.mark.parametrize("page", sorted(REFUSED_PAGES))
def test_a_start_page_naming_a_secret_where_its_origin_shows_refuses_a_fire(home, page):
    """🔴 Red before: the fire filled it in."""
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    written, fix = REFUSED_PAGES[page]

    with pytest.raises(UnresolvedSecret, match=fix):
        resolve_for(_provider("browse"), {"goal": "read the docs", "start_url": written})


@pytest.mark.asyncio
async def test_a_run_that_held_a_value_leaves_its_tab_with_no_history():
    """🔴 Red before: the tab kept every address it went to."""
    from personalclaw import filled_secrets
    from personalclaw.action_providers.browse_provider import forget_where_it_went

    left: list[bool] = []

    class _Tab:
        async def leave_blank(self) -> None:
            left.append(True)

    await forget_where_it_went(_Tab())
    assert left == [], "a run that held nothing keeps its tab as it is"
    with filled_secrets.handed([VALUE]):
        await forget_where_it_went(_Tab())
    assert left == [True]


#: A stored value of words, so a cut at a space can fall inside it.
WORDY = "7q1x z9k2 WQXZT PLMKV JHGRD"
WORDY_NAME = "ORCHARD_BROWSE_WORDY"


@pytest.mark.asyncio
async def test_a_page_that_shows_the_value_across_its_cut_is_masked_before_it(home, monkeypatch):
    """A page's text is cut to what browse's model reads: a value it shows is masked first, so no
    piece of it is left wherever the cut falls. 🔴 Red before: the piece before the cut reached a
    prompt."""
    import re

    from personalclaw.guardrails.model_call import ModelCallGuard

    cred.save_credential(WORDY_NAME, WORDY)
    os.environ.pop(WORDY_NAME, None)
    written = INDEX_URL + "?k={{secret:" + WORDY_NAME + "}}"
    opened = written.replace("{{secret:" + WORDY_NAME + "}}", WORDY)
    shown = "the beds hold " + " ".join([WORDY] * 400)
    page = _Landing({opened: f"<html><body><p>{shown}</p></body></html>"}, None)
    model = _Model()
    guard = ModelCallGuard(model, use_case="browse", provider_name="orchard", model="m")

    async def _decide(prompt: str) -> str:
        async for _ in guard.stream(prompt):
            pass
        return "DONE"

    _opening(monkeypatch, _FakeSession(page), page, _decide)

    await _browse("read the docs' version", start_url=written)

    pieces = [piece for piece in re.split(r"\s+", WORDY)]
    assert model.sent, "browse's model was asked"
    assert [p for p in pieces if any(p in sent for sent in model.sent)] == []
