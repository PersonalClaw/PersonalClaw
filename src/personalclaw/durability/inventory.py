"""The state inventory — one manifest of everything that matters (§1).

Every durability mechanism (snapshot, export, and later shard/sync/drills) reads
THIS instead of carrying its own allowlist. That is the whole point: the previous
design had `snapshot.CORE_FILES` and `portability.EXPORT_EXCLUDE` maintained by
hand, and they had already drifted — nine real store directories were covered by
neither. A declarative manifest plus :func:`audit_home` (which fails on any
unclaimed path) makes that class of bug impossible to reintroduce silently.

Each entry declares four things that matter to a backup:

* **kind** — how to read/write it safely. ``sqlite`` in particular must never be
  raw-copied while the gateway holds it open; see :func:`sqlite_entries`.
* **domain** — the user-facing grouping. Snapshot components are exactly the
  domains, so ``VALID_COMPONENTS`` is derived, never typed twice.
* **secret** — never leaves this machine, in any export or sync.
* **derived** — an index/cache rebuilt from authoritative state. Excluded from
  shards and exports; restoring it is at best wasted bytes and at worst a
  corrupt index paired with a newer store.

``merge`` and ``tombstones`` are declared here but consumed by later sessions
(restore --mode merge, shard sync); they are part of the entry's identity, so
they belong in the manifest rather than being bolted on later.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── the vocabularies ────────────────────────────────────────────────────────

# How an entry is stored — determines the safe read/write mechanism.
KIND_JSON_ENTITY_DIR = "json_entity_dir"  # one JSON file per entity
KIND_JSON_FILE = "json_file"  # a single JSON document
KIND_JSONL_APPEND = "jsonl_append"  # append-only log
KIND_SQLITE = "sqlite"  # a database — NEVER raw-copy while live
KIND_TREE = "tree"  # an opaque file tree
KINDS = (KIND_JSON_ENTITY_DIR, KIND_JSON_FILE, KIND_JSONL_APPEND, KIND_SQLITE, KIND_TREE)

# User-facing grouping. Snapshot components ARE these (plus "everything").
DOMAIN_MEMORY = "memory"
DOMAIN_KNOWLEDGE = "knowledge"
DOMAIN_WORK = "work"
DOMAIN_AUTOMATION = "automation"
DOMAIN_PLATFORM = "platform"
DOMAIN_CONFIG = "config"
DOMAIN_SECURITY = "security"
DOMAINS = (
    DOMAIN_MEMORY,
    DOMAIN_KNOWLEDGE,
    DOMAIN_WORK,
    DOMAIN_AUTOMATION,
    DOMAIN_PLATFORM,
    DOMAIN_CONFIG,
    DOMAIN_SECURITY,
)

# How two copies of an entry reconcile (consumed by restore-merge + sync, S2/S3).
MERGE_UNION_BY_ID = "union_by_id"
MERGE_LWW = "lww_by_updated_at"
MERGE_APPEND_DEDUP = "append_dedup"
MERGE_SQLITE_ATTACH_IGNORE = "sqlite_attach_ignore"
MERGE_REPLACE_ONLY = "replace_only"
MERGES = (
    MERGE_UNION_BY_ID,
    MERGE_LWW,
    MERGE_APPEND_DEDUP,
    MERGE_SQLITE_ATTACH_IGNORE,
    MERGE_REPLACE_ONLY,
)


@dataclass(frozen=True)
class StateEntry:
    """One declared piece of PersonalClaw's state."""

    id: str
    kind: str
    path: str  # relative to the home directory
    domain: str
    merge: str
    secret: bool = False  # never leaves this machine
    derived: bool = False  # rebuildable index/cache — excluded from exports
    tombstones: bool = False  # deletes need markers to survive a sync merge
    # This store's content IS databases, one per key (`codegraph/<workspace>.db`), so the
    # undeclared-DB audit cannot match them by exact path and must accept the whole subtree. Opt-in
    # per entry, NOT inferred from `kind`: exempting every tree would blind the audit to a DB nested
    # in `loop/` or `workspace/`, which is the hazard it exists to catch.
    db_container: bool = False
    help: str = ""  # operator-facing description
    # Sub-paths inside `path` that are themselves derived (indexes, caches,
    # git-owned working copies). Relative to `path`; glob syntax allowed.
    derived_within: tuple[str, ...] = field(default_factory=tuple)


# ── the manifest ────────────────────────────────────────────────────────────
# Grounded in what actually exists under a real home (verified 2026-07-28 against
# both a fresh dev home and a long-lived real one), NOT in what the code's old
# allowlists claimed. Entries are declared even when the store is usually absent —
# an entry for a missing path is harmless; a missing entry for a present path is
# the bug this manifest exists to prevent.

