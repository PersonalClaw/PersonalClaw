"""Staged pack triggers — the enable path into Automations (AGENT-PACKS §3.1/§4, AP-7).

The direct sibling of ``test_packs_kinds.py``'s roster-deploy tests, and the load-bearing test
here is the SAFETY one: a deployed trigger must land in the live store DISABLED, because a pack
that could arm automation through its own enable path would be switching on work the user never
chose. The staged file even SAYS ``enabled=True`` on purpose, so the assertion proves the deploy
forces the flag rather than trusting the bytes.

The rest prove a broken staged file is SKIPPED (never raised, never armed), the deploy is
idempotent, and the state change is SEL-audited.

Every test binds ``PERSONALCLAW_HOME`` to a tmp dir — ``config_dir()``, the trigger store and SEL
all read it live — so nothing here can touch the real home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.packs.triggers import deploy_triggers, staged_trigger_ids
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


def _stage(home: Path, stage: str, trigger: dict) -> Path:
    """Write one staged trigger exactly where the importer stages it."""
    staged = home / "packs" / "staged" / stage / "triggers"
    staged.mkdir(parents=True, exist_ok=True)
    path = staged / f"{trigger['id']}.json"
    path.write_text(json.dumps(trigger, indent=2), encoding="utf-8")
    return path


def _clock(trigger_id: str, *, enabled: bool) -> dict:
    """A runnable clock trigger — a realistic pack digest, parseable by `parse_trigger`."""
    return {
        "id": trigger_id,
        "name": trigger_id.replace("-", " ").title(),
        "kind": "clock",
        "spec": {"kind": "cron", "expr": "0 9 * * 1"},
        "enabled": enabled,
    }


def test_a_staged_trigger_deploys_into_the_store_disabled(home):
    """The safety invariant: a staged file that SAYS enabled=True still lands DISABLED.

    Staged enabled=True deliberately — the deploy must not trust the staged bytes. A pack trigger
    arriving enabled would be an automation the user never switched on.
    """
    _stage(home, "cfo", _clock("cfo-spending-digest", enabled=True))

    result = deploy_triggers("cfo")
    assert result == {"deployed": ["cfo-spending-digest"], "skipped": []}

    loaded = TriggerStore(home).get("cfo-spending-digest")
    assert loaded is not None
    assert loaded.trigger.enabled is False  # forced, not trusted — the whole point
    assert loaded.trigger.kind == "clock"
    # It is now visible in the store the Automations page reads.
    assert [t.id for t in TriggerStore(home).list_triggers()] == ["cfo-spending-digest"]


def test_a_broken_staged_trigger_is_skipped_not_raised(home):
    """A staged file the entity refuses (or unreadable JSON) is reported, never raised — one
    corrupt file must not fail the whole deploy, and must never reach the live store."""
    _stage(home, "cfo", _clock("good", enabled=False))
    # A cron clock with no `expr` → an error-severity issue from `parse_trigger`.
    _stage(
        home, "cfo", {"id": "bad-cron", "name": "Bad", "kind": "clock", "spec": {"kind": "cron"}}
    )
    # Not even valid JSON.
    corrupt = home / "packs" / "staged" / "cfo" / "triggers" / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")

    result = deploy_triggers("cfo")
    assert result["deployed"] == ["good"]
    assert set(result["skipped"]) == {"bad-cron", "corrupt"}

    # The broken ones never reached the live store.
    assert [t.id for t in TriggerStore(home).list_triggers()] == ["good"]


def test_deploy_is_idempotent(home):
    """Re-deploying upserts the same ids rather than duplicating or dropping them."""
    _stage(home, "cfo", _clock("cfo-spending-digest", enabled=False))
    first = deploy_triggers("cfo")
    second = deploy_triggers("cfo")
    assert first == second == {"deployed": ["cfo-spending-digest"], "skipped": []}
    ids = [t.id for t in TriggerStore(home).list_triggers()]
    assert ids.count("cfo-spending-digest") == 1


def test_the_deploy_is_sel_audited(home):
    """Making automations visible + manageable is a state change, so it leaves an audit row."""
    from personalclaw.sel import sel

    _stage(home, "cfo", _clock("cfo-spending-digest", enabled=False))
    deploy_triggers("cfo")

    row = next((e for e in sel().recent(50) if e.get("operation") == "pack_triggers_deploy"), None)
    assert row is not None, "the deploy wrote no SEL audit row"
    assert row["outcome"] == "completed"
    assert "cfo-spending-digest" in row["resources"]


def test_a_pack_with_no_staged_triggers_deploys_nothing(home):
    """No staged dir → empty result and no error (fail soft, like `load_roster`)."""
    assert staged_trigger_ids("ghost") == []
    assert deploy_triggers("ghost") == {"deployed": [], "skipped": []}
