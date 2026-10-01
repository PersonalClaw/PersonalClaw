"""A batch leaf is read by the types its contract declares, and its boundary says what it fences.

``subagent_run`` with two or more tasks compiles them into one run, each task a leaf contract. A
model sends that contract the way the tool's schema shows it, so the schema shows the contract,
and every declaration is read by its own type:

* ``writes`` is a list of paths. One path sent as text is a one-item list, never the characters of
  a string; a value of any other type is refused, naming the field and the shape it takes.
* ``capability`` is ``research`` or ``mutating``. Any other word is refused, not read as research.
* A key the contract does not have is refused, naming the ones it does.

The boundary has two parts: what the worker is told (``boundary``, prose) and the paths it must not
write (``off_limits``). Only the second is compared with ``writes``: a sentence cannot say which of
the paths it names are fenced and which are allowed, so prose that names the folder a leaf is told
to work in no longer reads as a fence around it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import mcp_subagents
from personalclaw.workflows import batch_compile
from personalclaw.workflows.batch_compile import Capability, compile_batch

CONTRACT = {
    "objective": "decide whether the regression test pins the fix",
    "output_format": "a numbered list of findings with file and line",
    "boundary": "do not commit, push or edit files in the main checkout",
}


def _item(task: str, **kw) -> dict:
    return {"task": task, **CONTRACT, **kw}


def leaf_from_item(item: dict):
    """One `tasks[]` item as the tool reads it."""
    return mcp_subagents._to_leaf(item["task"], item, "")


def _compile(*items: dict):
    return compile_batch([leaf_from_item(item) for item in items])


def _codes(result) -> list[str]:
    return [f.code for f in result.findings]


def _messages(result) -> str:
    return "\n".join(f.message for f in result.findings)


# ── writes ────────────────────────────────────────────────────────────────────────────────────


def test_one_path_sent_as_text_is_a_one_item_list():
    """🔴 Before: "declares writes to t, e, m, p, o, r, a, r, y, …" and a `multi_writer` warning
    for every letter that appeared twice."""
    leaf = leaf_from_item(_item("review the test", writes="/tmp/review-copy"))
    assert leaf.writes == ["/tmp/review-copy"]

    result = _compile(
        _item("review the fix", capability="research"),
        _item("review the test", capability="research", writes="/tmp/review-copy"),
    )
    assert _codes(result) == ["research_leaf_writes"], _messages(result)
    assert "/tmp/review-copy" in _messages(result)
    assert "t, e, m, p" not in _messages(result)


def test_a_list_sent_as_json_text_is_that_list():
    leaf = leaf_from_item(_item("review", writes='["out/a.md", "out/b.md"]'))
    assert leaf.writes == ["out/a.md", "out/b.md"]


@pytest.mark.parametrize("value", [12, {"path": "/tmp/review-copy"}, [["nested"]], True])
def test_writes_of_any_other_type_is_refused_by_its_field(value):
    result = _compile(_item("review the fix"), _item("review the test", writes=value))
    assert "leaf_declaration_type" in _codes(result), _messages(result)
    assert "writes" in _messages(result) and "list of paths" in _messages(result)
    assert result.ok is False


# ── the other declarations ────────────────────────────────────────────────────────────────────


def test_a_capability_that_is_not_one_of_the_two_is_refused_not_read_as_research():
    """🔴 Before: `capability: "shell"` compiled as research, and the leaf was then refused for
    declaring writes a research leaf may not have, which named the wrong problem."""
    result = _compile(_item("review the fix"), _item("review the test", capability="shell"))
    assert "leaf_declaration_type" in _codes(result), _messages(result)
    assert "research" in _messages(result) and "mutating" in _messages(result)


@pytest.mark.parametrize("field", ["objective", "output_format", "boundary"])
def test_a_declaration_that_is_not_text_is_refused(field):
    result = _compile(_item("review the fix"), _item("review the test", **{field: ["a", "b"]}))
    assert "leaf_declaration_type" in _codes(result), _messages(result)
    assert field in _messages(result)


def test_a_key_the_contract_does_not_have_is_refused_naming_the_ones_it_does():
    result = _compile(_item("review the fix"), _item("review the test", role="tough reviewer"))
    assert "leaf_declaration_unknown" in _codes(result), _messages(result)
    assert "role" in _messages(result) and "off_limits" in _messages(result)


def test_a_contract_that_reads_cleanly_compiles():
    result = _compile(
        _item("review the fix"),
        _item("review the test", capability="mutating", writes=["/tmp/review-copy"]),
    )
    assert result.ok, _messages(result)


# ── the boundary ──────────────────────────────────────────────────────────────────────────────


def test_a_boundary_that_names_the_folder_a_leaf_works_in_does_not_fence_it():
    """🔴 Before: `boundary_contradicts_writes` on a leaf told to work "only inside … the temporary
    worktree /tmp/review-copy" that declared it writes /tmp/review-copy."""
    result = _compile(
        _item("review the fix"),
        _item(
            "review the test",
            capability="mutating",
            writes=["/tmp/review-copy"],
            boundary=(
                "Work only inside the repo checkout and the temporary worktree /tmp/review-copy, "
                "which you create and must remove afterward. No commits, no pushes."
            ),
        ),
    )
    assert "boundary_contradicts_writes" not in _codes(result), _messages(result)
    assert result.ok, _messages(result)


