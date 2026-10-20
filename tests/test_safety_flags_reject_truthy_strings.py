"""A safety flag must not be enabled by a string whose author meant to disable it.

``bool("false")`` is ``True`` and ``bool("0")`` is ``True``, so five flags that decide whether a
safety control applies were **enabled** by a value plainly written to disable them — and the failure
landed in the unsafe direction with nothing logged.

The five, and where the value comes from:

* ``hooks.auto_approve_subagent_spawn`` / ``auto_approve_subagent_tools`` — the hooks JSON file the
  user hand-edits. JSON takes both ``false`` and ``"false"``, and the quoted form is the habit
  anyone arriving from YAML or a shell env brings.
* ``skip_preflight`` / ``always_allow`` — an **HTTP request body**: whatever the client sent.
* ``unattended_suppress`` — config.

This module owns the one spelling table of the boolean-as-text family: every way a boolean has
been sent, and what each reader makes of it. The readers disagree by design — a blank is a no to
``strict_bool`` and neither to ``yes_or_no``, a number is a yes or a no to ``strict_bool`` and
neither to ``yes_or_no`` — so each row names the answer of each. The doors are held to their
readers by the two AST censuses (``test_tool_boolean_census.py``, ``test_request_boolean_census.py``
and its rail) and by :data:`GUARDED_FLAGS` below, and each family of doors is driven end to end
with the representative spellings this module names, three times: a yes, a no, and the values
that spell neither, among them the blank and the number the readers part on.

The stored switches are the family here: a file or a row the product reads back, through
``strict_bool``. The **CALL SITES** are asserted too, not just the helper: a correct `strict_bool`
is worthless if a gate still calls bare `bool()`, which is exactly the shape of the original
defect. A file is read through ``strict_bool``; a request body is held stricter, through
``request_validation.bool_field``, which refuses anything but a JSON boolean.
"""

from __future__ import annotations

import ast
import logging
import pathlib
from typing import NamedTuple

import pytest
from table_rows import row

from personalclaw import hooks
from personalclaw.request_validation import (
    RequestValidationError,
    bool_field,
    optional_bool,
    require_bool,
)
from personalclaw.safety_flags import BOOL_FALSE_WORDS, BOOL_TRUE_WORDS, strict_bool, yes_or_no
from personalclaw.workflows.contracts import coerce_declared_inputs

pytestmark = pytest.mark.security


SRC = pathlib.Path(hooks.__file__).resolve().parent

#: What ``strict_bool`` answers for a value it cannot read: the default its caller passed.
DEFAULT = "the default"
#: What a declared workflow input, and a request body, answer for a value they will not take.
REFUSED = "refused"
#: What a declared workflow input answers for a blank or a null: unset, left to the run's defaults.
UNSET = "unset"


class Spelling(NamedTuple):
    """One way a boolean has been sent, and what each reader makes of it."""

    value: object
    #: ``safety_flags.strict_bool``: a stored switch.
    strict: object
    #: ``safety_flags.yes_or_no``: a field declared boolean that a model sends.
    words: bool | None
    #: ``workflows.contracts.coerce_declared_inputs``: a workflow's declared ``boolean`` input.
    declared: object
    #: ``request_validation.bool_field`` / ``require_bool`` / ``optional_bool``: a request body.
    request: object
    #: Which branch of the readers the value takes.
    kind: str


def _word(value: str, answer: bool) -> Spelling:
    return Spelling(value, answer, answer, answer, REFUSED, "a word")


def _forms(word: str) -> list[str]:
    """The word in each case a person, a form or a template sends it, bare and padded."""
    cases = [word, word.title(), word.upper()]
    return list(dict.fromkeys([*cases, *(f" {case} " for case in cases)]))


#: Something no reader recognises, which is not a container or a number either.
_OPAQUE = object()

