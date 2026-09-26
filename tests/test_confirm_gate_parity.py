"""Destructive consent is ONE predicate, and `confirm: "false"` is never a yes (issue 3000).

``bool("false")`` is ``True``, so a JSON body that literally says do-not-confirm read as
confirmed. ``POST /api/knowledge/items/{id}/merge`` with ``{"confirm": "false"}`` answered 200
and **deleted** the merged-away item; the same shape sat on the tag merge (whose own comment
calls it "strictly more destructive than delete_tag"), the restructure apply, the durability
history revert, the task-list reset and the lexicon wipe. Nine sibling doors on the same
gateway required the literal ``true`` and refused the identical body, so the tree carried two
incompatible answers to one question.

**Why this file is a ban and not a checklist.** Issue 2983 was the same defect on a different
field: eight of nine doors were fixed, the per-route rail went green, and the ninth stayed
broken because nothing asked whether the pattern still existed anywhere. The flag half of
:mod:`personalclaw.safety_flags` has the same blind spot today —
``test_safety_flags_reject_truthy_strings`` enumerates ``(module, flag)`` pairs, so the whole
``confirm`` family was invisible to it and it read green throughout. So the structural test
below does not list the twenty-four doors. It asserts that **no module in ``src/`` other than
``safety_flags`` reads a confirm field at all**, which is a claim about the door added tomorrow
as much as the ones fixed today.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import personalclaw
from personalclaw.safety_flags import (
    CONFIRM_FIELDS,
    confirm_granted,
    confirm_granted_query,
)

SRC = pathlib.Path(personalclaw.__file__).resolve().parent

#: The one module allowed to read a confirm field — it *is* the predicate.
OWNER = "safety_flags.py"


# ── The predicate ───────────────────────────────────────────────────────────


class TestTheBodyPredicate:
    """`confirm` is granted by the JSON literal `true`, and by nothing else."""

    @pytest.mark.parametrize(
        "value",
        [
            "false",  # the reproduced defect: a stringified NO
            "False",
            "0",
            "no",
            "off",
            {"nested": 1},  # every non-empty container was a yes under truthiness
            ["x"],
            1,  # unambiguous, but still not the literal
            1.0,
            "1",
            "true",  # refused ON PURPOSE — see the module docstring
            "TRUE",
            "yes",
            0,
            0.0,
            [],
            {},
            "",
            None,
        ],
    )
    def test_only_the_literal_true_is_consent(self, value):
        assert confirm_granted({"confirm": value}) is False

    def test_the_literal_true_is_consent(self):
        assert confirm_granted({"confirm": True}) is True

    def test_an_absent_field_is_not_consent(self):
        assert confirm_granted({}) is False
        assert confirm_granted({"merge_id": "x"}) is False

    @pytest.mark.parametrize("payload", [None, [], "confirm", 1, [{"confirm": True}], object()])
    def test_a_non_mapping_body_is_not_consent(self, payload):
        """A body that failed to parse must not be able to confirm anything, and a call site
        must not need its own `isinstance(body, dict)` guard to get that."""
        assert confirm_granted(payload) is False

    def test_a_named_field_is_read_not_the_default_one(self):
        """`confirm_cascade` is a second consent flag on the workflow-edit door; passing the
        field name must actually change which key is read, or the cascade gate would silently
        read a `confirm` the client never sent."""
        assert confirm_granted({"confirm_cascade": True}, "confirm_cascade") is True
        assert confirm_granted({"confirm_cascade": "true"}, "confirm_cascade") is False
        assert confirm_granted({"confirm": True}, "confirm_cascade") is False


class TestTheQueryPredicate:
    """A URL cannot carry a real boolean, so the query peer compares one documented spelling."""

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", " true ", "\ttrue\n"])
    def test_the_documented_spelling_is_consent(self, value):
        assert confirm_granted_query({"confirm": value}) is True

    @pytest.mark.parametrize(
        "value",
        ["false", "False", "0", "no", "", "1", "yes", "on", "truthy", "true1"],
    )
    def test_everything_else_is_a_refusal(self, value):
        """`1`/`yes` included: `api_durability_import` accepted them, no caller in the tree, the
        frontend or the docs ever sent them, and a door that overwrites a home is the wrong
        place to keep an undocumented synonym alive."""
        assert confirm_granted_query({"confirm": value}) is False

    def test_an_absent_param_is_not_consent(self):
        assert confirm_granted_query({}) is False

    @pytest.mark.parametrize("query", [None, "confirm=true", 7])
    def test_an_unreadable_query_is_not_consent(self, query):
        assert confirm_granted_query(query) is False

    def test_a_non_string_value_is_not_consent(self):
        """A MultiDict can hold a non-string if something built it by hand; `True` is not the
        `"true"` this predicate is for, and coercing it would re-introduce the guess."""
        assert confirm_granted_query({"confirm": True}) is False


# ── The ban: nobody else may read a confirm field ───────────────────────────


def _confirm_reads(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every read of a :data:`CONFIRM_FIELDS` key in *path*, as ``(lineno, source)``.

    Both spellings a call site could use: ``x.get("confirm")`` and ``x["confirm"]``. A
    truthiness wrapper (``bool(...)``, ``if not ...``) does not need its own detector — the
    READ is what is banned, so there is nothing left for a wrapper to be wrong about.
    """
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        key: str | None = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            key = node.args[0].value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            key = node.slice.value
        if key in CONFIRM_FIELDS:
            found.append((node.lineno, ast.unparse(node)))
    return found


