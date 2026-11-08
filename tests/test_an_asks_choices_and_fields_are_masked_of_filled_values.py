"""A step's question is masked of every value its bindings filled in, all of it.

An approval gate's ask is kept on the run's record and shown to whoever answers it: its prompt, its
choices and its fields. A secret's value filled into any of them is masked there, as it is in the
step's output and its failure (``engine_support.masked``). A field's name and type are what the
form is, so a secret there is refused, and so is a secret piped in what the ask shows, at save and
at dispatch (``engine_support.ask_refused``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from personalclaw.config import credentials as cred
from personalclaw.config import loader

pytestmark = pytest.mark.security

NAME = "ORCHARD_ASK_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be the VALUE.
VALUE = "av-6e08b3d1-ORCHARD-ASK-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"


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


ASKS = {
    "its-choices": {
        "ask_kind": "choice",
        "prompt": f"approve the beds with {REFERENCE}",
        "choices": [f"use {REFERENCE}", "skip"],
    },
    "its-fields": {
        "ask_kind": "form",
        "fields": [
            {
                "name": "token",
                "type": "choice",
                "label": f"the token, {REFERENCE}",
                "choices": [REFERENCE, "none"],
                "default": REFERENCE,
            }
        ],
    },
}


def _spec(ask: dict[str, Any], gate_id: str = "ok") -> dict[str, Any]:
    gate: dict[str, Any] = {"kind": "approval", "prompt": "approve the beds", **ask}
    return {
        "name": "orchard-ask",
        "inputs": {"token": {"required": False}},
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "gate", "id": gate_id, "config": gate}],
        },
    }


async def _parked(
    ask: dict[str, Any], gate_id: str = "ok", completion: Any = None, **inputs: Any
) -> Any:
    """A run of an approval gate asking *ask*, parked on its question (the wait ends at its
    timeout), with *inputs*, each holding a reference handed to the run as one, and *completion*
    as its model."""
    from personalclaw.workflows import input_secrets, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    spec = _spec(ask, gate_id)
    spec["inputs"] |= {key: {"required": False} for key in inputs}
    extra = input_secrets.stamp({}, inputs, [NAME])
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, extra=extra))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(completion=completion))
    await controller.run_to_completion(timeout=3)
    return controller


@pytest.mark.asyncio
@pytest.mark.parametrize("ask", sorted(ASKS))
async def test_an_asks_choices_and_fields_are_masked_of_filled_values(home, ask):
    """What the run keeps of its question, and what its status shows a model, hold the mask.
    🔴 Red before: the run's state, its continuation and its status held the value."""
    from personalclaw import filled_secrets
    from personalclaw.workflows import service, store
    from personalclaw.workflows.human_input import list_continuations
    from personalclaw.workflows.models import RunStatus

    controller = await _parked(ASKS[ask])
    run = store.get(controller.run.id)
    mask = filled_secrets.masked(VALUE, [VALUE])

    assert run.status == RunStatus.NEEDS_INPUT, run.status
    files = [p for p in store.run_dir(run.id).rglob("*") if p.is_file()]
    assert any(p.name == "state.json" for p in files), "vacuity: the run kept its state"
    assert [str(p) for p in files if VALUE.encode() in p.read_bytes()] == []
    asks = json.dumps([c.ask for c in list_continuations(run.id)], default=str)
    assert mask in asks and VALUE not in asks, "what a blocking start hands its caller"
    assert VALUE not in json.dumps(service.status(run.id), default=str), "what its status shows"


@pytest.mark.asyncio
async def test_a_short_value_masks_only_what_an_ask_shows(home):
    """A one-character secret masks every place its text appears, so only what the ask shows is
    masked: its kind, its node and each field's name and type are what they are. 🔴 Red before:
    the choices held the value; masking the whole ask instead turns a `choice` into an
    approval."""
    from personalclaw.workflows import store
    from personalclaw.workflows.human_input import list_continuations

    cred.save_credential(NAME, "c")
    os.environ.pop(NAME, None)
    field = ASKS["its-fields"]["fields"][0] | {"name": "code"}
    ask = {"ask_kind": "choice", "choices": [REFERENCE, "x"], "fields": [field]}
    controller = await _parked(ask, gate_id="check")
    run = store.get(controller.run.id)

    (asked,) = [c.ask for c in list_continuations(run.id)]
    assert asked["kind"] == "choice", asked
    assert asked["node_id"] == "check", asked
    assert asked["fields"][0]["name"] == "code" and asked["fields"][0]["type"] == "choice"
    assert "c" not in [str(x) for x in asked["choices"]]


# ── what the form is, and what the ask shows a pipe making ────────────────────────────────────