INVENTORY: tuple[StateEntry, ...] = (
    # ── memory ──
    StateEntry(
        id="memory_db",
        kind=KIND_SQLITE,
        path="memory.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="semantic facts, episodes, lessons, memory events",
    ),
    StateEntry(
        id="memory_index_db",
        kind=KIND_SQLITE,
        path="memory_index.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="memory search index (rebuilt from memory.db)",
    ),
    StateEntry(
        id="memory_faiss",
        kind=KIND_TREE,
        path="memory.faiss",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="vector index (rebuilt from embeddings)",
    ),
    StateEntry(
        id="memory_ids",
        kind=KIND_JSON_FILE,
        path="memory.ids.json",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="vector index id map (rebuilt with the index)",
    ),
    # 🔴 UNCLAIMED until MGAV-6. `audit_home()` reported `memory-vault/` the moment a
    # user turned the vault on, and the synthetic eight-path fixture the audit tests
    # build never contains it — a declared guard that could not see the store.
    #
    # NOT `derived=True`, even though `POST /api/memory/vault/sync` rebuilds the whole
    # vault from `memory.db`. In `two_way` mode a page may hold an edit the human made
    # and the sync has not read back yet, and that edit exists NOWHERE else — dropping
    # it from the backup as "rebuildable" would lose the one thing in here that is not.
    #
    # Restoring it is safe *because of* `source_hash`: every page carries a hash of its
    # own body, so a restored page that still matches is recognized as an untouched
    # projection and simply re-rendered from whatever the store now says. Only a page
    # whose body diverges from its hash — a real, unsynced human edit — is read back.
    # A stale vault therefore cannot push old text over newer memory unless the human
    # genuinely typed that text and never synced; and that write rides `vault_edit`
    # through the WAL, so `undo_event` restores the prior value.
    #
    # `replace_only`: a markdown tree has no per-record identity to merge by. The path
    # is the DEFAULT `memory.vault_path`; a vault relocated elsewhere in the home is
    # the user putting their state outside the manifest's reach, same as any absolute
    # path.
    StateEntry(
        id="memory_vault",
        kind=KIND_TREE,
        path="memory-vault",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        help="readable markdown vault (browsable memory; may hold unsynced edits)",
    ),
    # ── knowledge ──
    StateEntry(
        id="knowledge_db",
        kind=KIND_SQLITE,
        path="workspace/knowledge/knowledge.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="knowledge items, entities, extractions",
    ),
    StateEntry(
        id="knowledge_files",
        kind=KIND_TREE,
        path="workspace/knowledge/files",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="original uploaded documents behind knowledge items",
    ),
    StateEntry(
        id="lexicon_db",
        kind=KIND_SQLITE,
        path="workspace/lexicon/lexicon.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="learned vocabulary / term lexicon",
    ),
    # ── work ──
    StateEntry(
        id="tasks",
        kind=KIND_JSON_ENTITY_DIR,
        path="tasks",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="tasks, task lists, and task comments",
    ),
    StateEntry(
        id="projects",
        kind=KIND_JSON_ENTITY_DIR,
        path="projects",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="projects and their briefs/context",
        # Worktrees are git-owned checkouts, re-creatable from the repo.
        derived_within=("*/worktrees",),
    ),
    StateEntry(
        id="loops_db",
        kind=KIND_SQLITE,
        path="loop/loops.db",
        domain=DOMAIN_WORK,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="autonomous run records",
    ),
    StateEntry(
        id="loop",
        kind=KIND_TREE,
        path="loop",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="autonomous run findings, verdicts, per-run files",
        # loops.db is its own entry (it needs the sqlite backup API, not a copy).
        derived_within=("loops.db",),
    ),
    StateEntry(
        id="artifacts",
        kind=KIND_TREE,
        path="artifacts",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="saved artifacts and their version history",
    ),
    StateEntry(
        id="sessions",
        kind=KIND_JSONL_APPEND,
        path="sessions",
        domain=DOMAIN_WORK,
        merge=MERGE_APPEND_DEDUP,
        help="chat transcripts",
    ),
    StateEntry(
        id="subagents",
        kind=KIND_TREE,
        path="subagents",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="subagent run records",
    ),
    StateEntry(
        id="uploads",
        kind=KIND_TREE,
        path="uploads",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="files uploaded through chat",
    ),
    StateEntry(
        id="code",
        kind=KIND_TREE,
        path="code",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="code-loop working checkouts",
        derived=True,  # git-owned clones, re-creatable from their remotes
    ),
    # EI-8's turn-bound file checkpoint store — pre-edit copies of workspace files so
    # /rewind-to-turn can restore them. Declared so `audit_home()` claims the path the moment
    # a session records its first backup.
    #
    # `derived=True` for the same reason as `code` above, and NOT because it is rebuildable
    # (it is not — the prior bytes exist nowhere else once the agent has overwritten them).
    # It is machine-LOCAL by construction: every manifest entry is an ABSOLUTE host path, so
    # the tree is meaningless in another home, and a restore that re-planted it would drop
    # copies of one machine's workspace into another's. It is also a live-session safety net
    # pruned with the session (`turn_checkpoints.prune_session`), so travelling it would ship
    # up to `checkpoints.max_mb` per session of file bodies that the workspace itself still
    # holds authoritatively.
    #
    # Credential material is excluded at CAPTURE time, not here:
    # `turn_checkpoints.NEVER_CAPTURE_GLOBS` means a `.env` body never enters the tree, so
    # this entry is not the thing keeping secrets out of an export.
    StateEntry(
        id="turn_checkpoints",
        kind=KIND_TREE,
        path="checkpoints",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        help="pre-edit file backups behind /rewind-to-turn (machine-local, session-scoped)",
        derived=True,
    ),
    # ── automation ──
    # 🔴 THE trigger store, and it was never declared here (S184). `triggers/store.py` opens with
    # "`triggers.json` — the one trigger store … absorbing crons.json / hooks.json /
    # event_triggers.json / autonudge config", and it is hand-listed in BOTH `snapshot.py` and
    # `portability.py` — each with a comment about the round trip that lost it. So it travels, but
    # nothing inventory-derived could see it: it was invisible to `home_is_populated`, to the
    # ratchet, and to every projection S176-S183 built.
    #
    # `audit_home()` WOULD have flagged it — verified, it reports `triggers.json` as unclaimed on a
    # home that has one. S179 audited both real homes clean because neither migrated to the store
    # yet, so the guard was right and the population was the gap. That is the same
    # fixture-versus-reality shape S179 itself was about.
    StateEntry(
        id="triggers",
        kind=KIND_JSON_FILE,
        path="triggers.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="the one trigger store (automations, event triggers, hooks)",
    ),
    StateEntry(
        id="crons",
        kind=KIND_JSON_FILE,
        path="crons.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="scheduled jobs (legacy; read-only, absorbed by triggers.json)",
    ),
    # 🔴 The script-cron store, and it was never declared here. `schedule_script.py` requires
    # every zero-token script job to live under `crons/` ("no escape"), and `triggers.json` —
    # which travels — references those scripts by path. So a restore reproduced the trigger and
    # lost its script: the automation survived as a row and broke as a behavior. Self-QA seeds a
    # script cron on first boot, which is how `audit_home()` caught it — EVERY fresh home reported
    # "1 unclaimed path(s)" and the dashboard strip opened coral on a box minutes old. The audit
    # was honest (the store really was in no snapshot); this entry is the fix, not the alarm.
    StateEntry(
        id="cron_scripts",
        kind=KIND_TREE,
        path="crons",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="script-cron files (the scripts + configs triggers.json script jobs execute)",
    ),
    # The self-QA companion's own data dir. SV-11 moved the commit-watch cursor here when the
    # watch stopped being a `crons/` script: an in-process module keeps companion state with the
    # companion. Declared rather than ignored because the cursor is what makes a restore quiet --
    # without it the first fire on a restored home sees every commit in the repo as new.
    StateEntry(
        id="selfqa",
        kind=KIND_TREE,
        path="selfqa",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_LWW,
        help="self-QA companion state (the commit-watch last-seen head)",
    ),
    StateEntry(
        id="hooks",
        kind=KIND_JSON_FILE,
        path="hooks.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="lifecycle triggers",
    ),
    StateEntry(
        id="event_triggers",
        kind=KIND_JSON_FILE,
        path="event_triggers.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="event-pattern triggers",
    ),
    StateEntry(
        id="autonudge",
        kind=KIND_JSON_FILE,
        path="autonudge.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_LWW,
        help="auto-nudge state",
    ),
    StateEntry(
        id="cron_history",
        kind=KIND_JSONL_APPEND,
        path="cron-history",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_APPEND_DEDUP,
        help="scheduled-run history",
    ),
    StateEntry(
        id="workflows",
        kind=KIND_JSON_ENTITY_DIR,
        path="workflows",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="workflows and SOPs",
    ),
    # ── platform ──
    StateEntry(
        id="skills",
        kind=KIND_TREE,
        path="skills",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="installed and authored skills",
        derived_within=(".skill_embeddings.json",),
    ),
    StateEntry(
        id="agents",
        kind=KIND_JSON_ENTITY_DIR,
        path="agents",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="agent definitions",
    ),
    StateEntry(
        id="prompts",
        kind=KIND_JSON_ENTITY_DIR,
        path="prompts",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="saved prompts",
    ),
    StateEntry(
        id="themes",
        # A custom theme is saved server-side by `POST /api/themes` into
        # `config_dir()/themes/<slug>.json`, and this inventory did not know the directory
        # existed — so `personalclaw snapshot` dropped every theme the user authored, and a
        # restore came back without them (#647). That matters more than an ordinary coverage
        # gap because the pre-1.0 release notes tell users to run `personalclaw snapshot`
        # BEFORE upgrading: the one command positioned as the safety net did not cover this.
        #
        # `json_entity_dir` + union-by-id, matching `prompts` and `agents`: one JSON file per
        # slug, and two homes that each authored a theme should end up with both rather than
        # one clobbering the other.
        kind=KIND_JSON_ENTITY_DIR,
        path="themes",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="custom colour themes saved from Settings > Design",
    ),
    StateEntry(
        id="prompt_snippets",
        kind=KIND_TREE,
        path="prompt_snippets",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="prompt snippets injected into system prompts",
    ),
    StateEntry(
        id="apps",
        kind=KIND_TREE,
        path="apps",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="installed app copies (their data/ holds real state)",
    ),
    StateEntry(
        id="extensions",
        kind=KIND_TREE,
        path="extensions",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="provider instances and per-use-case settings",
    ),
    StateEntry(
        id="entity_settings",
        kind=KIND_JSON_ENTITY_DIR,
        path="entity_settings",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-entity user settings",
    ),
    # 🔴 `dashboard_views.json` shipped with AS-1 and was never declared, so `audit_home()`
    # reports it the moment a user pins a tile — the exact drift this manifest exists to catch,
    # found by declaring the store next to it.
    StateEntry(
        id="dashboard_views",
        kind=KIND_JSON_FILE,
        path="dashboard_views.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="composable-home views and their pinned artifact tiles",
    ),
    # A TREE, not a `jsonl_append`, even though its leaves are JSONL: on disk it is one
    # DIRECTORY per tile (`dashboard_tiles/<view>__<slug>/events.jsonl`). Declaring the leaf kind
    # is the shape `sessions` got wrong — the generic per-file tree copy is the right executor
    # either way, and a line-dedup merge across a directory-per-tile store would be the wrong one.
    StateEntry(
        id="dashboard_tile_ledger",
        kind=KIND_TREE,
        path="dashboard_tiles",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="per-tile chatless-refresh ledger (freshness, cost, per-source outcomes)",
    ),
    StateEntry(
        id="models",
        kind=KIND_TREE,
        path="models",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived=True,  # re-downloadable weights; enormous
        help="downloaded local model weights (re-downloadable)",
    ),
    StateEntry(
        id="acp_adapters",
        kind=KIND_TREE,
        path="acp-adapters",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_REPLACE_ONLY,
        derived=True,  # installed CLI adapters, re-installable
        help="installed ACP CLI adapters (re-installable)",
    ),
    StateEntry(
        id="workspace",
        kind=KIND_TREE,
        path="workspace",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="the agent workspace (memory markdown, scratch files)",
        # The knowledge store lives inside workspace/ but is its own entry.
        derived_within=("knowledge",),
    ),
    StateEntry(
        id="screenshots",
        kind=KIND_TREE,
        path="screenshots",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="captured screenshots",
    ),
    StateEntry(
        id="crashes",
        kind=KIND_JSON_ENTITY_DIR,
        path="crashes",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="crash artifacts (rotated)",
    ),
    # EVALUATION-SUBSTRATE §1.1/§10 — the offline eval store (matrices, the append-only
    # results.tsv ledger; studies/benchmarks/trust arrive in later atoms). Harness
    # mechanics, so DOMAIN_PLATFORM; a file tree, so KIND_TREE + union-by-id like the
    # other tree stores (loop, artifacts, skills). Not derived: the evidence is the
    # point of backup, not a rebuildable index.
    #
    # 🔴 ES-5 wires the exclusion the ES-1a note deferred. §1.1/§2.2: `studies/*/locked`
    # holds hidden validation answer keys that are "never rendered into any worker session's
    # prompt, bindings, or workspace" — and an export archive handed to an agent is the
    # longest way round to exactly that. Now that `evals/studies/<id>/locked/` has a writer
    # (`evals/store.write_locked_check`), the control has something to protect.
    #
    # `derived_within` is the field with a live reader on BOTH paths that copy this tree
    # (`portability.py:_is_derived_within` and `snapshot.py:_derived_within`), so declaring
    # it here excludes the answer keys from the portability export AND from snapshots. The
    # snapshot half is a deliberate consequence, not an oversight: an answer key belongs on
    # one machine, and the cost — a REGISTERED-but-unrun study restored from a snapshot has
    # lost its checks — is caught loudly rather than silently, because `studies.run_study`
    # REFUSES a study whose registration declares checks it cannot load.
    #
    # §7/ES-10 adds `benchmarks/bakeoff` for the same reason and by the same mechanism: it
    # holds redacted excerpts of the user's OWN traffic, captured only while an off-by-
    # default flag is on. Those excerpts are scored once and expire, so the export/snapshot
    # cost of dropping them is nil (re-enable capture to refill), while carrying a redacted
    # slice of real inputs off the machine is a leak the flag's own privacy posture forbids.
    StateEntry(
        id="evals",
        kind=KIND_TREE,
        path="evals",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived_within=("studies/*/locked", "benchmarks/bakeoff"),
        help=(
            "offline eval substrate: scenario library, matrices, pinned results ledger, "
            "pre-registered studies (their hidden locked/ checks never leave this machine)"
        ),
    ),
    # 🔴 S179 — the ten paths `audit_home()` reports on a REAL home. The guard was correct and had
    # never been pointed at one: every existing test builds an 8-path synthetic fixture, so a store
    # added after the manifest was written could not fail it. Driven: `learning.db`,
    # `session_search.db`, `spend.json`, `model_calls.jsonl` and `inbox.json` were absent from a
    # real
    # archive.
    StateEntry(
        id="learning_db",
        kind=KIND_SQLITE,
        path="learning.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the learning staging log and usage counters",
    ),
    StateEntry(
        id="inbox",
        kind=KIND_JSON_FILE,
        path="inbox.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="native inbox items",
    ),
    StateEntry(
        id="spend",
        kind=KIND_JSON_FILE,
        path="spend.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-day model spend (drives the budget caps)",
    ),
    StateEntry(
        # AUTONOMY-GUARDRAILS §4.3: per-project Trust/Preview decisions keyed by resolved dir.
        # LWW — a decision is a small last-writer-wins flag, not an append log.
        id="project_trust",
        kind=KIND_JSON_FILE,
        path="project_trust.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-project Trust/Preview decisions (Preview → read-only project-script execution)",
    ),
    StateEntry(
        id="model_calls",
        kind=KIND_JSONL_APPEND,
        path="model_calls.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="one line per model-call attempt",
    ),
    StateEntry(
        # HARNESS-CRAFT §2.1 (HC-3): one bounded line per best-of-N call
        # ({ts,n,criteria_digest,winner_idx,score_spread,tokens_total} — no prompt or
        # candidate text). DERIVED, which is what "snapshot-excluded" means here: it is
        # telemetry-of-self for the learning/eval feed, reconstructible in spirit and
        # worthless to restore, so it is claimed (audit_home sees it) but never backed up.
        id="sampling_outcomes",
        kind=KIND_JSONL_APPEND,
        path="sampling_outcomes.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="one bounded line per best-of-N sampling call (did sampling help?)",
    ),
    StateEntry(
        # COST-AND-TOKEN-OBSERVABILITY §2.4: the per-turn cost/token ledger. Derived =
        # reconstructible telemetry-of-self (rebuildable from the SEL/event stream), not
        # irreplaceable user content, so export/retention treats it as disposable.
        id="usage_ledger",
        kind=KIND_JSONL_APPEND,
        path="usage",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="per-turn token + cost ledger (usage/turns.jsonl)",
    ),
    # Both index stores declare themselves disposable in their own docstrings — session_search
    # "holds no truth of its own … better rebuilt than restored", codegraph re-parses on mtime — so
    # they are DERIVED, which keeps them out of `backup_entries()` while still being claimed. Not
    # declaring them at all was the bug; declaring them as state would ship a 10552-entry cache in
    # every snapshot.
    StateEntry(
        id="session_search_db",
        kind=KIND_SQLITE,
        path="session_search.db",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="FTS index over transcripts (rebuilt by reindex_session)",
    ),
    # A DIRECTORY of per-workspace databases (`codegraph/<workspace-key>.db`), not one file — so
    # `kind` is a tree and the DB check needs the glob below rather than an exact path. Derived: the
    # index re-parses on mtime, and a real home had 5478 of these.
    StateEntry(
        id="codegraph",
        kind=KIND_TREE,
        path="codegraph",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        db_container=True,
        help="per-workspace symbol index (re-parsed on mtime)",
    ),
    # 🔴 A live DB inside a `tree` entry — precisely the hazard the undeclared-DB check exists to
    # catch ("it gets filesystem-copied while open in WAL mode"). `workflows` is declared
    # `json_entity_dir`, so its run ledger was being tree-copied rather than staged through the safe
    # backup API. Declaring it routes it to `_safe_copy_db` and excludes it from the tree copy.
    StateEntry(
        id="workflow_runs_db",
        kind=KIND_SQLITE,
        path="workflows/runs.db",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the workflow run ledger",
    ),
    StateEntry(
        id="knowledge_root_db",
        kind=KIND_SQLITE,
        path="knowledge/knowledge.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the home-level knowledge store",
    ),
    StateEntry(
        id="agent_metadata",
        kind=KIND_JSON_ENTITY_DIR,
        path="agent-metadata",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="per-agent metadata records",
    ),
    StateEntry(
        id="learning_proposals",
        kind=KIND_TREE,
        path="learning",
        domain=DOMAIN_MEMORY,
        merge=MERGE_UNION_BY_ID,
        help="staged learning proposals awaiting review",
    ),
    StateEntry(
        id="durability_state",
        kind=KIND_JSON_FILE,
        path="durability_state.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="the durability scheduler's own last-run state",
    ),
    StateEntry(
        id="folders",
        kind=KIND_JSON_FILE,
        path="folders.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="folder organization",
    ),
    StateEntry(
        id="tags",
        kind=KIND_JSON_FILE,
        path="tags.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="tag vocabulary",
    ),
    StateEntry(
        id="tool_usage",
        kind=KIND_JSON_FILE,
        path="tool_usage.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="tool usage counters",
    ),
    StateEntry(
        id="tokenjuice_savings",
        kind=KIND_JSON_FILE,
        path="tokenjuice_savings.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="context-savings ledger",
    ),
    StateEntry(
        id="feedback",
        kind=KIND_JSONL_APPEND,
        path="feedback.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="thumbs feedback on AI judgments",
    ),
    StateEntry(
        id="notifications",
        kind=KIND_JSONL_APPEND,
        path="notifications.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="notification history",
    ),
    # ── config ──
    StateEntry(
        id="config",
        kind=KIND_JSON_FILE,
        path="config.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the main configuration document",
    ),
    StateEntry(
        id="autonomy_rungs",
        kind=KIND_JSON_FILE,
        path="autonomy_rungs.json",
        domain=DOMAIN_CONFIG,
        # Last-write-wins per file rather than a union: a rung grant and a demotion for
        # the same action type are contradictory decisions, and merging them would
        # resurrect a grant the other machine already withdrew. The conservative merge
        # for a permission store is the newest whole document.
        merge=MERGE_LWW,
        help="earned-autonomy rung grants and demotion history",
    ),
    StateEntry(
        id="autonomy_reversals",
        kind=KIND_JSON_FILE,
        path="autonomy_reversals.json",
        domain=DOMAIN_CONFIG,
        # Last-write-wins for the same reason as the grants beside it, with one extra: a
        # union merge could resurrect a record another machine has already marked reversed,
        # and re-offering an undo for something already undone is the one wrong answer this
        # store can give.
        merge=MERGE_LWW,
        help="undo handles for actions that ran at the auto-with-undo rung",
    ),
    StateEntry(
        id="active_models",
        kind=KIND_JSON_FILE,
        path="active_models.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="per-use-case model bindings",
    ),
    StateEntry(
        id="active_search_providers",
        kind=KIND_JSON_FILE,
        path="active_search_providers.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="search provider bindings",
    ),
    StateEntry(
        id="voice_profiles",
        kind=KIND_JSON_ENTITY_DIR,
        path="voice_profiles",
        domain=DOMAIN_CONFIG,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="voice profiles: records, reference audio, locked clips, consent recordings",
        # A generation-history clip is disposable render output (bounded LRU, re-derived
        # by simply speaking again) — a backup should not carry it. The reference clip,
        # the locked clip and the consent recording ARE authoritative user content and
        # stay covered: losing them loses the voice and its provenance.
        derived_within=("*/history",),
    ),
    StateEntry(
        id="voice_bindings",
        kind=KIND_JSON_FILE,
        path="voice_bindings.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="per-surface voice profile bindings (channel/agent/client + default)",
    ),
    StateEntry(
        id="active_prompts",
        kind=KIND_JSON_FILE,
        path="active_prompts.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="active prompt selections",
    ),
    StateEntry(
        id="tool_prefs",
        kind=KIND_JSON_FILE,
        path="tool_prefs.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="disabled tools and providers",
    ),
    StateEntry(
        id="mcp",
        kind=KIND_JSON_FILE,
        path="mcp.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="MCP server configuration",
    ),
    StateEntry(
        id="session_map",
        kind=KIND_JSON_FILE,
        path="session_map.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        secret=True,  # maps to provider-side session ids; machine-local
        help="provider session id map (machine-local)",
    ),
    StateEntry(
        id="project_dir",
        kind=KIND_JSON_FILE,
        path="project_dir",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the bound project directory pointer",
    ),
    StateEntry(
        id="workspace_dir",
        kind=KIND_JSON_FILE,
        path="workspace_dir",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the bound workspace directory pointer",
    ),
    # ── security (secrets: never exported, never synced) ──
    StateEntry(
        id="sel_hmac_key",
        kind=KIND_TREE,
        path="sel_hmac.key",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="audit-log HMAC key",
    ),
    StateEntry(
        id="telemetry_salt",
        kind=KIND_TREE,
        path="telemetry_salt",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="local hashing salt",
    ),
    StateEntry(
        id="local_secret",
        kind=KIND_TREE,
        path=".local_secret",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="gateway session-token secret",
    ),
    StateEntry(
        id="env",
        kind=KIND_TREE,
        path=".env",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="provider credentials",
    ),
    # SH-2's rollback snapshot: the pre-migration `.env`, kept only while the
    # `credentials_to_keychain` move is still reversible. Claimed here for two reasons —
    # `audit_home()` fails on any unclaimed path, and `secret=True` is what puts it in
    # `portability.EXPORT_EXCLUDE` (a projection of this set), so the one file that holds a
    # second plaintext copy of every credential cannot ride out in an export.
    StateEntry(
        id="env_pre_keychain",
        kind=KIND_TREE,
        path=".env.pre-keychain",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="pre-migration .env snapshot (rollback source for the keychain move)",
    ),
    StateEntry(
        id="credentials",
        kind=KIND_TREE,
        path="credentials",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="the credential store",
    ),
    # The gateway's OWN auth store: the argon2id login hash (`credentials.py`), the 2FA
    # enrolment (`enrollment.py`) and device pairing codes (`pairing.py`) all resolve into
    # `config_dir() / "auth"`. Declared late (#130), and the cost of the omission was not a
    # missing backup but a FALSE ALARM: `audit_home()` is wired as the `durability.inventory`
    # Doctor probe, so an undeclared real directory turned the health strip coral
    # (`worst: "durability"`, `unclaimed: ["auth/"]`) the moment the owner set a password or
    # paired a device — the ordering hazard of wiring a probe before the manifest is complete.
    #
    # `secret=True`, NOT `IGNORED`. The distinction the neighbours already draw: `machine_id`,
    # `session_key` and `sessions.json` are IGNORED because they are per-install IDENTITY, and
    # carrying them would let a restored copy masquerade as the machine it came from. This is
    # the owner's own credential store, which travels with the owner — so it takes the posture
    # stated at the top of this module for exactly that case: EXCLUDED from exports (via the
    # `secret` projection) but CAPTURED by snapshots on purpose, because a restore that
    # silently dropped the login would lock a user out of their own gateway. Declared as the
    # whole tree rather than per-file: the pair/enrol code files are short-lived and expire on
    # their own, and claiming only `credentials.json` would leave the directory unclaimed and
    # the probe coral, which is the bug.
    StateEntry(
        id="auth",
        kind=KIND_TREE,
        path="auth",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="gateway auth store: login hash, 2FA enrolment, device pairing codes",
    ),
    # 🔴 #2217 — the provider credential DESCRIPTORS (`llm/credentials.py` `CREDENTIALS_FILE`,
    # written 0600 by `CredentialStore.save`). Neither claimed nor ignored, so
    # `personalclaw snapshot` — the command the pre-1.0 release notes tell users to run BEFORE
    # upgrading — did not carry it, and a restore came back with every model provider's key
    # gone. That is the same asset #951 destroyed from the other side, a `config set` dropping
    # `providers[]` with the keys in it.
    #
    # Declared rather than left as census debt because this one needs no guess. The debt set
    # exists because a wrong `merge` silently corrupts on convergence — and `merge` cannot fire
    # for a secret at all: shards are built from `export_entries()` (which drops secrets) and
    # the merge only ever runs on rows imported FROM a shard. So `secret=True` supplies both
    # halves the issue asked for — captured by snapshot, excluded from every export — and
    # leaves nothing undecided. Same posture, and the same argument, as `auth` directly above.
    #
    # Distinct from the `credentials` TREE two entries up: that is the keychain-backed store,
    # this is the top-level `credentials.json` descriptor file, and `claim_for` is
    # longest-prefix over path SEGMENTS, so `credentials` never claimed `credentials.json`.
    StateEntry(
        id="provider_credentials",
        kind=KIND_JSON_FILE,
        path="credentials.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="provider credential descriptors (API keys)",
    ),
    StateEntry(
        id="security_events",
        kind=KIND_JSONL_APPEND,
        path="security_events.jsonl",
        domain=DOMAIN_SECURITY,
        merge=MERGE_APPEND_DEDUP,
        help="the security event log (audit trail)",
    ),
    # EXTERNAL-ACCESS §10. The client registry EXPORTS: it holds an integration's
    # label and bindings, which a user restoring a home expects back, and losing it
    # silently means every external client stops working after a restore with no
    # indication why. It carries token HASHES only, so it is not `secret=True` —
    # the tokens themselves live in the credential store, which is already excluded.
    StateEntry(
        id="inbound_clients",
        kind=KIND_JSON_FILE,
        path="inbound_clients.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_LWW,
        help="inbound access clients: labels, bindings and token hashes (never tokens)",
    ),
    # 🔴 `derived=True`, so this is DELIBERATELY excluded from exports (§10 lists it
    # among the excluded stores) while still being CLAIMED — an undeclared file under
    # the home fails `audit_home()`, so leaving it out entirely would report the
    # request trail as unmanaged drift the first time anyone calls an inbound surface.
    # It is a local request trace, trimmed at 2× cap, and its security-relevant lines
    # are mirrored into `security_events.jsonl`, which DOES export.
    StateEntry(
        id="inbound_audit",
        kind=KIND_JSONL_APPEND,
        path="inbound_audit.jsonl",
        domain=DOMAIN_SECURITY,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="per-request inbound trace (local; security events also go to the SEL)",
    ),
    # ── #2217: the stores the static census pinned as debt, each decided at its own
    # call site. Fifteen of the twenty-one were determinate — the write shape names the
    # `kind`, and an already-declared store with the SAME shape names the `merge`, so
    # none of these is a guess. The four still pinned in the census
    # (`incident.json`, `browse_kill.json`, `push_subscriptions.json`,
    # `push_relay_tokens.json`) are the ones whose call site genuinely does NOT decide:
    # each carries "a human stopped this" or "this one physical device", and whether a
    # RESTORE should re-plant that is a product decision, not a shape reading.
    StateEntry(
        id="app_messages",
        kind=KIND_JSON_ENTITY_DIR,
        path="app_messages",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="app-to-app broker queues, one JSON per target app (APE-9)",
    ),
    StateEntry(
        id="chat_plans",
        kind=KIND_JSON_ENTITY_DIR,
        path="chat_plans",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="plan-mode walkthrough sessions, one JSON per chat (CC-8)",
    ),
    # A nested `{"rows": {topic_key: {...}}}` document, NOT a list of id-keyed rows, so
    # `replace_only` rather than `lww_by_updated_at`: the per-row `updated_at` inside
    # `rows` is not the row identity the merge engine keys on, and pointing an id-keyed
    # merge at this shape is exactly the mis-declaration #2217 warns about. It is also
    # not derived — the raw signals are never stored, only this decayed fold, so the
    # file IS the authority and a lost copy cannot be rebuilt.
    StateEntry(
        id="engagement",
        kind=KIND_JSON_FILE,
        path="engagement.json",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        help="per-topic engagement weights with half-life decay",
    ),
    # A DIRECTORY of `<channel_id>.jsonl` append streams — the same shape as
    # `cron-history` above, which is why the kind is `jsonl_append` on a directory path
    # rather than a tree.
    StateEntry(
        id="channel_history",
        kind=KIND_JSONL_APPEND,
        path="history",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="observe-mode channel message buffers, one JSONL per channel",
    ),
    # `inbox/incoming/*.json` is core's drop seam for a local producer (a mail fetcher,
    # a channel bridge). `incoming/processed/` is where the provider MOVES a file it has
    # already ingested, so it is spent-fuel rather than state: declared `derived_within`
    # so the drop directory is backed up without the archive growing every file the
    # provider ever read. Distinct from the `inbox` ENTRY above, which is `inbox.json`.
    StateEntry(
        id="inbox_dropbox",
        kind=KIND_TREE,
        path="inbox",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived_within=("incoming/processed",),
        help="filesystem inbox source: JSON files dropped in inbox/incoming/",
    ),
    # One dict of polling cursors plus the user's dismissed/muted sets — a document, not
    # id-keyed rows, so `replace_only` for the same reason as `engagement.json`. The
    # dismissed/muted sets are real user decisions, which is why this is declared at all
    # rather than treated as a cursor cache.
    StateEntry(
        id="inbox_state",
        kind=KIND_JSON_FILE,
        path="inbox_state.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_REPLACE_ONLY,
        help="inbox polling cursors plus dismissed/muted items",
    ),
    StateEntry(
        id="onboarding",
        kind=KIND_TREE,
        path="onboarding",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="onboarding import ledger and staged documents",
    ),
    # 🔴 NOT `IGNORED` as export output, which is what `packs/*.pclaw` looks like at a
    # glance and would have been the wrong call: this directory also holds the INSTALLED
    # PACKS LEDGER (`packs/installed.json`) and the fingerprint rejections
    # (`packs/fingerprint_rejections.json`), both authoritative — "which packs are
    # installed" cannot be rebuilt from anything else. So the store is declared and only
    # the genuinely rebuildable parts are `derived_within`: the built `.pclaw` archives
    # (re-exportable from the live stores), the `.installing/` scratch dir, and
    # `staged/` (re-stageable from the source pack).
    StateEntry(
        id="packs",
        kind=KIND_TREE,
        path="packs",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived_within=("*.pclaw", ".installing", "staged"),
        help="installed-pack ledger, fingerprint rejections, and built pack archives",
    ),
    # A bare JSON LIST of absolute project paths — no ids, so no id-keyed merge can
    # apply. `config` + `replace_only` follows `project_dir`/`workspace_dir`, the two
    # other entries that record this machine's filesystem layout.
    StateEntry(
        id="recent_projects",
        kind=KIND_JSON_FILE,
        path="recent_projects.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="most-recently-opened project directories (capped at 10)",
    ),
    # A list of `ReportDefinition` rows each carrying `id` — the same shape as
    # `triggers.json`, hence the same `json_file` + `union_by_id`.
    StateEntry(
        id="research_reports",
        kind=KIND_JSON_FILE,
        path="research_reports.json",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="standing research report definitions, cadences and watermarks",
    ),
    StateEntry(
        id="runners",
        kind=KIND_JSON_ENTITY_DIR,
        path="runners",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="bring-your-own agent runner definitions, one JSON per runner id",
    ),
    # The LEGACY MCP store (`settings/mcp.json`). UT3 made `mcp.json` canonical and
    # folds this file in on first boot, then empties it — so on a migrated home this is
    # a husk. Declared rather than ignored for the one window where it still holds the
    # only copy: a user who upgrades and runs `personalclaw snapshot` BEFORE starting a
    # gateway has not had the migration run yet, and ignoring the path would drop their
    # MCP servers out of exactly the backup the release notes told them to take. An
    # entry for a usually-empty path is harmless; this manifest says so at the top.
    StateEntry(
        id="legacy_mcp_settings",
        kind=KIND_TREE,
        path="settings",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="legacy settings/mcp.json (folded into mcp.json on first boot)",
    ),
    StateEntry(
        id="sources",
        kind=KIND_TREE,
        path="sources",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="watched sources: saved queries, digest cursor and the event stream",
    ),
    StateEntry(
        id="surfaces",
        kind=KIND_TREE,
        path="surfaces",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="user-authored surface overlay files",
    ),
)


