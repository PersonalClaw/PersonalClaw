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
    # NOTE (future, not now): §1.1 excludes `studies/*/locked` (hidden validation
    # answer keys) from the PORTABILITY export. That subtree does not exist in ES-1a —
    # matrices carry no answer keys — so no `derived_within`/export-exclusion is wired
    # here yet; ES-5 adds it when `locked/` is first written (no dead control before then).
    StateEntry(
        id="evals",
        kind=KIND_TREE,
        path="evals",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="offline eval substrate: matrices, results ledger (studies/benchmarks later)",
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
        id="model_calls",
        kind=KIND_JSONL_APPEND,
        path="model_calls.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="one line per model-call attempt",
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
    StateEntry(
        id="credentials",
        kind=KIND_TREE,
        path="credentials",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="the credential store",
    ),
    StateEntry(
        id="security_events",
        kind=KIND_JSONL_APPEND,
        path="security_events.jsonl",
        domain=DOMAIN_SECURITY,
        merge=MERGE_APPEND_DEDUP,
        help="the security event log (audit trail)",
    ),
)


# Paths under the home that are deliberately NOT state: run-time scratch, logs,
# lock files, caches, and the backup output itself. Anything here is skipped by
# the claims-everything audit; anything NOT here and not claimed FAILS it, which
# is how a newly added store gets caught instead of silently dodging backup.
IGNORED: tuple[str, ...] = (
    "snapshots",  # backup output — never backed up recursively
    "outbox",  # sync staging (S3)
    "shards",  # shard export output (S2)
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
    "update_check.json",  # last update check — regenerated on the next poll
    "fixture.yaml",  # test-fixture marker written by `--seed`
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
        # A directory whose CONTENTS are declared is claimed by them. `knowledge/` holds only
        # `knowledge/knowledge.db`, and `claim_for` is longest-prefix, so it can name the child
        # without naming the parent — reporting the parent as unclaimed would demand a redundant
        # wrapper entry for every nested store.
        if child.is_dir() and any(e.path.startswith(rel + "/") for e in INVENTORY):
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