#: A field whose name or type names a secret, as written or through a run input that holds one.
WHAT_THE_FORM_IS = {
    "its-name": ({"name": REFERENCE, "type": "text"}, {}),
    "its-type": (
        {
            "name": "token",
            "type": "{{secret:" + NAME + " | slice(0,14)}}{{secret:" + NAME + " | slice(14,99)}}",
        },
        {},
    ),
    "its-name-slugged": ({"name": "{{secret:" + NAME + " | slugify}}", "type": "text"}, {}),
    "its-name-through-an-input-in-two-pieces": (
        {"name": "{{inputs.token | slice(0,14)}}{{inputs.token | slice(14,99)}}", "type": "text"},
        {"token": REFERENCE},
    ),
    "its-name-through-an-input": (
        {"name": "{{inputs.token}}", "type": "text"},
        {"token": REFERENCE},
    ),
    "its-type-through-an-input": (
        {"name": "t", "type": "{{inputs.token}}"},
        {"token": REFERENCE},
    ),
    "its-name-a-slice-of-an-input": (
        {"name": "{{inputs.token | slice(0,14)}}", "type": "text"},
        {"token": REFERENCE},
    ),
    "its-name-a-slug-of-an-input": (
        {"name": "{{inputs.token | slugify}}", "type": "text"},
        {"token": REFERENCE},
    ),
}
#: An ask that shows what a pipe makes of a secret, written or through a run input.
PIPED = {
    "its-prompt-in-two-pieces": (
        {
            "prompt": "first {{secret:"
            + NAME
            + " | slice(0,14)}} then {{secret:"
            + NAME
            + " | slice(14,99)}}"
        },
        {},
    ),
    "its-choices-through-an-input": (
        {
            "ask_kind": "choice",
            "choices": ["{{inputs.token | slice(0,14)}}", "{{inputs.token | slice(14,99)}}"],
        },
        {"token": REFERENCE},
    ),
    "a-fields-default-in-two-pieces": (
        {
            "ask_kind": "form",
            "fields": [
                {
                    "name": "t",
                    "default": "{{secret:"
                    + NAME
                    + " | slice(0,14)}}{{secret:"
                    + NAME
                    + " | slice(14,99)}}",
                }
            ],
        },
        {},
    ),
}


#: The value, and the pieces of it no name or spec holds, any of which a pipe or a split could
#: leave: its hex part, and what follows its fourteenth character.
PIECES = [VALUE, VALUE.split("-")[1], VALUE[14:]]


def _kept(home: Path, *texts: str) -> list[str]:
    """Which of :data:`PIECES` any file under the home but the credential store, or any of
    *texts*, holds."""
    blobs = [
        p.read_text(errors="replace") for p in home.rglob("*") if p.is_file() and p.name != ".env"
    ]
    return sorted({piece for piece in PIECES for blob in (*blobs, *texts) if piece in blob})


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Every event a run publishes, workflow_attention and workflow_needs_input among them."""
    from personalclaw.workflows.controller import RunController

    events: list[tuple[str, Any]] = []
    real = RunController._publish

    def _publish(self: Any, event: str, payload: dict[str, Any]) -> None:
        events.append((event, json.dumps(payload, default=str)))
        real(self, event, payload)

    monkeypatch.setattr(RunController, "_publish", _publish)
    return events


def _refused(controller: Any, words: str) -> None:
    from personalclaw.workflows import service, store
    from personalclaw.workflows.human_input import list_continuations

    run = store.get(controller.run.id)
    failure = controller.instances["root.children[0]"].failure
    assert failure is not None and words in f"{failure.cause_plain}, so {failure.remediation}"
    assert list_continuations(run.id) == [], "nothing was asked"
    home = store.run_dir(run.id).parents[2]
    assert _kept(home, json.dumps(service.status(run.id), default=str)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(WHAT_THE_FORM_IS))
async def test_a_field_whose_name_or_type_names_a_secret_is_refused(home, where):
    """🔴 Red before: the run kept the field's name or type filled, and its status showed it."""
    field, inputs = WHAT_THE_FORM_IS[where]

    controller = await _parked({"ask_kind": "form", "fields": [field]}, **inputs)

    _refused(controller, "a field's name and type are what the form is, so take the")


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(PIPED))
async def test_an_ask_that_pipes_a_secret_is_refused(home, where):
    """In the ask's own words, a field's label, default or choices included. 🔴 Red before: the
    pieces the pipes made held the whole value, kept and shown; a field's piped default was refused
    as a field's name."""
    ask, inputs = PIPED[where]

    controller = await _parked(ask, **inputs)

    _refused(controller, "an ask can't pipe a secret")


