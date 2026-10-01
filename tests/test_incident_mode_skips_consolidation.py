"""Memory consolidation — and the background compression that rides the same tick — skip while
incident mode is on.

Measured with the switch on: `background` model calls from the consolidation pass kept running
(and retrying) beside the owner's chat, competing for the same model. Consolidation is a model
call nobody typed, the unattended work the switch exists to suspend, and nothing on its path asked
`guardrails.incident.incident_active()`.

A skipped pass must lose nothing: the messages stay unconsolidated (no offset advances), so the
first pass after the switch is off picks them up. The one explicit surface, `personalclaw
consolidate`, says why it did nothing instead of the "already in flight" it would otherwise print.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.guardrails import incident
from personalclaw.history import _CONSOLIDATION_THRESHOLD, HistoryConsolidator


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    incident.reset_incident_mirror()
    yield tmp_path
    incident.reset_incident_mirror()


def _consolidator(msg_count: int = _CONSOLIDATION_THRESHOLD) -> HistoryConsolidator:
    log = MagicMock()
    log._read_messages = MagicMock(return_value=[{}] * msg_count)
    log.unconsolidated_count = MagicMock(return_value=msg_count)
    log.recorded_memory_mode = MagicMock(return_value=None)  # a transcript that records no mode
    return HistoryConsolidator(log=log, memory=MagicMock(), sessions=None, history_idle_secs=0)


def test_the_after_turn_pass_is_skipped_and_its_offset_kept() -> None:
    c = _consolidator()

    async def run() -> tuple[int, int]:
        with patch.object(c, "_consolidate", new_callable=AsyncMock) as m:
            incident.activate("a drill")
            c.maybe_consolidate("chat-1")
            await asyncio.gather(*c._tasks, return_exceptions=True)
            skipped_calls = m.await_count
            incident.resume()
            c.maybe_consolidate("chat-1")
            await asyncio.gather(*c._tasks, return_exceptions=True)
            return skipped_calls, m.await_count

    held, after = asyncio.run(run())
    assert held == 0, "consolidation ran with incident mode on"
    assert after == 1, "the skipped messages were lost to the first pass after the switch"
    assert c._prefs_offset["chat-1"] == _CONSOLIDATION_THRESHOLD


def test_the_idle_pass_is_skipped() -> None:
    c = _consolidator()
    c._last_activity["chat-1"] = 0.0  # idle for ages

    async def run() -> tuple[int, int]:
        with patch.object(c, "_consolidate", new_callable=AsyncMock) as m:
            incident.activate("a drill")
            c.check_idle_sessions()
            await asyncio.gather(*c._tasks, return_exceptions=True)
            held = m.await_count
            incident.resume()
            c.check_idle_sessions()
            await asyncio.gather(*c._tasks, return_exceptions=True)
            return held, m.await_count

    held, after = asyncio.run(run())
    assert (held, after) == (0, 1)
    assert "chat-1" in c._history_consolidated


def test_an_ending_session_is_not_consolidated_but_still_sealed() -> None:
    """The session-end seam: its model call is skipped, while the seal — bookkeeping on records
    already stored, no model — still runs, so a session is never left half-ended."""
    c = _consolidator()
    svc = MagicMock()
    svc.seal_session.return_value = 0

    async def run() -> tuple[bool, int]:
        with (
            patch.object(c, "_consolidate", new_callable=AsyncMock) as m,
            patch.object(type(c), "_svc", new=svc),
            patch("personalclaw.memory_vault.mirror_after_consolidation"),
        ):
            incident.activate("a drill")
            ran = await c.consolidate_session("chat-1")
            return ran, m.await_count

    ran, calls = asyncio.run(run())
    assert (ran, calls) == (False, 0)
    svc.seal_session.assert_called_once_with("chat-1")
    assert "chat-1" not in c._running, "a skipped pass left its key marked as running"


def test_the_background_compression_pass_is_skipped() -> None:
    from personalclaw.bg_compress import run_bg_compression_pass

    log = MagicMock()
    with patch("personalclaw.bg_compress._eligible_keys", return_value=["chat-1"]) as eligible:
        incident.activate("a drill")
        assert asyncio.run(run_bg_compression_pass(log)) == []
    eligible.assert_not_called()
    log.prune_orphan_summaries.assert_not_called()


def test_the_consolidate_command_says_incident_mode_is_why(capsys: pytest.CaptureFixture) -> None:
    from personalclaw import cli_server

    consolidator = MagicMock()
    consolidator.consolidate_session = AsyncMock(return_value=False)
    conv_log = MagicMock()
    conv_log.has_log.return_value = True
    incident.activate("a drill")
    with (
        patch.object(
            cli_server, "_build_consolidator", return_value=(None, consolidator, conv_log)
        ),
        pytest.raises(SystemExit) as exit_,
    ):
        asyncio.run(cli_server._consolidate_cmd(argparse.Namespace(all=False, key="chat-1")))
    assert exit_.value.code == 1
    out = capsys.readouterr()
    assert "Already in flight" not in out.out + out.err
    assert "incident mode is on" in out.err.lower()
    assert "personalclaw incident off" in out.err
    consolidator.consolidate_session.assert_not_called()
