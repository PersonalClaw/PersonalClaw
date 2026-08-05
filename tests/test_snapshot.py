"""Tests for personalclaw.snapshot — snapshot and restore."""

import argparse
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

from personalclaw.snapshot import restore_main, snapshot_main

# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """Prevent gateway-running check from blocking restore in tests."""
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: False)


def _setup_fake_personalclaw(d: Path) -> None:
    """Create a realistic fake ~/.personalclaw directory."""
    for sub in (
        "workspace/memory/history",
        "workspace/hygiene_data",
        "skills/my-skill",
        "plan_memory",
    ):
        (d / sub).mkdir(parents=True, exist_ok=True)

    # memory.db with all tables
    conn = sqlite3.connect(str(d / "memory.db"))
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            confidence REAL DEFAULT 0.5, source TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, is_deleted INTEGER DEFAULT 0, embedding BLOB);
        CREATE TABLE episodic_memories (id TEXT PRIMARY KEY, conversation_id TEXT,
            text TEXT NOT NULL, embedding BLOB, tags TEXT DEFAULT '[]',
            importance REAL DEFAULT 0.5, created_at TEXT NOT NULL,
            last_accessed_at TEXT, is_deleted INTEGER DEFAULT 0);
        CREATE TABLE memory_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL, memory_type TEXT NOT NULL, memory_key TEXT NOT NULL,
            old_value TEXT, new_value TEXT, source TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE knowledge_facts (id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
            episode_id TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(subject, predicate, object));
        CREATE TABLE knowledge_edges (source_key TEXT NOT NULL, target_key TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'related', weight REAL NOT NULL DEFAULT 0.0,
            metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL,
            PRIMARY KEY (source_key, target_key, relation));
        INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at)
            VALUES ('test.key1', '"value1"', 0.9, 'test', '2026-01-01', '2026-01-01');
        INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at)
            VALUES ('test.key2', '"value2"', 0.8, 'test', '2026-01-01', '2026-01-01');
        INSERT INTO episodic_memories (id, text, created_at)
            VALUES ('ep1', 'test episode 1', '2026-01-01');
        INSERT INTO episodic_memories (id, text, created_at)
            VALUES ('ep2', 'test episode 2', '2026-01-01');
        INSERT INTO knowledge_facts (subject, predicate, object, episode_id, created_at)
            VALUES ('user', 'prefers', 'dark_mode', 'ep1', '2026-01-01');
        INSERT INTO knowledge_edges (source_key, target_key, relation, weight, created_at)
            VALUES ('user', 'dark_mode', 'prefers', 1.0, '2026-01-01');
    """)
    conn.close()

    (d / "crons.json").write_text(
        json.dumps(
            {
                "version": 2,
                "jobs": [
                    {
                        "id": "abc123",
                        "name": "test-job",
                        "message": "hello",
                        "cron_expr": "0 9 * * *",
                    }
                ],
            }
        )
    )
    (d / "config.json").write_text('{"agent": {"model": "test"}}')
    (d / "session_map.json").write_text("{}")
    (d / "hooks.json").write_text("{}")
    (d / "sel_hmac.key").write_bytes(b"\x00\x01\x02\x03")
    (d / "telemetry_salt").write_bytes(b"\x04\x05\x06\x07")
    (d / "notifications.jsonl").write_text('{"ts":"2026-01-01","msg":"test"}\n')
    (d / "project_dir").write_text("/home/user/project")
    (d / "workspace_dir").write_text("/home/user/.personalclaw/workspace")
    (d / "workspace/memory/history/2026-01-01.md").write_text("history entry")
    (d / "workspace/doc.md").write_text("doc content")
    (d / "workspace/hygiene_data/week1.json").write_text("big data")
    (d / "plan_memory/plan1.json").write_text("plan data")
    (d / "skills/my-skill/SKILL.md").write_text("# My Skill")

    # The unified trigger store + an event trigger (S113). The fake home carried `crons.json`
    # ALONE, so every snapshot test passed while the component backed up a legacy relic nothing
    # writes and dropped the automations the user actually has.
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=d).upsert(
        Trigger(
            id="clock:nightly",
            name="Nightly backup",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "0 3 * * *"},
            workflow={"inline": {"provider": "bash", "config": {"command": "backup"}}},
        )
    )
    (d / "event_triggers.json").write_text(
        json.dumps(
            [
                {
                    "id": "e1",
                    "pattern": "memory",
                    "action_provider": "run-prompt",
                    "action_config": {},
                }
            ]
        )
    )


def _make_snapshot(src: Path, out: Path, extra_args: list[str] | None = None) -> Path:
    """Create a snapshot and return the tarball path. Caller must set PERSONALCLAW_HOME."""
    args = [str(out)] + (extra_args or [])
    snapshot_main(args)
    tarballs = sorted(
        out.glob("personalclaw-snapshot-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    assert tarballs, "No tarball created"
    return tarballs[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Set up source dir, output dir, and snapshot tarball."""
    src = tmp_path / "src"
    out = tmp_path / "out"
    _setup_fake_personalclaw(src)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
    tarball = _make_snapshot(src, out)
    return src, out, tarball, tmp_path


# ── Snapshot Tests ────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_creates_valid_tarball(self, env):
        """TEST 1"""
        _, _, tarball, tmp_path = env
        assert tarball.is_file()
        extract = tmp_path / "extract"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snaps = [d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-")]
        assert snaps
        snap = snaps[0]
        assert (snap / "memory.db").is_file()
        assert (snap / "crons.json").is_file()
        # 🔴 S113: the `crons` component held `crons.json` alone — the legacy file nothing has
        # written since S108 — so `personalclaw snapshot` backed up an empty relic and dropped every
        # automation the user had. `triggers.json` is the store; `event_triggers.json` was named in
        # the plan's own recon note as missing alongside it.
        assert (snap / "triggers.json").is_file(), "the automation store must travel"
        assert (snap / "event_triggers.json").is_file()
        assert (snap / "config.json").is_file()
        assert (snap / "MANIFEST.json").is_file()
        assert (snap / "workspace/doc.md").is_file()
        assert (snap / "workspace/memory/history/2026-01-01.md").is_file()
        assert (snap / "skills/my-skill/SKILL.md").is_file()
        assert not (snap / "workspace/hygiene_data/week1.json").exists()
        m = json.loads((snap / "MANIFEST.json").read_text())
        assert m["version"] == 2

    def test_db_content_survives(self, env):
        _, _, tarball, tmp_path = env
        extract = tmp_path / "extract2"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snap = next(d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-"))
        conn = sqlite3.connect(str(snap / "memory.db"))
        assert conn.execute("SELECT count(*) FROM semantic_memory").fetchone()[0] == 2
        conn.close()

    def test_state_files_captured(self, env):
        _, _, tarball, tmp_path = env
        extract = tmp_path / "extract3"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snap = next(d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-"))
        for f in (
            "sel_hmac.key",
            "telemetry_salt",
            "notifications.jsonl",
            "project_dir",
            "workspace_dir",
            "plan_memory/plan1.json",
        ):
            assert (snap / f).is_file(), f"{f} missing"

    def test_keep_prunes(self, env, monkeypatch):
        """TEST 2"""
        src, _, _, tmp_path = env
        out2 = tmp_path / "out2"
        out2.mkdir()
        # Create 3 fake old snapshots
        for i in range(3):
            (out2 / f"personalclaw-snapshot-2026010{i}T000000Z.tar.gz").write_text("fake")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        snapshot_main([str(out2), "--keep", "2"])
        total = len(list(out2.glob("personalclaw-snapshot-*.tar.gz")))
        assert total == 2

    def test_list(self, env, capsys, monkeypatch):
        """TEST 3"""
        src, out, _, _ = env
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        snapshot_main([str(out), "--list"])
        assert "personalclaw-snapshot-" in capsys.readouterr().out

    def test_keep_zero_errors(self, env, capsys, monkeypatch):
        """TEST 29 partial"""
        src, _, _, tmp_path = env
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        # argparse will raise SystemExit for --keep 0 since we validate > 0
        # But our validation is post-parse, so it returns 1
        ret = snapshot_main([str(tmp_path / "x"), "--keep", "0"])
        assert ret == 1
        assert "positive integer" in capsys.readouterr().out


# ── Restore Tests ─────────────────────────────────────────────────────────────


class TestRestoreDryRun:
    def test_dry_run(self, env, capsys, monkeypatch):
        """TEST 4"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh4"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--dry-run"])
        assert "Dry run" in capsys.readouterr().out
        assert not (fresh / "memory.db").exists()


class TestRestoreReplace:
    def test_replace_fresh(self, env, capsys, monkeypatch):
        """TEST 5"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh5"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(tarball), "--mode", "replace"])
        assert ret == 0
        assert (fresh / "memory.db").is_file()
        assert (fresh / "crons.json").is_file()
        assert (fresh / "config.json").is_file()
        assert (fresh / "workspace/doc.md").is_file()
        assert (fresh / "skills/my-skill/SKILL.md").is_file()
        assert (fresh / "sel_hmac.key").is_file()
        assert (fresh / "notifications.jsonl").is_file()
        assert (fresh / "plan_memory/plan1.json").is_file()
        conn = sqlite3.connect(str(fresh / "memory.db"))
        assert conn.execute("SELECT count(*) FROM semantic_memory").fetchone()[0] == 2
        conn.close()
        assert "integrity" in capsys.readouterr().out

    def test_replace_backs_up(self, env, monkeypatch):
        """TEST 6"""
        _, _, tarball, tmp_path = env
        existing = tmp_path / "existing6"
        _setup_fake_personalclaw(existing)
        (existing / "workspace/original.md").write_text("original")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(existing))
        restore_main([str(tarball), "--mode", "replace"])
        backups = [
            d for d in existing.iterdir() if d.is_dir() and d.name.startswith("pre-restore-")
        ]
        assert backups
        assert (backups[0] / "memory.db").is_file()
        assert (backups[0] / "sel_hmac.key").is_file()
        # original.md should be gone (replaced by snapshot content)
        assert not (existing / "workspace/original.md").exists()

    def test_replace_backs_up_directories(self, env, monkeypatch):
        """TEST 24"""
        _, _, tarball, tmp_path = env
        existing = tmp_path / "existing24"
        _setup_fake_personalclaw(existing)
        (existing / "workspace/local_only.md").write_text("local-only-file")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(existing))
        restore_main([str(tarball), "--mode", "replace"])
        backups = [
            d for d in existing.iterdir() if d.is_dir() and d.name.startswith("pre-restore-")
        ]
        assert backups
        assert (backups[0] / "workspace/local_only.md").is_file()