#: What a save sees: a secret in a field's name or type, or piped in what the ask shows.
AT_ITS_SAVE = {
    "a-name": ({"ask_kind": "form", "fields": [{"name": REFERENCE}]}, "WF_ASK_FIELD_SECRET"),
    "a-type": (
        {"ask_kind": "form", "fields": [{"name": "t", "type": REFERENCE}]},
        "WF_ASK_FIELD_SECRET",
    ),
    "a-piped-type": (
        {"ask_kind": "form", "fields": [WHAT_THE_FORM_IS["its-type"][0]]},
        "WF_ASK_FIELD_SECRET",
    ),
    "a-piped-prompt": (PIPED["its-prompt-in-two-pieces"][0], "WF_ASK_PIPED_SECRET"),
    "a-piped-default": (
        {
            "ask_kind": "form",
            "fields": [
                {
                    "name": "t",
                    "default": "{{secret:"
                    + NAME
                    + " | slice(0,14)}}{{secret:"
                    + NAME
                    + " | slice(14,99)}}",
                }
            ],
        },
        "WF_ASK_PIPED_SECRET",
    ),
}


@pytest.mark.parametrize("where", sorted(AT_ITS_SAVE))
def test_an_ask_that_would_be_refused_is_refused_at_its_save(where):
    """With its own code alone. 🔴 Red before: the template saved; a piped default was refused as a
    field's name too."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    ask, code = AT_ITS_SAVE[where]
    root = Node.from_dict(_spec(ask)["root"])

    codes = [issue.code for issue in validate_node_tree(root).issues]
    assert [c for c in codes if c.startswith("WF_ASK_")] == [code], codes


# ── fields bound whole, choices as one string, shown text of any shape, and a judge's park ──

#: Fields a run input fills whole, or one field item it fills whole: (the ask, its inputs).
BOUND_FIELDS = {
    "fields-bound-whole": (
        {"ask_kind": "form", "fields": "{{inputs.token}}"},
        {"token": [{"name": REFERENCE, "type": "text"}]},
    ),
    "one-field-item-bound-whole": (
        {"ask_kind": "form", "fields": ["{{inputs.token}}"]},
        {"token": {"name": REFERENCE, "type": "text"}},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(BOUND_FIELDS))
async def test_fields_a_run_input_fills_are_read_as_written_there(home, published, where):
    """Read as the dispatch resolves them, with each reference kept: refused, nothing asked, and no
    piece of the value in the run's state, its continuation, its status, the runs index or what it
    published. 🔴 Red before: the field's name held the value, kept and shown."""
    ask, inputs = BOUND_FIELDS[where]

    controller = await _parked(ask, **inputs)

    _refused(controller, "a field's name and type are what the form is, so take the")
    assert _kept(home, *(payload for _event, payload in published)) == []


@pytest.fixture
def pin(home: Path) -> str:
    """A second secret whose value is a number, which a numeric pipe reads and its reference
    can't be."""
    cred.save_credential("ORCHARD_ASK_PIN", "4242")
    os.environ.pop("ORCHARD_ASK_PIN", None)
    return "ORCHARD_ASK_PIN"


@pytest.mark.asyncio
async def test_a_fields_name_is_read_on_its_own_beside_a_key_its_reference_cant_fill(
    home, published, pin
):
    """Each field's name and type are read on their own with each reference kept, so another
    key's numeric pipe on a secret, which resolves filled and not as the reference, hides no
    name. 🔴 Red before: that key failed the fields' kept reading, the fields were read as written,
    and the name its run input filled was asked and kept."""
    fields = [
        {"name": "{{inputs.token}}"},
        {"name": "n", "required": "{{secret:" + pin + " | clamp(0, 99999)}}"},
    ]

    controller = await _parked({"ask_kind": "form", "fields": fields}, token=REFERENCE)

    _refused(controller, "a field's name and type are what the form is, so take the")
    assert _kept(home, *(payload for _event, payload in published)) == []


@pytest.mark.asyncio
async def test_a_fields_required_is_no_part_of_what_the_form_is(home, pin):
    """The control: a field's `required` piping a numeric secret is no part of what the form is,
    so the ask parks with its one field."""
    from personalclaw.workflows import store
    from personalclaw.workflows.human_input import list_continuations

    field = {"name": "n", "required": "{{secret:" + pin + " | clamp(0, 99999)}}"}
    controller = await _parked({"ask_kind": "form", "fields": [field]})

    (asked,) = [c.ask for c in list_continuations(store.get(controller.run.id).id)]
    assert [f["name"] for f in asked["fields"]] == ["n"], asked


