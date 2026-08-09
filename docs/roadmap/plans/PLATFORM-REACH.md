# PLATFORM-REACH

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PR.md`](../atomic/PR.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
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

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

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
- **The 6 FTS5 consumers** call `require_fts5()` at their store-init (fail-actionable at init, never mid-query — the fail-closed-for-capability rule in AGENTS.md → Shared conventions); where a LIKE fallback genuinely exists, document it per-module and degrade with a warn instead of raising (record the per-module choice in the Execution log — no silent skips).

### C2 — Support matrix (README + `docs/guides/platforms.md`), every row names its proof
`| OS/arch | status | proof |` where proof ∈ {`CI:<job>`, `checklist:<runbook-section>`, `community`}. No row may say "supported" without a proof token.

### Integration points
- **Refactors (class R):** the try/except `import pysqlite3` in `snapshot.py`, `knowledge/retrieval.py`, `memory.py`, `portability.py`, `vector_memory.py`, and the bare `import sqlite3` in `loop/store.py` → all import from `sqlite_compat`.
- **Called by:** `cli_doctor.py` (the SQLite line), the 6 FTS5 modules (`require_fts5`).
- **CI:** adds arm64 jobs to `full.yml` (plan 33); multi-arch release gate in `release.yml`.
- **Owned docs:** `docs/guides/platforms.md`, `docs/roadmap/research/windows-native-audit.md`.
- **Consumed by:** DESKTOP (45) non-mac targets gate on this plan's rungs.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

## Execution log

- **PR-1 DONE — one SQLite binding + capability probe.** Added `src/personalclaw/sqlite_compat.py`:
  the driver choice (`pysqlite3` when the wheel is present, else stdlib `sqlite3`) is now made ONCE,
  and `probe()` (memoized) reports `{driver, version, fts5, json1}` against an in-memory connection.
  The five `try/except pysqlite3` blocks (snapshot.py, knowledge/retrieval.py, memory.py,
  portability.py, vector_memory.py) AND `loop/store.py`'s bare `import sqlite3` (the "Open" item the
  plan flagged — folded in, as directed) now all `from personalclaw.sqlite_compat import sqlite3`, so
  a test patching SQLite has one bind point instead of seven (clean break — the dual import paths are
  deleted, not shimmed). `cli_doctor.py` renders `SQLite: <driver> <version>, FTS5 <✅|❌>, JSON1
  <✅|❌>` with a `pip install pysqlite3-binary` fix hint when FTS5 is absent. **Gates:** `make lint`
  clean (698 source files — new module picked up); `tests/test_sqlite_compat.py` (8: all-six-consumers
  share one binding, real-driver version/FTS5/JSON1 probe agreeing with a direct check, memoization,
  faked full + stripped drivers, connect-blows-up→all-absent) + the touched-module suites
  (durability_shards / memory_smoke / vector_memory) = 88 passed. CHANGELOG `### Added` (the doctor
  line is user-visible). Scope: PR-1 only — the arm64 build matrix (PR-4) + Windows guides (PR-6/7)
  are later atoms.
- **PR-1 follow-up (amended):** the full-suite `test` job surfaced two consequences of the
  consolidation the file-scoped run missed. (1) `knowledge/store.py` also carried the `try/except
  pysqlite3` and was folded into `sqlite_compat` too (clean break — no straggler). (2)
  `test_knowledge.py::TestPysqlite3Fallback` evicts `pysqlite3` + reimports a consumer to prove the
  stdlib fallback; the driver choice now lives in `sqlite_compat`, so the helper must ALSO evict
  `personalclaw.sqlite_compat` (else the cached pysqlite3 binding defeats the fallback). Verified
  under a simulated pysqlite3-present environment (the CI condition): all three modules fall back to
  stdlib and `sqlite_compat` is restored afterward.
- **PR-8 DONE — native-Windows rung-3 audit (audit ONLY, zero code).** Wrote
  `docs/roadmap/research/windows-native-audit.md`. Honors the soul guardrail: the doc adds NO
  implementation code — it costs a native `pip install personalclaw` on Windows PowerShell (no WSL2,
  no Docker) and recommends against it. Each of the six mechanisms is grounded in a verified as-built
  citation and given options+effort+risk+verdict: (1) process reaping — `start_new_session=True` +
  `os.killpg` across 4 spawn / 5 kill sites (`acp/transport.py:366`/`:456`, escaped-child PPID walk
  `:109-208`) → **Job Objects** (M) beat `taskkill /T` (which reintroduces the escaped-child bug the
  POSIX code closes); (2) file perms — `chmod 0o600` + `_enforce_perms` on `mode & 0o077`
  (`llm/credentials.py:96`/`:288-297`, ~30 secret sites) → **`icacls`/ACL seam** (M) — the load-bearing
  risk, because `os.chmod` on NTFS only toggles read-only so the owner-only guarantee **silently**
  fails; (3) symlinks — 3 create sites (`frontend.py:71`/`:98`, `resilience/fixes.py:137`) →
  **junction-or-copy fallback** (S, cheapest); (4) PTY — module-load POSIX imports
  (`terminal.py:4-12`) + `pty.openpty()` `:273` → **disable the page** (S) or pywinpty (M); (5) service
  — `service/common.py:63-74` dispatch with no `Platform.WINDOWS` → **Task Scheduler** (S–M, fits the
  personal-agent model; a Windows Service fights the gateway's user-session + browser-open); (6) sandbox
  — `detect_backend()` (`sandbox.py:596-618`) + `_spawn_exec_shim.py:54-85` degradation → **isolation =
  none, ceilings via Job Object limits, agent command execution off-by-default with a loud
  acknowledgement** — the decisive posture question, and an owner-level call. **Verdict: NO-GO now** —
  the two blocking mechanisms (perms §2, sandbox §6) silently weaken security guarantees, are the most
  expensive to validate (a real Windows host, not CI mocks), and a finished port would still be weaker
  than the WSL2 path (rungs 1–2) that runs the real Linux code intact. Demand-evidence gate to flip to
  go: ≥10 distinct native-Windows-only issues, a top-5 sustained community theme, requests that
  converge on an environment WSL2/Docker genuinely cannot serve, and owner ratification of the §6
  posture. **Rung-2.5 finding (recorded, NOT fixed here — out of this rung's audit-only scope):** the
  unguarded method-local `import resource` at `gateway.py:3471` `ImportError`s at gateway **boot** on
  native Windows, so even "run it and observe" fails before start; guarding it (as
  `_spawn_exec_shim.py:54-57` already does) is a future one-line-class hardening, not a port. **Gate:**
  doc-only change — `make lint` clean; `python3 -c 'json.load(dag.json)'` parses (598 atoms). No source
  touched, so no pytest/web run applies.