class TestRestoreMerge:
    def test_merge_memory_dedup(self, env, monkeypatch):
        """TEST 7"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst7"
        _setup_fake_personalclaw(dst)
        conn = sqlite3.connect(str(dst / "memory.db"))
        conn.execute(
            "INSERT INTO semantic_memory (key, value_json, confidence, source, "
            "created_at, updated_at) VALUES ('dst.only', '\"local\"', 0.9, "
            "'test', '2026-02-01', '2026-02-01')"
        )
        conn.execute(
            "UPDATE semantic_memory SET value_json='\"modified\"' " "WHERE key='test.key1'"
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        conn = sqlite3.connect(str(dst / "memory.db"))
        val = conn.execute(
            "SELECT value_json FROM semantic_memory " "WHERE key='dst.only'"
        ).fetchone()[0]
        assert val == '"local"'
        val = conn.execute(
            "SELECT value_json FROM semantic_memory " "WHERE key='test.key1'"
        ).fetchone()[0]
        assert val == '"modified"'
        conn.close()

    def test_merge_restores_automations_from_the_store(self, env, monkeypatch):
        """🔴 S113. The `crons` component restored `crons.json` only — the legacy file — so a
        restore gave the user back an empty relic and none of their automations."""
        from personalclaw.triggers.store import TriggerStore

        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst-auto"
        _setup_fake_personalclaw(dst)
        # The destination renames its own copy, so the snapshot's row is a NEW name.
        store = TriggerStore(base_dir=dst)
        row = store.get("clock:nightly")
        row.trigger.name = "My own backup"
        store.upsert(row.trigger)

        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        assert restore_main([str(tarball), "--mode", "merge"]) == 0

        rows = TriggerStore(base_dir=dst).load()
        names = sorted(r.trigger.name for r in rows)
        assert names == ["My own backup", "Nightly backup"], names
        # The home's own automation keeps firing; the imported one arrives paused.
        by_name = {r.trigger.name: r.trigger for r in rows}
        assert by_name["My own backup"].enabled is True
        assert by_name["Nightly backup"].enabled is False

    def test_merge_skips_an_automation_name_the_home_already_has(self, env, monkeypatch):
        from personalclaw.triggers.store import TriggerStore

        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst-dupe"
        _setup_fake_personalclaw(dst)  # already holds "Nightly backup"
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        assert restore_main([str(tarball), "--mode", "merge"]) == 0

        rows = TriggerStore(base_dir=dst).load()
        assert [r.trigger.name for r in rows] == ["Nightly backup"]
        assert rows[0].trigger.enabled is True, "the home's own row must not be paused by a restore"

    def test_merge_cron_dedup(self, env, monkeypatch):
        """TEST 8"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst8"
        _setup_fake_personalclaw(dst)
        before = len(json.loads((dst / "crons.json").read_text())["jobs"])
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        after = len(json.loads((dst / "crons.json").read_text())["jobs"])
        assert before == after

    def test_merge_new_cron(self, env, monkeypatch):
        """TEST 9"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst9"
        _setup_fake_personalclaw(dst)
        d = json.loads((dst / "crons.json").read_text())
        d["jobs"][0]["name"] = "different-job"
        (dst / "crons.json").write_text(json.dumps(d))
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge"])
        count = len(json.loads((dst / "crons.json").read_text())["jobs"])
        assert count == 2

    def test_merge_workspace_no_overwrite(self, env, monkeypatch):
        """TEST 10"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst10"
        _setup_fake_personalclaw(dst)
        (dst / "workspace/doc.md").write_text("local version")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        assert (dst / "workspace/doc.md").read_text() == "local version"

    def test_merge_episodic_facts_edges(self, env, monkeypatch):
        """TEST 12"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst12"
        _setup_fake_personalclaw(dst)
        conn = sqlite3.connect(str(dst / "memory.db"))
        conn.execute(
            "INSERT INTO episodic_memories (id, text, created_at) "
            "VALUES ('ep_local', 'local episode', '2026-02-01')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        conn = sqlite3.connect(str(dst / "memory.db"))
        assert conn.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM knowledge_edges").fetchone()[0] == 1
        conn.close()

    def test_merge_import_count_accurate(self, env, capsys, monkeypatch):
        """TEST 13"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst13"
        _setup_fake_personalclaw(dst)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge"])
        assert "Semantic Memory imported: 0" in capsys.readouterr().out

    def test_merge_import_count_one_new(self, env, capsys, monkeypatch):
        """TEST 13b"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst13b"
        _setup_fake_personalclaw(dst)
        conn = sqlite3.connect(str(dst / "memory.db"))
        conn.execute("DELETE FROM semantic_memory WHERE key='test.key2'")
        conn.commit()
        conn.close()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge"])
        assert "Semantic Memory imported: 1" in capsys.readouterr().out

    def test_merge_notifications(self, env, monkeypatch):
        """TEST 14"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst14"
        _setup_fake_personalclaw(dst)
        (dst / "notifications.jsonl").write_text('{"ts":"2026-02-01","msg":"local"}\n')
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge"])
        lines = (dst / "notifications.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_merge_plan_memory(self, env, monkeypatch):
        """TEST 15"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst15"
        _setup_fake_personalclaw(dst)
        (dst / "plan_memory/local_plan.json").write_text("local plan")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        assert (dst / "plan_memory/plan1.json").is_file()
        assert (dst / "plan_memory/local_plan.json").read_text() == "local plan"

    def test_merge_restores_missing_security(self, env, capsys, monkeypatch):
        """TEST 16"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst16"
        _setup_fake_personalclaw(dst)
        (dst / "sel_hmac.key").unlink()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge"])
        assert (dst / "sel_hmac.key").is_file()
        assert "sel_hmac.key: restored" in capsys.readouterr().out

    def test_merge_fresh_copies_memory(self, env, capsys, monkeypatch):
        """TEST 26"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh26"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "merge", "--components", "memory"])
        assert (fresh / "memory.db").is_file()
        assert "copied" in capsys.readouterr().out

    def test_merge_notifications_dedup(self, env, capsys, monkeypatch):
        """TEST 25"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst25"
        _setup_fake_personalclaw(dst)
        # Same ts as snapshot
        (dst / "notifications.jsonl").write_text('{"ts":"2026-01-01","msg":"test"}\n')
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge", "--components", "notifications"])
        lines = (dst / "notifications.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        assert "Notifications imported: 0" in capsys.readouterr().out


class TestAutoDetect:
    def test_auto_replace_fresh(self, env, capsys, monkeypatch):
        """TEST 11a"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh11"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball)])
        assert "replace" in capsys.readouterr().out.lower()

    def test_auto_merge_existing(self, env, capsys, monkeypatch):
        """TEST 11b"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst11"
        _setup_fake_personalclaw(dst)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball)])
        assert "merge" in capsys.readouterr().out.lower()


class TestComponents:
    def test_list_components(self, capsys):
        """TEST 18"""
        restore_main(["--list-components"])
        out = capsys.readouterr().out
        for c in ("memory", "crons", "config", "skills", "workspace", "notifications", "security"):
            assert c in out

    def test_memory_only(self, env, monkeypatch):
        """TEST 19"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh19"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "replace", "--components", "memory"])
        assert (fresh / "memory.db").is_file()
        assert not (fresh / "crons.json").exists()
        assert not (fresh / "config.json").exists()
        assert not (fresh / "skills").exists()
        assert not (fresh / "notifications.jsonl").exists()

    def test_crons_and_skills(self, env, monkeypatch):
        """TEST 20"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh20"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "replace", "--components", "crons,skills"])
        assert (fresh / "crons.json").is_file()
        assert (fresh / "skills/my-skill/SKILL.md").is_file()
        assert not (fresh / "memory.db").exists()
        assert not (fresh / "config.json").exists()

    def test_components_merge(self, env, monkeypatch):
        """TEST 21"""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst21"
        _setup_fake_personalclaw(dst)
        (dst / "crons.json").unlink()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        restore_main([str(tarball), "--mode", "merge", "--components", "crons"])
        assert (dst / "crons.json").is_file()
        conn = sqlite3.connect(str(dst / "memory.db"))
        assert conn.execute("SELECT count(*) FROM semantic_memory").fetchone()[0] == 2
        conn.close()

    def test_invalid_component(self, env, capsys, monkeypatch):
        """TEST 22"""
        _, _, tarball, tmp_path = env
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        ret = restore_main([str(tarball), "--components", "bogus"])
        assert ret == 1
        assert "Unknown component: bogus" in capsys.readouterr().out

    def test_all_components(self, env, monkeypatch):
        """TEST 23"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh23"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "replace"])
        assert (fresh / "memory.db").is_file()
        assert (fresh / "crons.json").is_file()
        assert (fresh / "config.json").is_file()
        assert (fresh / "skills/my-skill/SKILL.md").is_file()
        assert (fresh / "notifications.jsonl").is_file()
        assert (fresh / "sel_hmac.key").is_file()


class TestIntegrity:
    def test_integrity_check(self, env, capsys, monkeypatch):
        """TEST 17"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh17"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "replace"])
        assert "integrity: OK" in capsys.readouterr().out

    def test_fts_missing_warning(self, env, capsys, monkeypatch):
        """TEST 31"""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh31"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        restore_main([str(tarball), "--mode", "replace", "--components", "memory"])
        capsys.readouterr()  # discard first call's output
        # Remove index db
        (fresh / "memory_index.db").unlink(missing_ok=True)
        # Re-run merge to trigger warning
        restore_main([str(tarball), "--mode", "merge", "--components", "memory"])
        assert "memory_index.db is missing" in capsys.readouterr().out


