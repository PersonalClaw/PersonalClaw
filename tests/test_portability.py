"""Tests for personalclaw.portability — export/import zip feature."""

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.learning.staging import StagingStore
from personalclaw.portability import (
    EXPORT_EXCLUDE,
    _is_excluded,
    apply_import_zip,
    create_export_zip,
    validate_import_zip,
)


@pytest.fixture
def fake_personalclaw_home(tmp_path):
    """Create a realistic ~/.personalclaw directory structure for testing."""
    pc = tmp_path / ".personalclaw"
    pc.mkdir()

    # config.json
    config = {
        "agent": {"provider": "acp", "model": "auto", "yolo": False},
        "session": {"timeout_secs": 3600},
        "memory": {"embedding_provider": "none"},
    }
    (pc / "config.json").write_text(json.dumps(config, indent=2))

    # hooks.json
    (pc / "hooks.json").write_text(json.dumps({"hooks": [{"id": "h1", "cmd": "echo hi"}]}))

    # crons.json
    crons = {
        "jobs": [{"id": "c1", "name": "daily-check", "schedule": "0 9 * * *", "message": "check"}]
    }
    (pc / "crons.json").write_text(json.dumps(crons, indent=2))

    # notifications.jsonl
    (pc / "notifications.jsonl").write_text(
        json.dumps({"ts": "1700000000", "title": "test", "body": "notification"}) + "\n"
    )

    # memory.db (SQLite)
    db_path = pc / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, value_json TEXT, confidence REAL, source TEXT, created_at TEXT, updated_at TEXT, embedding BLOB, is_deleted INTEGER DEFAULT 0)"  # noqa: E501
    )
    conn.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at, is_deleted) VALUES ('user.name', '\"Alice\"', 0.9, 'agent', '2026-01-01', '2026-01-01', 0)"  # noqa: E501
    )
    conn.execute(
        "CREATE TABLE episodic_memories (id TEXT PRIMARY KEY, conversation_id TEXT, text TEXT, embedding BLOB, tags TEXT, importance REAL, created_at TEXT, last_accessed_at TEXT, is_deleted INTEGER DEFAULT 0)"  # noqa: E501
    )
    conn.execute(
        "INSERT INTO episodic_memories (id, conversation_id, text, importance, created_at, last_accessed_at, is_deleted) VALUES ('ep1', 'conv1', 'user asked about deployment', 0.8, '2026-01-01', '2026-01-01', 0)"  # noqa: E501
    )
    conn.execute(
        "CREATE TABLE knowledge_facts (subject TEXT, predicate TEXT, object TEXT, episode_id TEXT, created_at TEXT)"  # noqa: E501
    )
    conn.execute(
        "CREATE TABLE knowledge_edges (source_key TEXT, target_key TEXT, relation TEXT, weight REAL, metadata TEXT, created_at TEXT)"  # noqa: E501
    )
    conn.commit()
    conn.close()

    # memory_index.db (FTS5)
    idx_path = pc / "memory_index.db"
    conn = sqlite3.connect(str(idx_path))
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(path, content, tokenize='porter unicode61')"  # noqa: E501
    )
    conn.execute(
        "INSERT INTO memory_fts (path, content) VALUES ('preferences.md', 'user prefers dark mode')"
    )
    conn.commit()
    conn.close()

    # learning.db (the capture staging log + flush outcome records)
    learn = StagingStore(pc)
    learn.stage(
        cadence="per_turn",
        kind="lesson",
        content="Chose sqlite because the json file corrupted under writes",
    )
    with learn.flush("per_turn") as _result:
        _result["staged"] = 1
    learn.close()

    # workspace/memory/
    mem_dir = pc / "workspace" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "preferences.md").write_text(
        "# User Preferences\n\n- Prefers dark mode\n- Uses vim\n"
    )
    (mem_dir / "projects.md").write_text(
        "# Active Projects\n\n## PersonalClaw\nWorking on portability feature\n"
    )
    hist_dir = mem_dir / "history"
    hist_dir.mkdir()
    (hist_dir / "2026-05-17.md").write_text(
        "# 2026-05-17\n\n#### 09:00 PDT\nDiscussed architecture\n"
    )
    (hist_dir / "2026-05-18.md").write_text(
        "# 2026-05-18\n\n#### 10:00 PDT\nImplemented export feature\n"
    )

    # plan_memory/
    pm_dir = pc / "plan_memory"
    pm_dir.mkdir()
    (pm_dir / "current_plan.md").write_text("# Plan\n\nStep 1: Export\nStep 2: Import\n")

    # skills/
    sk_dir = pc / "skills" / "my-skill"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Test skill\n---\n# My Skill\n"
    )

    # Credential files that must be EXCLUDED
    (pc / ".env").write_text("SLACK_BOT_TOKEN=xoxb-secret\nSLACK_APP_TOKEN=xapp-secret\n")
    (pc / ".local_secret").write_text("dashboard-auth-token-xyz")
    (pc / "sel_hmac.key").write_text("hmac-key-content")
    (pc / "telemetry_salt").write_text("salt-value")
    (pc / "session_map.json").write_text(json.dumps({"dashboard:chat-1": {"sid": "abc"}}))
    (pc / "session_pids.txt").write_text("12345\n67890\n")
    (pc / "agent_pids.txt").write_text("111:222\n333:444\n")

    # Directories that must be excluded
    (pc / "snapshots").mkdir()
    (pc / "snapshots" / "old-snapshot.tar.gz").write_text("fake")
    (pc / "outbox").mkdir()
    (pc / "outbox" / "file.txt").write_text("delivered")

    return pc


