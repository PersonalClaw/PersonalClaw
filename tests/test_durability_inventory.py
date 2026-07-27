"""DURABILITY §1 — the state inventory and the gap it closes.

The inventory exists because two hand-maintained allowlists (`snapshot.CORE_FILES`
and `portability.EXPORT_EXCLUDE`) had drifted from reality: nine real store
directories were backed up by NEITHER. These tests lock the three properties that
keep that from recurring:

1. the manifest is internally well-formed (no typos in kind/domain/merge, no
   duplicate ids or paths);
2. `audit_home` FAILS on an unclaimed path or an undeclared database — this is
   the guard that catches a newly added store;
3. every database is declared, so it gets the sqlite backup API rather than a
   filesystem copy of a live WAL store.
"""

from __future__ import annotations

import sqlite3

from personalclaw.durability import inventory as inv


class TestManifestWellFormed:
    def test_no_duplicate_ids_or_paths(self):
        ids = [e.id for e in inv.all_entries()]
        paths = [e.path for e in inv.all_entries()]
        assert len(ids) == len(set(ids)), "duplicate entry id"
        assert len(paths) == len(set(paths)), "duplicate entry path"

    def test_vocabularies_are_respected(self):
        """A typo'd kind/domain/merge would silently route an entry to the wrong
        mechanism (e.g. a DB copied as a tree), so pin them to the vocabularies."""
        for e in inv.all_entries():
            assert e.kind in inv.KINDS, f"{e.id}: bad kind {e.kind}"
            assert e.domain in inv.DOMAINS, f"{e.id}: bad domain {e.domain}"
            assert e.merge in inv.MERGES, f"{e.id}: bad merge {e.merge}"
            assert e.path and not e.path.startswith("/"), f"{e.id}: path must be home-relative"

    def test_secrets_are_never_exportable(self):
        """The one-way door: a secret must not appear in the export projection."""
        exportable = {e.id for e in inv.export_entries()}
        for e in inv.all_entries():
            if e.secret:
                assert e.id not in exportable, f"{e.id} is secret but exportable"

    def test_known_secrets_are_marked(self):
        secret = set(inv.secret_paths())
        for path in (".env", ".local_secret", "sel_hmac.key", "telemetry_salt", "credentials"):
            assert path in secret, f"{path} must be marked secret"

    def test_derived_entries_excluded_from_backup_by_default(self):
        """A stale index restored alongside a newer store is worse than no index."""
        default_ids = {e.id for e in inv.backup_entries()}
        with_derived = {e.id for e in inv.backup_entries(include_derived=True)}
        assert "memory_index_db" in with_derived
        assert "memory_index_db" not in default_ids
        assert default_ids < with_derived

    def test_domains_projection_covers_every_entry(self):
        """Snapshot components ARE the domains, so every entry must land in one."""
        covered = {e.id for d in inv.domains() for e in inv.entries_for_domain(d)}
        assert covered == {e.id for e in inv.all_entries()}


class TestClaimsEverything:
    """The guard that makes the CORE_FILES-drift bug class impossible."""

    def _home(self, tmp_path):
        home = tmp_path / "home"
        (home / "tasks").mkdir(parents=True)
        (home / "tasks" / "t-1.json").write_text("{}")
        (home / "workspace" / "knowledge").mkdir(parents=True)
        (home / "config.json").write_text("{}")
        (home / "gateway.log").write_text("noise")  # ignored
        (home / "locks").mkdir()  # ignored
        return home

    def test_clean_home_is_fully_claimed(self, tmp_path):
        result = inv.audit_home(self._home(tmp_path))
        assert result.ok, f"unclaimed={result.unclaimed} dbs={result.undeclared_dbs}"
        assert result.claimed > 0 and result.ignored > 0

    def test_a_new_undeclared_store_fails_the_audit(self, tmp_path):
        """THE point of this module: add a store, forget the manifest → caught."""
        home = self._home(tmp_path)
        (home / "brand_new_store").mkdir()
        (home / "brand_new_store" / "thing.json").write_text("{}")
        result = inv.audit_home(home)
        assert not result.ok
        assert "brand_new_store/" in result.unclaimed

    def test_an_undeclared_database_fails_the_audit(self, tmp_path):
        """A DB hidden inside a tree entry is the dangerous case — it would be
        filesystem-copied while the gateway holds it open in WAL mode."""
        home = self._home(tmp_path)
        (home / "loop").mkdir(exist_ok=True)
        sqlite3.connect(str(home / "loop" / "surprise.db")).close()
        result = inv.audit_home(home)
        assert not result.ok
        assert "loop/surprise.db" in result.undeclared_dbs

    def test_missing_home_is_not_an_error(self, tmp_path):
        assert inv.audit_home(tmp_path / "nope").ok

    def test_ignored_patterns_cover_runtime_noise(self, tmp_path):
        home = self._home(tmp_path)
        for noise in ("x.log", "y.pid", "z.lock", "config.json.bak", "memory.db-wal"):
            (home / noise).write_text("")
        assert inv.audit_home(home).ok

    def test_nested_store_claims_its_own_subtree(self):
        """Longest-match: knowledge.db belongs to knowledge_db, not to workspace."""
        claim = inv.claim_for("workspace/knowledge/knowledge.db")
        assert claim is not None and claim.id == "knowledge_db"
        outer = inv.claim_for("workspace/memory/notes.md")
        assert outer is not None and outer.id == "workspace"


class TestGapClosure:
    """The nine directories that were in NEITHER snapshot nor export."""

    def test_previously_uncovered_stores_are_declared(self):
        declared = {e.path for e in inv.all_entries()}
        for path in (
            "tasks",
            "projects",
            "loop",
            "artifacts",
            "prompts",
            "workflows",
            "agents",
            "apps",
            "entity_settings",
        ):
            assert path in declared, f"{path} is still undeclared"

    def test_every_real_database_is_declared_sqlite(self):
        """Each of these was found on a real home; a tree copy of any of them is
        the live-WAL hazard this session fixes."""
        db_paths = {e.path for e in inv.sqlite_entries()}
        for path in (
            "memory.db",
            "workspace/knowledge/knowledge.db",
            "workspace/lexicon/lexicon.db",
            "loop/loops.db",
        ):
            assert path in db_paths, f"{path} must be declared kind=sqlite"

    def test_backup_includes_work_domain(self):
        """A 'full backup' that drops the task board is the bug being fixed."""
        backed_up = {e.path for e in inv.backup_entries()}
        assert "tasks" in backed_up and "projects" in backed_up
