# PLATFORM-REACH — atomic plans

**Source plan:** [`PLATFORM-REACH`](../plans/PLATFORM-REACH.md)  
**Code:** `PR`  
**Source status:** proposed

PLATFORM-REACH is DESIGNED with nothing shipped; decomposed into 8 todo atoms across the two owner-declared independent tracks (ARM: PR-1..PR-5; Windows: PR-6..PR-8).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PR-1` | ⬜ | sqlite_compat.py C1 helper: sqlite_features()/require_fts5() + refactor 7 import sites + doctor SQLite line | — | src/personalclaw/sqlite_compat.py created (driver/version/fts5/json1 probe, memoized); the try/except pysqlite3 imports in snapshot.py, knowledge/retrieval.py, memory.py, portability.py, vector_memory.py + bare import in loop/store.py all import from it; cli_doctor.py renders 'SQLite: <driver> <version>, FTS5 <✅\|❌>, JSON1 <✅\|❌>'; unit tests fake both drivers |
| `PR-2` | ⬜ | Fail-actionable FTS5 guards at the 6 FTS5 consumers' store-init (require_fts5 / documented degrade) | `PR-1` | each of the 6 FTS5 modules checks sqlite_features().fts5 once at init and raises/degrades with the fixed remedy text; a monkeypatched no-FTS5 probe fixture produces the actionable error/degradation at init, never a mid-query traceback; per-module degrade-vs-raise choice recorded in the Execution log |
| `PR-3` | ✅ | CI: add ubuntu-24.04-arm + macos-14 arm64 jobs to full.yml (non-PR path) | `PR-1`, `PR-2`, `EXT:CI-RELEASE-ENGINEERING:full.yml job matrix exists to extend` | both arm jobs added to full.yml and green; arm-specific failures root-caused or xfail-annotated with issues and logged |
| `PR-4` | ✅ | Make multi-arch images release-blocking: per-arch smoke gate (personalclaw --version) in release.yml | `EXT:CI-RELEASE-ENGINEERING:release.yml images job to gate`, `EXT:DISTRIBUTION:published multi-arch images` | release.yml gate requires both amd64+arm64 to build AND smoke 'personalclaw --version' in each; a failed arm64 build/smoke fails the release (multi-arch BUILD already present in release.yml images job; this adds the blocking smoke gate) |
| `PR-5` | ⬜ | docs/guides/platforms.md + README support matrix (C2): [models] arm64 wheel/dep audit, Pi-class note, proof-token matrix | `PR-1` | docs/guides/platforms.md created with [models]-extra per-arch reality (faiss-cpu/torch/sentence-transformers, verified by recorded install attempts) + Pi-class RAM floor/skip-extras/swap note; README + platforms.md carry the support matrix where every row names its proof token (CI:<job> \| checklist:<section> \| community), no unproven 'supported' |
| `PR-6` | ⬜ | Windows rung 1: Docker-Desktop guide section + per-release Windows checklist in release-runbook | `EXT:DISTRIBUTION:published Windows-runnable images` | docs/guides/platforms.md Windows-via-Docker-Desktop section (WSL2 backend, volume semantics, localhost ports, .env Windows paths); per-release Windows checklist (compose up, dashboard, one chat, snapshot) merged into docs/maintainers/release-runbook.md (owner task 2 validates verbatim) |
| `PR-7` | ⬜ | Windows rung 2 (WSL2): browser auto-open fallback + doctor WSL awareness + WSL2 guide | — | auto-open site (near cli_server.py --no-open handling) prints URL prominently + tries wslview when launch fails or /proc/version contains microsoft, normal Linux unchanged (WSL fixture); cli_doctor.py detects WSL and notes service/systemd status; docs/guides/platforms.md WSL2 guide (uv install, systemd wsl.conf note, localhost forwarding, ext4-not-/mnt/c perf) |
| `PR-8` | ✅ (#944) | Windows rung 3: per-mechanism native-Windows audit doc (audit ONLY, no code) | — | docs/roadmap/research/windows-native-audit.md covers each verified mechanism (Job Objects vs PPID reaping, icacls vs chmod, junction/copy vs symlink, ConPTY/pywinpty vs disable-terminal, Windows Service vs Task Scheduler, sandbox-degradation policy) with options+effort+risk and an explicit go/no-go + demand-evidence criteria; NO implementation code (soul guardrail — E6 if tempted) |

## Atom scopes

### `PR-1` — sqlite_compat.py C1 helper: sqlite_features()/require_fts5() + refactor 7 import sites + doctor SQLite line

**Status:** todo

Contracts C1; Task A1.1; Integration points Refactors(class R)

**Done when:** src/personalclaw/sqlite_compat.py created (driver/version/fts5/json1 probe, memoized); the try/except pysqlite3 imports in snapshot.py, knowledge/retrieval.py, memory.py, portability.py, vector_memory.py + bare import in loop/store.py all import from it; cli_doctor.py renders 'SQLite: <driver> <version>, FTS5 <✅|❌>, JSON1 <✅|❌>'; unit tests fake both drivers

### `PR-2` — Fail-actionable FTS5 guards at the 6 FTS5 consumers' store-init (require_fts5 / documented degrade)

**Status:** todo

Contracts C1 (require_fts5, remedy text, §2.7 fail-closed-for-capability); Task A1.2

**Done when:** each of the 6 FTS5 modules checks sqlite_features().fts5 once at init and raises/degrades with the fixed remedy text; a monkeypatched no-FTS5 probe fixture produces the actionable error/degradation at init, never a mid-query traceback; per-module degrade-vs-raise choice recorded in the Execution log

### `PR-3` — CI: add ubuntu-24.04-arm + macos-14 arm64 jobs to full.yml (non-PR path)

**Status:** done

Task A1.3; Integration points CI; Design Track A

**Done when:** both arm jobs added to full.yml and green; arm-specific failures root-caused or xfail-annotated with issues and logged

### `PR-4` — Make multi-arch images release-blocking: per-arch smoke gate (personalclaw --version) in release.yml

**Status:** done

Task A2.1; Design Track A

**Done when:** release.yml gate requires both amd64+arm64 to build AND smoke 'personalclaw --version' in each; a failed arm64 build/smoke fails the release (multi-arch BUILD already present in release.yml images job; this adds the blocking smoke gate)

### `PR-5` — docs/guides/platforms.md + README support matrix (C2): [models] arm64 wheel/dep audit, Pi-class note, proof-token matrix

**Status:** todo

Contracts C2; Tasks A2.2, A2.3, A2.4

**Done when:** docs/guides/platforms.md created with [models]-extra per-arch reality (faiss-cpu/torch/sentence-transformers, verified by recorded install attempts) + Pi-class RAM floor/skip-extras/swap note; README + platforms.md carry the support matrix where every row names its proof token (CI:<job> | checklist:<section> | community), no unproven 'supported'

### `PR-6` — Windows rung 1: Docker-Desktop guide section + per-release Windows checklist in release-runbook

**Status:** todo

Track B Session B1; Tasks B1.1, B1.2

**Done when:** docs/guides/platforms.md Windows-via-Docker-Desktop section (WSL2 backend, volume semantics, localhost ports, .env Windows paths); per-release Windows checklist (compose up, dashboard, one chat, snapshot) merged into docs/maintainers/release-runbook.md (owner task 2 validates verbatim)

### `PR-7` — Windows rung 2 (WSL2): browser auto-open fallback + doctor WSL awareness + WSL2 guide

**Status:** todo

Track B Session B2; Tasks B2.1, B2.2, B2.3

**Done when:** auto-open site (near cli_server.py --no-open handling) prints URL prominently + tries wslview when launch fails or /proc/version contains microsoft, normal Linux unchanged (WSL fixture); cli_doctor.py detects WSL and notes service/systemd status; docs/guides/platforms.md WSL2 guide (uv install, systemd wsl.conf note, localhost forwarding, ext4-not-/mnt/c perf)

### `PR-8` — Windows rung 3: per-mechanism native-Windows audit doc (audit ONLY, no code)

**Status:** done (#944)

Track B Session B3; Task B3.1

**Done when:** docs/roadmap/research/windows-native-audit.md covers each verified mechanism (Job Objects vs PPID reaping, icacls vs chmod, junction/copy vs symlink, ConPTY/pywinpty vs disable-terminal, Windows Service vs Task Scheduler, sandbox-degradation policy) with options+effort+risk and an explicit go/no-go + demand-evidence criteria; NO implementation code (soul guardrail — E6 if tempted)