@pytest.fixture
def patched_config_dir(fake_personalclaw_home):
    """Patch config_dir() to return our fake directory."""
    with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(fake_personalclaw_home)}):
            yield fake_personalclaw_home


# ── Export Tests ──


class TestExport:
    def test_export_creates_valid_zip(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert len(zip_bytes) > 0
        assert manifest["version"] == 2
        assert manifest["format"] == "zip"
        assert "created_at" in manifest
        assert "hostname" in manifest
        assert "contents" in manifest

        # Verify it's a valid zip
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert any("MANIFEST.json" in n for n in names)
        assert any("config.json" in n for n in names)
        zf.close()

    def test_export_includes_config(self, patched_config_dir):
        zip_bytes, _ = create_export_zip()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        config_entries = [n for n in zf.namelist() if n.endswith("config.json")]
        assert len(config_entries) == 1
        data = json.loads(zf.read(config_entries[0]))
        assert data["agent"]["provider"] == "acp"
        zf.close()

    def test_export_includes_crons(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert manifest["contents"].get("crons.json", 0) > 0
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        cron_entries = [n for n in zf.namelist() if n.endswith("crons.json")]
        assert len(cron_entries) == 1
        data = json.loads(zf.read(cron_entries[0]))
        assert data["jobs"][0]["name"] == "daily-check"
        zf.close()

    def test_export_includes_memory_db(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert manifest["contents"].get("memory.db", 0) > 0
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        db_entries = [n for n in zf.namelist() if n.endswith("memory.db")]
        assert len(db_entries) == 1
        # Verify it's a valid SQLite DB
        db_bytes = zf.read(db_entries[0])
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.write(db_bytes)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            rows = conn.execute("SELECT key, value_json FROM semantic_memory").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "user.name"
            conn.close()
        finally:
            os.unlink(tmp.name)
        zf.close()

    def test_export_includes_workspace_files(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert manifest["contents"]["workspace_files"] >= 4  # prefs, projects, 2 history
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert any("preferences.md" in n for n in names)
        assert any("projects.md" in n for n in names)
        assert any("2026-05-17.md" in n for n in names)
        zf.close()

    def test_export_includes_skills(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert manifest["contents"]["skill_count"] >= 1
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert any("SKILL.md" in n for n in names)
        zf.close()

    def test_export_includes_plan_memory(self, patched_config_dir):
        zip_bytes, manifest = create_export_zip()
        assert manifest["contents"]["plan_memory_files"] >= 1
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert any("current_plan.md" in n for n in names)
        zf.close()

    def test_export_excludes_credentials(self, patched_config_dir):
        zip_bytes, _ = create_export_zip()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        for excluded in EXPORT_EXCLUDE:
            assert not any(n.endswith(excluded) for n in names), f"{excluded} should be excluded"
        zf.close()

    def test_export_excludes_snapshots_dir(self, patched_config_dir):
        zip_bytes, _ = create_export_zip()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert not any("snapshots" in n for n in names)
        assert not any("outbox" in n for n in names)
        zf.close()

    def test_export_excludes_pid_files(self, patched_config_dir):
        # Add a .pid file
        (patched_config_dir / "gateway.pid").write_text("99999")
        zip_bytes, _ = create_export_zip()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert not any(".pid" in n for n in names)
        zf.close()

    def test_export_skips_symlinks(self, patched_config_dir):
        # Create a symlink in workspace
        link = patched_config_dir / "workspace" / "memory" / "evil_link.md"
        try:
            link.symlink_to("/etc/passwd")
        except OSError:
            pytest.skip("Cannot create symlinks")
        zip_bytes, _ = create_export_zip()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        assert not any("evil_link" in n for n in names)
        zf.close()

    def test_export_empty_personalclaw_dir(self, tmp_path):
        pc = tmp_path / "empty_pc"
        pc.mkdir()
        with patch("personalclaw.portability.config_dir", return_value=pc):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(pc)}):
                zip_bytes, manifest = create_export_zip()
        assert len(zip_bytes) > 0
        assert manifest["contents"].get("workspace_files", 0) == 0


# ── Validate Tests ──


class TestValidate:
    def test_validate_valid_zip(self, patched_config_dir):
        zip_bytes, _ = create_export_zip()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.write(zip_bytes)
        tmp.close()
        try:
            ok, error, manifest = validate_import_zip(Path(tmp.name))
            assert ok is True
            assert error == ""
            assert manifest["version"] == 2
        finally:
            os.unlink(tmp.name)

    def test_validate_not_a_zip(self, tmp_path):
        bad = tmp_path / "notazip.zip"
        bad.write_text("this is not a zip file")
        ok, error, _ = validate_import_zip(bad)
        assert ok is False
        assert "Invalid zip" in error

    def test_validate_missing_manifest(self, tmp_path):
        # Create a zip without MANIFEST.json
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("some-dir/config.json", '{"agent":{}}')
        zip_path = tmp_path / "no_manifest.zip"
        zip_path.write_bytes(buf.getvalue())
        ok, error, _ = validate_import_zip(zip_path)
        assert ok is False
        assert "MANIFEST" in error

    def test_validate_bad_version(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("export/MANIFEST.json", json.dumps({"version": 99}))
        zip_path = tmp_path / "bad_version.zip"
        zip_path.write_bytes(buf.getvalue())
        ok, error, _ = validate_import_zip(zip_path)
        assert ok is False
        assert "version" in error.lower()

    def test_validate_path_traversal(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd", "root:x:0:0")
            zf.writestr("export/MANIFEST.json", json.dumps({"version": 2}))
        zip_path = tmp_path / "traversal.zip"
        zip_path.write_bytes(buf.getvalue())
        ok, error, _ = validate_import_zip(zip_path)
        assert ok is False
        assert "traversal" in error.lower()

    def test_validate_absolute_path(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("/etc/shadow", "bad")
            zf.writestr("export/MANIFEST.json", json.dumps({"version": 2}))
        zip_path = tmp_path / "absolute.zip"
        zip_path.write_bytes(buf.getvalue())
        ok, error, _ = validate_import_zip(zip_path)
        assert ok is False
        assert "traversal" in error.lower()

    def test_validate_corrupt_manifest_json(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("export/MANIFEST.json", "not valid json {{{{")
        zip_path = tmp_path / "corrupt_manifest.zip"
        zip_path.write_bytes(buf.getvalue())
        ok, error, _ = validate_import_zip(zip_path)
        assert ok is False
        assert "manifest" in error.lower()


# ── Import Tests ──


class TestImportMerge:
    def _make_export(self, source_dir):
        """Export from source_dir and return zip path."""
        with patch("personalclaw.portability.config_dir", return_value=source_dir):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(source_dir)}):
                zip_bytes, _ = create_export_zip()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.write(zip_bytes)
        tmp.close()
        return Path(tmp.name)

    def test_import_merge_into_empty(self, patched_config_dir, tmp_path):
        """Import into a fresh (empty) PersonalClaw instance."""
        zip_path = self._make_export(patched_config_dir)
        try:
            # Target: empty directory
            target = tmp_path / "target_mc"
            target.mkdir()
            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    summary = apply_import_zip(zip_path, mode="merge")
            assert len(summary["items"]) > 0
            # memory.db should be copied
            assert (target / "memory.db").is_file()
            # crons.json should be copied
            assert (target / "crons.json").is_file()
        finally:
            os.unlink(str(zip_path))

    def test_import_merge_deduplicates_crons(self, patched_config_dir, tmp_path):
        """Merging the same export twice doesn't duplicate cron jobs."""
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_mc"
            target.mkdir()
            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")
                    # Import again — should not duplicate
                    apply_import_zip(zip_path, mode="merge")
            crons = json.loads((target / "crons.json").read_text())
            job_names = [j["name"] for j in crons["jobs"]]
            assert job_names.count("daily-check") == 1
        finally:
            os.unlink(str(zip_path))

    def test_import_merge_memory_db(self, patched_config_dir, tmp_path):
        """Merging memory.db inserts new rows without overwriting existing."""
        zip_path = self._make_export(patched_config_dir)
        try:
            # Create target with its own memory.db with different data
            target = tmp_path / "target_mc"
            target.mkdir()
            dst_db = target / "memory.db"
            conn = sqlite3.connect(str(dst_db))
            conn.execute(
                "CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, value_json TEXT, confidence REAL, source TEXT, created_at TEXT, updated_at TEXT, embedding BLOB, is_deleted INTEGER DEFAULT 0)"  # noqa: E501
            )
            conn.execute(
                "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at, is_deleted) VALUES ('user.team', '\"Platform\"', 0.95, 'agent', '2026-01-01', '2026-01-01', 0)"  # noqa: E501
            )
            conn.execute(
                "CREATE TABLE episodic_memories (id TEXT PRIMARY KEY, conversation_id TEXT, text TEXT, embedding BLOB, tags TEXT, importance REAL, created_at TEXT, last_accessed_at TEXT, is_deleted INTEGER DEFAULT 0)"  # noqa: E501
            )
            conn.execute(
                "CREATE TABLE knowledge_facts (subject TEXT, predicate TEXT, object TEXT, episode_id TEXT, created_at TEXT)"  # noqa: E501
            )
            conn.execute(
                "CREATE TABLE knowledge_edges (source_key TEXT, target_key TEXT, relation TEXT, weight REAL, metadata TEXT, created_at TEXT)"  # noqa: E501
            )
            conn.commit()
            conn.close()

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            # Both keys should exist
            conn = sqlite3.connect(str(dst_db))
            rows = conn.execute("SELECT key FROM semantic_memory ORDER BY key").fetchall()
            keys = [r[0] for r in rows]
            assert "user.name" in keys  # from import
            assert "user.team" in keys  # pre-existing
            conn.close()
        finally:
            os.unlink(str(zip_path))

    def test_import_copies_learning_db_when_absent(self, patched_config_dir, tmp_path):
        """The staging log travels, so capture history survives a move.

        A restored home that reports "no capture activity" because the log was
        left behind is indistinguishable from a broken capture path — the exact
        ambiguity the outcome records exist to remove.
        """
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_learn"
            target.mkdir()
            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            restored = StagingStore(target)
            try:
                assert restored.path.is_file()
                assert len(restored.pending()) == 1
                assert restored.health()["passes"] == 1
            finally:
                restored.close()
        finally:
            os.unlink(str(zip_path))

    def test_import_never_merges_two_staging_logs(self, patched_config_dir, tmp_path):
        """An existing log is left alone rather than merged.

        Merging would double-count evidence occurrences, and the evidence floor
        (`learning.min_evidence`) is what decides whether a pattern is real — so a
        merge would manufacture proposals out of a restore rather than out of the
        user's actual behaviour.
        """
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_learn_existing"
            target.mkdir()
            local = StagingStore(target)
            local.stage(cadence="per_turn", kind="lesson", content="a local signal only")
            local.close()

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            after = StagingStore(target)
            try:
                contents = [e.content for e in after.pending()]
                assert contents == ["a local signal only"]
            finally:
                after.close()
        finally:
            os.unlink(str(zip_path))

    def test_import_merge_workspace_no_overwrite(self, patched_config_dir, tmp_path):
        """Merge doesn't overwrite existing workspace files."""
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_mc"
            target.mkdir()
            # Create a pre-existing preferences file with different content
            mem_dir = target / "workspace" / "memory"
            mem_dir.mkdir(parents=True)
            (mem_dir / "preferences.md").write_text("# Existing prefs\n- Keep this\n")

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            # Pre-existing file should NOT be overwritten
            content = (mem_dir / "preferences.md").read_text()
            assert "Existing prefs" in content
            assert "Uses vim" not in content
        finally:
            os.unlink(str(zip_path))

    def test_import_merge_notifications(self, patched_config_dir, tmp_path):
        """Merge deduplicates notifications by timestamp."""
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_mc"
            target.mkdir()
            # Pre-existing notification
            (target / "notifications.jsonl").write_text(
                json.dumps({"ts": "1700000000", "title": "existing"}) + "\n"
            )

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            # Should still have only 1 entry (same ts)
            lines = [
                line
                for line in (target / "notifications.jsonl").read_text().splitlines()
                if line.strip()
            ]
            assert len(lines) == 1
        finally:
            os.unlink(str(zip_path))

    def test_import_merge_skills_no_overwrite(self, patched_config_dir, tmp_path):
        """Merge adds new skills but doesn't overwrite existing ones."""
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_mc"
            target.mkdir()
            sk_dir = target / "skills" / "my-skill"
            sk_dir.mkdir(parents=True)
            (sk_dir / "SKILL.md").write_text("# Existing skill content\n")

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="merge")

            # Existing skill should NOT be overwritten
            content = (sk_dir / "SKILL.md").read_text()
            assert "Existing skill content" in content
        finally:
            os.unlink(str(zip_path))


class TestImportReplace:
    def _make_export(self, source_dir):
        with patch("personalclaw.portability.config_dir", return_value=source_dir):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(source_dir)}):
                zip_bytes, _ = create_export_zip()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.write(zip_bytes)
        tmp.close()
        return Path(tmp.name)

    def test_import_replace_overwrites(self, patched_config_dir, tmp_path):
        """Replace mode overwrites existing files."""
        zip_path = self._make_export(patched_config_dir)
        try:
            target = tmp_path / "target_mc"
            target.mkdir()
            # Pre-existing config with different content
            (target / "config.json").write_text(json.dumps({"agent": {"provider": "ollama"}}))

            with patch("personalclaw.portability.config_dir", return_value=target):
                with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                    apply_import_zip(zip_path, mode="replace")

            # Config should be replaced
            data = json.loads((target / "config.json").read_text())
            assert data["agent"]["provider"] == "acp"
        finally:
            os.unlink(str(zip_path))


# ── Exclusion Logic Tests ──


class TestExclusionLogic:
    def test_excludes_env_file(self):
        from pathlib import PurePosixPath

        assert _is_excluded(PurePosixPath(".env"))

    def test_excludes_local_secret(self):
        from pathlib import PurePosixPath

        assert _is_excluded(PurePosixPath(".local_secret"))

    def test_excludes_pid_files(self):
        from pathlib import PurePosixPath

        assert _is_excluded(PurePosixPath("gateway.pid"))
        assert _is_excluded(PurePosixPath("some/nested/thing.pid"))

    def test_excludes_snapshots_dir(self):
        from pathlib import PurePosixPath

        assert _is_excluded(PurePosixPath("snapshots/backup.tar.gz"))

    def test_excludes_outbox_dir(self):
        from pathlib import PurePosixPath

        assert _is_excluded(PurePosixPath("outbox/file.txt"))

    def test_allows_config_json(self):
        from pathlib import PurePosixPath

        assert not _is_excluded(PurePosixPath("config.json"))

    def test_allows_memory_files(self):
        from pathlib import PurePosixPath

        assert not _is_excluded(PurePosixPath("workspace/memory/preferences.md"))

    def test_allows_skills(self):
        from pathlib import PurePosixPath

        assert not _is_excluded(PurePosixPath("skills/my-skill/SKILL.md"))


# ── Round-Trip Tests ──


class TestRoundTrip:
    """Verify export→import→export produces consistent state."""

    def test_full_round_trip(self, patched_config_dir, tmp_path):
        """Export from instance A, import to empty B, export from B — manifests should match."""
        # Export from A
        zip_bytes_a, manifest_a = create_export_zip()

        # Import to B
        target = tmp_path / "instance_b"
        target.mkdir()
        zip_path = tmp_path / "export_a.zip"
        zip_path.write_bytes(zip_bytes_a)

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="replace")

        # Export from B
        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                _, manifest_b = create_export_zip()

        # Content counts should match
        assert (
            manifest_b["contents"]["workspace_files"] == manifest_a["contents"]["workspace_files"]
        )
        assert manifest_b["contents"]["skill_count"] == manifest_a["contents"]["skill_count"]

    def test_export_import_preserves_semantic_memory(self, patched_config_dir, tmp_path):
        """Semantic memory entries survive a full export→import cycle."""
        zip_bytes, _ = create_export_zip()

        target = tmp_path / "target"
        target.mkdir()
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(zip_bytes)

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="replace")

        # Verify semantic memory
        conn = sqlite3.connect(str(target / "memory.db"))
        rows = conn.execute("SELECT key, value_json FROM semantic_memory").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "user.name"
        assert json.loads(rows[0][1]) == "Alice"

    def test_export_import_preserves_episodic_memory(self, patched_config_dir, tmp_path):
        """Episodic memory entries survive a full export→import cycle."""
        zip_bytes, _ = create_export_zip()

        target = tmp_path / "target"
        target.mkdir()
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(zip_bytes)

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="replace")

        conn = sqlite3.connect(str(target / "memory.db"))
        rows = conn.execute("SELECT id, text FROM episodic_memories").fetchall()
        conn.close()
        assert len(rows) == 1
        assert "deployment" in rows[0][1]