class TestSecurity:
    def test_symlink_filtered_out(self, env, monkeypatch):
        """TEST 30 — symlinks are silently dropped by _data_filter."""
        src, _, _, tmp_path = env
        out = tmp_path / "sym_out"
        out.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        tarball = _make_snapshot(src, out)

        # Extract, inject symlink, re-tar
        extract = tmp_path / "sym_extract"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snap = next(d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-"))
        os.symlink("/etc/passwd", str(snap / "evil_link"))
        evil_tar = tmp_path / "evil.tar.gz"
        with tarfile.open(str(evil_tar), "w:gz") as tar:
            tar.add(str(snap), arcname=snap.name)

        fresh = tmp_path / "fresh30"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(evil_tar), "--mode", "replace"])
        # Symlink is filtered out by _data_filter, restore succeeds
        assert ret == 0
        assert not (fresh / "evil_link").exists()

    def test_mode_without_value(self, env, monkeypatch):
        """TEST 28"""
        _, _, tarball, _ = env
        # argparse handles this — --mode without value raises SystemExit
        with pytest.raises(SystemExit):
            restore_main([str(tarball), "--mode"])

    def test_path_traversal_filtered(self, env, capsys, monkeypatch):
        _, _, _, tmp_path = env
        evil_tar = tmp_path / "traversal.tar.gz"
        with tarfile.open(str(evil_tar), "w:gz") as tar:
            # Add a valid snapshot dir so extraction finds something
            info = tarfile.TarInfo(name="personalclaw-snapshot-20260101T000000Z/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            # Add traversal entry — will be filtered
            info2 = tarfile.TarInfo(
                name="personalclaw-snapshot-20260101T000000Z/../../../etc/passwd"
            )
            info2.size = 0
            tar.addfile(info2)
        fresh = tmp_path / "fresh_traversal"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(evil_tar), "--mode", "replace"])
        # Traversal entry filtered out, restore proceeds
        assert ret == 0
        # Verify no "passwd" file anywhere under restore dir
        assert not any(p.name == "passwd" for p in fresh.rglob("*"))
        # Also verify it didn't escape to tmp_path
        assert not (tmp_path / "etc" / "passwd").exists()

    def test_absolute_path_filtered(self, env, capsys, monkeypatch):
        _, _, _, tmp_path = env
        evil_tar = tmp_path / "abspath.tar.gz"
        with tarfile.open(str(evil_tar), "w:gz") as tar:
            info = tarfile.TarInfo(name="personalclaw-snapshot-20260101T000000Z/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            info2 = tarfile.TarInfo(name="/etc/passwd")
            info2.size = 0
            tar.addfile(info2)
        fresh = tmp_path / "fresh_abspath"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(evil_tar), "--mode", "replace"])
        assert ret == 0
        assert not any(p.name == "passwd" for p in fresh.rglob("*"))

    def test_hardlink_filtered(self, env, capsys, monkeypatch):
        _, _, _, tmp_path = env
        evil_tar = tmp_path / "hardlink.tar.gz"
        with tarfile.open(str(evil_tar), "w:gz") as tar:
            # Add valid snapshot dir
            info = tarfile.TarInfo(name="personalclaw-snapshot-20260101T000000Z/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            info2 = tarfile.TarInfo(name="personalclaw-snapshot-20260101T000000Z/evil")
            info2.type = tarfile.LNKTYPE
            info2.linkname = "personalclaw-snapshot-20260101T000000Z/memory.db"
            tar.addfile(info2)
        fresh = tmp_path / "fresh_hardlink"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(evil_tar), "--mode", "replace"])
        assert ret == 0
        assert not (fresh / "evil").exists()


class TestIntegrityFailure:
    def test_integrity_failure(self, env, capsys, monkeypatch):
        src, _, tarball, tmp_path = env
        extract = tmp_path / "corrupt_extract"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snap = next(d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-"))
        (snap / "memory.db").write_bytes(b"not a valid sqlite database")
        corrupt_tar = tmp_path / "corrupt.tar.gz"
        with tarfile.open(str(corrupt_tar), "w:gz") as tar:
            tar.add(str(snap), arcname=snap.name)
        fresh = tmp_path / "fresh_corrupt"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ret = restore_main([str(corrupt_tar), "--mode", "replace"])
        assert ret == 1
        assert "integrity check failed" in capsys.readouterr().out


class TestParsedNamespace:
    """Exercise the parsed= keyword path used by cli.py in production."""

    def test_snapshot_via_parsed_namespace(self, env, monkeypatch):
        src, _, _, tmp_path = env
        out = tmp_path / "out_parsed"
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        ns = argparse.Namespace(output_dir=str(out), keep=7, list_snapshots=False)
        ret = snapshot_main(parsed=ns)
        assert ret == 0
        assert list(out.glob("personalclaw-snapshot-*.tar.gz"))

    def test_restore_via_parsed_namespace(self, env, monkeypatch):
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh_parsed"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        ns = argparse.Namespace(
            snapshot=str(tarball),
            mode="replace",
            dry_run=False,
            components=None,
            list_components=False,
            force=True,
        )
        ret = restore_main(parsed=ns)
        assert ret == 0
        assert (fresh / "memory.db").is_file()


# ── Comment 8: New edge-case tests ───────────────────────────────────────────


class TestSchemaIncompatibleMerge:
    def test_merge_incompatible_schema(self, env, capsys, monkeypatch):
        """Merge gracefully skips tables that don't exist in source."""
        _, _, tarball, tmp_path = env
        dst = tmp_path / "dst_schema"
        _setup_fake_personalclaw(dst)
        # Drop a table from destination to simulate schema mismatch
        conn = sqlite3.connect(str(dst / "memory.db"))
        conn.execute("DROP TABLE knowledge_edges")
        conn.commit()
        conn.close()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(tarball), "--mode", "merge"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Semantic Memory imported" in out


class TestCorruptSourceDB:
    def test_merge_corrupt_source_db(self, env, capsys, monkeypatch):
        """Merge with corrupt source DB skips merge gracefully."""
        src, _, _, tmp_path = env
        out = tmp_path / "corrupt_src_out"
        out.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        tarball = _make_snapshot(src, out)

        # Extract, corrupt memory.db, re-tar
        extract = tmp_path / "corrupt_src_extract"
        extract.mkdir()
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract, filter=lambda t, _d="": t)
        snap = next(d for d in extract.iterdir() if d.name.startswith("personalclaw-snapshot-"))
        (snap / "memory.db").write_bytes(b"corrupt data here")
        corrupt_tar = tmp_path / "corrupt_src.tar.gz"
        with tarfile.open(str(corrupt_tar), "w:gz") as tar:
            tar.add(str(snap), arcname=snap.name)

        dst = tmp_path / "dst_corrupt_src"
        _setup_fake_personalclaw(dst)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(dst))
        ret = restore_main([str(corrupt_tar), "--mode", "merge"])
        assert ret == 0
        out_text = capsys.readouterr().out
        assert "Source DB" in out_text or "Merge complete" in out_text


class TestGatewayRunningRefusal:
    def test_restore_refused_when_gateway_running(self, env, capsys, monkeypatch):
        """Restore refuses if gateway is running (unless --force)."""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh_gw"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: True)
        ret = restore_main([str(tarball), "--mode", "replace"])
        assert ret == 1
        assert "Gateway is running" in capsys.readouterr().out

    def test_restore_allowed_with_force(self, env, capsys, monkeypatch):
        """--force bypasses gateway check."""
        _, _, tarball, tmp_path = env
        fresh = tmp_path / "fresh_gw_force"
        fresh.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
        monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: True)
        ret = restore_main([str(tarball), "--mode", "replace", "--force"])
        assert ret == 0


class TestEmptyPersonalclawDir:
    def test_snapshot_empty_dir(self, tmp_path, monkeypatch):
        """Snapshot succeeds on an empty ~/.personalclaw directory."""
        empty = tmp_path / "empty_pc"
        empty.mkdir()
        out = tmp_path / "empty_out"
        monkeypatch.setenv("PERSONALCLAW_HOME", str(empty))
        ret = snapshot_main([str(out)])
        assert ret == 0
        assert list(out.glob("personalclaw-snapshot-*.tar.gz"))


class TestConcurrentSnapshot:
    def test_concurrent_snapshots_unique(self, env, monkeypatch):
        """Two rapid snapshots produce distinct files."""
        src, _, _, tmp_path = env
        out = tmp_path / "concurrent_out"
        out.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(src))
        snapshot_main([str(out)])
        # Ensure different timestamp by creating a second one
        import time

        time.sleep(1.1)
        snapshot_main([str(out)])
        tarballs = list(out.glob("personalclaw-snapshot-*.tar.gz"))
        assert len(tarballs) == 2
        assert tarballs[0].name != tarballs[1].name


