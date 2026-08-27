# DESKTOP-COMPUTER-USE

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DCU.md`](../atomic/DCU.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Desktop Computer Use — Native GUI Automation via the Accessibility Tree

**Status:** DESIGNED — created 2026-08-05. An accessibility-tree-driven GUI automation design
(~13k LOC of new capability). This plan covers **desktop application automation**, which no existing
plan owns: BROWSE-AUTOMATION (#…) is deliberately browser-only.

**Created:** 2026-08-05
**Wave:** 3 — a large, security-heavy capability. **Hard-gated on AUTONOMY-GUARDRAILS** (the safety
floor + a keystone enable) and on PLATFORM-HARDENING-FLOORS §1 (resource ceilings on the driver
subprocess).
**Depends on:** AUTONOMY-GUARDRAILS (safety profiles, SEL audit, egress irrelevant here but the
approval ladder applies), PLATFORM-HARDENING-FLOORS §1 (the driver runs as a ceilinged spawn),
SECURITY-HARDENING (this is a class-B/S capability that touches the OS input layer).

**Scope:** let the agent **read and drive the operator's own desktop applications** through the OS
accessibility layer — enumerate on-screen apps, walk one app's window into an indexed accessibility
tree, then act on an element **by index** (press, type, set value, scroll, named action), with a
coordinate path reserved for canvas/custom-drawn UI. macOS first; Windows/Linux report a typed
refusal until their drivers land. **Soul guardrail:** element-targeted, non-pointer input is the
DEFAULT and the only thing on by default; the real cursor never moves *by accident*; the whole
capability is OFF until the operator turns it on out-of-band in a file the agent can neither read
nor write. This is a power tool with a physical-world blast radius — every default is the safe one.

---

## 1. Why this is a separate plan, and why the accessibility-tree approach

**BROWSE-AUTOMATION is browser-only** (a `browse` action provider over headless Chromium). Driving
the operator's *desktop* apps — Mail, a native IDE, a design tool — is a different substrate: it
speaks to the OS accessibility API, not a DOM. Verified: no PersonalClaw plan mentions accessibility/
AXPress/desktop-automation (grep across all 67 plans, zero hits). So this is genuinely new capability.

**The approach (validated against the open `open-codex-computer-use` MCP contract):** act on an
**indexed accessibility element**, not on screen coordinates. `computer_click` with an `element_index`
performs an `AXPress` — it activates a control with **no pointer involved at all**. This is
dramatically safer and more reliable than screenshot-and-coordinate clicking (which breaks on
theme/resolution/layout shifts and warps the physical cursor). Coordinate clicking + drag exist only
for UI that exposes no addressable element (canvases, maps), and even those post a *located* event to
the target process (`CGEventPostToPid`) rather than moving the cursor. The one path that warps the
real cursor (`click_method: "global"`) must be **named explicitly by the model** — `auto` never
resolves onto it — and emits its own SEL record.

---

## 2. Architecture (thin shim, in-gateway dispatch)

Separate the OS driver from the shim, matching PersonalClaw's MCP-tool conventions:

```
agent (ACP)
  └─ spawns  personalclaw mcp-computer          (stdio MCP server)
       │      THIN SHIM — resolves session identity strictly, forwards, returns text
       ▼
  gateway dispatch (the real work; the shim holds no OS handles)
       1. enable_state.is_enabled()          keystone primary enable (out-of-band file)
       2. policy.check_app                    target policy (self + operator allowlists)
       3. index freshness (TTL) + fingerprint re-walk
       4. policy.check_input_target           secure-field / sensitive-text screen
       5. gate.require_computer_use           SEL audit (records, does not decide)
       6. platform driver (macOS AX / Windows UIA / Linux AT-SPI)
       7. re-snapshot, redact_result