#: Every value that is not a real boolean or a word of the vocabulary, each with its readings.
_NOT_WORDS = [
    Spelling("", False, None, UNSET, REFUSED, "a blank"),
    Spelling("   ", False, None, UNSET, REFUSED, "a blank"),
    Spelling("maybe", DEFAULT, None, REFUSED, REFUSED, "another word"),
    Spelling("yep", DEFAULT, None, REFUSED, REFUSED, "another word"),
    Spelling("met", DEFAULT, None, REFUSED, REFUSED, "another word"),
    Spelling("sometimes", DEFAULT, None, REFUSED, REFUSED, "another word"),
    Spelling(1, True, None, REFUSED, REFUSED, "a number"),
    Spelling(0, False, None, REFUSED, REFUSED, "a number"),
    Spelling(1.5, True, None, REFUSED, REFUSED, "a number"),
    Spelling(None, DEFAULT, None, UNSET, REFUSED, "a null"),
    Spelling([], DEFAULT, None, REFUSED, REFUSED, "a list"),
    Spelling(["false"], DEFAULT, None, REFUSED, REFUSED, "a list"),
    Spelling({}, DEFAULT, None, REFUSED, REFUSED, "an object"),
    Spelling({"met": True}, DEFAULT, None, REFUSED, REFUSED, "an object"),
    Spelling({"a": 1}, DEFAULT, None, REFUSED, REFUSED, "an object"),
    Spelling(_OPAQUE, DEFAULT, None, REFUSED, REFUSED, "anything else"),
]

#: The branches the readers take on a value, each of which the table must keep a row for.
KINDS = frozenset(
    {"a boolean", "a word", "a blank", "another word", "a number", "a null", "a list"}
    | {"an object", "anything else"}
)

#: The one spelling table: a real boolean, every word of the vocabulary in three forms, and every
#: other value a door of this family has been sent.
SPELLINGS: list[Spelling] = [
    Spelling(True, True, True, True, True, "a boolean"),
    Spelling(False, False, False, False, False, "a boolean"),
    *[_word(form, True) for word in sorted(BOOL_TRUE_WORDS) for form in _forms(word)],
    *[_word(form, False) for word in sorted(BOOL_FALSE_WORDS - {""}) for form in _forms(word)],
    *_NOT_WORDS,
]

#: What a door that reads words is driven with for a yes and for a no, each a row above.
WORD_YES, WORD_NO = "yes", "false"
#: What it is driven with for a value that spells neither: another word, a blank and a number.
#: Each is neither to ``yes_or_no``, and ``strict_bool`` answers each differently (its default,
#: a no, a yes), so a door read by ``strict_bool`` in its place does not give its safe value, or
#: does not refuse, for at least one of them.
WORD_NEITHER = ("maybe", "", 1)
#: The same where a blank is the field left out (``grep`` and ``glob``'s ``ignore_case`` leave
#: the case to smart case): a zero is ``strict_bool``'s no in its place.
WORD_NEITHER_NOT_BLANK = ("maybe", 0, 1)
#: What a door that takes only a JSON boolean is driven with: the two booleans, and the word
#: ``"false"``, which it must refuse rather than read.
JSON_YES, JSON_NO, JSON_REFUSED = True, False, "false"


def _row_name(spelling: Spelling) -> str:
    return "an opaque object" if spelling.value is _OPAQUE else repr(spelling.value)


def _row(value: object) -> Spelling:
    """The table's row for *value*, matched on its type too, so ``1`` is not ``True``."""
    [spelling] = [s for s in SPELLINGS if type(s.value) is type(value) and s.value == value]
    return spelling


def spells(value: object) -> bool | None:
    """What *value* spells to a door that reads words, as its row says: a door's test sets up
    the state a row must change from this, not from the reader it is testing."""
    return _row(value).words