# ── automations travel with a snapshot (S113) ──


def _seed_automations(pc, *, names=("Nightly backup",), armed=True):
    """Write the trigger store + an event trigger + a run record the way the runtime does."""
    import asyncio

    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=pc)
    for n, name in enumerate(names):
        store.upsert(
            Trigger(
                id=f"clock:auto-{n}",
                name=name,
                kind="clock",
                enabled=True,
                spec={"kind": "cron", "expr": "0 3 * * *"},
                workflow={"inline": {"provider": "bash", "config": {"command": "backup"}}},
                next_fire_at="2026-01-01T03:00:00+00:00" if armed else "",
                run_count=42,
            )
        )
    (pc / "event_triggers.json").write_text(
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
    asyncio.run(
        ScheduleRunStore(pc).append(
            ScheduleRun(
                run_id="r1",
                job_id="clock:auto-0",
                trigger="schedule",
                started_at=1.0,
                finished_at=2.0,
                duration_ms=1,
                status="success",
            )
        )
    )
    return store


class TestAutomationsInASnapshot:
    """🔴 THE DEFECT (S113). `create_export_zip` carried `crons.json` and `hooks.json` and NOT
    `triggers.json` — the store that has been the sole source of automations since S101, and the
    only one since S112 deleted `ScheduleService`.

    Driven before the fix, against a home holding two automations, an event trigger and run history:
    the snapshot captured **`config.json` alone**. So `personalclaw snapshot` silently lost every
    automation the user had, and the release notes advise taking one before a breaking upgrade — the
    one moment it must not lose anything.
    """

    def test_the_trigger_store_is_captured(self, fake_personalclaw_home):
        _seed_automations(fake_personalclaw_home)
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, manifest = create_export_zip()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        assert any(n.endswith("/triggers.json") for n in names), names
        assert "triggers.json" in manifest["contents"]

    def test_event_triggers_are_captured(self, fake_personalclaw_home):
        """Named in the plan's own recon note as missing alongside the trigger store."""
        _seed_automations(fake_personalclaw_home)
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        assert any(n.endswith("/event_triggers.json") for n in names)

    def test_the_run_ledger_is_captured(self, fake_personalclaw_home):
        """A restored home whose triggers exist but whose history is empty reports "never ran" for
        automations that have run for months — indistinguishable from a broken fire path."""
        _seed_automations(fake_personalclaw_home)
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, manifest = create_export_zip()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        assert any("cron-history/" in n and n.endswith(".jsonl") for n in names), names
        assert manifest["contents"]["run_history_files"] >= 1

    def test_advisory_lock_files_do_not_travel(self, fake_personalclaw_home):
        """🔴 Found by driving: the run-history tree exported `cron-history/.history.lock`. A
        restored lock is one held by a process that does not exist on this machine."""
        _seed_automations(fake_personalclaw_home)
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        assert not [n for n in names if n.endswith(".lock")], names

    def test_a_round_trip_into_an_empty_home_restores_the_automations(
        self, fake_personalclaw_home, tmp_path
    ):
        from personalclaw.triggers.store import TriggerStore

        _seed_automations(fake_personalclaw_home, names=("Nightly backup", "Weekly report"))
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        zip_path = tmp_path / "snap.zip"
        zip_path.write_bytes(zip_bytes)

        target = tmp_path / "target"
        target.mkdir()
        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="merge")

        rows = TriggerStore(base_dir=target).load()
        assert {r.trigger.name for r in rows} == {"Nightly backup", "Weekly report"}
        assert (target / "event_triggers.json").is_file()
        assert (target / "cron-history" / "clock:auto-0.jsonl").is_file()

    def test_a_merge_skips_a_name_the_home_already_has(self, fake_personalclaw_home, tmp_path):
        """Skip-by-NAME, mirroring `_merge_crons`: an id collision between two homes is meaningless
        (ids are slugs), while a name collision means a second copy would fire the same work twice.
        """
        from personalclaw.triggers.store import TriggerStore

        _seed_automations(fake_personalclaw_home, names=("Nightly backup", "Weekly report"))
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        zip_path = tmp_path / "snap.zip"
        zip_path.write_bytes(zip_bytes)

        target = tmp_path / "target"
        target.mkdir()
        (target / "config.json").write_text("{}")
        _seed_automations(target, names=("Nightly backup",))  # the collision

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="merge")

        rows = TriggerStore(base_dir=target).load()
        names = sorted(r.trigger.name for r in rows)
        assert names == ["Nightly backup", "Weekly report"], names
        assert len(rows) == 2, "the duplicate must not be imported twice"

    def test_an_imported_automation_arrives_PAUSED_and_unarmed(
        self, fake_personalclaw_home, tmp_path
    ):
        """🔴 Runtime state is dropped on purpose. `next_fire_at` from another machine is a fire
        already scheduled elsewhere, and `run_count`/health describe runs this home never performed.
        An imported row arrives paused so the user chooses when it starts firing here."""
        from personalclaw.triggers.store import TriggerStore

        _seed_automations(fake_personalclaw_home, names=("Weekly report",))
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        zip_path = tmp_path / "snap.zip"
        zip_path.write_bytes(zip_bytes)

        target = tmp_path / "target"
        target.mkdir()
        (target / "config.json").write_text("{}")
        _seed_automations(target, names=("Something else",))

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="merge")

        imported = next(
            r.trigger
            for r in TriggerStore(base_dir=target).load()
            if r.trigger.name == "Weekly report"
        )
        assert imported.enabled is False
        assert imported.next_fire_at == ""
        assert imported.run_count == 0

    def test_the_home_s_own_automations_are_never_touched(self, fake_personalclaw_home, tmp_path):
        from personalclaw.triggers.store import TriggerStore

        _seed_automations(fake_personalclaw_home, names=("Weekly report",))
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        zip_path = tmp_path / "snap.zip"
        zip_path.write_bytes(zip_bytes)

        target = tmp_path / "target"
        target.mkdir()
        (target / "config.json").write_text("{}")
        _seed_automations(target, names=("Mine",))

        with patch("personalclaw.portability.config_dir", return_value=target):
            with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
                apply_import_zip(zip_path, mode="merge")

        mine = next(
            r.trigger for r in TriggerStore(base_dir=target).load() if r.trigger.name == "Mine"
        )
        assert mine.enabled is True, "an existing automation must keep firing"
        assert mine.next_fire_at, "and must keep its armed fire"

    def test_the_legacy_cron_file_still_travels(self, fake_personalclaw_home):
        """§6 keeps `crons.json` read-only on disk so `automation verify-migration` can diff both
        sides — a snapshot that dropped it would break that command after a move."""
        _seed_automations(fake_personalclaw_home)
        with patch("personalclaw.portability.config_dir", return_value=fake_personalclaw_home):
            zip_bytes, _ = create_export_zip()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        assert any(n.endswith("/crons.json") for n in names)