# ── DURABILITY §1: safe live-database capture + inventory-driven gap closure ──


class TestLiveDatabaseSafety:
    """A live sqlite store must be captured via the backup API, never raw-copied.

    Regression: only the two files in CORE_FILES["memory"] got the safe path.
    `knowledge.db`, `lexicon.db` and `loops.db` were inside tree copies, so a
    snapshot taken while the gateway held them open could capture a torn page set
    — measured at 2000 of 4000 rows lost in a raw copy of a WAL-heavy DB.
    """

    def _home_with_live_db(self, tmp_path, rel: str, rows: int = 500):
        home = tmp_path / "src"
        _setup_fake_personalclaw(home)
        db = home / rel
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()
        for i in range(rows):
            conn.execute("INSERT INTO probe(v) VALUES (?)", (f"row-{i}" * 20,))
        conn.commit()
        # Deliberately keep the handle OPEN with un-checkpointed WAL content —
        # this is the state a real snapshot runs against.
        return home, conn

    @pytest.mark.parametrize(
        "rel", ["workspace/knowledge/knowledge.db", "loop/loops.db", "workspace/lexicon/lexicon.db"]
    )
    def test_declared_db_captured_completely_while_open(self, tmp_path, monkeypatch, rel):
        home, conn = self._home_with_live_db(tmp_path, rel)
        try:
            monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
            out = tmp_path / "out"
            tarball = _make_snapshot(home, out)
            extract = tmp_path / "x"
            with tarfile.open(str(tarball)) as tar:
                tar.extractall(extract)
            snap = next(extract.glob("personalclaw-snapshot-*"))
            staged = snap / rel
            assert staged.is_file(), f"{rel} missing from the snapshot"
            c = sqlite3.connect(str(staged))
            try:
                assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert c.execute("SELECT count(*) FROM probe").fetchone()[0] == 500
            finally:
                c.close()
        finally:
            conn.close()

    def test_wal_sidecars_never_ride_along(self, tmp_path, monkeypatch):
        """Sidecars are checkpointed/backed-up state, not files to copy — shipping
        them alongside a backup-API copy risks a mismatched pair on restore."""
        home, conn = self._home_with_live_db(tmp_path, "workspace/knowledge/knowledge.db")
        try:
            monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
            tarball = _make_snapshot(home, tmp_path / "out")
            with tarfile.open(str(tarball)) as tar:
                names = tar.getnames()
            assert not [n for n in names if n.endswith(("-wal", "-shm"))]
        finally:
            conn.close()


class TestInventoryGapClosure:
    """The nine stores that used to be in NEITHER the snapshot nor the export."""

    def test_previously_missing_stores_are_captured(self, tmp_path, monkeypatch):
        home = tmp_path / "src"
        _setup_fake_personalclaw(home)
        # Seed one real file in each formerly-uncovered store.
        seeded = {
            "tasks/t-1.json": '{"id": "t-1", "title": "keep me"}',
            "projects/p-1/project.json": '{"id": "p-1"}',
            "artifacts/a-1/meta.json": '{"slug": "a-1"}',
            "prompts/p.json": '{"name": "p"}',
            "workflows/w.json": '{"id": "w"}',
            "agents/a.json": '{"name": "a"}',
            "entity_settings/s.json": "{}",
            "sessions/s-1.jsonl": '{"role": "user"}\n',
        }
        for rel, body in seeded.items():
            path = home / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        tarball = _make_snapshot(home, tmp_path / "out")
        extract = tmp_path / "x"
        with tarfile.open(str(tarball)) as tar:
            tar.extractall(extract)
        snap = next(extract.glob("personalclaw-snapshot-*"))
        for rel, body in seeded.items():
            staged = snap / rel
            assert staged.is_file(), f"{rel} was NOT captured — the backup is incomplete"
            assert staged.read_text() == body, f"{rel} content differs"

    def test_default_output_dir_honors_the_active_home(self, tmp_path, monkeypatch):
        """The fallback used to hardcode ~/.personalclaw/snapshots, so snapshotting
        an isolated home wrote its archive into the REAL one."""
        from personalclaw.snapshot import _default_snapshot_dir

        home = tmp_path / "isolated"
        home.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        # No config file → the fallback path is what we're pinning.
        monkeypatch.setattr(
            "personalclaw.config.loader.AppConfig.load",
            classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no config"))),
        )
        assert _default_snapshot_dir() == str(home / "snapshots")


# ── 🔴 the run history's declared merge had no executor (S176) ──


def _hist_row(run_id: str, job_id: str = "clock:backup") -> str:
    return json.dumps({"run_id": run_id, "job_id": job_id, "started_at": 1.0, "status": "success"})


def _hist(root: Path, shard: str, run_ids: list[str]) -> Path:
    d = root / "cron-history"
    d.mkdir(parents=True, exist_ok=True)
    p = d / shard
    p.write_text("".join(_hist_row(r) + "\n" for r in run_ids), encoding="utf-8")
    return p


def _ids(root: Path, shard: str) -> list[str]:
    p = root / "cron-history" / shard
    if not p.is_file():
        return []
    return [json.loads(line)["run_id"] for line in p.read_text().strip().splitlines()]


def test_a_MERGE_restore_recovers_the_run_history(tmp_path: Path) -> None:
    """🔴 THE DEFECT. `inventory.py` declares `cron_history` with `merge=append_dedup` and
    `_do_merge` had no branch for it — so a merge restore printed "✅ Merge complete" while
    recovering **no run history at all**.

    Driven: a snapshot holding `FROM-SNAPSHOT` merged into a home holding `LIVE-run` left only
    `LIVE-run`. A declared strategy with no executor, in the durability layer — where the whole
    promise is that a restore returns what the snapshot holds.
    """
    from personalclaw.snapshot import _merge_run_history

    snap, pc = tmp_path / "snap", tmp_path / "home"
    _hist(snap, "clock:backup.jsonl", ["FROM-SNAPSHOT"])
    _hist(pc, "clock:backup.jsonl", ["LIVE-run"])

    _merge_run_history(snap / "cron-history", pc / "cron-history")

    got = _ids(pc, "clock:backup.jsonl")
    assert "FROM-SNAPSHOT" in got, "the snapshot's run must be recovered"
    assert "LIVE-run" in got, "and the live run must be preserved"


def test_the_merge_DEDUPES_on_run_id(tmp_path: Path) -> None:
    """Deduped on `run_id`, not a whole-line compare: the same run round-trips through `to_dict()`,
    so key ordering or a re-serialised float could make an identical run look new and double it.
    Mirrors `_merge_notifications`, which dedupes on `ts` for the same reason."""
    from personalclaw.snapshot import _merge_run_history

    snap, pc = tmp_path / "snap", tmp_path / "home"
    _hist(snap, "j.jsonl", ["a", "b"])
    _hist(pc, "j.jsonl", ["a"])

    _merge_run_history(snap / "cron-history", pc / "cron-history")
    assert _ids(pc, "j.jsonl") == ["a", "b"]


def test_the_merge_is_IDEMPOTENT(tmp_path: Path) -> None:
    """A restore drill re-run must not grow the history. `_do_merge` is the path a user reaches by
    re-running a restore, so a non-idempotent merge would double every row each attempt."""
    from personalclaw.snapshot import _merge_run_history

    snap, pc = tmp_path / "snap", tmp_path / "home"
    _hist(snap, "j.jsonl", ["a", "b"])
    _hist(pc, "j.jsonl", ["a"])

    _merge_run_history(snap / "cron-history", pc / "cron-history")
    once = _ids(pc, "j.jsonl")
    _merge_run_history(snap / "cron-history", pc / "cron-history")
    assert _ids(pc, "j.jsonl") == once


def test_a_SNAPSHOT_ONLY_shard_is_copied_whole(tmp_path: Path) -> None:
    """The store is one file per job. A job that exists only in the snapshot — an automation the
    live home has never run — must come back, not be skipped for having no local counterpart."""
    from personalclaw.snapshot import _merge_run_history

    snap, pc = tmp_path / "snap", tmp_path / "home"
    _hist(snap, "j.jsonl", ["x"])
    _hist(snap, "other.jsonl", ["z"])
    _hist(pc, "j.jsonl", ["y"])

    _merge_run_history(snap / "cron-history", pc / "cron-history")
    assert _ids(pc, "other.jsonl") == ["z"]


def test_a_MALFORMED_line_does_not_abort_the_merge(tmp_path: Path) -> None:
    """One bad line must not cost the rest of the restore. The same call `count_since` makes about a
    malformed ledger row: skip it, keep going — a partial recovery beats an aborted one."""
    from personalclaw.snapshot import _merge_run_history

    snap, pc = tmp_path / "snap", tmp_path / "home"
    d = snap / "cron-history"
    d.mkdir(parents=True)
    (d / "j.jsonl").write_text(
        _hist_row("ok1") + "\nNOT JSON\n" + _hist_row("ok2") + "\n", encoding="utf-8"
    )
    _hist(pc, "j.jsonl", ["live"])

    _merge_run_history(snap / "cron-history", pc / "cron-history")
    got = _ids(pc, "j.jsonl")
    assert "ok1" in got and "ok2" in got and "live" in got


def test_an_ABSENT_snapshot_history_is_a_NO_OP(tmp_path: Path) -> None:
    """A snapshot taken before the store existed (or from a home that never scheduled anything) must
    not create an empty directory or raise — the restore has to stay usable either way."""
    from personalclaw.snapshot import _merge_run_history

    pc = tmp_path / "home"
    pc.mkdir()
    _merge_run_history(tmp_path / "nope" / "cron-history", pc / "cron-history")
    assert not (pc / "cron-history").exists()


