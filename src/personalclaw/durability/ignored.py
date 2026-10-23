"""The paths under the home that are deliberately NOT state, for ``durability.inventory``'s audit.

Run-time scratch, logs, lock files, caches, per-install identity and the backup output itself. The
claims-everything audit (``inventory.audit_home``) skips each of these; a path that is neither here
nor claimed by an inventory entry FAILS it. Moved out of ``inventory.py`` whole (#3905).
"""

from __future__ import annotations

# Paths under the home that are deliberately NOT state: run-time scratch, logs,
# lock files, caches, and the backup output itself. Anything here is skipped by
# the claims-everything audit; anything NOT here and not claimed FAILS it, which
# is how a newly added store gets caught instead of silently dodging backup.
IGNORED: tuple[str, ...] = (
    "snapshots",  # backup output — never backed up recursively
    "outbox",  # sync staging
    # The sync root: the pull cursor's per-peer high-water marks, the outbox's delivery
    # obligations, and the conflict review queue — all MACHINE-LOCAL. Carrying them
    # into a snapshot would make a restored copy claim another machine's cursor position, and a
    # conflict is *this* machine's unresolved decision (both versions durably persist in the
    # shared store, so the queue is bookkeeping, not the only copy of anything).
    # Declaring it instead would export the queue into the very shards a pull rewrites
    # mid-cycle — a self-referential synced store.
    "sync",
    "shards",  # shard export output
    # Time-travel's git repositories. IGNORED, not declared: this history is
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
    # 🔴 MACHINE-LOCAL, and deliberately ignored rather than declared. A snapshot is
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
    # The id this home's namespace in the OS keychain is named from
    # (`config.credentials.keychain_service`). MACHINE-LOCAL identity for the same reason as
    # `machine_id`: it names which of this machine's keychain items are this home's, so a copy
    # planted by a restore or an import would make two homes one namespace, each reading,
    # overwriting and deleting the other's secrets. A home restored or made from an archive names
    # its own on its first keychain write.
    "keychain_namespace",
    # 🔴 The per-site browser profiles (`browse/profiles/<site_slug>/`). IGNORED for the
    # SAME reason as `session_key`/`sessions.json` directly above, and deliberately NOT a
    # `secret=True` entry, because those two postures differ in exactly the way that matters here:
    # a secret entry is EXCLUDED from exports but CAPTURED by snapshots on purpose, so a backup can
    # restore the credential store. A Chrome `user-data-dir` holds the cookies that ARE the
    # authentication, and a snapshot is restored onto another machine — or handed to someone
    # debugging — so capturing one plants a live logged-in session on a host the user never signed
    # in from. The rule is explicit: "never backed up by snapshot/portability
    # (credentials), never exported". That is this list, not the secret set. The profiles are also
    # unbounded in size and trivially re-creatable by signing in again, so nothing is lost.
    "browse",
    # The installed apps' Python packages (`apps/app_python.py`): a pip `--prefix` rebuilt from
    # the apps' own manifests. IGNORED, not declared, because it is a function of two things a
    # restore does not carry over — the manifests (restored with `apps/`) and the INTERPRETER
    # (its layout is keyed by the Python version, and its wheels are built for one platform). A
    # restored copy would be at best redundant and at worst extension modules compiled for
    # another machine's Python; the boot-time repair (`app_manager.repair_app_packages`)
    # reinstalls from the manifests instead. It is also unbounded in size (PyTorch alone is ~1
    # GB), and a dependency may ship a `.db` file that is data, not a store this gateway holds
    # open — which the undeclared-database audit would otherwise report.
    "app-python",
    # What the installers PersonalClaw runs keep (`_installer.installer_cache_env`): npm's cache,
    # which npm cannot run without, and the temp folder pip, uv and npm run with. Kept in the home
    # so none of it lands in the user's own `~/.npm` or temp folder, and ignored because it is a
    # cache: a missing one is refilled on the next run, and restoring one is pointless. A specific
    # name, not `cache`, because an ignored name is ignored at every depth and would hide a
    # database in any folder called `cache`.
    "installer-cache",
    # The home's own tmux server socket (`tmux_substrate.server_flags`), which the home's
    # persistent terminals and durable workers run in. MACHINE-LOCAL and process-scoped: a
    # snapshot cannot carry a socket, and a restored home starts its own server on first use.
    "tmux.sock",
    # The home's claim (`gateway_base.LOCK_FILE`): the lock its gateway holds for as long as it
    # runs, so no second gateway starts on the home. MACHINE-LOCAL and process-scoped: what a
    # lock means is the process holding it, which no copy carries, so a restored copy means
    # nothing. An empty file, never moved or deleted while a gateway may hold it.
    "gateway.lock",
    # The cache an earlier release's update check kept of GitHub's "Latest" release. Nothing
    # writes or reads it now (the check resolves over the releases list below), but a home that
    # ran such a release still holds one, and it carries nothing a restore needs.
    "update_check.json",
    # The releases-LIST cache: the ETag-cached, offline-tolerant releases view the update check
    # and every apply resolve against, refetched on the next poll. Ignored because it carries no
    # unique truth, so a restored stale release list is worse than the empty one the next check
    # refills.
    "update_releases.json",
    # The run-state file (`self_update._RUN_STATE_FILENAME`): the version this install
    # was running the last time a gateway started. MACHINE-LOCAL, and the one update file
    # here whose restored copy would be actively WRONG rather than merely stale. It is the
    # input `record_running_version` compares the running version against to derive
    # `updates.last_version`, so a snapshot taken on 0.2.0 and restored onto a 0.1.3 install
    # would make the next startup record `last_version = 0.2.0` and the Updates panel offer
    # "Roll back to v0.2.0" — an UPGRADE, i.e. the exact mis-offer rollback must avoid.
    # Omitted, `record_running_version` writes nothing on that first run and makes no offer
    # until it observes a real version change, which is the honest answer.
    "update_run.json",
    # 🔴 #2906 — the per-day usage/spend fold (`routing/usage.py`). Ignored, not
    # declared, for the SAME reason as the two update caches directly above: it is a DERIVED
    # fold carrying no unique truth. `routing.usage.refresh` refolds it from scratch out of
    # sources that ARE declared — `usage/turns.jsonl` (`usage_ledger`), `model_calls.jsonl`
    # (`model_calls`) and `spend.json` (`spend`) — and `GET /api/usage` calls that on read, so
    # a deleted `usage_stats.json` self-heals (`handlers/usage.py`). It is written at first boot,
    # so leaving it neither claimed nor ignored made `audit_home()` report "1 unclaimed path" and
    # turned the doctor/health strip coral on every fresh install. A snapshot restoring a stale
    # copy is worse than the empty one the next read refolds.
    "usage_stats.json",
    # The tool-description vectors tool retrieval ranks with (`agents.native.tool_vectors`). A
    # cache: every vector is recomputed from the tool catalog the gateway loads, a missing file is
    # refilled in the background, and a restored one would be another machine's catalog embedded
    # with what may be another model. It is kept across restarts so a restart embeds nothing that
    # has not changed.
    "tool_embeddings.json",
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
    # When knowledge consolidation's unattended dry run last ran
    # (`action_providers/knowledge_maintain_provider.py`), so the maintenance tick does not
    # re-plan the whole library on every write. Ignored on the same argument as
    # `graph_maintenance.json` directly above: run bookkeeping with no unique truth. A missing
    # copy costs one extra plan on the next tick (its reader treats a missing stamp as never
    # run); a restored copy claims a dry run this home never ran and holds the tick back.
    "knowledge_consolidation.json",
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
    # 🔴 #3473 — the per-store disk-footprint series (`durability/footprint.py`). It is a
    # MEASUREMENT of this machine's filesystem, not user state: `measure()` re-reads bytes-on-disk
    # for every declared store off this same manifest, and the maintenance tick records a fresh
    # sample on its own cadence. The only content a restore could not reproduce is the TREND, and
    # that is exactly what must not travel — a snapshot is restored onto another machine or the same
    # one after a wipe, where those byte counts describe a filesystem that no longer exists, so
    # `growth()` would read the ends of a window straddling the restore and report a rate that never
    # happened. The module's own rule is that a single sample "deliberately reports NO rate rather
    # than a fabricated zero"; a foreign series fabricates something worse than a zero, because it
    # looks like real history. Ignored rather than `derived=True` on an entry for the same reason as
    # `usage_stats.json` above: a derived entry is still CLAIMED, and there is nothing here worth
    # claiming — the next tick writes the only sample that is true of this machine.
    "footprint.json",
    # 🔴 #3473 — the trigger claim store (`triggers/claims.py`). Pure RUNTIME COORDINATION, and the
    # one row in this list where restoring the path would be actively harmful rather than merely
    # stale. A claim answers "which trigger is running RIGHT NOW" and names `owner_pid`, the process
    # that granted it and is doing the work. Restored onto any machine, every pid in it is dead or
    # belongs to an unrelated process — and because expiry is read-time, not swept, the claim reads
    # as LIVE until `max_duration_secs` elapses. For that window `overlap: skip` suppresses fires
    # that should happen and `is_running` asserts a run that does not exist, which is precisely the
    # single-flight failure `claims.py` was written to fix, reintroduced by a backup. `reaper
    # .terminalize_orphans` consumes `orphaned_ids` at boot to clear exactly these, so a restore
    # that re-plants them manufactures work for the reaper in the best case. Nothing is lost:
    # high-churn sidecar state whose whole meaning is "a process on THIS machine holds this
    # trigger", the same posture as `locks` and `*.lock` above.
    "trigger-claims",
    # The regenerate retake records (`artifacts/retakes.py`): which images the answer being
    # regenerated RIGHT NOW made, so the replayed turn saves its image as their next version. One
    # turn's scratch, closed when the turn ends and named for the gateway process that opened it;
    # restored anywhere it names a dead process and is discarded unread. Nothing is lost.
    "retakes",
    # The task lease sidecars (`workflows/pool.py`): the claim-store argument above, for tasks.
    # A lease names the worker renewing it once a minute, and a restored one would show a task as
    # claimed by a worker that does not exist until it expired. The module's own contract is that
    # a sidecar "can be deleted to force-release without touching user data".
    "task_leases",
    # The trigger dispatch spool and its retry hold (`triggers/dispatch.py`): fires parked for
    # seconds until a loop drains them. `dispatch.py` already recorded this decision in prose —
    # "high-churn runtime bookkeeping that is meaningless once restored" — and a restored spool
    # would re-deliver fires the pre-restore home already delivered. The row makes the audit
    # honour the decision the code made.
    "trigger-spool.jsonl",
    "trigger-spool-hold.json",
    # The restart review (`triggers/review.py`): the runs THIS home missed or had interrupted,
    # waiting for a decision. Restored anywhere else it would offer to run, late, slots the
    # pre-restore home already decided about — the storm the "review, don't auto-run" exists to
    # prevent, arriving by backup. Nothing is lost: the history rows record what happened.
    "trigger-review.json",
    # A trigger's open question (`triggers/parks.py`): its action stopped for a person on THIS
    # machine — browse at a sign-in page, in a browser profile `browse` above keeps out of every
    # snapshot. Restored elsewhere, its Approve would run the action against a profile that was not
    # carried, which stops at the same page and asks again. Nothing is lost without it: the history
    # row says the run waited, and the trigger's next run that stops asks afresh.
    "trigger_parks",
    # The poll cursors of the file, web and view triggers (`file_poll`, `web_poll`,
    # `pull_on_view`). Each module treats a MISSING state as a quiet re-seed — a watch's first
    # look records what it sees and fires nothing, a view binding refreshes on its next render —
    # so a restore without them costs nothing, while a restored stale file or web cursor re-fires
    # every change the pre-restore home already acted on. That is what separates them from
    # `trigger-idle/`, which IS declared: its `cycle_count` enforces a `max_cycles` budget, and a
    # re-seed there would hand every bounded loop a fresh one.
    "trigger-watch",
    "trigger-web-watch",
    "trigger-view",
    # A legacy automation file is renamed `<name>.imported-<date>` once its rows are in
    # `triggers.json` (`triggers/legacy_import.py`: `crons.json`, `event_triggers.json`,
    # `autonudge.json`); `<name>.migrated` is the name an earlier build gave the same copy. What
    # remains is a pre-import copy of state that now lives elsewhere — the same category as `*.bak`.
    "*.imported-*",
    "*.migrated",
    # A memory database SQLite reported damaged, moved aside whole so a new one could start in its
    # place (`memory.MemoryStore`): `memory_index.db.broken-<UTC instant>` beside where it was, and
    # the files SQLite kept beside it. Never deleted, and never opened or written to again by
    # PersonalClaw, so it is not live state any store keeps: the home's own copy is a damaged
    # search index, rebuilt from the memory files, and a working folder's copy holds memories no
    # store has any more. The Doctor's memory check names each one until the owner removes it,
    # which is where a set-aside copy is reported, rather than here as a path nothing claims.
    # The same mark is on the copy kept of a store file that could not be read
    # (`record_files.SET_ASIDE_MARK`): `triggers.json.broken-<UTC instant>` and its kind, beside
    # the file, which stays where it is and is never written over until it can be read. A copy of
    # what the file held then, not what any store keeps now; the Doctor names it
    # (`automations.store`, `durability.store_files`).
    "*.broken-*",
    # The record that this home already settled what an earlier release left OUTSIDE it on this
    # machine (`outside_home.settle_previous_locations`: skills copied home from ~/.agents/skills,
    # a saved pointer to the old default workspace dropped). It describes THIS machine's leftovers,
    # so it must not travel: a restore onto another machine settles that machine's own, once.
    ".outside-home-settled.json",
    # The incremental shard export's change cursor (`durability/shards.dirty_entries`).
    # MACHINE-LOCAL like `sync` and `shards` above: a cursor restored from another export would make
    # the next incremental export skip exactly what changed since.
    ".shard-state.json",
    # A replace-restore's copy of what it replaced (`snapshot.py`, `pre-restore-<timestamp>/`), kept
    # beside the home so the restore can be undone. Backing it up would nest the previous home
    # inside every later snapshot, and a snapshot is itself the way back.
    "pre-restore-*",
)