# ── the inventory ⇄ snapshot drift guard (S113) ──

#: Inventory entries the snapshot does NOT yet carry. Each is real missing coverage, PINNED so the
#: list can only shrink — a new state file added without snapshot coverage fails the test below
#: rather than silently joining a backlog nobody re-measures.
#:
#: 🔴 Found by cross-checking `durability.inventory.INVENTORY` (57 entries) against both snapshot
#: paths while closing §7 item 9: **25 of 57 declared state files travelled in neither.** The
#: automation domain is closed by this session; the remaining 22 belong to DURABILITY-AND-SYNC,
#: whose §1 promises "every byte of state is enumerated in one inventory" and whose plan owns the
#: export shards. Hand-listing them here would be a silent, unreviewed scope grab into that plan.
#: 🔴 Re-measured in S178 and it went from **24 entries to 4**. The old check grepped `snapshot.py`
#: for each literal path, which went stale the moment coverage became inventory-DERIVED: 18 of the
#: 24 were verified to round-trip through a real archive and come back, and 3 more are carried by an
#: ancestor's tree copy. A ratchet that over-reports is not the safe direction — the list becomes
#: noise and the one real gap hides among eighteen that are not.
#:
#: What remains is exactly the DELIBERATE omissions: every survivor is `derived=True`, so
#: `backup_entries()` skips it on purpose ("a stale index paired with a newer store is worse than
#: none"). Asserted below, so an entry can no longer sit here for a reason nobody re-checks.
_SNAPSHOT_COVERAGE_GAPS: frozenset[str] = frozenset(
    {
        # memory: the FAISS sidecar + its id map — rebuildable from memory.db, though a restore
        # currently re-embeds from scratch.
        "memory_faiss",
        "memory_ids",
        # platform: local model blobs and generated ACP adapters, both regenerated on demand.
        "models",
        "acp_adapters",
    }
)