def test_the_merge_does_NOT_re_apply_retention(tmp_path: Path) -> None:
    """`ScheduleRunStore.rotate_all()` owns retention and runs at gateway boot (S175). Trimming here
    would be a second copy of that policy — the exact duplication S175 removed, which had silently
    reverted S173."""
    import inspect

    from personalclaw import snapshot

    source = inspect.getsource(snapshot._merge_run_history)
    body = source.split('"""')[-1]
    assert "_MAX_RECORDS_PER_JOB" not in body
    assert "rotate" not in body


def test_the_run_history_merge_is_WIRED_into_do_merge(tmp_path: Path) -> None:
    """🔴 Caught by my own load-bearing check: disabling the call site left all 61 tests green,
    because every other test in this group calls `_merge_run_history` DIRECTLY. A helper that works
    perfectly and is never called is the inert-control shape this whole program keeps finding —
    and I had just written seven tests that could not tell the difference.

    Drives `_do_merge`, the function a real restore reaches.
    """
    from personalclaw.snapshot import _do_merge

    snap, pc = tmp_path / "snap", tmp_path / "home"
    snap.mkdir()
    pc.mkdir()
    _hist(snap, "clock:backup.jsonl", ["FROM-SNAPSHOT"])
    _hist(pc, "clock:backup.jsonl", ["LIVE-run"])

    _do_merge(snap, pc, None)

    got = _ids(pc, "clock:backup.jsonl")
    assert "FROM-SNAPSHOT" in got, "_do_merge must invoke the run-history merge"
    assert "LIVE-run" in got


# ── 🔴 the capture side was widened and the RESTORE side was not (S177) ──

_STORES = ("tasks", "projects", "agents", "prompts", "workflows", "artifacts", "uploads")


def _seeded_snapshot(root: Path, *, secrets: bool = False) -> Path:
    """An extracted snapshot tree holding the stores `_everything_paths` captures."""
    snap = root / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    for d in _STORES:
        (snap / d).mkdir(parents=True, exist_ok=True)
        (snap / d / "x.json").write_text('{"v":"FROM-SNAPSHOT"}', encoding="utf-8")
    (snap / "entity_settings").mkdir(exist_ok=True)
    (snap / "entity_settings" / "e.json").write_text('{"v":"FROM-SNAPSHOT"}', encoding="utf-8")
    if secrets:
        (snap / ".env").write_text("OPENAI_API_KEY=sk-FROM-SNAPSHOT", encoding="utf-8")
        (snap / ".local_secret").write_text("FROM-SNAPSHOT", encoding="utf-8")
        (snap / "credentials").mkdir(exist_ok=True)
        (snap / "credentials" / "c.json").write_text('{"tok":"FROM-SNAPSHOT"}', encoding="utf-8")
    return snap


def test_a_MERGE_restore_recovers_the_task_board(tmp_path: Path) -> None:
    """🔴 THE DEFECT. `_everything_paths` widened CAPTURE to every inventory entry — its comment
    says a full backup "silently dropped the user's whole task board". Both restore modes stayed
    hand-written seven-component lists, so the archive held the board and neither mode gave it back,
    while both printed a success line.

    Driven: 8 stores in the snapshot, 8 absent from the restored home. The asymmetry IS the bug — a
    snapshot is only as good as its restore, and widening one side made the archive look complete.
    """
    from personalclaw.snapshot import _do_merge

    snap = _seeded_snapshot(tmp_path)
    pc = tmp_path / "home"
    pc.mkdir()

    _do_merge(snap, pc, None)

    missing = [d for d in _STORES if not (pc / d / "x.json").is_file()]
    assert missing == [], f"a merge restore dropped these stores: {missing}"
    assert (pc / "entity_settings" / "e.json").is_file()


def test_a_REPLACE_restore_recovers_the_task_board(tmp_path: Path) -> None:
    """The same gap in replace mode. Both are reachable from `--mode`, and a user recovering onto a
    wiped machine picks replace — the shape where losing the board is total."""
    from personalclaw.snapshot import _do_replace

    snap = _seeded_snapshot(tmp_path)
    pc = tmp_path / "home"
    pc.mkdir()

    _do_replace(snap, pc, None)

    missing = [d for d in _STORES if not (pc / d / "x.json").is_file()]
    assert missing == [], f"a replace restore dropped these stores: {missing}"


def test_the_restore_does_NOT_re_plant_SECRETS(tmp_path: Path) -> None:
    """🔴 SECURITY. `backup_entries()` includes secrets deliberately — "losing the credential store
    is exactly what a backup should prevent" — so `.env`, `credentials/` and `.local_secret` ARE in
    the archive. Restoring them through the generic path would re-plant credential material into a
    home that may have deliberately rotated or removed it.

    Capture writes a local 0600 archive; restore writes into a live home. The two directions do not
    warrant the same default, so the generic path excludes `secret_paths()` and the named `security`
    component stays the deliberate route (copy-if-missing, chmod 0600).
    """
    from personalclaw.snapshot import _do_merge

    snap = _seeded_snapshot(tmp_path, secrets=True)
    pc = tmp_path / "home"
    pc.mkdir()

    _do_merge(snap, pc, None)

    for leaked in (".env", ".local_secret", "credentials/c.json"):
        assert not (pc / leaked).exists(), f"restore re-planted secret material: {leaked}"


def test_MERGE_leaves_an_existing_file_ALONE(tmp_path: Path) -> None:
    """Merge mode's contract is that local state wins. These entries have no field-level merge
    executor yet, so copy-if-missing is the honest half — overwriting a live task board with an
    older snapshot's copy is the data loss a merge restore exists to avoid."""
    from personalclaw.snapshot import _do_merge

    snap = _seeded_snapshot(tmp_path)
    pc = tmp_path / "home"
    (pc / "tasks").mkdir(parents=True)
    (pc / "tasks" / "x.json").write_text('{"v":"LIVE-EDIT"}', encoding="utf-8")

    _do_merge(snap, pc, None)

    assert json.loads((pc / "tasks" / "x.json").read_text())["v"] == "LIVE-EDIT"


def test_REPLACE_keeps_the_overwritten_copy_RECOVERABLE(tmp_path: Path) -> None:
    """Replace mode is destructive by design, and its existing contract is that the previous state
    lands in `pre-restore-<ts>/`. Widening it without extending that backup would make the new
    coverage the one unrecoverable path in the function."""
    from personalclaw.snapshot import _do_replace

    snap = _seeded_snapshot(tmp_path)
    pc = tmp_path / "home"
    (pc / "tasks").mkdir(parents=True)
    (pc / "tasks" / "x.json").write_text('{"v":"LIVE-EDIT"}', encoding="utf-8")

    _do_replace(snap, pc, None)

    assert json.loads((pc / "tasks" / "x.json").read_text())["v"] == "FROM-SNAPSHOT"
    backups = [p for p in pc.iterdir() if p.name.startswith("pre-restore-")]
    assert len(backups) == 1, "replace must leave exactly one pre-restore backup"
    saved = backups[0] / "tasks" / "x.json"
    assert saved.is_file(), "the overwritten live copy is not recoverable"
    assert json.loads(saved.read_text())["v"] == "LIVE-EDIT"


def test_a_TARGETED_restore_stays_targeted(tmp_path: Path) -> None:
    """`--components memory` must not drag in the whole state. The new coverage is gated on
    `everything`, which is also what `components is None` selects — so the default (the invocation a
    user in a recovery actually types) is complete, and an explicit narrow ask is still narrow."""
    from personalclaw.snapshot import _do_merge

    snap = _seeded_snapshot(tmp_path)
    pc = tmp_path / "home"
    pc.mkdir()

    _do_merge(snap, pc, ["memory"])

    assert not (pc / "tasks").exists()


def test_EVERYTHING_is_a_valid_component(tmp_path: Path) -> None:
    """🔴 Criterion 1 names this invocation verbatim — "`--components everything` followed by wiping
    `~/.personalclaw` and restoring reproduces … including tasks, projects, entity_settings" — and
    the CLI answered **"❌ Unknown component: everything"**. Without it there is no way to ask for
    the task board at all."""
    from personalclaw.snapshot import COMPONENT_HELP, VALID_COMPONENTS

    assert "everything" in VALID_COMPONENTS
    assert "everything" in COMPONENT_HELP, "--list-components must advertise it"


def test_DERIVED_entries_are_not_restored(tmp_path: Path) -> None:
    """`backup_entries()` skips derived indexes because "a stale index paired with a newer store is
    worse than none". The restore projection reuses that same call rather than re-deciding, so the
    reasoning cannot drift between the two directions."""
    from personalclaw.snapshot import _extra_restore_paths

    snap = tmp_path / "snap"
    (snap / "models").mkdir(parents=True)
    (snap / "models" / "m.bin").write_text("derived", encoding="utf-8")
    (snap / "tasks").mkdir()
    (snap / "tasks" / "x.json").write_text("{}", encoding="utf-8")

    got = _extra_restore_paths(snap)
    assert "tasks" in got
    assert "models" not in got, "a derived index must not be restored"


