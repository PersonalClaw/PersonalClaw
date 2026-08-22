"""End-to-end: an ACP wire frame → the effective-risk decision and the task-mode gate.

`G10` — "effective risk is heuristic and mis-calibrated in both directions". Every
claude ``session/request_permission`` frame arrived with ``tool_kind: ""`` because the
declared kind lives on the *preceding* ``tool_call`` frame, so ``task_modes``' kind→risk
mapping had nothing to read on the approval path; and a shell call whose command text
had not arrived yet resolved to the LITERAL ``"destructive"``, auditing a read-only
``pwd; ls`` as destructive (`O7`, `O10`) while the same missing text made ask mode deny a
read-only ``ls`` (`O13`).

These tests deliberately start at the **JSON-RPC frame** and end at the **risk verdict /
gate answer**, through ``acp_event_to_agent_event`` — never at the layer that authors the
kind. `#1877` measured why that matters: ``translate.py`` authored ``tool_meta`` correctly
and the adapter, documented as mapping "field-for-field", dropped the field, so a live
consumer could not execute. A test that asserts the decoder's output would have passed.
``TestTheAdapterLink`` pins that boundary for ``tool_kind`` explicitly.
"""

from __future__ import annotations

import ast
import pathlib

from personalclaw.acp import translate
from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.dialect import DefaultDialect
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.llm.events import AgentEvent
from personalclaw.task_modes import (
    MUTATING,
    READ_ONLY,
    UNCLASSIFIED,
    classify_invocation,
    resolve_effective_risk,
    task_mode_denies,
)

# ── the two production call shapes, replicated exactly ────────────────────────
#
# Both are pinned structurally by ``TestTheProductionCallShapes`` below, so a change
# in ``chat_runner`` that stops routing through ``resolve_effective_risk`` — or starts
# feeding the declared kind to the task-mode gate, which §2.2 forbids — reds here
# instead of silently making these tests measure a path production no longer takes.


def _production_risk(event: AgentEvent) -> str:
    """What ``chat_runner`` records as ``metadata.risk`` and shows on the card."""
    return resolve_effective_risk(
        getattr(event, "risk_level", "") or "", event.title, event.tool_kind, event.tool_input
    )


def _production_gate(event: AgentEvent, mode: str) -> str:
    """What ``chat_runner``'s task-mode gate answers for this permission frame.

    ``tool_kind`` is passed EMPTY on purpose — that is production's deliberate §2.2
    choice (a CLI-declared "read" must not turn a deny-by-default into an allow), and
    replicating it is what makes the ask-mode assertion below measure the real gate.
    """
    return task_mode_denies(mode, event.title, "", event.tool_input)


def _tool_call_frame(tool_call_id: str, title: str, kind: str, raw_input: object) -> JsonRpcMessage:
    update: dict = {"sessionUpdate": "tool_call", "toolCallId": tool_call_id, "title": title}
    if kind:
        update["kind"] = kind
    update["rawInput"] = raw_input
    update["status"] = "pending"
    return JsonRpcMessage(method="session/update", params={"update": update})


def _permission_frame(
    request_id: int, tool_call_id: str, title: str, *, kind: str = "", raw_input: object = None
) -> JsonRpcMessage:
    """A ``session/request_permission`` frame.

    Defaults to the shape AAP-1 measured on claude-code-acp (`O5`): a ``toolCall`` with
    only ``toolCallId`` and ``title`` — no ``kind``, no input. ACP types this field as a
    ``ToolCallUpdate``, so ``kind`` and ``rawInput`` are both legal here (codex sends
    ``kind``); the parameters let a test send either.
    """
    tool_call: dict = {"toolCallId": tool_call_id, "title": title}
    if kind:
        tool_call["kind"] = kind
    if raw_input is not None:
        tool_call["rawInput"] = raw_input
    return JsonRpcMessage(
        id=request_id,
        method="session/request_permission",
        params={"toolCall": tool_call, "options": []},
    )


class _Turn:
    """One turn's correlation caches, owned exactly as ``AcpSession`` owns them."""

    def __init__(self) -> None:
        self.inputs: dict[str, str] = {}
        self.kinds: dict[str, str] = {}
        self.offered: dict[str, list[dict[str, str]]] = {}
        self.stats: list[tuple[str, str]] = []

    def tool_call(self, msg: JsonRpcMessage) -> AgentEvent:
        ev = translate.extract_tool_event(msg, self.inputs, self.kinds, self.stats)
        assert ev is not None
        return acp_event_to_agent_event(ev)

    def tool_call_update(self, msg: JsonRpcMessage) -> list[AgentEvent]:
        return [
            acp_event_to_agent_event(e)
            for e in translate.extract_tool_update_events(msg, self.inputs, self.kinds)
        ]

    def permission(self, msg: JsonRpcMessage) -> AgentEvent:
        return acp_event_to_agent_event(
            translate.build_permission_event(
                msg, DefaultDialect(), self.inputs, self.kinds, self.offered
            )
        )