def test_no_module_outside_safety_flags_reads_a_confirm_field():
    """The whole point. A door that reads `confirm` itself has its own coercion, and a coercion
    nobody centralised is a coercion that will disagree with the other twenty-three."""
    offenders = [
        f"{path.relative_to(SRC)}:{lineno}  {src}"
        for path in sorted(SRC.rglob("*.py"))
        if path.name != OWNER
        for lineno, src in _confirm_reads(path)
    ]
    assert not offenders, (
        "These read a destructive-consent field directly instead of calling "
        "safety_flags.confirm_granted() / confirm_granted_query():\n  " + "\n  ".join(offenders)
    )


def test_the_owner_module_is_actually_where_the_predicate_lives():
    """Guards the test above against becoming vacuous by renaming: if the predicate moved out of
    `OWNER`, the ban would be exempting a file that no longer defines anything."""
    owner = SRC / OWNER
    assert owner.is_file()
    assert confirm_granted.__module__ == "personalclaw.safety_flags"
    assert confirm_granted_query.__module__ == "personalclaw.safety_flags"


def test_every_destructive_door_calls_the_predicate():
    """The ban's other half. Banning the READ cannot notice a door that stopped gating at all,
    so this counts the CALLS: every module that used to read a confirm field must now call the
    predicate, and the total may not fall.

    The number is a floor, not an enumeration — a new door raises it and never lowers it.
    """
    callers: dict[str, int] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == OWNER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        n = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("confirm_granted", "confirm_granted_query")
        )
        if n:
            callers[str(path.relative_to(SRC))] = n

    total = sum(callers.values())
    assert total >= 24, f"only {total} confirm gates call the predicate: {callers}"
    # Each surface that owned a door still owns one — a door deleted wholesale (rather than
    # converted) would drop off this list and the count above alone would not say which.
    for expected in (
        "dashboard/handlers/knowledge.py",
        "dashboard/handlers/durability.py",
        "dashboard/handlers/doctor.py",
        "dashboard/handlers/packs.py",
        "dashboard/handlers/core.py",
        "dashboard/handlers/browse_mirror.py",
        "dashboard/handlers/model_downloads.py",
        "dashboard/handlers/security_credentials.py",
        "dashboard/chat_file_rewind.py",
        "lexicon/handlers.py",
        "tasks/hierarchy_handlers.py",
        "mcp_automation.py",
        "mcp_workflows.py",
        "workflows/handlers.py",
    ):
        assert expected in callers, f"{expected} no longer gates on the shared predicate"


def test_the_app_install_doors_gate_on_a_reviewed_digest_not_on_confirm():
    """`dashboard/handlers/apps.py` left the list above by CONVERSION, not deletion — and this is
    what says so, so its absence there cannot hide a door that stopped gating.

    Its two doors (`POST /api/apps` and `POST /api/apps/{name}/update`) called the predicate, and a
    clean-scanning app then installed on no confirmation at all. They now commit only against the
    `consent` digest `POST /api/apps/preview` returned for the reviewed bytes — a string a boolean
    cannot forge — so `confirm: true` must NOT be a way in, and the predicate must not be called
    there. The behaviour (a bare or `confirm: true` POST is a 409 that installs nothing) is pinned
    in `test_app_install_needs_consent.py`; this pins the structure the list above used to.
    """
    path = SRC / "dashboard" / "handlers" / "apps.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doors = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in ("api_app_install", "api_app_update")
    }
    assert set(doors) == {"api_app_install", "api_app_update"}, "a door was renamed — re-point this"
    for name, fn in doors.items():
        called = {
            node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_consent_token" in called, f"{name} no longer reads the reviewed-bytes consent"
        assert not called & {
            "confirm_granted",
            "confirm_granted_query",
        }, f"{name} accepts a boolean confirm again — a yes that was never shown the review"
