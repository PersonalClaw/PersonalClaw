"""ACP-AGENT-PARITY §2.5 (atom ``AAP-8``) — tool-card fidelity and risk plumbing.

Three gaps, and the reason each one needed a different shape of fix:

* **gap 7, structured input.** ``translate.py`` receives a real ``rawInput`` object and
  ``json.dumps``-ed it away, so ``chat_runner._redact_tool_input_obj`` was handed a
  ``str``, returned ``None`` by contract, and every ACP tool card fell back to the flat
  string preview no matter how well the CLI described its call. Fixed by carrying the
  object BESIDE the string (``tool_input_obj``) rather than replacing it — the string is
  what the card prints, and flattening the object into that field would have changed
  every existing preview to buy the fields.
* **gap 7, diff chips.** The native chip is inferred: a write-tool NAME set, a workspace
  path resolution, a disk read. None of that transfers to a CLI whose edit tool is named
  and shaped however its vendor chose. An ACP ``diff`` content block states path, old
  text and new text outright, so the chip is built from the declaration alone.
* **gap 8, declared risk.** Measured, and it needed NO plumbing: for every dict-defined
  core tool the "declaration" IS the name inference (``agents/native/tools.py`` uses
  ``infer_risk_from_name`` when the dict carries no explicit ``risk_level``, and none
  does), so threading a declared level through the MCP listing would compute the same
  answer twice. The rail below pins that equivalence instead, so the day a core tool
  declares an explicit level, the divergence fails here rather than silently mislabeling
  an approval card.

The three tool-name renderings exercised here are the ones MEASURED live on 2026-08-24
during ``AAP-4``'s reachability drive — codex renders ``mcp.personalclaw-core.notify``,
claude-code ``mcp__personalclaw-core__notify`` and kiro-cli ``@personalclaw-core/notify``
for the same server. A risk resolver that only handles one dialect mislabels two thirds
of the fleet, so every risk assertion runs over all three.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.translate import (
    extract_tool_event,
    extract_tool_update_events,
)
from personalclaw.acp.types import EVENT_TOOL_CALL_UPDATE, JsonRpcMessage
from personalclaw.dashboard.chat_runner import (
    _capture_declared_file_change,
    _redact_tool_input_obj,
)
from personalclaw.task_modes import infer_risk_from_name, resolve_effective_risk

# ── frames a real CLI puts on the wire ───────────────────────────────────────

_DIALECTS = (
    "mcp.personalclaw-core.{name}",  # codex
    "mcp__personalclaw-core__{name}",  # claude-code
    "@personalclaw-core/{name}",  # kiro-cli
)


def _call_frame(raw_input: object, *, title: str = "Read", kind: str = "read") -> JsonRpcMessage:
    update: dict = {
        "sessionUpdate": "tool_call",
        "toolCallId": "c1",
        "title": title,
        "kind": kind,
    }
    if raw_input is not None:
        update["rawInput"] = raw_input
    return JsonRpcMessage(method="session/update", params={"update": update})


def _update_frame(**update: object) -> JsonRpcMessage:
    base: dict = {"sessionUpdate": "tool_call_update", "toolCallId": "c1"}
    base.update(update)
    return JsonRpcMessage(method="session/update", params={"update": base})


def _session():
    return SimpleNamespace(_file_changes=[])


# ── gap 7a: the object reaches the renderer ──────────────────────────────────


class TestStructuredInputSurvivesToTheRenderer:
    def test_tool_call_frame_carries_the_object_and_the_string(self):
        ev = extract_tool_event(_call_frame({"path": "/tmp/a.txt", "limit": 40}), {}, {}, [])
        assert ev is not None
        assert ev.tool_input_obj == {"path": "/tmp/a.txt", "limit": 40}
        # The string is untouched: the card's existing preview must not change shape.
        assert json.loads(ev.tool_input) == {"path": "/tmp/a.txt", "limit": 40}

    def test_update_frame_carries_the_object(self):
        """The site that matters most: adapters routinely open with ``rawInput: {}`` and
        stream the real arguments in the update, so a fix that only touched the opening
        frame would leave the fields empty for the whole turn."""
        events = extract_tool_update_events(
            _update_frame(rawInput={"command": "ls -la"}, title="Terminal"), {}, {}
        )
        upd = [e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE]
        assert upd, "no tool_call_update event produced"
        assert upd[0].tool_input_obj == {"command": "ls -la"}

    def test_the_adapter_does_not_drop_it(self):
        """``acp_event_to_agent_event`` is a field-for-field mapper, and this repo has
        already lost ``tool_meta`` there once (`G6`/`G7`) — a mapper that silently omits
        a field looks exactly like a backend that never populated it."""
        ev = extract_tool_event(_call_frame({"path": "/tmp/a.txt"}), {}, {}, [])
        assert ev is not None
        assert acp_event_to_agent_event(ev).tool_input_obj == {"path": "/tmp/a.txt"}

    def test_the_renderer_now_returns_fields_instead_of_none(self):
        """The whole point, end to end: before this change the renderer got the string
        and returned None by contract."""
        ev = extract_tool_event(_call_frame({"path": "/tmp/a.txt", "limit": 40}), {}, {}, [])
        assert ev is not None
        agent_ev = acp_event_to_agent_event(ev)
        rendered = _redact_tool_input_obj(
            agent_ev.tool_input_obj if agent_ev.tool_input_obj is not None else agent_ev.tool_input
        )
        assert rendered == {"path": "/tmp/a.txt", "limit": 40}

    def test_the_string_only_frame_still_renders_none(self):
        """Vacuity floor. A frame whose ``rawInput`` is a bare string must NOT be dressed
        up as a one-key dict — the string preview is the honest rendering, and a fake
        object would make the card assert a schema the CLI never sent."""
        ev = extract_tool_event(_call_frame("just a string"), {}, {}, [])
        assert ev is not None
        assert ev.tool_input_obj is None
        assert _redact_tool_input_obj(acp_event_to_agent_event(ev).tool_input) is None

    def test_an_absent_rawinput_is_none_not_empty_dict(self):
        """SC #6's "native-only meta stays empty (not fabricated) where frames are
        empty": ``None`` means the frame supplied nothing. An empty dict would read as
        "the tool was called with no arguments", which is a different claim."""
        ev = extract_tool_event(_call_frame(None), {}, {}, [])
        assert ev is not None
        assert ev.tool_input_obj is None

    def test_secrets_in_the_object_are_redacted_by_the_renderer(self):
        """The object crosses the boundary unredacted, exactly like the native runtime's
        dict, because ``_redact_tool_input_obj`` is the single redaction+cap point for
        the structured shape. So prove the secret does not survive that point."""
        ev = extract_tool_event(
            _call_frame({"cmd": "curl -H 'Authorization: Bearer sk-ant-api03-SECRETVALUE'"}),
            {},
            {},
            [],
        )
        assert ev is not None
        rendered = _redact_tool_input_obj(acp_event_to_agent_event(ev).tool_input_obj)
        assert rendered is not None
        assert "sk-ant-api03-SECRETVALUE" not in json.dumps(rendered)


# ── gap 7b: a declared edit becomes a chip ───────────────────────────────────


class TestDeclaredFileChangeBecomesAChip:
    def _diff_update(self, old: str, new: str, path: str = "src/a.py"):
        return _update_frame(
            content=[{"type": "diff", "path": path, "oldText": old, "newText": new}],
            title="Edit",
        )

    def test_diff_content_block_declares_the_change(self):
        events = extract_tool_update_events(self._diff_update("a\n", "b\n"), {}, {})
        upd = [e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE]
        assert upd, "no update event produced"
        assert upd[0].file_change == {"path": "src/a.py", "before": "a\n", "after": "b\n"}

    def test_the_chip_lands_on_the_session(self):
        events = extract_tool_update_events(self._diff_update("a\n", "b\n"), {}, {})
        ev = acp_event_to_agent_event([e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE][0])
        session = _session()
        _capture_declared_file_change(session, ev.file_change)
        assert session._file_changes == [{"path": "src/a.py", "before": "a\n", "after": "b\n"}]

    def test_a_noop_edit_files_no_chip(self):
        """Same guard the native path enforces — an edit that changed nothing must not
        render a chip that implies it did."""
        session = _session()
        _capture_declared_file_change(session, {"path": "src/a.py", "before": "x", "after": "x"})
        assert session._file_changes == []

    def test_a_pathless_declaration_files_no_chip(self):
        """``_flush_file_changes`` dedups per path, so a chip keyed on "" would collapse
        every unnamed edit in the turn into one row."""
        session = _session()
        _capture_declared_file_change(session, {"path": "", "before": "a", "after": "b"})
        assert session._file_changes == []

    def test_strreplace_fragments_are_deliberately_not_a_chip(self):
        """``oldStr``/``newStr`` are the FRAGMENTS being replaced, not the file's
        contents. Filing one as ``before`` would render a chip asserting the file
        contained only that fragment. The unified diff still shows the user the change —
        assert that too, so this reads as a scoped withholding and not a lost feature."""
        events = extract_tool_update_events(
            _update_frame(
                rawInput={
                    "command": "strReplace",
                    "path": "src/a.py",
                    "oldStr": "one_line()",
                    "newStr": "another_line()",
                },
                title="Edit",
            ),
            {},
            {},
        )
        upd = [e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE]
        assert upd
        assert upd[0].file_change is None
        assert "another_line()" in upd[0].tool_input

    def test_a_plain_tool_update_declares_no_change(self):
        """Vacuity floor: the chip must come from a DECLARATION, never from a tool whose
        name merely sounds like a write."""
        events = extract_tool_update_events(
            _update_frame(rawInput={"path": "src/a.py"}, title="write_file"), {}, {}
        )
        upd = [e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE]
        assert upd
        assert upd[0].file_change is None

    def test_a_progressive_redeclaration_replaces_both_sides(self):
        """MEASURED live on claude-code (2026-08-24) and the reason this path does NOT
        reuse the native merge rule. A streaming adapter re-declares the same edit as its
        arguments fill in: an early frame named the replaced FRAGMENT as ``oldText`` and a
        later one the whole file. ``_flush_file_changes`` keeps the earliest ``before`` and
        the latest ``after``, so the merge produced a chip whose before was one line and
        whose after was the entire file — a diff asserting the file used to contain only
        that line. Last declaration wins on BOTH sides."""
        session = _session()
        _capture_declared_file_change(
            session,
            {"path": "src/a.py", "before": '    return "hello"', "after": '    return "bye"'},
        )
        _capture_declared_file_change(
            session,
            {
                "path": "src/a.py",
                "before": 'def greet():\n    return "hello"\n',
                "after": 'def greet():\n    return "bye"\n',
            },
        )
        assert session._file_changes == [
            {
                "path": "src/a.py",
                "before": 'def greet():\n    return "hello"\n',
                "after": 'def greet():\n    return "bye"\n',
            }
        ], "a progressive re-declaration must replace the partial one, not append beside it"

    def test_two_different_paths_still_get_two_chips(self):
        """Vacuity floor for the replacement above — it is keyed per path, so an agent
        editing two files must not have the second chip overwrite the first."""
        session = _session()
        _capture_declared_file_change(session, {"path": "a.py", "before": "1", "after": "2"})
        _capture_declared_file_change(session, {"path": "b.py", "before": "3", "after": "4"})
        assert [c["path"] for c in session._file_changes] == ["a.py", "b.py"]

    def test_a_huge_snapshot_is_capped(self):
        from personalclaw.dashboard.chat_runner import _MAX_FILE_SNAPSHOT

        session = _session()
        _capture_declared_file_change(
            session, {"path": "src/a.py", "before": "", "after": "x" * (_MAX_FILE_SNAPSHOT + 500)}
        )
        assert len(session._file_changes[0]["after"]) < _MAX_FILE_SNAPSHOT + 100


# ── gap 8: the declared level and the inferred level are ONE function ────────


_CORE_TOOL_RISK = {
    "artifact_delete": "destructive",
    "memory_forget": "destructive",
    "notify": "caution",
    "knowledge_search": "safe",
}


class TestDeclaredRiskNeedsNoPlumbing:
    @pytest.mark.parametrize("bare,expected", sorted(_CORE_TOOL_RISK.items()))
    @pytest.mark.parametrize("dialect", _DIALECTS)
    def test_every_dialect_infers_the_same_risk_as_the_bare_name(self, dialect, bare, expected):
        """§2.5 gap 8 measured: the ACP-rendered name does NOT break inference, in any of
        the three dialects a live drive observed. This is why no name-normalizing
        resolver was added — it would have returned the same value it was handed."""
        assert infer_risk_from_name(dialect.format(name=bare)) == expected

    @pytest.mark.parametrize("dialect", _DIALECTS)
    def test_a_destructive_core_tool_resolves_destructive_through_the_acp_path(self, dialect):
        """The clause that matters on screen: the approval card for a destructive core
        tool must show destructive, with the empty ``declared`` an ACP event carries."""
        name = dialect.format(name="artifact_delete")
        assert resolve_effective_risk("", name, "other", "") == "destructive"

    def test_a_declaration_still_wins_when_one_exists(self):
        """The plumbing would only ever matter for a tool that declares a level the name
        does not imply. Prove the resolver already honours that, so the day a core tool
        does declare one, passing it through is the whole change."""
        assert resolve_effective_risk("destructive", "knowledge_search", "other", "") == (
            "destructive"
        )

    def test_no_core_tool_dict_declares_an_explicit_risk_level(self):
        """The census this conclusion rests on, as a rail. If a core tool ever sets
        ``risk_level`` explicitly, the equivalence above stops holding and the ACP path
        starts showing an inferred level where a declared one exists — so fail HERE,
        loudly, rather than mislabeling an approval card.

        Scoped to ``mcp_core`` and the category modules it aggregates; ``llm/scripted.py``
        (a scripted test backend) and ``browse/cdp.py`` (its own outcome shape) are
        legitimate writers of that key and are not core tool dicts."""
        import importlib
        import pathlib

        from personalclaw.mcp_core import _AGGREGATED_CATEGORY_MODULES

        # Resolved through the import system, not by joining the last dotted segment onto
        # the package root. That shortcut was silently wrong for a NESTED category module and
        # its own vacuity floor is what caught it: `personalclaw.computer_use.tools` (DCU-4)
        # reduced to `tools.py`, pointing the census at an unrelated top-level module. Asking
        # the module where it lives cannot drift from where it actually lives.
        modules = ["personalclaw.mcp_core", *_AGGREGATED_CATEGORY_MODULES]
        paths = [pathlib.Path(importlib.import_module(m).__file__ or "") for m in modules]
        # Vacuity floor: a mistyped path would scan nothing and pass. Assert the sweep
        # actually opened the modules, and enough of them to be the real set.
        assert all(p.is_file() for p in paths), [str(p) for p in paths if not p.is_file()]
        assert len(paths) >= 6, len(paths)
        offenders = []
        for path in paths:
            for num, line in enumerate(path.read_text().splitlines(), 1):
                if '"risk_level"' in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.name}:{num}")
        assert offenders == [], (
            "a core tool dict now declares an explicit risk_level; the ACP path passes "
            f"declared='' and would show the INFERRED level instead: {offenders}"
        )