def test_the_table_holds_the_whole_vocabulary_and_every_kind():
    # 2 booleans, 48 forms of the 10 words, and 16 other values.
    assert len(_NOT_WORDS) == 16, "a value that is not a word was dropped from the table"
    assert len(SPELLINGS) == 66, "a spelling was dropped from the table"
    values = [_row_name(s) for s in SPELLINGS]
    assert len(values) == len(set(values)), "a spelling is in the table twice"
    # Spelled out here rather than through `_forms`, so a form the table stops making fails.
    vocabulary = {
        form
        for word in BOOL_TRUE_WORDS | (BOOL_FALSE_WORDS - {""})
        for case in (word, word.title(), word.upper())
        for form in (case, f" {case} ")
    }
    missing = sorted(vocabulary - {s.value for s in SPELLINGS if isinstance(s.value, str)})
    assert not missing, f"words of the vocabulary with no row: {missing}"
    assert {s.kind for s in SPELLINGS} == KINDS, "a branch of the readers lost its last row"
    for value, answer in ((WORD_YES, True), (WORD_NO, False)):
        assert spells(value) is answer, f"the door spelling {value!r} no longer reads {answer}"
    for neither in (WORD_NEITHER, WORD_NEITHER_NOT_BLANK):
        for value in neither:
            assert spells(value) is None, f"the door spelling {value!r} no longer spells neither"
        # In this order: the stored switches below name them so.
        assert [_row(value).strict for value in neither] == [DEFAULT, False, True], neither
    assert _row(JSON_REFUSED).request == REFUSED


def _strict_reading(value: object, caplog: pytest.LogCaptureFixture) -> object:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="personalclaw.safety_flags"):
        safe, unsafe = (strict_bool(value, field="probe.flag", default=d) for d in (False, True))
    if (safe, unsafe) == (False, True):
        warned = [r for r in caplog.records if "probe.flag" in r.getMessage()]
        # Unreadable must mean "safe", and must not be silent; a null alone may say nothing.
        assert warned or value is None, f"{value!r} took the default without a WARNING naming it"
        return DEFAULT
    assert safe is unsafe, f"{value!r} read {safe} under one default and {unsafe} under the other"
    return safe


def _declared_reading(value: object) -> object:
    coerced, errors = coerce_declared_inputs(
        {"inputs": {"flag": {"type": "boolean"}}}, {"flag": value}
    )
    if errors:
        assert len(errors) == 1 and errors[0].startswith("flag: expected boolean"), errors
        return REFUSED
    if coerced["flag"] is value and not isinstance(value, bool):
        return UNSET
    return coerced["flag"]


def _request_reading(value: object) -> object:
    readings: list[object] = []
    body = {"flag": value}
    for read in (
        lambda: bool_field(body, "flag", default=False),
        lambda: require_bool(body, "flag"),
        lambda: optional_bool(body, "flag"),
    ):
        try:
            readings.append(read())
        except RequestValidationError as refused:
            assert (refused.code, refused.status) == ("field_not_a_boolean", 400)
            assert "flag" in refused.message, refused.message
            readings.append(REFUSED)
    assert (
        len(set(map(repr, readings))) == 1
    ), f"the request readers disagree on {value!r}: {readings}"
    return readings[0]


def test_every_spelling_reads_as_its_row_says_in_every_reader(subtests, caplog):
    for spelling in SPELLINGS:
        with row(subtests, sent=_row_name(spelling)):
            value = spelling.value
            assert _strict_reading(value, caplog) == spelling.strict, "strict_bool"
            assert yes_or_no(value) is spelling.words, "yes_or_no"
            assert _declared_reading(value) == spelling.declared, "a declared input"
            assert _request_reading(value) == spelling.request, "a request body"


def test_the_old_behaviour_really_was_wrong():
    """The vacuity floor: if `bool("false")` were already False, none of this would matter and
    this whole file would be asserting nothing."""
    assert bool("false") is True
    assert bool("0") is True


# ── The stored switches, end to end ──────────────────────────────────────────────────────────


def _hooks(field: str, sent: object) -> bool:
    return getattr(hooks.HooksConfig.from_dict({field: sent}), field)


def _stored_trigger(field: str, sent: object) -> bool:
    from personalclaw.triggers.models import parse_trigger

    trigger, _ = parse_trigger(
        {
            "id": "clock:backup",
            "name": "Nightly backup reminder",
            "kind": "clock",
            "spec": {"kind": "cron", "expr": "0 3 * * *"},
            field: sent,
        }
    )
    return getattr(trigger, field)


