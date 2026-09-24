"""AG-14: the pre-edit read gate at the native write seam.

Two kinds of test here, deliberately:

* **behaviour** — a modifying write is admitted only when the target's current content was
  actually observed, and refused with the read to do first when it was not; and
* **the bypass rail** — every filesystem write in ``builtin_tools`` is covered by the ONE
  gate at the seam, so a new write path cannot ship uncovered (the
  fixing-the-wrapper-misses-a-raw-child defect). The rail carries a vacuity assertion:
  if its scan matches no write paths at all it FAILS rather than reading clean.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import (
    _READ_GATED_WRITE_TOOLS,
    NativeBuiltinToolProvider,
)

_BUILTIN_TOOLS_SRC = Path(read_gate.__file__).with_name("builtin_tools.py")


@pytest.fixture(autouse=True)
def _clean_ledger():
    # The ledger is process-global; reset around every test so a leaked observation from
    # one test cannot admit a write another test asserts is refused (or vice versa).
    read_gate.reset_all()
    yield
    read_gate.reset_all()


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nsecond line\n")
    (tmp_path / "b.txt").write_text("bee content\n")
    return tmp_path


def _p(ws, key="sess-ag14"):
    return NativeBuiltinToolProvider(ws, session_key=key)


# ── the closed failure: an edit against content never observed ──────────────────


@pytest.mark.asyncio
async def test_edit_without_reading_is_refused(ws):
    r = await _p(ws).invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert not r.success
    assert r.metadata["read_gate"] == "not_observed"
    # unchanged on disk — refused, not applied optimistically
    assert (ws / "a.txt").read_text() == "hello world\nsecond line\n"


@pytest.mark.asyncio
async def test_edit_after_reading_is_admitted(ws):
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert r.success, r.error
    assert (ws / "a.txt").read_text().startswith("HI world")


@pytest.mark.asyncio
async def test_refusal_names_the_path_and_the_read_to_perform(ws):
    """The refusal is a NEXT ACTION, not an opaque denial — one step to self-correct."""
    r = await _p(ws).invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    text = r.error + " " + " ".join(r.recovery_hints)
    assert "a.txt" in text  # the path
    assert "read_file" in text  # the verb/tool to call
    assert "retry" in text  # and that retrying then works


# ── the call-count defect: a read must be OF THIS FILE, and COVER the region ────


@pytest.mark.asyncio
async def test_reading_a_different_file_does_not_license_the_edit(ws):
    """A read-tool-was-called check would admit this. The content check must not."""
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "b.txt"})).success
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert not r.success and r.metadata["read_gate"] == "not_observed"
    assert (ws / "a.txt").read_text() == "hello world\nsecond line\n"


@pytest.mark.asyncio
async def test_truncated_read_does_not_license_an_edit_past_the_shown_region(ws):
    """Observing the head of a big file licenses the head — not the dropped middle."""
    big = "\n".join(f"line {i:06d} filler filler filler" for i in range(4000))
    (ws / "big.txt").write_text(big + "\n")
    p = _p(ws)
    r = await p.invoke("read_file", {"path": "big.txt"})
    assert r.truncated, "fixture must exceed the output cap or the test is vacuous"
    assert "line 002000 filler" not in r.output, "the mid region must NOT have been shown"

    mid = await p.invoke(
        "edit_file", {"path": "big.txt", "old_str": "line 002000 filler", "new_str": "MID"}
    )
    assert not mid.success and mid.metadata["read_gate"] == "region_not_observed"
    assert "line 002000 filler" in (ws / "big.txt").read_text(), "must not have been applied"

    # ...while the region that WAS shown edits fine, so the gate is not just "big files off".
    head = await p.invoke(
        "edit_file", {"path": "big.txt", "old_str": "line 000001 filler", "new_str": "HEAD"}
    )
    assert head.success, head.error


@pytest.mark.asyncio
async def test_retrieving_the_dropped_slice_makes_the_refusal_actionable(ws):
    """The refusal names tool_result_get; taking that route must actually unblock."""
    big = "\n".join(f"line {i:06d} filler filler filler" for i in range(4000))
    (ws / "big.txt").write_text(big + "\n")
    p = _p(ws)
    r = await p.invoke("read_file", {"path": "big.txt"})
    rid = r.metadata.get("raw_ref")
    assert rid, "a projected read must retain its raw or the recovery route is fiction"
    assert (await p.invoke("tool_result_get", {"result_id": rid, "grep": "line 002000"})).success
    ok = await p.invoke(
        "edit_file", {"path": "big.txt", "old_str": "line 002000 filler", "new_str": "MID"}
    )
    assert ok.success, ok.error


# ── three operations: edit / overwrite-existing / create-new ────────────────────


@pytest.mark.asyncio
async def test_creating_a_new_file_needs_no_prior_read(ws):
    r = await _p(ws).invoke("write_file", {"path": "new/c.txt", "content": "fresh"})
    assert r.success, r.error
    assert (ws / "new" / "c.txt").read_text() == "fresh"


@pytest.mark.asyncio
async def test_overwriting_an_existing_file_is_gated_like_an_edit(ws):
    p = _p(ws)
    blind = await p.invoke("write_file", {"path": "a.txt", "content": "CLOBBER"})
    assert not blind.success and blind.metadata["read_gate"] == "not_observed"
    assert (ws / "a.txt").read_text() == "hello world\nsecond line\n"
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    assert (await p.invoke("write_file", {"path": "a.txt", "content": "CLOBBER"})).success
    assert (ws / "a.txt").read_text() == "CLOBBER"


@pytest.mark.asyncio
async def test_overwrite_of_a_partly_observed_file_is_refused(ws):
    """An overwrite's region is the WHOLE file, so a truncated read licenses nothing."""
    (ws / "big.txt").write_text("\n".join(f"line {i:06d} pad pad pad" for i in range(4000)))
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "big.txt"})).truncated
    r = await p.invoke("write_file", {"path": "big.txt", "content": "tiny"})
    assert not r.success and r.metadata["read_gate"] == "partial_observation"
    assert len((ws / "big.txt").read_text()) > 60_000


