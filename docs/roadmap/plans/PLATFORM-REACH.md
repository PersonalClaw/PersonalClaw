# Plan: Platform Reach — Reliable ARM + the Windows Ladder

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: separate, independently prioritizable platform tracks)
**Created:** 2026-07-18
**Wave:** 1 (Track A: ARM) + 2 (Track B: Windows rungs 1-2 + the rung-3 audit). Native Windows implementation deliberately unscheduled until the audit + demand evidence justify the permanent tax.
**Depends on:** CI-RELEASE-ENGINEERING (matrix + multi-arch pipeline), DISTRIBUTION (wheels/images as delivery). MOBILE-COMPANION and DESKTOP-CAPABILITIES are separate plans.
**Scope:** ARM Linux + Apple Silicon become CI-proven first-class; Windows gets honest, tested paths. **Soul guardrail:** a platform is "supported" only when CI (or a per-release executed checklist) proves it — no support-matrix rows backed by hope. Anything not proven is labeled community-tested, explicitly.

---

## Context (code recon, 2026-07-18)

- **SQLite fallback is real and load-bearing:** the `try: import pysqlite3 except: import sqlite3` pattern appears in `snapshot.py`, `knowledge/retrieval.py`, `memory.py`, `portability.py`, `vector_memory.py` (+ plain `sqlite3` in `loop/store.py`); **FTS5 is used by six modules** (`memory.py`, `vector_memory.py`, `knowledge/{retrieval,store}.py`, `dashboard/handlers/knowledge.py`, `memory_providers/filesystem.py`). `pysqlite3-binary` is pinned linux-x86_64-only — so every ARM Linux install already runs the stdlib path today, unverified. The ARM question is precisely: *does the platform's bundled SQLite carry FTS5 (+ JSON1), and do we detect it when it doesn't?*
- **Windows-blocking mechanisms, verified locations:** PPID-1 orphan reaping (`apps/backend_runtime.py`), Unix-only process calls concentrated in `sandbox.py` (fork/setsid/SIGHUP family), `0600` chmods (`.env`, `sel_hmac.key` via `config/loader.py::save_credential`, `sel.py`), the `static/dist` symlink (`frontend.py` — though wheels install a real dir, shrinking this to dev-only), PTY confined to `dashboard/handlers/terminal.py`, `service/` = `linux.py` + `macos.py` only.
- Desktop bundling excludes torch-class deps by design (`personalclaw-backend.spec`); ARM wheel audit therefore matters mostly for `[models]` server installs.

## Design

- **Track A (ARM):** CI proves it (ubuntu-arm + macos-14 arm64 runners); a **SQLite capability probe** becomes first-class: one helper (`sqlite_features()` → {driver, version, fts5, json1}) used by `doctor` (reported line) and by the six FTS5 consumers' init paths (fail with an actionable message when FTS5 is absent, instead of mid-query errors); multi-arch images become release-blocking; wheel audit documents `[models]`-extra degradations per arch; support matrix lands in README + docs, CI-backed.
- **Track B (Windows):** rung 1 = Docker Desktop (published images + a per-release checklist); rung 2 = WSL2 (docs + two small fixes: browser auto-open fallback, systemd note); rung 3 = a costed **audit only** against the verified mechanism list, producing a go/no-go with per-mechanism options (Job Objects vs PPID reaping; icacls vs chmod; junction/copy vs symlink; ConPTY via pywinpty vs disabling the terminal page; Windows Service vs Task Scheduler; sandbox degradation policy = the hard one, likely "no native sandbox on Windows, documented loudly").

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — `src/personalclaw/sqlite_compat.py` (new — absorbs the repeated try/except import)