#: ``(door, field, read)``: each switch the product reads back from a file or a row it stored,
#: through its real loader. A row an agent's patch wrote as text reads as what it said; one nothing
#: can read is off, because a switch that runs things on its own must not come on by a guess.
STORED_SWITCHES = [
    ("the hooks file", "auto_approve_subagent_spawn", _hooks),
    ("the hooks file", "auto_approve_subagent_tools", _hooks),
    ("a stored automation", "enabled", _stored_trigger),
    ("a stored automation", "yield_to_user", _stored_trigger),
]


#: A stored switch reads through ``strict_bool``, so of what spells neither to a door that
#: reads words, the blank is a no here and the number a yes (rows of the table): only the other
#: word spells neither.
_OTHER_WORD, _BLANK, _NUMBER = WORD_NEITHER


@pytest.mark.parametrize(
    ("sent", "reads"),
    [
        ((WORD_YES, True, _NUMBER), True),
        ((WORD_NO, False, _BLANK), False),
        ((_OTHER_WORD,), False),
    ],
    ids=["yes", "no", "neither"],
)
def test_a_stored_switch_reads_as_the_word_it_spells(subtests, sent, reads):
    """Three checks for the family: a yes is on, a no is off, and what spells neither is off. A
    yes and a no are each sent as the word, as the real boolean, which is still itself, and as
    the number or the blank ``strict_bool`` reads as one."""
    assert len(STORED_SWITCHES) == 4, "a stored switch was dropped from the table"
    assert len(STORED_SWITCHES) == len({(door, field) for door, field, _ in STORED_SWITCHES})
    for door, field, read in STORED_SWITCHES:
        for value in sent:
            with row(subtests, door=door, field=field, sent=repr(value)):
                assert read(field, value) is reads, f"{door}'s {field} sent {value!r}"


def test_a_stored_automation_that_never_said_is_on_and_does_not_yield():
    from personalclaw.triggers.models import parse_trigger

    trigger, _ = parse_trigger(
        {
            "id": "clock:b",
            "name": "B",
            "kind": "clock",
            "spec": {"kind": "cron", "expr": "0 3 * * *"},
        }
    )
    assert (trigger.enabled, trigger.yield_to_user) == (True, False)


# ── The call sites ───────────────────────────────────────────────────────────────────────────

#: ``(module, flag, reader)`` for every safety flag that reads user-controlled data.
GUARDED_FLAGS = [
    ("hooks.py", "auto_approve_subagent_spawn", "strict_bool"),
    ("hooks.py", "auto_approve_subagent_tools", "strict_bool"),
    ("workflows/handlers.py", "skip_preflight", "bool_field"),
    ("workflows/handlers.py", "always_allow", "bool_field"),
    ("workflows/engine.py", "unattended_suppress", "strict_bool"),
]


def _reads(module_rel: str, flag: str, reader: str) -> tuple[list[int], list[int]]:
    """``(bare, strict)``: the lines of *module_rel* that read *flag* with ``bool()``, and with
    *reader*."""
    path = SRC / module_rel
    assert path.exists(), f"{module_rel} moved — re-point this rail"
    tree = ast.parse(path.read_text(), filename=str(path))
    bare: list[int] = []
    strict: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        # does this call mention the flag anywhere inside it?
        if name in ("bool", reader) and flag in ast.dump(node):
            (strict if name == reader else bare).append(node.lineno)
    return bare, strict


def test_every_guarded_flag_is_read_through_its_strict_reader_not_bare_bool(subtests):
    """The half that catches a regression: the helper being right does not make the gates right."""
    assert len(GUARDED_FLAGS) == 5, "a guarded flag was dropped from the table"
    assert len(GUARDED_FLAGS) == len({(m, f) for m, f, _ in GUARDED_FLAGS})
    for module_rel, flag, reader in GUARDED_FLAGS:
        with row(subtests, module=module_rel, flag=flag):
            bare, strict = _reads(module_rel, flag, reader)
            assert not bare, (
                f"{module_rel}:{bare} reads the safety flag {flag!r} with bare bool(). "
                f'bool("false") is True, so a value written to DISABLE the control enables it. '
                f"Use {reader}(...)."
            )
            assert strict, (
                f"{module_rel} no longer reads {flag!r} through {reader} — either the flag was "
                f"renamed or the guard was dropped. A rail that matches nothing is not a rail."
            )
