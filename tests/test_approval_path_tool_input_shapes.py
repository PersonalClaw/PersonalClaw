"""Rails for the approval path's ``tool_input`` type contract.

The defect, verbatim from the owner's first run on the installed macOS app::

    ERROR personalclaw.subagent: Subagent 4d0e1902 failed
      File "personalclaw/subagent.py", line 2162, in _run_inner
      File "personalclaw/gateway.py", line 718, in _approve
      File "personalclaw/dashboard/state.py", line 1244, in request_approval
      File "personalclaw/security.py", line 665, in redact_exfiltration_urls
      File "personalclaw/security.py", line 629, in scan_exfiltration_urls
    TypeError: expected string or bytes-like object, got 'dict'

``DashboardState.request_approval`` declared ``tool_input: str``. The approval path carries
``AgentEvent.tool_input``, which is typed ``Any`` on purpose — the native loop puts a *dict*
there, an ACP frame puts a pretty-printed *string* (``llm/events.py``) — and the gateway hands
it over unchanged at both of its ``_approve`` call sites. The annotation was a claim, not a
conversion, so a native-loop dict reached ``_URL_RE.finditer(text)`` and raised out of a
SECURITY control, from inside the approval path, killing the subagent that was waiting on the
answer.

**The posture, stated deliberately, because a crashing redactor is fail-closed by accident:**

* the scanner keeps its ``str`` contract and keeps raising on a non-``str``. A redactor that
  silently accepted anything would let the string it SCANS diverge from the string a surface
  SHOWS, and that divergence is how an unredacted secret reaches a user. A ``TypeError`` at the
  chokepoint is a loud bug report about its caller — correct behaviour. What was wrong is that
  the approval path could produce one.
* the conversion happens ONCE, at the boundary that mints the display string, through the same
  ``task_modes.tool_input_to_str`` the chat card's ``input_preview`` already used. So the string
  that is scanned is byte-identical to the string that is shown.
* **nothing is skipped.** A dict is JSON-encoded, so every URL and credential inside a
  structured argument is now scanned — strictly more than before, never less. Wrapping the call
  in ``except`` would have traded a dead subagent for a silently skipped exfiltration scan,
  which is worse than the crash.

Every test below drives the real ``request_approval``, not the scanner in isolation: the bug was
a boundary, and a unit test of ``scan_exfiltration_urls`` would have passed throughout.
"""

from __future__ import annotations

import asyncio
import json

import pytest


def _state():
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


async def _row(state, tool: str, tool_input: object) -> dict:
    """Publish one approval and return its stored row, then release the waiter.

    ``request_approval`` blocks on a human, so it runs as a task; what is under test is the
    row it publishes at the moment the human is asked.
    """
    task = asyncio.create_task(
        state.request_approval("ap-1", "subagent", tool, tool_input=tool_input)
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if "ap-1" in state._pending_approvals:
            break
    else:  # pragma: no cover — only reachable if the call raised before publishing
        await task
        raise AssertionError("request_approval never published a row")
    row = dict(state._pending_approvals["ap-1"])
    state.resolve_approval("ap-1", False)
    assert await task is False
    return row


# ── the offending shape reaches a verdict instead of raising ──────────────────


@pytest.mark.asyncio
async def test_a_dict_tool_input_yields_a_verdict_rather_than_a_TypeError() -> None:
    """THE regression test: the exact shape from the traceback, end to end.

    A ``TypeError`` here is the shipped defect. A returned verdict is the fix.
    """
    state = _state()
    row = await _row(state, "bash", {"command": "ls -la", "description": "list"})
    # The scan ran and produced a display string, not an exception.
    assert isinstance(row["tool_input"], str)
    assert "ls -la" in row["tool_input"]
    # …and the approval itself resolved to a real decision (asserted inside `_row`).


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_input",
    [
        {"command": "ls"},  # native loop
        json.dumps({"command": "ls"}),  # ACP frame
        ["a", "b"],  # a list argument
        None,  # absent
        42,  # a scalar nobody intends but nothing forbids
        "",  # the declared default
    ],
    ids=["dict", "json-str", "list", "none", "int", "empty"],
)
async def test_every_shape_the_approval_path_can_carry_is_handled(tool_input) -> None:
    """``AgentEvent.tool_input`` is ``Any``; enumerate the shapes rather than one.

    The finding asked for "every shape the approval path can carry", not just the dict that
    happened to crash — a rail pinned to one shape is how the next provider's list argument
    reproduces this.
    """
    state = _state()
    row = await _row(state, "bash", tool_input)
    assert isinstance(row["tool_input"], str)


@pytest.mark.asyncio
async def test_a_credential_inside_a_STRUCTURED_argument_is_still_redacted() -> None:
    """The scan got WIDER, not narrower — this is the claim that makes coercion safe.

    Before, a dict raised; nothing was scanned and nothing was shown. The fear with any
    coercion is that it becomes a way to skip the scan, so this drives a real credential in a
    NESTED value and asserts the published row does not carry it.
    """
    state = _state()
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF"
    row = await _row(state, "bash", {"env": {"TOKEN": secret}, "command": "deploy"})
    assert secret not in row["tool_input"]
    assert "command" in row["tool_input"]  # the rest of the argument survived


@pytest.mark.asyncio
async def test_the_screening_verdict_reads_the_RAW_dict_not_the_serialized_copy() -> None:
    """Coercion must not move what ``is_read_only`` is computed from.

    ``read_only_command`` is typed ``object`` precisely so it can read a native dict's
    ``command`` key. Screening the serialized copy would work by accident (it re-parses JSON)
    and would stop working the moment redaction or serialization changed the text, so the
    ordering is pinned: coerce for DISPLAY, screen the RAW value.
    """
    state = _state()
    assert (await _row(state, "bash", {"command": "ls -la"}))["is_read_only"] is True
    state = _state()
    assert (await _row(state, "bash", {"command": "rm -rf /tmp/x"}))["is_read_only"] is False
    state = _state()
    # Not a shell call at all → the question does not apply. `None`, never `False`.
    assert (await _row(state, "read_file", {"path": "/etc/hosts"}))["is_read_only"] is None


# ── the scanner keeps its `str` contract, and that is the stated posture ──────


def test_the_scanner_still_refuses_a_non_string() -> None:
    """Asserted, not assumed: the fix is a boundary, NOT a laxer security control.

    If someone later "hardens" ``scan_exfiltration_urls`` to accept anything, this reds — and
    it should, because at that point the string being scanned and the string being displayed
    are free to diverge, and no test would notice.
    """
    from personalclaw.security import redact_exfiltration_urls, scan_exfiltration_urls

    with pytest.raises(TypeError):
        scan_exfiltration_urls({"command": "ls"})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        redact_exfiltration_urls({"command": "ls"})  # type: ignore[arg-type]


def test_the_coercion_has_one_owner_shared_with_the_chat_card() -> None:
    """Two surfaces describe one call; one serializer, so they cannot disagree.

    ``request_approval`` (the approval prompt) and ``chat_runner``/``chat_utils`` (the
    tool pill's ``input_preview``) both coerce through this function. A second copy would be a
    second convention, and the two surfaces asking a human about one call would describe it
    differently — the same shape #2821 named for the screening verdict.
    """
    from personalclaw.dashboard import approval_state, chat_runner, chat_utils
    from personalclaw.task_modes import tool_input_to_str

    assert approval_state.tool_input_to_str is tool_input_to_str
    assert chat_runner.tool_input_to_str is tool_input_to_str
    assert chat_utils.tool_input_to_str is tool_input_to_str
