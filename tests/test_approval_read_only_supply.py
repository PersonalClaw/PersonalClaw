"""Rails for #2821: the command-screening verdict had no supplier.

``ONBOARDING-UX`` Contract C2 names three inputs to the approval brief's blast-radius
derivation — the tool name, the existing risk level, and the command-screening
classification. The first two were supplied. The third was COMPUTED per approval
(``is_read_only_bash`` behind ``perm_meta["is_read_only"]``) and then dropped: it was not
on the ``approval`` WS payload, not on ``GET /api/approvals``, and no frontend call site
passed ``readOnlyCommand``. So ``deriveBlastRadius``'s
``input.readOnlyCommand === true`` branch was unreachable in production.

The fix is a pass-through with ONE owner, not a second classifier —
``task_modes.read_only_command()``. These rails assert the OBSERVABLE supply at each door
rather than that the function exists, because "the constant is defined" is exactly the
assertion that let this survive as a source comment for two atoms.

**Why the verdict is tri-state and why that is asserted everywhere.** ``None``/absent
means "this call runs no shell, so the question does not apply"; ``False`` means
"screened, and it mutates". Collapsing those two is the whole failure mode: the honesty
contract downstream reads absence as *not established*, never as *verified absent*, so a
``False`` flattened to absent silently loses a negative verdict. Every door below asserts
all three states, not just the positive one.

The doors are enumerated deliberately. ``_pending_approvals`` is BOTH the gateway
``approval`` WS payload and the ``GET /api/approvals`` row, so one supply reaches two
surfaces — and :class:`TestBothPermissionSurfacesAgree` pins that they cannot diverge,
since a facet present on one surface and absent on the other was the drift #2821 named.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


# ── the one owner ─────────────────────────────────────────────────────────────


class TestTheSupplierIsOneOwner:
    """``read_only_command`` composes the scoped extractor with the screener."""

    @pytest.mark.parametrize(
        "tool,tool_input,expected",
        [
            # Screened, read-only.
            ("bash", {"command": "ls -la"}, True),
            ("bash", {"command": "git status"}, True),
            # Screened, NOT read-only — the state that must not flatten to absent.
            ("bash", {"command": "rm -rf /tmp/x"}, False),
            ("bash", {"command": "cat a > b"}, False),
            # Not a shell call at all: `command` is an ordinary argument name, and
            # reading it off a non-shell tool labelled a destructive call as a read
            # (#443). The answer is "does not apply", never False-as-in-mutating.
            ("workflow_delete_def", {"command": "ls"}, None),
            ("read_file", {"path": "/etc/hosts"}, None),
            # A shell tool with nothing to screen.
            ("bash", {}, None),
        ],
    )
    def test_the_verdict_is_tri_state(self, tool, tool_input, expected) -> None:
        from personalclaw.task_modes import read_only_command

        assert read_only_command(tool, "", tool_input) is expected

    def test_it_screens_the_command_not_the_tool_name(self) -> None:
        """Deliberately NOT ``classify_invocation``.

        ``classify_invocation`` falls back to the tool NAME and answers READ_ONLY for any
        name carrying no mutating hint — which is why ``approval_brief.py`` argues against
        feeding it to a blast-radius derivation. This owner screens the actual command
        string, so the same tool name gets opposite answers for opposite commands. If that
        stops being true, this supplier has become the thing that argument warns about.
        """
        from personalclaw.task_modes import read_only_command

        assert read_only_command("bash", "", {"command": "ls"}) is True
        assert read_only_command("bash", "", {"command": "rm x"}) is False


# ── door: the gateway's pending-approval store (WS payload + GET /api/approvals) ──


def _state():
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


async def _request(state, tool: str, tool_input: str):
    """Fire ``request_approval`` and return the stored row, then resolve the future.

    The call blocks on a human, so it is driven as a task and released immediately —
    what is under test is the row it publishes at the moment the human is asked.
    """
    import asyncio

    task = asyncio.create_task(
        state.request_approval("ap-1", "subagent", tool, tool_input=tool_input)
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if "ap-1" in state._pending_approvals:
            break
    row = dict(state._pending_approvals["ap-1"])
    state.resolve_approval("ap-1", False)
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
async def test_a_non_shell_tool_publishes_no_verdict_rather_than_False() -> None:
    """``None``, not ``False``. See this module's header — this IS the failure mode."""
    state = _state()
    row = await _request(state, "read_file", json.dumps({"path": "/etc/hosts"}))
    assert row["is_read_only"] is None


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
    read-only, because ``[REDACTED: credential]`` introduces none of the characters
    ``_UNSAFE_SHELL_RE`` rejects and the ``--help``/``--version`` suffix checks survive
    substitution. So this asserts the observable half — the two strings genuinely differ,
    and the verdict tracks the raw one — rather than claiming a demonstrated miscue. Its
    value is that it reds if a future redaction rule (or a new screener prefix) makes the
    two disagree, at which point screening the display copy would be a live defect.
    """
    state = _state()
    raw = "grep sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF ."
    row = await _request(state, "bash", json.dumps({"command": raw}))
    # The two strings really do differ — otherwise this test proves nothing at all.
    assert "REDACTED" in str(row["tool_input"])
    assert "sk-ant-api03" not in str(row["tool_input"])
    # …and the published verdict is the one the raw command earns.
    from personalclaw.task_modes import is_read_only_bash

    assert row["is_read_only"] is is_read_only_bash(raw) is True


# ── door: the frontend consumes what the backend now supplies ─────────────────


class TestBothPermissionSurfacesAgree:
    """The chat card and the companion queue derive from the same inputs.

    Source-level assertions, because the two surfaces are separate React trees and the
    drift #2821 named is precisely "one of them forgot". A rail that only checked the
    chat path would have passed on the exact state the issue was filed about.
    """

    def test_every_derive_call_site_passes_the_screening_verdict(self) -> None:
        """``deriveBlastRadius`` has three call sites; all three must supply the input.

        Enumerated from the source rather than trusted: the census is the assertion, so a
        FOURTH call site added later without the input reds here instead of shipping a
        surface that quietly describes a read-only call as unscreened.
        """
        sites = sorted(
            p.relative_to(WEB).as_posix()
            for p in WEB.rglob("*.ts*")
            if not p.name.endswith(".test.ts") and not p.name.endswith(".test.tsx")
            for _ in range(1)
            if "deriveBlastRadius(" in p.read_text(encoding="utf-8")
        )
        assert sites == [
            "app/approvalToast.ts",
            "pages/chat/ApprovalCard.tsx",
            "pages/chat/approvalMeta.ts",
            "pages/companion/CompanionPage.tsx",
        ], sites
        for rel in sites:
            if rel == "pages/chat/approvalMeta.ts":
                continue  # the definition itself, not a call site
            text = (WEB / rel).read_text(encoding="utf-8")
            assert "readOnlyCommand" in text, f"{rel} derives a blast radius without the verdict"

    def test_every_wire_parse_decodes_through_the_one_decoder(self) -> None:
        """``is_read_only`` must never be read by casting or by truthiness.

        ``""`` is falsy but not ``=== false``, so a raw pass-through lands in the
        "unknown" branch and drops a negative verdict. ``readOnlyCommandOf`` owns the
        tri-state; every reader goes through it.
        """
        readers = {
            p.relative_to(WEB).as_posix()
            for p in WEB.rglob("*.ts*")
            if not p.name.endswith(".test.ts")
            and not p.name.endswith(".test.tsx")
            and "is_read_only" in p.read_text(encoding="utf-8")
        }
        # api.ts and approvalMeta.ts declare/document the field; the rest READ it.
        for rel in readers - {"lib/api.ts", "pages/chat/approvalMeta.ts"}:
            text = (WEB / rel).read_text(encoding="utf-8")
            assert "readOnlyCommandOf(" in text, (
                f"{rel} reads is_read_only without the tri-state decoder; "
                "an empty string is falsy but not === false"
            )

    def test_the_source_census_is_not_vacuous(self) -> None:
        """Both scans above are greps over a tree; prove the tree was actually read."""
        assert (WEB / "pages" / "chat" / "approvalMeta.ts").exists()
        assert len(list(WEB.rglob("*.ts*"))) > 100
        assert "readOnlyCommandOf" in (WEB / "pages" / "chat" / "approvalMeta.ts").read_text(
            encoding="utf-8"
        )