def test_every_remaining_coverage_gap_is_DERIVED(tmp_path: Path):
    """The gap list must hold only deliberate omissions (S178).

    Before this, the list was a 24-entry backlog nobody re-measured — and 20 of the 24 were already
    covered. Pinning "every survivor is `derived=True`" means a genuinely uncovered NON-derived
    store cannot be parked here: it fails `test_the_snapshot_coverage_gap_list_can_only_shrink` on
    arrival, and adding it to the list fails this test instead.
    """
    from personalclaw.durability import inventory as inv

    by_id = {e.id: e for e in inv.INVENTORY}
    not_derived = [g for g in _SNAPSHOT_COVERAGE_GAPS if not by_id[g].derived]
    assert not not_derived, (
        f"these gaps are real state, not derived indexes: {sorted(not_derived)}. "
        "Cover them in a snapshot path rather than listing them."
    )


def _snapshot_source_text() -> str:
    import personalclaw.portability as _port
    import personalclaw.snapshot as _snap

    return Path(_port.__file__).read_text() + Path(_snap.__file__).read_text()


def _snapshot_covered_ids(tmp: Path) -> set[str]:
    """Inventory ids a snapshot round-trip actually carries — MEASURED, not grepped (S178).

    🔴 The substring check this replaces went stale the moment coverage became inventory-derived.
    `_everything_paths` (capture) and `_extra_restore_paths` (restore) iterate the inventory, so a
    path no longer appears literally in `snapshot.py` — and the grep reported **18 false gaps**,
    including `tags.json`, `crashes`, `sessions` and `loop/loops.db`, every one of which was
    verified to round-trip through a real archive and come back.

    A ratchet that over-reports is not the safe direction: the list becomes noise, and the ONE entry
    that is a real gap hides among eighteen that are not. So this asks the projections directly.
    """
    import personalclaw.snapshot as _snap
    from personalclaw.durability import inventory as inv

    src = _snapshot_source_text()
    for entry in inv.backup_entries():
        target = tmp / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if "." in entry.path.split("/")[-1]:
            target.write_text("{}", encoding="utf-8")
        else:
            target.mkdir(exist_ok=True)
    reachable = set(_snap._everything_paths(tmp)) & set(_snap._extra_restore_paths(tmp))

    def covered(path: str) -> bool:
        if path in src or path in reachable:
            return True
        # A nested entry is carried by its ANCESTOR's tree copy. `workspace/knowledge/knowledge.db`
        # and `workspace/lexicon/lexicon.db` are not enumerated by either projection — the top-level
        # `workspace` component already stages the tree — but a real round-trip restores both
        # (measured). Counting them as gaps would keep three permanent false entries on the list.
        parts = path.split("/")
        return any(
            "/".join(parts[:i]) in src or "/".join(parts[:i]) in reachable
            for i in range(1, len(parts))
        )

    return {e.id for e in inv.INVENTORY if covered(e.path)}