# Paths under the home that are deliberately NOT state: run-time scratch, logs,
# lock files, caches, and the backup output itself. Anything here is skipped by
# the claims-everything audit; anything NOT here and not claimed FAILS it, which
# is how a newly added store gets caught instead of silently dodging backup.
IGNORED: tuple[str, ...] = (
    "snapshots",  # backup output — never backed up recursively
    "outbox",  # sync staging (S3)
    # The sync root: the pull cursor's per-peer high-water marks, the outbox's delivery
    # obligations, and the conflict review queue (S3/DAS-7) — all MACHINE-LOCAL. Carrying them
    # into a snapshot would make a restored copy claim another machine's cursor position, and a
    # conflict is *this* machine's unresolved decision (both versions durably persist in the
    # shared store per §4.2, so the queue is bookkeeping, not the only copy of anything).
    # Declaring it instead would export the queue into the very shards a pull rewrites
    # mid-cycle — a self-referential synced store.
    "sync",
    "shards",  # shard export output (S2)
    # Time-travel's git repositories (§5). IGNORED, not declared: this history is
    # LOCAL-ONLY by design — one writer per mechanism, and the shard/sync layer is
    # the cross-machine story. Declaring it would put a git object database into
    # every export and every transport, and restoring it onto a second machine
    # would graft one machine's undo history onto another's live tree.
    "state-history",
    "locks",  # runtime lock files
    "__pycache__",
    "*.log",
    "*.log.*",
    "*.pid",
    "*.lock",
    "*.bak",
    "*-wal",  # sqlite sidecars: checkpointed, never copied standalone
    "*-shm",
    "*.tmp",
    ".DS_Store",
    "session_pids.txt",
    "session_pids.lock",
    "agent_pids.txt",
    "doctor",  # remediation run ledger (regenerated)
    ".git",
    # 🔴 S179 — MACHINE-LOCAL, and deliberately ignored rather than declared. A snapshot is
    # portable: it is restored onto another machine, or the same one after a wipe, and each of these
    # identifies or authenticates THIS install. Carrying them would either re-plant a credential
    # (`session_key`, `sessions.json` hold live auth material) or make two installs claim one
    # identity (`machine_id` is what `durability/shards.py` stamps shards with, so a restored copy
    # would masquerade as the machine it came from). Ignored, not `secret=True`: they must not
    # travel
    # at all, whereas a secret entry is captured on purpose so a backup can restore the credential
    # store.
    "session_key",
    "sessions.json",
    "machine_id",
    # 🔴 BA-4 — the per-site browser profiles (`browse/profiles/<site_slug>/`). IGNORED for the
    # SAME reason as `session_key`/`sessions.json` directly above, and deliberately NOT a
    # `secret=True` entry, because those two postures differ in exactly the way that matters here:
    # a secret entry is EXCLUDED from exports but CAPTURED by snapshots on purpose, so a backup can
    # restore the credential store. A Chrome `user-data-dir` holds the cookies that ARE the
    # authentication, and a snapshot is restored onto another machine — or handed to someone
    # debugging — so capturing one plants a live logged-in session on a host the user never signed
    # in from. BROWSE-AUTOMATION §5.1 says it outright: "never backed up by snapshot/portability
    # (credentials), never exported". That is this list, not the secret set. The profiles are also
    # unbounded in size and trivially re-creatable by signing in again, so nothing is lost.
    "browse",
    "update_check.json",  # last update check — regenerated on the next poll
    # RUM-2's releases-LIST cache, the direct twin of update_check.json above: the
    # ETag-cached, offline-tolerant releases view the channel/pin resolver reads,
    # refetched on the next poll. Ignored for the same reason — it carries no unique
    # truth, so a restored stale release list is worse than the empty one the next
    # check refills.
    "update_releases.json",
    # RUM-9's run-state file (`self_update._RUN_STATE_FILENAME`): the version this install
    # was running the last time a gateway started. MACHINE-LOCAL, and the one update file
    # here whose restored copy would be actively WRONG rather than merely stale. It is the
    # input `record_running_version` compares the running version against to derive
    # `updates.last_version`, so a snapshot taken on 0.2.0 and restored onto a 0.1.3 install
    # would make the next startup record `last_version = 0.2.0` and the Updates panel offer
    # "Roll back to v0.2.0" — an UPGRADE, i.e. the exact mis-offer RUM-9 exists to prevent.
    # Omitted, `record_running_version` writes nothing on that first run and makes no offer
    # until it observes a real version change, which is the honest answer.
    "update_run.json",
    # 🔴 #2906 — the per-day usage/spend fold (`routing/usage.py`, MRT-3). Ignored, not
    # declared, for the SAME reason as the two update caches directly above: it is a DERIVED
    # fold carrying no unique truth. `routing.usage.refresh` refolds it from scratch out of
    # sources that ARE declared — `usage/turns.jsonl` (`usage_ledger`), `model_calls.jsonl`
    # (`model_calls`) and `spend.json` (`spend`) — and `GET /api/usage` calls that on read, so
    # a deleted `usage_stats.json` self-heals (`handlers/usage.py`). It is written at first boot,
    # so leaving it neither claimed nor ignored made `audit_home()` report "1 unclaimed path" and
    # turned the doctor/health strip coral on every fresh install. A snapshot restoring a stale
    # copy is worse than the empty one the next read refolds.
    "usage_stats.json",
    "fixture.yaml",  # test-fixture marker written by `--seed`
    # The rendered run-prompt (`LOOP_MD_NAME`). Not state: it is re-rendered from the loop's own
    # declared inputs on every run, so a restored copy would only ever be a stale duplicate of
    # something the next run overwrites. Recorded HERE, where every other "deliberately not
    # state" decision lives, rather than only in the census's own exception list — that list
    # held a second copy of eight of these rows, and a decision kept in two places is a decision
    # that can disagree with itself. `audit_home()` reads IGNORED and could not see it at all
    # while it lived only in the test (#2217).
    "loop.md",
    # 🔴 #2539 — the socket this gateway bound, plus the pid that bound it
    # (`gateway_base.RUNTIME_FILE`). MACHINE-LOCAL and process-lifetime-scoped: it is written
    # after bind, removed on shutdown, and it is the record every child resolves its API base
    # from. Carrying it into a snapshot is the precise shape of the bug it exists to fix — a
    # restored home would hand its children a port another instance bound, which is how a tool
    # call reaches a stranger's gateway. Ignored rather than declared for the same reason as
    # `machine_id`: it must not travel at all, and it is regenerated on the next bind.
    "gateway.runtime.json",
    # 🔴 #2217 — the control bridge's discovery file (`inbound.bridge.DISCOVERY_FILENAME`).
    # The DIRECT twin of `gateway.runtime.json` directly above, and ignored on the identical
    # argument: it records the OS-assigned ephemeral port this process bound plus the actions
    # digest, `remove_discovery()` deletes it on shutdown, and `_write_discovery` rewrites it
    # on the next boot. Restoring it would point an external agent at a port nothing is
    # listening on — its own comment already says "a stale file pointing at a dead port is
    # worse than none", which is the whole decision.
    "control_bridge.json",
    # 🔴 #2217 — knowledge's maintenance cadence bookkeeping (`knowledge/maintenance.py`):
    # when each pass last ran, so the tick knows what is stale. Ignored on the same argument
    # as `doctor` above — regenerated run bookkeeping carrying no unique truth. The worst a
    # missing copy costs is one maintenance pass running sooner than it needed to; a RESTORED
    # stale copy is worse, because it claims passes ran that this home never ran.
    "graph_maintenance.json",
    # 🔴 #2217 — `session_pid_<pid>.txt`, written per ACP agent PID so an MCP tool can hand
    # the spawn API its session key (`dashboard/chat_runner.py`, globbed by `mcp_core.py`).
    # Process-lifetime and pid-keyed, exactly like `session_pids.txt` and `agent_pids.txt`
    # above, and a restored file names a pid that belongs to some other process on the target
    # machine. Found by the census only AFTER it learned to read an f-string path: spelled
    # `config_dir() / f"session_pid_{pid}.txt"`, it had been counted as the unresolved
    # identifier `f` and therefore never checked, so `audit_home()` reported one unclaimed
    # path per ACP agent on every real home that had run a chat — the live Doctor probe going
    # coral for a file nobody had decided about.
    #
    # The glob is `session_pid_*` and not `session_pid_*.txt` on purpose: it has to match both
    # the real on-disk name (`session_pid_4711.txt`) AND the static prefix the census resolves
    # an f-string path to (`session_pid_`). A `.txt` suffix would cover the first and silently
    # miss the second, which is the shape of gap this row exists to close.
    "session_pid_*",
    # 🔴 #2217 — the autonudge stop sentinel (`dashboard/handlers/autonudge.py`). Normally it
    # lands in the session's own working directory, but when there is no cwd and no workspace
    # root it falls back to `config_dir()`, so a home CAN accumulate `.stop-<session>` markers.
    # A sentinel whose whole meaning is "a stop was requested for this running loop" is
    # lifetime-scoped scratch in the same family as `locks` and `*.lock` above, and a restored
    # one would ask a fresh machine to stop a loop that does not exist on it. The SECOND
    # location the census surfaced only after it learned to follow a home bound to a local name
    # (`base = config_dir()` … `base / f".stop-{key}"`) — it had been invisible to every
    # spelling the scan knew.
    ".stop-*",
)