@pytest.mark.asyncio
async def test_a_field_item_a_numeric_pipe_on_a_secret_fills_is_refused(home, pin):
    """A field item that reads a secret and doesn't resolve with each reference kept can't be
    checked, so it is refused in the form's words: it fails closed. 🔴 Red before (at the ask's
    parent): the ask parked."""
    controller = await _parked(
        {"ask_kind": "form", "fields": ["{{secret:" + pin + " | clamp(0, 99999)}}"]}
    )

    _refused(controller, "a field's name and type are what the form is, so take the")


@pytest.mark.asyncio
async def test_a_field_a_numeric_pipe_on_a_run_inputs_secret_fills_is_refused(home):
    """A field's name that pipes a run input holding a reference, a pipe that doesn't resolve on
    the reference, reads a secret, so it is refused in the form's words, nothing asked, and not
    filed as the pipe's failure. 🔴 Red before (at the ask's parent): the secret's value failed the
    pipe, and the step was filed as a binding failure."""
    controller = await _parked(
        {"ask_kind": "form", "fields": [{"name": "{{inputs.token | clamp(0, 99999)}}"}]},
        token=REFERENCE,
    )

    _refused(controller, "a field's name and type are what the form is, so take the")


@pytest.mark.asyncio
async def test_a_field_that_doesnt_resolve_and_reads_no_secret_is_a_binding_failure(home):
    """A field's name that reads no secret and doesn't resolve is filed as a binding failure in its
    own words, nothing asked. A regression guard: the ask's parent files it so too."""
    from personalclaw.workflows import store
    from personalclaw.workflows.human_input import list_continuations

    controller = await _parked(
        {"ask_kind": "form", "fields": [{"name": "{{inputs.form.name}}"}]}, form={}
    )

    failure = controller.instances["root.children[0]"].failure
    assert failure is not None, "vacuity: the gate failed"
    assert (
        "binding failed: unresolved reference at 'name'" in failure.cause_plain
    ), failure.cause_plain
    assert "{{secret:" not in failure.remediation, failure.remediation
    assert list_continuations(store.get(controller.run.id).id) == [], "nothing was asked"


#: Fields bound whole through a pipe that reads a secret.
FIELDS_PIPED_WHOLE = {"ask_kind": "form", "fields": "{{secret:" + NAME + " | slice(0,99)}}"}


@pytest.mark.asyncio
async def test_fields_bound_whole_through_a_pipe_on_a_secret_are_refused(home):
    """🔴 Red before (at the ask's parent): the pipe's text was asked as the form."""
    controller = await _parked(FIELDS_PIPED_WHOLE)

    _refused(controller, "a field's name and type are what the form is, so take the")


