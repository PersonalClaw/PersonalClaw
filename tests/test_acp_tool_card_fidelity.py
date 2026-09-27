"""Tool-card fidelity and risk plumbing.

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
* **gap 8, declared risk.** An ACP CLI declares nothing about its own tools, so a call from
  one carries no declaration and is treated as a change. The ``personalclaw-core`` server is
  the exception — the host serves it and its tools declare what they do — so a call to one of
  them carries that tool's declaration (``acp.mcp_servers.core_tool_declaration``), and the
  ACP card, Ask mode and Trust reads answer for ``artifact_delete`` what the native path does.
  It used to be the same NAME inference on both paths, which is why no plumbing was needed;
  inference from a name is gone, so the declaration has to travel.

The three wire shapes exercised here are the ones each adapter really sends: claude-code
``mcp__personalclaw-core__notify`` (kind ``other``), codex ``mcp.personalclaw-core.notify``
(kind ``execute``, with a structured ``{server, tool, arguments}`` input) and kiro-cli
``Running: @personalclaw-core/notify`` (the tool line the unattended drive recorded).
A lookup that only handles one of them mislabels two thirds of the fleet, so every risk
assertion runs over all three.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.dialect import DefaultDialect
from personalclaw.acp.translate import (
    build_permission_event,
    extract_tool_event,
    extract_tool_update_events,
)
from personalclaw.acp.types import EVENT_TOOL_CALL_UPDATE, JsonRpcMessage
from personalclaw.dashboard.chat_runner import (
    _capture_declared_file_change,
    _redact_tool_input_obj,
)
from personalclaw.llm.events import AgentEvent
from personalclaw.task_modes import resolve_effective_risk
from personalclaw.tool_providers.base import risk_from_annotations

# ── frames a real CLI puts on the wire ───────────────────────────────────────


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
        already lost ``tool_meta`` there once — a mapper that silently omits
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
            _call_frame({"cmd": "curl -H 'Authorization: Bearer fake-anthropic-1'"}),
            {},
            {},
            [],
        )
        assert ev is not None
        rendered = _redact_tool_input_obj(acp_event_to_agent_event(ev).tool_input_obj)
        assert rendered is not None
        assert "fake-anthropic-1" not in json.dumps(rendered)


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
        """MEASURED live on claude-code and the reason this path does NOT
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


# ── which providers the chip actually covers — MEASURED, not inferred ─────────