def test_a_write_inside_a_path_it_must_not_write_is_refused():
    result = _compile(
        _item("review the fix"),
        _item(
            "migrate the schema",
            capability="mutating",
            writes=["db/schema.sql"],
            off_limits=["db/"],
        ),
    )
    assert _codes(result) == ["boundary_contradicts_writes"], _messages(result)


def test_the_paths_it_must_not_write_reach_the_worker():
    leaf = leaf_from_item(_item("migrate", off_limits="db/"))
    assert leaf.off_limits == ["db/"]
    assert "db/" in leaf.prompt()


# ── what the model is shown, and what the tool says ───────────────────────────────────────────


def test_the_tool_shows_a_batch_task_as_the_contract_the_compiler_reads():
    """🔴 Before: `tasks` was shown as a list of strings, so a model that followed the schema sent
    a batch the compiler refused for having no contract."""
    (spec,) = [t for t in mcp_subagents._list_tools() if t["name"] == "subagent_run"]
    items = spec["inputSchema"]["properties"]["tasks"]["items"]
    assert items["type"] == "object"
    assert set(items["required"]) == {"task", "objective", "output_format", "boundary"}
    props = items["properties"]
    assert props["capability"]["enum"] == [c.value for c in Capability]
    for paths in ("writes", "off_limits"):
        assert props[paths]["type"] == "array" and props[paths]["items"] == {"type": "string"}
    assert set(props) == set(batch_compile.LEAF_DECLARATIONS)


def test_the_tool_reads_the_batch_a_model_sent_as_text(monkeypatch):
    """The call the chat made, end to end: `tasks` arrived as JSON text with `writes` as text."""
    posts: list[tuple[str, dict]] = []

    def fake_post(path: str, body: dict) -> dict:
        posts.append((path, body))
        return {"ok": True, "run_id": "5e1a7c20"} if path == "/api/workflows/runs" else {"ok": True}

    monkeypatch.setattr(mcp_subagents, "_post", fake_post)
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "dashboard:chat-1")
    tasks = [
        _item("review the fix", capability="research"),
        _item("review the test", capability="mutating", writes="/tmp/review-copy"),
    ]
    out = mcp_subagents._call_tool_inner("subagent_run", {"tasks": json.dumps(tasks)})

    assert "/api/workflows/runs" in [p for p, _ in posts], out
    assert "did not compile" not in out, out
    assert "multi_writer" not in out, out
