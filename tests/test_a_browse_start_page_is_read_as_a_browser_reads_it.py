"""A browse start page is read as a browser reads it, however its arguments are written.

A page that names a secret where the address browse shows keeps it (its scheme, host or port), or
that pipes one, is refused at save, at dispatch and at each fire, read as a browser reads it:
tabs and line breaks dropped, a backslash as a slash, a scheme and any slashes as an authority. A
value that moves the address into another host is refused before anything opens, and so is a pipe
on a secret a run input holds. A step's arguments are one mapping, however they are written, and
none of them is named by a binding.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import test_browse_is_never_handed_a_secrets_value as never
from test_browse_is_never_handed_a_secrets_value import (
    INDEX_URL,
    NAME,
    REFERENCE,
    VALUE,
    _provider,
    _said,
)

from personalclaw.config import credentials as cred

pytestmark = pytest.mark.security

#: The isolated home and the scripted browser the goal's pins use.
home = never.home
browser = never.browser

HOST = REFERENCE + ".example.test"
#: Start pages a browser reads with the reference in its host, however loosely written.
LENIENT = {
    "with-no-scheme": "//" + HOST + "/docs",
    "a-tab-in-its-slashes": "https:\t//" + HOST + "/docs",
    "a-line-break-in-its-slashes": "https:/\n/" + HOST + "/docs",
    "one-slash": "https:/" + HOST + "/docs",
    "three-slashes": "https:///" + HOST + "/docs",
    "a-backslash": "https:/\\" + HOST + "/docs",
}
LOGIN = "ORCHARD_BROWSE_LOGIN"
#: A secret a numeric pipe reads: its value resolves through the pipe, its reference doesn't.
WAIT = "ORCHARD_BROWSE_WAIT"
#: The piece of the value a twelve-character slice makes.
PIECE = VALUE[:12]
#: Logins whose value ends the login part, as a browser reads it, so what follows is the host.
LOGINS = {"a-slash": "loginhead77/side", "a-backslash": "loginhead77\\side"}


async def _run(step: Any, **inputs: Any) -> Any:
    """A run whose one browse step is written *step* (its ``with``), with *inputs*, each holding a
    reference handed to the run as one."""
    return await _run_config({"with": step}, **inputs)


async def _run_config(config: dict[str, Any], **inputs: Any) -> Any:
    """A run whose one browse step's config, beside its provider, is *config*, with *inputs*."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.workflows import input_secrets, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    _ensure_default_providers_registered()
    spec = {
        "name": "orchard-browse",
        "inputs": {key: {"required": False} for key in inputs},
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "action", "id": "look", "config": {"provider": "browse", **config}}
            ],
        },
    }
    extra = input_secrets.stamp({}, inputs, [NAME, LOGIN, WAIT])
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, extra=extra))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    await controller.run_to_completion(timeout=30)
    return controller


def _saved(start_url: str) -> str:
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    step = {"provider": "browse", "with": {"goal": "read the docs", "start_url": start_url}}
    root = Node.from_dict({"kind": "action", "id": "look", "config": step})
    return " ".join(i.message for i in validate_node_tree(root).issues)


@pytest.mark.parametrize("page", sorted(LENIENT))
def test_a_loosely_written_start_page_is_refused_at_its_save(page):
    """🔴 Red before: the template saved."""
    assert "out of the host" in _saved(LENIENT[page])


@pytest.mark.asyncio
@pytest.mark.parametrize("page", sorted(LENIENT))
async def test_a_loosely_written_start_page_is_refused_at_its_dispatch(home, browser, page):
    """🔴 Red before: browse opened it."""
    controller = await _run({"goal": "read the docs", "start_url": LENIENT[page]})

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the host" in _said(controller)


@pytest.mark.parametrize("page", sorted(LENIENT))
def test_a_loosely_written_start_page_refuses_a_fire(home, page):
    """🔴 Red before: the fire filled it in."""
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    with pytest.raises(UnresolvedSecret, match="a browse start page can't"):
        resolve_for(_provider("browse"), {"goal": "read the docs", "start_url": LENIENT[page]})


