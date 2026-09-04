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

    def test_script_cron_store_is_claimed_and_travels(self, tmp_path):
        """`crons/` holds the scripts that `triggers.json` script jobs execute by path.
        Before it was declared, EVERY fresh home failed the audit (self-QA seeds a
        script cron at first boot) and a restore reproduced the trigger row while
        losing its script — the automation survived as data and broke as behavior."""
        # Claimed: a fresh-boot-shaped home with a seeded script cron audits clean.
        home = tmp_path / "home"
        (home / "crons").mkdir(parents=True)
        (home / "crons" / "selfqa_commit_watch.py").write_text("# script cron")
        (home / "crons" / "selfqa_commit_watch.config.json").write_text("{}")
        result = inv.audit_home(home)
        assert result.ok, f"unclaimed={result.unclaimed} dbs={result.undeclared_dbs}"
        # The directory claim must not shadow the legacy single-file entry.
        assert inv.claim_for("crons.json").id == "crons"
        assert inv.claim_for("crons/anything.py").id == "cron_scripts"
        # Travels: in the snapshot projection AND the portable export (scripts are
        # user-authored automation, same standing as skills/workflows).
        assert "crons" in {e.path for e in inv.backup_entries()}
        assert "crons" in {e.path for e in inv.export_entries()}


# ── 🔴 the claims-everything guard had never met a real home (S179) ──


class TestTheGuardMeetsARealHome:
    """`audit_home()` is the guard that "keeps the manifest honest". Every test above builds an
    eight-path synthetic fixture, and the function had **no runtime caller** — so a store added
    after
    the manifest was written could not fail it.

    Pointed at a real home for the first time it reported **10 unclaimed paths and 5482 undeclared
    databases**, and `learning.db` — the learning staging log and usage counters — was verified
    absent
    from a real archive.
    """

    def test_a_declared_store_is_reachable_by_a_snapshot(self, tmp_path):
        """Each newly declared entry must be CARRIED, not merely declared. Declaring without
        capturing is the inert half of this fix: the manifest would read complete while the archive
        stayed short."""
        import personalclaw.snapshot as snap

        for entry in inv.backup_entries():
            target = tmp_path / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if "." in entry.path.split("/")[-1]:
                target.write_text("{}", encoding="utf-8")
            else:
                target.mkdir(exist_ok=True)

        staged = set(snap._everything_paths(tmp_path)) | {
            f for files in snap.CORE_FILES.values() for f in files
        }
        staged |= {"workspace", "skills"}
        staged |= set(snap._declared_db_paths())

        for new_id in (
            "learning_db",
            "inbox",
            "spend",
            "model_calls",
            "knowledge_root_db",
            "agent_metadata",
            "learning_proposals",
            "durability_state",
            "workflow_runs_db",
        ):
            entry = next(e for e in inv.INVENTORY if e.id == new_id)
            covered = entry.path in staged or any(
                "/".join(entry.path.split("/")[:i]) in staged
                for i in range(1, len(entry.path.split("/")))
            )
            assert covered, f"{entry.path} is declared but no snapshot path carries it"

    def test_the_MACHINE_LOCAL_paths_are_ignored_not_declared(self):
        """🔴 SECURITY / identity. `session_key` and `sessions.json` hold live auth material, and
        `machine_id` is what `durability/shards.py` stamps shards with — a restored copy would
        masquerade as the machine it came from.

        Ignored rather than `secret=True`: a secret entry is captured ON PURPOSE so a backup can
        restore the credential store, whereas these must not travel at all.
        """
        for path in ("session_key", "sessions.json", "machine_id"):
            assert inv.is_ignored(path), f"{path} must not travel in a snapshot"
            assert inv.claim_for(path) is None, f"{path} must not be a declared entry"

    def test_a_DB_inside_a_TREE_entry_is_still_caught(self, tmp_path):
        """🔴 MY OWN FIX BLINDED THIS AND A DRIVE CAUGHT IT.

        `codegraph/` holds one database per workspace (5478 in a real home), so an exact-path
        compare
        can never match them and the audit drowns — the same over-reporting failure S178 fixed in
        the
        coverage ratchet. My first exemption keyed off `kind`/`derived` and therefore skipped every
        tree prefix, including `loop/` and `workspace/` — silencing the exact hazard the check
        exists
        for ("a database nested inside a `tree` entry … gets filesystem-copied while open in WAL
        mode").

        Narrowed to an opt-in `db_container` flag, so the exemption names the stores whose whole
        content IS databases and nothing else inherits it.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text("{}", encoding="utf-8")
        for tree in ("loop", "workspace"):
            (home / tree).mkdir()
            sqlite3.connect(str(home / tree / "surprise.db")).close()

        result = inv.audit_home(home)

        for tree in ("loop", "workspace"):
            assert f"{tree}/surprise.db" in result.undeclared_dbs

    def test_a_DB_CONTAINER_absorbs_its_own_databases(self, tmp_path):
        """The narrow exemption still has to work: `codegraph/<key>.db` must not be reported."""
        home = tmp_path / "home"
        (home / "codegraph").mkdir(parents=True)
        (home / "config.json").write_text("{}", encoding="utf-8")
        for key in ("ws-a", "ws-b"):
            sqlite3.connect(str(home / "codegraph" / f"{key}.db")).close()

        result = inv.audit_home(home)

        assert result.undeclared_dbs == []
        assert "codegraph/" not in result.unclaimed

    def test_only_codegraph_is_a_DB_CONTAINER(self):
        """Pinned so the flag cannot spread. Every added `db_container` widens the blind spot the
        test above exists to keep narrow — a second store needs its own argued reason."""
        containers = sorted(e.id for e in inv.INVENTORY if e.db_container)
        assert containers == ["codegraph"]

    def test_a_directory_is_claimed_by_its_DECLARED_CONTENTS(self, tmp_path):
        """`knowledge/` holds only `knowledge/knowledge.db`. `claim_for` is longest-prefix, so it
        can
        name the child without naming the parent — and the audit's top-level loop then reported the
        parent as unclaimed. Requiring a redundant wrapper entry per nested store would make the
        manifest describe the audit's implementation rather than the state."""
        home = tmp_path / "home"
        (home / "knowledge").mkdir(parents=True)
        (home / "config.json").write_text("{}", encoding="utf-8")
        sqlite3.connect(str(home / "knowledge" / "knowledge.db")).close()

        result = inv.audit_home(home)

        assert "knowledge/" not in result.unclaimed
        assert result.undeclared_dbs == []

    def test_the_DERIVED_indexes_stay_out_of_a_backup(self):
        """Both index stores declare themselves disposable in their own docstrings —
        `session_search` "holds no truth of its own … better rebuilt than restored", `codegraph`
        re-parses on mtime. Declaring them as state would ship a 10552-entry cache in every
        snapshot;
        not declaring them at all was the bug."""
        backed_up = {e.id for e in inv.backup_entries()}
        for derived_id in ("session_search_db", "codegraph"):
            entry = next(e for e in inv.INVENTORY if e.id == derived_id)
            assert entry.derived is True
            assert derived_id not in backed_up