# ── projections (the point: everything derives from the manifest) ───────────


def all_entries() -> tuple[StateEntry, ...]:
    return INVENTORY


def by_id(entry_id: str) -> StateEntry | None:
    return next((e for e in INVENTORY if e.id == entry_id), None)


def domains() -> tuple[str, ...]:
    """The domains actually present in the manifest, in declaration order."""
    seen: list[str] = []
    for entry in INVENTORY:
        if entry.domain not in seen:
            seen.append(entry.domain)
    return tuple(seen)


def entries_for_domain(domain: str) -> tuple[StateEntry, ...]:
    return tuple(e for e in INVENTORY if e.domain == domain)


def backup_entries(*, include_derived: bool = False) -> tuple[StateEntry, ...]:
    """Entries a SNAPSHOT should capture. Secrets are included (a snapshot is a
    local, 0600 archive — losing the credential store is exactly what a backup
    should prevent); derived indexes are skipped unless asked for, since they
    rebuild and a stale index paired with a newer store is worse than none."""
    return tuple(e for e in INVENTORY if include_derived or not e.derived)


def export_entries() -> tuple[StateEntry, ...]:
    """Entries a PORTABLE EXPORT may contain — the projection that replaces
    `portability.EXPORT_EXCLUDE`: neither secrets (they must never leave the
    machine) nor derived data (rebuildable)."""
    return tuple(e for e in INVENTORY if not e.secret and not e.derived)


