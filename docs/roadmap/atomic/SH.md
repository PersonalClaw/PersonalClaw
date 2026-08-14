# SECURITY-HARDENING — atomic plans

**Source plan:** [`SECURITY-HARDENING`](../plans/SECURITY-HARDENING.md)  
**Code:** `SH`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `SH-1` | ⬜ | Keychain credential backend selector behind save_credential/read, keyring optional extra, headless fail-closed to .env 0600, doctor reports active backend | — | reads are backend-transparent; headless fixture (no keyring) falls back to .env 0600 with a doctor warning (never plaintext-elsewhere); doctor reports the active backend; unit tests cover both backends; keyring added as optional extra in pyproject.toml |
| `SH-2` | ⬜ | credential_keychain gate (class B) + m_*_credentials_to_keychain migration (snapshot-backed, rollback restores .env) + Settings 'move to keychain' action | `SH-1` | migration fixture (fake keyring) moves .env secrets to keychain and removes the keys, idempotent + verify passes; rollback restores .env; portability export still excludes secrets; Settings action runs the migration with a visible snapshot-confirm step; macOS migrate/rollback + headless .env-fallback validation both recorded |
| `SH-3` | ⬜ | Signing scheme decision + scripts/sign_app.py + in-tree public key; Store verifies signature at install; ScanReport/consent payload gains signature {state,signer}; unsigned stays community-tier installable | — | signing scheme doc records rationale (minisign recommended); signing+verifying a sample bundle round-trips locally; signed first-party bundle shows 'signed by PersonalClaw'; tampered signature refused with reason; unsigned bundle installs at community tier; consent UI renders the signature state |
| `SH-4` | ⬜ | Release pipeline signs first-party app bundles + core release artifacts; registry records signer identity per listing | `SH-3`, `EXT:CI-RELEASE-ENGINEERING:release.yml pipeline to sign artifacts (present/done)`, `EXT:ECOSYSTEM-TOOLING:registry.json listings record signer identity` | released bundles carry valid signatures verified in CI; registry validation script records signer per listing |
| `SH-5` | ⬜ | Adversarial corpus harness (archive/integrity-race/verdict-evasion/invisible-char/degenerate-manifest) against SkillScanner/install_guarded + scanned==installed race invariant + nightly CI job + published methodology doc | — | each of the five attack classes has >=1 asserting test; a swap-after-scan race fixture proves scanned-bytes==installed-bytes holds; nightly job in full.yml runs the corpus; docs/security/scanner-testing.md lets an outsider reproduce; a deliberate scanner weakness on a branch turns the corpus red |
| `SH-6` | ✅ | Baseline denylist as packaged data file (baseline_denylist.json + sha256) with integrity re-assert on read + periodic re-verify, SEL baseline_denylist_reasserted/_tamper_attempt events, strictly-additive user config, shared source with guardrails/denylist.py | — | mutating the in-memory denylist at runtime is healed on next denied_command_patterns() read and SEL-logged; effective set is provably a superset of the packaged baseline (property test); user additions still merge (dedupe, no shrink path); guardrails/denylist.py loads the same packaged source |
| `SH-7` | ⬜ | Mode-independence matrix: baseline-matched command refused under default/auto/yolo/acceptEdits and trust simulators, deny-before-approval ordering regression-pinned, baseline-tamper corpus class added to S3 harness | `SH-5`, `SH-6` | every approval-mode fixture refuses a baseline-matched command; reordering the deny check below the approval gate turns CI red; a baseline-tamper corpus class is added to the S3 harness |
| `SH-8` | ⬜ | SEL audit surface: paginated /api/security/audit (caller/operation/outcome/downstream_service/time filters) + /api/security/audit/verify wrapping verify_integrity + 'What did my agent do' Settings page with credential-safe JSONL export | — | filters work; verify endpoint returns (checked, ok) with a tamper fixture showing ok=false; page renders real SEL events and shows a deliberately-broken chain link; export reuses redact and excludes secrets (fixture-verified); both themes/WCAG; export round-trips |
| `SH-9` | ⬜ | External-review scoping doc: five high-risk paths, commissioned-vs-self-audit format, publication plan | — | docs/security/review-scope.md lists the five high-risk paths, the review format, and the publication plan; scope approved by owner (owner task 3); review executed or scheduled with a date |
| `SH-10` | ✅ | Security panel: baseline denylist shown read-only with version + verified-hash indicator and 'N user additions'; anti-drift/anti-LLM-tamper (not anti-owner) limitation documented | `SH-6` | the /api/security/denied-commands payload + security settings page render the baseline-verified state; a tamper fixture flips the indicator; 'N user additions' shown; the anti-drift/anti-LLM-tamper-not-anti-owner threat model is documented in docs/security/ |

## Atom scopes

### `SH-1` — Keychain credential backend selector behind save_credential/read, keyring optional extra, headless fail-closed to .env 0600, doctor reports active backend

**Status:** todo