# ── currency: the observation must be of the CURRENT content ────────────────────


@pytest.mark.asyncio
async def test_a_concurrent_write_invalidates_the_observation(ws):
    """The closed failure itself: an edit that would silently revert someone else."""
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    (ws / "a.txt").write_text("hello world\nsecond line\nTHEIR NEW LINE\n")  # another writer
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert not r.success and r.metadata["read_gate"] == "changed_on_disk"
    assert "THEIR NEW LINE" in (ws / "a.txt").read_text(), "their change must survive"


@pytest.mark.asyncio
async def test_the_agents_own_write_keeps_its_observation_current(ws):
    """Consecutive edits must work: our own write is content we observed, not drift."""
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    assert (
        await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    ).success
    second = await p.invoke("edit_file", {"path": "a.txt", "old_str": "second", "new_str": "2nd"})
    assert second.success, second.error
    assert (ws / "a.txt").read_text() == "HI world\n2nd line\n"


@pytest.mark.asyncio
async def test_a_new_turn_drops_the_observations(ws):
    p = _p(ws, key="turny")
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    read_gate.begin_turn("turny")
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert not r.success and r.metadata["read_gate"] == "not_observed"


@pytest.mark.asyncio
async def test_an_expired_observation_is_refused(ws):
    """Injected, not slept: a timing sleep would measure the clock, not the gate."""
    p = _p(ws, key="oldy")
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    obs = read_gate.observation("oldy", ws / "a.txt")
    assert obs is not None
    read_gate._LEDGER["oldy"][str(ws / "a.txt")] = read_gate.Observation(
        content_sha256=obs.content_sha256,
        complete=obs.complete,
        at=obs.at - read_gate.OBSERVATION_TTL_SECS - 10,
        fragments=obs.fragments,
    )
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert not r.success and r.metadata["read_gate"] == "expired"


