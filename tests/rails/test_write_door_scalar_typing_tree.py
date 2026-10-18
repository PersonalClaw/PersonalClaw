"""The source-tree checks split from
``tests/test_write_door_scalar_typing.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pytest
import source_index
import write_door_scalar_typing_shared
from rails_repo import REPO
from write_door_scalar_typing_shared import _CoercionScan

_clear_store = write_door_scalar_typing_shared._clear_store


_SRC = REPO / "src" / "personalclaw"


#: ``(id, module, handler, field, validator)`` — the door table this change closed.
#:
#: ``validator`` is the shape the door's semantics demand, and the three are NOT
#: interchangeable: ``require_string`` for a field the resource cannot exist without,
#: ``optional_string`` for an update door re-asking its create door's rule about a field
#: the caller may simply omit, ``string_field`` for a field whose BLANK is legal and gets
#: derived downstream (an inbox proposal's title falls back to the kind's label) but whose
#: wrong TYPE must still be refused rather than stored as a repr.
#:
#: Ids are short deliberately: a parametrize id built from a payload once reached 100k
#: chars and truncated the CI log that was supposed to name the failure (#2720).
_DOORS: tuple[tuple[str, str, str, str, str], ...] = (
    ("artifact-create", "artifacts/handlers.py", "api_artifacts_create", "name", "require_string"),
    (
        "artifact-folder-update",
        "artifacts/handlers.py",
        "api_artifact_folder_update",
        "name",
        "optional_string",
    ),
    (
        "chat-folder-create",
        "dashboard/chat_folders.py",
        "api_chat_folder_create",
        "name",
        "require_string",
    ),
    (
        "chat-folder-update",
        "dashboard/chat_folders.py",
        "api_chat_folder_update",
        "name",
        "optional_string",
    ),
    ("chat-tag-update", "dashboard/chat_tags.py", "api_chat_tag_update", "name", "optional_string"),
    (
        "session-rename",
        "dashboard/chat_title.py",
        "api_chat_session_rename",
        "title",
        "require_string",
    ),
    ("file-create", "dashboard/handlers/files.py", "api_file_create", "name", "require_string"),
    (
        "loop-rename",
        "dashboard/handlers/loop_routes.py",
        "api_loop_update",
        "name",
        "require_string",
    ),
    ("mcp-toggle", "dashboard/handlers/mcp.py", "api_mcp_toggle", "name", "require_string"),
    (
        "entity-create",
        "dashboard/handlers/memory.py",
        "api_memory_entity_create",
        "name",
        "require_string",
    ),
    (
        "entity-proposal",
        "dashboard/handlers/memory.py",
        "api_memory_entity_proposals",
        "name",
        "require_string",
    ),
    (
        "provider-create",
        "dashboard/handlers/providers.py",
        "api_provider_create",
        "name",
        "require_string",
    ),
    ("secret-put", "dashboard/handlers/secrets.py", "api_secrets_put", "name", "require_string"),
    (
        "overlay-revert",
        "dashboard/handlers/skills.py",
        "api_skill_overlay_revert",
        "name",
        "require_string",
    ),
    (
        "skill-promote",
        "dashboard/handlers/skills.py",
        "api_ephemeral_skill_promote",
        "title",
        "string_field",
    ),
    ("tool-toggle", "dashboard/handlers/tools.py", "api_tools_toggle", "name", "require_string"),
    (
        "voice-migrate",
        "dashboard/handlers/voice_profiles.py",
        "api_voice_migrate",
        "name",
        "string_field",
    ),
    (
        "proposal-create",
        "dashboard/handlers_inbox.py",
        "api_inbox_proposal_create",
        "title",
        "string_field",
    ),
    ("wf-def-save", "workflows/handlers.py", "api_def_save", "name", "require_string"),
    ("wf-run-start", "workflows/handlers.py", "api_run_start", "name", "require_string"),
)


def _handler(module: str, name: str) -> ast.AST:
    tree = source_index.tree(_SRC / module)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{module} no longer defines {name}() — the door table is stale")


def _validator_calls(handler: ast.AST) -> set[tuple[str, str]]:
    """``{(validator_name, field)}`` for every shared-validator call inside *handler*."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"require_string", "optional_string", "string_field"}:
            continue
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            # A field name is a string literal; narrowing here (rather than trusting
            # `ast.Constant.value`, which is typed as any literal) keeps the set's own
            # type honest and ignores a call whose second argument is not a name at all.
            if isinstance(node.args[1].value, str):
                found.add((node.func.id, node.args[1].value))
    return found


