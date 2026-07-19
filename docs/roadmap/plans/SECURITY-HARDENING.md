# Plan: Security Hardening — Deep Features Beyond Legibility

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "let's do this but towards the end of the roadmap")
**Created:** 2026-07-18
**Wave:** 4 — deliberately late; SECURITY-LEGIBILITY (Wave 0) carries the launch-time trust story.
**Depends on:** SECURITY-LEGIBILITY (the threat model that prioritizes this — its DISCOVERY entries seed candidate scope), CI-RELEASE-ENGINEERING (fuzz jobs, signing infra), ECOSYSTEM-TOOLING S2 (the registry signed manifests protect), LIFECYCLE-DOCTRINE (the keychain migration is class B).
**Scope:** the security *features* deepening an already-strong architecture — credential storage, artifact signing, adversarial gate testing, the user-facing audit surface, external review. **Soul guardrail:** enforcement-over-request stands — every addition is a chokepoint control, never a prompt-side plea; and no addition may weaken a fail-closed default for convenience. This plan does not re-architect; it deepens existing chokepoints.

---

## Context (code recon, 2026-07-18)

- **Credentials:** `.env` (0600) via `config/loader.py::save_credential`; **no keyring backend today** (only an unrelated ssh-keyring path string in `acp/transport.py`).
- **Scanner is well-structured for fuzzing:** `supply_chain.py` — `Verdict`(rank), `TrustTier`, `Finding`, `ScanReport`(is_dangerous), `SkillScanner.scan(staged_dir, tier)` / `scan_text` / `_scan_script` / `_scan_invisible` (invisible-char detection already exists) / `_aggregate`. Clean seams for a corpus harness.
- **SEL has verification already:** `sel.py::verify_integrity(max_entries)` returns (checked, ok) — the chain-verify indicator for the audit page is a read away; periodic trim + startup verify already run.
- **Security panel exists:** `/api/security/{stats,denied-commands,egress}` — the SEL audit view extends this surface.

## Design

- **S1 — OS keychain (class B, gate `credential_keychain`):** a keyring backend behind the existing `save_credential`/read API (macOS Keychain, Linux Secret Service via `keyring` lib as an *optional* extra — headless/container installs without a secret service **fail closed to `.env` 0600**, never fail open to plaintext-elsewhere). Lifecycle: opt-in → default-new-installs → migrate-on-consent (a migration moves `.env` secrets into the keychain, leaving `.env` keys absent; export exclusions unchanged; rollback = the pre-migration snapshot). `doctor` reports the active credential backend.
- **S2 — signed manifests + registry trust:** maintainer signing of first-party + registry-listed bundles (minisign or Sigstore keyless — decide against CI capabilities in the task; minisign is simpler, no OIDC dance, one public key shipped in-tree). The Store verifies signatures and renders state on the consent surface (`signed by <known key>` / `unsigned — community tier`); **unsigned stays installable at community tier** (graduated trust, never a hard wall — the supply-chain-tier doctrine). Registry (`ECOSYSTEM-TOOLING`) records signer identity per listing.
- **S3 — adversarial gate testing:** a hypothesis-driven corpus against `SkillScanner`/`install_guarded`: archive attacks (symlink escape, path traversal, case-collision, zip-slip), the scanned-bytes==installed-bytes integrity invariant under concurrent install races, verdict-evasion (obfuscated/split dangerous patterns, invisible-char tricks the existing `_scan_invisible` should catch — prove it), degenerate/oversized manifests. Corpus committed; nightly CI job; **publish the corpus + methodology** (`docs/security/scanner-testing.md`).
- **S4 — SEL as a user surface + external review:** a "What did my agent do" audit page (filter by caller/operation/outcome/downstream-service, chain-verify indicator from `verify_integrity`, export) extending the security panel; and an external review (commissioned or a structured public self-audit) of the highest-risk paths — webhook auth (`_verify_hook_token`), app reverse-proxy token model, scanner bypasses, egress guard layering, inbound surfaces (plans 41/24) — findings published with fixes per SECURITY.md.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Credential backend selector (behind existing `save_credential`/read API, §2.5 — callers unchanged)
```python
CredentialBackend = Literal["keychain", "dotenv"]
def credential_backend() -> CredentialBackend: ...   # keychain if available+enabled, else dotenv
# save_credential(key, value) routes to the active backend; reads are backend-transparent.
# Absent secret service (headless/container) → dotenv fallback + doctor warn. NEVER plaintext-elsewhere (fail-closed to the MORE protected store).
```
`keyring` as an optional extra. Gate `credential_keychain` (class B). Migration `m_*_credentials_to_keychain` (moves `.env` secrets → keychain, removes keys from `.env`; snapshot-backed; rollback restores `.env`). **Shares this backend with plan 13's secret vault** (§1.3 landmine #2 — build once).