def test_undeterminable_content_fails_closed(ws, monkeypatch):
    """If the gate cannot tell what is there now, it refuses — it does not wave through."""
    monkeypatch.setattr(read_gate, "file_sha256", lambda _p: None)
    read_gate.record_read(
        "s", ws / "a.txt", observed_text="hello world\n", content_sha256="deadbeef", complete=True
    )
    refusal = read_gate.admit_write(
        "s", ws / "a.txt", operation="edit", display_path="a.txt", required_text="hello"
    )
    assert refusal is not None and refusal.reason == "undetermined"


def test_an_unknown_operation_is_refused(ws):
    read_gate.record_read(
        "s",
        ws / "a.txt",
        observed_text=(ws / "a.txt").read_text(),
        content_sha256=read_gate.file_sha256(ws / "a.txt") or "",
        complete=True,
    )
    refusal = read_gate.admit_write(
        "s", ws / "a.txt", operation="append", display_path="a.txt", required_text="hello"
    )
    assert refusal is not None and refusal.reason == "unknown_operation"


@pytest.mark.asyncio
async def test_missing_old_str_still_reports_the_tools_own_error(ws):
    """A fully-observed file whose old_str is absent is the TOOL's error, not the gate's:
    blaming the model for a read it already did would teach it the wrong recovery."""
    p = _p(ws)
    assert (await p.invoke("read_file", {"path": "a.txt"})).success
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "nope", "new_str": "x"})
    assert not r.success
    assert r.error == "old_str not found in file"
    assert "read_gate" not in r.metadata


# ── the bypass rail ────────────────────────────────────────────────────────────

#: Calls that write CONTENT to a filesystem path. A handler performing one of these is a
#: write path and must be covered by the seam's registry. Deletion/rename (``unlink``,
#: ``rename``) are deliberately out: the gate is about content computed against a stale
#: version, and including ``unlink`` credited ``_t_bash`` for deleting its own temp script.
_CONTENT_WRITE_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "atomic_write",
        "atomic_write_bytes",
        "copyfile",
        "copy",
        "copy2",
        "replace",  # os.replace / shutil.replace — a rename-into-place IS a content write
    }
)

#: Write paths that CANNOT be gated on an observed target, declared so the exemption is
#: visible and tested rather than silently missing from the registry. ``bash`` takes a
#: command, not a path: ``sed -i`` / ``> file`` mutate files the gate never sees a name
#: for. It is fenced by approval + risk level + the bash denylist instead, and this row
#: is the honest statement of the residual hole.
_UNGATEABLE_WRITE_PATHS = {"bash": "takes an opaque command, not a target path"}


def _write_handlers_in_builtin_tools() -> set[str]:
    """Every ``_t_<tool>`` handler in builtin_tools.py whose body mutates a path.

    An AST walk, not a grep: a handler is attributed by the function it is lexically
    inside, so a mutating call cannot be credited to the wrong tool (and a mention in a
    docstring or comment is not a call at all).
    """
    tree = ast.parse(_BUILTIN_TOOLS_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_t_"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            # `str.replace` is pervasive and harmless; only a path receiver mutates.
            if name == "replace" and not (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"os", "path", "shutil"}
            ):
                continue
            if name in _CONTENT_WRITE_CALLS:
                found.add(node.name[len("_t_") :])
    return found


def test_rail_every_write_path_is_covered_by_the_one_seam():
    discovered = _write_handlers_in_builtin_tools()
    # VACUITY: a scan that matches nothing would "pass" while covering nothing. If the
    # write handlers get renamed or the call vocabulary drifts, FAIL here.
    assert discovered, (
        "the write-path scan matched ZERO handlers — the rail is vacuous. Update "
        f"_CONTENT_WRITE_CALLS / the _t_ prefix against {_BUILTIN_TOOLS_SRC}."
    )
    assert {
        "write_file",
        "edit_file",
    } <= discovered, f"the two known write paths must be discoverable; got {sorted(discovered)}"
    uncovered = discovered - set(_READ_GATED_WRITE_TOOLS) - set(_UNGATEABLE_WRITE_PATHS)
    assert not uncovered, (
        f"write path(s) {sorted(uncovered)} write file content but are not in "
        "_READ_GATED_WRITE_TOOLS, so they bypass the pre-edit read gate. Add a row "
        "there (do NOT re-implement the gate in the handler)."
    )


