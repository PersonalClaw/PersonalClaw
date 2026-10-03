"""Every door that writes a workflow definition runs the posture screen.

A step that approves its own tool calls (``approval_mode: "auto"``) or may change things
(``capability: "mutating"``) does so each time its workflow runs, unattended included, so only the
owner's own yes may put one on a step (``automation_posture.POSTURE_SPECS``). The editor's save
asked her, and nothing else did: the agent's ``workflow_author``, an accepted refiner diff, a prompt
card's template and a pack's template each wrote such a step with nobody asked. Now one writer
screens every save (``workflows.service._write_definition``: a step that would do more than the
same step of the stored definition does needs her yes), and this census holds the doors to it,
read off the source:

* **the store's save** (a definition provider's ``save_def``) is called in one place, that writer;
* **every claim of her yes** (an ``owner_allowed=`` argument) is a door that has one, declared in
  :data:`OWNER_YES` with where the yes comes from, so a new door that passes one fails until it
  says where its yes comes from;
* **every path into the definitions folder** (``workflows/defs``) is declared in
  :data:`DEFS_PATHS` with how what it writes is screened, so a new writer of that folder fails
  until it is screened.

The doors are driven too, so a declaration is not a claim: the one writer refuses a step that
would do more, the yes lets it through, and what arrives from another machine or a pack brings no
such step.
"""

from __future__ import annotations

import ast
import copy
import functools
from pathlib import Path

import pytest

import personalclaw
from personalclaw.workflows import defs as defs_mod

SRC = Path(personalclaw.__file__).resolve().parent

#: The one place a definition provider's ``save_def`` is called: the writer behind the screen.
WRITER = ("workflows/service.py", "_write_definition")

#: Every call that passes ``owner_allowed=``, by file and function, and where that yes comes from.
OWNER_YES: dict[tuple[str, str], str] = {
    ("workflows/service.py", "author_def"): "hands its caller's yes to the one writer",
    ("workflows/handlers.py", "_save_def"): (
        "her confirm on her editor's save, the consent dialog's answer (`confirm: true`)"
    ),
    ("workflows/batch_start.py", "_begin"): (
        "her Allow of a batch's one ask, which named each task and what it may change"
    ),
    ("workflows/definition_ask.py", "_save_allowed"): (
        "her Allow of an agent's save's one ask, which named each step and what it would then do"
    ),
    # An edit of a running workflow is held to the same screen (`mid_flight.posture_refusal`).
    ("workflows/handlers.py", "api_run_edit"): (
        "her confirm on her own edit of a running workflow, the consent dialog's answer"
    ),
    ("workflows/service.py", "edit_run"): "hands its caller's yes to the run's controller",
    ("workflows/controller.py", "submit_mutation"): "hands its caller's yes to the edit's screen",
}

#: Every function that builds a path into the definitions folder, and how what it writes there
#: is screened.
DEFS_PATHS: dict[tuple[str, str], str] = {
    ("workflows/native_defs.py", "defs_root"): (
        "the store's own folder: its writes are its `save_def`, which only the one writer calls"
    ),
    ("packs/import_.py", "component_path"): (
        "a pack's template, which arrives without a step key only her yes puts there "
        "(`_commit_file_component`)"
    ),
    ("packs/build.py", "_resolve_template"): "a read, for a pack's export",
}


@functools.lru_cache(maxsize=1)
def _trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    }


def _in_functions(tree: ast.Module):
    """Every node of *tree*, with the innermost function it is in ("" at module level)."""
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, function = stack.pop()
        yield node, function
        inner = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
        stack.extend((child, inner) for child in ast.iter_child_nodes(node))


@functools.lru_cache(maxsize=None)
def _docstrings(rel: str) -> frozenset[int]:
    """The ids of every docstring constant of module *rel*: prose, not a path anything builds."""
    out = set()
    for node in ast.walk(_trees()[rel]):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return frozenset(out)


def _sites(match) -> set[tuple[str, str]]:
    return {
        (rel, function)
        for rel, tree in _trees().items()
        for node, function in _in_functions(tree)
        if match(node, rel)
    }


def _saves_def(node: ast.AST, _rel: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "save_def"
    )


def _claims_her_yes(node: ast.AST, _rel: str) -> bool:
    return isinstance(node, ast.Call) and any(k.arg == "owner_allowed" for k in node.keywords)


def _texts(node: ast.AST) -> list[str]:
    """The string constants a node spells, an f-string's literal parts included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.Constant)]
    return []


def _names(node: ast.AST) -> set[str]:
    """The names and string constants a path expression is built from."""
    out: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            out.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            out.add(inner.attr)
        out.update(_texts(inner))
    return out


def _builds_a_defs_path(node: ast.AST, rel: str) -> bool:
    """A path into ``workflows/defs``: ``<…workflows…> / "defs"``, or a string naming it."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return "defs" in _texts(node.right) and bool(
            {"workflows", "workflows_dir"} & _names(node.left)
        )
    texts = _texts(node)
    return any("workflows/defs" in text for text in texts) and id(node) not in _docstrings(rel)