```python
# The ONE place the driver is chosen. The 5 modules (snapshot, knowledge/retrieval,
# memory, portability, vector_memory) + loop/store import `sqlite3` FROM HERE.
import sqlite3   # re-exported; may be pysqlite3 aliased

@dataclass(frozen=True)
class SqliteFeatures:
    driver: str        # "pysqlite3" | "sqlite3"
    version: str       # sqlite library version, e.g. "3.45.1"
    fts5: bool
    json1: bool

def sqlite_features() -> SqliteFeatures: ...   # probes once, memoized
def require_fts5() -> None: ...                # raises RuntimeError with the remedy text if absent
```

- **Remedy text (fixed string, reused):** `"This feature needs SQLite with FTS5. Your runtime's SQLite (<driver> <version>) lacks it. See docs/guides/platforms.md#sqlite."` — used by `require_fts5()` and the doctor line.
- **Doctor line format:** `SQLite: <driver> <version>, FTS5 <✅|❌>, JSON1 <✅|❌>`.
- **The 6 FTS5 consumers** call `require_fts5()` at their store-init (fail-actionable at init, never mid-query — INTEGRATION-ARCHITECTURE §2.7 fail-closed-for-capability); where a LIKE fallback genuinely exists, document it per-module and degrade with a warn instead of raising (record the per-module choice in the Execution log — no silent skips).

### C2 — Support matrix (README + `docs/guides/platforms.md`), every row names its proof
`| OS/arch | status | proof |` where proof ∈ {`CI:<job>`, `checklist:<runbook-section>`, `community`}. No row may say "supported" without a proof token.

### Integration points
- **Refactors (class R):** the try/except `import pysqlite3` in `snapshot.py`, `knowledge/retrieval.py`, `memory.py`, `portability.py`, `vector_memory.py`, and the bare `import sqlite3` in `loop/store.py` → all import from `sqlite_compat`.
- **Called by:** `cli_doctor.py` (the SQLite line), the 6 FTS5 modules (`require_fts5`).
- **CI:** adds arm64 jobs to `full.yml` (plan 33); multi-arch release gate in `release.yml`.
- **Owned docs:** `docs/guides/platforms.md`, `docs/roadmap/research/windows-native-audit.md`.
- **Consumed by:** DESKTOP (45) non-mac targets gate on this plan's rungs.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Track A, Session A1 — ARM correctness

| ID | Task | Files | Done when |
|---|---|---|---|
| A1.1 | `sqlite_features()` helper (driver name, version, fts5 bool via `pragma compile_options`/probe table, json1 bool) in ONE module; `doctor` line ("SQLite: pysqlite3 3.45, FTS5 ✅ JSON1 ✅") | create `src/personalclaw/sqlite_compat.py` (absorb the repeated try/except import into it; the 5 modules import from here — mechanical refactor, class R), `cli_doctor.py` | six call sites use the shared import; doctor renders the line; unit tests fake both drivers |
| A1.2 | Fail-actionable FTS5 guard: the FTS5 consumers' init paths check `sqlite_features().fts5` once and raise/degrade with the remedy text ("install personalclaw on Python with FTS5-enabled SQLite — see docs/platforms") instead of failing mid-query; degradation behavior per module recorded (memory FTS recall → LIKE fallback exists? verify per module, document truth, no silent skips) | the six FTS5 modules | a no-FTS5 fixture (monkeypatched probe) produces the actionable error/degradation at init, never a mid-query traceback |
| A1.3 | CI: add `ubuntu-24.04-arm` + `macos-14` jobs to `full.yml` (not PR path — budget); fix what breaks; record arm-specific failures in the Execution log | `.github/workflows/full.yml` | both jobs green; failures root-caused or xfail-annotated with issues |
| V-A1 | Validation: on a real ARM box or arm64 container — fresh install, onboarding, knowledge ingest + FTS search, memory recall, snapshot/restore | — | walkthrough clean; doctor line correct |

### Track A, Session A2 — ARM delivery