def secret_paths() -> tuple[str, ...]:
    return tuple(e.path for e in INVENTORY if e.secret)


def sqlite_entries() -> tuple[StateEntry, ...]:
    """Every database in the manifest. Callers MUST copy these with the sqlite
    backup API rather than a filesystem copy: the gateway holds them open in WAL
    mode, so a raw copy can capture a torn page set. This projection is what
    fixed the live `knowledge.db` raw-copy hazard — it was outside the old
    hand-written allowlist and got tree-copied."""
    return tuple(e for e in INVENTORY if e.kind == KIND_SQLITE)


def _parts(rel: str) -> tuple[str, ...]:
    """Home-relative path split into its meaningful segments."""
    return tuple(p for p in rel.replace("\\", "/").split("/") if p and p != ".")


def is_ignored(rel: str) -> bool:
    """Whether a home-relative path is deliberately not state."""
    parts = _parts(rel)
    for pattern in IGNORED:
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def claim_for(rel: str) -> StateEntry | None:
    """The entry claiming a home-relative path, or None.

    Longest path match wins, so a nested store claims its own subtree even when
    an ancestor entry also exists (``workspace/knowledge/knowledge.db`` belongs
    to ``knowledge_db``, not to ``workspace``).
    """
    parts = _parts(rel)
    best: StateEntry | None = None
    best_depth = -1
    for entry in INVENTORY:
        ep = _parts(entry.path)
        if len(ep) <= len(parts) and tuple(parts[: len(ep)]) == ep and len(ep) > best_depth:
            best, best_depth = entry, len(ep)
    return best