def test_the_restore_projection_MIRRORS_the_capture_one(tmp_path: Path) -> None:
    """The defect was capture and restore disagreeing about what state IS. Both now project the same
    `backup_entries()` and exclude the same named components, so a store added to the inventory
    later is captured AND restored without editing either function. Asserted structurally, because
    the failure mode is the two drifting apart again — not a wrong value today."""
    from personalclaw.snapshot import _everything_paths, _extra_restore_paths

    home = tmp_path / "home"
    for d in (*_STORES, "entity_settings"):
        (home / d).mkdir(parents=True)
        (home / d / "x.json").write_text("{}", encoding="utf-8")

    captured = set(_everything_paths(home))
    restorable = set(_extra_restore_paths(home))
    # Restore excludes secrets by design; nothing else may differ.
    assert restorable <= captured
    from personalclaw.durability import inventory as inv

    secret = inv.secret_paths()
    unexplained = {p for p in captured - restorable if p.split("/", 1)[0] not in secret}
    assert unexplained == set(), f"captured but not restorable, for no stated reason: {unexplained}"


def test_EVERYTHING_selects_the_NAMED_components_too(tmp_path: Path) -> None:
    """🔴 MY OWN FIX SHIPPED HALF-INERT, and the eight tests above all passed while it did.

    Found by driving criterion 1's actual drill — snapshot, wipe the home, restore — instead of
    trusting the component I had just added. `--components everything` restored the task board and
    **dropped `config.json`, `memory.db`, `notifications.jsonl`, `workspace/` and `skills/`**,
    because `everything` had been added as just another member of the list: naming it made `_want`
    answer False for all seven NAMED components. A flag whose entire promise is completeness,
    silently narrowing the restore — and the invocation criterion 1 tells a user to type.

    `everything` is a superset marker, not a peer.
    """
    from personalclaw.snapshot import _want

    for named in ("memory", "crons", "config", "skills", "workspace", "notifications", "security"):
        assert _want(["everything"], named), f"'everything' must select '{named}'"
    assert _want(["everything"], "everything")
    # and a narrow ask stays narrow
    assert _want(["memory"], "memory")
    assert not _want(["memory"], "everything")
    assert not _want(["memory"], "crons")


def test_a_WIPE_and_restore_returns_every_named_component(tmp_path: Path) -> None:
    """Criterion 1 end to end, at the `_do_replace` level: the drill is "wipe `~/.personalclaw` and
    restore", so the test that matters drives an EMPTY home rather than a partially-populated one —
    the state a user recovering onto a new machine actually has."""
    from personalclaw.snapshot import _do_replace

    snap = _seeded_snapshot(tmp_path)
    (snap / "notifications.jsonl").write_text('{"ts":1}\n', encoding="utf-8")
    (snap / "workspace").mkdir(exist_ok=True)
    (snap / "workspace" / "w.md").write_text("# ws", encoding="utf-8")
    (snap / "skills").mkdir(exist_ok=True)
    (snap / "skills" / "s.md").write_text("# skill", encoding="utf-8")
    pc = tmp_path / "wiped"
    pc.mkdir()

    _do_replace(snap, pc, ["everything"])

    for rel in ("config.json", "notifications.jsonl", "workspace/w.md", "skills/s.md"):
        assert (pc / rel).is_file(), f"'everything' dropped a named component: {rel}"
    assert (pc / "tasks" / "x.json").is_file()


def test_the_widened_restore_is_BOUNDED_by_the_inventory(tmp_path: Path) -> None:
    """🔴 SECURITY. `portability.py:305` calls `_do_replace(snap, pc, None)` on the IMPORT path, so
    widening the restore widens what an import writes into a live home — and an import archive can
    come from someone else's export.

    The projection iterates `backup_entries()` and asks whether each DECLARED path exists in the
    archive; it never walks the archive and copies what it finds. So an undeclared directory cannot
    be steered into the home no matter what the tree contains. Asserted with an archive carrying
    `evil/`, `.ssh/authorized_keys` and credential files: only `tasks` is selected.
    """
    from personalclaw.durability import inventory as inv
    from personalclaw.snapshot import _extra_restore_paths

    snap = tmp_path / "snap"
    (snap / "tasks").mkdir(parents=True)
    (snap / "tasks" / "x.json").write_text("{}", encoding="utf-8")
    for hostile, name in (("evil", "payload.sh"), (".ssh", "authorized_keys")):
        (snap / hostile).mkdir()
        (snap / hostile / name).write_text("x", encoding="utf-8")
    (snap / ".env").write_text("KEY=ATTACKER", encoding="utf-8")

    got = _extra_restore_paths(snap)

    declared = {e.path for e in inv.INVENTORY}
    assert [p for p in got if p not in declared] == [], "restore must copy only declared paths"
    for hostile in ("evil", ".ssh", ".env"):
        assert hostile not in got


# ── 🔴 the remaining append_dedup entries, and the one that must NOT merge (S178) ──


def _sel_home(root: Path, tools: list[str], *, key: bytes | None = None) -> Path:
    """A home with a real HMAC-signed SEL log. Signed through the real writer, because the whole
    question is whether imported rows verify — a hand-built fixture could not answer it."""
    import importlib

    root.mkdir(parents=True, exist_ok=True)
    if key is not None:
        (root / "sel_hmac.key").write_bytes(key)
    os.environ["PERSONALCLAW_HOME"] = str(root)
    from personalclaw import sel as sel_mod

    importlib.reload(sel_mod)
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    log = sel_mod.SecurityEventLog()
    for t in tools:
        log.log_tool_invocation(tool_name=t, outcome="completed", session_key="s")
    return root


def _sel_verify(root: Path) -> tuple[int, int]:
    import importlib

    os.environ["PERSONALCLAW_HOME"] = str(root)
    from personalclaw import sel as sel_mod

    importlib.reload(sel_mod)
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    return sel_mod.SecurityEventLog().verify_integrity(max_entries=None)


@pytest.fixture(autouse=False)
def _restore_home():
    prev = os.environ.get("PERSONALCLAW_HOME")
    yield
    if prev is None:
        os.environ.pop("PERSONALCLAW_HOME", None)
    else:
        os.environ["PERSONALCLAW_HOME"] = prev


def test_the_SEL_merge_is_SKIPPED_when_the_HMAC_KEY_DIFFERS(tmp_path, _restore_home) -> None:
    """🔴 SECURITY, and the reason a generic `append_dedup` executor would have been wrong.

    `inventory.py` declares `security_events.jsonl` with `merge=append_dedup`. Appending the
    snapshot's rows unconditionally was measured against two homes with different `sel_hmac.key`
    files: `verify_integrity` reported **checked=5, valid=2**, logging "SEL HMAC mismatch" for every
    imported row. A restore would have made the tamper-EVIDENT log report tampering — turning the
    surface a user consults to ask "was I compromised?" into a false positive they cannot clear
    except by rotating the chain.

    Fail-CLOSED here, unlike the other merges: a missing row is strictly better than an
    unverifiable one, because an audit trail's whole value is that a mismatch means something.
    """
    from personalclaw.snapshot import _merge_security_events

    snap = _sel_home(tmp_path / "snap", ["snapX", "snapY", "snapZ"])
    live = _sel_home(tmp_path / "live", ["liveX"])  # its own, different key

    _merge_security_events(snap, live)

    checked, valid = _sel_verify(live)
    assert checked == valid, f"{checked - valid} row(s) now report tampering"
    assert "snapX" not in (live / "security_events.jsonl").read_text()


def test_the_SEL_merge_RECOVERS_history_when_the_KEY_MATCHES(tmp_path, _restore_home) -> None:
    """The case worth merging, and why the guard is a key comparison rather than a blanket refusal.

    `security`'s key restore is copy-if-missing, so a WIPED home takes the snapshot's key — and then
    the snapshot's rows verify under it. Skipping unconditionally would discard recoverable audit
    history in exactly the scenario a restore exists for.
    """
    from personalclaw.snapshot import _merge_security_events

    snap = _sel_home(tmp_path / "snap", ["snapA", "snapB", "snapC"])
    key = (snap / "sel_hmac.key").read_bytes()
    live = _sel_home(tmp_path / "live", ["liveA"], key=key)

    _merge_security_events(snap, live)

    checked, valid = _sel_verify(live)
    assert checked == 4, "the snapshot's 3 rows must be recovered beside the live one"
    assert valid == checked, "every recovered row must verify under the shared key"
    assert "snapA" in (live / "security_events.jsonl").read_text()


def test_the_SEL_merge_is_IDEMPOTENT(tmp_path, _restore_home) -> None:
    """Deduped on `event_id`, so a repeated restore drill cannot double the audit log."""
    from personalclaw.snapshot import _merge_security_events

    snap = _sel_home(tmp_path / "snap", ["a", "b"])
    key = (snap / "sel_hmac.key").read_bytes()
    live = _sel_home(tmp_path / "live", ["c"], key=key)

    _merge_security_events(snap, live)
    once = (live / "security_events.jsonl").read_text()
    _merge_security_events(snap, live)
    assert (live / "security_events.jsonl").read_text() == once


def test_the_FEEDBACK_merge_recovers_rows_and_dedupes_on_id(tmp_path: Path) -> None:
    """🔴 The third `append_dedup` entry with no executor. Carries no HMAC, so plain dedup is
    safe — keyed on `FeedbackRecord.id` rather than the whole line, because the record round-trips
    through a serializer on both sides."""
    from personalclaw.snapshot import _merge_feedback

    snap, pc = tmp_path / "s", tmp_path / "p"
    snap.mkdir()
    pc.mkdir()
    (snap / "feedback.jsonl").write_text(
        '{"id":"f1","verdict":"up"}\n{"id":"f2","verdict":"down"}\n', encoding="utf-8"
    )
    (pc / "feedback.jsonl").write_text(
        '{"id":"f1","verdict":"up"}\n{"id":"live","verdict":"up"}\n', encoding="utf-8"
    )

    _merge_feedback(snap / "feedback.jsonl", pc / "feedback.jsonl")

    ids = [json.loads(ln)["id"] for ln in (pc / "feedback.jsonl").read_text().splitlines() if ln]
    assert ids == ["f1", "live", "f2"], "f1 must dedupe, f2 must arrive, live must survive"

    _merge_feedback(snap / "feedback.jsonl", pc / "feedback.jsonl")
    ids2 = [json.loads(ln)["id"] for ln in (pc / "feedback.jsonl").read_text().splitlines() if ln]
    assert ids2 == ids, "a repeated restore must not grow the log"