def test_fields_bound_whole_through_a_pipe_on_a_secret_are_refused_at_their_save():
    """🔴 Red before (at the ask's parent): the template saved."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    root = Node.from_dict(_spec(FIELDS_PIPED_WHOLE)["root"])

    codes = [issue.code for issue in validate_node_tree(root).issues]
    assert [c for c in codes if c.startswith("WF_ASK_")] == ["WF_ASK_FIELD_SECRET"], codes


#: Asks whose shown text the ask builder would turn into text of its own: choices written as one
#: string (at the top, in a field, through a held input), and a prompt that is a mapping.
SHAPES = {
    "choices-as-one-string": ({"ask_kind": "choice", "choices": REFERENCE}, {}),
    "a-fields-choices-as-one-string": (
        {"ask_kind": "form", "fields": [{"name": "t", "type": "choice", "choices": REFERENCE}]},
        {},
    ),
    "choices-as-one-string-through-an-input": (
        {"ask_kind": "choice", "choices": "{{inputs.token}}"},
        {"token": REFERENCE},
    ),
    "a-prompt-that-is-a-mapping": ({"prompt": {"say": "use " + REFERENCE, "and": "it"}}, {}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_shown_text_of_any_shape_is_masked_before_the_ask_is_built(home, published, shape):
    """Masked on the resolved config before the ask makes text of it, and a string of choices is
    one choice, the mask. 🔴 Red before: the string was split one character per choice, spelling
    the value, or the mapping made text first, so the value was kept."""
    from personalclaw import filled_secrets
    from personalclaw.workflows import service, store
    from personalclaw.workflows.human_input import list_continuations

    ask, inputs = SHAPES[shape]

    controller = await _parked(ask, **inputs)

    run = store.get(controller.run.id)
    (asked,) = [c.ask for c in list_continuations(run.id)]
    status = json.dumps(service.status(run.id), default=str)
    assert _kept(home, json.dumps(asked), status, *(p for _e, p in published)) == []
    mask = filled_secrets.masked(VALUE, [VALUE])
    if "choices" in shape and "field" not in shape:
        assert asked["choices"] == [mask], asked["choices"]
    if shape == "a-fields-choices-as-one-string":
        assert asked["fields"][0]["choices"] == [mask], asked["fields"]


@pytest.fixture
def quoted(home: Path) -> str:
    """A value holding both kinds of quote, stored under its own name."""
    value = "av \"qd\" 'qs' 7c2e91-ORCHARD-QUOTED"
    cred.save_credential("ORCHARD_ASK_QUOTED", value)
    os.environ.pop("ORCHARD_ASK_QUOTED", None)
    return value


@pytest.mark.asyncio
async def test_a_prompt_mapping_holding_both_quote_kinds_is_masked(home, quoted):
    """🔴 Red before: the mapping was made text with its quotes escaped, a form no mask knew."""
    from personalclaw.workflows import store
    from personalclaw.workflows.human_input import list_continuations

    controller = await _parked({"prompt": {"say": "{{secret:ORCHARD_ASK_QUOTED}}"}})

    run = store.get(controller.run.id)
    (asked,) = [c.ask for c in list_continuations(run.id)]
    blobs = [
        p.read_text(errors="replace") for p in home.rglob("*") if p.is_file() and p.name != ".env"
    ]
    assert all("7c2e91-ORCHARD-QUOTED" not in blob for blob in (*blobs, json.dumps(asked)))


#: A judge gate's park builds the same ask: a field name naming a secret, and piped choices, each
#: refused in its words and with its code.
JUDGED = {
    "a-field-name": (
        {"fields": [{"name": REFERENCE, "type": "text"}]},
        "a field's name and type",
        "WF_ASK_FIELD_SECRET",
    ),
    "piped-choices": (
        {
            "choices": [
                "{{secret:" + NAME + " | slice(0,14)}}",
                "{{secret:" + NAME + " | slice(14,99)}}",
            ]
        },
        "an ask can't pipe",
        "WF_ASK_PIPED_SECRET",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(JUDGED))
async def test_a_judge_gates_ask_is_refused_as_an_approvals_is(home, published, where):
    """🔴 Red before: a judge's park kept the field's name and the piped choices."""
    extra, words, _code = JUDGED[where]
    ask = {"kind": "judge", "prompt": "judge the beds", **extra}

    controller = await _parked(ask)

    _refused(controller, words)
    assert _kept(home, *(payload for _event, payload in published)) == []


@pytest.mark.parametrize("where", sorted(JUDGED))
def test_a_judge_gates_ask_is_refused_at_its_save(where):
    """🔴 Red before: the template saved."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    extra, _words, code = JUDGED[where]
    root = Node.from_dict(_spec({"kind": "judge", "prompt": "judge the beds", **extra})["root"])

    codes = [issue.code for issue in validate_node_tree(root).issues]
    assert codes == [code], codes


class _NeedsInput:
    """A judge's model that says the work can't be judged without a person's answer."""

    async def __call__(self, prompt: str, **_kw: Any) -> str:
        return json.dumps({"verdict": "NEEDS_INPUT", "reasoning": "the beds' owner must say"})


#: What a parked judge asks besides its prompt: choices written as one string, and a field's.
JUDGE_ASKS = {
    "its-choices-as-one-string": {"ask_kind": "choice", "choices": REFERENCE},
    "a-fields-choices-as-one-string": {
        "ask_kind": "form",
        "fields": [{"name": "t", "type": "choice", "choices": REFERENCE}],
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ask", sorted(JUDGE_ASKS))
async def test_what_a_parked_judge_asks_besides_its_prompt_is_masked(home, published, ask):
    """A judge's prompt is left to its own refusal of a value in it; what else its park asks is
    masked before the ask is built, as an approval's is. 🔴 Red before: the judge's park kept the
    value, one character per choice."""
    from personalclaw import filled_secrets
    from personalclaw.workflows.models import InstanceState

    gate = {"kind": "judge", "prompt": "judge the beds", **JUDGE_ASKS[ask]}

    controller = await _parked(gate, completion=_NeedsInput())

    parked = controller.instances["root.children[0]"]
    assert parked.state == InstanceState.WAITING, parked.state
    asked = parked.ask
    mask = filled_secrets.masked(VALUE, [VALUE])
    shown = asked["choices"] if "fields" not in JUDGE_ASKS[ask] else asked["fields"][0]["choices"]
    assert shown == [mask], asked
    assert _kept(home, json.dumps(asked), *(p for _e, p in published)) == []