def claims_within(rel: str) -> bool:
    """Does any entry declare a path INSIDE the directory *rel*?

    A directory whose CONTENTS are declared is accounted for by them: ``knowledge/`` holds only
    ``knowledge/knowledge.db``, and :func:`claim_for` is longest-prefix, so it names the child
    without naming the parent. Reporting the parent as unclaimed would demand a redundant
    wrapper entry for every nested store.
    """
    prefix = "/".join(_parts(rel)) + "/"
    return any(e.path.startswith(prefix) for e in INVENTORY)


def is_accounted(rel: str) -> bool:
    """Is this home-relative path claimed by an entry, or deliberately ignored?

    🔴 THE ONE QUESTION, ASKED ONCE. The inventory accounts for a path two ways — an entry
    CLAIMS it, or :data:`IGNORED` deliberately excludes it — and :func:`audit_home` has always
    honoured both. The static census over the source tree
    (``tests/test_durability_inventory_census.py``) re-derived a NARROWER version that read only
    the claims, plus its own hand-written copy of eight of IGNORED's rows.

    Two consequences, both measured (#2217). Five locations resolved the *correct* way for
    machine-local state — ``session_key``, ``sessions.json``, ``update_check.json``,
    ``update_releases.json``, ``doctor``, each ignored with a written argument — were still
    reported as undeclared debt, inflating the gap the issue reports. And because the census's
    stale-pin ratchet could only notice a pin that became *declared*, the debt set could shrink
    by declaring and never by ignoring, so those five could never be retired.

    Exported so the census asks the inventory rather than modelling it: a second spelling of
    "accounted for" is the same class of bug as the duplicated allowlist it replaces.
    """
    return is_ignored(rel) or claim_for(rel) is not None or claims_within(rel)


