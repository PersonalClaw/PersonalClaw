# PLATFORM-HARDENING-FLOORS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PHF.md`](../atomic/PHF.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Platform Hardening Floors — Enforcement Floors, Trust Seams & Gate Ergonomics

**Status:** DESIGNED — created 2026-08-04. Every item below is an enforcement or trust
correction found by auditing the platform's own code (Gateway, sessions, memory, skills,
taskrunner, apps, SEL); each was verified against the codebase before being written down.
Nothing here is adopted on the strength of a claim — only on a verified code path.

**Created:** 2026-08-04
**Wave:** 0/1 — enforcement + trust floors (§1, §2, §5) are pre-engine; the gate-ergonomics
items (§3, §4, §6) are cheap legibility multipliers that make every later session cheaper.
**Depends on:** nothing hard. **This plan deliberately owns almost no new contracts** — its job
is to correct, complete, and re-home work that existing plans already own. See §0.

**Scope:** four enforcement/trust corrections whose premises were found wrong or whose owning
task rows were found inert, plus four self-contained developer-gate items — all surfaced by
auditing the platform's own code. **Soul guardrail:** adopt *mechanisms and their measured
hazards*, never a foreign posture. A default-on telemetry beacon, an EC2 launcher, a
gateway-federation hub, and i18n catalogs are **explicitly out of scope** (§7 records why, so a
future session does not mistake them for unbuilt ideas). Where we are already ahead —
provider-agnostic core, the Workflows-V2 engine, zero tracking, the design-system ratchets —
this plan changes nothing.

---

## 0. Ownership map — read this before touching any task row

**This plan is mostly a set of amendments to other plans.** Nine of the twelve items covered
already have an owner. Creating parallel machinery for them would violate *one path per concern*.
The table is the authoritative routing; a row marked **re-home** means the task line lives in the
named plan and this document only records the correction.

| Item | Existing owner | Disposition |
|---|---|---|
| rlimit ceilings + spawn audit | EXECUTION-ISOLATION `EI-A1`/`EI-A2`/`EI-A3` (2026-07-26 amendment) | **re-home + PREMISE CORRECTION** — §1. `EI-A1`'s `preexec_fn` mechanism is unsafe as written; the row needs rewriting, not duplicating |
| App-backend env inheritance | EXECUTION-ISOLATION `D1` (2026-07-29 amendment) | **re-home, unchanged** — already correctly specified (allowlist, not scrub). §2.2 only adds the two additional spawn sites it does not cover |
| App-backend inbound auth | *none* | **NEW here** — §2.1. Closest neighbours (REMOTE-USER-AUTH, EXTERNAL-ACCESS) own *external* auth; this is a local trust boundary inside the app platform |
| `SafetyProfile` ceiling ∩ profile | AUTONOMY-GUARDRAILS §3 + `S5.2` | **re-home + design input** — §5. The plan already records `SafetyProfile` as having **zero non-test callers**; this supplies the composition algebra for the wiring session |
| Config baseline / drift | *none* (adjacent: `test_config_roundtrip.py`) | **NEW here** — §3 |
| Inert-control inventory | *none* (recurring pain, five memories) | **NEW here** — §3.2 |
| Fake-model E2E harness | DESIGN-SYSTEM-CONSISTENCY `T3.2`/`T3.4`/`V3` (owner-closed with this as the recorded tail) | **NEW here, unblocking a named tail** — §4 |
| `--dist worksteal` escape hatch | CI-RELEASE-ENGINEERING (S1 amendment set it deliberately) | **re-home + narrow correction** — §6.1. Not a reversal; an escape hatch the root-cause fix did not provide |
| docs-lint gate | *none* | **NEW here** — §6.2 |
| Telegram sequencing | CHANNEL-EXPANSION `S2-3` | **no plan change** — §7.1. A sequencing note only |
| Release channels | DISTRIBUTION (DONE) | **deferred, not dropped** — §7.2 |
| `personalclaw logs`, subagent coalescing, onboarding import, store-listing fields | PLATFORM-RESILIENCE / WORK-CONTAINERS / ONBOARDING-UX / APP-PLATFORM-EVOLUTION | **re-home** — §6.3 carries them as one-line adoption notes in their owning plans |

**Change class.** §1, §2, §5 are class-B (they change what a child process receives and what a
request must carry). Under the pre-1.0 banner these are **clean breaks** — no gates, no dual
paths. §3, §4, §6 are class-R.

---

## 1. Resource ceilings — correcting `EI-A1`'s mechanism (PREMISE CORRECTION)

### 1.1 What the existing row gets wrong

EXECUTION-ISOLATION `EI-A1` (2026-07-26) specifies `ceiling_kwargs()` returning
`{"preexec_fn": ...}` and justifies it:

> `preexec_fn` is fork-safe here because every seam spawns from the single-threaded-at-fork
> asyncio path — documented per-seam

**That premise is false, verified 2026-08-04.** Core has **67 thread-creation sites**
(`threading.Thread` / `ThreadPoolExecutor` / `run_in_executor` / `to_thread`), and two of the
named seams demonstrably do not spawn single-threaded:

- `apps/backend_runtime.py:288` `start_backend_watchdog()` runs `_check_and_revive()` on a
  **daemon thread** every 30s, and that sweep **respawns app backends**. So an app-backend spawn
  happens off a thread, in a process with many others running.
- `action_providers/bash_provider.py:216` spawns via `asyncio.create_subprocess_exec` — i.e. on
  the **event loop thread** of a multi-threaded process.