class TestTheDiffBlockIsNotACodexOnlyShape:
    """The `diff` content block is **claude's shape too**, so the chip is not the
    codex-only path the parity matrix's wording implies.

    Measured statically from the installed adapter — `@agentclientprotocol/`
    `claude-agent-acp@0.74.0`, the exact build the 2026-09-19 re-drive ran, read out of
    `dist/tools.js` rather than guessed:

    * ``Write`` → ``{type:"diff", path: file_path, oldText: null, newText: content}``
      with ``kind: "edit"``. Whole-file by construction, and ``oldText: null`` is the
      adapter's *creation* spelling.
    * ``Edit``  → ``{type:"diff", path: file_path, oldText: old_string || null,
      newText: new_string ?? ""}``, also ``kind: "edit"``.
    * the completion frame re-declares the edit whole-file from the SDK's
      ``originalFile``/``content`` when it has them.

    So `translate.py`'s ``cb["type"] == "diff"`` branch already fires on claude, and the
    parity row's "the host drops it / claude sends `kind` + `rawInput`" is a description
    of the ADAPTER VERSION IT WAS FILLED AT, not a statement that claude needs a second
    decoder. The frames below are that adapter's output, so the day claude stops sending
    diff blocks this fails here instead of silently reverting the chip to one provider.

    What this class deliberately does NOT assert: that every claude declaration is a
    whole-file pair. It is not — ``Edit``'s old/new are the replaced FRAGMENT, and the
    completion frame emits one diff block PER HUNK of the structured patch. Both are
    recorded in the parity doc as the open half of this row; pinning today's handling of
    them as correct is what would make them permanent.
    """

    def _chip(self, blocks: list[dict], *, on_update: bool) -> dict | None:
        if on_update:
            events = extract_tool_update_events(_update_frame(content=blocks, title="Edit"), {}, {})
            upd = [e for e in events if e.kind == EVENT_TOOL_CALL_UPDATE]
            assert upd, "no update event produced"
            return upd[0].file_change
        msg = JsonRpcMessage(
            method="session/update",
            params={
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "Write src/a.py",
                    "kind": "edit",
                    "content": blocks,
                }
            },
        )
        ev = extract_tool_event(msg, {}, {}, [])
        assert ev is not None
        return ev.file_change

    def test_claudes_write_frame_declares_a_creation_chip(self):
        """``oldText: null`` must not be read as "no declaration" — it is the adapter
        saying the file did not exist, and the chip's ``before`` for a creation is ``""``.
        """
        chip = self._chip(
            [{"type": "diff", "path": "src/a.py", "oldText": None, "newText": "hello\n"}],
            on_update=False,
        )
        assert chip == {"path": "src/a.py", "before": "", "after": "hello\n"}

    def test_claudes_edit_frame_declares_a_chip_on_the_opening_frame(self):
        """Claude puts its diff block on the ``tool_call`` frame, not only the update —
        which is why the opening frame reads ``content`` at all."""
        chip = self._chip(
            [{"type": "diff", "path": "src/a.py", "oldText": "a\n", "newText": "b\n"}],
            on_update=False,
        )
        assert chip == {"path": "src/a.py", "before": "a\n", "after": "b\n"}

    def test_claudes_completion_frame_redeclares_it_whole_file(self):
        """The adapter's own repair path: when the SDK hands it ``originalFile`` it
        re-declares both sides whole-file, and ``_capture_declared_file_change``'s
        last-declaration-wins is what lets that replace an earlier partial one."""
        chip = self._chip(
            [
                {
                    "type": "diff",
                    "path": "src/a.py",
                    "oldText": 'def f():\n    return "a"\n',
                    "newText": 'def f():\n    return "b"\n',
                }
            ],
            on_update=True,
        )
        assert chip == {
            "path": "src/a.py",
            "before": 'def f():\n    return "a"\n',
            "after": 'def f():\n    return "b"\n',
        }

    def test_a_non_diff_content_block_beside_it_is_not_mistaken_for_one(self):
        """Vacuity floor. Claude also emits ``{type:"content"}`` blocks (``Write`` with no
        ``file_path``), and the branch must key on the declared type, not on position."""
        chip = self._chip(
            [{"type": "content", "content": {"type": "text", "text": "hello\n"}}],
            on_update=False,
        )
        assert chip is None


# ── gap 8: a call to one of our own tools carries that tool's declaration ────

_CORE = "personalclaw-core"
_CLIS = ("claude-code", "codex", "kiro-cli")


class _Turn:
    """One turn's correlation caches, owned exactly as ``AcpSession`` owns them."""

    def __init__(self) -> None:
        self.inputs: dict[str, str] = {}
        self.seen: dict = {}
        self.stats: list[tuple[str, str]] = []

    def tool_call(self, update: dict) -> AgentEvent:
        frame = {"sessionUpdate": "tool_call", "toolCallId": "c1", **update}
        msg = JsonRpcMessage(method="session/update", params={"update": frame})
        ev = extract_tool_event(msg, self.inputs, self.seen, self.stats)
        assert ev is not None
        return acp_event_to_agent_event(ev)

    def permission(self, tool_call: dict) -> AgentEvent:
        msg = JsonRpcMessage(
            id=7,
            method="session/request_permission",
            params={"toolCall": {"toolCallId": "c1", **tool_call}, "options": []},
        )
        return acp_event_to_agent_event(
            build_permission_event(msg, DefaultDialect(), self.inputs, self.seen, {})
        )