@dataclass
class AuditResult:
    """What :func:`audit_home` found."""

    unclaimed: list[str] = field(default_factory=list)
    claimed: int = 0
    ignored: int = 0
    # Databases found on disk that no `sqlite` entry declares. These are the
    # dangerous kind of gap: an undeclared DB inside a `tree` entry gets
    # filesystem-copied while the gateway holds it open in WAL mode.
    undeclared_dbs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unclaimed and not self.undeclared_dbs


def audit_home(home: Path) -> AuditResult:
    """Assert every top-level path under ``home`` is claimed or ignored.

    This is the guard that keeps the manifest honest: when a new store directory
    appears and nobody declared it, this reports it and the test that calls it
    fails — which is precisely how nine directories silently escaped backup
    before the inventory existed.

    Scans the top level plus one level inside each unclaimed directory, which is
    enough to name the offending store without walking a huge tree.
    """
    result = AuditResult()
    if not home.is_dir():
        return result
    for child in sorted(home.iterdir()):
        rel = child.name
        if is_ignored(rel):
            result.ignored += 1
            continue
        if claim_for(rel) is not None:
            result.claimed += 1
            continue
        # A directory whose CONTENTS are declared is claimed by them — see `claims_within`, which
        # the static census now shares rather than re-deriving (#2217).
        if child.is_dir() and claims_within(rel):
            result.claimed += 1
            continue
        result.unclaimed.append(rel + ("/" if child.is_dir() else ""))

    # Every *.db on disk must be declared as a sqlite entry, wherever it lives.
    # A database nested inside a `tree` entry is the exact hazard this plan
    # exists to close: it gets filesystem-copied while open in WAL mode.
    declared = {e.path for e in sqlite_entries()}
    # A store can be a DIRECTORY of databases rather than one file — `codegraph/<workspace-key>.db`,
    # of which a real home held 5478, so an exact-path compare can never match them and the check
    # drowns in thousands of rows (the same over-reporting failure S178 fixed in the coverage
    # ratchet).
    #
    # 🔴 But my first version exempted every `tree`/`derived` prefix, which BLINDED the check to the
    # exact hazard it exists for: driven, a surprise DB inside the `loop` and `workspace` trees was
    # no longer reported. A DB nested in a tree entry is the dangerous case — it gets
    # filesystem-copied while open in WAL mode. So the exemption is opt-in per entry
    # (`db_container=True`), naming the stores whose whole content IS databases.
    declared_trees = tuple(e.path + "/" for e in INVENTORY if e.db_container)
    for db in sorted(home.rglob("*.db")):
        rel_db = db.relative_to(home).as_posix()
        if is_ignored(rel_db) or rel_db in declared:
            continue
        if rel_db.startswith(declared_trees):
            continue
        result.undeclared_dbs.append(rel_db)
    return result