def _presence_gated(handler: ast.AST, field: str) -> bool:
    """True when *handler* tests ``field in body`` — the other spelling of OPTIONAL.

    ``optional_string(body, f)`` and ``require_string(body, f) if f in body else None``
    ask the SAME two questions in the same order: is the field present, and if so is it
    a real non-blank ``str``. The presence test is what makes the second form optional,
    so it is the thing this helper looks for: a door that calls ``require_string`` with
    no presence test genuinely REQUIRES the field and must still fail the row below.
    """
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and node.left.value == field
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
        ):
            return True
    return False


@pytest.mark.parametrize(
    ("module", "handler", "field", "validator"),
    [row[1:] for row in _DOORS],
    ids=[row[0] for row in _DOORS],
)
def test_door_routes_its_name_through_the_shared_validator(
    module: str, handler: str, field: str, validator: str
) -> None:
    """The door asks the ONE shared question about its own ``name``/``title``."""
    node = _handler(module, handler)
    calls = _validator_calls(node)
    accepted = f"{validator}(body, {field!r})"
    satisfied = (validator, field) in calls
    if not satisfied and validator == "optional_string":
        # The OPTIONAL semantic, spelled the other way. Three update doors (chat-tag,
        # chat-folder, artifact-folder) landed on `main` as a presence-gated
        # `require_string` inside a local `RequestValidationError` wrapper, because those
        # modules answer flat everywhere and a nested refusal for `name` alone would split
        # one endpoint across two envelope shapes. That is the same rule this row exists to
        # pin — same validator, same refusals — so the row accepts both spellings and keeps
        # its teeth via `_presence_gated`, which is the half that makes the form optional.
        satisfied = ("require_string", field) in calls and _presence_gated(node, field)
        accepted += f" (or a presence-gated require_string(body, {field!r}))"
    assert satisfied, (
        f"{module}:{handler} no longer calls {accepted}. "
        f"Found: {sorted(calls) or 'nothing'}. A door that reads its name any other way "
        f"invents a third answer for a wrong type — that is #3001/#456."
    )


#: The ONLY surviving ``.strip()``-on-a-raw-body-read sites, and why they are not the
#: defect: ``api_themes_create``/``api_theme_detail`` both run ``_validate_theme_data``
#: FIRST, which refuses a non-string ``name`` with a message naming the field before the
#: ``.strip()`` is ever reached. Their refusal is correct; only its envelope is the older
#: flat ``{"error": ...}`` shape, and reshaping a correct refusal is an error-envelope
#: decision this change does not make.
#:
#: The exact count is the point. A pair list with no number lets a third site join the
#: exception quietly; a number makes any new site red.
_GUARDED_ELSEWHERE: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("dashboard/handlers/agents.py", "api_themes_create", "name"),
        ("dashboard/handlers/agents.py", "api_theme_detail", "name"),
    }
)


def _scan_tree() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path, tree in source_index.package_trees():
        scan = _CoercionScan()
        scan.visit(tree)
        rel = path.relative_to(_SRC).as_posix()
        found |= {(rel, fn, field) for fn, field in scan.hits}
    return found


def test_no_unguarded_name_or_title_coercion_survives() -> None:
    """No ``api_*`` handler coerces a ``name``/``title`` it never type-checked."""
    survivors = _scan_tree()
    assert survivors == _GUARDED_ELSEWHERE, (
        "write doors coercing a name/title outside the shared validator:\n  "
        + "\n  ".join(f"{m}:{fn} [{f}]" for m, fn, f in sorted(survivors - _GUARDED_ELSEWHERE))
        + "\nEach one invents its own answer for a wrong type — a persisted Python repr, or an "
        "AttributeError the request boundary flattens into a field-less 400. Route it through "
        "personalclaw.request_validation instead (#3001, #456)."
    )
    assert len(survivors) == len(_GUARDED_ELSEWHERE) == 2