The CPython behavior that makes this a hazard, verified against the interpreter internals:
`preexec_fn` forces CPython off `posix_spawn`/`vfork` onto a plain `fork()` of the whole gateway
and runs Python bytecode in the child before `exec`. A lock another thread held at fork time
cannot be released there, so the child can wedge before reaching `exec` — and a wedged child
takes more than itself down:

- `subprocess.Popen._execute_child` blocks in an **unbounded `os.read(errpipe_read, …)`**
  waiting for the child to exec or die. Under `asyncio.create_subprocess_exec` that read runs on
  the **event loop thread with no `await` point**, so no `asyncio.wait_for` can interrupt it and
  the whole gateway stops.
- `_posixsubprocess`'s `child_exec()` runs `_close_open_fds()` **after** `preexec_fn`, so the
  wedged child still holds a duplicate of every inherited fd — `gateway.lock` and the dashboard's
  listening socket included — which then **outlive the gateway**.

We have zero `preexec_fn` uses today, so this is a hazard we would be *introducing* by executing
`EI-A1` as written. This is exactly the wired-but-wrong control class: a control that runs every
time and is still category-wrong.

### 1.2 The corrected mechanism: deliver limits after `exec`

The corrected delivery model: limits are applied by a tiny **exec shim** — a stdlib-only module
prepended to the argv, which calls `setrlimit` in the already-`exec`'d, single-threaded child and
then `os.execv`s the real target. Coverage is unchanged (limits are inherited by the exec'd image
and all descendants); only the delivery point moves off the fork.

**Profile split** (a single global ceiling is wrong, and this is why):

| Profile | Applies to | Effect |
|---|---|---|
| `tool` (default) | every ordinary agent-influenced spawn | full ceiling + `oom_score_adj=1000` (prefer killing agent work over the gateway) |
| `session_host` | ACP session hosts (`acp/transport.py`) | **raises** NOFILE to the inherited hard limit, nothing else. A session host multiplexes many MCP pipe pairs; a 1024 cap causes EMFILE crashes. No OOM bias — a trusted host must not be the preferred kill target |
| `build` | frontend/npm builds | thousands of descriptors; keeps the OOM bias |
| `none` | the user's own interactive terminal (`dashboard/handlers/terminal.py`) | no limits, no bias — the shim would have nothing to deliver and costs an interpreter startup per terminal open |

The `session_host` and `none` rows are the load-bearing part: without them a uniform ceiling
either breaks ACP sessions or taxes every terminal open. `EI-A1` as written has one ceiling for
everything.

### 1.3 Tasks (rewrite `EI-A1`/`EI-A2`/`EI-A3` in EXECUTION-ISOLATION)