Session 1 — OS keychain credential storage / T1.1; Design S1; Contracts C1 (credential backend selector)

**Done when:** reads are backend-transparent; headless fixture (no keyring) falls back to .env 0600 with a doctor warning (never plaintext-elsewhere); doctor reports the active backend; unit tests cover both backends; keyring added as optional extra in pyproject.toml

### `SH-2` — credential_keychain gate (class B) + m_*_credentials_to_keychain migration (snapshot-backed, rollback restores .env) + Settings 'move to keychain' action

**Status:** todo

Session 1 T1.2/T1.3/V1; Contracts C1 migration m_*_credentials_to_keychain; owner task 1 (macOS Keychain validation)

**Done when:** migration fixture (fake keyring) moves .env secrets to keychain and removes the keys, idempotent + verify passes; rollback restores .env; portability export still excludes secrets; Settings action runs the migration with a visible snapshot-confirm step; macOS migrate/rollback + headless .env-fallback validation both recorded

### `SH-3` — Signing scheme decision + scripts/sign_app.py + in-tree public key; Store verifies signature at install; ScanReport/consent payload gains signature {state,signer}; unsigned stays community-tier installable

**Status:** todo

Session 2 — Signed manifests + registry trust / T2.1, T2.2, V2; Design S2; Contracts C2; owner task 2 (generate signing key)

**Done when:** signing scheme doc records rationale (minisign recommended); signing+verifying a sample bundle round-trips locally; signed first-party bundle shows 'signed by PersonalClaw'; tampered signature refused with reason; unsigned bundle installs at community tier; consent UI renders the signature state

### `SH-4` — Release pipeline signs first-party app bundles + core release artifacts; registry records signer identity per listing

**Status:** todo

Session 2 T2.3

**Done when:** released bundles carry valid signatures verified in CI; registry validation script records signer per listing

### `SH-5` — Adversarial corpus harness (archive/integrity-race/verdict-evasion/invisible-char/degenerate-manifest) against SkillScanner/install_guarded + scanned==installed race invariant + nightly CI job + published methodology doc

**Status:** todo

Session 3 — Adversarial gate testing / T3.1, T3.2, T3.3, V3; Design S3; Contracts C3; owner task 4 (approve publishing corpus)

**Done when:** each of the five attack classes has >=1 asserting test; a swap-after-scan race fixture proves scanned-bytes==installed-bytes holds; nightly job in full.yml runs the corpus; docs/security/scanner-testing.md lets an outsider reproduce; a deliberate scanner weakness on a branch turns the corpus red

### `SH-6` — Baseline denylist as packaged data file (baseline_denylist.json + sha256) with integrity re-assert on read + periodic re-verify, SEL baseline_denylist_reasserted/_tamper_attempt events, strictly-additive user config, shared source with guardrails/denylist.py

**Status:** done

Amendment (2026-07-26) T3.4

**Done when:** mutating the in-memory denylist at runtime is healed on next denied_command_patterns() read and SEL-logged; effective set is provably a superset of the packaged baseline (property test); user additions still merge (dedupe, no shrink path); guardrails/denylist.py loads the same packaged source

### `SH-7` — Mode-independence matrix: baseline-matched command refused under default/auto/yolo/acceptEdits and trust simulators, deny-before-approval ordering regression-pinned, baseline-tamper corpus class added to S3 harness

**Status:** todo

Amendment (2026-07-26) T3.5

**Done when:** every approval-mode fixture refuses a baseline-matched command; reordering the deny check below the approval gate turns CI red; a baseline-tamper corpus class is added to the S3 harness

### `SH-8` — SEL audit surface: paginated /api/security/audit (caller/operation/outcome/downstream_service/time filters) + /api/security/audit/verify wrapping verify_integrity + 'What did my agent do' Settings page with credential-safe JSONL export

**Status:** todo

Session 4 — SEL surface + external review / T4.1, T4.2, V4; Design S4; Contracts C4

**Done when:** filters work; verify endpoint returns (checked, ok) with a tamper fixture showing ok=false; page renders real SEL events and shows a deliberately-broken chain link; export reuses redact and excludes secrets (fixture-verified); both themes/WCAG; export round-trips

### `SH-9` — External-review scoping doc: five high-risk paths, commissioned-vs-self-audit format, publication plan

**Status:** todo

Session 4 T4.3; Design S4 (external review); owner task 3 (decide external review)

**Done when:** docs/security/review-scope.md lists the five high-risk paths, the review format, and the publication plan; scope approved by owner (owner task 3); review executed or scheduled with a date

### `SH-10` — Security panel: baseline denylist shown read-only with version + verified-hash indicator and 'N user additions'; anti-drift/anti-LLM-tamper (not anti-owner) limitation documented

**Status:** done

Amendment (2026-07-26) T4.4

**Done when:** the /api/security/denied-commands payload + security settings page render the baseline-verified state; a tamper fixture flips the indicator; 'N user additions' shown; the anti-drift/anti-LLM-tamper-not-anti-owner threat model is documented in docs/security/

