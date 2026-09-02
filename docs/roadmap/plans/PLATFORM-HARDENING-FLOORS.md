# PLATFORM-HARDENING-FLOORS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PHF.md`](../atomic/PHF.md) as 15 atomic plan(s).

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
- [2026-08-12][PHF-13] DONE — per-class provenance verdicts for the census's 13 surviving enum
  surfaces, and the ruling that value-lookup `E(value)` must NOT clear a member. This is a
  CLASSIFICATION atom: it makes the census's verdict on each surface correct and evidence-backed and
  builds none of the missing writers. **`PHF-12`'s premise was wrong.** It recorded that
  `judge_contract.py:342` (`Verdict(str(raw.get("verdict", "")).upper())`) runs on model-emitted text
  and so makes `Verdict.REPLAN` reachable. That line lives inside `validate_verdict`, and **nothing
  in `src/` calls `validate_verdict`** — `engine.py:1510` deliberately RESTATES the aggregation rule
  rather than importing it, and only tests import the function. Model output never reaches that
  constructor. Same shape at `judge_actors.py:84` (`Actor(...)`, reached only from
  `resolve_transition`, whose only callers are in `tests/test_workflows_judge_pretier.py`) and
  `judge_contract.py:224` (`enum_cls(str(value))` for `Ratchet`, inside the equally uncalled
  `hints_from_dict`). THE RULE THAT FALLS OUT: `E(value)` proves reachability only when BOTH hold —
  the construction EXECUTES in production, and its value crosses a trust/authoring boundary.
- [2026-08-12][PHF-13] THE 13-MEMBER VERDICT (member → verdict → evidence). Six sit on classes with
  an `E(value)` site; seven have none at all:
  - `DependencyType.REQUIRED_FOR` (tasks/models.py) → **FALSE RED — externally reachable.**
    `tasks/handlers.py:242` calls `registry.create_task(**body)` on `await request.json()`;
    `tasks/native.py:246` coerces via `_coerce_dependencies`; `TaskDependency.from_dict`
    (models.py:74) looks the member up. A `POST /api/tasks` client picks it. The bulk endpoint
    (`handlers.py:164`) is a second path. LEFT REPORTED rather than cleared by an unsound rule.
  - `PromptCache.AUTOMATIC` (llm/prompt_cache.py) → **reachable OUT OF REPO only.** An app sets
    `BrandedProviderSpec(prompt_cache=PromptCache.AUTOMATIC)` (`sdk/provider_helpers.py:65`,
    threaded into `ProviderCapability` at `:334`); `agents/native/runtime.py:775` reads the grade.
    Attribute access in another repo — the census's declared blind spot, the same one that makes
    `sdk_export` 127. Not dead weight; not clearable by any in-repo rule.
  - `MemoryTier.SEGMENT` (memory_record.py) → **genuinely inert.** `MemoryTier(self.tier)` (:216)
    only ever sees rows `to_row` wrote, read back by `from_semantic_row`/`from_episodic_row`
    (`vector_memory.py:1124`/`:1161`); the vault mirror (`memory_vault.render_record`) is
    render-only with no parse-back. No writer produces `"segment"` — sealing into topic clusters is
    unbuilt. TO CLOSE: the §3.5 sealing step that clusters episodics into a segment row.
  - `MemoryScope.WORKSPACE` (memory_record.py) → **genuinely inert.** Same round-trip as above, and
    the one surface that advertises the value throws it away: `mcp_memory.py:41-49` offers
    `scope: global|workspace` on `memory_remember`, and `dashboard/handlers/schedule.py:181` calls
    `svc.write_lesson(rule, category, negative)` — `scope` is never read. TO CLOSE: thread `scope` +
    `workspace` from the lessons POST body into the write (one param, one test).
  - `Status.RESOLVED` (workflows/confirmation.py) → **genuinely inert.** `from_dict` (:177) reads
    what `to_dict` wrote, and that is only `pending`/`expired`: `resolve()` returns a `Resolution`
    and never stamps a status, `on_expiry` returns `EXPIRED`. So `dag_card`'s
    `live = request.status is Status.PENDING` (:480) still offers Approve/Deny after an answer.
    TO CLOSE: stamp `RESOLVED` on the record when a resolution is applied.
  - `Actor.WORKER` (workflows/judge_actors.py) → **genuinely inert.** `Actor(...)` at :84 is inside
    `check_transition`, called only by `resolve_transition`, which no production file calls — the
    actor-authority invariant ships unwired. TO CLOSE: call `resolve_transition` from the engine's
    state-transition path (that is the plan's own intent, not this atom's scope).
  - `Verdict.REPLAN` (workflows/judge_contract.py) → **genuinely inert.** `validate_verdict` has no
    production caller (see above), so no judge response is ever parsed through this contract.
    TO CLOSE: route the judge response through `validate_verdict` instead of `engine.py`'s restated
    rule — one seam, and it retires the duplication `engine.py:1510` documents.
  - `Ratchet.RELAXED` (workflows/judge_contract.py) → **genuinely inert.** Its only construction is
    `enum_cls(str(value))` at :224 inside `hints_from_dict`, which also has no production caller; a
    template's `runtime_hints.judge.ratchet` is never parsed. Note it is loop-bound, so an
    `E(value)` rule cannot see it even syntactically. TO CLOSE: same seam as `Verdict.REPLAN`.
  - `StructuredOutput.JSON_MODE` and `.JSON_SCHEMA` (llm/capabilities.py) → **genuinely inert, and
    worse than unwired.** `llm/capabilities.py:63-64` says apps declare the grade via
    `BrandedProviderSpec.structured_output` — **that field does not exist**
    (`sdk/provider_helpers.py:48-65` carries `prompt_cache` only), so `grounding.py:492`'s live
    reader (`getattr(cap, "structured_output", StructuredOutput.NONE)`) can never see anything but
    `NONE`, and `:496`'s `bundle.structured_output = True` never fires. A live reader of a key
    nobody can write. TO CLOSE: add the field to the SDK spec and thread it at `:325`, or delete the
    claim.
  - `Completeness.INFERRED` (workflows/containers.py) → **genuinely inert.** The only producer
    (`:428-431`) returns `COMPLETE`/`ERROR`/`PARTIAL`; nothing marks a projection as inferred.
    TO CLOSE: mark derived-not-observed sections at the point they are derived.
  - `ItemStatus.SENT` (inbox.py) → **genuinely inert, and deliberately so.** `status` is a plain
    `str` field (`inbox.py:152`) never coerced through the enum, and no writer produces `"sent"`;
    the declaration's own docstring says it "predates the others… it stays because those items
    exist on disk". A legacy on-disk value kept as documentation. TO CLOSE: nothing — keep, or
    delete with the legacy items.
  - `SessionMode.CONTINUOUS` (workflows/models.py:153) → **genuinely inert; the whole class is.**
    `SessionMode` is referenced NOWHERE in `src/`; `FRESH` escapes the census only because
    `judge_contract.py:170` `Isolation.FRESH` shares the attribute name (the deliberate global
    attr-name under-report). The clearest DELETE candidate of the 13 — left in place because
    node-session semantics are live WF2 spec surface and deletion is an owner call, not a
    classification one.
- [2026-08-12][PHF-13] DECISION: the census rule is **NOT widened**, and that is the atom's answer.
  A syntactic `E(value)` clear would clear six classes of which exactly ONE is genuinely reachable —
  measured, not argued: a temporary widening probe took enum 13 → 7 (clearing `MemoryTier.SEGMENT`,
  `MemoryScope.WORKSPACE`, `DependencyType.REQUIRED_FOR`, `Status.RESOLVED`, `Actor.WORKER`,
  `Verdict.REPLAN`) while leaving `Ratchet.RELAXED` flagged, so the rule would be unsound AND
  inconsistent. A false CLEAR is strictly worse than the over-report it replaces: it buries a
  genuine gap inside every internal deserializer, and unlike a false red it passes the shrink-only
  ratchet in silence because the count goes DOWN. And "provably outside" is not a cheap deterministic
  AST rule — the one genuine case needs four interprocedural hops across three modules
  (`request.json()` → `create_task(**body)` → `_coerce_dependencies` → `from_dict`). So the verdicts
  above ARE the deliverable, recorded in the detector docstring (where the flags are produced) as
  well as here.