| ID | Task | Files | Done when |
|---|---|---|---|
| SH1.1 | `_spawn_exec_shim.py` (stdlib-only, no core imports): parse a `RLIMIT_NAME:value` policy string from argv, `setrlimit` each, set `oom_score_adj` when the profile asks, `os.execv` the real target. `ResourceCeilings` + the four profiles + `spawn_shim_argv(argv, profile)` in `sandbox_providers/ceilings.py`. Config knobs `sandbox.nofile` / `sandbox.max_pids` / `sandbox.max_rss_mb` wired 4-point (dataclass + `_meta`, `load()`, `to_dict()`, write path) | new `src/personalclaw/_spawn_exec_shim.py`, `sandbox_providers/ceilings.py`, `config/loader.py` | a child running `ulimit -n` reports the ceiling; the shim is importable with no core dependency (assert by running it under `-S`); config round-trips |
| SH1.2 | Route the agent-influenced async seams through `create_subprocess_limited()` (shim-prepended, `preexec_fn=None`): native bash, bash action provider, subagent spawn, app backends, MCP stdio (`mcp_client.py`, `mcp_discovery.py`), ACP transport (`session_host`), loop gates/worktree. Terminal gets `none`; frontend/service/update spawns are operator-exempt | `agents/native/builtin_tools.py`, `action_providers/bash_provider.py`, `subagent.py`, `apps/backend_runtime.py`, `mcp_client.py`, `mcp_discovery.py`, `acp/transport.py`, `loop/gates.py`, `loop/worktree.py` | every listed seam spawns through the shim; ACP sessions still open with many MCP servers (the EMFILE regression is what `session_host` prevents — verify with a multi-server config, not a unit test) |
| SH1.3 | Two AST tripwires. (a) `tests/test_spawn_ceiling_audit.py`: every `create_subprocess_*` / `Popen` / `run` / `StdioServerParameters` site is in an allowlist tagged `ceiling-wrapped` or `operator-exempt`; a new unmapped site fails CI with `file:line`. (b) `tests/test_spawn_preexec_guard.py`: **no async spawn site passes `preexec_fn`**, with exactly the documented exceptions (the shim's own no-shim fallback; the `none`-profile terminal) | new tests | audit green with every agent-influenced seam covered; adding an unmapped `Popen`, or a `preexec_fn` on an async site, reds CI naming the site |
| SH1.4 | cgroup v2 tier (Linux, opt-in `sandbox.cgroup_scopes`): `systemd-run --user --scope -p TasksMax -p MemoryMax -p MemorySwapMax=0`. Probe once for unified hierarchy + systemd user session. Unavailable (macOS, non-systemd, containers) → **one loud warning naming what is not enforced**, NOFILE still applies | `sandbox_providers/ceilings.py`, `resilience/doctor.py` probe line | Linux fixture: a fork bomb hits `pids.max` and dies contained; macOS: one warning states pids/RSS are not enforced; the probe never raises |
| SH1.5 | Audit the two hazard sites named in §1.1 for the *pre-existing* form of the bug: a `Popen`/`run` reached from the watchdog thread while the loop holds locks. Record findings; fix only what the audit proves | `apps/backend_runtime.py`, `action_providers/bash_provider.py` | either a regression test for a proven wedge, or a recorded DISCOVERY that the sites are safe and why |
| V1 | Validation: start the dev gateway; from a chat session run `ulimit -n` and a bounded fork bomb; open a terminal (confirm no shim cost); open an ACP session with ≥3 MCP servers; kill an app backend and let the watchdog revive it — confirm the revived child carries the ceiling. Inspect SEL + gateway log | — | holds; timings and the fork-bomb containment recorded in the Execution log |

**Sequencing.** SH1.1→SH1.3 are one session and land **before** SH1.4 (the cgroup tier is a
second enforcement layer, not the floor). SH1.5 is independent and can go first — it is an audit.

---

## 2. The app-platform trust boundary

### 2.1 App-backend inbound authentication (NEW — a verified inconsistency)

`docs/architecture/app-platform.md:76` documents a careful **outbound** credential boundary: the
proxy strips the owner's cookie and `Authorization` and injects a fresh 1-hour app-scoped token,
so "an app backend must never see a token it could replay against the full gateway API." That
half is real and good.

The **inbound** direction is unauthenticated. `apps/backend_runtime.py:251` binds the backend to
`127.0.0.1:<ephemeral port>` and the port is the only thing standing between any local process
and the backend's full route surface. There is no HMAC, shared secret, or token check on the way
in (`grep` for `X-PersonalClaw-Proxy` / `proxy_hmac` / `app_secret` returns nothing). So a local
process that finds the port talks to the backend **directly, bypassing the gateway proxy** — and
therefore bypassing session auth and `app_permission_middleware` entirely. Loopback binding is a
network boundary, not an authorization one, and the app platform's whole permission story assumes
requests arrive through the proxy.

The fix closes exactly this: every proxied request carries
`X-PersonalClaw-Proxy: <ts>:<hmac>` over `<ts>:<METHOD>:<path>[?q]:<sha256(body)>` with a ±60s
window, verified **fail-closed** by the backend's middleware, with the secret at
`apps_dir()/<app>/.app_secret`.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH2.1 | Mint a per-app proxy secret at install/first-boot (`apps_dir()/<app>/.app_secret`, mode 0600); hand it to the backend by env. Sign every proxied request in `api_app_proxy`: `X-PersonalClaw-Proxy: <ts>:<hmac>` over `<ts>:<METHOD>:<path>[?query]:<sha256(body)>` | `dashboard/handlers/apps.py`, `apps/backend_runtime.py`, `apps/app_manager.py` | a proxied request carries the header; the secret file is 0600 and never logged |
| SH2.2 | Verify in the SDK app-server helper, **fail-closed**: absent/malformed/stale (>±60s)/wrong signature → `401`, no route body runs. Constant-time compare (`hmac.compare_digest`). Denials log to SEL | `sdk/` app-server helper, `sdk/security.py` | a direct `curl` to the backend port is refused; a replayed request outside the window is refused; a valid proxied request succeeds |
| SH2.3 | Roll every first-party app that ships a backend onto the verifying helper. Any app that cannot verify is a **bug in this task**, not an exemption | `PersonalClawApps/*/backend/*` | every first-party backend refuses unsigned requests; the full app-boot path runs green (not just unit tests) |
| SH2.4 | Document the boundary honestly in `docs/architecture/app-platform.md`: loopback is not authorization; the signature is what makes the permission model hold. Same section notes the pre-fix posture so the CHANGELOG entry is accurate | `docs/architecture/app-platform.md`, `CHANGELOG.md` | the doc states what each layer does and does not buy |
| V2 | Validation: install a first-party app; `curl` its backend port directly (refused); drive it through the UI (works); replay a captured signed request after 90s (refused); confirm the SEL entries | — | holds |

**Cross-repo note.** SH2.3 spans both repos — that is a **re-scope across two commits**, never a
blocker. Core lands the helper first; apps follow in the same session.

**Fail-open vs fail-closed, stated per control:** verification is **fail-closed** (an app backend
with no verifiable caller must serve nothing). The secret *mint* is fail-closed too — a backend
that cannot read its secret does not start, rather than starting unprotected.

### 2.2 Environment inheritance — two sites `D1` does not cover

EXECUTION-ISOLATION `D1` already owns this correctly, and its shape is **better than a scrub**:
build the child env from a **minimal allowlist** (`PATH`, locale, home-equivalent, the three
PersonalClaw vars) plus declared needs, with `_SENSITIVE_ENV_PREFIXES` as the floor. `D1` needs
no redesign. Note the near-miss it already records: `backend_runtime.py:127-131` carefully
withholds `PERSONALCLAW_APP_DATA_DIR` when `storage` isn't declared — the permission discipline
is present, but the env it starts from is `dict(os.environ)`.

This plan adds only the sweep `D1` does not name:

| ID | Task | Files | Done when |
|---|---|---|---|
| SH2.5 | Apply `D1`'s allowlist shape to the other agent-influenced spawn sites that inherit the gateway env: shell hooks and cron scripts. `action_providers/bash_provider.py` already has `_scrub_env` — confirm it is the allowlist shape or align it | `hooks.py`, `schedule_script.py`, `action_providers/bash_provider.py` | a planted secret in the gateway env is absent from a hook child, a cron-script child, and a bash-action child (one regression test per site) |

---

## 3. Config baseline + the inert-control inventory (NEW)

### 3.1 Why a committed baseline beats a round-trip test

`test_config_roundtrip.py` proves each field survives a round trip. It cannot see **drift**: a
renamed key, a silently dropped `_meta`, a field that stopped being written. The fix: generate
`config-baseline.json` from the dataclass schema registry and commit it, so any schema change
that isn't regenerated fails CI. Same source of truth (our `_meta` registry), strictly more
coverage.

### 3.2 The shape that serves the recurring inert-control pain

The higher-value half. A recurring defect class shows up in different costumes: a live reader for
a key nothing writes, an enum member with no writer, trigger kinds shipped declared+inert, a
defaulted field that is really an unsupplied input, and other inert-control shapes. In every case
the symptom is the same: **something is declared and nothing on the other side of the seam
consumes or produces it**, and the tests pass because they hand-build the state the writer was
supposed to create.

A generated, committed, **per-file counter that may only shrink** is the natural home for it. A
per-file `missing_code`-style counter (e.g. `missing_code: 1465` per file, with the standing rule
*"the CI ratchet and the worklist… Never raise a number to make CI pass"*) proves the ergonomics
at scale. A count makes a large cleanup schedulable and makes silent backsliding impossible.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH3.1 | `scripts/generate_config_baseline.py` walking the existing `_meta` registry → committed `config-baseline.json` (flat path list + type + default + `sensitive`). CI job asserts regeneration is a no-op | new script, `config-baseline.json`, `.github/workflows/ci.yml` | renaming a config field without regenerating reds CI naming the path; the generator is deterministic (byte-identical on re-run) |
| SH3.2 | `tests/test_inert_surface_baseline.py` + committed `inert-surface-baseline.json`: for each declared surface (config keys, enum members, registered kinds/runtimes, `_EDITABLE_CONFIG` entries, SDK exports) record whether a **writer** and a **reader** exist. Counters are per file and **may only shrink**. Ship it at the measured population, not at zero | new test + baseline | the current inert population is measured and committed; adding a declared-but-unread surface reds CI; the doc line states plainly that raising a number to go green is forbidden |
| SH3.3 | Drive the top offenders in the committed baseline down, one file per commit. **`SafetyProfile` is expected to appear** (§5) — do not fix it here; §5 owns it | per-file | each commit shrinks a counter with a test proving the writer now exists |
| V3 | Validation: add a config field without regenerating (CI red); add an enum member with no writer (CI red); regenerate and confirm green | — | holds |

**Enforcement caution.** Ship both baselines at their **measured** population. Giving a never-run
gate teeth at zero is an outage — measure, commit the real number, then ratchet.

---

## 4. The offline fake-model E2E harness (NEW — unblocks a named tail)

DESIGN-SYSTEM-CONSISTENCY was **owner-closed with an honestly-recorded tail**: `T3.2`/`T3.4`/`V3`
(authenticated axe-per-route CI gate, full keyboard/reduced-motion walkthrough) "need a seeded,
authenticated, per-route CI harness that was not stood up here." `ci.yml:81` carries the same
note. **A packaged fake model backend is that harness.**

A packaged fake-model E2E harness boots a real gateway against a packaged fake model backend,
then shells Playwright at it — no model, no credentials, no network, no cost. Three wiring
details are load-bearing, each a trap worth avoiding up front:

- **Skipped in a bare `pytest`** behind an env flag — minutes per interpreter is far too slow for
  the per-commit gate.
- **Forced serial with `addopts` cleared** — xdist would spawn one gateway per worker, and
  coverage of a subprocess gateway measures nothing.
- **A much higher timeout** than the unit cap, because a generic pytest timeout kills the run and
  hides which specs failed.

A second mechanism worth building: a strict-mode env flag (e.g.
`PERSONALCLAW_STRICT_ON_LOOP_PERSIST=1`) that promotes a "we always call it through the helper"
convention into an **enforced invariant for the duration of the gate** — a raw call that skipped
the helper raises instead of silently losing data. We have several such conventions (on-loop
persistence, atomic writes, `security.is_fenced` for fenced-content detection).

| ID | Task | Files | Done when |
|---|---|---|---|
| SH4.1 | A fake model provider fixture (deterministic, scripted responses, tool-call emission, zero network) usable as a real bound provider. Reuse the `empty` seed fixture path | `src/personalclaw/tests_fixtures/`, a fixture provider bundle | a gateway boots with the fake provider bound and completes a scripted chat turn with no credentials present |
| SH4.2 | `make test-e2e`: boot a real gateway on the fake provider with a **seeded authenticated session**, then run `web/e2e/`. Skipped unless `PERSONALCLAW_E2E=1`; serial; own timeout; no coverage instrumentation | `Makefile`, `scripts/`, `web/playwright.config.ts` | one command runs the browser gate offline; a bare `pytest` does not run it |
| SH4.3 | Mount DESIGN-SYSTEM-CONSISTENCY's deferred `T3.2`/`T3.4` axe-per-route a11y rail on the harness; add it to CI | `web/e2e/a11y.spec.ts`, `.github/workflows/ci.yml` | every authenticated route is axe-scanned in CI; a new WCAG AA violation reds the build |
| SH4.4 | A strict-mode env flag that turns one persistence/fencing convention into a hard failure for the gate's duration; the harness gateway inherits it | the convention's helper module, harness wiring | a deliberately raw call that skips the helper reds the gate |
| SH4.5 | Port the hourly UI-validation loop's click-path into the harness as specs, so it is reproducible in CI rather than manual | `web/e2e/` | the loop's core surfaces are asserted by the offline gate |
| V4 | Validation: run `make test-e2e` on a machine with **no provider credentials and network off**; confirm it passes and the a11y rail reports per route | — | holds; runtime recorded |

---

## 5. `SafetyProfile` → ceiling ∩ profile (design input for AUTONOMY-GUARDRAILS S5.2)

AUTONOMY-GUARDRAILS already records the finding: §3's `SafetyProfile` family and §4.2's
`egress_policy_for_tier`/`REGISTRY` shipped with **zero non-test callers**, so Success Criterion
#7 holds only inside `tests/test_guardrails_profiles.py`. `tool_grants` / `denylist_extra` /
`egress_tier` have no reader. This is the textbook "live reader of an unwritten key" shape,
inverted — a declared *decision object* nothing consults.

The wiring session (`S5.2`) is the moment to get the layering right. **Do not invent a second
layering scheme**; adopt the following, which is better than what a from-scratch pass would
produce:

- **Two levels, one rule — tightest wins.** Level 1 `Ceiling` is loaded once at boot from a path
  the agent process does not own; the running agent **cannot weaken it**. Level 2 is the existing
  `SafetyProfile`, which may only **narrow**. Effective = `ceiling ∩ profile`. We have the profile
  half; the ceiling half is missing.
- **Four archetypes, one compose function each** — `ScopedRuleset {mode, allow[], deny[]}`,
  `OrdinalControl` (strictest-of on an enforcer-owned scale), `CapabilityGate`
  (`enabled` = AND, scopes are rulesets), `ScopedMap`. The evaluator dispatches on
  **archetype, never on scope name** — which is what makes adding a scope *data* rather than
  engine code.
- **Enforcer-owned registries.** Ordinal scales and matchers live in code, never sourced from a
  governed file, so no profile can reorder strictness or redefine matching. An unknown matcher
  aborts governance boot under `fail_closed`.

**The path-matcher rule, lifted verbatim as a test — this is our own recorded landmine.** Normalize
**only the queried item** (expand `~`/`$VAR`, then `abspath`, which anchors a relative path and
collapses `.`/`..`). **Never** run the *pattern* through `normpath`: `normpath` treats `*`/`**` as
ordinary segments and collapses an adjacent `..` against them — `/a/**/../b` → `/a/b`, silently
**dropping the `**`** and widening an allow (or shrinking a deny). Two properties this buys:
(1) `~/ws/../.bashrc` collapses to `~/.bashrc` and no longer matches an allow of `~/ws/**`;
(2) an agent-supplied relative `../../etc/passwd` is absolutized so it cannot dodge a deny of
`/etc/**` by failing to match. This is precisely the wired-but-wrong control class (paths compared
as strings) — a control that runs every time and is still category-wrong.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH5.1 | `Ceiling` loaded at boot from an operator-owned path; `resolve(ceiling, profile)` returns the intersection; the four archetypes each with one compose function; matchers + ordinal scales in enforcer-owned registries; unknown matcher = fail-closed boot abort | `guardrails/policy.py`, new `guardrails/ceiling.py` | a profile cannot widen the ceiling (test per archetype); an unknown matcher aborts boot with a WHAT/WHY/FIX error |
| SH5.2 | Table-driven path-matcher tests encoding the rule above, including `/a/**/../b`, a `..` traversal against an allow-prefix, and a relative item against an absolute deny | `tests/test_guardrails_path_matcher.py` | every case asserts the documented outcome; a `normpath`-on-pattern implementation reds them |
| SH5.3 | Wire `profile_for_session` into the three dispatch seams + spawn (AUTONOMY-GUARDRAILS `S5.2`'s actual scope) so Success Criterion #7 holds **in code**, not only in tests. Composes with §1: profile picks tools/egress, `SandboxSpec.ceilings` picks blast radius | `session.py`, dispatch seams, `subagent.py` | an unattended trigger-fired run resolves through `HEADLESS` with a live reader, proven by driving a real trigger, not a constructed object |
| V5 | Validation: fire a real unattended trigger; confirm from logs/SEL the resolved profile and that a narrower profile bites; attempt to widen via profile and confirm refusal | — | holds |

**Ordering.** SH5.3 is the point of the section — a ceiling nobody consults would just add a
second inert layer to the one we are fixing.

---

## 6. Developer-gate ergonomics

### 6.1 An `xdist_group` escape hatch (narrow correction to CI-RELEASE-ENGINEERING)

**This is not a reversal.** CI-RELEASE-ENGINEERING's S1 amendment deliberately chose
`--dist worksteal` after root-causing four isolation bugs in-code rather than shipping
loadscope/reruns as mitigations. That was the right call and the fixes were real.

The narrow gap: `grep xdist_group tests/` returns **nothing**, and `worksteal` does not honor the
mark anyway — so there is **no way to serialize a test that genuinely requires it**. Two live
consequences are on record: two SEL-audit tests flake ~1-in-3, only in the full run (which
contradicts the pyproject claim that the suite is deterministic under worksteal), and a
knowledge-merge test flakes on unclosed sqlite handles — a resource-exhaustion flake. Both are
classed PRE-EXISTING on main.

The `--dist loadgroup` framing is the useful one: without group honoring, tests needing
serialization scatter across workers and produce **flaky races rather than reproducible ordering
bugs**. The unclosed sqlite handle is a real bug either way; `worksteal` is why it presents as
flake instead of a failure you can debug.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH6.1 | Switch `--dist worksteal` → `loadgroup` **only if** measured suite wall-time regression is acceptable; record the measurement either way. The mark is the deliverable, not the scheduler | `pyproject.toml` | before/after wall time recorded in the Execution log; if `loadgroup` costs too much, record that and keep `worksteal` — SH6.2/6.3 still stand |
| SH6.2 | Fix the knowledge-merge root cause: close the `KnowledgeStore` connection in fixture teardown. No rerun, no xfail | `tests/conftest.py`, `knowledge/store.py` | the suite runs 5× with zero `ResourceWarning: unclosed database`; the two named tests pass deterministically |
| SH6.3 | Fix the subagent SEL flake at its root: the SEL call in `_force_reap`/`_spawn_with_approval` is inside a swallowing `except Exception`, so contention makes the patched mock never fire. **Narrow the except so a real error surfaces**, and give the two tests an isolated home | `subagent.py`, `tests/test_subagent.py` | both tests pass 10/10 in the full suite; a genuine SEL write failure now raises instead of silently skipping |
| V6 | Validation: full suite 5× consecutively; zero flakes; both memories' symptoms absent. **Update or delete both flake memories** — a fixed flake documented as PRE-EXISTING becomes a trap | — | holds; memories reconciled |

**Why SH6.2/6.3 matter more than SH6.1:** a swallowed SEL write is a *security-audit* gap, not a
test problem. If contention can make an audit write vanish silently in tests, it can in
production.

### 6.2 A docs-lint gate (NEW)

CLAUDE.md and the AGENTS.md definition-of-done both require docs to move with the change. There is **no
mechanical enforcement** — no docs job in `ci.yml`, no docs linter in `scripts/`. Given that
25 of 66 plan `**Status:**` lines were once found wrong (plan headers drift from their execution
logs), and that docs routinely drift from code (a governance doc can document matchers the code
has since removed), a cheap checker is well aimed.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH6.4 | `scripts/docs_lint.py` + `make docs-lint` + a blocking CI job: dead relative links, missing anchors, `file.py:NNN` citations whose file no longer exists, and code-fence language tags. Ship at the measured population with a shrink-only allowlist | new script, `Makefile`, `.github/workflows/ci.yml` | a dead link or a stale `file:line` citation reds CI; the current population is committed, not zero |
| SH6.5 | Extend it to plan hygiene: a plan whose `**Status:** DONE` has unchecked task rows, or whose header contradicts its `## Execution log`, is reported. **Report-only at first** — the log and the code win over the header, so this ratchets rather than blocks | `scripts/docs_lint.py` | the checker reproduces the 2026-08-04 audit's findings on a seeded stale header |

### 6.3 One-line adoption notes for their owning plans

Small, verified, each belonging to an existing plan. Recorded here so the finding is not lost;
the task line goes in the named plan.

| Item | Goes to | Note |
|---|---|---|
| `personalclaw logs` | PLATFORM-RESILIENCE (Doctor family) | Resolve the source automatically — systemd journal → launchd stdout → foreground `gateway.log` — with `-f`/`-n`. Our own CLAUDE.md documents "which log?" as a gotcha; this deletes the gotcha |
| Subagent event coalescing | WORKFLOWS-V2-WORK-CONTAINERS | Above ~8 active subagents, buffer per-agent delta events (latest wins) and flush one batched WS frame per ~1s tick. `_MAX_CONCURRENT = 3` today; **needed before the cap rises** — the parent-session injection wall already bites at 8, which that plan's amendment records |
| Conservative onboarding import | ONBOARDING-UX | Import user-owned data from other local agent tools. A direct switching-cost reducer for a self-hosted product |
| Incognito / ephemeral session | SESSION-MANAGEMENT | Opt out of persistence per conversation. No equivalent in `session.py`; cheap trust affordance for a memory-heavy product |
| `highlights` + `screenshots` manifest fields | APP-PLATFORM-EVOLUTION | We have `heroImage`/`tags`. These two most change how an app reads in the Store. A repo-sourced store fetches `app.json` from the app's own repo (`git archive`, 24h cache) so a version bump needs no registry edit — we already do repo-sourced manifests; only the fields are missing |
| Reasoning-effort capability table | MODEL-USE-CASES-V2 | One source of truth for reasoning-effort levels **and which models reject the knob**. We thread `reasoning_effort_override` through `session.py:517` with no central table |
| Config-referencing-paths drift validator | ECOSYSTEM-TOOLING | A CODEOWNERS-style path validator fails the build when a path pattern matches no file. The generalizable half; applies to `_EDITABLE_CONFIG` and plan task tables |

---

## 7. Deliberately excluded — do NOT re-surface as open work

Recorded so a later session does not mistake these for unbuilt ideas.

### 7.1 Telegram — a sequencing note, not a plan change
CHANNEL-EXPANSION `S2-3` already specifies Telegram correctly (raw Bot API over `httpx`, no SDK,
long-poll inbound so nothing is exposed, MarkdownV2 escaper as its own module, inline-keyboard
approvals). Comparable agents ship many channels to our one, and "works from my phone" is the
retention and demo story. That comparable channels (WeCom, WeChat, Webex) can be added
independently validates that our transport-provider seam is the right abstraction. **No plan
edit** — this is an argument for picking `S2-3` up sooner.

### 7.2 Release channels — deferred, not dropped
DISTRIBUTION is DONE and publishes a single stream; `_installer.py` has no channel concept. We
already have OIDC build-provenance attestation (`release.yml:218`), which is stronger than a bare
`SHA256SUMS`. The gap is only the **channel** concept, whose value is dogfooding the engine
program's cadence without shipping every merge. **DEFERRED** to a DISTRIBUTION follow-on; named
gate: when the engine queue's merge rate makes a nightly worth the release machinery.

### 7.3 Dropped outright
Rejected mechanisms, recorded so a later session does not mistake them for unbuilt ideas — none
align with the zero-tracking, provider-agnostic, local-first posture:
- **Telemetry beacon.** Even a principled design (five fields, a CDN configured not to log client
  IPs, per-app receipts HMAC'd so they cannot be correlated into an installed-app profile,
  auto-off in CI and non-default homes, an enterprise pin-off) is still a daily default-on
  outbound signal. **Our zero-tracking posture is the better promise — keep it and market it.**
  The only transferable artifact is the model of honest disclosure documentation.
- **Gateway-to-gateway federation / multi-instance hub** — already a **permanent architectural
  veto** here; Companion-Apps' multi-gateway switching is the sanctioned alternative. Listed only
  so a polished implementation elsewhere is not mistaken for a new idea.
- **Cloud launcher** (an EC2/CloudFormation provisioner) — out of soul (local-first). The honest
  caveat worth internalizing: a shell deny-list and in-layer chokepoints are "best-effort
  friction, not containment," because a default sandbox does not hide `~/.aws`. The transferable
  artifact is the **honest containment write-up**, which §1/§2's doc rows already adopt — not the
  feature.
- **i18n (ten languages)** — real engineering, wrong stage for a pre-1.0 solo project. The
  runner-not-`&&`-chain lesson is taken (§8); the catalogs are not.

---

## 8. One cross-cutting lesson: aggregate gates report, they don't short-circuit

A well-built aggregate gate spawns its N sub-scripts, keeps every byte of output, and reports
every check in **one table**, with the verdict as a pure function over plain data. The reasoning
generalizes: an `&&` chain of N independent checks tells a PR about only its **first** failure —
so a tree with three unrelated problems costs three full push/wait rounds to discover, over
independent measurements of the same commit.

Our candidates are `scripts/run_prepush.sh` and `personalclaw.dev`'s `test:ci` aggregate.

| ID | Task | Files | Done when |
|---|---|---|---|
| SH8.1 | Convert `run_prepush.sh` from a short-circuiting chain to a runner: run every independent check, capture all output, print one result table, exit non-zero if any failed. Verdict computed from collected data, not inline | `scripts/run_prepush.sh` | a tree with three independent failures reports all three in one run |
| SH8.2 | Same for `personalclaw.dev`'s `test:ci` (respecting the repo-owned pre-push hook — never weaken an assertion to go green) | `personalclaw.dev` scripts | one table; every failure visible in one run |

---

## Recommended execution order

Ordered by **atomic completability** (`ROADMAP.md` §4) — each row finishes to a clean,
dependency-complete state without waiting on the engine queue.

| # | Work | Size | Why here |
|---|---|---|---|
| 1 | §2.1 app-backend proxy signature (SH2.1-2.4) + §2.2 env sweep (SH2.5) | S | A verified hole in a documented boundary. Self-contained; `D1` covers the rest |
| 2 | §3.1 config baseline (SH3.1) | S | Drift detection today, and it stands up the generator §3.2 reuses |
| 3 | §6.3 `personalclaw logs` | S | Deletes a gotcha we keep re-documenting |
| 4 | §6.1 the two flake root causes (SH6.2, SH6.3) | S | A swallowed SEL write is an audit gap, not a test problem. Do **before** any suite-wide scheduler change |
| 5 | §1 ceilings + exec shim + tripwires (SH1.1-1.3, 1.5) | M-L | Largest robustness gap; **rewrites `EI-A1`'s unsafe mechanism** — the premise correction must land before anyone executes that row |
| 6 | §4 fake-model E2E harness (SH4.1-4.3) | M | Unblocks DESIGN-SYSTEM-CONSISTENCY's recorded a11y tail and mechanizes the hourly loop |
| 7 | §3.2 inert-surface baseline (SH3.2) | M | Wants §3.1's generator first; measure before ratcheting |
| 8 | §5 ceiling ∩ profile (SH5.1-5.3) | M | Completes an inert shipped control. After §1 so `SandboxSpec.ceilings` exists to compose with |
| 9 | §1.4 cgroup tier (SH1.4) | M | Second enforcement layer; only after the floor |
| 10 | §6.2 docs-lint (SH6.4-6.5) + §8 runner gates (SH8.1-8.2) | S-M | Cheap legibility; do while something larger is in review |
| 11 | §6.1 scheduler decision (SH6.1) | S | **Last** — decide with measurements, after the root causes are gone |

Rows 1-4 are roughly one focused session between them.

---

## Owner tasks (real world)

- **Decide the ceiling's trust root** for §5: which path holds `Ceiling`, and how it is protected
  from the agent process on a single-user machine (this is the one genuinely open design question
  — the obvious answer assumes an enterprise operator we do not have).
- **Confirm the §7.2 deferral gate** (when a nightly channel earns its machinery).

## Risks & open questions

- **§1 is the risky one.** The exec shim changes how every agent-influenced child starts. The
  `session_host` profile exists precisely because a uniform NOFILE cap breaks ACP sessions —
  validate with a real multi-MCP-server config, not a unit test.
- **§5's trust root is weak on a single-user machine.** An agent that can run code can often reach
  the ceiling file. Document what the layer does and does not buy (§1/§2's doc rows set the
  precedent) rather than overstating it — the standing rule from EXECUTION-ISOLATION's `D0` is
  that an inaccurate security claim is a live defect.
- **SH6.1 may not be worth it.** If `loadgroup` costs significant wall time, record the
  measurement and keep `worksteal`; SH6.2/6.3 deliver the real value either way.
- **§3.2's scope could sprawl.** "Declared but unread" is a wide net. Ship the measured
  population, ratchet down, and resist widening the definition mid-session.

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-04][plan] DISCOVERY: EXECUTION-ISOLATION `EI-A1` specifies `preexec_fn` and justifies
  it as "fork-safe because every seam spawns from the single-threaded-at-fork asyncio path". The
  premise is **false**: core has 67 thread-creation sites, `apps/backend_runtime.py:288` respawns
  backends from a daemon thread, and `action_providers/bash_provider.py:216` spawns on the event
  loop thread. Executing that row as written would introduce a gateway-wedge hazard (a
  `preexec_fn` child can wedge before `exec` while holding inherited fds). §1 rewrites the
  mechanism (post-`exec` shim + four profiles + a `preexec_fn` AST tripwire). No code was changed
  by this plan.
- [2026-08-04][plan] DISCOVERY: the app-platform trust boundary is one-directional. The outbound
  half is documented and real (`app-platform.md:76`); the inbound half is unauthenticated —
  `apps/backend_runtime.py:251` binds loopback with no signature/token check, so a local process
  bypasses the gateway proxy, session auth, and `app_permission_middleware`. Filed as §2.1.
- [2026-08-04][plan] DISCOVERY: nine of the twelve audited items already have plan owners; this
  plan re-homes them rather than creating parallel machinery (§0). `SafetyProfile` (§5) and the
  app-env inheritance (§2.2 / `D1`) were **already recorded as inert or open** by their owning
  plans — the code audit independently re-derived both, which is corroboration, not new scope.
- [2026-08-12][PHF-12] DONE: the §3.2 census violated the invariant its own detector documents.
  `_inert_enum_surfaces` stated the rule and the error direction explicitly — *"An enum member
  whose name is never accessed as an attribute anywhere in `src/` is declared and never
  referenced […] Iteration-only consumption (`for m in E:`) is not detected; that is the accepted
  under-reporting direction (**never a false red**)"* — and then produced exactly that false red.
  `workflows/publish.py` declares `Lineage` = `SOURCE`/`INFORMED_BY`/`RELATED` and `parse_publish`
  (publish.py:136) validates author-supplied edges **by iterating the enum**
  (`if edge not in {e.value for e in Lineage}`, with `{[e.value for e in Lineage]}` in the error
  string); `flatten_lineage` (publish.py:433) then persists the edge as a `lineage_informed_by`
  scalar and `upsert_plan` (publish.py:240-246) only adds the `SOURCE` run marker on top. Both
  members reachable and functional, both listed inert. That matters because five recent atoms were
  picked straight off this baseline, so a false red spends a session "wiring up" working code.
  FIX: whole-enum iteration now clears every member of the iterated class. Detected shapes —
  `for`/`async for`, all four comprehension forms, and `list`/`tuple`/`set`/`frozenset`/`sorted`/
  `iter`/`reversed`/`enumerate` applied to the class — each matched bare (`E`) or module-qualified
  (`mutations.OpKind` at workflows/service.py:1748, `detectors.Skip` at learning/template_gate.py:119,
  `grill_mod.Channel` at mcp_workflows.py:1078, the three real qualified shapes in this tree).
  Deliberately NOT detected, each on the under-reporting side: `E.__members__` walks and
  `value in E` (both measured at zero occurrences), `getattr(E, name)`, value-lookup `E(value)`,
  local aliases, out-of-repo consumers; `isinstance(x, E)` was excluded after a first pass wrongly
  counted it. Resolution is IMPORT-AWARE, not name-based: `src/` declares seven distinct `Verdict`
  enums and `workflows/verify.py:76` iterates its OWN, so a name-only index would have cleared
  `workflows/judge_contract.py`'s `Verdict.REPLAN` on an unrelated namesake and deleted a real
  finding. DELTA (145 → 140 total, enum 18 → 13, all other kinds unchanged): five surfaces left,
  **zero entered**, each departure verified reachable at a named iteration site —
  `LoopKind.DESIGN`/`.GENERAL`/`.GOAL` via loop/loop.py:39 `frozenset(k.value for k in LoopKind)`,
  whose `KINDS` is a live validator at loop/store.py:279; `Lineage.INFORMED_BY`/`.RELATED` via
  publish.py:136-137. The forbidden-to-raise ratchet is untouched and proven to still bite (a
  temporary `Completeness.PHF12_RATCHET_PROBE` in workflows/containers.py reds the rise check
  naming file + surface, 2 failed / 13 passed; reverted by a targeted edit). The documented cost is
  proven too: an unreferenced member added to the *iterated* `Lineage` is NOT reported. No
  CHANGELOG entry — a repo audit tool plus its committed baseline is not a user-visible surface.
- [2026-08-12][PHF-12] DISCOVERY: a SECOND, distinct false-red class remains and is deliberately
  left unfixed. Value-lookup construction `E(value)` also makes every member reachable when the
  value is externally supplied: `workflows/judge_contract.py:342` does
  `Verdict(str(raw.get("verdict", "")).upper())` on **model-emitted** text, so `Verdict.REPLAN` is
  reachable and still reported. Six of the thirteen surviving enum surfaces sit on classes
  constructed that way (`MemoryTier`/`MemoryScope` memory_record.py:216/218, `DependencyType`
  tasks/models.py:74, `Status` workflows/confirmation.py:177, `Actor` workflows/judge_actors.py:84,
  `Verdict` judge_contract.py:342). Unlike iteration, `E(value)` does not prove reachability on its
  own — a deserializer fed only from internally-written state proves nothing — so telling an
  author/model-supplied value apart from our own round-trip is a judgment call worth its own atom
  rather than a widening of `PHF-12` (§3.2's stated risk is exactly "resist widening the definition
  mid-session"). Named in the detector docstring and LEFT IN THE BASELINE rather than blessed away.
- [2026-08-12][PHF-12] DEVIATION: `PHF-6`'s docstring instructs "do not 'fix' any surface this
  census reports here" and reserves count-driving for SH3.3. This atom changes no `src/` behaviour
  and fixes no reported surface — it corrects the CLASSIFIER, so five lines leave because they were
  never inert. Regeneration is therefore legitimate under the shrink-only rule (the population
  genuinely shrank) and happens in the same commit, by the tool, never by hand.