def _drive(cli: str, bare: str, args: dict, *, kind: str = "execute") -> list[AgentEvent]:
    """The ``tool_call`` event and the permission event one CLI's call to *bare* produces.

    Each shape is the adapter's own: claude-code titles an MCP call with the tool's name and
    kind ``other`` and repeats both on its approval; codex's approval carries only the id and
    kind ``execute``, so the host fills the title and input in from the opening frame; kiro-cli
    titles the call ``Running: @server/tool``. kiro's KIND for an MCP call was never recorded,
    so ``kind`` lets a rail show that nothing below depends on it.
    """
    turn = _Turn()
    if cli == "claude-code":
        title = f"mcp__{_CORE}__{bare}"
        return [
            turn.tool_call({"title": title, "kind": "other", "rawInput": args}),
            turn.permission({"title": title, "kind": "other", "rawInput": args}),
        ]
    if cli == "codex":
        raw = {"server": _CORE, "tool": bare, "arguments": args}
        return [
            turn.tool_call({"title": f"mcp.{_CORE}.{bare}", "kind": "execute", "rawInput": raw}),
            turn.permission({"kind": "execute", "status": "pending"}),
        ]
    title = f"Running: @{_CORE}/{bare}"
    return [
        turn.tool_call({"title": title, "kind": kind, "rawInput": args}),
        turn.permission({"title": title}),
    ]


def _risk(ev: AgentEvent) -> str:
    """What the card, Trust reads and ``--approval reads`` act on for this event."""
    return resolve_effective_risk(ev.risk_level, ev.title, ev.tool_kind, ev.tool_input)


#: A core tool per declaration, each confirmed against its own definition below.
_CORE_TOOL_RISK = {
    "artifact_delete": "destructive",
    "memory_forget": "destructive",
    "notify": "caution",
    "memory_remember": "caution",
    "memory_recall": "safe",
    "get_context": "safe",
}


class TestACoreToolCarriesItsDeclaration:
    def test_the_premise_is_what_each_tool_declares(self):
        """The table below is not hand-rated: each entry is what the tool's own definition
        declares, so a tool that changes its declaration changes this test's answer."""
        from personalclaw import mcp_core

        for bare, expected in _CORE_TOOL_RISK.items():
            tool = mcp_core.own_tool(bare)
            assert tool is not None, f"{bare} is not on the personalclaw-core surface"
            assert risk_from_annotations(tool["annotations"], trusted=True).value == expected

    @pytest.mark.parametrize("bare,expected", sorted(_CORE_TOOL_RISK.items()))
    @pytest.mark.parametrize("cli", ["claude-code", "codex"])
    def test_every_frame_carries_the_tools_own_declaration(self, cli, bare, expected):
        """Both frames — the opening one the audit row is written from, and the approval the
        gate acts on — so the card and the gate cannot disagree about the same call."""
        for ev in _drive(cli, bare, {}):
            assert ev.risk_level == expected, (cli, ev.kind, ev.title)
            assert _risk(ev) == expected

    @pytest.mark.parametrize("cli", ["claude-code", "codex"])
    def test_a_build_mode_producer_and_a_proposal_carry_their_flags(self, cli):
        saved = _drive(cli, "artifact_save", {})
        proposed = _drive(cli, "propose_template_diff", {})
        assert all(ev.builds and not ev.proposes for ev in saved)
        assert all(ev.proposes and not ev.builds for ev in proposed)

    @pytest.mark.parametrize("kind", ["execute", "other", ""])
    def test_kiro_takes_only_a_destructive_declaration(self, kind):
        """kiro-cli titles its shell calls ``Running: <command>``, so a command that reads
        ``@personalclaw-core/memory_recall`` produces this title too. A read, a Build mode
        producer or a proposal taken from it would let that shell call through; a destructive
        declaration only adds a question, so it is the one part the host takes."""
        for ev in _drive("kiro-cli", "artifact_delete", {}, kind=kind):
            assert ev.risk_level == "destructive" and _risk(ev) == "destructive"
        for bare in ("memory_recall", "notify", "artifact_save", "propose_template_diff"):
            for ev in _drive("kiro-cli", bare, {}, kind=kind):
                assert (ev.risk_level, ev.builds, ev.proposes) == ("", False, False), bare
                assert _risk(ev) == "caution", bare