- [2026-08-12][PHF-13] RAILS, since the ruling is a decision and decisions rot. Three tests in
  `tests/test_inert_surface_baseline.py` (18 passed): `test_value_lookup_alone_does_not_clear_a_member`
  (fixture tree — a member reachable only through `E(row[...])` is STILL reported);
  `test_the_audited_value_lookup_call_sites_have_no_production_caller` (real tree — `validate_verdict`
  / `hints_from_dict` / `resolve_transition` still have none, so it reds the moment one is wired up,
  which is exactly when `Verdict.REPLAN`/`Ratchet.RELAXED`/`Actor.WORKER` must be re-verdicted); and
  `test_the_value_lookup_ruling_is_recorded_in_the_generator` (the verdict vocabulary is present and
  `PHF-12`'s superseded claim is refused). PROBE, proving the pin can fail: the temporary widening
  reds exactly three tests — the new pin plus the two baseline-freshness tests ("3 failed, 15
  passed") — reverted by a targeted edit, never `git checkout --`.
- [2026-08-12][PHF-13] RATCHET UNTOUCHED. Forbidden-to-raise is intact, no counter rose, and the
  baseline was regenerated BY THE TOOL and is byte-identical: 140 total (enum 13, sdk_export 127,
  config / trigger_kind / editable_config 0). Nothing left and nothing entered, because no `src/`
  behaviour changed — the change is the classifier's DOCUMENTED RULING plus its rails. No `web/`
  change. No CHANGELOG entry: a repo audit tool and its committed baseline are not a user-visible
  surface.
- [2026-08-12][PHF-13] DEVIATION: the atom was scoped assuming `PHF-12`'s DISCOVERY was factually
  correct and that the only question was whether "provably external" could be expressed cheaply.
  The audit found the cited premise itself wrong (three of the six sites never execute), so the atom
  delivered the corrected verdicts and a documented refusal to widen rather than a widened detector.
  Per the census's own standing rule the fix for a misclassification is to teach the detector the
  shape — here the correct teaching is that this shape must NOT clear, so the detector is taught in
  prose and in tests, and the baseline is untouched.
- [2026-08-12][PHF-8] DONE: §5's ceiling ∩ profile layer is built and WIRED. New
  `guardrails/registries.py` (enforcer-owned matchers + ordinal scales) and
  `guardrails/ceiling.py` (`Ceiling`, `resolve`, the four archetypes each with ONE compose
  function — `compose_ordinal`/`compose_ruleset`/`compose_gate`/`compose_map` — dispatched on
  ARCHETYPE via `_COMPOSE`, never on scope name, so the six governed scopes are one
  `ScopeSpec` row of data each). Composition happens inside `profile_for_session`, the single
  object every seam already consults, so the rung router, the action denylist, the
  tool-approval pick, the spawn grant and the egress plane are all bounded by one call site.
  Boot: `ensure_governance_boot()` is the FIRST statement of `GatewayOrchestrator.run`, and
  `cli_server` renders a `GovernanceBootError` as WHAT/WHY/FIX + exit 1 rather than a
  traceback. Gate: `make lint` rc=0; full suite `18906 passed, 30 skipped, 12 xfailed, 3
  failed` — the 3 being the known `tests_harness_validate` worktree-cwd failures; 72 new tests
  (`tests/test_guardrails_ceiling.py` 48, `tests/test_guardrails_path_matcher.py` 24); all four
  generated baselines regenerated byte-identical.
- [2026-08-12][PHF-8] MEASURED, before writing a line: all three dispatch seams passed
  `session_key=""` to the guardrails (`gateway.py:_fire_store_trigger`,
  `event_triggers.py::_fire`, and `hooks.py` on a top-level fire). `is_unattended_session("")`
  is FALSE, so every clock/file/webhook/memory-event fire resolved INTERACTIVE — "headless by
  construction" (Success Criterion #7) held in `tests/test_guardrails_profiles.py` and NOWHERE
  else, which is the inverted-inert shape §5 names. Fixed with
  `policy.unattended_dispatch_key(origin)` (prefix `unattended:`, classified unattended by
  construction, carrying the trigger/hook id so a clamp in the SEL names the automation).
- [2026-08-12][PHF-8] V5 HOLDS — driven against a REAL gateway (`PERSONALCLAW_HOME=/tmp/phf8-home`,
  port 10917), not a constructed object. (1) Fail-closed boot: a ceiling with
  `approval.value = "wide-open"` refused to start — exit 1, "PersonalClaw did not start —
  governance could not be established" + WHAT/WHY/FIX naming the three valid values. (2) With a
  valid ceiling (`approval: ask`, `egress: listed`, `paths` closed to `/tmp/phf8-allowed/**`), a
  real event trigger created over `POST /api/triggers` and fired via
  `POST /api/triggers/event:phf8-probe/test` was REFUSED: `denylist: ceiling:paths.allow — action
  path '/tmp/phf8-forbidden/out.txt' is outside the paths this run is confined to`. (3) The
  refused widening is visible in both places: `gateway.log` carries three
  `governance: ceiling narrowed headless.<scope>` warnings and `security_events.jsonl` carries
  `guardrails.governance_boot` (outcome `bounded`, source + digest — the tamper-evidence row),
  three `guardrails.ceiling_clamp` rows with `caller_identity: profile:headless` (which is the
  live proof the trigger resolved through HEADLESS), and the `guardrails.denylist` `blocked` row.
  (4) Not a brick: the same trigger with `/tmp/phf8-allowed/out.txt` fired `ok: true`.
- [2026-08-12][PHF-8] DEVIATION: `HEADLESS.egress_tier` changed `"registry"` → `"all"` at the
  moment the tier gained a real enforcement point. REGISTRY was authored (net/policy.py) for
  "sandboxed code runs that need the common dev registries WITHOUT opening the whole internet" —
  a package-manager posture. The planes that actually exist to enforce a tier on are the agent's
  page fetch (`web/fetch.py::web_fetch`) and the watched-source poll; core has NO code-run egress
  plane (the PHF-1 sandbox providers own no network namespace). Enforcing "registry" there would
  deny every unattended research fetch, every inbox/channel link read and every watched-source
  poll whose host is not pypi/npm/crates — an outage with no UI to undo it, and precisely the
  "enforcing a dead control is an outage" failure. "all" is not unguarded (STRICT: public hosts
  only, no loopback/RFC-1918/link-local, pinned IPs, byte + timeout caps, operator `deny_hosts`);
  the narrowing an operator actually wants for unattended runs is now expressible AND enforced in
  the ceiling (`{"scopes": {"egress": {"value": "listed"}}}`), proven live above.
- [2026-08-12][PHF-8] DISCOVERY, two wired-but-wrong egress controls found while giving
  `egress_tier` a reader. (a) `EgressPolicy.allow_hosts` is ADDITIVE — it waives the private-range
  block, it does not restrict — so `REGISTRY` reached every public host exactly like `STRICT`, and
  `egress_policy_for_tier("listed")` returned `STRICT` outright. Both tiers were decorative even
  for a caller that consulted them. Fixed with `allow_only` (exclusive stance, enforced in
  `net/guard.py` BEFORE DNS resolution, since a lookup is itself an egress signal) plus a named
  `LISTED` profile, which is what makes the ordinal chain `off ⊂ listed ⊆ registry ⊂ all` a real
  containment order rather than a naming convention. (b) `triggers/web_poll.py::_render_headless`
  passed a bare `STRICT`, so an operator's configured `deny_hosts` was honoured on the plain fetch
  tier and IGNORED on the headless tier. Both tiers now take the poll's resolved policy from
  `_poll_egress_policy` (SOURCE → operator layering → run tier).
- [2026-08-12][PHF-8] DISCOVERY: `denylist._glob_match` was the wired-but-wrong path matcher the
  §5 landmine describes — it fnmatched an UN-absolutized item (so a relative `../../etc/passwd`
  dodged a deny of `/etc/**` by not matching as a string) and lowered `**` to `*` (so `~/.ssh/**`
  missed `~/.ssh/sub/key`). Replaced by the one enforcer-owned `registries.path_glob`; both holes
  are now table rows in `tests/test_guardrails_path_matcher.py`, which also carries the mutation
  proof that a `normpath`-on-pattern implementation reds the table.
- [2026-08-12][PHF-8] DISCOVERY, left for its owning plan: the third dispatch seam
  (`gateway.py::_fire_store_trigger` — every clock/file/webhook/chained trigger) has the rung
  routing but NO `enforce_action` denylist gate at all; `grep -n "denylist\|check_action"
  src/personalclaw/gateway.py` returns nothing. So AUTONOMY-GUARDRAILS §1.2's "three dispatch
  seams" is honoured at two. Not fixed here: adding a hard block gate to the clock-trigger path is
  a user-visible behaviour change (a wrongly-blocked cron fire is an outage) that belongs to that
  plan with its own validation, and the ceiling's `paths` scope already has two live production
  readers through the hook and event-trigger seams.
- [2026-08-12][PHF-8] DEVIATION: `SafetyProfile` gained one field, `path_allowlist`, because the
  `paths` ceiling scope's ALLOW plane cannot be expressed as a denylist ("confine this run to
  `~/ws/**`"). It ships with a live reader (`denylist.check_action`, verdict
  `ceiling:paths.allow`) and a live writer (the ceiling parser) in the same commit — no named
  profile sets it, so the default posture is byte-identical to deny-only. `SafetyProfile` is not
  config, so no `config.json` round-trip applies; the ceiling is deliberately NOT PATCH-able
  (`test_ceiling_has_no_config_patch_surface`).
- [2026-08-12][PHF-8] OWNER RULING on the trust root the atom asked for: default
  `$PERSONALCLAW_HOME/governance/ceiling.json`, overridable by `PERSONALCLAW_CEILING_FILE`. On a
  single-user machine no in-process check can make a file unwritable, so the layer buys exactly
  four things, each with a test: no HTTP write surface (absent from `_EDITABLE_CONFIG`, no PUT of
  its own); agent write paths refuse it (`.personalclaw/governance` added to
  `security._SENSITIVE_HOME_DIRS`); no mid-run widening (read once and cached, so a tamper needs a
  restart an operator can see); and tamper evidence (boot SEL row with source + digest). What it
  does NOT buy — OS-level immutability against a process running as the operator — is stated in
  the module header and in `docs/architecture/security.md`, with the root-owned `0444` +
  `PERSONALCLAW_CEILING_FILE` recipe as the real trust root.
- [2026-08-13][PHF-9] DONE: SH6.2 + SH6.3 + the scheduler decision (SH6.1). All three of this
  atom's premises were checked against the tree first, and two needed correcting.
  **SH6.2 (knowledge-merge / unclosed sqlite).** The row scopes this to "close the
  `KnowledgeStore` connection in fixture teardown", but the measured population is far wider: one
  baseline run printed **1,596** `ResourceWarning: unclosed database` lines attributed to **95 test
  files** — knowledge, memory, durability, codegraph, learning, lexicon, session-search, snapshot,
  loop — all the same shape (a fixture builds a store, returns it, nothing closes it, and the
  connection is finalized whenever a later `gc.collect()` reaches it, so the warning lands on a
  bystander test). Fixed at the one seam every store shares instead of ~95 fixtures:
  `tests/conftest.py::_close_sqlite_connections` wraps `connect` on BOTH sqlite driver bindings
  (the stdlib module, which six stores still import directly, and the one `sqlite_compat`
  resolved) and closes what each test opened at that test's teardown. Population **1,596 → 0**.
  Only `ProgrammingError` is passed over on close (a connection opened with the default
  `check_same_thread=True` inside a worker thread may only be closed by that thread); nothing else.
  **DISCOVERY, found by driving the fix rather than reading:** with connections actually being
  closed, `test_inbound_mcp.py::TestToolBehavior::test_empty_stores_answer_honestly` failed with
  `Cannot operate on a closed database` — `knowledge.get_knowledge_store()` memoizes one store in a
  module global resolved from `config_dir()` on FIRST use, so the first test in a worker to touch it
  pinned every later test in that worker to the first test's tmp home. That test had been searching
  an earlier test's knowledge DB all along and passing only because that DB happened not to contain
  its query string. Second fixture, `_reset_knowledge_store_singleton`, clears the global around
  every test (the `_reset_sel_singleton` discipline).
  **SH6.3 (subagent SEL flake) — premise partly wrong.** The row and the flake memory both say the
  SEL call in `_force_reap` *and* `_spawn_with_approval` sit inside a swallowing
  `except Exception`. Only the first does: `_spawn_with_approval`'s audit write
  (`subagent.py:1528`) is already unguarded, and its enclosing `except` covers the approval
  callback, not the write. Fixed the one that is real — the `except Exception:
  logger.exception("Reaper: SEL audit failed…")` around `_force_reap`'s
  `sel().log_tool_invocation` is DELETED, so a genuine audit-write failure raises to
  `_reaper_loop`, which logs it per-agent and continues with the other agents. This also makes the
  file self-consistent: 13 of the 17 `sel()` audit writes in `subagent.py` were already unguarded.
  Proven both ways by `test_a_failed_reaper_audit_write_raises` (SEL raises `OSError` → the reap
  raises): with the swallow restored the new test fails `DID NOT RAISE OSError`, and the pin was
  driven in that failing state before the fix was kept. The row's "give the two tests an isolated
  home" was NOT re-implemented: CRE-8's `_isolate_real_home_writers` already gives every test its
  own tmp home suite-wide, and both tests patch `personalclaw.subagent.sel`, so a third mechanism
  would be duplicate machinery.
  **DISCOVERY (deliberately NOT fixed here).** Three sibling audit-write swallows remain in
  `subagent.py`: `_reconcile_orphans` (line 533), `_note_child_outcome` (1368) and
  `_charge_child_and_check_budget` (1413). Each is nested inside a DELIBERATE fail-open (the budget
  one is caught again by its own outer `except` at 1418, so removing the inner swallow changes
  nothing on its own), so making them raise is a behavioural ruling about whether an unauditable
  guardrail decision should abort the guardrail — a bigger call than this atom, and one for
  AUTONOMY-GUARDRAILS' owner.
  **DEVIATION — the third flake, fixed because `done_when` #1 is otherwise unreachable.** This
  atom names two flakes; a worktree also fails three of eleven tests in
  `tests/test_harness_validate.py` on EVERY branch, so "5 consecutive clean runs" was impossible
  without it. Root cause: `harness/profiles.py` set `VENV_PY = ".venv/bin/python"` — a
  **cwd-relative** interpreter. A worktree has no `.venv`, so `collect_test_ids` could not launch
  (`[Errno 2]`), `validate_refs` reported one "could not collect the test suite" error, and the
  dangling-node-id test could not tell a bad node-id from a failed collection. Replaced with
  `profiles.resolve_python()` (renamed export `HARNESS_PY`): this tree's own `.venv/bin/python`
  when it exists, else `sys.executable`, always ABSOLUTE, never cwd-relative — with an injectable
  root so both branches are pinned by tests. `python -m harness validate` now exits 0 in the
  worktree (`✅ 15 specs valid`), and the trio passes there. Every session in this repo has been
  paying a manual "those three are pre-existing" justification for this; that ends here.
- [2026-08-13][PHF-9] DISCOVERY + the fix that actually closed `done_when` #1: the teardown fixture
  alone left **12** warnings, and they were a PRODUCTION leak, not a test one. Five sites used
  `with sqlite3.connect(...)` — whose context manager ends the **transaction** and leaves the
  connection OPEN (`snapshot.py` 705/1859, `durability/shards.py` 224/252/279, the last opening
  two). Because those run inside worker threads with the default `check_same_thread=True`, the
  fixture's main-thread `close()` was refused with `ProgrammingError`, which is why they were the
  residue. In a long-lived gateway this is a real handle leak: the hourly incremental export calls
  `_sqlite_rows` once per table per inventory entry. Fixed at source with `contextlib.closing`,
  which `snapshot.py` was already using three lines away (this was drift, not a convention).
  `test_sqlite_compat.py::test_no_production_site_uses_a_bare_with_on_a_connection` holds the line
  at population ZERO, proven able to fail by reverting one site (`durability/shards.py:259`).
- [2026-08-13][PHF-9] SH6.1 DECISION — **keep `--dist worksteal`; do NOT switch to `loadgroup`.**
  Measured on this tree, after the root-cause fixes, `python -m pytest` (18,928 passed, 30 skipped,
  12 xfailed, 0 failed each time):

  | scheduler | run | wall | notes |
  |---|---|---|---|
  | `worksteal` | 1 | **155.98s** | machine otherwise idle |
  | `worksteal` | 2 | **154.56s** | idle |
  | `worksteal` | 3 | 192.20s | ⚠️ another pytest selection ran concurrently — excluded |
  | `worksteal` | 4 | **155.71s** | idle |
  | `worksteal` | 5 | 183.80s | ⚠️ concurrent load — excluded |
  | `loadgroup` | 1 | — | **HUNG at 99%** for >20 min with every worker already exited; killed |
  | `loadgroup` | 2 | **244.98s** | idle, completed clean |

  So `loadgroup` costs **+58% wall time** (245s vs a 155s median on an idle machine — ~90s on every
  developer run and every CI job) and failed to terminate once in two attempts. That is not an
  acceptable regression for a capability nothing currently needs, so `worksteal` stays and the plan
  row's escape hatch is answered rather than bought.
  **How a test would be serialized if one ever needs it, stated plainly because `worksteal` ignores
  `xdist_group`:** (1) preferred — make it isolation-safe with a fixture, which is exactly what
  SH6.2/SH6.3 did and why zero tests need serialization today; (2) have the test take the
  cross-process `flock` the repo already uses (`concurrency.single_flight`, isolated per test by
  `_isolate_single_flight_locks`); (3) last resort — run that file in its own invocation
  (`pytest -p no:xdist <file>` or `--dist loadgroup` for that run only) as a separate CI step.
  Deliberately NOT done: adding an `xdist_group` mark. Under `worksteal` the mark is a no-op, so a
  mark added now would be an inert control that reads as protection and provides none — the exact
  defect class `PHF-6`'s census exists to catch. `grep -rn xdist_group tests/` stays empty by
  intent, and this log entry is the record of why.
- [2026-08-13][PHF-9] V6 gate: `make lint` rc=0 (black/isort/flake8/mypy). Full suite **5×
  consecutively, 18,928 passed / 30 skipped / 12 xfailed / 0 failed** every run, with
  `ResourceWarning: unclosed database` at **0** in all five (baseline on this branch: 1,596 in one
  run) and the CRE-8 real-home rail printing "`/Users/golani/.personalclaw` unchanged by this run"
  every time — no `PERSONALCLAW_HOME`/`HOME` override was used, because CRE-8's own docstring
  rejects a global one (setting it made 11 unrelated tests fail on env-precedence rails, which is
  the trap it warns about). The three named flaky tests
  (`test_a_failed_merge_leaves_both_items`, the two `test_subagent.py` SEL tests) passed in all
  five, as did the harness trio that used to fail in every worktree. 5 new tests (2 harness
  interpreter, 1 reaper-audit-raises, 1 bare-`with` ratchet, and the ratchet's own vacuity guard).
  All four generated baselines byte-identical after regeneration except `dag.json`'s derived block,
  which this atom's status change requires. No `web/` change. Both flake memories reconciled, plus
  the harness-worktree memory whose diagnosis was wrong.
- [2026-08-13][PHF-4] DONE: SH2.5 — the child environment at the hook, cron-script and
  bash-action spawn sites is now built by ALLOWLIST (`sandbox.build_child_env`), not inherited.
  One shared helper, used at both real call sites, plus one declared-needs seam
  (`sandbox.env_passthrough`) and `_SENSITIVE_ENV_PREFIXES` as an absolute floor.
- [2026-08-13][PHF-4] 🔴 THE MEASUREMENT the atom asked for, taken before tightening anything.
  A **real running gateway** (`ps eww`, dev instance on :10092) carried **121 environment
  variables**. Classified: 4 that decide what runs (`PATH`, `SHELL`, `PWD`, `TERM`), ~6 locale/TZ,
  ~6 home-equivalents (`HOME`, `TMPDIR`, `USER`, `LOGNAME`, …), 2 PersonalClaw vars
  (`PERSONALCLAW_HOME`, `PERSONALCLAW_BYPASS_LOCAL_NETWORKS`), **1 credential-adjacent socket**
  (`SSH_AUTH_SOCK`), and the remaining ~100 pure launching-shell residue: 19 `CLAUDE_*`/`CLAUDECODE`
  agent-CLI variables, 22 `TOOLBOX_*`, 11 `WARP_*`, 13 `DISABLE_*`, 8 `__MISE_*`, `AWS_REGION`,
  `JAVA_HOME`, terminal/pager/less settings. **Not one of those ~100 is something a hook or cron
  script could plausibly need**, which is what makes the allowlist safe here. The credential
  population is worse than the variable list shows: `config/loader.py:4008` deliberately
  `setdefault`s `.env` credentials (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `PERSONALCLAW_OWNER_ID`)
  into `os.environ` *so that* "spawned children (sandboxed agents, MCP servers, cron-fired
  subprocesses) inherit them" — so on any install with Slack configured, `printenv SLACK_BOT_TOKEN`
  in a one-line hook returned the bot token. `sandbox.py` already refuses exactly those keys for
  cc/strict *agent* children (`_AGENT_DENIED_ENV_KEYS`), so the doctrine was in place and only these
  seams were exempt.
- [2026-08-13][PHF-4] WHAT THE MEASUREMENT IMPLIES FOR REAL SCRIPTS, and where the base was
  widened past the atom's literal list because narrowing there would have been an outage, not a
  tightening:
  * **`PYTHONPATH`.** The PHF-1 ceiling shim prepends `python -m personalclaw._spawn_exec_shim` to
    every bash-provider spawn. Dropping `PYTHONPATH` breaks that import in any layout where the
    package is not on the interpreter's default path — a *total spawn outage*, not a leak. In the
    base, with the reason recorded at the constant.
  * **Proxy + CA variables** (`HTTP(S)_PROXY`, `NO_PROXY`, lowercase forms, `SSL_CERT_FILE`,
    `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`). Absent on the measured host, but
    present on any corporate install, and a script that curls or pip-installs without them fails
    *silently* (hang → timeout) — the worst diagnostic shape. In the base; none are credential-
    shaped by the floor, and all are inherited today, so keeping them widens nothing.
  * **`XDG_*`** as home-equivalents, and `PERSONALCLAW_PORT`/`PERSONALCLAW_WORKSPACE` alongside
    `PERSONALCLAW_HOME` (the atom's "three PersonalClaw vars"): a child that loses these addresses a
    *different install* — the default home on port 10000 — which is a silent wrong-target bug.
  * **`SSH_AUTH_SOCK` is the one thing an operator cannot get back**, by design: it is in the floor,
    so a declaration is refused. A cron script that `git push`es over SSH agent-forwarding will
    break. Deliberate — the floor is what makes the control a floor — and cheap to work around with
    a deploy key. Bash hooks already lost it before this change (the old pattern matched it), so
    only cron scripts are affected.
  * **Everything else a script might want is reachable by declaration**, including the Slack tokens
    (`sandbox.env_passthrough: ["SLACK_BOT_TOKEN"]`). The declared-needs mechanism therefore covers
    every need the measurement found except the deliberate `SSH_AUTH_SOCK` refusal, so no plausible
    breakage is being shipped without a remedy.
- [2026-08-13][PHF-4] DEVIATION (premise correction, `hooks.py`): this plan's row and the atom both
  name `hooks.py` as a spawn site with no `env=`. **`hooks.py` has no child spawn at all** — verified
  by grep (`subprocess`, `Popen`, `create_subprocess*` all absent) and by reading `run_script_hook`,
  which dispatches through `provider.execute(...)` and nothing else. A *hook child* is spawned by
  `bash_provider` (bash hooks) or by `schedule_script.run_script_sandboxed` (`run-script` hooks), so
  the three sites in the `done_when` are reached through **two** call sites, both fixed. `hooks.py`
  is unchanged, and that is the correct diff — editing it would have meant inventing a spawn.
- [2026-08-13][PHF-4] `D1` had NOT shipped, so there was no builder to reuse:
  `apps/backend_runtime.py:140` is still `env = dict(os.environ)` (the plan's re-home note assumed
  `D1` might already be in). The shared helper therefore lives in `sandbox.py` — where
  `_SENSITIVE_ENV_PREFIXES` and the spawn wrappers already are, and which both call sites already
  import — as `CHILD_ENV_BASE_NAMES` + `build_child_env(site=…, extra=…, source=…)`.
  **`D1` should now consume this helper rather than write a second allowlist**; that is the whole
  point of putting it in `sandbox.py`, and `backend_runtime.py` is deliberately left untouched
  because an app backend's *declared needs* (`PORT`, `PERSONALCLAW_APP_NAME`,
  `PERSONALCLAW_APP_DATA_DIR` gated on the `storage` permission) are `D1`'s scope, not this atom's.
- [2026-08-13][PHF-4] CLEAN BREAK: `bash_provider._scrub_env` is **deleted**, with both of its
  constants (`_SECRET_NAME_PATTERNS`, `_KEEP_NAMES`). It was a name-pattern denylist — the shape the
  atom asked to confirm-or-align — and its own comment conceded the false negatives
  ("`MY_GITHUB_PAT` is kept"). `schedule_script`'s one-prefix denylist
  (`not k.startswith("PERSONALCLAW_SECRET")`) is deleted the same way. Preserved intact: the cron
  cfg-file secret channel (a test drives it), `PROTECTED_ENV_NAMES` and the payload-key guard (a
  payload still cannot shadow `PATH`), and the merge ORDER that hazard depends on.
- [2026-08-13][PHF-4] Two rulings worth recording because they are security-control decisions, not
  mechanics: (1) **the floor is enforced where the env is BUILT**, not only where declarations are
  parsed — the parse-time check exists to *warn* the operator which entry was ignored, the build-time
  check is what makes the floor hold no matter how a name reached the set; a test patches the parser
  out of the way to prove it. (2) **The floor also applies to what a call site INJECTS** (`extra`),
  so a trigger payload cannot plant an `AWS_SECRET_ACCESS_KEY` that would redirect a hook's `aws`
  call to someone else's account. The known relaxation, stated rather than hidden: the old
  `_SECRET_NAME_PATTERNS` also filtered *payload* keys ending in `_TOKEN`/`_KEY`, and the floor is
  narrower than that. A payload-supplied token is attacker-owned material, not gateway-held, and
  `PROTECTED_ENV_NAMES` still stops every key that changes *what runs*.
- [2026-08-13][PHF-4] Observability, per the fail-closed-and-observably rule: every spawn logs the
  names it withheld (names only, never values) at DEBUG on `personalclaw.sandbox`, naming
  `sandbox.env_passthrough` as the remedy in the same line, and a declaration refused by the floor or
  malformed logs a WARNING. A test asserts the withheld-name line, because a dropped variable a
  script needed is otherwise indistinguishable from a bug in the script.
- [2026-08-13][PHF-4] Config round-trip for the new field: `SandboxConfig.env_passthrough` (dataclass
  + `_meta`) → `load()` (normalising, blank-dropping) → `to_dict()` (via the existing `asdict`) →
  `_EDITABLE_CONFIG["sandbox.env_passthrough"] = {"type": "str_list", "max_items": 40}` PATCH write
  path → documented in `docs/reference/CONFIG-REFERENCE.md`. **No frontend control, deliberately**:
  every `sandbox.*` sibling is backend-only for the same reason (an operator/deployment knob edited
  by `personalclaw config set`), and `CONFIG-REFERENCE.md` is exactly the surface that documents
  those. Making it a dashboard field would put "which of my credentials reach a hook" one click from
  a chat-driven UI.
- [2026-08-13][PHF-4] Gate: `make lint` rc=0 (black/isort/flake8/mypy, 810 source files). Full suite
  **18,941 passed / 30 skipped / 12 xfailed / 0 failed** in 136s, with the CRE-8 real-home rail
  printing "`/Users/golani/.personalclaw` unchanged by this run" (no global `PERSONALCLAW_HOME`;
  live validation used a throwaway `PERSONALCLAW_HOME` under `/tmp`). Baseline on this branch was
  18,928 + the 13 new tests. Three failures on the FIRST full run, all attributable and all fixed
  rather than excused: `test_config_baseline` (the new field needs the generator re-run),
  `test_spawn_shim::test_sandbox_config_in_to_dict` (an exact-key-set assertion over
  `SandboxConfig` — **extended** to include `env_passthrough`, and its sibling
  `_EDITABLE_CONFIG` test extended with the `str_list` type check), and `test_gate_report`
  (downstream of the stale baseline). All four generators re-run with `PYTHONPATH` set:
  `config-baseline.json` gains exactly the one new field, and `docs-lint-baseline.json`,
  `inert-surface-baseline.json` and the offline agent reference (`python -m
  personalclaw.manifest_reference`) are **byte-identical** — the new config key is not inert
  (`build_child_env` reads it, `_EDITABLE_CONFIG` writes it) and the new doc lines add no dead
  link. No `web/` change: this field is backend-only by design.
- [2026-08-13][PHF-4] VALIDATED AS A USER, driven through the real provider against an isolated
  `PERSONALCLAW_HOME=/tmp/phf4-live` (never the real home — the suite's own CRE-8 rail confirms it
  too). Three secrets planted in the gateway process (`ACME_CLOUD_API_KEY`, `SLACK_BOT_TOKEN`,
  `AWS_SECRET_ACCESS_KEY`), then a bash action running `env | sort`:
  * **127 variables in the parent → 19 in the child** (12 from the allowlist; `/bin/sh` itself adds
    `SHLVL`/`_`/`__CF_USER_TEXT_ENCODING`, and `EVENT`/`CONTEXT`/`PERSONALCLAW_HOOK_*` are the site's
    own injected values). All three planted secrets **absent**.
  * The withheld-name DEBUG line named all 108 dropped variables and pointed at
    `sandbox.env_passthrough` — the diagnosability requirement, confirmed from real output.
  * Then declared `["ACME_REGION", "SLACK_BOT_TOKEN", "AWS_SECRET_ACCESS_KEY"]` by writing
    `config.json` in that home (the real loader, no monkeypatch): the child went to 21 variables with
    `ACME_REGION` and `SLACK_BOT_TOKEN` now present — so the escape hatch works end to end — while
    `AWS_SECRET_ACCESS_KEY` was still **refused**, logging
    "`sandbox.env_passthrough names AWS_SECRET_ACCESS_KEY, which the credential floor refuses`".
    The floor holds against an operator declaration, observably.
- [2026-08-21][PHF-14] DONE — three shrink-only STRUCTURAL ratchets ship beside the existing
  config/inert/docs-lint baselines: `scripts/generate_structural_baseline.py` +
  `structural-baseline.json` + `tests/test_structural_baseline.py`, all three registered as
  SEPARATE gates in `scripts/gate_report.py` (PHF-11's aggregate is now six gates, not three).
  Every number below is a MEASUREMENT of the tree, never an aspiration — `PHF-6`'s
  ship-at-the-measured-population ruling restated in the generator docstring and railed by
  `test_every_threshold_shipped_at_the_measured_population_not_at_zero`.
  * **`structural-size`** — TWO rails, and the split between them is the whole design.
    (1) an absolute **ceiling of 6000 lines** that no file may reach, set one 1000-line STEP above
    the measured max of **5427** (`config/loader.py`); (2) the **population** of the **2500-line
    watch band** — **10 files**, shrink-only. A file ENTERING the band reds (10 -> 11); splitting
    one is how you go green (10 -> 9). Growth WITHIN the band, below the ceiling, is deliberately
    NOT a violation. RATIONALE: a module nobody can hold in their head is where every other kind
    of decay hides, and +40 lines reviews fine every time — but the honest decay signal is HOW
    MANY files are giant, not whether a giant gained three lines. No raw line count is stored in
    the baseline at all, so no routine commit can make the byte-compare gate demand a
    regeneration. The band sits at a real GAP in the distribution, not a round number:
    next-largest non-member is 2294, so **206 lines of headroom**, computed LIVE by
    `watch_band_headroom()` and railed at >= 100 by
    `test_the_watch_band_is_not_sitting_on_a_cliff` (which matters MORE under population counting,
    because the boundary is now the whole trigger). A band at 2000 would have had 12 lines of
    headroom (`memory_service.py` at 1988) and would red an innocent session — which is how a
    ratchet teaches everyone to regenerate baselines.
  * **`structural-import-direction`** — a declared layer order, **66 upward edges across 33
    files**: `core-must-not-import-the-http-surface` 56 edges / 26 files,
    `core-must-not-import-its-own-published-facade` 10 edges / 7 files, `ledger-is-a-leaf` 0
    (PP-4's extraction is clean and this pins it for one line). Deliberately picks directions
    `tests/test_apps_import_boundary.py` does NOT cover — INSIDE core, and the REVERSE direction
    (core importing the facade apps depend on); that test also SKIPS in a standalone clone, so
    this direction had never been guarded at all. Relative imports are RESOLVED, because
    `from ..dashboard import x` never contains the string `personalclaw.dashboard` and a
    grep-shaped rule would be a rail that matches nothing. **Shrink-only replaces the exemption
    list**: the ~26 legitimate entry-point composers (`gateway.py`, `cli_*.py`) are grandfathered
    by the measurement instead of an allowlist, because an allowlist rots and a measured floor
    does not.
  * **`structural-duplication`** — **40 re-derived sites in 36 files** across three families,
    censused fresh rather than assumed: **verdict-type 23** (`Verdict`/`*Verdict` outside
    `workflows/judge_contract.py` — PP-14's un-named primitive), **http-error-envelope-helper
    12** (PL-8 deleted 13 `json_error` clones; twelve survive as `_err`/`_bad`/`_bad_request`/
    `_rpc_error`/`_invalid_path`, detected by the `{"error": {"code"}}` SHAPE so a rename cannot
    dodge the counter), **durable-write 5** (DAS-9's `mkstemp`+`rename` bypass, in 4 files).
- [2026-08-21][PHF-14] CENSUS SCOPE, stated because a drifting number is not a measurement. The
  walk is rooted at `src/personalclaw` and NEVER at the repo root — this repo is routinely
  checked out as ~200 concurrent worktrees (the main checkout carries a `.worktrees/` right now),
  and a walk that wandered into `.worktrees/`/`node_modules/`/`.venv/`/`build/` would census
  ANOTHER agent's tree and drift every run. `_EXCLUDED_DIR_NAMES` is a belt-and-suspenders floor
  and `test_the_walk_cannot_wander_into_a_worktree_or_a_vendor_directory` asserts both the root
  and every excluded name. Census: **915 production `.py` files, 63 sub-packages**. The live
  census count is deliberately NOT stored in the baseline: it changes on every module add, so
  pinning it would make the byte-compare demand a regeneration on routine commits, and a
  baseline people regenerate routinely is a baseline nobody reads.
- [2026-08-21][PHF-14] VACUITY, three checks per ratchet, because a rail that matches nothing
  looks clean and that is how gates die here. (1) the census must see >= `MIN_CENSUS_PY_FILES`
  (800; measured 915); (2) each ratchet must have inspected EXACTLY as many files as the census
  counted, and the count comes from the ratchet's OWN scan (`Scan.inspected`) rather than a
  parallel re-walk that would go stale the moment someone added a filter inside a scan; (3) each
  ratchet must have touched every sub-package on disk — a file COUNT alone cannot see a whole
  package leaving the walk, since dropping one of 63 leaves the total comfortably above any
  floor. The scan memo's cache KEY carries `_parse` and `_src_py_files` so a narrowing
  invalidates it instead of serving a stale clean result; a cache that outlived its inputs would
  be its own vacuity bug.
- [2026-08-21][PHF-14] FALSIFIED, all reds observed live and every mutation re-read to confirm it
  landed (restores from `cp` copies, never `git checkout`):
  * **One real violation per ratchet, at the same time** — a NEW 2,600-line
    `src/personalclaw/phf14_new_giant.py`, a `from personalclaw.dashboard import state` added to
    `ledger/writer.py`, and a `ProbeVerdict` + `_err` added to `errors.py`. ONE
    `scripts/gate_report.py` run reported **`SUMMARY: 3 of 6 gate(s) FAILED, 3 failure(s)
    total`** — three structural gates red BY NAME, the other three still PASS, no red hiding
    another. Size: "`the 2500-line watch-band population ROSE 10 -> 11; new giant(s):
    ['src/personalclaw/phf14_new_giant.py']`". Import: fired BOTH applicable rules on the one edge
    (`ledger-is-a-leaf` AND `core-must-not-import-the-http-surface`). Duplication: named both new
    sites. The pytest side reds identically: **3 failed**, one per parametrized ratchet.
  * **The two size rails, separately.** A band member pushed to 6001 lines reds on the CEILING
    ("`config/loader.py: 6001 lines EXCEEDS the committed per-file ceiling of 6000`") with the
    other five gates green; the new-giant case above reds on the POPULATION. Two distinct defects,
    two distinct failure lines.
  * **A ratchet made to inspect ZERO files reports VACUITY rather than reading clean** — proven
    in both shapes. Root typo'd to `src/personalclaw_TYPO`: all three gates red with "the census
    saw only 0 production .py files (floor 800)". Then `_parse` forced to return `None` with the
    census INTACT: the two AST ratchets red with "inspected 0 of the 915 files the census
    counted" while `structural-size` — which needs no parse — correctly stayed PASS, proving the
    checks are per-ratchet and not one shared flag. Without them the ratchets read PASS on an
    empty walk, which `test_an_empty_walk_fires_the_vacuity_assertion_for_every_ratchet` asserts
    explicitly so nobody deletes the check as redundant.
  * **The forbidden-to-raise doc line deleted reds the rail** — rewrote the generator's
    `FORBIDDEN-TO-RAISE` block into a well-meaning "or to update the baseline"; both markers
    confirmed gone (`grep -c` 0) and `test_forbidden_to_raise_doc_line_is_present` failed. The
    phrase is asserted in the generator docstring, in the test module's docstring, AND in the
    ratchet's own FAILURE MESSAGE (asserted by reading this test file's source) — a doc line
    nobody sees when the gate reds is a doc line that will be dropped.
- [2026-08-21][PHF-14] DELIBERATELY NOT RATCHETED, as decisions with reasons (in the generator's
  `# what this deliberately does NOT ratchet` section, railed by
  `test_the_deliberate_non_ratchets_are_recorded_as_decisions`): **`tests/` file length** (a
  3,000-line test module is not the comprehension hazard a 3,000-line production module is —
  tests are read one function at a time and grow by append, and taxing that taxes the activity we
  want cheapest); **function length / cyclomatic complexity** (needs a metric everyone agrees on;
  picking one badly produces a gate people route around — deferred, not rejected); **`web/`** (the
  design-system ratchets own frontend structure under vitest, and a second Python-side counter
  over `web/src` would be exactly the duplicate gate `structural-duplication` measures); **the
  apps -> core direction** (`test_apps_import_boundary.py` owns it); **total lines of `src/`** (a
  growing project grows; that ratchet reds on every feature and teaches baseline-regeneration);
  **import CYCLES** (a genuinely different defect — initialization order, not layer inversion —
  needing an SCC pass rather than a per-edge rule; its own atom). Also NOT counted as duplication:
  the three `_atomic_write*` wrappers that DELEGATE to `atomic_write` (that is the shape we want,
  and counting them would teach the next reader that wrapping the canonical helper is the defect),
  and route handlers that build an envelope inline (a much larger population with no sanctioned
  alternative yet — the counter is bounded to <=3-statement helpers so the number stays
  actionable).
- [2026-08-21][PHF-14] DEVIATION: `tests/test_gate_report.py` had to change — registering three
  gates necessarily changes the aggregate's arity. Assertions were TIGHTENED, not relaxed: the
  hard-coded `len(results) == 3` became `[r.name for r in results] == _ALL_GATE_NAMES`, so the six
  gates are now pinned by NAME and ORDER and a future registration can neither drop one silently
  nor reorder the table. No CHANGELOG entry — a repo audit tool plus its committed baseline is not
  a user-visible surface (`PHF-6`'s precedent). No `web/` change.
- [2026-08-21][PHF-14] DESIGN CHANGE, and the reason belongs on the record because it is the
  difference between a ratchet and an outage. The size ratchet was FIRST built to freeze each of
  the 10 watch-band files at its exact measured length, and that was wrong. `config/loader.py`
  would have been pinned at 5427 — and it is simultaneously the largest file in the repo AND the
  file the config round-trip contract touches on every new field (dataclass + `_meta` + `load()`
  are all in it: three of the contract's five points). So the gate as first written would have
  RED a correct `natural_voice` boolean addition and demanded a 5,427-line split as the price of a
  toggle. That is the exact "gate people route around" outcome this atom refused to risk for
  function length and cyclomatic complexity, and it was live: an agent was adding that field
  concurrently, and two open PRs touch `chat_runner.py` (another band member).
  **What replaced it:** the band is now a shrink-only POPULATION (count + member identities, no
  lengths), and the ceiling moved from 5427 to 6000 — one 1000-line step above the max.
  * The clause is still satisfied: "shrink-only, never at zero" wants a measured quantity that may
    only decrease, and a count of giants is one. Decay is a new 2,500-line module appearing; +3
    lines on an existing one is ordinary maintenance of the file that by construction gets
    maintained most.
  * **The ceiling had to move, and this is a deliberate deviation from "keep the ceiling at the
    measured max".** The two requirements are arithmetically incompatible: the ceiling HOLDER is
    `config/loader.py` at exactly 5427, so a ceiling AT the max gives that file zero headroom and
    any edit to it reds — including the innocent one the gate is required to pass. Resolved by
    keeping the rail's stated purpose (nothing may reach a step change; a new worst file reds) and
    giving it **573 lines of headroom**. It is forbidden to raise, and it comes DOWN in 1000-line
    steps (`stale_high` emits "lower SIZE_CEILING_LINES to N" via `ceiling_slack_steps` once the
    max drops a full step). A STEP multiple, not `max + N`, so the rendered value stays stable
    while the max drifts and the byte-compare gate stays quiet on routine commits.
  * Two new rails guard the new failure mode:
    `test_the_ceiling_leaves_the_biggest_file_room_for_ordinary_maintenance` (>= 100 lines of
    headroom on the ceiling holder, so a future "tighten the ceiling to the max" cleanup cannot
    ship the outage) and
    `test_an_ordinary_config_field_addition_to_the_largest_file_stays_green`.
  * Import-direction and duplication are UNCHANGED — the objection was scoped to size, and so was
    the fix.
- [2026-08-21][PHF-14] FALSIFIED — THE INNOCENT EDIT STAYS GREEN. Six lines appended to
  `src/personalclaw/config/loader.py` in the shape of a real config field (a default, a `_meta`
  row, a key constant), taking it **5427 -> 5433**: `scripts/gate_report.py` reported
  **`SUMMARY: all 6 gate(s) passed`**, the size ratchet returned **zero failures**, the committed
  baseline still **byte-matched** a fresh render (so no regeneration was demanded either), and the
  live band headroom was unchanged at 206. WHAT IT PROVES: **ordinary maintenance of an existing
  large file is not a violation; a new large file is.** Under the first design this same edit
  breached the ceiling AND churned the stored per-file length — two reds for a correct change.
  The rail that keeps it true is a test, not a one-time observation.
- [2026-08-21][PHF-14] BAND MOVED 2500 -> 2800 ON REBASE, and the trigger was this atom's own
  cliff rail. Rebased onto `main` at `0c9a01f7`, `test_the_watch_band_is_not_sitting_on_a_cliff`
  failed: "only 33 lines of headroom below the 2500-line watch band". Cause, measured:
  `agents/native/builtin_tools.py` is now **2467** lines — it grew ~233 in three days from merged
  atoms (`AG-14` alone added 122) and came to rest 33 lines under the boundary. Nothing was
  violated; the BOUNDARY had stopped sitting at a gap. Incidentally the cleanest possible
  vindication of dropping the per-file freeze in the entry above: the most-edited large file in the
  repo gained 233 lines in three days, and under the first design every one of those merges would
  have red CI.
  **Distribution measured independently over 921 files** (the recommendation I was handed had one
  transposition — 2600 gives population 9 with headroom 17, not population 17):

  | band | population | largest non-member | headroom |
  |---|---|---|---|
  | 2400 | 11 | 2294 `subagent.py` | 106 |
  | 2500 | 10 | 2467 `builtin_tools.py` | **33** (the cliff) |
  | 2600 | 9 | 2583 `workflows/engine.py` | 17 |
  | **2800** | **9** | 2583 `workflows/engine.py` | **217** |
  | 2900 | 8 | 2808 `chat_handlers.py` | 92 |
  | 3000 | 7 | 2992 `handlers/files.py` | 8 |

  **2800 chosen, and the usual reading of the 2400-vs-2800 trade is backwards.** The apparent cost
  of 2800 is that `engine.py` (2583) and `builtin_tools.py` (2467) are not watched — but a band
  member's growth is deliberately NOT a violation, so at 2400 those two would be GRANDFATHERED and
  free to run to the 6000 ceiling unchallenged. At 2800 they sit outside, and crossing 2800 REDS.
  The higher band therefore puts MORE pressure on the two fastest-growing large files in the repo,
  not less. Secondary: 106 lines of headroom is well under one feature's growth for this codebase
  (233 in three days, above), so 2400 would ship a boundary already known to be one merge from
  redding; 2800 gives 2.17x the rail's own floor.
  **Known cost, stated rather than discovered later:** the band's smallest member is
  `chat_handlers.py` at 2808, so it has 8 lines of SHRINK margin — delete nine lines from it and
  the stale-high check asks for a regeneration. Accepted deliberately: the 225-line gap
  (2583 -> 2808) cannot give 200+ lines of margin in both directions, and the remedies are not
  equally priced. A stale-high red is one command in the same commit and is the sanctioned flow for
  a file leaving the giant population; a cliff red asks for the boundary itself to be re-authored.
  Optimise the margin against GROWTH — the direction this ratchet exists to measure.
  **The loophole this opens is closed by protocol, and named as a loophole.** The band is a
  threshold, not a counter, so forbidden-to-raise does not cover it. The generator's
  `# moving SIZE_WATCH_BAND_LINES is a re-authoring, not a regeneration` section states the only
  sanctioned trigger (the cliff rail under 100), requires the measured table in this log, and
  FORBIDS moving the band in response to a population RISE — widening the band to make a new giant
  disappear is the same act as regenerating a baseline to bless a higher number. If a red names an
  entrant, split the entrant.
- [2026-08-21][PHF-14] REGENERATION AGAINST CURRENT `main` — and the ratchet earned its keep in
  BOTH directions on its first day, before it had even landed. Census 915 -> **921** files, max
  5427 -> **5447** (ceiling stays 6000, `ceiling_slack_steps` 0, holder headroom 553).
  * **`structural-duplication` 40 -> 33 sites** (36 -> 29 files). `http-error-envelope-helper`
    **12 -> 4** — `PL-8`'s clone deletion has now merged, and eight of the twelve one-statement
    envelope re-derivations are gone. Only `_bad`, `_disabled_response`, `_invalid_path` and
    `_rpc_error` survive. This is the counter measuring a real cleanup, unprompted.
  * **`verdict-type` 23 -> 24.** `WF2LEA-15` landed `LessonVerdict` at
    `learning/lesson_confidence.py:152` (commit `8529be35`) — a **fifth** verdict dialect, three
    atoms after `WF2LOO-16` reconciled four of them. Verified by reading the class, not by
    trusting the delta. Had this ratchet been on `main` a week earlier, that would have red and
    asked the question at authoring time, which is the entire thesis of `PP-14`.
  * `structural-import-direction` unchanged at 66 edges / 33 files; `durable-write` unchanged at 5.
  Both movements are the point: a counter that only ever goes up is a complaint, and a counter that
  never moves is dead. This one did both within three days.
- [2026-08-21][PHF-14] RE-FALSIFIED at the new threshold, all reds observed live (mutations re-read
  to confirm they landed; restores from `cp` copies, never `git checkout`):
  * **New band entrant** — a fresh 2,900-line `src/personalclaw/phf14_new_giant.py`:
    "`the 2800-line watch-band population ROSE 9 -> 10; new giant(s):
    ['src/personalclaw/phf14_new_giant.py']`", other five gates green.
  * **Ceiling breach** — `config/loader.py` padded to 6001: "`6001 lines EXCEEDS the committed
    per-file ceiling of 6000`", other five gates green. The two size rails still fail separately.
  * **Three simultaneous** — the new giant + a `dashboard` import in `ledger/writer.py` + a
    `ProbeVerdict`/`_err` pair in `errors.py`: **`SUMMARY: 3 of 6 gate(s) FAILED, 3 failure(s)
    total`**, each gate red by name, the import rule firing BOTH applicable directions on one edge.
  * The innocent-edit-stays-green falsification was re-verified by the reviewer on the rebased tree
    (six lines appended to `config/loader.py`, 5447 -> 5452, `structural-size` PASS with the
    baseline still byte-matching), so it is cited rather than repeated here. Its permanent rail
    (`test_an_ordinary_config_field_addition_to_the_largest_file_stays_green`) runs on every suite.
- [2026-08-21][PHF-14] DISCOVERY, caught by the band move and worth the record because it is this
  plan's own favourite defect class: `test_a_new_giant_file_reds_by_naming_the_band_population`
  hard-coded a **2,600**-line probe file. At band 2500 that reds correctly; at band 2800 the probe
  falls BELOW the boundary, so the test would have gone GREEN while asserting nothing — a rail that
  matches nothing looks clean. It failed loudly here only because it also asserted
  `len(failures) == 1`. Fixed by deriving the probe size from the constant
  (`gen.SIZE_WATCH_BAND_LINES + 100`), so the test cannot go vacuous on the next band move. Swept
  the rest of the suite for band-relative literals in executable code: none remain (every other
  size probe is already keyed to `SIZE_CEILING_LINES` or sits far below any plausible band). THE
  GENERAL RULE: a test that probes a threshold must be keyed to the threshold CONSTANT, never to a
  literal that happened to straddle it when the test was written.
- [2026-08-21][PHF-14] DISCOVERY (a gap in the gate, NOT fixed here, with the evidence a future
  atom needs): **`make lint` does not cover `scripts/`.** The target lints `$(PKG) $(TESTS)
  $(HARNESS)` = `src/personalclaw tests harness` only, so this atom's own generator —
  `scripts/generate_structural_baseline.py`, the file that produces a committed baseline — is
  outside the definition-of-done's lint step. An E501 in it passed `make lint` and was caught only
  by the repo-owned pre-commit hook, which does lint staged Python regardless of path. Deliberately
  NOT fixed in this atom: `python -m flake8 scripts` currently reports pre-existing violations in
  `scripts/memory_validate.py` (E127, E501) and `scripts/seed_tasks.py` (six E501s), so adding
  `scripts` to the lint target reds on files this atom does not own, and doing it while several
  agents hold concurrent branches would red their trees too. Cheap to close in its own change: fix
  those two files, then append `SCRIPTS := scripts` to the three lint invocations.
- [2026-08-21][PHF-14] GATE (rebased onto `main` at `0c9a01f7`): `make lint` rc=0 (black/isort/
  flake8 over `src`+`tests`+`harness`, mypy clean on **949** source files) and
  `flake8 scripts/generate_structural_baseline.py` clean too, since `make lint` does not reach it
  (see the lint-scope DISCOVERY above). `scripts/gate_report.py` **all 6 gates PASS**;
  `structural-baseline.json` byte-matches a fresh render. Targeted:
  `tests/test_structural_baseline.py` + `tests/test_gate_report.py` **37 passed**. FULL SUITE
  **23,654 passed / 30 skipped / 12 xfailed / 0 FAILED** in 328s. Residue sweep
  `grep -rn "FALSIFICATION\|if False and\|# PROBE\|MUTANT" src/personalclaw tests` = 16 = 13
  pre-existing + 3 new, all three the FIRST LINE of a test docstring, `src/personalclaw` = zero.
  `git status --porcelain` empty. One commit, seven files, correct author/committer, DCO sign-off,
  no agent trailers. Baseline regenerated with the worktree first on `PYTHONPATH`; the only changed
  file was this worktree's copy and the main checkout stayed clean. No CHANGELOG entry (a repo audit
  tool plus its baseline is not a user-visible surface — `PHF-6`'s precedent). No `web/` change.
  `docs/roadmap/atomic/dag.json` deliberately untouched (fenced under concurrent multi-agent edit).

### 2026-08-22 — `PHF-7` PARTIAL: clauses 2 and 4 shipped, clause 3 was already met, 1+5 recorded unmet

**`PHF-7` stays `todo`.** Its `done_when` carries five clauses; three are now satisfied and two are
not, and the two that are not are the *same* piece of work. Recorded clause by clause with evidence
rather than flipped.

| # | clause | state |
|---|---|---|
| 1 | a gateway boots on the **fake provider** and completes a scripted chat turn with no credentials | **UNMET** — no fake provider exists |
| 2 | `make test-e2e` runs the browser gate offline; a bare `pytest` does not run it | **DONE** this session |
| 3 | every authenticated route is axe-scanned in CI | **already MET on main**, unrecorded |
| 4 | a deliberately raw call that skips the enforced helper reds the gate | **DONE** this session |
| 5 | validated with network off and no provider credentials, runtime recorded | **UNMET** — depends on (1) |

**Why 1+5 were not attempted, measured rather than assumed.** A model provider is registered through
`PROVIDER_TYPES`, and `providers/registry.py` states the constraint twice: a manifest "lands in the
SAME commit as its `PROVIDER_TYPES` entry (the #47 rule)". Vendor model providers live in app bundles
(`bedrock-models`, `openai-compatible`, `ollama-models` …), so a bootable fake provider is either a
new core provider type or a first-party app — a **cross-repo, architecture-shaped decision**, and
clause 5 is just clause 1 validated. Starting it inside this tick would have produced a half-built
provider, which the workspace's own completability amendment exists to prevent. Left for a session
scoped to it.

**Clause 3 was already true and nobody had said so.** `e2e/a11y.spec.ts` runs as the `e2e-a11y` job
in `ci.yml` — a real job, no `continue-on-error`, auth ON — over `ROUTES + SETTINGS_ROUTES +
VIEW_ROUTES`, i.e. every authenticated route plus all 32 settings panels. The atom's `EXT` dep on
`DESIGN-SYSTEM-CONSISTENCY`'s deferred axe tail is satisfied in code by `web/e2e/auth.setup.ts`.

**Clause 4 — `tests/test_model_call_chokepoint_rail.py`.** An unwrapped `ModelProvider` works
perfectly and spends money with no `model_calls.jsonl` audit row, no spend-meter charge and no
budget/breaker policy: silent, and indistinguishable from correct behaviour at runtime. Measured
first — the invariant **already holds**, so the rail ships at zero because zero is the measured
population:

* `wrap_model_call_guard` has exactly **one** call site in `src/`, `provider_bridge:1158`, inside
  `_resolve_from_config_registry`, which wraps "at the single point where the entry name + model are
  known" (its own comment).
* `resolve_provider_for_use_case` has **five** provider-returning paths. Four flow from
  `_resolve_from_config_registry`. The fifth is `_build_native_runtime`, whose docstring records that
  its inference provider "is resolved through the SAME active-model" path — the resolver's own SCOPE
  comment says the same of the ACP CLI branch. So `ALLOWED_BUILDERS` has two entries.

**AST, not regex, and that is load-bearing.** The property is structural — "does this return value
come from a guarded builder" — and four of the five real paths are `x = f(); … ; return x`, which no
text scan can follow. A regex rail here would either miss every real bypass or flag every correct
path. The rail also **refuses to trust its own allowlist by name**: a separate test asserts the wrap
call is really inside `_resolve_from_config_registry`, because an allowlist keyed on a name would keep
passing if that function stopped wrapping.

**Clause 2 — `make test-e2e` (+ `make test-visual`).** The second half was already true by
construction: the browser gate is Playwright, not pytest, and `tests/test_web_render.py:9` states
outright that "the browser itself is never launched". So the work was the target, which runs
`npx playwright test --project=chromium` against playwright's own `webServer` — a gateway with
`PERSONALCLAW_HOME` under `$TMPDIR`, a config carrying only a user name, and readiness taken from the
`PERSONALCLAW_READY` line rather than a port probe. **Verified by running it: 149 passed, 4 skipped.**

**🪤 The visual suite is split out, and the reason is a measurement.** On an **untouched `main`
checkout** in this Darwin dev environment `e2e/visual.spec.ts` is **28 failed / 11 passed** — the
committed platform-qualified baselines have drifted from what this machine renders. Folding an
always-red suite into the default browser gate would make the gate mean nothing, so `test-e2e` passes
`--ignore-snapshots` and `test-visual` owns the screenshot assertions. That drift is real and worth
fixing; it is not this atom's clause and is not silently absorbed either.

**🪤 CORRECTION to this entry's own first draft, and the real finding underneath it.** This entry
originally claimed `visual.spec.ts` "MINTS a baseline for a route that has none, instead of failing",
reported as a silent pass. **That is wrong, and it was published before it was tested.** Measured
directly afterwards by deleting a committed baseline (`terminal-light-darwin.png`) and running that one
test: Playwright writes the actual image and **FAILS** — `Expected:
e2e/__screenshots__/.../terminal-light-darwin.png`, `1 failed`. The six PNGs that appeared during the
enforced run were written by six FAILING tests, not by silent passes. The inference "files appeared,
therefore the gate passed while minting them" skipped the one cheap check that settles it, and
`git clean` removing the strays is what made it look like a pass.

**What is actually true, and it matters more.** Three routes (`artifacts`, `learning`,
`knowledge-graph`) were added to `routes.ts` without capturing baselines, so 6 visual tests fail on a
missing file and 22 more on render drift — that is the 28-failed / 11-passed split measured on an
untouched `main`. The reason nobody noticed: **no CI job runs `visual.spec.ts` at all.** The only
`playwright test` invocation under `.github/workflows/` is `e2e/a11y.spec.ts` (`ci.yml:228`), and there
are **zero `-linux` baselines** — all 32 committed goldens are `-darwin`, so the suite could not pass on
a Linux runner even if it were wired. `DSC-2`'s visual-regression harness is a shipped mechanism with no
gate executing it, which is precisely why its baselines were free to drift; `pwa.spec.ts` is in the same
position. The fix (Linux baselines + a CI job) needs a Linux runner and is not this atom's clause.

**Falsification (3, each on a live line, applied-count confirmed, restored from a file copy).**
(1) Inject `return _SomeVendorProvider(model='x')` into `resolve_provider_for_use_case` → red naming
the AST node at line 864. (2) Make `_resolve_from_config_registry` stop calling the chokepoint → the
"allowlist is not trusted by name" test reds, *and* the never-called floor fires too. (3) Append a
second `wrap_model_call_guard(...)` call site → red reporting **2 call sites** with both paths named.
The rail's own detector is additionally asserted against synthetic modules in both directions, so
"a raw call reds the gate" is a property of the file rather than a claim about one hand-run.

**🪤 A falsification whose anchor does not match is not a falsification.** The first attempt at (1)
inserted after `            return fallback` (12 spaces); the real line is 8. The script printed
`anchor not found`, the mutation applied to nothing, and the suite stayed green at 8/8 — which would
have been reported as "absorbed by a complementary guard" without the applied-count check. Also worth
recording: the first `git commit` was **aborted** by the pre-commit hook (5 × `E501`) and printed why;
a hand-rewrap then created two NEW overlong lines, so the prose was reflowed programmatically and
`flake8` confirmed clean before the commit was retried.

**Gate.** `tests/test_model_call_chokepoint_rail.py` 8 passed (path confirmed to exist first) ·
`make test-e2e` 149 passed / 4 skipped · `make lint` clean · `make -n test` shows plain `pytest`, so
the browser gate stays out of the unit run. `docs/roadmap/atomic/dag.json` deliberately **untouched**:
`PHF-7` is PARTIAL and must stay `todo`.

- **2026-08-22 — `PHF-2` PARTIAL: the tier ships and the macOS half is measured; the Linux fork-bomb
  clause is unexercised. Atom stays `todo`.**
  `ResourceCeilings.policy()` carried the comment *"NPROC (fork-bomb bound) and RSS apply to every
  non-none profile when configured"*. On Darwin that is false, and this atom exists to make it true on
  Linux and audible everywhere else.
  **Measured on Darwin 26.6.1 / CPython 3.13 in fresh children — twice, by the implementing agent and
  then independently — not read from documentation:**
  * **RSS is never installed at all**, which is worse than ignored. `RLIMIT_AS == RLIMIT_RSS` on Darwin,
    the inherited pair is `(INFINITY, INFINITY)`, and `setrlimit` refuses **any** finite value —
    including a soft-only change far below the reported hard limit — with
    `ValueError: current limit exceeds maximum limit`. The shim's `except (ValueError, OSError): continue`
    swallows it. End to end: the policy asked `RLIMIT_AS [67108864, 67108864]` and the child reported
    `(9223372036854775807, 9223372036854775807)`.
  * **pids installs but is not a per-tree bound.** `RLIMIT_NPROC (450, 450)` — deliberately half this
    uid's 901 live processes, so a per-tree limit must permit one fork — succeeded, and then the capped
    child's **first** `fork()` failed `BlockingIOError(35)` EAGAIN while its own tree held one process.
    Darwin counts every process of the real uid: below the uid's live count it denies every fork, above
    it, it bounds nothing.
  * **NOFILE is enforced**: `nofile=137` arrived in the child as `NOFILE (137, ...)`.
  **What ships.** An opt-in `sandbox.cgroup_scopes` tier wrapping the existing NOFILE shim in
  `systemd-run --user --scope` with `TasksMax`, `MemoryMax` and `MemorySwapMax=0`. The scope is the
  **outer** layer, so it contains the shim and the exec'd target — a second layer above the NOFILE floor,
  not a substitute (its own rail asserts the shim survives inside). `MemorySwapMax=0` always rides with
  `MemoryMax` because swap escape defeats a memory cap; a property is emitted only when its ceiling is
  configured, since `TasksMax=0` would be an accidental total denial. `probe_cgroup_scopes()` answers
  once (`lru_cache`), requires a unified cgroup v2 hierarchy AND a usable systemd user session, and never
  raises.
  **The warning claims exactly what was measured and no more:** pids and RSS do not bound the spawn's
  process tree (naming both mechanisms), a fork bomb or memory blowup in an agent child is **not**
  contained, `sandbox.nofile` **is** still enforced, and the remedy is Linux + cgroup v2 + a systemd user
  session with `cgroup_scopes=true`. Exactly one per process, latched — a per-spawn warning would flood a
  tool-heavy turn. **Two vacuity tests fail loudly with "the warning has become a LIE — narrow it"** if
  Darwin ever starts enforcing either ceiling; the NPROC one derives its cap as
  `live_uid_processes // 2` precisely so a per-tree limit would permit the fork and a per-user one cannot.
  **The doctor row splits on CONSEQUENCE, not platform.** `CAPABILITY` tier, so it can never gate the
  core ladder. Unavailable with no ceiling configured is `ok=True` — a permanent red on every Mac trains
  operators to ignore the doctor, and nothing is being silently dropped. Unavailable **while** `max_pids`
  or `max_rss_mb` is set is `ok=False`, the only case where green would hide a configured control that
  cannot do what the operator asked. Both reads degrade toward "not enforced": a probe that cannot prove
  enforcement must not claim it.
  **UNMET — the Linux clause.** *"On a Linux fixture a fork bomb hits pids.max and dies contained"* is
  unexercised: this host is macOS and no container runtime is available (`docker`, `podman`, `nerdctl`,
  `lima`, `limactl`, `colima` all absent — the same measurement that blocks `WF2WOR-12` and `EI-7`'s SC7).
  The construction is unit-tested exactly (both properties, `MemorySwapMax=0`, per-ceiling emission,
  outer-layer ordering), so what is owed is the live containment observation, not the code.
  **A rail only the MERGED tree could see.** `test_spawn_shim.py` holds two exhaustive `sandbox.*` key
  assertions. Neither implementing branch could red them — the `to_dict` set-equality passes while the
  config field is absent, and the config work never ran that suite — so the failure exists only after
  integration. Both were extended with the reason the new key belongs, not weakened. This is the
  argument for integrating and re-gating rather than trusting three green branch reports.
  **DISCOVERY (pre-existing, outside this atom, not swept in): `ResourceCeilings.from_config()` fails
  open through a blanket `except Exception`.** A missing or misspelled `sandbox.*` attribute raises
  `AttributeError` inside it, is swallowed, and **every** configured ceiling silently reverts to the class
  default with only a `logger.debug`. That was visible here as four unrelated ceiling tests failing on the
  tier branch for one absent field. Fail-open is defensible for a ceiling — a spawn must not be blocked by
  a config typo — but a control that quietly stops enforcing deserves at least a WARNING naming the field.
- **2026-08-22 — `PHF-7`: all five clauses now MET, including the live ones. Atom stays `todo` only
  because this code is unmerged** (never flip an atom whose implementation is not yet on `main`); flip
  it when this PR lands.
  **Three of five were already shipped and were not rebuilt:** `make test-e2e` with a bare pytest
  excluded, the axe-per-route job (`ci.yml` `e2e-a11y`), and the raw-model-call chokepoint rail. What was
  missing was clause 1 — the gate booted a gateway with **no model at all**, so the SPA rendered but no
  chat turn could complete and every spec was written to avoid needing one.
  **Clause 1, driven directly** (not inferred from a passing spec): a gateway on a fresh `$TMPDIR` home
  with `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_PROFILE` and `AWS_ACCESS_KEY_ID` stripped from the
  environment booted in **1.9 s**, `/api/model-providers` returned 200 naming `scripted`, and
  `POST /api/chat` returned 200 in **0.88 s** carrying the scripted reply. The real `~/.personalclaw`
  was untouched.
  **Clause 5, runtime recorded:** `e2e/chat.spec.ts` **2 passed in 24.0 s** cold on the fully integrated
  tree (Apple silicon, Darwin 25.6 arm64); the implementing agent measured 29.4 s cold / 9.1 s warm
  including the vite build.
  **Why the provider lives in core rather than a first-party app.** Measured before briefing: the
  gate's `GATEWAY_COMMAND` boots a temp home with **no app installed**, and core CI never checks out the
  apps repo. Every other provider type is registered by its own app, so an app-registered fixture would
  be unreachable from the gate it exists to serve.
  **The gate on the fake is two conditions, both loud typed refusals.** The env var's VALUE is the script
  path, so enabling the fixture and stating exactly what it will reply are one act — there is no "on"
  state with a built-in default, and there is deliberately no `script_path` kwarg (its own test pins
  `inspect.signature` to `["self"]`). It also refuses the real home outright, re-checked at `start()`
  because `PERSONALCLAW_HOME` is read live. Verified live: no env → `ScriptedProviderNotEnabled`; script
  set with `PERSONALCLAW_HOME` unset or pointing at the real home → `ScriptedProviderRefused`; script
  path missing → `ScriptedScriptError`; isolated home → constructs. The gate never calls `config_dir()`,
  because that helper `mkdir`s the home it resolves and would CREATE the thing it is refusing to touch.
  **Not a credential bypass.** `credential=None` is a literal on the one synthesized entry, and a rail
  proves a real provider type still raises `CredentialMissing` under the same opt-in. That rail was
  partly decorative when written — both anti-bypass tests red at a cheap earlier assertion so the
  `pytest.raises` never executed — and was reordered to assert the refusal FIRST.
  **THREE integration defects no single branch could see, and why lint could not help.**
  `pyproject.toml` sets `ignore_missing_imports = true`, so mypy says nothing about a sibling module that
  does not exist yet: both branches were lint-clean and green while the pair was broken.
  1. The two halves named **different env vars** (`PERSONALCLAW_SCRIPTED_LLM` vs
     `PERSONALCLAW_SCRIPTED_MODEL_SCRIPT`) while each docstring claimed they were the same one — so the
     pair could only ever be half-enabled: one registers a type that cannot construct, the other builds
     nothing. Now one variable, plus a **drift rail** asserting the two constants are equal.
  2. The factory called `ScriptedProvider(model=...)` against an `__init__` taking only `self` — a
     `TypeError` at bind time. Now a **build-through rail** constructs via the registry instead of
     reading source. Both rails deliberately avoid the stub: a stub that accepts any constructor call and
     needs no fixture file is exactly what hid both defects, and the stub itself had mirrored the wrong
     contract. Two shipped assertions encoding the impossible contract were restated to the real one
     (the model id is descriptive on the ENTRY; a per-turn override is accepted and ignored) with a note
     saying what they used to claim.
  3. `GATEWAY_COMMAND` did not pin `PYTHONPATH`, so a **worktree run booted the editable install from the
     MAIN checkout**. The first integrated run failed with *"no model provider resolves for use case
     'background'"* and a traceback entirely in the main checkout, because the scripted provider does not
     exist there. The gate now tests the tree it lives in — redundant in CI, decisive in a worktree.
  **`chat.spec.ts` is named in `ci.yml` explicitly.** `make test-e2e` passes no spec list so a new spec
  joins it automatically, but CI lists specs, and a spec not listed runs nowhere. Declared-and-never-
  executed is the failure class these rails exist to catch, so wiring it was part of the change rather
  than a follow-up.
  **DISCOVERY — the "Turn complete" telemetry line is not in the DOM after a turn completes.** Measured
  against the real provider: a fully completed turn rendered the reply, the per-message action row and an
  idle composer, with **no ContextLedger node at all** (zero matches after 30 s). The line is documented
  in-tree as live-only and does not survive the re-render that lands the finished turn, so the spec
  asserts completion two ways that ARE rendered — `Stop` gone with `Send message` restored, and the
  newest assistant turn's action row. A shipped completion signal a user cannot see at the moment it
  matters is worth its own issue.
  **DISCOVERY (pre-existing on `main`, not swept in): `chat_runner.py:4210` raises `UnboundLocalError`
  and MASKS the real error.** `maybe_offer_check_work(state, session, _turn_tool_call_count)` reads a
  local that is only initialized inside the turn loop (~2521), so whenever provider resolution fails
  first it raises inside an unretrieved asyncio task and hides the actual failure. Same family as the
  `_record_model` defect `PCS-7` fixed one variable over; deliberately not touched here because both
  changes land in the same initialiser block and would collide in the merge train.
- **2026-08-24 — `PHF-7` AUDIT: the code landed. All five clauses hold on `main` at `827751b9`, and
  the atom is CLOSEABLE. Two clauses were true only by construction with nothing asserting them —
  both now have rails, and one of the two had ALREADY drifted.**
  The entry above ends "flip it when this PR lands." It landed. Verified by content, not by
  `git cherry`: `feature-phf7-offline-scripted-harness` (the integration branch) reverse-applies
  cleanly against `main`, i.e. every byte of it is present. **Nothing is stranded on any of the five
  `phf7*` branches** — `-scripted-provider` is likewise byte-identical; `-scripted-binding`,
  `-scripted-e2e` and `-chokepoint-rail-and-e2e-target` differ only because `main` is *ahead* of
  them (the binding branch still carries the wrong env var `PERSONALCLAW_SCRIPTED_LLM` and the
  `ScriptedProvider(model=…)` `TypeError`; `main` has both integration fixes plus 100 more lines of
  rails, and `main`'s chokepoint allowlist has gained `_build_acp_runtime`). All five can be deleted.
  **Clause 1 — driven live, not inferred from a green spec.** A gateway on a fresh `$TMPDIR` home
  with every API-key variable stripped booted in **3.16 s**; `/api/model-providers` returned **200**
  with exactly one entry (`Scripted`/`scripted`/`scripted-1`, `credential_status: ok`); `POST
  /api/chat` streamed the fixture's reply and `[DONE]` in **1.27 s**. `~/.personalclaw` mtime was
  byte-identical before and after.
  🪤 **Worth stating precisely: "no credentials present" is a property of the PROVIDER, not of the
  harness.** Nothing asserts the environment is credential-free, and the gate would pass identically
  with `ANTHROPIC_API_KEY` set. That is fine — the real proof is that the scripted entry carries
  `credential=None` and a rail shows a genuine provider type still raises `CredentialMissing` under
  the same opt-in — but "the harness enforces credential absence" would be a false claim.
  **Clause 5 — "network off" is proven better than by unplugging.** `test_scripted_provider.py`
  proves zero network twice: an AST sweep for network-capable imports in the module's closure, and a
  runtime `sys.modules`-delta check that importing `scripted.py` pulls in no networking module. One
  run with wifi off would only have described one machine. Runtime recorded above and in the gate.
  **Clause 3 — the axe scan is on a real `run:` line, and its route list had a THIRD-AXIS hole.**
  `ci.yml:278` is `run: npx playwright test e2e/a11y.spec.ts --project=chromium`, no
  `continue-on-error` anywhere in the workflow, auth ON, iterating `ROUTES + SETTINGS_ROUTES +
  VIEW_ROUTES` × both themes plus five opened surfaces. `routeManifestParity.test.ts` already
  enforced NAV↔manifest parity in BOTH directions with a vacuity floor, and
  `test_e2e_specs_are_executed.py` already refuses to count a spec named only in a workflow COMMENT
  (its `names_spec` vacuity test feeds it a commented run line and asserts the answer flips). So the
  two hazards that usually break this clause were already closed.
  🪤 **The hole was the axis neither rail can see.** `App.tsx`'s `ROUTABLE` set carries routes with
  no nav tile, exempted from the harness by a COMMENT — and the comment said **six** while `App.tsx`
  had **seven**. `mission-control` (a locked dashboard view reached from the command palette) was
  added later, making it an authenticated, routable page with no axe scan, no visual baseline, and
  nothing anywhere recording that as intended. NAV↔manifest parity stays green because
  `mission-control` is in *neither* list. Fixed by making the exemption a **contract**
  (`EXEMPT_FROM_THE_HARNESS`, four new assertions: undeclared-route sweep, stale-exemption sweep,
  exempt-AND-scanned contradiction, and a reason-length floor) so the next such page is a decision
  rather than a silence. Whether `mission-control` should be *scanned* is left as the owner call the
  file already says it is — recorded, not guessed.
  **Clause 2 — the positive half runs; the NEGATIVE half had no rail at all.** "a bare pytest does
  not run it" was true only because the gate is Playwright, so its specs are `.ts`. No test said so.
  New `tests/test_browser_gate_stays_out_of_pytest.py` asserts it from both ends: the `test` target
  is a plain pytest invocation that neither performs nor depends on the browser leg (Makefile
  *parsed*, because `test-e2e` also appears in `.PHONY` and nine comments), and no pytest-collected
  module shells the gate. That second sweep is **AST-based, and that is load-bearing**:
  `test_e2e_specs_are_executed.py` contains the literal `"npx playwright test e2e/ghost.spec.ts"`
  twice inside its own vacuity assertion, so a text scan would flag the repo's best rail and the
  only available fix would be to weaken the scan. The detector is asserted in both directions and
  against that real file by name.
  **A measured drift the new rails caught immediately.** The `test-e2e` recipe's comment named
  `PERSONALCLAW_SCRIPTED_SCRIPT` — **a variable nothing reads**. The gate worked and its stated
  reason for working was false, which is the more corrosive half of a drift because the next person
  wiring a fixture reads the prose. The existing rail pinned the two *Python* constants together;
  the switch is named in four places. Fixed, and `test_the_harness_spells_the_env_var_the_SAME_way`
  now covers `playwright.config.ts` and the `Makefile` too. Writing it surfaced a second question
  immediately: `playwright.config.ts` legitimately NARRATES the dead `PERSONALCLAW_SCRIPTED_LLM`
  while telling the story of the fix. Banning that would have got the rail deleted, so retirements
  are declared in `RETIRED_ENV_NAMES` and `test_no_retired_env_name_is_still_read_by_anything`
  checks the retirement against shipped source — otherwise the allowlist is just a way to silence
  the sweep.
  **Falsifications (6, each on the LIVE line, applied-count confirmed, restored from a file copy —
  never `git checkout`).** (1) `return _SomeVendorProvider(model='x')` planted in
  `resolve_provider_for_use_case` → 1 failed/7 passed naming the AST node at line 723 — **so yes,
  clause 4's "a raw call reds the gate" reds when a raw call is actually planted, not merely when
  the rail is read.** (2) A seventh `ROUTABLE` extra (`ghost-surface`) → the new undeclared-route
  sweep reds naming it. (3) The `ROUTABLE` parser's regex switched to double quotes so `extras` goes
  empty → the vacuity floor reds (the contract test would otherwise have passed on nothing).
  (4) `subprocess.run(["npx","playwright","test"])` appended to `test_web_render.py` → the AST sweep
  reds naming `run() at line 190`. (5) `test: test-e2e` → the back-door test reds. (6) The exact
  drift found on `main` reintroduced → the env-var rail reds; and declaring the LIVE name as retired
  → the retirement floor reds naming `registry.py`/`scripted.py`.
  **Corrections to the brief that commissioned this audit.** Its premise was right — the deliverables
  *are* on `main`, and `dag.json` does carry `"status": "todo"` for the atom (checked: under the
  `plans` key, not a top-level `atoms` one — an early probe of this file reported "no PHF atoms" and
  that probe was wrong, not the brief). Two sub-claims did not survive:
  clause 3's "does the route list cover the authenticated routes" was **already** two-directionally
  railed, so the classic one-sided-inventory failure was not the live defect — the live defect was
  one axis over, on routes in *neither* list; and clause 2's marker hazard ("a marker that matches
  nothing satisfies this trivially") does not apply, because there is no marker at all — the gate is
  a different test runner, which is a stronger separation than a marker and needed a different rail.
  **Gate.** `make lint` clean (black/isort/flake8/mypy, 992 source files). **`make test-e2e`: 470
  passed, 8 skipped, exit 0, 8.7 min** on this Darwin dev machine (chromium already installed) —
  worth recording because the previous entry measured 149/4, so the browser gate has roughly tripled
  and still runs green offline on the scripted provider. `npm run test:web` 474 files / 4985 passed ·
  `npm run build` clean · `npm run typecheck:web` clean.
  `tests/test_model_call_chokepoint_rail.py` 8 passed ·
  `tests/test_browser_gate_stays_out_of_pytest.py` 14 passed ·
  `tests/test_scripted_provider_binding.py` 14 passed (12 before) ·
  `routeManifestParity.test.ts` 8 passed (3 before). `scripts/gate_report.py` all 6 gates PASS.
  **Full suite: 12 failed / 25625 passed / 30 skipped / 12 xfailed in 24:20 under `-n auto` (18
  workers) — and all 12 pass with `-n0` (25 passed in 3:08 and 53 passed in 2:48 across the two
  re-run groups), so all 12 are CPU starvation.** Worth naming precisely, because only half carried
  the signature the playbook says to look for: six were literal `Timeout (>120.0s)` from
  pytest-timeout (`test_gate_report` ×3, `test_inert_surface_baseline` ×3), but four
  (`test_cron_script_ceiling`) failed on their OWN in-test 90 s script budget and one
  (`test_inbound_mcp::test_rate_cap_returns_429_with_retry_after`) failed as `assert 200 == 429` — a
  rate-cap window that simply did not close in time. **A starvation failure does not have to look
  like a timeout**; under contention a test with any wall-clock assumption fails as a wrong VALUE,
  which reads exactly like a real defect. The twelfth was
  `test_loop_worktree_sparse::TestPoolBound::test_batch_creates_every_worktree`, already documented
  pre-existing. Context for the contention: four other agent sessions were running suites on the
  same machine, visible as foreign `pytest` processes in other worktrees — deliberately not killed.
  `docs/roadmap/atomic/dag.json` deliberately **untouched** — the owner flips the atom.


## Execution log — `PHF-7` (offline fake-model E2E harness + a11y rail) — VERIFIED MERGED; two clauses railed; **one owner decision keeps it `todo`**

- [2026-08-24][PHF-7] **All five clauses hold on `main` as-is** — the prior entry's *"flip when this PR
  lands"* condition is technically met. Two clauses were true only **by construction** with nothing
  asserting them; both are now railed. **Left `todo` for one reason: the axe clause carries a
  newly-discovered, undeclared exemption whose resolution is an owner call** (see the mission-control
  finding). Gate at integration: `make lint` 0 (mypy 992), 28 targeted + a 13-test browser-gate rail,
  `typecheck:web` 0 / `test:web` 474 files / 4985 tests, 6-gate aggregate 6/6, probe residue 0.

- [2026-08-24][PHF-7] **The atom-index `done_when` is FIVE clauses; the prior log enumerated four and
  dropped the axe clause** — the exact omission pattern this codebase keeps hitting. All five, per provider:
  (1) gateway boots on the fake provider, scripted turn, no credentials — driven live, boot 3.16 s, one
  `Scripted` entry, SSE reply + `[DONE]` in 1.27 s, real home byte-identical; (2) `make test-e2e` runs the
  gate offline (470 passed / 8 skipped) and a bare pytest does not; (3) every authenticated route
  axe-scanned in CI; (4) a raw call skipping the enforced helper reds the gate; (5) validated network-off.

- [2026-08-24][PHF-7] 🔴 **OWNER DECISION — an authenticated, routable page has no axe scan, and the
  exemption lived in a comment that had already drifted.** `App.tsx`'s `ROUTABLE` set carries non-nav
  routes exempted by a **comment that said six while the code had seven**: `mission-control` (a locked
  dashboard view reached from the command palette) was added later, making it an authenticated routable
  page with no axe scan, no visual baseline, and nothing recording that as intended. The NAV↔manifest
  parity rail stays green because `mission-control` is in **neither** list. This work makes the exemption a
  contract (`EXEMPT_FROM_THE_HARNESS` + assertions) so the next such page is a decision, not a silence —
  **but whether `mission-control` should be *scanned* is the owner's call, and until it is ruled on, clause
  3's "*every* authenticated route" carries a live exception.** That is why the atom stays `todo` rather
  than flipping on the prior note.

- [2026-08-24][PHF-7] **The two by-construction clauses, now railed and falsified.** (2)'s negative half
  ("a bare pytest does not run the gate") was true only because the gate is a **different test runner**
  (Playwright `.ts`) — a stronger separation than a marker, but nothing asserted it.
  `tests/test_browser_gate_stays_out_of_pytest.py` now does, via a Makefile parse + an AST subprocess
  sweep; re-falsified at integration by planting `subprocess.run(["npx","playwright","test"])` into a
  shipped test module → the sweep reds naming it (1 failed, 13 passed), restored from a file copy.
  (4)'s raw-call rail was falsified live: planting `return _SomeVendorProvider(model='x')` in
  `resolve_provider_for_use_case` reds naming the AST node with a WHAT/WHY/FIX message.

- [2026-08-24][PHF-7] **"No credentials present" is a property of the provider, not the harness — stated
  rather than glossed.** Nothing asserts the environment is credential-free; the gate passes identically
  with `ANTHROPIC_API_KEY` set. The real proof is `credential=None` on the synthesized entry plus the
  existing rail showing a genuine provider type still raises `CredentialMissing` under the same opt-in.
  Zero-network is proven twice in `test_scripted_provider.py` (AST sweep of the import closure + a runtime
  `sys.modules` delta) — stronger than one wifi-off run.

- [2026-08-24][PHF-7] **The five stranded `phf7*` branches carry NO content missing from main** (checked by
  reverse-applying each branch's own patch, not `git cherry`): two are byte-identical, three are superseded
  (one still carries the wrong env var `PERSONALCLAW_SCRIPTED_LLM` and a `ScriptedProvider(model=…)`
  `TypeError` that main fixed; another is 20 lines behind on `playwright.config.ts`; the third predates
  `_build_acp_runtime` in `ALLOWED_BUILDERS`). **All five are safe to delete.** A stale env-var name in the
  `test-e2e` Makefile comment (`PERSONALCLAW_SCRIPTED_SCRIPT` → `PERSONALCLAW_SCRIPTED_MODEL_SCRIPT`) was
  corrected in passing.

- [2026-08-24][PHF-7] **A starvation failure does NOT have to look like a timeout** — worth carrying
  forward. Of 12 failures in a contended full run, only 6 carried the `Timeout (>120.0s)` signature; four
  `test_cron_script_ceiling` blew their **own** in-test 90 s script budget, and
  `test_inbound_mcp::test_rate_cap_returns_429_with_retry_after` failed as `assert 200 == 429` — a rate-cap
  window that didn't close in time, which reads exactly like a real defect. All passed under `-n0`. The
  "all failures are timeouts ⇒ starvation" heuristic is too narrow; the reliable test is re-running the
  suspects serially.

- [2026-08-25][PHF-7] **The owner decision is RULED, and the ruling was cheaper than the question.**
  The prior entry left the atom `todo` on one call: should `mission-control` — an authenticated,
  parameterless, routable page — be axe-scanned? **Ruled: scan it.** The criterion says *every*
  authenticated route, and the honest test is whether the harness CAN reach the page, not whether it
  has a nav tile. Measured rather than predicted: `mission-control` is **axe-clean in both themes**, so
  the exemption was never protecting the gate from a known red — it was an unexamined list entry.
  Clause 3 now holds with **no live exception**.

- [2026-08-25][PHF-7] 🪤 **A SECOND drift, found while closing the first — and the more instructive
  one.** Three of the seven `EXEMPT_FROM_THE_HARNESS` entries stated why the page has no **NAV TILE**,
  not why the harness **cannot reach it**: `notifications` ("reached from the header bell, no nav
  tile"), `discover` ("reached from Apps"), `mission-control` ("reached only from the command
  palette"). `App.tsx`'s `renderPage` switches on the **first route segment**, so all three render off
  a bare `#/<id>`. "No nav tile" is not a reason a page cannot be scanned, and reading it as one is
  exactly what kept three authenticated pages unscanned behind a contract that looked complete. All
  three are now scanned in both themes — **6 new scans, all green, 7 passed in 81 s** (incl. the auth
  setup). The four survivors are exempt for a reason the harness cannot satisfy: `loops`/`code`/`app`
  need a record id or an installed app name, and `loop` additionally carries the logged
  overflowing-control-row taste call.
  **Carry forward: when an exemption list is introduced to replace a drifted comment, audit the
  REASONS, not just the membership.** The count was right; three of the reasons were category errors.

- [2026-08-25][PHF-7] **`NON_NAV_ROUTES` is deliberately a third list, not new `ROUTES` entries.**
  `routeManifestParity.test.ts` holds `ROUTES` to an exact mirror of `App.tsx`'s NAV ids, and
  `visual.spec.ts` iterates `ROUTES + VIEW_ROUTES` — so folding these in would both red the parity
  rail and mint three platform-qualified visual baselines nobody asked for. The separate list buys the
  a11y scan alone. The parity contract learned it: a `nonNavRoutes()` parser (throws rather than
  silently narrowing), a stale-entry rail, a NAV/ROUTES disjointness rail (a nav route hiding here
  could lose its visual baseline with nothing reding), and a vacuity floor. The undeclared-route
  message now says outright that "it has no nav tile" is not an acceptable reason.

- [2026-08-25][PHF-7] **The other four clauses re-verified on `main` at `20488b9e`, not assumed:**
  71 passed across `test_scripted_provider.py`, `test_browser_gate_stays_out_of_pytest.py`,
  `test_e2e_specs_are_executed.py`, `test_model_call_chokepoint_rail.py`, with the real-home rail
  reporting `/Users/golani/.personalclaw` unchanged. Clause 1 was also observed **incidentally live**
  in this session's browser run: the gateway log carries
  `Failed to parse suggestions response: SCRIPTED-E2E-OK: this reply came from the offline scripted
  provider` — i.e. the boot bound the scripted provider and served a real turn with no credential and
  no network.

- [2026-08-25][PHF-7] **Both falsifications re-run against the live rails.** (i) The strict/raw-call
  rail: planting `if _force_model_axis: return _SomeVendorProvider(model="x")` into
  `resolve_provider_for_use_case` reds `1 failed / 7 passed`, naming the resolver and the chokepoint;
  restored from a file copy, `git diff` empty. (ii) The bare-pytest rail: appending a
  `subprocess.run(["npx","playwright","test", …])` test to `tests/test_scripted_provider.py` reds
  `1 failed / 13 passed` naming `test_scripted_provider.py: run() at line 666`; restored from a file
  copy. Both mutations were grepped back before the run, so neither red was a no-op.

- [2026-08-25][PHF-7] **What the axe tag set does NOT cover, now stated in the spec itself.** The scan
  runs `wcag2a, wcag2aa, wcag21a, wcag21aa`, which **omits `target-size`** (WCAG 2.2 AA, tag
  `wcag22aa`) — a control under 24×24 CSS px passes here. axe also cannot express intent-level
  questions ("was the user TOLD it failed?"), so a green run means "no machine-detectable AA violation
  on the scanned states", not "accessible". Written into `a11y.spec.ts` so the next reader does not
  over-read a pass.

- [2026-08-25][PHF-7] **Standing DEVIATION restated (not newly introduced): `PERSONALCLAW_E2E=1` is
  not the opt-in mechanism, and should not be added.** The scope wording asks for "skipped unless
  `PERSONALCLAW_E2E=1`". The shipped separation is **by test runner** — the gate is Playwright `.ts`
  under `web/e2e/`, unreachable from any `pytest` invocation — which is strictly stronger than an env
  guard that a stray `export` would defeat, and it is now *asserted* by
  `test_browser_gate_stays_out_of_pytest.py` (Makefile parse + AST sweep over 100+ collected modules)
  rather than merely true. Adding the env var would create a second, weaker gate to keep in sync.
  Likewise "no coverage on the subprocess gateway": the gateway is spawned by Playwright's `webServer`,
  never under `pytest-cov`, so there is no instrumentation to disable.

- [2026-08-25][PHF-7] **The axe clause's real weak point was not coverage, it was CREDIBILITY: a
  skip reads exactly like a pass.** `npx playwright test e2e/a11y.spec.ts` exits 0 whether it scanned
  112 routes or skipped them, so the green check proved the job *ran*, not that anything was
  *measured* — and this job's entire claim is a COUNT. Added `web/e2e/assert-no-route-skips.mjs` plus a
  CI step that reads the JSON report back and holds it to the manifest: **zero route-level skips**, and
  **scans == declared routes × themes**, with the expected number DERIVED from `routes.ts` rather than
  hard-coded (emptying a route list is the one edit that would shrink coverage to nothing and stay
  green). It prints the count, so the CI log now *says* `112 authenticated route scans ran
  (56 routes × 2 themes), 0 skipped — ROUTES=18, SETTINGS_PANELS=34, VIEW_ROUTES=1, NON_NAV_ROUTES=3`.
  Route scans must never skip; the interaction tier legitimately may (4 do — the peek docks have no
  rows in a fresh home), so the checker keys on the `(#/…)` title that only route scans carry.
  Deliberately a `.mjs`, not a `*.spec.ts`, so `test_e2e_specs_are_executed.py`'s glob correctly does
  not treat a report checker as an unexecuted spec.

- [2026-08-25][PHF-7] **Both arms of that checker falsified, and its own vacuity floor too.** Count
  arm: adding a 4th `NON_NAV_ROUTES` entry and re-running against the SAME report reds with
  `route scans ran: 112, manifest declares: 114`; restored from a file copy. Skip arm: flipping one
  `Mission Control (#/mission-control)` result to `skipped` in a doctored copy of the report reds
  naming that route. The checker also fails loudly if any manifest list parses as EMPTY — otherwise it
  would demand 0 scans and pass vacuously, which is the exact shape of the bug it exists to catch.
  Full a11y gate measured end-to-end on this machine: **119 passed / 4 skipped in 153 s**.

- [2026-08-25][PHF-7] Gate: `make lint` clean (black 2070 files unchanged, isort, flake8, mypy);
  targeted `pytest --no-cov` 71 passed (4 rails) + the two falsification runs; `npm run typecheck:web`
  clean; `npm run test:web` **487 files / 5182 tests passed**; `npm run build` EXIT=0 (527 assets);
  the new axe leg **7 passed in 81 s** (recorded runtime). Probe residue 0, `git status` empty,
  real-home rail green, harness gateway confirmed gone (port 10437 free, no worktree processes).
  `docs/roadmap/atomic/dag.json` deliberately **untouched** — the owner flips the atom.

- [2026-08-25][SH1.3] DISCOVERY: **an unbounded drain after `proc.kill()` is not a timeout — it is
  the grandchild's runtime wearing a timeout's name.** Measured, not reasoned: `asyncio`'s
  `Process.wait()` resolves when every *inherited pipe* has disconnected, **not** when the child is
  reaped, so `proc.kill(); await proc.communicate()` waits for whatever grandchild still holds the
  inherited stdout/stderr. A 1s timeout over a `sleep 30` grandchild: **30.02s** with `proc.kill()`,
  **1.01s** with `start_new_session=True` + `os.killpg`. Two corrections to the obvious theory, both
  measured: (1) the block is **not** in `wait_for` — `wait_for` returns at 1.00s every time; it is in
  the post-kill reap (29.0s of a 30.0s total), so the async shape is affected exactly as the sync one
  is; (2) `start_new_session=True` **alone does not fix it** (30.03s) — the group must actually be
  *signalled*. Third discriminator: a spawn with `stdout=DEVNULL, stderr=DEVNULL` is **immune**
  (post-kill reap 0.00s) because there is no inherited pipe to hold; **either** stream as a `PIPE` is
  enough to expose it (DEVNULL+PIPE = 29.02s, PIPE+DEVNULL = 29.02s).
- [2026-08-25][SH1.3] DONE: censused all 17 `.kill()`/`terminate()` files under `src/personalclaw/`
  on three axes — leads its own group? / can fork a grandchild? / does a timeout promise depend on
  it? **5 of ~30 sites needed changing; the rest are provably safe and were left alone.** Fixed:
  `dashboard/handlers/updates.py` `git fetch` (:95), `pip -U` (:359), `git pull` (:564),
  `pip install -e` (:591) — each forks a remote helper or a build backend that inherits the pipe —
  and `loop/gates.py:57`, the worst case (`/bin/sh -c <persisted command>`, so the shell *always*
  forks, under a 180s bound). Each gained `start_new_session=True` plus a new single primitive,
  `cancellation.kill_timed_out`, which routes through the existing `_signal_child` leader rail
  (group when the child leads one, single pid when it does not) and **bounds** the reap.
  **Deliberately NOT changed, with reasons:** the five `updates.py` leaf spawns (`git rev-parse`
  ×2, `git show`, `git diff`, `git status`) fork nothing, and none of them leads its own group — a
  group signal there would have hit **the gateway's own group**, which is why this is a census and
  not a sweep; `frontend.py` `npm ci`/`npm run build` and `self_update.py`'s `git fetch` are
  DEVNULL-on-both and measured immune; `dashboard/handlers/files.py`'s `_git` helper serves only
  read-only plumbing callers; `screencapture` is DEVNULL-both; `personalclaw skills list` /
  `skills mcp uninstall` are leaves (`src/personalclaw/skills/` has **zero** spawn sites);
  `mcp_discovery.py:710` already bounds its reap at 5s; `workflows/effects.py:238` and
  `provisioning.py:256` kill and return without waiting, so they cannot hang; every sync `Popen`
  site (`apps/backend_runtime.py`, `apps/worker_runtime.py`, `cli_run.py`, `local_models/sidecar.py`)
  uses `Popen.wait(timeout=…)`, which reads no pipes; and `acp/transport.py`, `terminal.py`,
  `session.py`, `session_pid.py`, `subagent.py` + `cancellation.py` itself were already
  group-correct. `builtin_tools.py:1712`'s comment ("reaps the shell's children too, which
  `proc.kill()` never did") was right in 2 of ~30 places and never propagated; `kill_timed_out` is
  now the one place it lives.
- [2026-08-25][SH1.3] `tests/test_kill_timed_out_pipe_block.py` pins the **call site**, not the
  mechanism: it drives the real `loop.gates.run_verify_command` against a real forking shell and
  asserts both halves — returned under the bound **and** the process group is empty afterwards
  (fast-because-we-stopped-waiting would leak the tree). Vacuity is explicit: a control test runs the
  shape the fix replaced against the same grandchild and the same bound and must **exceed** it —
  measured **8.07s** control vs **1.02s** fixed over an 8s grandchild with a 1s bound. The census
  rail is bidirectional, so a future blanket sweep that gives a leaf spawn a session it does not need
  reds it. The **spawn-ceiling audit's expected set did not have to move**: SH1.3a keys on
  `file::qualname::callee`, and adding a kwarg moves no key (3 passed, unchanged).
- [2026-08-25][SH1.3] Falsifications, both directions. F1: deleted the live `start_new_session=True`
  in `loop/gates.py` → the call-site test reds with *"spawned the shell into the gateway's own
  process group"* and the rail reds independently; restored from a file copy, same invocation
  **2 passed in 12.10s**. F2: swapped `_signal_child(proc, signal.SIGKILL)` for `proc.kill()` inside
  `kill_timed_out` → reds with *"process group … still has members"* — notably **not** on the timer,
  because the bounded reap alone hides the leak, which is precisely why the group assertion exists;
  restored, same invocation **1 passed**.
- [2026-08-25][SH1.3] SCOPE NOTE — this entry does **not** satisfy SH1.3's own criterion. SH1.3 is the
  two AST tripwires (`test_spawn_ceiling_audit.py`, `test_spawn_preexec_guard.py`); this work only
  *confirmed* the first one's expected set is unmoved by the kwarg. The kill-timeout fix is an
  adjacent hardening improvement filed here because it shares the plan's spawn/teardown surface, and
  it is tagged SH1.3 for locality only. No atom status changes — `SH1.3` is a plan task row, not an
  id in `docs/roadmap/atomic/dag.json` (0 occurrences), so there is nothing to flip.

- **2026-08-26 — `PHF-7` DONE (PR #2092).** Three prior branches had already landed clauses 1-3;
  this pass closed the two that were open and flipped the atom. **Clause 4** held as a property but
  nothing named or pinned it: the existing suite pins the DETECTOR against synthetic trees, which
  proves the detector can detect, not that the real census walk catches a real raw write. The new
  rail splices a planted module into the census WALK so the walk, parse, detector, per-file census
  and regression comparison all really run. Its justification is a measured result, not a claim:
  neutering the detector AND regenerating `structural-baseline.json` (which that file's own
  instructions invite when a counter "legitimately shrank") leaves `durable-write: 0`, at which
  point the existing structural suite reports **31 passed, fully green** while raw writes sail
  through — and the new rail reports 2 failed by name. **Clause 5** had never been run: measured
  **484 passed / 8 skipped / 0 failed, 469s wall clock**, 118 a11y assertions, behind a dead proxy
  with every provider credential unset. The vacuity check is what makes that number mean anything —
  egress was **denied, not merely unused**: the update checker's `git fetch` was refused **537
  times** and the gate stayed green.
  **DEVIATION 1 — "network off" was egress-denied, not interface-down.** Four sibling agents share
  this machine. The import-closure rail independently proves the scripted provider loads no
  networking module at all.
  **DEVIATION 2 — this section's prose names a "strict-mode env flag" that does not exist, and one
  was NOT built.** No `PERSONALCLAW_STRICT*` flag is anywhere in the tree. The `done_when` clause it
  serves is satisfied statically at every commit by shipping machinery; a runtime flag would be a
  second enforcement path for one convention, against the clean-break tenet. The prose should be
  corrected rather than the flag built.
  **Not covered, so not claimed:** `target-size` — the shipped axe tags omit it, and the a11y gate
  fails only on serious/critical.

- [2026-08-26][PHF-14] **NEW ATOM — the ceiling rail fired, and this is the payment.** `PHF-5` and
  `PHF-8` built the size rails and their docstrings predicted this exact arrival by name; it has now
  arrived. Measured on `origin/main`: `config/loader.py` is **5900** lines,
  `scripts/generate_structural_baseline.py` sets `SIZE_CEILING_LINES = 6000` as an absolute ceiling,
  and `tests/test_structural_baseline.py::test_the_ceiling_leaves_the_biggest_file_room_for_ordinary_maintenance`
  asserts `ceiling - max_file_lines >= 100`. Headroom is **exactly 100**, so **one added line reds the
  gate** — and that test's own docstring names this file and this scenario ("adding one boolean toggle
  would red CI and demand a 5,427-line split as its price"). `loader.py` was 5427 when that was
  written; it has grown 473 lines since and spent every line of its headroom.
  **Why this is a floor and not a cleanup.** The config round-trip contract touches `loader.py` on
  every new field, so with zero headroom the file is a chokepoint on all remaining user-facing work.
  `LV-4` is already parked on it by name, and on 2026-08-26 three separate atoms in flight each had to
  be told not to add a line to it. **Explicitly NOT the answer:** raising the ceiling, widening the
  watch band, or regenerating the baseline to clear the red — each retires the rail instead of paying
  it, and the rail is right.
  Scoped in `atomic/PHF.md` with a `<= 5400`-line target (>= 600 lines of headroom, so the next
  several ordinary fields do not each need a refactor), a no-shim clean-break extraction into sibling
  modules under `src/personalclaw/config/` following the `agents/native/decision_tool_defs.py`
  precedent created for this same rail at the 2800-line watch band, a RUNTIME import sweep with a
  stated count as the anti-stranding proof (`mypy` cannot see it — `ignore_missing_imports` is true),
  `PHF-5`'s schema baseline at ZERO drift as the proof that no observable config key moved, and
  `LV-4`'s `learning.identity_report_*` field landed in the same change as the proof that headroom was
  really restored. No deps; startable immediately.

- [2026-08-27][PHF-15] **NEW ATOM — a read that mutates the user's config, and the rail that could not
  see it.** Found while root-causing a CI-only `real-home rail FAILED` on `PP-16`'s branch (#2111).
  Every one of the ~27k tests passed; the run failed on the session rail with `modified config.json
  (23667 bytes)`. Two defects, and **the second is what hid the first**.
  **(1) `AppConfig.load()` is not a pure read.** When `needs_migration` is true it does
  `shutil.copy2(path, path.with_suffix(".json.bak"))` and then `cfg.save()`, best-effort inside
  `load()`'s `try`. So *any* caller that merely reads config **mutates the user's `config.json`** and
  drops a `.bak` beside it — including a module imported for an entirely unrelated reason. The
  delivery mechanism in this instance was fixed in #2111 (`mcp_core._API = _resolve_api_base()` at
  module level became a call-time `_api_base()`, the **fourth** instance of the import-time-constant
  shape `tests/conftest.py::_isolate_real_home_writers` documents as beyond a fixture's reach — and
  fixing it also closed a product bug, since the API base was pinned to first-import config so a port
  change was invisible to every MCP tool call until restart). But **the impurity itself remains**, and
  the next module-level reader re-arms it.
  **(2) The real-home rail under-reports against a metadata-preserving writer.** It detects change by
  mtime-since-session-start, and `copy2` preserves the SOURCE mtime — so the `.bak` looks older than
  the session and is invisible. That is why the rail said "1 entries changed" when two things changed.
  General blind spot, not a quirk of this writer.
  **Both are invisible on a developer machine**, which is why this needs a rail and not just a fix:
  the write only fires when `config.json` exists AND is pre-migration, and a developer's config is
  already migrated, so `needs_migration` is `False` and the rail reads green however broken the code
  is. Reproduced deliberately: a 10,024-byte pre-migration seed became **24k plus a `.bak` carrying
  the seed's mtime** — the same transformation CI reported.
  **Scoped in `atomic/PHF.md`** as: an explicit migration entry point called from startup only, with
  `load()` returning the migrated object in memory and persisting nothing; a runtime import sweep over
  every `load()` caller with a stated count; a fresh-interpreter subprocess rail that `load()` writes
  nothing, paired with a boot rail that the migration **does** still run (that pairing is the vacuity
  leg — "no write" is otherwise satisfiable by deleting the migration); and teaching the real-home
  rail to see a planted `copy2`, with `ALLOWED_RESIDUE` staying `frozenset()`.
  **Deliberately NOT in scope:** deleting the migration (load-bearing for real upgrades) and
  redesigning the rail's single-walk performance choice. No deps; startable immediately.

## Execution log — `PHF-15` (`AppConfig.load()` is a pure read; the real-home rail sees a metadata-preserving write) — 2026-08-26 — DONE

**Both defects reproduced on `origin/main` (`c9fff2f3`) before any change.** A 10,024-byte
pre-migration `config.json` seed, read once by `AppConfig.load()`, became **23,667 bytes plus a
`config.json.bak` carrying the seed's mtime** — the same 23,667 the red CI run on PR #2111
reported. And the real-home detector, driven against a fake root with a planted `shutil.copy2`,
returned `[]`: the `.bak` was **completely invisible**, because `copy2` back-dates mtime and the
`.bak` sits directly under the root, whose own mtime the walk never inspects.

**What shipped.**
- `config/migrations.py` (new, 192 lines) owns both halves. `apply_config_migrations(cfg) -> bool`
  is the migration, moved verbatim, mutating the parsed object in memory and reporting whether
  anything changed. `load_and_persist_migrations() -> AppConfig` is the **only** writing entry
  point: `copy2` the original aside, then `save()`, best-effort, never blocking startup.
- `config/loader.py` **5900 → 5799** (−101; ceiling 6000 with a `>= 100` headroom assertion, so
  headroom went from exactly 100 to 201). `load()` is now a documented one-line delegate over a
  new `load_with_migration_state() -> tuple[AppConfig, bool]`. The flag is not re-derivable by
  diffing the dump against the file — `to_dict()` emits every default, so a perfectly current
  config still differs from its own on-disk form; without the flag the write would fire on every
  boot for every user.
- `cli_server._boot_config()` is the startup seam and the only caller of the writer. It is a named
  function precisely so a test can point `config_dir` at `tmp_path` and drive the real boot step
  instead of asserting on a string in `_gateway`'s body.
- `tests/real_home_guard.py` compares `max(mtime, ctime)` and names the new shape
  `metadata-preserving-write`. **`ALLOWED_RESIDUE` is still `frozenset()`** — fixed at the seam,
  which is the rail's own instruction. Cost documented in the module docstring: ctime is *precision*,
  not performance (`entry.stat()` already returns it, so the walk stays single-pass), but it is
  **broader** — chmod/chown/rename report too. Deliberate: re-permissioning the developer's config
  is not leaving their home alone. Reads move atime, never ctime, so walking or reading the real
  home cannot red the rail (railed by `test_reading_the_tree_does_not_red_the_rail`).

**Rails, and the pairing that makes them mean anything.** `tests/test_config_load_purity.py` (10
tests) seeds a genuinely pre-migration config and asserts `(mtime_ns, contents)` unchanged **and no
`.bak`** — in a fresh-interpreter subprocess, because the original write ran during pytest
**collection** where no fixture exists, and because the property must hold with no conftest in play
(which also makes it deterministic rather than xdist-dependent). "`load()` writes nothing" is
trivially satisfiable by deleting the migration, so it ships with
`test_the_gateway_boot_path_persists_the_migration` (a pre-migration config IS migrated on disk at
boot, and the `.bak` holds the *pre*-migration bytes) and `test_load_still_applies_the_migration_in_memory`.
`test_load_does_not_call_save_even_when_a_migration_applies` detonates `save()` rather than scanning
text for `cfg.save()` — a text scan reads comments and dies at a rename.

**Caller audit — 259 `AppConfig.load()` call sites in `src/` across 147 files (plus 239 in
`tests/`).** RUNTIME import sweep: **1029 first-party modules imported in a fresh interpreter,
0 failures**, against a seeded **pre-migration** config that was byte- and mtime-unchanged
afterwards with no `.bak` and no other entry created in the home. That is the anti-stranding proof
`mypy` cannot give (`ignore_missing_imports` hides a missing sibling module).

**Four sites depended on the write-back. None needed persistence; all four were consuming the
impurity as a fact about the world.** Recorded as DEVIATIONs because each is a behaviour change a
reviewer will otherwise read as a regression:
1. **DEVIATION — deleted `evals/ablation.py::_normalize_config_before_snapshot()` and its call
   site.** It existed only to absorb `load()`'s write-back: the ablation pin loads config *inside*
   the guarded block, so the first ablation in a fresh home used to accuse itself of mutating live
   config. With `load()` pure the helper is dead code; clean break deletes it. Its test is now the
   rail that says the deletion was safe.
2. **DISCOVERY — `dashboard/handlers/core.py::api_personalclaw_config_patch` was silently
   normalizing the user's config on every Settings edit.** The handler read-modify-writes the *raw*
   JSON, deliberately, so a key the dataclasses do not model survives an edit — but a later
   `AppConfig.load()` in the same handler rewrote the whole file behind it. Its test asserted
   `len(data) > 20` and called that "pre-existing behaviour of the PATCH path"; it was the
   write-back's footprint. Post-fix the file keeps exactly its own top-level keys (5, not 22 KB), so
   unmodelled keys now genuinely survive. The assertion states the true property.
3. `test_config_loader.py::test_retired_system_agent_pruned_and_persisted` now asserts **both**
   halves at their real entry points — pruned in memory by `load()` with the file untouched,
   committed by `load_and_persist_migrations()`.
4. `test_config_write_paths_..._validated_mutator.py::test_the_cli_cannot_write_past_the_bounds...`
   read a key back and compared it to the default, which only worked because the write-back
   materialised every field. Now a section snapshot. The file's own `_unchanged()` helper docstring
   (which cited the 22 KB rewrite as the reason it exists) was corrected; the snapshot shape stays,
   because it states the real property regardless of what is materialised.

**DISCOVERY — two AST detectors were anchored on the literal method name `load`, and the
not-found branch of one meant "everything is inert".** `scripts/generate_inert_surface_baseline.py`
returned `set()` when it could not find `AppConfig.load`, so splitting `load()` made the
`inert-surface` gate report **295 bogus inert config surfaces** — a wall of output that reads like a
real regression and buries the one-line cause. Both it and `harness/scanner.py` now anchor on a
named `_LOAD_MAPPING_METHODS` set, and the gate script **raises a sentence naming the fix** instead
of failing 295 times. Probed both ways: the real tree resolves 336 kwarg names; a stub `AppConfig`
with no anchor raises. This fragility predated PHF-15 — any rename of `load()` would have triggered it.

**DISCOVERY — two stale citations in the atom brief, corrected by grep; neither contradicts the
atom.** (a) `tests/test_import_time_config_writes.py` **does not exist on `main`** — it was added by
`9488acaa` on the unmerged `feature-pp16-loop-as-workflowrun` branch, which is not an ancestor of
`c9fff2f3`. Its shape was read from that commit and reused. (b) The brief states the `mcp_core`
delivery mechanism "is already fixed on main (the constant became a call-time `_api_base()`)". It is
**not**: `src/personalclaw/mcp_core.py:111` still reads `_API = _resolve_api_base()` at module scope,
calling `AppConfig.load()` at import time. That fix is also only on the PP-16 branch. `mcp_core.py`
was left untouched — this atom closes the hazard at the seam, which is the correct fix and makes the
call-time workaround unnecessary. Both `mcp_core` and `mcp_artifacts` are parametrized legs of the
new purity rail, so the actual CI entry path is pinned.

**⚠️ CROSS-BRANCH COLLISION the driver must reconcile — surfaced, not decided.**
`feature-pp16-loop-as-workflowrun` carries `tests/test_import_time_config_writes.py`, whose
`test_the_probe_can_see_a_write` asserts that `AppConfig.load()` on a pre-migration config **DOES**
rewrite `config.json`. This atom makes that assertion **false**. Whichever branch lands second, that
test reds. It is a two-line re-point (drive `load_and_persist_migrations()` instead of
`AppConfig.load()`, exactly as this atom's own vacuity leg does), but nothing forces it: a separate
filename was chosen here rather than colliding on a file this atom does not own, so the sibling's
leg will red **silently at merge** unless re-pointed. PP-16's `mcp_core._api_base()` change remains
correct and complementary.

**Falsification (each: live line mutated, `git grep`-confirmed applied, red observed, restored from a
file copy at the literal path — never `git checkout`).**
- Reinstated `shutil.copy2` + `cfg.save()` inside `load_with_migration_state` → **5 red**:
  `test_load_writes_nothing_on_a_pre_migration_config`, both
  `test_importing_a_module_that_reads_config_writes_nothing[mcp_core|mcp_artifacts]`,
  `test_the_gateway_boot_path_persists_the_migration` (the `.bak` stopped holding pre-migration
  bytes), and the ablation rail. The vacuity leg stayed green, as it must.
- Reverted `_touched_ns` to `st.st_mtime_ns` (the pre-fix detector) → `test_a_metadata_preserving_
  copy_is_caught` red with "a copy2'd backup went unreported". Paired with the standalone
  before/after probe: pre-fix report `[]`, post-fix `[('config.json.bak',
  'metadata-preserving-write')]`. That is the proof the rail was **blind**, not merely quiet.
- Neutered `_boot_config()` to `return AppConfig.load()` → `test_the_gateway_boot_path_persists_the_
  migration` red with "the gateway booted and left a pre-migration config.json on disk", proving the
  vacuity pairing is load-bearing rather than decorative.

**Gate.** `make lint` clean (black/isort/flake8 + mypy, 1060 source files). `python scripts/gate_report.py`
**all 6 gates PASS** (`inert-surface` returned to PASS only after the anchor fix). Targeted: 23
existence-checked paths, **615 collected — 605 passed, 1 skipped, 9 xfailed, 0 failed**. Full suite:
**27,596 collected — 27,554 passed, 30 skipped, 12 xfailed**, and the real-home rail reported
verbatim: `real-home rail: /Users/golani/.personalclaw unchanged by this run.` The single failure in
the first full run was mine and root-caused, not a flake:
`test_surface_layers_safe_mode.py::TestTheCliFlag::test_the_entrypoint_latches_before_it_boots_anything`
does `src.index("AppConfig.load()")` on `_gateway`'s source to prove the safe-surfaces latch precedes
the first config read; the anchor moved to `_boot_config()`. Re-pointed, with an explicit
presence assertion so the next move fails with a sentence instead of `ValueError: substring not found`.
24 passed at `-n0`.

**Every `done_when` clause met.** `loader.py` ends **below** its starting count (5799 vs 5900).

- [2026-08-28][PHF-14] **THE 2026-08-21 `[PHF-14]` ENTRIES ABOVE DESCRIBE DIFFERENT WORK — read this
  before reading them.** `PHF-14` is an **ID COLLISION**. The ~15 entries dated 2026-08-21 and tagged
  `[PHF-14]` belong to the commit that built the three structural ratchets (`f6281199`, "PHF-14
  structural ratchets"); `git show f6281199:docs/roadmap/atomic/dag.json` contains **no `PHF-14` atom
  at all**, and that commit touched `config/loader.py` **zero** times. This atom was created five days
  later, on 2026-08-26 (`0b40b3f4`), for the decomposition, and reuses the id. So none of those
  entries is this atom's history and none of its clauses was partly done by them. `scripts/
  generate_structural_baseline.py`'s own module docstring still opens "(PLATFORM-HARDENING-FLOORS
  PHF-14)" and refers to the RATCHETS, not to this split — left as-is, because renaming a citation to
  a since-reused id is its own change.
- [2026-08-28][PHF-14] DONE — `config/loader.py` decomposed into five per-domain sibling modules.
  **Measured, because the number in this plan's own 2026-08-26 entry is wrong.** That entry (and the
  atom's `scope`/`done_when`) say **5900** lines and "headroom is exactly 100". On `origin/main` at
  `06861fc2` the file is **5652** lines, so headroom was **348**, not 100. Three other numbers were
  in circulation the same day (5581, 5619, 5647), each correct when taken. The atom was never
  one-line-from-red; it was ~348 lines from red. The floor argument is unaffected and the target was
  met with room: **5652 → 4285 (−1367, −24.2%)**.
  **The split, by domain and not by line count** (`credentials.py`/`SH-2` as the precedent — clean
  break, no re-export shim, importers updated in the same change):
  `coercion.py` (131) the shared `_meta` + value-coercion leaf; `validation.py` (212) the JSON-Schema
  pass over raw config data; `safety.py` (409) egress/budget/breaker/autonomy/guardrails/auth/
  security/sandbox; `learning.py` (518) loops/learning/feedback/planning/evals/proactive; and
  `external_access.py` (232) the access + capture surfaces. **`coercion.py` is the enabling move, not
  a line-count dodge**: a dependency census showed every one of the ~60 `*Config` dataclasses depends
  on exactly one loader symbol, `_meta`, so leaving that 3-line function behind would have forced
  every extracted section to import its own parent (`loader` → `safety` → `loader`) and **no section
  could have moved at all**. `AppConfig`, `load()`, `to_dict()` and the path helpers (`config_dir`,
  `config_path`, `env_path`, `workspace_root`) all deliberately STAY — ~290 tests monkeypatch
  `"personalclaw.config.loader.config_dir"` and friends by string, and moving those names would have
  silently detached every one of those patches.
  **Rails, measured:** ceiling `SIZE_CEILING_LINES = 6000` and band `SIZE_WATCH_BAND_LINES = 2800`
  both UNCHANGED. `structural-baseline.json` regenerates **byte-identical** — stronger than the
  `done_when`'s "regenerated only for the moved-file rows", and the reason is that the size block
  stores no raw line counts: `loader.py` stays a band member at 4285, no new module comes near 2800
  (largest is 518), and `ceiling_slack_steps` stays 0 because `workflows/controller.py` (5287, which
  I do not touch) becomes the holder and is still inside the top step. Watch-band headroom unchanged
  at **179** — note that rail's docstring claims 217; the live figure has been 179 for some time.
  `loader.py` also carried **zero** rows in the import-direction and duplication ratchets, so no
  moved chunk could carry an upward edge or a duplicate site into a new file.
- [2026-08-28][PHF-14] EQUIVALENCE, proven differentially rather than re-asserted. Captured
  `AppConfig.load().to_dict()`, `JSON_SCHEMA` and the flattened `SCHEMA_REGISTRY` from `origin/main`
  BEFORE touching anything, then re-rendered after: **byte-identical on all three** (10113 / 182141 /
  190948 bytes; 393 registry entries), and identical again after `black`/`isort`. Not vacuous —
  changing one default in a moved section (`evals.study_default_k` 5 → 6) flips all three to
  non-identical. `tests/test_config_baseline.py` (PHF-5's schema baseline) green, and the generator
  writes the committed file unchanged: **ZERO schema drift**.
  This mattered more than it looks. `config/schema.py::_resolve_field_type` resolves a STRING
  annotation with `eval(tp, vars(config.loader))` behind `except Exception: return str` — so a moved
  section that used `from __future__ import annotations` would render every one of its fields as
  `"string"` in the JSON schema **with no error anywhere**. The three section modules therefore omit
  that import deliberately, each says why in its docstring, and
  `test_a_section_module_never_postpones_its_annotations` pins it shut. Three quoted annotations
  already existed in `AppConfig` (`durability`, `evals`, `proactive`) and two of them name moved
  classes; they still resolve only because `loader.py` imports those names as a consumer.
- [2026-08-28][PHF-14] DISCOVERY — **the split silently narrowed an ERROR-level gate, and nothing
  red.** `harness/scanner.py::check_config_four_points` hardcoded `config/loader.py` for BOTH halves
  of its check: the `_meta` field declarations and `AppConfig.load()`'s mapping. Those used to be one
  file. After the split it kept passing while checking **280 of 367** fields (76%) — the 87 moved
  fields became invisible — and a diff touching only `config/safety.py` did not run the check at all.
  Proven by measurement, not inspection: with the `max_pids` load mapping deleted, the OLD scan
  reports **0** findings and the new one reports **1**, on the identical tree. Fixed by reading
  declarations from every `config/*.py` while keeping the mapping anchored in `loader.py` (where
  `AppConfig` lives), and by triggering on any config module. Breadth restored to **367**.
  A gate that narrows without redding is worse than one that is absent, because the green reads as
  coverage. `scripts/generate_inert_surface_baseline.py::_inert_config_surfaces` looked like the same
  defect and is NOT: it derives its leaf list from the live `AppConfig` dataclass tree via
  `generate_config_baseline`, so it is location-agnostic. Its baseline regenerates byte-identical.
- [2026-08-28][PHF-14] DEVIATION — two source-text rails had to follow the code they guard, and both
  named their own remedy. (1) `test_the_loader_imports_jsonschema_unconditionally` greps `loader.py`
  for `^import jsonschema$`; `_validate_config_data` and that import moved to `validation.py`, so the
  rail is re-pointed and renamed `test_the_validator_imports_jsonschema_unconditionally`. Pointed at
  `loader.py` it would have "failed" on the move and been fixable only by re-adding an import
  `loader` does not use — i.e. by writing the shim the tenet forbids. (2)
  `test_an_ordinary_config_field_addition_to_the_largest_file_stays_green` asserted
  `biggest == "src/personalclaw/config/loader.py"`, which the split falsifies by design; its own
  failure text says "Re-point this test". I did better than re-point it: the holder is now **derived**
  and the hard-coded path is gone, with the existing band-member assertion keeping the derivation
  honest. A hard-coded holder prices every legitimate split as a red whose cheapest fix is editing
  that line — the same defect the band's docstring already records for a hard-coded 2,600-line probe
  that fell below the moved band. The ceiling and the band themselves are untouched.
- [2026-08-28][PHF-14] CLAUSE 4 (the "add a NEW field end-to-end as proof" clause) — **discharged by
  `LV-4`, plus a rail, because the named field is spent.** The clause asks for "LV-4's
  `learning.identity_report_*` field" to land in this change; `LV-4` shipped
  `learning.identity_report_cadence` separately in `06861fc2`, which **is** the commit this branch
  forks from, so adding it again is not available. I did not substitute an unrelated invented field:
  the strongest candidate (`evals.study_low_power_cases`, promoting `evals/studies.py`'s arbitrary
  `LOW_POWER_CASES = 3` and closing EVALUATION-SUBSTRATE's open Owner decision A) needs 4 readers
  retargeted and a field threaded through `StudyRegistration`, which is a semantic change to the eval
  substrate's verdict labelling and does not belong inside a 1400-line refactor. Filed as its own
  work instead.
  What the clause actually protects against is a decomposition that leaves the file unable to carry a
  field, and a one-off new field demonstrates that **once**. `tests/test_config_section_modules.py`
  demonstrates it **on every run, for all 87 moved fields at once**, and does it end-to-end for a real
  user-facing field that now sits on the far side of the seam:
  `test_lv4s_field_still_reaches_all_five_points_from_its_new_home` asserts
  `learning.identity_report_cadence`'s declaration+`_meta` in `config/learning.py`, its `load()`
  mapping, its `to_dict()` output, its `_EDITABLE_CONFIG` entry, and the `api.patchConfig` call in
  `IdentityReportPanel.tsx`. It is the same five points the clause wanted, asserted on the post-split
  tree, and it keeps guarding the seam afterwards.
  This also closes the two contract points `test_config_roundtrip.py` provably misses. Measured:
  deleting the `learning.identity_report_cadence` allowlist entry leaves `test_config_roundtrip.py`
  at **17 passed** while the new file reds — so the gap is real and is now covered, not duplicated.
- [2026-08-28][PHF-14] FALSIFIED, every red observed live with its count, each mutation `git grep`-ed
  back to prove it applied, and each restored from a file copy at the literal path.
  (1) **A dropped `load()` mapping reds.** Deleted `max_pids=...` from `AppConfig.load()`:
  `tests/test_config_section_modules.py` **2 failed / 15 passed** (was 17 passed) — the static rail
  and the behavioural save→load rail both caught it, and the `learning`/`external_access` params
  stayed green, so the sweep is per-module and not smeared.
  (2) **A dropped `_EDITABLE_CONFIG` entry reds.** **1 failed / 16 passed**, with
  `test_config_roundtrip.py` **17 passed** on the same tree (the documented 3-of-5 gap, measured).
  (3) **A stranded import — and the brief's premise about this is only half right, which is worth
  the record.** The claim is that `mypy` cannot see a stranded first-party import because
  `ignore_missing_imports` is true. Two mutations, opposite results:
  a stranded **attribute** (`from personalclaw.config.loader import EXTERNAL_ACCESS_SURFACES`, a name
  that no longer lives there) → **`mypy` REDS with 1 error**, and the **runtime sweep is blind: 0
  failures**, because that importer's import is *function-local* and importing the module never
  executes it. A stranded **module** (`config.coercion` → `config.coercion_helpers`, at module level)
  → **`mypy`: "Success: no issues found in 1088 source files"**, and the sweep reds with **127
  failures**. So the two are complementary and neither alone is sufficient: `mypy` misses a missing
  MODULE, the sweep misses a function-local import. Also read the sweep's DISCOVERY count, not just
  its failure count — it fell 1056 → **838** under mutation (2), because `pkgutil.walk_packages`
  cannot descend past a package whose import raises.
  (4) **The equivalence proof is not vacuous** — see the differential entry above.
- [2026-08-28][PHF-14] GATE (branch `improvement-phf14-decompose-loader`, off `origin/main` at
  `06861fc2`): `make lint` rc=0 (black 2203 files, isort, flake8, **mypy 1088 source files clean**);
  `tests/test_structural_baseline.py` at `-n 0 --timeout=900` **31 passed**; the config contract
  cluster — `test_config_roundtrip.py`, `test_config_load_purity.py`, `test_config_baseline.py`,
  `test_config_schema.py`, `test_config_section_modules.py`,
  `test_config_validation_is_not_optional.py`, `test_inert_surface_baseline.py` — **84 passed**, real-
  home rail clean; runtime import sweep **1056 modules, 0 failures**. No `web/` change, so no web
  chain. `PERSONALCLAW_HOME` left unset throughout (exporting it fakes ~12 unrelated failures); the
  venv is pinned to `isort>=8,<9` because a fresh resolve takes 9.0.1 and reds 8 files on a clean
  tree.
- [2026-08-28][PHF-14] BLOCKED-ADJACENT, resolved without guessing, recorded because the next atom
  hits it too: **"mark the atom done in `atomic/PHF.md`" and "do NOT touch `dag.json`" are jointly
  unsatisfiable.** `tests/test_roadmap_atomic_status_sync.py::test_every_row_mark_agrees_with_the_dag_status`
  asserts every atomic-table row mark agrees with `dag.json`'s status for that atom, so flipping
  `PHF-14`'s row to `✅` while `dag.json` still says `todo` reds the suite (measured: 1 failed, 6
  passed). The repo's real convention is that both move in the same edit — `PHF-13` carries
  `✅ (#PENDING)` and passes, which means its `dag.json` status is already `done`. Resolution: the row
  stays `⬜` so the rail stays green and `dag.json` stays untouched as instructed, and the dated status
  goes in the atom's SECTION BODY (`**Status:** ✅ implemented 2026-08-28 (#PENDING)`), which the rail
  does not parse. The body line says explicitly that the `⬜` is a sync artefact and that both marks
  flip together at merge, so nobody reads the row as a verdict on the work. Not a silent skip and not
  a weakened gate: the honest state is "implemented, not yet marked", and that is what is written.

## Execution log — `PHF-14` (Decompose config/loader.py below the ceiling) — **DONE**

- [2026-09-02][PHF-14] DONE, flipped by the rev-18 reconciliation with fresh evidence (the
  2026-08-27 NOT-DONE note had gone stale). Measured on `main` @`01e25c848`: `config/loader.py`
  is 4358 lines (ceiling 5400, headroom 1042); extracted siblings `coercion.py`,
  `external_access.py`, `learning.py`, `safety.py`, `validation.py` live under
  `src/personalclaw/config/`; zero re-export shims in `config/__init__.py`;
  `tests/test_structural_baseline.py` 32 passed.
