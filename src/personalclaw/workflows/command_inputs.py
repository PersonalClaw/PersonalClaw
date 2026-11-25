"""The inputs a workflow runs as a command or uses as a folder, and who may give them a value.

A step can run a command: a verify gate's (`verify.command`, `verify.guard`, `verify.script`), a
ladder's criteria, a judge's check (`checks`, and the run's `runtime_hints.judge` proof and hidden
checks), a shell action's (`with.command`, and every variable it sets, which its command, or a
script it runs, may run), a script action's (`with.script`). A step can work in a folder: any
step's `cwd`, a verify gate's or a check's, an action's, and every step reads the run's `cwd`
input as a folder beside its own (`provisioning.step_reads`). A step reads an input itself, as the
`{{item}}` of a `foreach` over it, or the run's document path (`{{run.document}}`). A definition
that binds one of these to an input (goal-pursuit-verifiable's `{{inputs.verify_command}}` in
`{{inputs.scope}}`) lets whoever gives that input its value choose a command the engine runs, or
a folder a step works in. The engine
runs a verify gate's and a judge check's command with nobody asked, screened by the denylists and
the destructive-command screen, never by the starter's own limits.

So a start by anyone but the owner (an agent's `workflow_start`, an app, an A2A client) is refused
when it gives such an input a value (:func:`refusal`), naming the input and the step that reads
it. Left out, the input takes the definition's own default, or the start is refused for a missing
required input as any start is: the run runs only what the definition the owner saved says. A
start's `project_id` is not one of these, and anyone who may start the run may give it: it
chooses which of the owner's own projects the run works in, never a command, nor a folder outside
her projects.

**And where the command runs, one rule for every door** (:class:`Chosen`). A step runs a command,
or works in a folder, only as the owner chose it: each input such a value reads holds the
definition's default, or a value she chose for the run. She chooses one at her own start, at her
confirmed edit, through an automation she made, and a fork of her run or a run its step starts
keeps what she chose (:func:`vouch`, :func:`carried`, :func:`handed`). The run records a digest of
each such value (:data:`VOUCHED_KEY`), and a value it does not hold, however it came to be there
(an agent's edit, an automation an agent or an app made, what a step produced and handed on), is
refused at the step: a verify gate or a shell step fails in the rule's words (:func:`step_refusal`),
and a judge's check does not run, and its judge reads why (`judge_checks`). A run that records
nothing holds nothing: a door that starts a run without saying what its owner chose runs no
command an input names.

A gate's check is held to one more limit, a judge check's: its command and folder read only the
run's inputs and its document (`judge_checks.read_of_the_work`). A step's output is the work's,
and the work does not choose the check it is judged by.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.bindings import refs_in

#: Who started a run, when it was not the owner (``""`` is the owner, or PersonalClaw on the
#: owner's behalf: a batch or a proposal they allowed, an automation they allowed).
AGENT = "an agent"
APP = "an app"
A2A = "an A2A client"
OTHER = "a caller other than you"

#: Where a run records the values its owner chose for its inputs: ``{input: [digest, ...]}``, the
#: digest (:func:`digest`) of each value she gave it, so the record holds no value itself.
VOUCHED_KEY = "inputs_you_chose"
#: Who started a run (:func:`starter`): "" for the owner, else :data:`AGENT`, :data:`APP`,
#: :data:`A2A` or :data:`OTHER`. A run a step of it starts, and a fork of it, keep it, so what a
#: run someone else started starts is held to what their start is (:func:`not_yours_to_start`).
STARTED_BY_KEY = "started_by"
#: On a run an automation started, the automation's name when its action was last written by a
#: door that did not ask the owner (`triggers.grants.action_by`): what it gives is then no value she
#: chose, and a refusal says to save the automation in its editor.
CHANGED_AUTOMATION_KEY = "automation_changed_unasked"
#: On a run a step of another starts, the workflow that started it when that workflow's definition
#: is not the owner's (`versions.hers`): what it hands on is then no value she chose, and a refusal
#: says where she can make it hers.
HANDED_BY_KEY = "inputs_handed_by"

#: The input every workflow's steps read as a folder beside their own, whether or not the
#: definition declares it or a step names it (`provisioning.step_reads`).
CWD_INPUT = "cwd"

#: The name the run's document path is held under here: a command may read it
#: (`{{run.document}}`), and the start that sets it (the loop route's `document`) chose it.
DOCUMENT = "run.document"

#: A step's config keys whose value runs as a command or names a folder a step works in.
_RUN_KEYS: tuple[str, ...] = ("command", "cwd", "script")

#: A verify block's: the command, the guard run after it, a script in its place, and the folder.
_VERIFY_KEYS: tuple[str, ...] = ("command", "guard", "script", "cwd")


def of_request(request: Any) -> str:
    """Who an HTTP request that starts a run is, as *started_by* reads it: "" for you (a signed-in
    session of yours, or you on a paired channel), else an app, an agent (the internal secret an
    agent's tools present), or another caller (`approval_answer.of_request`)."""
    from personalclaw import approval_answer

    kind = approval_answer.of_request(request).kind
    if kind in (approval_answer.OWNER, approval_answer.CHANNEL):
        return ""
    return {approval_answer.APP: APP, approval_answer.AGENT: AGENT}.get(kind, OTHER)


def readers(spec: dict[str, Any], *, _depth: int = 0) -> dict[str, str]:
    """Each input of *spec* that a step runs as a command or uses as a folder, with the first place
    that reads it (`verify.command of step verify`)."""
    found: dict[str, str] = {}
    for where, value, items in _run_values(spec):
        for name in _reads(value, items):
            found.setdefault(name, where)
    # An input a step hands a workflow it starts is one the child may run: so its default is a
    # command the definition writes, and a start by anyone else may not give it a value. A child
    # named as written is read here, and only the inputs it runs count; one named any other way, or
    # one this home cannot read, may run any of them.
    for where, value, child, child_input in _handed_to(spec):
        runs = _child_readers(child, _depth)
        if runs is not None and child_input not in runs:
            continue
        for name in _reads(value):
            found.setdefault(name, where)
    found.setdefault(CWD_INPUT, "the folder every step of a run reads beside its own")
    return found


def given(inputs: Mapping[str, Any] | None, document: str = "") -> dict[str, Any]:
    """What a start gives a run that a step may read: its *inputs*, and its *document* path as
    :data:`DOCUMENT` when it sets one."""
    return {**(inputs or {}), **({DOCUMENT: document} if document else {})}


def commands(spec: dict[str, Any]) -> set[tuple[str, str]]:
    """Every command a step of *spec* runs and folder it runs in, as written, with where it is:
    what an edit that adds or changes one is held to (`mid_flight.command_refusal`). The default of
    an input a step reads as one is one too (:func:`_default_commands`)."""
    written = _run_values(spec)
    steps = {(where, json.dumps(value, sort_keys=True, default=str)) for where, value, _ in written}
    handed = {(where, json.dumps(v, sort_keys=True, default=str)) for where, v in _handed(spec)}
    return steps | handed | _default_commands(spec)


#: Where a value a step hands the workflow it starts is said, as the owner's ask names it.
HANDS = "that step {step} hands the workflow it starts"


def _handed(spec: Mapping[str, Any] | None) -> Iterator[tuple[str, Any]]:
    """Each value a step of *spec* hands a workflow it starts, with where (:func:`_handed_to`)."""
    for where, value, _child, _input in _handed_to(spec):
        yield where, value


def _child_readers(name: str, depth: int) -> dict[str, str] | None:
    """The inputs the workflow *name* runs as a command or uses as a folder (:func:`readers`), or
    None when it is not named as written or this home cannot read it."""
    spec = _named_spec(name) if depth < 4 else None
    return None if spec is None else readers(spec, _depth=depth + 1)


def _named_spec(name: str) -> dict[str, Any] | None:
    """The definition this home runs for the workflow *name*: its own, else a bundled template."""
    from personalclaw.workflows.models import valid_name

    if not name or "{{" in name or not valid_name(name):
        return None
    from personalclaw.workflows import native_defs
    from personalclaw.workflows.bundled_defs import read_template

    try:
        path = native_defs._def_path(name)
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else None
        template = read_template(name)
    except (OSError, ValueError):
        return None
    return template.to_dict() if template is not None else None


def _handed_to(spec: Mapping[str, Any] | None) -> Iterator[tuple[str, Any, str, str]]:
    """Each value a step of *spec* hands a workflow it starts, with where, the workflow it names
    and the input: a ``subworkflow`` step's ``config.inputs`` (``config.ref``) and a
    ``run-workflow`` action's ``config.with.inputs`` (``config.with.workflow``). The run it starts
    holds each value its parent's definition writes as its owner's (:func:`handed`), so a value
    written there is a command the parent writes whenever the child runs that input."""

    def _walk(node: Any) -> Iterator[tuple[str, Any, str, str]]:
        if isinstance(node, list):
            for child in node:
                yield from _walk(child)
            return
        if not isinstance(node, dict):
            return
        raw = node.get("config")
        config: dict[str, Any] = raw if isinstance(raw, dict) else {}
        given: Any = None
        child = ""
        if node.get("kind") == "subworkflow":
            given, child = config.get("inputs"), str(config.get("ref") or "")
        elif node.get("kind") == "action" and config.get("provider") == "run-workflow":
            with_ = config.get("with")
            if isinstance(with_, dict):
                given, child = with_.get("inputs"), str(with_.get("workflow") or "")
        for name, value in (given.items() if isinstance(given, dict) else ()):
            if value not in (None, ""):
                where = HANDS.format(step=node.get("id") or "?")
                yield f"input `{name}` {where}", value, child, str(name)
        for key, value in node.items():
            if key != "config":
                yield from _walk(value)

    yield from _walk((spec or {}).get("root"))


def _default_commands(spec: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    """The default of each input a step runs as a command (or uses as a folder), as a command the
    definition writes: a run that gives the input nothing runs its default, and the default counts
    as the owner's (:meth:`Chosen.chose`), so only a save she made or said yes to writes one."""
    named = readers(dict(spec or {}))
    return {
        (f"the default of input `{name}`, which {named[name]} reads", json.dumps(value))
        for name, value in _defaults(spec).items()
        if name in named and value not in (None, "")
    }


def _a_choice(spec: Mapping[str, Any], name: str, value: Any) -> bool:
    """Whether *value*, given to the input *name*, is one of the values it names (its `enum`), as
    a start reads it: through its declared type (`contracts.coerce_declared_inputs`)."""
    from personalclaw.workflows.contracts import coerce_declared_inputs

    choices = _choices(spec).get(name)
    if not choices:
        return False
    declared = spec.get("inputs")
    meta = declared.get(name) if isinstance(declared, dict) else None
    coerced, errors = coerce_declared_inputs({"inputs": {name: meta}}, {name: value})
    return not errors and coerced.get(name) in choices


def _choices(spec: Mapping[str, Any] | None) -> dict[str, list[Any]]:
    """The values each input of *spec* that names them may take (its `enum`)."""
    declared = (spec or {}).get("inputs")
    if not isinstance(declared, dict):
        return {}
    return {
        str(name): list(meta["enum"])
        for name, meta in declared.items()
        if isinstance(meta, dict) and isinstance(meta.get("enum"), list) and meta["enum"]
    }


def _chosen_commands(spec: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    """Each value an input a step runs as a command (or uses as a folder) may take, as a command
    the definition writes: a choice among them is a command whoever chooses it runs."""
    named = readers(dict(spec or {}))
    return {
        (f"a choice of input `{name}`, which {named[name]} reads", json.dumps(choice, default=str))
        for name, values in _choices(spec).items()
        if name in named
        for choice in values
    }


#: What a save that writes a command a step runs is asked about, as the owner's dialog says it.
WRITES_A_COMMAND = (
    "This save writes a command a step of the workflow runs, or the folder it runs in, that you "
    "did not write: the engine runs it with nobody asked."
)


#: The bindings a command may read that bring it only what the owner chose (:class:`Chosen`): the
#: run's inputs, its document's path and a `foreach`'s item, each read as it is. Any other binding
#: is text the save writes: a ``{{secret:…}}``, since a command made of a secret reference and an
#: input sends the secret wherever the input says, and a binding with a pipe, whose
#: ``default('…')`` writes the command a run that gives the input nothing runs.
_WHAT_SHE_CHOSE = re.compile(
    r"\{\{\s*(?:inputs\.[\w.\-\[\]]+|run\.document|item(?:\.[\w.\-\[\]]+)?)\s*\}\}"
)


def written_commands(stored: Mapping[str, Any] | None, spec: Mapping[str, Any]) -> list[Any]:
    """Each command a step of *spec* runs, or folder it runs it in, that its text writes and the
    stored definition *stored* (None for a new one) does not, as a step a save asks the owner
    for (`automation_posture.Loosening`, key ``command``), an input's default a step runs among them
    (:func:`_default_commands`). A value that only reads what the owner chose
    (:data:`_WHAT_SHE_CHOSE`) writes no command: what reaches it is hers (:class:`Chosen`)."""
    from personalclaw.automation_posture import Loosening

    before = commands(dict(stored)) if stored else set()
    before |= _chosen_commands(stored) if stored else set()
    out = []
    for where, written in sorted((commands(dict(spec)) | _chosen_commands(spec)) - before):
        text = str(json.loads(written))
        if not _WHAT_SHE_CHOSE.sub("", text).strip():
            continue
        shown = text if len(text) <= 120 else f"{text[:117]}..."
        may = (
            f"hands `{shown}` to a workflow it starts, which may run it, with nobody asked"
            if " hands the workflow " in where
            else f"runs `{shown}` with nobody asked, outside its agent's own approval"
        )
        out.append(
            Loosening(
                path=where,
                label=where,
                agent="",
                keys=("command",),
                may=(may,),
                field="workflow.command",
                consent=WRITES_A_COMMAND,
                change=shown,
            )
        )
    return out


def refusal(spec: dict[str, Any], given: dict[str, Any] | None, started_by: str) -> str:
    """Why a start by *started_by* is refused for the inputs it *given*, or "" when it may start:
    always "" for the owner (*started_by* ""), and for anyone else "" unless it gives a value to an
    input a step runs as a command or uses as a folder (:func:`readers`)."""
    if not started_by or not given:
        return ""
    named = readers(spec)
    # An input that names the values it may take is chosen among commands the definition names:
    # a start may give it one of them, and nothing else.
    taken = sorted(
        name for name in given if name in named and not _a_choice(spec, name, given[name])
    )
    if not taken:
        return ""
    first = taken[0]
    listed = ", ".join(f"`{name}`" for name in taken)
    return (
        f"a run {started_by} starts cannot give {listed} a value: this workflow reads "
        f"`{first}` as {named[first]}, and only you choose what a run runs, or where. Leave "
        f"{'it' if len(taken) == 1 else 'them'} out to run the definition's own, or start the "
        f"run yourself."
    )


def at_start(spec: dict[str, Any], given: dict[str, Any] | None, started_by: str) -> str:
    """Why a start by *started_by* of *spec* with the inputs *given* is refused, or "": the
    definition is not hers to start (:func:`not_yours_to_start`), or a value it gives is not one
    it may give (:func:`refusal`)."""
    return not_yours_to_start(spec, started_by) or refusal(spec, given, started_by)


def unvouched_command(spec: dict[str, Any]) -> Any:
    """The first command *spec* writes (:func:`written_commands`, an `automation_posture.Loosening`)
    when it is not hers by what it runs (`versions.hers`): her own save, a save her yes covered, or
    a release's. None when it writes none, or is hers."""
    from personalclaw.workflows import versions

    written = written_commands(None, spec)
    if not written or versions.hers(str(spec.get("name") or ""), spec):
        return None
    return written[0]


def not_yours_to_start(spec: dict[str, Any], started_by: str) -> str:
    """Why a start by *started_by*, anyone but the owner, of the definition *spec* is refused, or
    "": every start's door asks it (`service._admitted_inputs`) before :func:`refusal`.

    A run counts the definition's own defaults and text as the owner's (:meth:`Chosen.chose`), so
    they run as hers only when the definition is hers by what it runs (`versions.hers`): her own
    save, a save her yes covered, or a release's. One another door wrote with nothing asked of
    her (an agent's save from before a save asked her, a file a sync or a restore brought in)
    that runs a command it writes (:func:`written_commands`) is refused at anyone else's start, and
    she makes it hers by saving it in its editor. Her own start runs it: she chose to."""
    if not started_by or (first := unvouched_command(spec)) is None:
        return ""
    name = str(spec.get("name") or "")
    return (
        f"a run {started_by} starts cannot run this workflow: it runs `{first.change}` "
        f"({first.label}), and no version you saved or allowed writes that. Open “{name}” "
        f"in Workflows and save it in its editor to make it yours, or start the run yourself."
    )


# ── what the owner chose, and the rule a step is held to ──────────────────────────────────────


def digest(value: Any) -> str:
    """What a run records of a value its owner chose: a SHA-256 of it written as JSON."""
    text = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vouch(extra: Mapping[str, Any] | None, given: Mapping[str, Any] | None) -> dict[str, Any]:
    """*extra* (a run's record) with each value of *given* recorded as one its owner chose for
    that input, beside the ones it holds already."""
    held = _held(extra)
    for name, value in (given or {}).items():
        digests = held.setdefault(str(name), [])
        if (hashed := digest(value)) not in digests:
            digests.append(hashed)
    return {**(extra or {}), VOUCHED_KEY: held}


def carried(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a fork of a run keeps of the run's record *extra* here: the values its owner chose,
    which the fork's inputs are copied from, and who started the run (:func:`starter`)."""
    return {VOUCHED_KEY: _held(extra), STARTED_BY_KEY: starter(extra)}


def starter(extra: Mapping[str, Any] | None) -> str:
    """Who started the run whose record is *extra*: "" for the owner, else who did, as its start
    recorded it (:func:`stamped`), a run a step starts and a fork keeping its parent's. A record
    that does not say (none, or one from before runs said) is read as someone else's
    (:data:`OTHER`): what that run starts is held to what such a start is."""
    value = (extra or {}).get(STARTED_BY_KEY)
    return value if isinstance(value, str) else OTHER


def child_refusal(spec: dict[str, Any], parent: Any) -> str:
    """Why a step of the run *parent* (a `WorkflowRun`, or None) may not start a run of *spec*, or
    "": the run was started by someone else (:func:`starter`), and *spec* is not theirs to start
    (:func:`not_yours_to_start`). A run she started starts what her definition names, as her own
    start of it would."""
    return not_yours_to_start(spec, starter(getattr(parent, "extra", None)))


def as_started_by(run: Any, spec: dict[str, Any], started_by: str) -> str:
    """Why *started_by* may not start the run *run* (a draft: a fork, kept to start later) of
    *spec*, or "": it is not theirs to start (:func:`not_yours_to_start`). The run is theirs from
    now on, so what it starts is held to their start (:func:`starter`)."""
    from personalclaw.workflows import store

    if refused := not_yours_to_start(spec, started_by):
        return refused
    run.extra = {**(run.extra or {}), STARTED_BY_KEY: started_by}
    store.save(run)
    return ""


def handed(
    given: Mapping[str, Any] | None,
    *,
    spec: Mapping[str, Any],
    extra: Mapping[str, Any] | None,
    definition_is_hers: bool = False,
) -> dict[str, Any]:
    """The record a run a step starts begins with: those of the values it is handed (*given*) that
    the run whose step it is (of *spec*, its record *extra*) holds as its owner's. Hers are a value
    she chose for an input of that run, and, when its definition is hers (*definition_is_hers*,
    `versions.hers`), a default of that definition and text it writes itself. A value a step
    produced, one someone else gave that run, and what a definition another door wrote with
    nothing asked of her says, are not."""
    hers = {hashed for digests in _held(extra).values() for hashed in digests}
    if definition_is_hers:
        hers |= {digest(value) for value in _defaults(spec).values() if value not in (None, "")}
        hers |= {digest(text) for text in _handed_texts(spec)}
    parent = {} if definition_is_hers else {HANDED_BY_KEY: str(spec.get("name") or "")}
    return (
        parent
        | {STARTED_BY_KEY: starter(extra)}
        | vouch({}, {name: value for name, value in (given or {}).items() if digest(value) in hers})
    )


def stamped(
    extra: Mapping[str, Any] | None, given: Mapping[str, Any] | None, started_by: str
) -> dict[str, Any]:
    """A new run's record *extra* as its start says what its owner chose: each value of *given*
    when she started it herself (*started_by* ""), and none when anyone else did, whatever
    *extra* held under :data:`VOUCHED_KEY`."""
    own = {key: value for key, value in (extra or {}).items() if key != VOUCHED_KEY}
    return vouch({**own, STARTED_BY_KEY: started_by}, {} if started_by else given)


def handed_by(parent: Any, given: Mapping[str, Any] | None) -> dict[str, Any]:
    """:func:`handed` for a run a step of the run *parent* (a `WorkflowRun`, or None) starts with
    the inputs *given*: the parent's definition as it keeps it, and its record. That definition is
    hers when what it runs is a version of hers (`versions.hers`), not when its number names one:
    an edit of the run since its start, or a file another door wrote under her number, is not."""
    if parent is None:
        return vouch({}, {})
    from personalclaw.workflows import store, versions

    spec = store.read_spec(parent.id) or {}
    name = str(getattr(parent, "workflow_name", "") or "")
    return handed(
        given, spec=spec, extra=parent.extra, definition_is_hers=versions.hers(name, spec)
    )


@dataclass(frozen=True)
class Chosen:
    """A run's inputs as its owner chose them: what a step that runs a command is held to."""

    values: Mapping[str, Any]
    defaults: Mapping[str, Any]
    held: Mapping[str, list[str]]
    #: The workflow's name, as the refusal tells its owner where to start it herself.
    name: str = ""
    #: The inputs each step's `{{item}}` comes from, by step id: a `foreach`'s items.
    items: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Whether the run keeps a record of what its owner chose at all: one started before the
    #: record was kept has none, holds no value as hers, and is told to start again.
    recorded: bool = True
    #: The values each input that names them may take (its `enum`): each is a command the
    #: definition names, so whoever chose it, it runs.
    choices: Mapping[str, list[Any]] = field(default_factory=dict)
    #: The workflow whose step started this run, when its definition is not the owner's
    #: (:data:`HANDED_BY_KEY`), or "".
    handed_by: str = ""
    #: The automation that started the run, when an edit that did not ask her wrote its action
    #: (:data:`CHANGED_AUTOMATION_KEY`), or "".
    changed_automation: str = ""

    @classmethod
    def of(
        cls,
        spec: Mapping[str, Any] | None,
        inputs: Mapping[str, Any] | None,
        extra: Mapping[str, Any] | None,
    ) -> Chosen:
        """What the owner chose for a run of *spec* with *inputs*, its record *extra*."""
        from personalclaw.workflows.deliverable import RUN_DOCUMENT_KEY

        values = {**(inputs or {}), DOCUMENT: (extra or {}).get(RUN_DOCUMENT_KEY)}
        return cls(
            values=values,
            defaults=_defaults(spec),
            held=_held(extra),
            name=str((spec or {}).get("name") or ""),
            items=_items_by_step((spec or {}).get("root")),
            recorded=isinstance((extra or {}).get(VOUCHED_KEY), dict),
            choices=_choices(spec),
            handed_by=str((extra or {}).get(HANDED_BY_KEY) or ""),
            changed_automation=str((extra or {}).get(CHANGED_AUTOMATION_KEY) or ""),
        )

    def chose(self, name: str) -> bool:
        """Whether the run's value of the input *name* is one its owner chose: nothing, the
        definition's default, or a value she gave the run."""
        value = self.values.get(name)
        if value in (None, "") or value == self.defaults.get(name):
            return True
        if value in self.choices.get(name, ()):
            return True
        return digest(value) in self.held.get(name, ())

    def refusal(self, text: Any, where: str, *, step: str = "") -> str:
        """Why *text*, a command the step *step* runs or the folder it runs in (*where*, as a
        sentence names it), may not run with this run's inputs, or "": each input it reads,
        itself or as the `{{item}}` of a `foreach` over it, holds a value its owner chose
        (:meth:`chose`)."""
        for name in _reads(text, self.items.get(step, ())):
            if self.chose(name):
                continue
            shown = "its document" if name == DOCUMENT else f"`{name}`"
            workflow = f"“{self.name}”" if self.name else "the workflow"
            if self.handed_by:
                return (
                    f"{where} reads {shown}, which the workflow “{self.handed_by}” that started "
                    "this run handed it, and that workflow's definition is not one you saved or "
                    "said yes to here (an agent or an app saved it, or it came with a restore or "
                    "an import), so what it hands on is no value you chose. To make it yours, open "
                    f"“{self.handed_by}” in Workflows and save it in its editor, or start "
                    f"{workflow} yourself and give {shown} the value there."
                )
            if self.changed_automation:
                return (
                    f"{where} reads {shown}, which the automation “{self.changed_automation}” "
                    "gave this run, and an agent or another door that did not ask you changed "
                    "what that automation runs since you last saved it, so what it gives is no "
                    f"value you chose. Open “{self.changed_automation}” in Triggers and save it in "
                    "its editor to make it yours."
                )
            why = (
                "its value in this run is not one you chose"
                if self.recorded
                else "this run started before PersonalClaw kept what you chose for a run, so it "
                "holds no value of an input as yours"
            )
            return (
                f"{where} reads {shown}, and {why}. A step runs a command, or works in a folder, "
                "only with the definition's default or a value you give it yourself: to run it "
                f"with this one, start it again: open {workflow} in Workflows, choose Run, and "
                f"give {shown} the value there."
            )
        return ""


def step_refusal(node: Any, chosen: Chosen) -> str:
    """Why the step *node* (a `Node`, or a node of a spec) may not run, or "": a command it runs,
    or the folder it runs it in, reads an input as its owner did not choose it
    (:meth:`Chosen.refusal`), or a gate's check reads more than the run's inputs and its document.
    A judge's checks are not asked here: each that may not run is said to its judge
    (`judge_checks`)."""
    raw = node.to_dict() if hasattr(node, "to_dict") else node
    if not isinstance(raw, dict):
        return ""
    from personalclaw.workflows.judge_checks import read_of_the_work

    for where, value, checked in _own_values(raw, judges=False):
        if checked and (read := read_of_the_work(value)):
            return (
                f"{where} reads {{{{{read}}}}}, and a check's command and folder come from the "
                "run's inputs, never from what a step produced."
            )
        if refused := chosen.refusal(value, where, step=str(raw.get("id") or "")):
            return refused
    return ""


def _held(extra: Mapping[str, Any] | None) -> dict[str, list[str]]:
    raw = (extra or {}).get(VOUCHED_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(d) for d in v] for k, v in raw.items() if isinstance(v, list)}


def _defaults(spec: Mapping[str, Any] | None) -> dict[str, Any]:
    declared = (spec or {}).get("inputs")
    if not isinstance(declared, dict):
        return {}
    return {str(k): meta.get("default") for k, meta in declared.items() if isinstance(meta, dict)}


def _handed_texts(spec: Mapping[str, Any] | None) -> Iterator[str]:
    """The texts *spec* writes itself that a step can hand a workflow it starts: each value a step
    hands on (:func:`_handed_to`), each transform's expression, which a step hands on as that
    transform's output, and each `foreach`'s items, which a step hands on as its `{{item}}`. A
    prompt or a description it writes is not among them, so a step's output that happens to equal
    one is no value she chose."""
    for _where, value, _child, _name in _handed_to(spec):
        yield from _written(value)

    def _walk(node: Any) -> Iterator[str]:
        if isinstance(node, list):
            for child in node:
                yield from _walk(child)
            return
        if not isinstance(node, dict):
            return
        raw = node.get("config")
        config: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if node.get("kind") == "transform":
            yield from _written(config.get("expr"))
        elif node.get("kind") == "foreach":
            yield from _written(config.get("items"))
        for key, value in node.items():
            if key != "config":
                yield from _walk(value)

    yield from _walk((spec or {}).get("root"))


def _written(node: Any) -> Iterator[str]:
    """Every text *node* writes itself: a string that reads no binding."""
    if isinstance(node, str):
        if "{{" not in node:
            yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _written(value)
    elif isinstance(node, list):
        for value in node:
            yield from _written(value)


def _reads(value: Any, items: tuple[str, ...] = ()) -> Iterator[str]:
    """The name of each input *value* reads (`{{inputs.check | trim}}` reads `check`), its
    document (:data:`DOCUMENT`) when it reads `{{run.document}}`, and *items* when it reads the
    `{{item}}` of a `foreach` over those inputs."""
    for expr in refs_in(value):
        path = expr.split("|", 1)[0].strip()
        root, _, rest = path.partition(".")
        root = root.split("[", 1)[0].strip()
        if root == "item":
            yield from items
        elif root == "run" and rest.strip() == "document":
            yield DOCUMENT
        elif root == "inputs" and rest:
            if name := rest.split(".", 1)[0].split("[", 1)[0].strip():
                yield name


def _run_values(spec: dict[str, Any]) -> Iterator[tuple[str, Any, tuple[str, ...]]]:
    """Every value of *spec* a step runs as a command or uses as a folder, with where it is and
    the inputs its `{{item}}` comes from."""
    hints = (spec.get("runtime_hints") or {}) if isinstance(spec, dict) else {}
    judge = hints.get("judge") if isinstance(hints, dict) else None
    if isinstance(judge, dict):
        if judge.get("proof_command"):
            yield "the judge's proof command", judge["proof_command"], ()
        for command in judge.get("hidden_validation_commands") or []:
            yield "a hidden check of the judge", command, ()
    yield from _steps(spec.get("root") if isinstance(spec, dict) else None)


def _steps(node: Any, items: tuple[str, ...] = ()) -> Iterator[tuple[str, Any, tuple[str, ...]]]:
    """Every value the steps of *node* run as a command or use as a folder (:func:`_own_values`),
    with the inputs their `{{item}}` comes from: a `foreach`'s body reads the inputs its items
    read, its own `{{item}}` included for a `foreach` inside another."""
    if isinstance(node, list):
        for child in node:
            yield from _steps(child, items)
        return
    if not isinstance(node, dict):
        return
    for where, value, _checked in _own_values(node):
        yield where, value, items
    inner = _items_of(node, items)
    for key, value in node.items():
        if key != "config":
            yield from _steps(value, inner if key == "body" else items)


def _items_of(node: dict[str, Any], items: tuple[str, ...]) -> tuple[str, ...]:
    """The inputs the `{{item}}` of *node*'s body comes from: a `foreach`'s items' reads, else
    *items* as its own body has them."""
    config = node.get("config")
    if node.get("kind") != "foreach" or not isinstance(config, dict):
        return items
    return tuple(dict.fromkeys(_reads(config.get("items"), items)))


def _items_by_step(node: Any, items: tuple[str, ...] = ()) -> dict[str, tuple[str, ...]]:
    """The inputs each step of *node* reads as its `{{item}}`, by step id (only those that read
    any)."""
    out: dict[str, tuple[str, ...]] = {}
    if isinstance(node, list):
        for child in node:
            out.update(_items_by_step(child, items))
    elif isinstance(node, dict):
        if items and node.get("id"):
            out[str(node["id"])] = items
        inner = _items_of(node, items)
        for key, value in node.items():
            if key != "config":
                out.update(_items_by_step(value, inner if key == "body" else items))
    return out


def _own_values(node: dict[str, Any], *, judges: bool = True) -> Iterator[tuple[str, Any, bool]]:
    """Every value of the step *node*'s own config that it runs as a command or uses as a folder:
    where it is, the value, and whether it is a gate's check (its `verify` block, a ladder's
    criteria), whose command reads only the run's inputs and its document. *judges* counts a
    judge's checks among them."""
    config = node.get("config")
    if not isinstance(config, dict) or "kind" not in node:
        return
    step = str(node.get("id") or node.get("kind"))
    gate = bool(node.get("kind") == "gate")
    for key in _RUN_KEYS:
        if key in config:
            yield f"the {key} of step {step}", config[key], False
    blocks: list[tuple[str, Any, tuple[str, ...], bool]] = [
        ("verify.{} of step {}", config.get("verify"), _VERIFY_KEYS, gate)
    ]
    criteria = _listed(config.get("criteria"))
    blocks += [("a criterion's {} of step {}", crit, _RUN_KEYS, gate) for crit in criteria]
    blocks.append(("with.{} of step {}", config.get("with"), _RUN_KEYS, False))
    for where, block, keys, checked in blocks:
        if isinstance(block, dict):
            for key in keys:
                if key in block:
                    yield where.format(key, step), block[key], checked
    yield from _shell_variables(config, step)
    if judges:
        for check in _listed(config.get("checks")):
            if isinstance(check, str):
                yield f"a check of step {step}", check, False
            elif isinstance(check, dict):
                for key in ("command", "cwd"):
                    if key in check:
                        yield f"a check's {key} of step {step}", check[key], False


def _shell_variables(config: dict[str, Any], step: str) -> Iterator[tuple[str, Any, bool]]:
    """Every variable a shell step sets (its `payload`, its `with.env`), each a value its command
    may run: code-project's baseline runs `sh -c "$PC_VERIFY_CMD"` and `cd "$PC_CWD"`, and a
    command that runs a script hands the script every one of them, which the command line cannot
    show. So none is read as a mere value."""
    if str(config.get("provider") or "") != "bash":
        return
    inner: dict[str, Any] = config["with"] if isinstance(config.get("with"), dict) else {}
    for env in (config.get("payload"), inner.get("env")):
        if isinstance(env, dict):
            for key, value in env.items():
                yield f"the {key} variable of step {step}'s shell", value, False


def _listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