```

**Why the shim is thin and the work is in-gateway:** the OS driver needs the gateway's policy,
enable-state, and SEL — replicating those in a subprocess would fork the security surface. The shim
resolves session identity and forwards; it holds no OS handles. This is the same reasoning as
PersonalClaw's other MCP tools that dispatch back into the gateway.

**The tool surface** (element-index discipline per turn): `computer_list_apps`, `computer_snapshot`
(walk a window → indexed AX tree), `computer_click` (element_index → AXPress; or a named coordinate
method), `computer_type`, `computer_set_value`, `computer_scroll`, `computer_perform_action` (a
named AX action). Every snapshot has a TTL + fingerprint so a stale index can't act on a moved
element.

---

## 3. The security floors (non-negotiable — this touches the OS input layer)

1. **Keystone primary enable, out-of-band.** OFF until the operator writes an enable file the agent
   can neither read nor write (not a config field the agent can PATCH). No prompt, no tool, no chat
   instruction can flip it. This is the single hardest gate and it is first in the dispatch chain.
2. **Element-index default, pointer never moves by accident.** `auto` resolves to `AXPress` whenever
   an element index is present. Coordinate/global paths must be explicitly named by the model and
   each emits a distinct SEL `tool_kind`.
3. **Target policy** (`policy.check_app`): a self-plus-operator allowlist of which apps may be
   driven; everything else refused. Secure/password fields and sensitive text screened
   (`check_input_target`) before any type/set-value.
4. **Every action is SEL-audited** (`gate.require_computer_use` records, doesn't decide) and subject
   to the AUTONOMY-GUARDRAILS approval ladder — an unattended profile cannot drive the desktop unless
   the operator granted it at creation time.
5. **Runs as a ceilinged spawn** (PLATFORM-HARDENING-FLOORS §1 `tool` profile) — a wedged/looping
   driver is bounded by the kernel, not just a userspace timeout.
6. **Honest platform story:** macOS first (AX API). Windows (UIA) and Linux (AT-SPI) report a typed
   refusal until their drivers land — never a silent no-op or a simulated success.
7. **The human-facing views grant nothing:** an optional live-view (PiP) mirrors screenshots the
   model already read; an optional cursor-motion overlay draws a *fake* cursor so a watching human
   sees where a click will land — it is not the real pointer and is invisible to screen capture.

---

## 4. Sessions (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — macOS driver + the safety floor (the whole gate before any capability)

| ID | Task | Files | Done when |
|---|---|---|---|
| DCU1.1 | Keystone enable-state: an out-of-band enable file + `enable_state.is_enabled()`; the agent has no read/write path to it; a config field cannot substitute | `src/personalclaw/computer_use/enable_state.py` (new pkg) | with the file absent, every tool refuses with a WHAT/WHY/FIX pointer to the enable step; no tool/config flips it |
| DCU1.2 | `policy.py` (target-app allowlist + `check_input_target` secure-field screen) + `gate.py` (SEL audit, no decision) | `computer_use/policy.py`, `gate.py` | driving a non-allowlisted app refuses; typing into a secure field refuses; every attempt (allowed or refused) has a SEL record |
| DCU1.3 | macOS AX driver (element walk → indexed tree with TTL+fingerprint; `AXPress`; type/set/scroll/named-action; located coordinate path via `CGEventPostToPid`; the explicit `global` warp behind its own name+SEL) via ctypes FFI | `computer_use/macos_driver.py`, `macos_ffi.py`, `types.py` | on macOS with the enable on, snapshot a TextEdit window, `AXPress` a button by index, type into a field — pointer does not move; a stale index (post-TTL) refuses and forces re-snapshot |
| DCU1.4 | The thin stdio shim + in-gateway dispatch + the tool surface registered; ceilinged spawn (PLATFORM-HARDENING-FLOORS §1 `tool` profile) | `computer_use/cli.py`, `service.py`, `tools.py`, MCP registration | the agent lists apps and clicks an element end-to-end; the shim holds no OS handles; the driver spawn carries the ceiling |
| V1 | Validation (macOS, enable ON): drive a real app by element index; confirm the pointer stays put; confirm a secure-field refusal; confirm SEL records; confirm the enable file being absent blocks everything | — | holds; recorded |

### Session 2 — Human-facing views + Windows/Linux refusal + approval integration

| ID | Task | Files | Done when |
|---|---|---|---|
| DCU2.1 | Optional live-view (PiP) mirroring already-read screenshots + optional cursor-motion overlay (fake cursor, invisible to capture); both grant the agent nothing | `computer_use/overlay*.py`, `render.py`, a dashboard view | the views render; neither adds any agent capability (assert the tool surface is unchanged with views on) |
| DCU2.2 | AUTONOMY-GUARDRAILS approval-ladder integration: an unattended profile drives the desktop only with a creation-time grant; interactive sessions get the approval prompt | `computer_use/policy.py`, guardrails seam | an unattended run without the grant refuses + notifies; an interactive run prompts |
| DCU2.3 | Windows (UIA) + Linux (AT-SPI) typed refusals with a clear "not yet on this platform" message (driver stubs that refuse honestly, not silently) | `computer_use/windows_driver.py`, `linux_driver.py` | on non-macOS, every tool returns a typed refusal naming the platform; no silent no-op |
| V2 | Validation: views on/off change no capability; unattended-without-grant refuses; a non-macOS run refuses honestly | — | holds |

**Windows/Linux real drivers** are a deliberate later split (their own sessions when the owner has
the platforms to validate on) — Session 1–2 ship macOS + honest refusals, per PLATFORM-REACH ordering.

---

## Owner tasks (real world)

- **Decide the enable-file location + shape** — it must be somewhere the agent process genuinely
  cannot reach (not under a config dir the agent can PATCH). This is the load-bearing gate.
- **Confirm macOS-first is acceptable** for v1 with honest Windows/Linux refusals (matches
  PLATFORM-REACH's ordering).

## Risks & open questions

- **This is the highest physical-world blast radius in the whole roadmap.** An agent that can drive
  the desktop can, in principle, do anything the operator can. The keystone enable + element-index
  default + target allowlist + SEL + the approval ladder are all load-bearing — none is optional, and
  the plan is structured so the *entire* safety floor lands in Session 1 before any capability is
  usable.
- **Accessibility APIs are OS-permission-gated** (macOS Accessibility permission). The enable flow
  must guide the operator through granting it and refuse legibly when it's missing — never a hang.
- **Element-index staleness** is the correctness risk: acting on a moved element. The TTL+fingerprint
  re-walk is the mitigation; validate it explicitly (DCU1.3 done-when).

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-05][plan] Created. Routed here (not into BROWSE-AUTOMATION) because that plan is
  browser-only by soul; desktop GUI automation is a distinct substrate (OS accessibility tree, not a
  DOM). The accessibility-tree / element-index approach is adopted over screenshot-and-coordinate
  because it is safer (no pointer motion), more reliable (survives theme/layout shifts), and its
  safety model (keystone enable, element-default, target allowlist) is proven sound.

- [2026-08-15][DCU-1] DONE: keystone out-of-band enable-state shipped as
  `src/personalclaw/computer_use/enable_state.py`, built on `guardrails/ceiling.py`'s trust model
  instead of a second one. The enable document lives at
  `$PERSONALCLAW_HOME/governance/computer_use.enable.json` — deliberately the directory the
  governance ceiling already owns, so the keystone and the ceiling share ONE trust root and ONE
  sensitive-path denylist entry (`security.py` needed no change) — and is overridable to an absolute
  path by `PERSONALCLAW_COMPUTER_USE_ENABLE_FILE` so an operator can put it on a root-owned `0444`
  file outside the agent's home. Required content: exactly `{"version": 1, "enabled": true, "apps": ["TextEdit"]}`.
  DISCOVERY: a touch-a-marker file is unsafe here, and it is measurable — as a marker, an empty file
  and a half-flushed write both ARM the machine; as a document with a required positive shape both
  fail closed. Those two cases are now named rows in the fail-closed corpus rather than a claim.
  Fourteen malformed shapes resolve to OFF (non-JSON, wrong root type, unknown version, unknown key,
  `"true"`, `1`, `false`, missing flag); `enabled` is compared with `is True`, never for truthiness.
  An unenforced key is REFUSED rather than ignored, on the ceiling's reasoning: `{"enabled": true,
  "only_apps": ["Mail"]}` means *on, narrowed*, so honouring the flag while dropping the scope would
  grant strictly more than was asked. Read once and cached, so neither a tamper nor a legitimate
  enable widens the running process — arming costs a restart the operator performs and can see.
  `require_enabled(tool)` raises `ComputerUseDisabled` carrying a typed `AgentError`
  (`ERR_COMPUTER_USE_DISABLED`) whose FIX names the resolved path, the exact bytes, the restart and
  the env override; never a falsy return, because a no-op reads to a model as "the click landed".

- [2026-08-15][DCU-1] DISCOVERY: ⚠️ **the computer-use tool population is EMPTY on `main`, so the
  done_when's "every computer-use tool refuses" cannot be exercised over real tools yet.** Verified:
  there was no `computer_use` package at all before this atom; `DCU-4` owns the tool surface, the
  stdio shim and the in-gateway dispatch chain, and `DCU-3` the macOS driver. A test that literally
  iterated the tool set would therefore have passed over nothing. What is armed vs exercised, stated
  plainly: **exercised today** — resolution + fail-closed corpus, the refusal message and its
  WHAT/WHY/FIX shape (through a fixture tool that calls the real guard), the no-config-field and
  no-write-surface proofs, the caching property, and the boot SEL audit. **Armed but unexercised
  until `DCU-4`** — the clause "every computer-use tool", and the atom scope's "first check in the
  dispatch chain" (there is no dispatch chain to be first in). The arming is two rails plus an
  explicit vacuity marker: rail A asserts by AST that every module-level `computer_*` function under
  `computer_use/` calls the guard as its FIRST statement, proven to detect rather than to match
  nothing (the scanner is run against an unguarded twin and against a guard-placed-after-the-work
  variant, and flags both); rail B pins every public function/class in the package, closing rail A's
  stated naming gap so a dispatch surface that abandons the `computer_*` convention still trips
  something; and `test_the_computer_use_tool_population_is_currently_empty` states the size is 0 out
  loud and reds the moment `DCU-4` adds the first tool. Measured on a synthetic future tool: an
  unguarded `computer_*` tool reds 3 tests; a correctly guarded one satisfies rail A and reds only
  the two census tests (so the ratchet is a gate, not "any new function reds"); a non-`computer_*`
  dispatch surface escapes rail A and is caught by rail B.

- [2026-08-15][DCU-1] DEVIATION: added a live call site the atom's file list does not name —
  `gateway.py::GatewayOrchestrator.run` calls `ensure_computer_use_boot()` immediately after
  `ensure_governance_boot()`, resolving the keystone once and SEL-auditing source + digest + outcome
  per run. Reason: without it this atom would ship an inert module, which this repo treats as a
  defect, and the alternative (inventing a dispatch chain to have a caller) is worse — `DCU-4` owns
  that chain. The boot hook is real work today: it fixes the process posture before any service
  exists and is the tamper evidence the precedent (`ceiling.ensure_governance_boot`) exists to
  provide. Unlike governance it NEVER aborts: "no bound" would be a widening, whereas OFF is the
  normal and default state, so a typo in an operator's JSON must not take the gateway down.
  Second, smaller deviation: `ERR_COMPUTER_USE_DISABLED` is registered in `errors.ERROR_CODES`,
  following that module's documented contract ("new failure paths add a code") rather than the
  ceiling's practice — its `ERR_GOVERNANCE_*` codes are raised without being registered.

- [2026-08-15][DCU-1] DISCOVERY (falsification found a real defect, not a test gap): 8 mutations
  were run and every one reded the intended rail — none reded nothing. `is_enabled() -> True`
  unconditionally: **20 tests red**, including `test_the_fixture_tool_refuses_when_the_keystone_is_
  absent` with `Failed: DID NOT RAISE ComputerUseDisabled`. That number is only 20 because the first
  version was wrong: `require_enabled` read `state.enabled` directly, bypassing `is_enabled()`, so
  the same mutation left EVERY refusal test green and reded 18 unrelated assertions instead. Two
  independent readers of one flag is exactly how one ends up answering differently from the other,
  so `require_enabled` now reads the decision through `is_enabled()` — the one check the plan names.
  Fail-open on a parse error (the `JSONDecodeError` branch returning `enabled=True`): 4 red,
  `AssertionError: '' armed the keystone`. An unguarded future `computer_*` tool: 3 red. A
  `computer_use.enabled` entry in `_EDITABLE_CONFIG`: 1 red. Removing the gateway boot call: 1 red.
  A second shipped module naming the env var: 1 red (`... the out-of-band property is gone:
  ['computer_use/enable_state.py', 'dashboard/handlers/core.py']`). Dropping
  `.personalclaw/governance` from the sensitive-path denylist: 2 red (so that rail is not vacuous).
  A `write_text` inside the keystone module itself: 2 red.

- [2026-08-15][DCU-1] Gate: `make lint` clean (black 1679 files, isort, flake8, mypy 866 sources, no
  issues) · `tests/test_computer_use_enable_state.py` 42 passed · repo-wide rails 129 passed
  (`test_inert_surface_baseline`, `test_portability`, `test_durability_inventory`,
  `test_config_baseline`, `test_resilience_degraded_lint`, `test_api_manifest_drift`,
  `test_error_codes_append_only`, `test_roadmap_dag_derived`) · security-adjacent 397 passed, 9
  xfailed (`tests/security/`, `test_guardrails_ceiling`, `test_security*`, `test_gateway`). The
  real-home rail reported `~/.personalclaw` unchanged on every run. The inert-surface baseline was
  NOT regenerated and did not need to be: this atom adds no config key, enum member, trigger kind,
  `_EDITABLE_CONFIG` entry or SDK export. No CHANGELOG entry — there is no user-reachable capability
  to announce (no computer-use tool exists to enable, and the only observable change on a default
  install is one boot-time SEL row), so the reasoning is recorded here instead per the atom's
  instruction. DISCOVERY (pre-existing, NOT fixed here — one concern per commit): `governance/` is
  neither claimed nor ignored by the durability inventory, so `audit_home()` would report it
  unclaimed if an operator ever creates it. Measured: `is_ignored('governance')` is False,
  `claim_for('governance')` is None, and no INVENTORY entry is nested under it. It does not red today
  because the directory does not exist on a default install and no test creates one. It arrived with
  the ceiling, not with this atom, and the correct fix is an *ignore* entry (operator-owned
  out-of-band state must not be snapshot-restored — restoring it could re-arm a machine), which
  belongs to whoever owns that inventory row.

- **2026-08-23 — `DCU-2` COMPLETE (all three clauses). Atom stays `todo` only because this code is
  unmerged**; flip it when the PR lands.
  Three clauses, all offline-testable, against `DCU-1`'s shipped keystone: a non-allowlisted app refuses,
  a secure/password destination refuses, and **every** attempt — allowed or refused — leaves a SEL row.
  The OS driver is `DCU-3` and is not touched here.
  **The allowlist lives in the out-of-band enable document, NOT `config.json` — a decision, not a
  convenience.** `DCU-1` built the keystone so *"no tool or config path can flip the state"*. The app
  allowlist is what stands between "computer use is on" and "the agent may drive your password manager", so
  PATCH-editable config would hand an agent with config-write access a route to widen its own reach. Same
  threat, same storage.
  **The `ENABLE_DOCUMENT` tension, resolved rather than deferred.** That constant is quoted verbatim in the
  refusal's FIX line precisely so *"the message a model reads and the bytes this module accepts can never
  drift apart"*. Once `apps` is required, the old two-key document arms a capability that drives nothing —
  an operator would follow the FIX exactly, hit a second refusal, and that one names no further fix, which
  teaches them the message cannot be trusted. So the quoted bytes became
  `{"version": 1, "enabled": true, "apps": ["TextEdit"]}`, one deliberately benign target, and a test runs
  the constant through the real parser and asserts the result can actually drive something. Verified at
  integration rather than taken on report.
  **Absent and explicit `[]` are identical BY CONSTRUCTION** (`data.get("apps", [])`), not by two branches
  — neither can mean "all" without inverting the narrower of the operator's two grants into the widest one.
  `[]` is deliberately not a parse refusal: "armed, targets not chosen yet" is coherent, and reporting it as
  malformed would collapse it with "your JSON has a typo", the distinction `EnableState.detail` exists to
  keep.
  **Name matching is exact byte equality, and every normalisation was rejected with a reason:** case-folding
  reaches a differently-cased app the operator never named (and these names are case-sensitive on the
  platforms this drives), sub/superstring reaches `TextEditPro`, display-name↔bundle-id reaches
  `com.apple.TextEdit`, stripping makes a padded entry start matching. So `parse_enable_document` REFUSES a
  non-list, a non-string entry, an empty or padded entry, and an exact duplicate — naming the offender —
  rather than normalising into something broader. Probed live: padded, duplicate and unknown-key documents
  all read as OFF with an empty allowlist.
  **`policy` decides; `gate` only records.** That split is the plan's, and it is enforced by naming: neither
  module uses the `computer_*` prefix, because that prefix is what the keystone ratchet binds to
  `require_enabled()`, and a second keystone reader inside the chain is exactly the drift
  `require_enabled`'s own docstring was written about (an earlier version read `state.enabled` directly and
  *"forcing `is_enabled` to return True left every refusal test GREEN"*).
  **Unknown means no, per shape.** `check_input_target` screens role/subrole/label/value across both the
  bare and `AX*` spellings, and **every present spelling must pass, not the first found** — a target
  claiming `{"role": "AXTextField", "AXRole": "AXButton"}` refuses, because screening whichever came first
  lets the caller choose which of its two claims gets enforced. A non-mapping, an empty mapping, a missing
  or empty role, a role outside the editable set, a subrole outside the allowed set, or any screened key
  holding a non-string all refuse. Falsified at integration: disabling the role guard reds 4, including the
  contradictory-spellings case and the never-seen-role case.
  **"Sensitive text" reuses the repo's one definition** — `security.redact_credentials`, refusing when it
  warns — rather than minting a second sensitivity vocabulary. The separate label lexicon (`password`,
  `passcode`, `cvv`, `otp`, …) identifies the FIELD, not the content, and exists because the commonest real
  password field is a web-view `AXTextField` titled "Password" with no secure subrole at all. Word-bounded,
  so `Pinned messages` and `Shipping address` do not refuse; the refusal never quotes the value it refused.
  **DEVIATION — no implicit self-allowance.** The plan says "self-plus-operator allowlist"; PersonalClaw
  itself is NOT granted. Driving PersonalClaw's own windows means clicking its own approval dialogs and
  settings — the one target where computer use converts into raising the agent's own permissions. "Self" is
  an ordinary operator-listed entry, and an empty allowlist refuses everything including PersonalClaw. Not
  escalated as E4 because the ambiguity only resolves toward granting MORE, and the strict version ships
  correctly.
  **The audit records and cannot decide.** `require_computer_use` never raises: the whole body is wrapped,
  and a dropped write logs a **WARNING** (a deliberate step up from `DCU-1`'s `logger.debug` on its
  once-per-run boot row — this one is per-attempt, so a broken audit is a silent ONGOING hole) naming the
  tool, outcome and app. A test asserts the level, the content, and that it fires exactly once, so "fails
  open" stays distinguishable from "never ran". `outcome` passes through unvalidated: coercing an
  unrecognised outcome would record a different verdict than happened, and rejecting it would be a decision.
  **`metadata` carries no free text, enforced structurally — and the reason is a real finding about the
  SEL.** `redact_event` protects nothing here: `log()` writes `json.dumps(asdict(event))` to disk
  **unredacted** (verified at `sel.py:339`; redaction runs only on the forward callback at `:344` and the
  read surface), and it recognises credential-shaped strings, not personal data. A window title like
  "Bank of America — Checking" is neither, so it would pass through and live in the audit log forever. So
  strings and containers are replaced **wholesale** with a `<str len=N>` shape — wholesale, not walked, so
  there is no depth at which a string survives and no recursion to get wrong.
  **Four things only the MERGE could resolve, all fixed here.** (1) The public-surface AST ratchet needed
  one combined entry for both new modules — three branches editing one dict literal is the conflict the
  fence exists to prevent, and both agents correctly deferred it. (2) `ERR_COMPUTER_USE_APP_NOT_ALLOWED`
  and `ERR_COMPUTER_USE_SECURE_FIELD` were missing from the append-only `ERROR_CODES` registry the way
  `DCU-1` registered its code; added, and verified by extracting the codes `policy.py` actually names and
  checking every one is registered. (3) The package docstring still said *"the only module here today is
  enable_state"*. (4) **Two owner-maintained docs stated required content the parser no longer accepts** —
  `DESKTOP-COMPUTER-USE.md:172` and `DCU.md:36` both said the file must contain exactly
  `{"version": 1, "enabled": true}`, which after this change arms a capability that can drive nothing.
  **A premise in the briefing was wrong, and the agent was right to override it.** I required `DCU-1`'s
  ratchet to pass untouched as the compatibility proof. It cannot: `DCU-1` deliberately chose `apps` as its
  canonical **unenforced scope key**, with a parametrized case asserting
  `{"version":1,"enabled":true,"apps":["Mail"]}` must refuse with *"does not enforce"*. `DCU-2` enforcing
  `apps` makes that assertion factually false. The case was **re-keyed to `windows`, not deleted**, so the
  case count and the property survive — and a falsification accepting unknown keys still reds it, proving
  the edit left it detecting rather than vacuous.

- **2026-08-24 — `DCU-2` AUDIT: the deliverable IS on `main` (commit `22f28ad6`), so the entry above's
  own condition for flipping — *"stays `todo` only because this code is unmerged"* — is met.** Audited
  rather than rebuilt: a branch carrying the DCU-2 commit rebased onto `main` reported *"dropping … patch
  contents already upstream"*, and `policy.py` (395 lines) + `gate.py` (197 lines) are present with the
  merge-resolution items verified individually — both codes registered in `errors.ERROR_CODES`
  (`errors.py:59`, `:64`) and both modules pinned in the public-surface AST ratchet
  (`test_computer_use_enable_state.py:524-531`). 192 tests green across the four computer-use files.
  **All three clauses hold as MODULE CONTRACTS, proved by driving the real functions, not by reading them.**
  Against a tmp `PERSONALCLAW_HOME` with `{"version":1,"enabled":true,"apps":["TextEdit"]}`: `TextEdit`
  allowed, while `Terminal`, `textedit`, `TextEditPro` and `PersonalClaw` all refuse
  `ERR_COMPUTER_USE_APP_NOT_ALLOWED`; an ordinary `AXTextField` and one titled `Pinned messages` are
  allowed, while `AXSecureTextField`, a web-view field titled `Password`, an `AXButton`, contradictory
  `role`/`AXRole` spellings, a target with no role, and a field already holding an API-key-shaped value
  all refuse `ERR_COMPUTER_USE_SECURE_FIELD`. With the enable document ABSENT the allowlist is `()` and
  every app refuses including PersonalClaw — the fail-closed direction needs no exemption.
  **Clause 3 re-proved against a REAL on-disk SEL, because the suite proves it against a fake.**
  `test_computer_use_gate.py` substitutes `gate.SecurityEventLog` with a capturing stand-in, which shows
  `log()` was *called* but not that the row is *writable*. Driving a real `SecurityEventLog` at a tmp
  `base_dir` produced exactly 2 rows in `security_events.jsonl`: the ALLOWED one
  (`operation=computer_click`, `outcome=completed`, `resources=app=TextEdit`, `error=""`) and the REFUSED
  one (`operation=computer_type`, `outcome=denied`, `error=ERR_COMPUTER_USE_APP_NOT_ALLOWED`). The
  metadata leak-proofing holds on a real write: `{"window_title": "Bank of America - Checking"}` landed as
  `{"window_title": "<str len=26>"}`. Real-home rail: `~/.personalclaw` unchanged.
  **Four falsifications, each mutating the LIVE line and restoring from a file copy (never
  `git checkout --`), md5-verified back to `e07c66a9`/`3e63c00b`:** dropping the allowlist comparison in
  `check_app` reds 15; admitting `AXSecureTextField` into `_ALLOWED_SUBROLES` with `_SECURE_SUBROLES`
  emptied reds 7; repointing `_SECRET_FIELD_TERMS` at a string that matches nothing reds 2 (so the
  web-view-password rail detects rather than merely matching nothing); an early `return` before
  `SecurityEventLog().log(...)` reds 34, **including `test_allowed_attempt_produces_one_sel_row` and
  `test_refused_attempt_produces_one_sel_row_with_the_refusal_code` separately** — the allowed half is
  the one a single "a row exists" assertion would have let rot.
  **THE FINDING, and it is the one this repo keeps rediscovering: all three screens have ZERO production
  callers.** Censused by AST (not `grep` — `policy.py`'s and `gate.py`'s own docstrings name all three, so
  a text-shaped scan reads as "already wired"): `check_app`, `check_input_target` and
  `require_computer_use` are invoked from `tests/` only. The whole package's sole production importer is
  `gateway.py:3869`, which imports `ensure_computer_use_boot` — `DCU-1`'s once-per-run boot row. So no
  clause is enforced against a real driving path, and in particular **nothing links a refusal to a SEL
  row**: `policy` raises without recording, by design, because `gate` is a separate step a caller must
  remember. That is correct sequencing (`DCU-3` owns the driver, `DCU-4` owns `service.py`'s chain and the
  `computer_*` tools) but it means the `done_when`'s end-to-end phrasing is only demonstrable at `DCU-4`,
  whose own `done_when` already repeats it verbatim ("a secure-field refusal, SEL records present").
  **This inertness was invisible to every gate on `main`, so it is now a test.** Added
  `tests/test_computer_use_call_sites.py` — an AST census of the three screens' production call sites,
  shipped at the measured population (ZERO) per the repo's rail idiom, which reds the moment the first
  caller appears and tells that author to assert the CALL SITE (refusal through the dispatch path, and a
  SEL row on the ALLOWED path as well as the refused one — separately). Nothing else could catch it:
  `inert-surface-baseline.json` censuses five surface kinds (config, `_EDITABLE_CONFIG`, enum, trigger
  kind, SDK export) and has **zero** occurrences of `computer_use`, so a module-level function with no
  importer is outside its vocabulary; `test_the_packages_public_surface_is_pinned` pins the three names
  as API but says nothing about anyone calling them. Three vacuity floors ship with it — a synthetic
  wired caller in all three spellings IS detected, prose naming the screens is NOT counted, and the
  corpus glob really reaches both shipped modules. Falsified: adding one real
  `policy.check_app(...)` call to `gate.py` reds it naming the file.
  **DELIBERATELY NOT BUILT — an owner call, not a guess.** A chain rail binding steps 2/4/5 the way
  `DCU-1`'s `test_every_computer_use_entry_point_guards_first` binds step 1 would have to choose
  `DCU-4`'s composition for it: a per-tool "must call `check_input_target`" rail contradicts `DCU-4`'s
  scope (the chain lives in a central `service.py` dispatch, not in each tool), and a central-dispatch
  rail has no dispatch to bind to yet. `DCU-1` left the same tension unresolved — its keystone rail
  requires every `computer_*` function in the package to call `require_enabled()` FIRST, which a central
  chain would satisfy only if the tools re-check it. Recorded for whoever takes `DCU-4`.

- **2026-08-24 — `DCU-4` DONE (composition, tool surface, thin shim, ceilinged spawn) except the one clause
  that needs `DCU-3`.** Shipped `computer_use/service.py` (the in-gateway chain), `tools.py` (the seven-tool
  surface + the thin shim), `driver_host.py` (the ceilinged child), `dashboard/handlers/computer_use.py` +
  `POST /api/computer-use/dispatch`, and the `mcp_core` aggregation entry. **`DCU-2`'s three screens now
  fire from a real driving path** — its audit's central finding closed.
  **CLAUSE STATUS.** *Met:* the shim holds no OS handles (AST-asserted: it imports neither a driver nor the
  dispatch, and calls none of the three screens); the driver spawn carries the resource ceiling (routed
  through `sandbox.create_subprocess_limited` and classified `CEILING_WRAPPED` in the spawn census); a
  secure-field refusal; SEL records present on BOTH paths; an absent enable file blocking everything (now
  exercisable over a real seven-tool population for the first time — `DCU-1` recorded that clause as
  *"armed but unexercised until `DCU-4`"*). *Not met, and it is a DEPENDENCY not a gap:* **"lists apps and
  clicks an element by index end-to-end … a real app driven by element index with the pointer staying
  put"** needs `DCU-3`'s macOS AX driver, which does not exist on `main` (no `macos_driver.py`,
  `macos_ffi.py` or `types.py`). `DCU-4` declares `DCU-3` as a dep; the atom was taken anyway because
  every other clause is completable without it and because the alternative — leaving `DCU-2`'s screens
  inert for another cycle — is the defect this repo keeps rediscovering. **V1 is therefore deferred to
  `DCU-3`**, which should record it.
  **The ceilinged spawn is a LIVE path today, not scaffolding.** `driver_host.py` is a real child that the
  dispatch really starts through the ceiling helper: `resolve_driver()` resolves by IMPORT (not a
  capability flag, which can say yes about a module that is not there), finds nothing, and answers a typed
  `ERR_COMPUTER_USE_DRIVER_UNAVAILABLE` naming the platform and the module an operator is waiting for. So
  when `DCU-3` lands, what changes is one importable module — not the containment story. Driven for real
  against an armed tmp home: `TextEdit` passes `check_app` and reaches the driver, `Terminal` refuses
  `ERR_COMPUTER_USE_APP_NOT_ALLOWED`, and the child's refusal arrives through the spawn.
  **ORDERING IS THE SUBSTANCE, so it is proved twice.** A runtime trace wraps (does not replace) all four
  collaborators and asserts the exact sequence `[enable, check_app, driver(re-walk), check_input_target,
  sel, driver(act)]`; an AST rail asserts the same order in the SOURCE, catching a step relocated onto a
  branch no test happens to take. The re-walk precedes step 4 deliberately and is a READ —
  `check_input_target`'s own docstring requires the screen to see *"the element that will be typed into,
  not a stale row"*, so the element screened comes from the re-walk and never from the stored snapshot.
  Falsified: moving `check_input_target` below the acting call reds 3, and the sharpest message is
  behavioural — `the type reached the driver: ['snapshot', 'type']`, i.e. the password was typed.
  **RESOLVING THE TENSION `DCU-1` AND `DCU-2` BOTH LEFT OPEN.** `DCU-1`'s keystone rail binds every
  module-level `computer_*` function to `require_enabled()` FIRST; a central chain satisfies it only if the
  tools re-check. Resolved by making the tool NAMES data and the dispatch the only function: `TOOL_SURFACE`
  in `tools.py` is a tuple of `ToolSpec`s, and `service.computer_dispatch` is the package's ONE dispatchable
  entry point. So the entry-point population went 0 → **1**, not 0 → 7, and `DCU-1`'s ratchet stopped being
  vacuous instead of staying vacuous with seven keystone readers beside it. Seven `computer_*` functions
  would have been seven readers of the one decision `require_enabled`'s docstring was written about.
  **SEL ON BOTH PATHS, and the allowed leg is the one that would have rotted.** Every exit funnels through
  one `_audit` helper: refusals record `outcome="denied"` with the refusal's stable code, an approved
  attempt records `outcome="approved"` **before** the driver runs (the plan's step-5 placement — a driver
  that wedges or is killed by its ceiling must still have left evidence). Consequence recorded as a test
  rather than left to accident: the row carries the VERDICT, so a driver failure after approval does not
  write a second row, and "exactly one row per attempt" stays countable. Proved against a REAL
  `SecurityEventLog` at a tmp `base_dir` (the sibling gate suite's fake proves `log()` was *called*, not
  that the row is writable). Falsified by deleting the allowed-path audit: 4 red including
  `test_the_allowed_path_writes_its_own_sel_row` — and the refused-path and keystone-refusal tests stayed
  **GREEN**, which is exactly the single-"a row exists"-assertion failure `DCU-2`'s audit predicted.
  **#1966's AST CENSUS FLIPPED FROM ZERO-POPULATION TO NAMED-CALLERS, and stayed non-vacuous.** It is on
  `main` (commit `8b4ca7b0`), so it reded on the first production caller exactly as designed, naming the
  file. It now asserts an exact map (`computer_use/service.py` → the three screens) by EQUALITY, so a NEW
  caller reds *and* a LOST call reds — without the second direction the file would have gone green again
  the moment somebody deleted a screen call, returning it to the inert state it was written to complain
  about. Falsified in both directions: adding a caller reds it (its own pre-existing floor), and deleting
  `check_app` from the chain reds it with `the population … changed`. Its three original vacuity floors
  ship untouched. **What a screen-call-site census structurally CANNOT see is a new UNSCREENED path** —
  code that calls no screen — so that direction is covered by two other rails and this is written down in
  the file: the entry-point population census (one dispatch, so a second chain reds) and the per-tool
  declaration rails (`screen_app` exempts only `computer_list_apps`; `screen_input_target` is exactly
  `computer_type`/`computer_set_value`), so an added tool cannot opt out of a screen silently.
  **BOUNDS, both directions, each with a floor.** Snapshot TTL 30s: at exactly the bound it acts, one tick
  past it refuses `ERR_COMPUTER_USE_STALE_INDEX` naming the re-snapshot (falsified by flipping `>` to
  `>=` — the at-bound leg reds alone). Element index: the last index acts, one past refuses
  `ERR_COMPUTER_USE_BAD_ARGUMENT` naming the count, and a NEGATIVE index refuses too (falsified by
  dropping the `0 <=` half: Python would have read `-1` as the last element and pressed a different
  control). Fingerprint: a changed window refuses INSIDE the TTL, so the TTL is a backstop rather than the
  check — and the fingerprint is the driver's, never recomputed here, because a fingerprint derived from
  the stored elements would compare a value with itself and could never disagree. Snapshot store ceiling
  16: at the ceiling the oldest survives, one past it is evicted and acting on the evicted id is a visible
  stale-index refusal. Driver timeout 20s is the userspace half of §3.5 — a wedged child becomes a legible
  refusal, not a request that never answers.
  **THIN STAYED THIN, and thinness is a shape rather than a claim.** Decisions live only in the dispatch:
  the shim (which runs in the `mcp-core` subprocess) is AST-asserted to import no driver and no dispatch
  and to call none of the four guards, and the driver child is asserted the same way. Falsified by giving
  the shim a `policy.check_app` call: 3 red, `the shim decides: ['check_app']`.
  **DEVIATIONS, each with its reason.** (1) **No `computer_use/cli.py` and no second `personalclaw
  mcp-computer` stdio server.** The plan's §2 draws one, but this repo already has exactly one stdio MCP
  server — `personalclaw mcp-core` — that aggregates category modules and forwards to the gateway over
  `_post`, which is precisely the thin shim §2 describes ("the same reasoning as PersonalClaw's other MCP
  tools that dispatch back into the gateway"). A second composition root would mean a second identity
  resolver in the one process that must hold no authority. So `computer_use/tools.py` is a category module
  in `_AGGREGATED_CATEGORY_MODULES`. (2) **`driver_host.py` is not in the atom's file list**, but
  "the driver running as a ceilinged subprocess" needs a child to run in; `DCU-3`'s `macos_driver.py` is
  described as in-process ctypes FFI, and an FFI call blocking inside the OS is a blocked gateway. (3) **The
  tool surface is listed unconditionally, even with the keystone OFF.** Hiding it while disarmed is the
  tempting optimisation and it breaks `DCU-1`'s clause: `mcp_core._aggregated_call_tool` routes by asking
  each module whether it LISTS the name, so an empty list makes `computer_click` fall through to core's
  "unknown tool" and replaces the WHAT/WHY/FIX refusal with a dead end. A conditional population is also a
  second code path whose disabled branch nothing exercises. (4) **A coordinate/global click carries no
  element index** and so skips step 3 — §2 reserves that path for canvas UI that exposes no addressable
  element. It is not a chain bypass: the method must be named by the model (`auto` never resolves onto it,
  and an unknown method refuses rather than falling back in either direction), the app must be named and
  allowlisted, and the attempt is audited under its own `operation` (`computer_click:global`) so a
  real-cursor warp is one field filter away from every ordinary click. `tool_kind` stays the category the
  `SecurityEvent` field definitions say it is.
  **DISCOVERY — the wire-envelope census caught a computed code, and the fix was at the source.** The first
  handler resolved its wire code with a conditional; `test_dynamic_code_sites_do_not_grow` reded at 17 of a
  ceiling of 16. Split into two literal-code emitters (`computer_use_refused` 403 / `computer_use_unavailable`
  503) rather than raising the ceiling. Both rows added to `HTTP_ERROR_CODES`, and the AgentError fields
  ride INSIDE the `error` object as `agent_code`/`what`/`why`/`fix` — the two vocabularies stay disjoint,
  and one refusal keeps one voice. A second defect the tests found: the shim initially rendered the WIRE
  code into the model's context, where `computer_use_refused` cannot distinguish "app not allowlisted" from
  "no such tool"; it now renders `agent_code`.
  **DEP NOTE — `EXT:AUTONOMY-GUARDRAILS` on `DCU-4`, answered concretely.** The **SEL-audit half is now
  fully consumed**: `DCU-2` shipped `gate.require_computer_use` inert, and it now fires on every attempt
  from a live call site. The **approval-ladder / `SafetyProfile` half stays UNCONSUMED here, deliberately** —
  it is `DCU-5`'s declared deliverable and `DCU-5` depends on `DCU-4`, so wiring it now would empty that
  atom. What `DCU-4` owes it instead is exactly one insertion point, and there is exactly one: in
  `computer_dispatch`, between step 4 and step 5, keyed on the `caller_identity` the dispatch already
  carries from `X-Session-Key`. `DCU-5` should read `guardrails.policy.profile_for_session(...)` and refuse
  when `profile.approval` is unattended without the `computer_*` names in `tool_grants="custom"` +
  `tool_allowlist` (the creation-time grant its clause names), raising the existing
  `ComputerUsePolicyRefusal` so no new envelope is needed. Nothing in this atom must be reshaped for that.
  **Nine falsifications, each mutating the LIVE line and restoring from a file copy (never
  `git checkout --`), md5-verified back:** input-target screen moved after the action → 3 red; allowed-path
  audit deleted → 4 red with the two refusal legs still green; TTL made exclusive → 1 red (the at-bound
  leg); index lower bound dropped → 1 red; ceiling helper replaced by raw `create_subprocess_exec` → 2 red
  on two INDEPENDENT rails (the dedicated test and the repo's spawn census, naming file:line); shim given a
  policy decision → 3 red; the stored element screened instead of the re-walked one → 1 red; `check_app`
  deleted → 6 red including the flipped call-site census; the child simulating success instead of refusing →
  2 red. None reded nothing.
  **A BRIEFING PREMISE WAS WRONG.** I was told #1966 was "open and unmerged, so that test may not be in
  your worktree" and to read it from the branch. It IS on `main` (`8b4ca7b0`, an ancestor of `2d7f5b6b`), so
  there was no overlap to handle and no duplication risk — the file was edited in place. Separately, the
  brief did not mention that `DCU-3` is unbuilt, which is what makes one `done_when` clause unreachable
  here.
  **DISCOVERY (found by the full suite, not by reading) — "MCP registration" is a TWO-SIDED
  obligation in this repo, and one side is rail-enforced.** Adding `computer_use.tools` to
  `mcp_core._AGGREGATED_CATEGORY_MODULES` reded three assertions in
  `tests/test_native_tool_categories.py`, whose stated invariant is that the ACP aggregate and the
  in-process catalog must not diverge: *"every tool the ACP aggregate exposes is present in the
  in-process catalog too"*, and *"each category is its own provider in the tool registry"*. So the
  aggregation alone would have shipped a surface an ACP CLI can call and the operator cannot see —
  half a feature, and exactly the asymmetry that rail exists to prevent. Completed the registration:
  `create_computer_use_provider` in `tool_providers/registry.py`, the bundled
  `apps/native/personalclaw-computer-use-tools/app.json`, both test censuses updated, and seven
  `TOOL_META` entries (`test_api_manifest_drift` requires a description, an example, and every
  example arg to be a REAL parameter). The seven tools now appear in `/api/tools`, the manifest and
  the offline reference (95 → 102 tools). **The provider changes no authority and adds no second
  path:** `tools.py` still forwards over `_post`, so in-process invocation is a loopback round trip
  rather than a shorter route into the dispatch — a branch on "am I inside the gateway" would give
  the one security-sensitive transport two code paths with only one of them exercised.
  `InProcessMcpToolProvider.invoke` runs `_call_tool` in an executor thread, so the loopback cannot
  stall the event loop (the shipped `mcp_automation._http_runner` is the same shape).
  **DISCOVERY — a nested category module broke a sibling rail's path derivation, and its OWN VACUITY
  FLOOR is what caught it.** `test_acp_tool_card_fidelity::test_no_core_tool_dict_declares_an_
  explicit_risk_level` built its corpus as `package_root / f"{module.rsplit('.', 1)[-1]}.py"`, which
  is correct only while every aggregated category is a top-level `personalclaw.<name>` module.
  `personalclaw.computer_use.tools` reduced to `tools.py`, pointing the census at an unrelated
  top-level module — and it did not silently scan the wrong file, because that test ships an
  `all(p.is_file())` floor precisely against a mistyped path. Fixed at the source: the corpus is now
  resolved through the import system (`importlib.import_module(m).__file__`), which cannot drift from
  where a module actually lives. The floor is kept.
  **Two other full-suite reds, both accounted for:** `test_planning_runner::test_poll_exits_early_
  when_loop_deactivated_without_sentinel` asserted `6.29 < 5` under a loaded machine (a concurrent
  `make test` plus other agents) and passes on `-n0`; the risk-level rail above was mine and is fixed.

- **[2026-08-25][`DCU-2`] DONE — its gate was inertness, and `DCU-4` landing IS the caller.** The atom's
  own entry ends on the audit finding that all three screens had ZERO production callers, so read alone it
  still reads blocked. The entry immediately below it rules otherwise: *"**`DCU-2`'s three screens now fire
  from a real driving path** — its audit's central finding closed."* Verified against code before flipping
  rather than taken from either entry.
  **Clause by clause, each satisfied THROUGH the real dispatch:** a non-allowlisted app refuses —
  `computer_use/service.py:542` `policy.check_app(app, tool=tool)  # step 2 — before any window is walked`,
  railed by `test_computer_use_dispatch.py:259`; typing into a secure field refuses — `service.py:547`
  `policy.check_input_target(element, tool=tool)  # step 4`, railed at `:269`; every attempt writes a SEL
  record — `service.py:178` `gate.require_computer_use(`, railed by four tests at `:349`, `:370`, `:397`
  and `:407`. **172 passed** across `test_computer_use_dispatch.py`, `test_computer_use_call_sites.py`,
  `test_computer_use_gate.py` and `test_computer_use_policy.py`.
  **"Would deleting the caller be caught?" — YES, proved by mutation at integration.** Replacing the
  `check_input_target` call with `pass` reds **6** tests: the exact-equality census
  (`test_the_dcu2_screens_are_consulted_only_by_the_dispatch`, `sites == _EXPECTED_CALL_SITES`) plus the
  behavioural pair (`..._sees_the_REWALKED_element...` → `DID NOT RAISE ComputerUsePolicyRefusal`, and
  `..._runs_every_screen_before_the_acting_driver_call`). This is the atom's own inertness ratchet, shipped
  at population ZERO and **flipped rather than deleted** by `DCU-4` — its docstring says why: *"a call
  REMOVED from the dispatch reds too. Without the equality this file would go green again the moment
  somebody deleted the `check_input_target` call — returning it to exactly the inert state it was written
  to complain about."*
  **The "DELIBERATELY NOT BUILT" chain rail in the entry above is NOT an unmet `done_when` clause.** That
  paragraph defers a rail binding steps 2/4/5, recorded as needing a central dispatch that did not exist
  yet ("a central-dispatch rail has no dispatch to bind to yet"). `DCU-4` supplied the dispatch, and
  `test_computer_use_call_sites.py` is now that rail. `done_when` names exactly three behavioural clauses
  and none of them mentions it.