def test_the_feedback_merge_does_NOT_re_apply_its_CAP(tmp_path: Path) -> None:
    """`feedback.py` owns its own retention ("atomic trim at 2x cap"). Re-implementing the bound
    here would be the duplication S175 deleted from the run store after one copy silently reverted
    the other."""
    import inspect

    from personalclaw import snapshot

    body = inspect.getsource(snapshot._merge_feedback).split('"""')[-1]
    assert "_CAP" not in body
    assert "trim" not in body


def test_every_declared_APPEND_DEDUP_entry_now_has_a_path(tmp_path: Path) -> None:
    """The sweep's closing assertion. Six entries declare `append_dedup`; each must now be handled,
    and by a REASON rather than by accident:

    * `cron-history` — `_merge_run_history` (S176)
    * `notifications.jsonl` — `_merge_notifications` (pre-existing)
    * `security_events.jsonl` — `_merge_security_events`, key-gated (S178)
    * `feedback.jsonl` — `_merge_feedback` (S178)
    * `crashes`, `sessions` — directories on disk, so the generic per-file tree copy (S177) already
      gives them entity-level union; a line-dedup executor would be the wrong shape entirely.

    🔴 **A declaration discrepancy found while asserting this:** `sessions` is declared
    `kind=jsonl_append`, but on disk it is a nested tree (`sessions/<key>/tool_results/*.json`),
    verified against both the dev home and the real one. The `kind` is wrong, not the merge — the
    tree copy is the right executor either way, which is why this is recorded rather than "fixed"
    by changing a declaration whose other readers I have not swept. Asserted as-declared so the
    discrepancy is visible instead of silently encoded.

    Pinned as a test because the failure mode is a SEVENTH entry being added later and silently
    inheriting copy-if-missing.
    """
    import inspect

    from personalclaw import snapshot
    from personalclaw.durability import inventory as inv

    declared = {e.path: e for e in inv.INVENTORY if e.merge == inv.MERGE_APPEND_DEDUP}
    assert set(declared) == {
        "cron-history",
        "notifications.jsonl",
        "security_events.jsonl",
        "feedback.jsonl",
        # Added by S179 and demanded by THIS test the moment the entry was declared — which is what
        # the ratchet is for. Keyed on `AttemptRecord.audit_id` via `_merge_keyed_jsonl`.
        "model_calls.jsonl",
        "crashes",
        "sessions",
    }, "a new append_dedup entry appeared — give it an executor, not copy-if-missing"

    # Named explicitly rather than derived from the path: `cron-history`'s executor is
    # `_merge_run_history`, so a stem-to-symbol guess would pass for the wrong reason.
    merge_src = inspect.getsource(snapshot._do_merge)
    executors = {
        "cron-history": "_merge_run_history",
        "notifications.jsonl": "_merge_notifications",
        "security_events.jsonl": "_merge_security_events",
        "feedback.jsonl": "_merge_feedback",
        # Shares `_merge_keyed_jsonl` with feedback, so the call site is matched by its ARGUMENT —
        # the symbol alone would pass even if this path were dropped from `_do_merge`.
        "model_calls.jsonl": "_merge_keyed_jsonl",
    }
    for path, symbol in executors.items():
        assert symbol in merge_src, f"{path} has no executor reachable from _do_merge"
        assert hasattr(snapshot, symbol), f"{symbol} is not defined"
    # `_merge_keyed_jsonl` is SHARED (feedback + model calls), so the symbol alone cannot prove both
    # call sites exist — each is pinned by its filename argument. The dedicated executors take their
    # paths internally, so there is nothing to pin at their call site.
    for path in ("feedback.jsonl", "model_calls.jsonl"):
        assert path in merge_src, f"{path} is not passed at any _do_merge call site"
    # The two directory-shaped ones ride the generic tree pass. `sessions` declares
    # `jsonl_append` while being a tree on disk (see the docstring) — pinned as-is so the
    # discrepancy stays visible.
    assert declared["crashes"].kind == "json_entity_dir"
    assert declared["sessions"].kind == "jsonl_append"


# ── 🔴 six declared sqlite stores had no ATTACH executor (S180) ──