class TestNothingElseCanWearTheName:
    """Each of these is a call whose title the MODEL writes, or another server's tool: none
    may carry a core tool's declaration, least of all a read."""

    def test_a_shell_approval_titled_with_a_core_tool_name(self):
        """claude-code titles a Bash approval with the model's description of the command."""
        turn = _Turn()
        ev = turn.permission(
            {
                "title": f"mcp__{_CORE}__memory_recall",
                "kind": "execute",
                "rawInput": {"command": "rm -rf build", "description": "x"},
            }
        )
        assert ev.risk_level == ""
        assert _risk(ev) == "destructive"

    def test_a_question_titled_with_a_core_tool_name(self):
        """claude-code titles its question tool with the question, kind ``other`` — the kind
        its MCP calls carry. The arguments are what give it away."""
        turn = _Turn()
        ev = turn.permission(
            {
                "title": f"mcp__{_CORE}__memory_recall",
                "kind": "other",
                "rawInput": {"questions": [{"question": f"mcp__{_CORE}__memory_recall"}]},
            }
        )
        assert ev.risk_level == ""

    def test_a_shell_call_titled_like_a_codex_mcp_call(self):
        """codex's MCP calls are kind ``execute`` too; its shell calls never carry the
        structured ``{server, tool, arguments}`` input."""
        ev = _Turn().tool_call(
            {
                "title": f"mcp.{_CORE}.memory_recall",
                "kind": "execute",
                "rawInput": {"command": ["bash", "-lc", "rm -rf build"]},
            }
        )
        assert ev.risk_level == ""

    def test_another_servers_tool_of_the_same_name(self):
        for title, raw in (
            ("mcp__acme__memory_recall", {}),
            ("mcp.acme.memory_recall", {"server": "acme", "tool": "memory_recall"}),
        ):
            ev = _Turn().tool_call({"title": title, "kind": "other", "rawInput": raw})
            assert ev.risk_level == "", title
            assert _risk(ev) == "caution", title

    def test_a_name_the_server_does_not_serve(self):
        for ev in _drive("claude-code", "memory_recall_everything", {}):
            assert ev.risk_level == ""
            assert _risk(ev) == "caution"


# ── gap 8: WHICH tools clause 3 can be about, and the no-downgrade rail ──────
#
# Both rails below exist because clause 3 ("the approval card for a personalclaw-core
# destructive tool shows its declared risk chip, not the heuristic one") has twice been
# adjudicated against tool names that cannot reach an ACP card, and a `resolve_effective_risk`
# call is happy to answer for a name nobody can send. The resolver arithmetic was right both
# times; the tool UNIVERSE was wrong. So the universe is pinned here, executably, instead of
# being restated in prose that the next audit re-derives from scratch.

#: The native runtime's workspace tools. They are native-ONLY by construction, and
#: ``builtin_tools.py``'s own header says why: "In the ACP architecture the file/edit/shell
#: tools were the external CLI's own built-ins (claude-code provides Read/Write/Bash);
#: ``personalclaw-core`` only layered orchestration (spawn/memory/artifact) on top." The
#: provider exists *because* the native loop has no CLI to supply them.
_PLATFORM_ONLY_TOOLS = ("bash", "read_file", "write_file", "edit_file", "list_dir")

#: Ascending risk, local on purpose: ``task_modes._RISK_ORDER`` is private, and a rail that
#: imported it could not fail if that mapping were the thing that broke.
_ASCENDING = ("safe", "caution", "destructive")


def _acp_core_surface() -> list[dict]:
    """The tool dicts an ACP CLI actually sees through the ``personalclaw-core`` server.

    ``mcp_core._aggregated_list_tools`` is the listing the stdio server answers
    ``tools/list`` with (``run_mcp_core_server``), so it is the only set a CLI can name in a
    ``tool_call`` frame. Listing only — no call, no write, no home touched.
    """
    from personalclaw.mcp_core import _aggregated_list_tools

    return list(_aggregated_list_tools())