@pytest.fixture(params=sorted(LOGINS))
def login(request: pytest.FixtureRequest, home: Path) -> Path:
    cred.save_credential(LOGIN, LOGINS[request.param])
    os.environ.pop(LOGIN, None)
    return home


def _profiles(home: Path) -> list[str]:
    return sorted(p.name for p in home.rglob("*") if "loginhead77" in p.name)


def _kept_anywhere(home: Path, browser: Any) -> list[str]:
    """Where the value, or its twelve-character piece (:data:`PIECE`), is, in any case: a prompt, a
    file under the home but the credential store, or a file's or a folder's name."""
    held: list[str] = []
    for value in (VALUE.lower(), PIECE.lower()):
        held += [f"prompt {i}" for i, sent in enumerate(browser.prompts) if value in sent.lower()]
        for p in home.rglob("*"):
            if value in str(p.relative_to(home)).lower():
                held.append(f"the path {p.relative_to(home)}")
            elif (
                p.is_file() and p.name != ".env" and value in p.read_text(errors="replace").lower()
            ):
                held.append(f"{p.relative_to(home)} holds it")
    return held


@pytest.mark.asyncio
async def test_a_login_whose_value_moves_the_host_is_refused_before_anything_opens(login, browser):
    """A login value that ends the login moves the rest of the address into the host: refused
    before a profile is made for it or a host looked up. 🔴 Red before: browse made a profile
    named after the value and opened it."""
    page = "https://{{secret:" + LOGIN + "}}@orchard.example.test/docs"

    controller = await _run({"goal": "read the docs", "start_url": page})

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the address" in _said(controller)
    assert _profiles(login) == []


def test_a_login_whose_value_moves_the_host_refuses_a_fire(login):
    """🔴 Red before: the fire filled it in."""
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    page = "https://{{secret:" + LOGIN + "}}@orchard.example.test/docs"

    with pytest.raises(UnresolvedSecret, match="out of the address"):
        resolve_for(_provider("browse"), {"goal": "read the docs", "start_url": page})