def _kb_db(path: Path, tag: str, n: int = 40) -> Path:
    """A knowledge-shaped DB: a PK'd content table plus an FTS5 index over it. Built through real
    sqlite because the whole question is how FTS shadow tables behave under a merge."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE items(id TEXT PRIMARY KEY, body TEXT)")
    conn.execute(
        "CREATE VIRTUAL TABLE items_fts USING fts5(body, content='items', content_rowid='rowid')"
    )
    for i in range(n):
        conn.execute("INSERT INTO items VALUES(?,?)", (f"{tag}{i}", f"{tag} doc {i}"))
    conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return path


def test_a_MERGE_recovers_every_declared_sqlite_store(tmp_path: Path) -> None:
    """🔴 THE DEFECT. Seven entries declare `merge=sqlite_attach_ignore`; only `memory.db` had an
    executor. S177 made the other six reachable, but reachably copy-if-missing — so a database the
    live home already had kept its own rows and dropped the snapshot's entirely.

    Driven across all six: a snapshot row and a live row went in, only the live row came out. Six
    stores silently half-restored, including `learning.db` and both knowledge stores.
    """
    from personalclaw.snapshot import _do_merge

    dbs = [
        "learning.db",
        "knowledge/knowledge.db",
        "loop/loops.db",
        "workflows/runs.db",
        "workspace/knowledge/knowledge.db",
        "workspace/lexicon/lexicon.db",
    ]
    snap, pc = tmp_path / "snap", tmp_path / "home"
    for root, tag in ((snap, "FROM-SNAPSHOT"), (pc, "LIVE")):
        root.mkdir(exist_ok=True)
        (root / "config.json").write_text("{}", encoding="utf-8")
        for rel in dbs:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(root / rel))
            conn.execute("CREATE TABLE rows(id TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO rows VALUES(?,?)", (tag, tag))
            conn.commit()
            conn.close()

    _do_merge(snap, pc, None)

    for rel in dbs:
        conn = sqlite3.connect(str(pc / rel))
        ids = sorted(r[0] for r in conn.execute("SELECT id FROM rows"))
        conn.close()
        assert ids == ["FROM-SNAPSHOT", "LIVE"], f"{rel} did not merge: {ids}"


def test_the_FTS_index_is_REBUILT_not_merged(tmp_path: Path) -> None:
    """🔴 The reason this is not a one-line "merge every table".

    Merging FTS5 shadow tables looks correct ONCE and breaks on the second run: measured 40
    documents
    indexed, then a repeated merge returned **80 rows for 40 documents** — every search result
    duplicated — because `items_fts_data`/`_idx`/`_docsize` carry segment state `INSERT OR IGNORE`
    cannot reconcile. A restore drill is exactly the thing a user runs twice.

    So virtual tables and their shadow tables are skipped and `rebuild` is issued instead. Asserted
    over THREE consecutive merges, since the defect only appears from the second.
    """
    from personalclaw.snapshot import _merge_sqlite_attach

    src = _kb_db(tmp_path / "snap.db", "snap")
    dst = _kb_db(tmp_path / "live.db", "live")

    for attempt in (1, 2, 3):
        _merge_sqlite_attach(src, dst, "kb")
        conn = sqlite3.connect(str(dst))
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 80
        hits = conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'snap'").fetchall()
        assert len(hits) == 40, f"merge #{attempt}: {len(hits)} search rows for 40 documents"
        orphans = [
            r[0]
            for r in hits
            if not conn.execute("SELECT 1 FROM items WHERE rowid=?", (r[0],)).fetchone()
        ]
        assert orphans == [], f"merge #{attempt}: index points at {len(orphans)} missing rows"
        conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
        conn.close()


def test_a_CORRUPT_source_leaves_the_live_store_untouched(tmp_path: Path) -> None:
    """A restore reads a file that has travelled: a truncated archive or a bad disk must cost that
    one store, not the database the user still has. Mirrors `_merge_memory`'s integrity pre-check.
    """
    from personalclaw.snapshot import _merge_sqlite_attach

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all")
    live = _kb_db(tmp_path / "live.db", "keep", n=3)

    assert _merge_sqlite_attach(bad, live, "corrupt") == 0

    conn = sqlite3.connect(str(live))
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 3
    conn.close()


def test_SCHEMA_DRIFT_skips_the_table_and_keeps_the_rest(tmp_path: Path) -> None:
    """An older snapshot meets a newer schema. Two cases, both driven:

    * a table only the SNAPSHOT has is not created — importing a shape this build's code cannot read
      would be worse than omitting it, and the owning module creates its own tables on open;
    * a column-count mismatch skips that table and keeps going, the same call `_merge_memory` makes
      about its opportunistic `contributor` column: a partial restore beats an aborted one.
    """
    from personalclaw.snapshot import _merge_sqlite_attach

    src, dst = tmp_path / "s.db", tmp_path / "d.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE shared(id TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE only_in_snapshot(id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE drifted(id TEXT PRIMARY KEY, v TEXT, extra TEXT)")
    conn.execute("INSERT INTO shared VALUES('s','snap')")
    conn.execute("INSERT INTO only_in_snapshot VALUES('x')")
    conn.execute("INSERT INTO drifted VALUES('d','snap','more')")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(dst))
    conn.execute("CREATE TABLE shared(id TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE drifted(id TEXT PRIMARY KEY, v TEXT)")  # narrower
    conn.execute("INSERT INTO shared VALUES('l','live')")
    conn.commit()
    conn.close()

    _merge_sqlite_attach(src, dst, "drift")

    conn = sqlite3.connect(str(dst))
    assert sorted(r[0] for r in conn.execute("SELECT id FROM shared")) == ["l", "s"]
    assert (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='only_in_snapshot'").fetchone() is None
    )
    conn.close()


def test_MEMORY_DB_keeps_its_own_executor(tmp_path: Path) -> None:
    """🔴 The one store that must NOT be routed here. `_merge_memory` filters `WHERE is_deleted=0`,
    so a generic all-tables merge would **resurrect memories the user deleted** — measured on a
    synthetic soft-delete table, where the naive merge imported the tombstoned row.

    That filter is the reason its allowlist exists, not an accident of it. The six stores routed to
    the generic path were checked for soft-delete columns against a long-lived real home and the dev
    home: none has one.
    """
    import inspect

    from personalclaw import snapshot

    merge_src = inspect.getsource(snapshot._do_merge)
    assert 'e.path != "memory.db"' in merge_src, "memory.db must be excluded at the call site"
    assert "_merge_memory(" in merge_src, "memory.db's own executor must still run"


def test_the_attach_merge_is_driven_by_the_INVENTORY(tmp_path: Path) -> None:
    """Read off `sqlite_entries()` rather than a hardcoded list, so a sqlite store declared later
    merges by default — the same reason capture reads `backup_entries()`. A second hand-maintained
    list is how this whole class of defect started."""
    import inspect

    from personalclaw import snapshot
    from personalclaw.durability import inventory as inv

    merge_src = inspect.getsource(snapshot._do_merge)
    assert "sqlite_entries()" in merge_src
    assert "MERGE_SQLITE_ATTACH_IGNORE" in merge_src

    declared = {e.path for e in inv.sqlite_entries() if e.merge == inv.MERGE_SQLITE_ATTACH_IGNORE}
    assert (
        "memory.db" in declared
    ), "memory.db still declares the strategy; it just has its own path"
    assert len(declared - {"memory.db"}) == 6, "the six generic stores"


def test_the_FTS_SKIP_avoids_importing_foreign_segment_state(tmp_path: Path) -> None:
    """The skip's own contribution, measured separately from the rebuild.

    🔴 Found by unwiring each half: removing the SKIP leaves the tests green, because the trailing
    `rebuild` repairs the shadow tables anyway — so the rebuild is the load-bearing fix. The skip
    still earns its place: without it the merge writes **160 rows** of the other database's segment
    state (measured, versus 40 real rows) before overwriting them, and a future caller that rebuilds
    conditionally would silently reintroduce the doubling.

    Asserted on the row COUNT the merge reports, which is the observable difference.
    """
    from personalclaw.snapshot import _merge_sqlite_attach

    src = _kb_db(tmp_path / "snap.db", "snap")
    dst = _kb_db(tmp_path / "live.db", "live")

    imported = _merge_sqlite_attach(src, dst, "kb")

    assert imported == 40, (
        f"imported {imported} rows for 40 documents — shadow tables are being copied, "
        "so the FTS skip is not in effect"
    )


def test_a_source_sqlite_calls_DAMAGED_is_refused_before_any_import(tmp_path, monkeypatch) -> None:
    """The integrity pre-check's own contract, isolated.

    🔴 Found by unwiring it: the corrupt-source test above passes WITHOUT the pre-check, because a
    file that is not a database fails at `ATTACH` and the rollback already protects the destination.
    So that test proves the rollback, not this check.

    The case only the pre-check covers is a file sqlite can OPEN and read while
    `PRAGMA integrity_check` reports damage — a torn page set from a partial copy, which is
    precisely
    what the safe-backup-API pass exists to avoid producing. Importing "readable" rows out of a
    database sqlite calls damaged would launder corruption into the one good copy the user has.

    Driven by forcing the pragma's answer rather than guessing byte offsets: a hand-corrupted file
    either still reports `ok` (damage in free space) or fails to open, so neither reaches this
    branch.
    """
    # Patch the sqlite3 the MODULE bound, not this test's stdlib import. On CI (Linux x86_64)
    # `pysqlite3-binary` is installed, so `snapshot.py` does `import pysqlite3 as sqlite3` — a
    # DIFFERENT module object from the test's stdlib `sqlite3`. Patching `sq.connect` there left
    # the code's real connections unpatched, the integrity check ran for real and passed on a
    # valid db, and the merge imported the row → `assert 1 == 0` on CI while passing locally
    # (where pysqlite3 is absent and the two happen to be the same object).
    from personalclaw import snapshot as snap_mod
    from personalclaw.snapshot import _merge_sqlite_attach

    sq = snap_mod.sqlite3

    src, dst = tmp_path / "s.db", tmp_path / "d.db"
    for path, tag in ((src, "snap"), (dst, "live")):
        conn = sq.connect(str(path))
        conn.execute("CREATE TABLE items(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO items VALUES(?)", (tag,))
        conn.commit()
        conn.close()

    real_connect = sq.connect

    class _Damaged:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args):
            if "integrity_check" in sql:

                class _Row:
                    def fetchone(self):
                        return ("*** in database main ***\nPage 3: btreeInitPage() error",)

                return _Row()
            return self._inner.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _connect(target, *args, **kwargs):
        conn = real_connect(target, *args, **kwargs)
        return _Damaged(conn) if "mode=ro" in str(target) else conn

    monkeypatch.setattr(snap_mod.sqlite3, "connect", _connect)
    imported = _merge_sqlite_attach(src, dst, "damaged")
    monkeypatch.undo()

    assert imported == 0
    conn = real_connect(str(dst))
    assert [r[0] for r in conn.execute("SELECT id FROM items")] == ["live"]
    conn.close()


def test_one_UNREADABLE_store_does_not_cost_the_others(tmp_path: Path) -> None:
    """Per-store containment, and the one place this executor deliberately DIFFERS from
    `_merge_memory`.

    `_merge_memory` re-raises on an outer failure, and `_do_merge` does not catch it — so a broken
    `memory.db` aborts the entire restore. That is right for the primary store: silently continuing
    past the memory merge would leave a user believing their memory came back.

    It is wrong for these six. They are independent stores, so one unreadable file must cost that
    file
    only. Driven with a poisoned `knowledge.db`: the other three databases merged, and the `skills`
    component still restored afterwards.
    """
    from personalclaw.snapshot import _do_merge

    dbs = ["learning.db", "knowledge/knowledge.db", "loop/loops.db", "workflows/runs.db"]
    snap, pc = tmp_path / "snap", tmp_path / "home"
    for root, tag in ((snap, "SNAP"), (pc, "LIVE")):
        root.mkdir(exist_ok=True)
        (root / "config.json").write_text("{}", encoding="utf-8")
        for rel in dbs:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(root / rel))
            conn.execute("CREATE TABLE r(id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO r VALUES(?)", (tag,))
            conn.commit()
            conn.close()
    (snap / "knowledge/knowledge.db").write_bytes(b"garbage not a db")
    (snap / "skills").mkdir()
    (snap / "skills" / "s.md").write_text("# skill", encoding="utf-8")

    _do_merge(snap, pc, None)

    for rel in ("learning.db", "loop/loops.db", "workflows/runs.db"):
        conn = sqlite3.connect(str(pc / rel))
        assert sorted(r[0] for r in conn.execute("SELECT id FROM r")) == ["LIVE", "SNAP"], rel
        conn.close()
    conn = sqlite3.connect(str(pc / "knowledge/knowledge.db"))
    assert [r[0] for r in conn.execute("SELECT id FROM r")] == ["LIVE"]
    conn.close()
    assert (pc / "skills" / "s.md").is_file(), "a later component must still restore"


def test_a_LOCKED_destination_degrades_to_a_skip(tmp_path: Path) -> None:
    """The gateway holds these databases open in WAL mode, and the IMPORT path
    (`portability.apply_import_zip` → `_do_replace`) has **no gateway gate** — only `restore_main`
    refuses while the gateway runs.

    So a locked destination is reachable in production. Measured: an uncommitted writer holding
    `BEGIN IMMEDIATE` makes the merge print a skip and import nothing, leaving the destination
    exactly as it was — the same shape `_merge_memory` uses for a per-table failure, not a crash and
    not a partial write.
    """
    # Hold the lock with the SAME sqlite the code uses. On CI (Linux x86_64) `snapshot.py` binds
    # `pysqlite3`, and a holder opened via the test's stdlib `sqlite3` is a different SQLite build
    # whose lock the code's pysqlite3 connection need not observe — so the merge acquired the lock
    # and imported, giving `assert 1 == 0` on CI while passing locally (one build, shared locking).
    from personalclaw import snapshot as snap_mod
    from personalclaw.snapshot import _merge_sqlite_attach

    sq = snap_mod.sqlite3

    src, dst = tmp_path / "s.db", tmp_path / "d.db"
    for path, tag in ((src, "snap"), (dst, "live")):
        conn = sq.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO items VALUES(?)", (tag,))
        conn.commit()
        conn.close()

    holder = sq.connect(str(dst))
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO items VALUES('inflight')")
    try:
        imported = _merge_sqlite_attach(src, dst, "locked")
    finally:
        holder.rollback()
        holder.close()

    assert imported == 0
    conn = sq.connect(str(dst))
    assert [r[0] for r in conn.execute("SELECT id FROM items")] == ["live"]
    conn.close()