# ── direction 1: a read-only call must not be labelled destructive ────────────
class TestNotLabelledDestructive:
    def test_pending_shell_frame_is_not_audited_as_destructive(self):
        """`O10`: ``Terminal``/``execute``/``risk: destructive`` for a read-only command.

        ACP agents open a tool call with ``rawInput: {}`` + ``status: pending`` and fill
        the input in a later ``tool_call_update`` (``extract_tool_update_events``' own
        docstring says so). The SEL ``invoked`` row is written from THIS frame, so the
        resolver was handed ``kind: "execute"`` and no command — and returned the literal
        ``"destructive"``. That is a verdict about the command minted from the command's
        absence, and it is what stamped a read-only ``pwd; ls`` destructive.
        """
        turn = _Turn()
        opened = turn.tool_call(_tool_call_frame("t1", "Terminal", "execute", {}))
        assert opened.tool_kind == "execute", "the kind IS on this frame"
        assert opened.tool_input == "", "and the command is NOT — that is the whole defect"

        assert (
            classify_invocation(opened.title, opened.tool_kind, opened.tool_input) is UNCLASSIFIED
        )
        risk = _production_risk(opened)
        assert risk != "destructive", "absence of a command is not evidence of destruction"
        assert risk == "caution", "but it is not safe either — the user still gets a card"

        # The same call, once the command arrives, resolves on the command itself.
        turn.tool_call_update(
            JsonRpcMessage(
                method="session/update",
                params={
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "rawInput": {"command": "pwd; ls -la"},
                    }
                },
            )
        )
        card = turn.permission(_permission_frame(7, "t1", "Terminal"))
        assert card.tool_kind == "execute", "correlated from the tool_call frame"
        assert _production_risk(card) == "safe", "a read-only command is SAFE, not destructive"

    def test_a_genuinely_destructive_command_keeps_its_verdict(self):
        """The fix must not buy its calibration by under-labelling the real thing."""
        turn = _Turn()
        turn.tool_call(_tool_call_frame("t2", "Terminal", "execute", {"command": "rm -rf build"}))
        card = turn.permission(_permission_frame(8, "t2", "Terminal"))
        assert classify_invocation(card.title, card.tool_kind, card.tool_input) is MUTATING
        assert _production_risk(card) == "destructive"

    def test_a_declared_read_kind_reaches_the_risk_decision(self):
        """`O5`: every claude permission frame arrived ``tool_kind: ""``.

        Consequence beyond the label: trust-reads auto-approves only ``effective_risk ==
        "safe"``, so a wire-declared ``kind: "read"`` that reached the resolver as ``""``
        floored at ``caution`` and a plain file read raised a card forever — the coarse
        "name/kind-based" downgrade the trust_reads row records as PARTIAL.
        """
        turn = _Turn()
        turn.tool_call(_tool_call_frame("t3", "Read File", "read", {"abs_path": "/tmp/probe.txt"}))
        card = turn.permission(_permission_frame(9, "t3", "Read File"))
        # The VERDICT first, deliberately: dropping the correlation must red on the risk
        # this surface reports, not merely on an empty field. A field assertion alone
        # would let a future change satisfy the test by populating the field with
        # something the resolver ignores.
        assert _production_risk(card) == "safe"
        assert card.tool_kind == "read"

    def test_the_frames_own_kind_still_wins(self):
        """codex DOES declare ``kind`` on its permission payload (`G18`). It is truth."""
        turn = _Turn()
        turn.tool_call(_tool_call_frame("t4", "Terminal", "execute", {}))
        card = turn.permission(_permission_frame(10, "t4", "apply_patch", kind="edit"))
        assert card.tool_kind == "edit", "the frame's own declaration is not overwritten"


# ── direction 2: ask mode must not deny a read-only ls ────────────────────────
class TestAskModeAllowsAReadOnlyLs:
    def test_command_carried_inline_on_the_permission_frame(self):
        """`O13`: "a follow-up read-only ``ls`` bash was denied by the same gate".

        The gate keys off the command text. ACP types the permission frame's ``toolCall``
        as a ``ToolCallUpdate``, whose input field is named ``rawInput`` — the same key
        ``extract_tool_event`` reads. The permission decoder read only ``input``/
        ``params``, so a frame that carried the command INLINE still reached the gate
        with an empty input, and the title-hint fallback ("Running: …" trips the ``run``
        mutating hint) denied a read-only ``ls``.
        """
        turn = _Turn()  # no preceding tool_call frame: the command is only on this one
        card = turn.permission(
            _permission_frame(11, "t5", "Running: ls -la", raw_input={"command": "ls -la"})
        )
        # The GATE ANSWER first, deliberately: this must red on the denial of a
        # read-only `ls` — the user-visible defect — not merely on an empty field.
        assert _production_gate(card, "ask") == "", "a read-only ls RUNS in ask mode"
        assert _production_gate(card, "plan") == "", "and in plan mode"
        assert _production_risk(card) == "safe"
        assert classify_invocation(card.title, "", card.tool_input) is READ_ONLY
        assert card.tool_input, "the command survived the decoder"

    def test_a_mutation_is_still_denied_in_ask_mode(self):
        turn = _Turn()
        turn.tool_call(
            _tool_call_frame("t6", "Write", "edit", {"abs_path": "/tmp/x", "content": ""})
        )
        card = turn.permission(_permission_frame(12, "t6", "Write"))
        assert "Ask mode" in _production_gate(card, "ask")

    def test_a_second_request_for_the_same_call_still_sees_the_command(self):
        """The input cache is READ, not popped.

        A popped cache makes the second permission request for one ``toolCallId`` look
        like a tool whose command the host cannot see — the exact state that used to mint
        a verdict out of nothing, reached without any adapter misbehaving.
        """
        turn = _Turn()
        turn.tool_call(_tool_call_frame("t7", "Terminal", "execute", {"command": "ls -la"}))
        first = turn.permission(_permission_frame(13, "t7", "Terminal"))
        second = turn.permission(_permission_frame(14, "t7", "Terminal"))
        assert first.tool_input == second.tool_input != ""
        assert _production_gate(second, "ask") == ""
        assert _production_risk(second) == "safe"