#: Start pages that pipe a run input holding a reference.
PIPED = {
    "slugged-in-its-query": INDEX_URL + "?k={{inputs.token | slugify}}",
    "sliced-into-its-host": "https://{{inputs.token | slice(0,5)}}.example.test/docs",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("page", sorted(PIPED))
async def test_a_start_page_that_pipes_a_secret_an_input_holds_is_refused(home, browser, page):
    """🔴 Red before: the pipe cut the reference, so nothing saw it, and browse opened the page."""
    controller = await _run({"goal": "read the docs", "start_url": PIPED[page]}, token=REFERENCE)

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the pipe" in _said(controller)
    assert _kept_anywhere(home, browser) == []


#: A step's arguments bound to an input, or written as pairs; each names the secret where browse
#: refuses it.
HELD = {
    "bound-start-page-in-its-host": (
        "{{inputs.args}}",
        {"args": {"goal": "read the docs", "start_url": "https://" + HOST + "/docs"}},
    ),
    "bound-goal": ("{{inputs.args}}", {"args": {"goal": "sign in " + REFERENCE}}),
    "pairs-start-page-in-its-host": (
        [["goal", "read the docs"], ["start_url", "https://" + HOST + "/docs"]],
        {},
    ),
    "pairs-goal": ([["goal", "sign in " + REFERENCE], ["start_url", INDEX_URL]], {}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("held", sorted(HELD))
async def test_a_steps_arguments_are_one_mapping_however_they_are_written(home, browser, held):
    """A binding to a mapping, or a list of pairs, is read as the mapping the dispatch hands
    browse: refused, nothing opened, and no prompt or file holds the value. 🔴 Red before: browse
    opened a page named after the value, or its model read it."""
    step, inputs = HELD[held]

    await _run(step, **inputs)

    assert browser.opened == 0 and browser.prompts == []
    assert _kept_anywhere(home, browser) == []


@pytest.mark.asyncio
async def test_arguments_that_are_no_mapping_are_refused(home, browser):
    """🔴 Red before: the dispatch failed reading them."""
    controller = await _run("{{inputs.args}}", args="just some text")

    assert browser.opened == 0
    assert "aren't a mapping" in _said(controller)


@pytest.mark.asyncio
async def test_an_automations_browse_start_page_is_refused_at_its_save():
    """An automation is refused at its save as each fire refuses it. 🔴 Red before: it saved, and
    every fire failed."""
    from personalclaw.dashboard.handlers.triggers import _action_problem

    action = {"provider": "browse", "config": {"goal": "g", "start_url": "https://" + HOST + "/"}}

    said = await _action_problem(action)

    assert said.startswith("a browse start page can't name a secret in its scheme, host or port")
    assert "take the {{secret:…}} out of the host" in said
    assert VALUE not in said


#: An action each provider that judges its own config refuses, in the order the save asks them.
JUDGED = {
    "send-message": {"via": "carrier pigeon", "text_template": "hi"},
    "bash": {"command": "rm -rf $HOME"},
    "browse": {"goal": "g", "start_url": "https://" + HOST + "/"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(JUDGED))
async def test_an_automations_save_asks_each_provider_that_judges_its_config(provider):
    """Each is refused at its save in that provider's own words, as before one lookup asked
    them."""
    from personalclaw.action_providers import bash_provider, browse_provider, send_message_provider
    from personalclaw.dashboard.handlers.triggers import _action_problem

    own = {"send-message": send_message_provider, "bash": bash_provider, "browse": browse_provider}
    config = JUDGED[provider]

    said = await _action_problem({"provider": provider, "config": config})

    assert said and said == own[provider].config_problem(config), said


# ── a run input's reference inside a holder, and the holder the dispatch picks ───────────────

#: A start page whose host, port or whole address is a run input holding a reference, under each
#: holder shape: (the step's config, its inputs).
BOUND_PAGES = {
    "a-host-under-with": (
        {"with": {"goal": "g", "start_url": "https://{{inputs.token}}.example.test/docs"}},
        {"token": REFERENCE},
    ),
    "a-host-under-config": (
        {"config": {"goal": "g", "start_url": "https://{{inputs.token}}.example.test/docs"}},
        {"token": REFERENCE},
    ),
    "a-host-in-pairs": (
        {"with": [["goal", "g"], ["start_url", "https://{{inputs.token}}.example.test/docs"]]},
        {"token": REFERENCE},
    ),
    "a-port-under-with": (
        {"with": {"goal": "g", "start_url": "https://orchard.example.test:{{inputs.token}}/"}},
        {"token": REFERENCE},
    ),
    "the-whole-page-under-with": (
        {"with": {"goal": "g", "start_url": "{{inputs.token}}"}},
        {"token": "https://" + REFERENCE + ".example.test/"},
    ),
    "an-empty-with-binding-beside-pairs": (
        {
            "with": "{{inputs.args}}",
            "config": [["goal", "g"], ["start_url", "https://" + HOST + "/docs"]],
        },
        {"args": {}},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", sorted(BOUND_PAGES))
async def test_a_start_page_a_run_input_fills_is_refused_however_its_holder_is_written(
    home, browser, bound
):
    """Read from the holder the dispatch picks, with each reference kept: refused before anything
    opens, and the value in no prompt, file or path in any case (a profile named after it
    included). 🔴 Red before: browse opened a page named after the value."""
    config, inputs = BOUND_PAGES[bound]

    controller = await _run_config(config, **inputs)

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the host" in _said(controller), _said(controller)
    assert _kept_anywhere(home, browser) == []


@pytest.mark.asyncio
async def test_a_login_a_run_input_fills_that_moves_the_host_is_refused(login, browser):
    """🔴 Red before: browse made a profile named after the value and opened it."""
    page = "https://{{inputs.who}}@orchard.example.test/docs"

    controller = await _run({"goal": "g", "start_url": page}, who="{{secret:" + LOGIN + "}}")

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the address" in _said(controller)
    assert _profiles(login) == []


#: Holders a save reads besides a mapping under ``with``.
SAVED_HOLDERS = {
    "pairs-under-with": {"with": [["goal", "g"], ["start_url", "https://" + HOST + "/"]]},
    "pairs-under-config-beside-a-with-binding": {
        "with": "{{inputs.args}}",
        "config": [["goal", "g"], ["start_url", "https://" + HOST + "/"]],
    },
}


@pytest.mark.parametrize("held", sorted(SAVED_HOLDERS))
def test_a_start_page_in_either_holder_is_refused_at_its_save(held):
    """🔴 Red before: the template saved, and each run opened it."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    step = {"provider": "browse", **SAVED_HOLDERS[held]}
    root = Node.from_dict({"kind": "action", "id": "look", "config": step})

    said = " ".join(i.message for i in validate_node_tree(root).issues)

    assert "out of the host" in said, said


#: A goal written as pairs that reads a secret: piped, or as written.
PAIRS_GOALS = {
    "sliced": "sign in {{secret:" + NAME + " | slice(0,12)}}",
    "slugged": "sign in {{secret:" + NAME + " | slugify}}",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("goal", sorted(PAIRS_GOALS))
async def test_a_goal_written_as_pairs_that_reads_a_secret_is_refused(home, browser, goal):
    """🔴 Red before: browse's model was sent the pieces the pipe made."""
    step = [["goal", PAIRS_GOALS[goal]], ["start_url", INDEX_URL]]

    controller = await _run(step)

    assert browser.opened == 0 and browser.prompts == []
    assert "browse's goal can't name a secret" in _said(controller), _said(controller)
    assert _kept_anywhere(home, browser) == []


#: Start pages refused whatever names them: one that isn't text, and one naming a secret that
#: isn't an http or https address.
NOT_A_PAGE = {
    "not-text": {"to": "https://orchard.example.test/"},
    "ftp": "ftp://orchard.example.test/" + REFERENCE,
}


@pytest.mark.parametrize("page", sorted(NOT_A_PAGE))
def test_a_start_page_that_is_no_address_is_refused_at_its_save_and_a_fire(home, page):
    """🔴 Red before: it saved and each fire handed it on."""
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    assert "a browse start page" in _saved(NOT_A_PAGE[page])
    with pytest.raises(UnresolvedSecret, match="a browse start page"):
        resolve_for(_provider("browse"), {"goal": "g", "start_url": NOT_A_PAGE[page]})


@pytest.mark.asyncio
@pytest.mark.parametrize("page", sorted(NOT_A_PAGE))
async def test_a_start_page_that_is_no_address_is_refused_at_its_dispatch(home, browser, page):
    """🔴 Red before: browse was handed it."""
    controller = await _run({"goal": "g", "start_url": NOT_A_PAGE[page]})

    assert browser.opened == 0
    assert "a browse start page" in _said(controller)


#: Each start-page refusal a fire makes, as written.
FIRE_REFUSALS = {
    "in-its-host": "https://" + HOST + "/docs",
    "through-a-pipe": INDEX_URL + "?k={{secret:" + NAME + " | slugify}}",
}


@pytest.mark.parametrize("page", sorted(FIRE_REFUSALS))
def test_a_refused_fire_says_why_in_the_start_pages_words(home, page):
    """A lifecycle trigger's record and an automation's say what the start page can't do, never
    that a secret named “” is missing. 🔴 Red before: they said a secret “” could not be found."""
    from personalclaw.hooks import ScriptHook, _hook_config
    from personalclaw.triggers import cannot_run
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    hook = ScriptHook(
        id="h-look",
        name="Look",
        event="SessionStart",
        provider="browse",
        provider_config={"goal": "g", "start_url": FIRE_REFUSALS[page]},
    )
    *_, hook_said = _hook_config(hook, _provider("browse"))
    with pytest.raises(UnresolvedSecret) as refused:
        resolve_for(_provider("browse"), {"goal": "g", "start_url": FIRE_REFUSALS[page]})
    fire_said = cannot_run.missing_secret(refused.value)

    for said in (hook_said, fire_said):
        assert "a browse start page can't" in said and "“”" not in said, said


def test_a_moved_login_refuses_a_fire_in_its_words(login):
    """🔴 Red before: the record said a secret “” could not be found."""
    from personalclaw.triggers import cannot_run
    from personalclaw.triggers.secrets import UnresolvedSecret, resolve_for

    page = "https://{{secret:" + LOGIN + "}}@orchard.example.test/docs"
    with pytest.raises(UnresolvedSecret) as refused:
        resolve_for(_provider("browse"), {"goal": "g", "start_url": page})

    said = cannot_run.missing_secret(refused.value)
    assert "a browse start page can't" in said and "“”" not in said, said


@pytest.mark.parametrize("door", ["create", "update"])
def test_the_chat_and_cli_door_refuses_a_browse_start_page(home, door):
    """🔴 Red before: the chat's and the CLI's door saved it, and every fire refused it."""
    from personalclaw.triggers import tools
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore()
    bad = {
        "inline": {
            "provider": "browse",
            "config": {"goal": "g", "start_url": "https://" + HOST + "/"},
        }
    }
    good = {"inline": {"provider": "browse", "config": {"goal": "g", "start_url": INDEX_URL}}}
    if door == "create":
        result = tools.create(store, name="look", kind="manual", workflow=bad)
    else:
        made = tools.create(store, name="look", kind="manual", workflow=good)
        assert made.ok, made.text
        (trigger,) = store.list_triggers()
        result = tools.update(store, trigger_id=trigger.id, patch={"workflow": bad})

    assert not result.ok and "a browse start page can't" in result.text, result.text


# ── an argument named by a binding, and one read on its own ──────────────────────────────────

#: A step's argument written as a pair whose name a run input fills, reading a secret through a
#: pipe: its goal, and its start page.
BOUND_NAMES = {
    "the-goal": (
        [
            ["{{inputs.k}}", "sign in {{secret:" + NAME + " | slice(0,12)}}"],
            ["start_url", INDEX_URL],
        ],
        {"k": "goal"},
    ),
    "the-start-page": (
        [
            ["goal", "g"],
            ["{{inputs.k}}", "https://{{secret:" + NAME + " | slice(0,12)}}.example.test/docs"],
        ],
        {"k": "start_url"},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("named", sorted(BOUND_NAMES))
async def test_an_argument_named_by_a_binding_is_refused_at_its_dispatch(home, browser, named):
    """Each check reads an argument by its name as written, so one a binding names is refused
    before anything opens. 🔴 Red before: no check read it, so browse's model read the pipe's
    piece, and the start page was opened with it in its host."""
    step, inputs = BOUND_NAMES[named]

    controller = await _run(step, **inputs)

    assert browser.opened == 0 and browser.prompts == []
    assert "can't be named by a binding" in _said(controller), _said(controller)
    assert _kept_anywhere(home, browser) == []


@pytest.mark.parametrize("where", ["with", "config"])
@pytest.mark.parametrize("named", sorted(BOUND_NAMES))
def test_an_argument_named_by_a_binding_is_refused_at_its_save(named, where):
    """Under either holder. 🔴 Red before: the template saved."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    step = {"provider": "browse", where: BOUND_NAMES[named][0]}
    root = Node.from_dict({"kind": "action", "id": "look", "config": step})

    issues = validate_node_tree(root).issues

    assert "WF_ARGUMENT_NAME_BOUND" in [i.code for i in issues], [i.code for i in issues]
    assert "write each argument's name as text" in " ".join(i.message for i in issues)


@pytest.mark.asyncio
async def test_an_argument_named_by_a_binding_under_config_is_refused_at_its_dispatch(
    home, browser
):
    """Beside a `with` bound to an input that resolves empty, the dispatch reads `config`, whose
    pairs name one with a binding. 🔴 Red before: browse opened the page with the pipe's piece."""
    _step, inputs = BOUND_NAMES["the-start-page"]
    config = {"with": "{{inputs.args}}", "config": BOUND_NAMES["the-start-page"][0]}

    controller = await _run_config(config, args={}, **inputs)

    assert browser.opened == 0 and browser.prompts == []
    assert "can't be named by a binding" in _said(controller), _said(controller)
    assert _kept_anywhere(home, browser) == []


def test_an_argument_named_by_a_binding_under_config_beside_a_with_binding_is_refused_at_its_save():
    """🔴 Red before: the template saved."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    step = {"provider": "browse", "with": "{{inputs.args}}", "config": BOUND_NAMES["the-goal"][0]}
    root = Node.from_dict({"kind": "action", "id": "look", "config": step})

    codes = [i.code for i in validate_node_tree(root).issues]

    assert "WF_ARGUMENT_NAME_BOUND" in codes, codes


@pytest.mark.parametrize("key", ["start_url", "goal"])
def test_an_argument_its_reference_cant_fill_is_refused(monkeypatch, key):
    """A start page or a goal that doesn't resolve with each reference kept can't be checked, so
    the step is refused: it fails closed. No step reaches this today, since a pipe on a secret is
    refused first, so the reading is made to fail here. A regression guard for a branch no step
    reaches today."""
    from personalclaw.action_providers.browse_provider import BrowseActionProvider
    from personalclaw.workflows import engine_support
    from personalclaw.workflows.bindings import BindingContext, BindingError
    from personalclaw.workflows.models import Node

    def _unread(*_a: Any, **_k: Any) -> Any:
        raise BindingError("clamp expects a number")

    monkeypatch.setattr(engine_support, "_kept", _unread)
    config = {"provider": "browse", "with": {"goal": "g", "start_url": INDEX_URL}}
    node = Node.from_dict({"kind": "action", "id": "look", "config": config})
    provider, ctx = BrowseActionProvider(), BindingContext()

    if key == "start_url":
        refused = engine_support._start_page_refused(provider, node, config, ctx)
        assert refused is not None and "can't be checked" in refused[0], refused
    else:
        refused = engine_support._model_text_refused(provider, node, config, ctx)
        assert refused is not None and "browse's goal can't name a secret" in refused[0], refused


@pytest.fixture
def waits(home: Path) -> Path:
    """A second secret whose value is a number, which a numeric pipe reads and its reference
    can't be."""
    cred.save_credential(WAIT, "30")
    os.environ.pop(WAIT, None)
    return home


@pytest.mark.asyncio
async def test_a_start_page_is_read_on_its_own_beside_an_argument_its_reference_cant_fill(
    waits, browser
):
    """Another argument a numeric pipe reads through a run input holding a reference resolves
    filled and not with the reference kept; the start page is read on its own, so its host's run
    input still shows the secret it names. 🔴 Red before: the other argument failed the holder's
    kept read, so the start page was read as written, and browse opened a page named after the
    value."""
    step = {
        "goal": "g",
        "start_url": "https://{{inputs.token}}.example.test/docs",
        "wait": "{{inputs.wait | clamp(1, 60)}}",
    }

    controller = await _run(step, token=REFERENCE, wait="{{secret:" + WAIT + "}}")

    assert browser.opened == 0 and browser.prompts == []
    assert "out of the host" in _said(controller), _said(controller)
    assert _kept_anywhere(waits, browser) == []