def test_rail_the_ungateable_write_path_is_declared_not_forgotten():
    """``bash`` is a real hole. Assert it is a DECLARED exemption with a stated reason,
    so the rail cannot pass by quietly not knowing about it."""
    src = _BUILTIN_TOOLS_SRC.read_text(encoding="utf-8")
    for tool, reason in _UNGATEABLE_WRITE_PATHS.items():
        assert f"async def _t_{tool}" in src, f"{tool} no longer exists — drop the exemption"
        assert (
            tool not in _READ_GATED_WRITE_TOOLS
        ), f"{tool} is now gateable — move it into _READ_GATED_WRITE_TOOLS"
        assert reason, f"{tool} is exempt with no stated reason"


def test_rail_the_gate_is_expressed_once_not_per_tool():
    """The handlers must not re-implement the gate — ``invoke`` owns it."""
    src = _BUILTIN_TOOLS_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    seam_calls, handler_calls = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        uses_gate = any(
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "read_gate"
            and sub.attr in {"admit_write", "record_overwrite", "record_edit"}
            for sub in ast.walk(node)
        )
        if not uses_gate:
            continue
        if node.name in {"_read_gate_refusal", "_read_gate_observe_write"}:
            seam_calls += 1
        else:
            handler_calls.append(node.name)
    assert seam_calls == 2, "the gate's two halves must live in the two seam helpers"
    assert not handler_calls, (
        f"{handler_calls} call the gate directly — it must be expressed ONCE at invoke() "
        "so every write path inherits it"
    )


@pytest.mark.asyncio
async def test_rail_every_registered_write_tool_is_actually_refused_when_blind(ws):
    """Declaration is not enforcement: drive each registered write path for real.

    This is the leg that reds if a tool is dropped from ``_READ_GATED_WRITE_TOOLS`` or if
    the seam stops consulting the gate — a static-only rail would still read clean.
    """
    args = {
        "write_file": {"path": "a.txt", "content": "CLOBBER"},
        "edit_file": {"path": "a.txt", "old_str": "hello", "new_str": "HI"},
    }
    assert set(args) == set(_READ_GATED_WRITE_TOOLS), (
        "a registered write tool has no blind-write probe here — add one, otherwise it "
        f"ships unenforced: {sorted(set(_READ_GATED_WRITE_TOOLS) - set(args))}"
    )
    before = (ws / "a.txt").read_text()
    for tool, a in args.items():
        p = _p(ws, key=f"blind-{tool}")
        r = await p.invoke(tool, dict(a))
        assert not r.success, f"{tool} admitted a write to a file it never read"
        assert r.metadata.get("read_gate"), f"{tool} failed for some OTHER reason: {r.error}"
        assert (ws / "a.txt").read_text() == before, f"{tool} mutated the file anyway"


def test_rail_the_read_tool_records_what_it_returns():
    """The recorded observation must be the RETURNED text, not the file's bytes —
    otherwise truncation is not honoured and the gate degrades to a call-count check."""
    src = _BUILTIN_TOOLS_SRC.read_text(encoding="utf-8")
    body = src[src.index("async def _t_read_file") : src.index("def _checkpoint_pre_edit")]
    assert "read_gate.record_read(" in body, "read_file must record its observation"
    assert re.search(
        r"observed_text=res\.output", body
    ), "record_read must be handed the PROJECTED output that the model saw"
    assert re.search(
        r"complete=bool\(byte_complete\) and not res\.truncated", body
    ), "completeness must account for BOTH truncation axes (byte cap + projection)"