class TestClauseThreeCanOnlyBeAboutTheAcpSurface:
    def test_the_platform_shell_and_file_tools_are_not_on_the_acp_surface(self):
        """``mcp__personalclaw-core__bash`` is not a name any ACP frame can carry.

        This rail is the one that would have failed the two adjudications: both reasoned
        about ``bash`` (declared ``RiskLevel.DESTRUCTIVE`` at ``builtin_tools.py:581``) and
        ``read_file`` (declared SAFE) as though they were core tools an ACP CLI calls. They
        are not exposed on that server at all, so a downgrade measured on those names is a
        fact about the resolver, not about any reachable approval card.
        """
        names = {t["name"] for t in _acp_core_surface()}
        # Vacuity floor + positive control: an empty or mistyped surface must not pass. The
        # absences asserted below are only meaningful because this set is real and populated.
        assert len(names) >= 50, f"ACP core surface implausibly small: {len(names)}"
        assert (
            "artifact_delete" in names
        ), "positive control missing — probe is not reading the surface"
        present = [n for n in _PLATFORM_ONLY_TOOLS if n in names]
        assert present == [], (
            "a native-only workspace tool is now on the personalclaw-core ACP surface: "
            f"{present}. The platform shell is the one declared tool whose command decides "
            "its risk, so reaching it over ACP needs its own look first."
        )

    def test_a_core_tool_declares_its_effect_in_one_place(self):
        """``annotations`` is the declaration both paths read (the native provider and the
        ACP lookup). A ``risk_level`` key beside it would be a second one that nothing
        reads, and the two could only ever disagree."""
        surface = _acp_core_surface()
        assert len(surface) >= 50, f"vacuity floor: {len(surface)}"
        assert [t["name"] for t in surface if "risk_level" in t] == []

    def test_no_tool_on_the_acp_surface_is_labelled_LOWER_than_its_native_answer(self):
        """The clause's observable, over the real surface, every CLI, and both frames.

        For each tool an ACP CLI can actually name, compare the level the ACP path produces
        (real frames through ``translate`` and the adapter) against the level the native path
        produces (the tool's own declaration, exactly what ``InProcessMcpToolProvider`` hands
        the native gate). A DOWNGRADE — the ACP card showing less risk than the native card for
        the same tool — is what clause 3 forbids. An UPGRADE is permitted and occurs only on
        kiro-cli, whose reads floor at ``caution`` because its title is not proof of a tool.
        """
        surface = _acp_core_surface()
        assert len(surface) >= 50, f"vacuity floor: {len(surface)}"
        downgrades, unequal = [], []
        compared = 0
        for tool in surface:
            bare = tool["name"]
            native = risk_from_annotations(tool.get("annotations"), trusted=True).value
            for cli in _CLIS:
                for ev in _drive(cli, bare, {}):
                    acp = _risk(ev)
                    compared += 1
                    if _ASCENDING.index(acp) < _ASCENDING.index(native):
                        downgrades.append(f"{cli} {ev.kind} {bare}: acp={acp} < native={native}")
                    if cli != "kiro-cli" and acp != native:
                        unequal.append(f"{cli} {ev.kind} {bare}: acp={acp} native={native}")
        assert compared >= 300, f"vacuity floor: only {compared} comparisons"
        assert downgrades == [], (
            "the ACP approval card now under-states risk for a reachable core tool — the "
            f"tool's declaration no longer reaches the ACP event: {downgrades}"
        )
        assert unequal == [], unequal

    def test_that_no_downgrade_rail_can_actually_fail(self):
        """The failable control for the rail above — its green is otherwise unfalsifiable.

        The same comparison on an event that carries no declaration — what every call was
        before the lookup, and what one becomes if the lookup stops matching — must detect a
        downgrade for a destructive core tool on every CLI.
        """
        for cli in _CLIS:
            for ev in _drive(cli, "artifact_delete", {}):
                bare_event = resolve_effective_risk("", ev.title, ev.tool_kind, ev.tool_input)
                assert _ASCENDING.index(bare_event) < _ASCENDING.index(
                    "destructive"
                ), f"{cli}: expected a detectable downgrade, got {bare_event}"
