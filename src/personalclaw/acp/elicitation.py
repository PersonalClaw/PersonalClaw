"""An agent CLI's question to the user, as ACP's form elicitation carries it.

A client that advertises ``clientCapabilities.elicitation.form`` is one an agent may ask a question
of mid-turn: the agent sends ``elicitation/create`` with a message and a ``requestedSchema`` and
waits for ``{"action": "accept", "content": {...}}``, ``{"action": "decline"}`` or
``{"action": "cancel"}``. Claude Code's ACP adapter puts its ``AskUserQuestion`` tool on that
request when, and only when, the client advertises it (it lists the tool as disallowed otherwise):
one ``question_<n>`` property per question, a titled ``oneOf`` (or, for several choices, an array
of a titled ``anyOf``) whose ``const`` is the option the tool records, and beside each an optional
``question_<n>_custom`` string for an answer of her own. Accepted content is folded back into the
tool's answers; a decline tells the model she skipped; a cancel aborts the tool call.

This module reads that shape into the questions :mod:`personalclaw.owner_questions` asks, and maps
how one ended back onto the response. A form of any other shape (a tool server's own form, a model
switch the CLI asks about) is not a question this host puts to her, and is answered ``cancel``:
she was never asked, so neither an answer nor a refusal is claimed for her.

Pure: no I/O, no session state.
"""

from __future__ import annotations

import re
from typing import Any

#: The client capability that lets an agent ask, as ``initialize`` advertises it.
FORM_CAPABILITY: dict[str, Any] = {"elicitation": {"form": {}}}

CANCEL: dict[str, str] = {"action": "cancel"}
DECLINE: dict[str, str] = {"action": "decline"}

_QUESTION_KEY = re.compile(r"^question_(\d+)$")
_CUSTOM = "_custom"


def _text(value: object, cap: int) -> str:
    return str(value or "").strip()[:cap]


def _option(raw: object, label_cap: int, text_cap: int) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("const"), str) or not raw["const"]:
        return None
    value = raw["const"]
    return {
        "label": _text(raw.get("title") or value, label_cap),
        "description": _text(raw.get("description"), text_cap),
        "value": value,
    }


def questions_from_form(params: object) -> list[dict[str, Any]] | None:
    """The questions an ``elicitation/create`` asks, when it is an agent's question tool on a
    form (the shape above), with each question's ``key`` in the form; ``None`` for any other
    request. Capped as the tool's own questions are (``validation.validate_ask_user_question``)."""
    from personalclaw.validation import (
        _AUQ_LABEL_CAP,
        _AUQ_MAX_OPTIONS,
        _AUQ_MAX_QUESTIONS,
        _AUQ_TEXT_CAP,
    )

    if not isinstance(params, dict) or str(params.get("mode") or "form") != "form":
        return None
    schema = params.get("requestedSchema")
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return None
    indexed = sorted(
        (int(m.group(1)), key) for key in props if (m := _QUESTION_KEY.match(str(key)))
    )
    if not indexed or len(indexed) > _AUQ_MAX_QUESTIONS:
        return None
    # Every property is a question or the box beside one; a form that also asks for anything
    # else is not a question tool's, and answering part of it would be inventing the rest.
    if set(props) - {key for _, key in indexed} - {key + _CUSTOM for _, key in indexed}:
        return None
    message = _text(params.get("message"), _AUQ_TEXT_CAP)
    questions: list[dict[str, Any]] = []
    for _, key in indexed:
        prop = props[key]
        if not isinstance(prop, dict):
            return None
        multi = prop.get("type") == "array"
        if multi:
            items = prop.get("items")
            raw_options = items.get("anyOf") if isinstance(items, dict) else None
        elif prop.get("type") == "string":
            raw_options = prop.get("oneOf")
        else:
            return None
        if not isinstance(raw_options, list) or not raw_options:
            return None
        options = [
            _option(o, _AUQ_LABEL_CAP, _AUQ_TEXT_CAP) for o in raw_options[:_AUQ_MAX_OPTIONS]
        ]
        if any(o is None for o in options):
            return None
        # One question carries its words on the message; several carry them on each field.
        text = _text(prop.get("description"), _AUQ_TEXT_CAP) or (
            message if len(indexed) == 1 else ""
        )
        if not text:
            return None
        companion = props.get(key + _CUSTOM)
        questions.append(
            {
                "key": key,
                "question": text,
                "header": _text(prop.get("title"), _AUQ_LABEL_CAP),
                "multiSelect": multi,
                "free_text": isinstance(companion, dict) and companion.get("type") == "string",
                "options": options,
            }
        )
    return questions


def form_response(questions: list[dict[str, Any]], outcome: Any) -> dict[str, Any]:
    """The ``elicitation/create`` response for how the question ended
    (:class:`personalclaw.owner_questions.Outcome`): her answers as the form's content, her Skip
    as a decline, and a question that ended with no answer as a cancel."""
    from personalclaw.owner_questions import ANSWERED, SKIPPED

    if outcome.kind == SKIPPED:
        return dict(DECLINE)
    if outcome.kind != ANSWERED:
        return dict(CANCEL)
    content: dict[str, Any] = {}
    for q, a in zip(questions, outcome.answers):
        chosen = [q["options"][i]["value"] for i in a.selected]
        if chosen:
            content[q["key"]] = chosen if q["multiSelect"] else chosen[0]
        if a.other:
            content[q["key"] + _CUSTOM] = a.other
    return {"action": "accept", "content": content}