- [2026-08-26][DCU-3] PARTIAL: the macOS accessibility driver ships as `types.py` (the
  platform-neutral element/fingerprint/error vocabulary every driver will share),
  `macos_ffi.py` (the ONLY module in the package containing `ctypes`) and `macos_driver.py`
  (the `op_<name>` layer, containing no `ctypes` at all — asserted by AST). The call site this
  atom exists to land: `driver_host.resolve_driver("Darwin")` returned `None` on every commit
  before this one and now returns the module, so the seven ops the dispatch derives from
  `TOOL_SURFACE` all resolve. **One `done_when` clause is unmet and it is an environment gate,
  not a code gap** — see the dated PARTIAL entry below. Row left `todo`, marked 🟡 on the
  precedent `test_roadmap_atomic_status_sync.py` documents for `DC-3` ("implementation landed,
  atom still open for its on-device walk-through").

- [2026-08-26][DCU-3] DECISION: **ctypes FFI, and therefore NO new dependency — not even an
  optional extra.** §3.2 already specified ctypes and it is also the cheaper answer: every symbol
  the driver needs (`AXUIElementCreateApplication`, `AXUIElementCopyAttributeValue`,
  `AXUIElementPerformAction`, `AXValueGetValue`, `CGEventPostToPid`, `CGEventCreateScrollWheelEvent2`,
  `CGWarpMouseCursorPosition`, `proc_listpids`) resolves from a system framework present on every
  macOS install, verified by binding all of them on this machine. A `pyobjc-framework-*` extra
  would have added a compiled wheel, an install step, and an absent-import refusal path in front of
  a feature whose real gate is a TCC permission granted by hand. Two ctypes specifics are
  load-bearing and recorded so nobody "simplifies" them: **every signature is bound** in `_bind`
  because ctypes defaults return/args to `int`, which on arm64 truncates a 64-bit
  `AXUIElementRef` to 32 bits and crashes undebuggably; and **`CGEventCreateScrollWheelEvent2`** is
  used instead of the variadic `CGEventCreateScrollWheelEvent`, which cannot be called correctly
  through ctypes on arm64 (variadic arguments follow a different register discipline).
  DISCOVERY: no framework load happens at import time and that is a rail, not a habit — the driver
  is imported inside the gateway's own process via `resolve_driver`, so a module-level
  `LoadLibrary` would turn "this machine has no desktop capability" into "this machine has no
  gateway". `test_importing_the_ffi_touches_no_framework` asserts it by AST.

- [2026-08-26][DCU-3] DEVIATION: added `ERR_COMPUTER_USE_AX_PERMISSION` to `errors.ERROR_CODES`
  **and to a `_CHILD_CODES` allowlist extracted in `service._run_driver`**, which the atom's file
  list does not name. Reason, and it is a real defect found rather than a preference: `_run_driver`
  honoured exactly two codes from the child and rewrote everything else as
  `ERR_COMPUTER_USE_DRIVER_FAILED`. The driver's two new refusals are both ones only the child can
  determine — it alone asks the OS whether input access is granted, and it alone re-walks the tree
  at the moment of acting — so without this the operator-fixable "tick this box in System Settings"
  FIX was flattened into "the driver failed", which is the one message that cannot be acted on. It
  remains an ALLOWLIST, and the reasoning is recorded at the constant: every member is a REFUSAL, so
  a child naming one can only cause a refusal and never an approval, and the `approved` SEL row is
  already written before the child runs — so honouring them cannot alter a decision, only explain
  it. `ERR_COMPUTER_USE_APP_NOT_ALLOWED` is asserted ABSENT from the set to pin that.

- [2026-08-26][DCU-3] DEVIATION: re-scoped two `DCU-4` tests whose premise this atom retires, in
  the same commit. `test_the_real_spawn_answers_with_a_typed_platform_refusal` and
  `test_the_driver_child_refuses_every_operation_while_no_driver_exists` both asserted
  `ERR_COMPUTER_USE_DRIVER_UNAVAILABLE` from all seven ops — true only while `resolve_driver` found
  nothing, and `DCU-4`'s own docstring predicted the change ("when `DCU-3` lands, what changes is
  one importable module"). Kept as the platform-independent clause §3 floor 6 actually states — the
  answer is a real result or a typed refusal naming a reason, *never* a silent no-op. Notably
  "every operation refuses" is now FALSE by design: `op_list_apps` succeeds without any
  accessibility grant, deliberately, so an operator can discover the app name to allowlist before
  granting anything. A test demanding a refusal from every op would now be asserting a bug.

- [2026-08-26][DCU-3] DISCOVERY: the structural-duplication ratchet caught a real re-derivation and
  changed the design for the better. A module-level `error_envelope(code, message, why, fix)`
  helper returning `{"error": {...}}` matched `http-error-envelope-helper` — the family PL-8 deleted
  thirteen clones of, where each clone is a place the envelope drifts silently because every
  caller's test asserts against its own copy. Fixed by making it a typed value
  (`types.DriverError` with one `to_dict()`) rather than a dict-builder function, which is what
  `AgentError` already is for the agent layer. NOT regenerated to bless the higher number, which the
  ratchet explicitly forbids. Also removed two functions this atom had declared with **zero**
  callers (`macos_ffi.available`, `macos_ffi.advertised_actions`) — the second was redundant with
  the actions the walk already reports. `pointer_position` is kept although its only caller is a
  rail, and the reason is recorded at the function: the alternative is `ctypes` inside a test file,
  which would break the property the module exists to hold.

- [2026-08-26][DCU-3] DISCOVERY (falsification, including one that did NOT red): six mutations were
  run on the live lines, each verified applied by `git grep` before the run and restored from a file
  copy after. (1) Route the `auto` click onto `ffi.click_located` → 1 red. (2) Fingerprint compare
  fails open (`actual = expected`) → 2 red, and the fresh-index VACUITY case stayed GREEN, which is
  what makes the refusal mean something. (3) `fingerprint` argument made optional → **stayed GREEN,
  and the reason is a real finding**: an empty expected fingerprint can never equal a real digest,
  so the comparison itself already refuses it — the fail-closed property has two independent
  guards. Re-falsified with the true fail-open shape (`if expected and actual != expected`) → 1 red
  with `an unverifiable tree was acted on`. (4) `ERR_COMPUTER_USE_AX_PERMISSION` dropped from
  `_CHILD_CODES` → 2 red, the end-to-end one showing the exact flattening to `..._DRIVER_FAILED`
  through the real spawn, which is the proof that change is load-bearing. (5)
  `DRIVER_MODULES["Darwin"]` pointed at a module that does not exist → 3 red, reproducing exactly
  the pre-atom state (`resolve_driver` → `None`). (6) `Element.to_dict` emitting `None` for an
  absent title → 1 red; `policy.check_input_target` refuses a screened key that is not a string, so
  a `None` there would make every element a malformed target.

- [2026-08-26][DCU-3] BLOCKED-BY-ENVIRONMENT (not an escalation — everything buildable is built):
  the `done_when`'s "snapshotting a TextEdit window then AXPress-ing a button by index and typing
  into a field **succeeds**" was NOT observed. Measured, not assumed: `AXIsProcessTrusted()` returns
  False and a real `AXUIElementCopyAttributeValue(AXWindows)` against Finder returns
  `kAXErrorAPIDisabled` (-25211). macOS gates the AX API behind TCC, whose database is
  SIP-protected, so no code path can grant it and no amount of implementation closes this clause.
  **What a human must click:** System Settings → Privacy & Security → Accessibility → `+` → add the
  python binary running the gateway (the interpreter itself, NOT the terminal hosting it) → restart
  the gateway. Deliberately never prompted for from code: `AXIsProcessTrustedWithOptions` can raise
  the system dialog, and an agent-triggered permission prompt is a consent surface the agent chose
  the timing of, so `is_process_trusted()` only ever REPORTS. Two tests are already written to flip
  branch with no code change once granted, so the walk-through is a run, not a work item.
