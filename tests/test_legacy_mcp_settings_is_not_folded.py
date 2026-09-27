"""The legacy `settings/mcp.json` is not read at start, and a server left in it is named, not run.

Found beside the legacy trigger imports (`test_legacy_trigger_import.py`), by the same question:
which boot pass reads a file nothing writes any more and turns what it holds into authority?
Measured on `main` (47bcbcce8): the dashboard's start-up folded every server in
`<home>/settings/mcp.json` that `mcp.json` did not have into `mcp.json`, on EVERY start — the
boot probe then spawned it, and the next agent rebuild put it in `allowedTools`, "runs without
asking" — and emptied the legacy file, so nothing recorded that it had happened. Every release
since v0.1.0 had already folded and emptied that file, so the only thing the fold still did was
take a file restored from an old snapshot, or put there by anything, and run what it said.

So the fold is gone and nothing reads that file. A server still in it is named by the Doctor.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.resilience.doctor import DoctorContext, run_capability

_LEGACY = {"mcpServers": {"left-behind": {"command": "/usr/bin/true", "args": ["--from-legacy"]}}}


def _plant(home: Path) -> None:
    (home / "settings").mkdir(parents=True, exist_ok=True)
    (home / "settings" / "mcp.json").write_text(json.dumps(_LEGACY))


@pytest.mark.asyncio
async def test_a_dashboard_start_does_not_fold_the_legacy_file_into_mcp_json(tmp_path, monkeypatch):
    """🔴 Red on main: after the start-up hooks ran, `mcp.json` held `left-behind`, and the legacy
    file had been emptied."""
    home = tmp_path / "home"
    home.mkdir()
    _plant(home)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")

    import personalclaw.dashboard.server as server_mod

    runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        canonical = home / "mcp.json"
        held = json.loads(canonical.read_text()) if canonical.is_file() else {}
        assert "left-behind" not in (held.get("mcpServers") or {})

        from personalclaw.mcp_discovery import list_servers

        assert "left-behind" not in {server.name for server in list_servers()}
        # Left exactly as it was: it is the only copy of whatever the owner may still want.
        assert json.loads((home / "settings" / "mcp.json").read_text()) == _LEGACY
    finally:
        await runner.cleanup()


def test_the_doctor_names_a_server_left_in_the_legacy_file(tmp_path):
    """🔴 Red on main: the Doctor had no such check (`tools` was an unknown capability)."""
    _plant(tmp_path)
    report = asyncio.run(run_capability("tools", DoctorContext(home=tmp_path)))

    (probe,) = [p for p in report["probes"] if p["id"] == "tools.legacy_mcp_settings"]
    assert probe["ok"] is False
    assert "left-behind" in probe["detail"]
    assert "no longer reads" in probe["detail"]
    assert "Tools page" in probe["remedy"]
    # Names only — never a value from the file.
    assert "--from-legacy" not in json.dumps(probe)


def test_the_doctor_is_quiet_for_an_emptied_or_already_added_server(tmp_path):
    """Vacuity floor: the husk every release leaves (`{"mcpServers": {}}`) and a server `mcp.json`
    already has are both nothing to report."""
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "mcp.json").write_text(json.dumps({"mcpServers": {}}))
    assert asyncio.run(run_capability("tools", DoctorContext(home=tmp_path)))["ok"] is True

    _plant(tmp_path)
    (tmp_path / "mcp.json").write_text(json.dumps(_LEGACY))
    assert asyncio.run(run_capability("tools", DoctorContext(home=tmp_path)))["ok"] is True
