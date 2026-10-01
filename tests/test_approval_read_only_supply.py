"""Rails for #2821: every approval says whether its call is established as a read.

``ONBOARDING-UX`` Contract C2 names three inputs to the approval brief's blast-radius
derivation — the tool name, the existing risk level, and the backend's read classification.
The third was once computed per approval and dropped before any wire; then it was published as
a command-screening verdict with THREE states, ``None`` meaning "not a shell call", so every
approval for a tool (a declared read included) carried ``is_read_only: null`` — a field named
for a yes-or-no question answering neither.

It is now the call-level answer from ONE owner, ``task_modes.reads_only()``: ``True`` when the
tool declares it only reads or its command screened read-only, ``False`` for every other call,
which PersonalClaw treats as the change it may be. These rails assert the OBSERVABLE supply at
each door rather than that the function exists, and both states at every door, because a
supplier that only ever publishes ``True`` is half wired.

The doors are enumerated deliberately. ``_pending_approvals`` is BOTH the gateway
``approval`` WS payload and the ``GET /api/approvals`` row, so one supply reaches two
surfaces — and :class:`TestBothPermissionSurfacesAgree` pins that they cannot diverge,
since a facet present on one surface and absent on the other was the drift #2821 named.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.approval_answer import YOU

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


# ── the one owner ─────────────────────────────────────────────────────────────


class TestTheSupplierIsOneOwner:
    """``reads_only`` is the effective risk's ``safe``: the declaration and the screened
    command, composed by their one owner."""

    @pytest.mark.parametrize(
        "tool,tool_input,declared,expected",
        [
            # A shell call whose command screened read-only.
            ("bash", {"command": "ls -la"}, "", True),
            ("bash", {"command": "git status"}, "", True),
            # A shell call whose command changes something.
            ("bash", {"command": "rm -rf /tmp/x"}, "", False),
            ("bash", {"command": "cat a > b"}, "", False),
            # A tool that declares it only reads.
            ("memory_recall", {"query": "x"}, "safe", True),
            ("read_file", {"path": "/etc/hosts"}, "safe", True),
            # Not a shell call: `command` is an ordinary argument name, and reading it off a
            # non-shell tool labelled a destructive call as a read (#443).
            ("workflow_delete_def", {"command": "ls"}, "destructive", False),
            # A tool that declares nothing is not established as a read.
            ("read_file", {"path": "/etc/hosts"}, "", False),
            # A shell tool with nothing to screen: nobody read a command, so no read.
            ("bash", {}, "", False),
        ],
    )
    def test_the_verdict_is_a_yes_or_a_no(self, tool, tool_input, declared, expected) -> None:
        """🔴 Before: ``None`` for every call that is not a shell command, a declared read
        included, so the approval said neither yes nor no."""
        from personalclaw.task_modes import reads_only

        assert reads_only(tool, "", tool_input, declared) is expected

    def test_it_screens_the_command_not_the_tool_name(self) -> None:
        """Deliberately NOT ``classify_invocation``.

        ``classify_invocation`` falls back to the tool NAME and answers READ_ONLY for any
        name carrying no mutating hint — which is why ``approval_brief.py`` argues against
        feeding it to a blast-radius derivation. This owner screens the actual command
        string, so the same tool name gets opposite answers for opposite commands. If that
        stops being true, this supplier has become the thing that argument warns about.
        """
        from personalclaw.task_modes import reads_only

        assert reads_only("bash", "", {"command": "ls"}) is True
        assert reads_only("bash", "", {"command": "rm x"}) is False


# ── door: the gateway's pending-approval store (WS payload + GET /api/approvals) ──


def _state():
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


async def _request(state, tool: str, tool_input: str, *, risk_level: str = ""):
    """Fire ``request_approval`` and return the stored row, then resolve the future.

    The call blocks on a human, so it is driven as a task and released immediately —
    what is under test is the row it publishes at the moment the human is asked.
    """
    import asyncio

    task = asyncio.create_task(
        state.request_approval(
            "ap-1", "subagent", tool, tool_input=tool_input, risk_level=risk_level
        )
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if "ap-1" in state._pending_approvals:
            break
    row = dict(state._pending_approvals["ap-1"])
    state.resolve_approval("ap-1", False, by=YOU)
    await task
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected",
    [("ls -la", True), ("rm -rf /tmp/x", False)],
)
async def test_the_pending_approval_row_carries_the_verdict(command, expected) -> None:
    """Both states, because a supplier that only ever publishes ``True`` is half wired."""
    state = _state()
    row = await _request(state, "bash", json.dumps({"command": command}))
    assert row["is_read_only"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("declared", "expected"), [("safe", True), ("caution", False)])
async def test_a_tool_that_runs_no_command_is_answered_by_its_declaration(
    declared, expected
) -> None:
    """🔴 Before: ``None`` for a declared read and a declared change alike."""
    state = _state()
    row = await _request(
        state, "read_file", json.dumps({"path": "/etc/hosts"}), risk_level=declared
    )
    assert row["is_read_only"] is expected


@pytest.mark.asyncio
async def test_an_acp_shell_call_is_screened_by_its_kind_as_the_chat_screens_it() -> None:
    """The kind the call arrived with reaches the queue too, so an ACP agent's shell call titled
    in prose is known as one here, as the chat's card knows it."""
    import asyncio

    state = _state()
    task = asyncio.create_task(
        state.request_approval(
            "ap-1",
            "subagent",
            "List the files",
            tool_input=json.dumps({"command": "ls -la"}),
            tool_kind="execute",
        )
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if "ap-1" in state._pending_approvals:
            break
    row = dict(state._pending_approvals["ap-1"])
    state.resolve_approval("ap-1", False, by=YOU)
    await task
    assert row["is_read_only"] is True


@pytest.mark.asyncio
async def test_the_ws_broadcast_and_the_api_row_are_the_SAME_object() -> None:
    """One supply, two doors — asserted rather than assumed.

    ``GET /api/approvals`` returns ``list(state._pending_approvals.values())`` and the WS
    frame broadcasts that same dict, so this is what makes fixing one fix both. A future
    change that composes a separate WS payload would red here, which is the point.
    """
    state = _state()
    sent: list[tuple[str, dict]] = []
    state.broadcast_ws = lambda kind, data: sent.append((kind, data))  # type: ignore[assignment]
    row = await _request(state, "bash", json.dumps({"command": "ls"}))
    frames = [data for kind, data in sent if kind == "approval"]
    assert frames, "no approval frame was broadcast"
    assert frames[0]["is_read_only"] is True
    assert frames[0]["is_read_only"] == row["is_read_only"]


@pytest.mark.asyncio
async def test_the_verdict_is_derived_before_redaction_rewrites_the_command() -> None:
    """The verdict describes the command the SHELL will run, not the display copy.

    ``request_approval`` stores a redacted ``tool_input`` (URLs and credentials rewritten)
    and screens the raw one. This pins that ordering.

    Honest about its own strength: **no input is known today for which redaction flips the
    verdict.** Every read-only command tried whose text redaction rewrites still screens
    read-only, because ``[REDACTED: credential]`` introduces no shell syntax the screen
    refuses and no option the program's read-only forms lack. So this asserts the observable
    half — the two strings genuinely differ, and the verdict tracks the raw one — rather than
    claiming a demonstrated miscue. Its
    value is that it reds if a future redaction rule (or a new read-only form) makes the
    two disagree, at which point screening the display copy would be a live defect.
    """
    state = _state()
    raw = "grep sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF ."
    row = await _request(state, "bash", json.dumps({"command": raw}))
    # The two strings really do differ — otherwise this test proves nothing at all.
    assert "REDACTED" in str(row["tool_input"])
    assert "fake-anthropic-api03" not in str(row["tool_input"])
    # …and the published verdict is the one the raw command earns.
    from personalclaw.task_modes import is_read_only_bash

    assert row["is_read_only"] is is_read_only_bash(raw) is True


# ── door: the frontend consumes what the backend now supplies ─────────────────


class TestBothPermissionSurfacesAgree:
    """Every surface describes a call from the radius the backend composed for it.

    Source-level assertions, because the surfaces are separate React trees and the drift #2821
    named is precisely "one of them forgot". A surface that derived its own facets from a tool's
    name would describe a shell call by "bash" rather than by what its command does.
    """

    #: The surfaces that read an approval's radius off the wire.
    READERS = [
        "app/useApprovalToasts.ts",
        "pages/ChatPage.tsx",
        "pages/chat/chatTypes.ts",
        "pages/companion/CompanionPage.tsx",
        "pages/loops/LoopApprovals.tsx",
    ]

    def test_every_reader_decodes_the_radius_through_the_one_decoder(self) -> None:
        """Enumerated from the source rather than trusted: a new reader added without the
        decoder reds here instead of shipping a surface that casts the wire."""
        readers = sorted(
            p.relative_to(WEB).as_posix()
            for p in WEB.rglob("*.ts*")
            if not p.name.endswith((".test.ts", ".test.tsx"))
            and "blast_radius" in p.read_text(encoding="utf-8")
        )
        # api.ts declares the field and approvalMeta.ts documents it; the rest READ it.
        assert [r for r in readers if r not in ("lib/api.ts", "pages/chat/approvalMeta.ts")] == (
            self.READERS
        ), readers
        for rel in self.READERS:
            text = (WEB / rel).read_text(encoding="utf-8")
            assert "blastRadiusOf(" in text, f"{rel} reads blast_radius without the one decoder"

    def test_no_surface_derives_a_radius_of_its_own(self) -> None:
        for p in WEB.rglob("*.ts*"):
            if p.name.endswith((".test.ts", ".test.tsx")):
                continue
            text = p.read_text(encoding="utf-8")
            assert "deriveBlastRadius" not in text, p
            assert "readOnlyOf" not in text, p

    def test_the_source_census_is_not_vacuous(self) -> None:
        """Both scans above are greps over a tree; prove the tree was actually read."""
        assert (WEB / "pages" / "chat" / "approvalMeta.ts").exists()
        assert len(list(WEB.rglob("*.ts*"))) > 100
        assert "export function blastRadiusOf" in (
            WEB / "pages" / "chat" / "approvalMeta.ts"
        ).read_text(encoding="utf-8")