# ── requirement: an unknown kind stays representable and fails closed ─────────
class TestUnknownStaysRepresentable:
    def test_unclassified_is_a_third_answer_not_a_missing_one(self):
        """``tool_kind: str = ""`` is a DEFAULT (``llm/events.py:46``), so an unsupplied
        kind and a genuinely-unknown one are the same bytes. The classifier therefore
        answers three ways, and absence gets its own value instead of being folded into
        either of the two that assert something.
        """
        assert classify_invocation("Terminal", "execute", "") is UNCLASSIFIED
        assert classify_invocation("Terminal", "execute", {"command": "ls"}) is READ_ONLY
        assert classify_invocation("Terminal", "execute", {"command": "rm -rf /"}) is MUTATING

    def test_unclassified_never_resolves_permissive(self):
        """Polarity: unknown RISK fails safe, not permissive.

        ``safe`` is the one verdict with teeth — trust-reads auto-approves it with no
        card. An unreadable command must never earn it.
        """
        assert resolve_effective_risk("", "Terminal", "execute", "") == "caution"
        assert resolve_effective_risk("", "Terminal", "command", "") == "caution"
        # A real declaration is a fact about the TOOL and still wins over the floor.
        assert resolve_effective_risk("destructive", "Terminal", "execute", "") == "destructive"

    def test_unclassified_is_denied_by_the_task_mode_gate(self):
        """The GATE's conservative answer is the opposite of the LABEL's: deny.

        Honest labelling must not become a hole. A command the host cannot read does not
        run under a read-only posture, even though it is no longer *called* destructive.
        """
        for mode in ("ask", "plan", "build"):
            assert task_mode_denies(mode, "Terminal", "execute", "") != ""
        assert task_mode_denies("agent", "Terminal", "execute", "") == ""


# ── the boundaries this fix depends on ────────────────────────────────────────
class TestTheAdapterLink:
    def test_acp_event_to_agent_event_carries_tool_kind(self):
        """`#1877`'s shape: the adapter is documented "field-for-field" and dropped one.

        Asserted on the real adapter, not a hand-built object, because a hand-built
        downstream object is a shape production cannot produce.
        """
        turn = _Turn()
        assert turn.tool_call(_tool_call_frame("t8", "Read File", "read", {})).tool_kind == "read"
        turn2 = _Turn()
        turn2.tool_call(_tool_call_frame("t9", "Terminal", "execute", {}))
        assert turn2.permission(_permission_frame(15, "t9", "Terminal")).tool_kind == "execute"


class TestTheProductionCallShapes:
    """Pin the two call sites these tests replicate, so the replication cannot drift."""

    @staticmethod
    def _chat_runner() -> ast.Module:
        import personalclaw.dashboard.chat_runner as cr

        return ast.parse(pathlib.Path(cr.__file__).read_text())

    def test_the_card_and_the_sel_row_share_one_risk_vocabulary(self):
        """Requirement: the SEL row and the approval notification derive from ONE place.

        Both read the local ``effective_risk``, and that name is bound from
        ``resolve_effective_risk`` — ``task_modes``' resolver, which is itself the only
        consumer of ``classify_invocation``. No second risk vocabulary.
        """
        tree = self._chat_runner()
        bound_from_resolver = {
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "resolve_effective_risk"
        }
        assert "effective_risk" in bound_from_resolver

        mirror_args = [
            node.args
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_mirror_approval_to_inbox"
        ]
        assert len(mirror_args) == 1, "one notification call site"
        risk_arg = mirror_args[0][3]
        assert isinstance(risk_arg, ast.Name) and risk_arg.id == "effective_risk"

    def test_the_task_mode_gate_is_not_handed_the_declared_kind(self):
        """§2.2's recorded decision: the carried kind informs the LABEL, never the gate.

        A CLI that labels its own mutation "read" must not be able to turn the gate's
        deny-by-default into an allow. This asserts the gate call still passes a literal
        empty kind — if that ever changes, ``_production_gate`` above is measuring a path
        production no longer takes.
        """
        tree = self._chat_runner()
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "task_mode_denies"
        ]
        assert len(calls) == 1
        kind_arg = calls[0].args[2]
        assert isinstance(kind_arg, ast.Constant) and kind_arg.value == ""