def test_the_census_reads_every_kind_of_door():
    """The vacuity floor: a scanner that finds nothing would hold every list trivially."""
    assert _sites(_saves_def), "no save_def call found at all: the matcher is broken"
    assert len(_sites(_claims_her_yes)) >= 7, _sites(_claims_her_yes)
    assert len(_sites(_builds_a_defs_path)) >= 3, _sites(_builds_a_defs_path)


def test_the_store_is_written_only_by_the_one_writer():
    """A ``save_def`` called anywhere else writes a definition the posture screen never read."""
    assert _sites(_saves_def) == {WRITER}, (
        "a definition is written around the posture screen; save it through "
        "`workflows.service.author_def` (or `_write_definition`), which asks the owner about a "
        f"step that would do more: {sorted(_sites(_saves_def) - {WRITER})}"
    )


def test_every_claim_of_her_yes_says_where_it_comes_from():
    found = _sites(_claims_her_yes)
    assert found == set(OWNER_YES), (
        "a save claims the owner's yes (`owner_allowed=`) that this census cannot place: "
        f"new {sorted(found - set(OWNER_YES))}, gone {sorted(set(OWNER_YES) - found)}. "
        "A yes is her confirm where she is shown the step, or her Allow of an ask that named it."
    )


def test_every_path_into_the_definitions_folder_is_screened():
    found = _sites(_builds_a_defs_path)
    assert found == set(DEFS_PATHS), (
        "a path into the definitions folder this census does not know: "
        f"new {sorted(found - set(DEFS_PATHS))}, gone {sorted(set(DEFS_PATHS) - found)}. "
        "What it writes there must be screened as every definition save is."
    )


# ── the doors, driven ───────────────────────────────────────────────────────────────────────────

#: One step whose agent approves its own tool calls.
LOOSE = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {
            "kind": "stage",
            "id": "fix",
            "label": "Fix the lint",
            "config": {"prompt": "Fix every lint error in src/app.", "approval_mode": "auto"},
        }
    ],
}


class _MemProvider(defs_mod.WorkflowDefProvider):
    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "aaa-census-test-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return list(self.saved.values())[offset : offset + limit], len(self.saved)

    async def get_def(self, name: str):
        return copy.deepcopy(self.saved.get(name))

    async def save_def(self, **fields):
        fields.setdefault("source", "user")
        fields["version"] = int((self.saved.get(fields["name"]) or {}).get("version") or 0) + 1
        self.saved[fields["name"]] = copy.deepcopy(dict(fields))
        return self.saved[fields["name"]]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    provider = _MemProvider()
    defs_mod.register_provider(provider)
    try:
        yield provider
    finally:
        defs_mod.unregister_provider("aaa-census-test-mem")


@pytest.mark.asyncio
async def test_the_writer_refuses_a_step_that_would_do_more_without_her_yes(store):
    from personalclaw.workflows import service

    refused = await service.author_def(name="lint-fixer", root=copy.deepcopy(LOOSE))

    assert refused["code"] == "WF_DEF_NEEDS_OWNER_YES", refused
    (step,) = refused["steps"]
    assert step["label"] == "Fix the lint" and step["keys"] == ["approval_mode"], step
    assert store.saved == {}


@pytest.mark.asyncio
async def test_an_accepted_diff_goes_through_the_same_writer(store):
    from personalclaw.workflows import service

    refused = await service.save_accepted_diff(
        {"name": "lint-fixer", "root": copy.deepcopy(LOOSE)}, ops=[]
    )

    assert refused["code"] == "WF_DEF_NEEDS_OWNER_YES", refused
    assert store.saved == {}


@pytest.mark.asyncio
async def test_her_yes_lets_it_through_and_a_resave_asks_nothing(store):
    from personalclaw.workflows import service

    saved = await service.author_def(
        name="lint-fixer", root=copy.deepcopy(LOOSE), owner_allowed=True
    )
    again = await service.author_def(name="lint-fixer", root=copy.deepcopy(LOOSE))
    published = await service.set_a2a_published("lint-fixer", True)

    assert saved["saved"] and again["saved"] and published["ok"], (saved, again, published)
    assert store.saved["lint-fixer"]["version"] == 3


def test_a_definition_from_another_machine_brings_no_such_step():
    """The device sync's arrival rule for the workflows folder (``durability.inventory``)."""
    from personalclaw.durability.inventory import INVENTORY

    (entry,) = [e for e in INVENTORY if e.id == "workflows"]
    row = {"id": "defs/lint-fixer/workflow", "data": {"name": "lint-fixer", "root": LOOSE}}
    assert entry.arrives is not None and entry.edit_arrives is not None
    arrived = entry.arrives(copy.deepcopy(row))
    assert "approval_mode" not in arrived["data"]["root"]["children"][0]["config"], arrived