| ID | Task | Files | Done when |
|---|---|---|---|
| A2.1 | Multi-arch images release-blocking (release.yml gate: both arches must build + smoke `personalclaw --version` in each) | `.github/workflows/release.yml` | a failed arm64 build fails the release |
| A2.2 | Wheel/dep audit for `[models]` extra on arm64 linux (faiss-cpu, torch, sentence-transformers availability) — document per-arch reality + graceful degradation in docs | `docs/guides/platforms.md` (new) | table states what works where, verified by install attempts recorded in the log |
| A2.3 | Support matrix in README + platforms doc (linux x86/arm, macOS arm/intel, Windows rungs, each with its proof mechanism: CI job / checklist / community) | `README.md`, `docs/guides/platforms.md` | every row names its proof; no unproven "supported" |
| A2.4 | Pi-class note: RAM floor, extras to skip, swap guidance | `docs/guides/platforms.md` | present; numbers from the A1 validation box |
| V-A2 | Validation: `docker compose up` on an ARM VM from published rc images; state persists; healthchecks green | — | holds |

### Track B, Session B1 — Windows rung 1 (containers)

| ID | Task | Files | Done when |
|---|---|---|---|
| B1.1 | Windows-via-Docker-Desktop guide (WSL2 backend note, volume semantics, localhost ports, .env on Windows paths) | `docs/guides/platforms.md` section | a Windows user reaches the dashboard following it verbatim (owner task 2 validates) |
| B1.2 | Per-release Windows checklist added to the release runbook (compose up, dashboard, one chat, snapshot) | `docs/maintainers/release-runbook.md` | checklist merged; executed once for the current release (owner task 2) |

### Track B, Session B2 — Windows rung 2 (WSL2)

| ID | Task | Files | Done when |
|---|---|---|---|
| B2.1 | Browser auto-open fallback: when `xdg-open`/browser launch fails or WSL detected (`/proc/version` contains microsoft), print the URL prominently instead (+ try `wslview` if present) | the auto-open site (locate via `--no-open` flag handling in `cli_server.py`) | WSL fixture prints URL; normal Linux unchanged |
| B2.2 | WSL2 guide: install-in-WSL2 (uv path), systemd-in-WSL2 for `service install` (wsl.conf note), localhost forwarding (automatic), file-system perf note (keep home in ext4, not /mnt/c) | `docs/guides/platforms.md` | guide verbatim-validated (owner task 2) |
| B2.3 | `doctor` WSL awareness: detect WSL, note service/systemd status accordingly | `cli_doctor.py` | WSL fixture shows the note |

### Track B, Session B3 — Windows rung 3 audit (audit ONLY)

| ID | Task | Files | Done when |
|---|---|---|---|
| B3.1 | Per-mechanism audit doc against the verified list (Context): options, effort, risk, and the sandbox-degradation policy question stated plainly; go/no-go recommendation + demand-evidence criteria (issue count, Discord signal) | create `docs/roadmap/research/windows-native-audit.md` | every mechanism has options + cost; recommendation explicit; NO implementation code written (soul guardrail — E6 if tempted) |

## Owner tasks (real world)

1. **ARM validation hardware** (V-A1/V-A2): a Raspberry Pi 4/5, any ARM VPS ($5 tier), or an arm64 cloud VM for an hour — your pick; the walkthroughs are scripted.
2. **Windows validation** (B1/B2): access to one Windows 11 machine (yours or borrowed) for two ~30-min checklist runs per release cycle until CI can cover it.
3. **Decide the rung-3 go/no-go** when B3.1's audit lands (the sandbox-degradation policy is an owner-level security posture call).

## Risks & open questions

- **Stdlib-SQLite FTS5 variance** on niche distros is the real ARM risk — A1.2 converts silent breakage into actionable errors, which is the honest floor; shipping an arm64 pysqlite3 build is the later nicety (DISCOVERY-file it if variance shows up in the wild).
- **Open:** whether `loop/store.py` (plain `import sqlite3`, no fallback) should join `sqlite_compat` — yes in A1.1's refactor sweep; flagged here so the executor doesn't treat it as out-of-scope.