def test_every_automation_state_file_is_in_a_snapshot(tmp_path: Path):
    """🔴 The automation domain must be COMPLETE. This session's whole point: `triggers.json` was
    declared in the inventory and carried by neither snapshot path, so `personalclaw snapshot` lost
    every automation the user had."""
    from personalclaw.durability import inventory as inv

    covered = _snapshot_covered_ids(tmp_path)
    missing = [
        e.id
        for e in inv.INVENTORY
        if e.domain == inv.DOMAIN_AUTOMATION
        and e.id not in covered
        and e.id not in _SNAPSHOT_COVERAGE_GAPS
    ]
    assert not missing, f"automation state not carried by any snapshot path: {missing}"


def test_the_snapshot_coverage_gap_list_can_only_shrink(tmp_path: Path):
    """A new state file added without snapshot coverage must FAIL here rather than joining a backlog
    nobody re-measures. If you covered one, delete its entry; if you added state, cover it or add it
    with a reason."""
    from personalclaw.durability import inventory as inv

    uncovered = {e.id for e in inv.INVENTORY} - _snapshot_covered_ids(tmp_path)
    new_gaps = uncovered - _SNAPSHOT_COVERAGE_GAPS
    assert not new_gaps, (
        f"state declared in the inventory but carried by NO snapshot path: {sorted(new_gaps)}. "
        "Add it to a snapshot path, or to _SNAPSHOT_COVERAGE_GAPS with the reason."
    )
    stale = {
        gap
        for gap in _SNAPSHOT_COVERAGE_GAPS
        if gap not in {e.id for e in inv.INVENTORY} or gap not in uncovered
    }
    assert not stale, f"these gaps are closed or gone — remove them from the list: {sorted(stale)}"
