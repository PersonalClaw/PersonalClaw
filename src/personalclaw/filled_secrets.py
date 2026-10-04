"""What work a ``{{secret:NAME}}`` was filled into keeps of what it did: masked of the value.

A dispatch that fills a stored secret into what it runs hands the value to work that can print it,
return it or fail with it: an ``echo``, a ``curl -v`` trace, an error that quotes its arguments, a
script's answer. What comes back is kept and shown: an automation's run history and last error, the
note that reports the run and the answer Run now gives (``triggers.secrets.resolve_for``), and a
workflow step's output, failure and ledger (``workflows.bindings.BindingContext.filled``). A value
has no shape a mask could find, so the dispatch that filled it masks it out by value before any of
those keep or show what came back, by the rule the agent's ``bash`` tool applies to the command it
runs (``security.redact_known_values``): every value it filled in, wherever it appears, however
short. A value the work changes on the way (encodes, cuts, splits) is not one this can recognise.

What the work writes itself while it runs is masked too: the dispatch holds the values it filled in
for as long as the work runs (:func:`handed`), and the writers of such a record mask them out of
what they are given (:func:`masked_here`).
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from personalclaw.security import redact_known_values

#: The values each dispatch the work running in this context runs inside filled into it, one
#: collection per dispatch (:func:`handed`).
_HANDED: ContextVar[tuple[Collection[str], ...]] = ContextVar("filled_secrets_handed", default=())

#: What an action's answer says (``ActionResult``): what it printed, its error output, its reason
#: and its line for a person. Its handles (``reversal``, ``work_id``) name what it did, and its
#: outcome and exit code are no text, so they are kept as they are.
_ANSWER_TEXT = ("stdout", "stderr", "error", "summary")

#: What a failure envelope (``errors.AgentError``) says.
_ENVELOPE_TEXT = ("what", "why", "fix", "suggestions")


def masked(value: Any, filled: Collection[str]) -> Any:
    """*value* with every one of *filled* masked in each string it holds: the text itself, a
    mapping's values, a list's and a tuple's items. A mapping's keys, a number and anything else
    are kept: what a step returns is keyed by its author or by the engine, and the text a command
    printed is masked as text before anything reads it as JSON (:func:`masked_answer`)."""
    if not filled:
        return value
    if isinstance(value, str):
        return redact_known_values(value, filled)
    if isinstance(value, dict):
        return {key: masked(item, filled) for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item, filled) for item in value]
    if isinstance(value, tuple):
        return tuple(masked(item, filled) for item in value)
    return value


def masked_fields(obj: Any, names: Iterable[str], filled: Collection[str]) -> Any:
    """*obj* with each of its attributes in *names* masked (:func:`masked`): a copy when that
    changes any, *obj* itself when it changes none."""
    changes: dict[str, Any] = {}
    for name in names:
        said = getattr(obj, name, None)
        if said is not None and (shown := masked(said, filled)) != said:
            changes[name] = shown
    return _copied(obj, changes)


def masked_answer(result: Any, filled: Collection[str]) -> Any:
    """*result*, what an action answered (``ActionResult``), with every one of *filled* masked in
    what it says (:data:`_ANSWER_TEXT`) and in its failure envelope. *result* itself when nothing
    was filled in or there is no answer (``None``)."""
    if not filled or result is None:
        return result
    envelope = getattr(result, "agent_error", None)
    if envelope is not None:
        told = masked_fields(envelope, _ENVELOPE_TEXT, filled)
        result = _copied(result, {"agent_error": told} if told is not envelope else {})
    return masked_fields(result, _ANSWER_TEXT, filled)


@contextmanager
def handed(filled: Collection[str]) -> Iterator[None]:
    """Hold *filled*, the values a dispatch filled into the work it runs, while that work runs.

    The work writes records of its own as it runs: a command it refuses is audited with the
    command, a check that cannot run is logged with the command and what it printed, a request is
    audited with its URL. Each of those writers masks by shape what it is given (the floors
    ``security.mask_child_output`` and ``security.MaskingFormatter``, and ``net.client.audit``),
    and by :func:`masked_here` these values, which no shape finds. Held as given, never copied: a
    step's bindings fill its values in as its dispatcher resolves them, after the hold begins. And
    held beside what an enclosing dispatch holds, never in its place: work dispatched inside other
    work can be handed what that work was."""
    token = _HANDED.set((*_HANDED.get(), filled))
    try:
        yield
    finally:
        _HANDED.reset(token)


def masked_here(text: str) -> str:
    """*text* masked of every value the work running here was handed (:func:`handed`); *text* as
    it is where no work holds any."""
    held = [value for filled in _HANDED.get() for value in filled]
    return redact_known_values(text, held) if held else text


def _copied(obj: Any, changes: dict[str, Any]) -> Any:
    """*obj* with *changes*, as a copy (a dataclass's own copy, any other object's shallow one);
    *obj* itself when there are none."""
    if not changes:
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.replace(obj, **changes)
    copied = copy.copy(obj)
    for name, value in changes.items():
        setattr(copied, name, value)
    return copied
