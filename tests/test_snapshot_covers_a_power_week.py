"""A power user's week, written by the REAL writers, comes back whole from a snapshot.

Settings B17 and day 8's "Durability degraded": after a week of normal use `audit_home()` named
the rotated audit log (10.4 MB, 19,212 rows after Audit → Rotate), `incident.json` (Guardrails →
Incident mode), `routing_stats.json`, `trigger-idle/` and `capture/` as "in NO snapshot" — and the
Doctor was right. `personalclaw snapshot` is what the pre-1.0 release notes tell a user to run
before upgrading, so every one of those was a restore that silently came back without it.

🪤 THE FIXTURE DRIVES THE PRODUCERS, NEVER HAND-TYPED FILENAMES. A test that writes
`home / "incident.json"` itself proves only that a string matches a string; the rotated archive's
name is minted by `SecurityEventLog.rotate()` from a timestamp, and the others by their modules'
own path helpers. So each store below is written by the code a user's week runs, the archive path
is read back from what `rotate()` returned, and the assertions follow the producers wherever they
put their files.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory
from personalclaw.snapshot import restore_main, snapshot_main


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: False)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    # Resolved, because the SEL resolves `$PERSONALCLAW_HOME` itself and the archive path it
    # returns is compared against this one.
    home = (tmp_path / "home").resolve()
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _a_week(home: Path) -> dict[str, str]:
    """Run the five writers the validator's week ran. Returns {home-relative path: content}."""
    from personalclaw.guardrails import incident
    from personalclaw.inbound import capture_store
    from personalclaw.routing import stats
    from personalclaw.sel import SecurityEvent, sel
    from personalclaw.triggers import idle_poll

    log = sel()
    for i in range(3):
        log.log(
            SecurityEvent(
                event_id=f"week-{i}",
                timestamp="2026-09-25T10:00:00+00:00",
                event_type="tool_call",
                caller_identity="dashboard",
                agent="personalclaw",
                source="dashboard",
                operation="shell",
                outcome="completed",
            )
        )
    rotated = log.rotate(archive=True)
    assert rotated["rotated"] and rotated["entries_before"] == 3, rotated

    incident.activate("drill: a human stopped unattended work")
    incident.reset_incident_mirror()

    stats.record_routing_stats(
        {
            "audit_id": "aud-1",
            "use_case": "chat",
            "query_class": "general",
            "provider": "ollama",
            "model": "small",
            "passed": True,
            "latency_ms": 120.0,
            "dollars_est": 0.0,
        },
        home=home,
        now="2026-09-25T10:00:00Z",
    )

    idle_poll.save_state(
        "idle:nudge-me", idle_poll.IdleState(armed_at=1.0, cycle_count=4), base_dir=home
    )

    session = capture_store.record_turn(
        client_id="claude-code",
        dialect="anthropic",
        model_requested="claude-opus-4",
        request_body={"messages": [{"role": "user", "content": "refactor the parser"}]},
        response_body={"content": [{"type": "text", "text": "done"}]},
    )
    assert session, "the capture store recorded nothing"

    written = {
        Path(rotated["archive_path"]).relative_to(home).as_posix(): "",
        "incident.json": "",
        "routing_stats.json": "",
        "trigger-idle/idle-nudge-me.json": "",
        f"capture/{session}.jsonl": "",
    }
    for rel in written:
        path = home / rel
        assert path.is_file(), f"the producer did not write {rel}, so this test proves nothing"
        written[rel] = path.read_text(encoding="utf-8")
    return written


def _snapshot(home: Path, out: Path) -> Path:
    assert snapshot_main([str(out)]) == 0
    (tarball,) = out.glob("personalclaw-snapshot-*.tar.gz")
    return tarball


def _members(tarball: Path) -> set[str]:
    with tarfile.open(tarball) as tar:
        return {m.name.split("/", 1)[1] for m in tar.getmembers() if m.isfile() and "/" in m.name}


def test_the_rotated_audit_log_is_archived_where_the_inventory_claims_it(home):
    written = _a_week(home)
    (archive,) = [rel for rel in written if rel.startswith("sel_archive/")]
    claim = inventory.claim_for(archive)
    assert claim is not None and claim.id == "security_events_archive", archive
    assert not list(home.glob("security_events.*.bak.jsonl")), "an archive was left unclaimed"


def test_a_power_week_leaves_nothing_unclaimed(home):
    _a_week(home)
    audit = inventory.audit_home(home)
    assert (
        audit.unclaimed == []
    ), f"the Doctor would report these as in NO snapshot: {audit.unclaimed}"
    assert audit.undeclared_dbs == []
    assert audit.claimed >= 5, "nothing was claimed, so a clean audit is vacuous"


def test_the_snapshot_carries_every_store_the_week_wrote(home, tmp_path):
    written = _a_week(home)
    members = _members(_snapshot(home, tmp_path / "out"))
    missing = sorted(rel for rel in written if rel not in members)
    assert not missing, f"`personalclaw snapshot` left these out: {missing}"


def test_a_wiped_home_gets_the_week_back(home, tmp_path):
    written = _a_week(home)
    tarball = _snapshot(home, tmp_path / "out")
    kept = tmp_path / "kept.tar.gz"
    shutil.copy2(tarball, kept)

    shutil.rmtree(home)
    home.mkdir()
    assert restore_main([str(kept), "--mode", "replace"]) == 0

    for rel, body in written.items():
        restored = home / rel
        assert restored.is_file(), f"{rel} did not come back from the restore"
        assert restored.read_text(encoding="utf-8") == body, f"{rel} came back different"
    # The stop a human set is still set: a restore must never quietly resume unattended work.
    assert json.loads((home / "incident.json").read_text())["active"] is True


def test_a_failed_archive_is_answered_as_a_failure_not_as_a_fresh_chain(home, monkeypatch):
    """The live log is kept when its archive cannot be written (`test_sel_rotation`), but the
    route still answered 200 with `rotated: false` — and Settings → Audit renders any 200 as
    "Audit log reset — a fresh chain has started." A user who confirmed Archive & reset was
    told it worked while nothing had moved. The route now refuses with a registered code, which
    the panel's error branch already names."""
    import asyncio

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import core
    from personalclaw.sel import SecurityEvent, SecurityEventLog, sel

    sel().log(
        SecurityEvent(
            event_id="keep-me",
            timestamp="2026-09-25T10:00:00+00:00",
            event_type="tool_call",
            caller_identity="dashboard",
            agent="personalclaw",
            source="dashboard",
            operation="shell",
            outcome="completed",
        )
    )

    def _refuse(self):
        raise OSError("disk full")

    monkeypatch.setattr(SecurityEventLog, "_archive_live", _refuse)

    async def _body():
        return {}

    request = make_mocked_request("POST", "/api/sel/rotate")
    request.json = _body
    response = asyncio.run(core.api_sel_rotate(request))

    assert response.status == 500
    assert json.loads(response.body)["error"]["code"] == "sel_archive_failed"
    live = home / "security_events.jsonl"
    assert live.is_file() and "keep-me" in live.read_text(encoding="utf-8")