### C2 — Manifest signature (minisign recommended; decide in T2.1)
`ScanReport`/consent payload gains `signature: {state: "signed"|"unsigned"|"invalid", signer: str}`. Store verifies if present; **unsigned → community tier, still installable** (graduated trust). Public key shipped in-tree; `scripts/sign_app.py` for maintainers. Registry (plan 38) records signer per listing.

### C3 — Adversarial corpus layout
`tests/security/corpus/<class>/` for the five classes (archive, integrity-race, verdict-evasion, invisible-char, degenerate-manifest); `tests/security/test_scanner_adversarial.py` (hypothesis strategies + fixed fixtures) against `SkillScanner.scan`/`install_guarded`. Published methodology `docs/security/scanner-testing.md`. Nightly job in `full.yml`.

### C4 — SEL audit surface (reuses `verify_integrity`, §3.3)
`GET /api/security/audit` (paginated, filters: caller/operation/outcome/downstream_service/time) + `GET /api/security/audit/verify` → `{checked, ok}` (wraps `verify_integrity`). Export = credential-safe JSONL (reuse `redact`). Frontend page under Settings → Security.

### Integration points
- **Calls:** `save_credential`/credential store (§2.5), `SkillScanner`/`install_guarded`, `sel().verify_integrity`, plan-31 gate+migration, `redact` (§3.7).
- **Consumed by:** plan 38 (registry signer records), plan 13 (shared credential backend).
- **Owner-critical:** the signing private key (owner task 2) — referenced in the continuity doc (plan 37).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — OS keychain credential storage

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Keyring backend behind the credential API: `save_credential`/read gain a backend selector (`keychain` | `dotenv`), `keyring` as an optional extra; **absent secret service → fall back to `.env` 0600 with a doctor warning (never plaintext-elsewhere)** | `src/personalclaw/config/loader.py`, `pyproject.toml` extra, `cli_doctor.py` | reads are backend-transparent; headless fixture (no keyring) uses `.env`; backend reported by doctor; unit tests both backends |
| T1.2 | Register gate `credential_keychain` (class B) + migration `m_*_credentials_to_keychain` (moves `.env` secrets → keychain, removes the keys from `.env`; idempotent; snapshot-backed; rollback restores `.env`) | `lifecycle/gates.py`, `lifecycle/migrations/m_*.py` | migration fixture (with a fake keyring) moves + verifies; rollback restores; `portability` export still excludes secrets |
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
2. **Generate + safeguard the signing key** (S2): create the minisign (or Sigstore identity) keypair; the private key is a release-critical secret — store it in your password manager + the CI `release` environment; the continuity doc (plan 37) must reference its recovery. This is a keep-it-safe-forever artifact.
3. **Decide external review** (S4): budget for a professional audit of the five paths (a scoped agent-security review is a real line item) vs. a published structured self-audit. Either is credible; the choice is yours to fund.
4. **Approve publishing the scanner corpus** (S3) — it advertises exactly how your gate is tested (a strength, but your call to make it public).

## Risks & open questions

- **`keyring` dependency reliability** across Linux desktops varies (Secret Service presence) — the fail-closed-to-`.env` default contains it; keychain is an upgrade, never a requirement.
- **Signing key loss** would break the update/trust chain — owner task 2's safeguarding + the continuity doc are the mitigation; minisign's simplicity (single keypair, no CA) is deliberately chosen to make recovery tractable.
- **Open:** whether to pursue a CVE-numbering-authority relationship or just GitHub advisories — GitHub advisories suffice at this scale; revisit if adoption warrants (ratchet).
