# SECURITY-HARDENING — atomic plans

**Source plan:** [`SECURITY-HARDENING`](../plans/SECURITY-HARDENING.md)  
**Code:** `SH`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `SH-1` | ✅ | Keychain credential backend selector behind save_credential/read, keyring optional extra, headless fail-closed to .env 0600, doctor reports active backend | — | reads are backend-transparent; headless fixture (no keyring) falls back to .env 0600 with a doctor warning (never plaintext-elsewhere); doctor reports the active backend; unit tests cover both backends; keyring added as optional extra in pyproject.toml |
| `SH-2` | ✅ | credential_keychain gate (class B) + m_*_credentials_to_keychain migration (snapshot-backed, rollback restores .env) + Settings 'move to keychain' action | `SH-1` | migration fixture (fake keyring) moves .env secrets to keychain and removes the keys, idempotent + verify passes; rollback restores .env; portability export still excludes secrets; Settings action runs the migration with a visible snapshot-confirm step; macOS migrate/rollback + headless .env-fallback validation both recorded |
| `SH-3` | ✅ | Signing scheme decision + scripts/sign_app.py + in-tree public key; Store verifies signature at install; ScanReport/consent payload gains signature {state,signer}; unsigned stays community-tier installable | — | signing scheme doc records rationale (minisign recommended); signing+verifying a sample bundle round-trips locally; signed first-party bundle shows 'signed by PersonalClaw'; tampered signature refused with reason; unsigned bundle installs at community tier; consent UI renders the signature state |
| `SH-4` | ⬜ | Release pipeline signs first-party app bundles + core release artifacts; registry records signer identity per listing | `SH-3`, `EXT:CI-RELEASE-ENGINEERING:release.yml pipeline to sign artifacts (present/done)`, `EXT:ECOSYSTEM-TOOLING:registry.json listings record signer identity` | released bundles carry valid signatures verified in CI; registry validation script records signer per listing |
| `SH-5` | ✅ | Adversarial corpus harness (archive/integrity-race/verdict-evasion/invisible-char/degenerate-manifest) against SkillScanner/install_guarded + scanned==installed race invariant + nightly CI job + published methodology doc | — | each of the five attack classes has >=1 asserting test; a swap-after-scan race fixture proves scanned-bytes==installed-bytes holds; nightly job in full.yml runs the corpus; docs/security/scanner-testing.md lets an outsider reproduce; a deliberate scanner weakness on a branch turns the corpus red |
| `SH-6` | ✅ | Baseline denylist as packaged data file (baseline_denylist.json + sha256) with integrity re-assert on read + periodic re-verify, SEL baseline_denylist_reasserted/_tamper_attempt events, strictly-additive user config, shared source with guardrails/denylist.py | — | mutating the in-memory denylist at runtime is healed on next denied_command_patterns() read and SEL-logged; effective set is provably a superset of the packaged baseline (property test); user additions still merge (dedupe, no shrink path); guardrails/denylist.py loads the same packaged source |
| `SH-7` | ✅ | Mode-independence matrix: baseline-matched command refused under default/auto/yolo/acceptEdits and trust simulators, deny-before-approval ordering regression-pinned, baseline-tamper corpus class added to S3 harness | `SH-5`, `SH-6` | every approval-mode fixture refuses a baseline-matched command; reordering the deny check below the approval gate turns CI red; a baseline-tamper corpus class is added to the S3 harness |
| `SH-8` | ✅ | SEL audit surface: paginated /api/security/audit (caller/operation/outcome/downstream_service/time filters) + /api/security/audit/verify wrapping verify_integrity + 'What did my agent do' Settings page with credential-safe JSONL export | — | filters work; verify endpoint returns (checked, ok) with a tamper fixture showing ok=false; page renders real SEL events and shows a deliberately-broken chain link; export reuses redact and excludes secrets (fixture-verified); both themes/WCAG; export round-trips |
| `SH-9` | ⬜ | External-review scoping doc: five high-risk paths, commissioned-vs-self-audit format, publication plan | — | docs/security/review-scope.md lists the five high-risk paths, the review format, and the publication plan; scope approved by owner (owner task 3); review executed or scheduled with a date |
| `SH-10` | ✅ | Security panel: baseline denylist shown read-only with version + verified-hash indicator and 'N user additions'; anti-drift/anti-LLM-tamper (not anti-owner) limitation documented | `SH-6` | the /api/security/denied-commands payload + security settings page render the baseline-verified state; a tamper fixture flips the indicator; 'N user additions' shown; the anti-drift/anti-LLM-tamper-not-anti-owner threat model is documented in docs/security/ |

## Atom scopes

### `SH-1` — Keychain credential backend selector behind save_credential/read, keyring optional extra, headless fail-closed to .env 0600, doctor reports active backend

**Status:** done

Session 1 — OS keychain credential storage / T1.1; Design S1; Contracts C1 (credential backend selector)

**Done when:** reads are backend-transparent; headless fixture (no keyring) falls back to .env 0600 with a doctor warning (never plaintext-elsewhere); doctor reports the active backend; unit tests cover both backends; keyring added as optional extra in pyproject.toml

**DONE.** `config/loader.py` owns the C1 selector: `CredentialBackend = Literal["keychain",
"dotenv"]`, `requested_credential_backend()` (intent), `credential_backend()` (**outcome**),
`keychain_available()` and `credential_backend_warning()`. `save_credential` and the new
`get_credential` are the chokepoints — no caller names a backend, and `get_credential(key)`
takes one parameter so none can.

**The write/read asymmetry is deliberate and is what makes reads transparent.** Writes go to
the ACTIVE backend only and fall back to `.env` 0600 when a keychain write fails; **reads are
the UNION of both stores** (keychain preferred) regardless of which backend is active. Without
that, an install that opted into the keychain before SH-2's migration ran would read `""` for
every secret still sitting in `.env` — a lost credential presenting as an empty one.

**Fail-closed in three places.** (1) No `keyring` module → `.env` 0600 + a doctor warning.
(2) `keyring.backends.fail` (raises) **and `keyring.backends.null` (SILENTLY DISCARDS)** are
both refused as unusable rather than adopted — the `null` case is the one that would have
destroyed secrets while returning success. (3) A keychain `set_password` that raises falls
back to `.env` 0600. There is no third location and no relaxed mode anywhere: the fallback
test asserts the mode is exactly `0600` **and that `.env` is the only file the write created**.

**Doctor reports the resolved backend, twice.** `cli_doctor._doctor_credentials()` prints the
CLI line (called from `_doctor()`, asserted by an AST call-site test), and
`security.credential_backend` (CAPABILITY tier, existing `security` capability) is the
dashboard/API half. Both read `credential_backend()`; both share one
`credential_backend_warning()` so they cannot disagree. The probe carries `backend`,
`requested`, `keychain_available` and the `.env` mode as evidence — never a value — and is
`ok=False` for exactly the requested-but-unavailable mismatch.

**Deliberate scope boundary:** the opt-in is the `PERSONALCLAW_CREDENTIAL_BACKEND` env var
(`keychain` | `dotenv`, default `dotenv`), **not** a config field. SH-2 owns the class-B
`credential_keychain` gate, the consent-triggered migration and the Settings action; adding a
config gate here would have built the same seam twice. Availability alone never moves where
secrets are written — a machine that merely *has* a keychain keeps writing `.env` until asked,
so no install silently ends up with half its secrets in each store.

**`keyring` is an optional extra** (`[keychain]` in `pyproject.toml`) and deliberately NOT in
`[dev]`/`[test]`: CI never installs it. `tests/test_credential_backend.py` (26 tests) proves the
no-keyring path with a `sys.meta_path` import blocker and the keychain path with a stub module
in `sys.modules`, so the suite behaves identically with or without the extra and never touches
a real OS keychain. **The keychain's key index lives inside the keychain** (one JSON entry of key
NAMES) rather than in a sidecar file, because `keyring` has no portable enumeration API and a
new file under the config dir would have needed a durability-inventory claim to be snapshot-safe.

Also folded in: `app_cli` had a **second, private `.env` parser** feeding every app's setup
`get_credential` — invisible to keychain-stored secrets. Deleted, routed through the loader
chokepoint (clean break, no shim).

### `SH-2` — credential_keychain gate (class B) + m_*_credentials_to_keychain migration (snapshot-backed, rollback restores .env) + Settings 'move to keychain' action

**Status:** todo

Session 1 T1.2/T1.3/V1; Contracts C1 migration m_*_credentials_to_keychain; owner task 1 (macOS Keychain validation)

**Done when:** migration fixture (fake keyring) moves .env secrets to keychain and removes the keys, idempotent + verify passes; rollback restores .env; portability export still excludes secrets; Settings action runs the migration with a visible snapshot-confirm step; macOS migrate/rollback + headless .env-fallback validation both recorded

**🟡 IMPL LANDED (2026-08-26) — every `done_when` clause met; the row waits on `dag.json`,**
which is the driver's to flip (the mark here is derived from it, so ✅ before the flip reds
`test_roadmap_atomic_status_sync`). Same shape as `DC-3`.

**The one property everything else serves:** no key leaves `.env` until its
value has been read back out of the keychain.** `config/credential_migration.py` snapshots
`.env`'s exact bytes to `.env.pre-keychain` (0600, atomic) *before* the first keychain write, moves
each key, re-READS it, and only then removes it — and on a read-back mismatch **deletes the bad
keychain entry too**, because reads are the union with the keychain preferred, so a wrong entry
there would shadow the good `.env` copy that survived. `rollback_credentials_to_keychain` writes
the snapshot bytes back verbatim (byte comparison, not a re-serialisation) and clears exactly the
keys the snapshot named. A second migrate finds `.env` empty, returns `moved=[]` and **does not
re-snapshot** — re-snapshotting is the idempotency hazard, since it would replace the
pre-migration `.env` with the post-migration one and make rollback restore nothing.

**The gate is `security.credential_keychain`**, wired through all five config points (dataclass +
`_meta`, `load()`, `to_dict()` via `asdict`, the `_EDITABLE_CONFIG` PATCH allowlist, and the
generated `SCHEMA_REGISTRY`) plus a Settings toggle. `PERSONALCLAW_CREDENTIAL_BACKEND` now wins in
**both** directions so an explicit `dotenv` is the recovery lever for a machine whose secret
service stopped answering.

**Not a lifecycle gate/migration pair** — see the DEVIATION in the plan's execution log.
`.env.pre-keychain` is a `secret=True` inventory entry (so `portability.EXPORT_EXCLUDE`, a
projection of that set, excludes it) *and* a literal in `_inventory_secrets()`'s fallback, because
that fallback exists for when the inventory cannot be imported and that is exactly when a file
whose whole content is credentials must not become exportable.

`config/loader.py` was **5900 lines against a 6000-line absolute ceiling with a 100-line minimum
headroom assertion**, so the credential store moved to `config/credentials.py` (no re-export shim;
importers updated) — same reasoning as `agents/native/decision_tool_defs.py`. loader is now 5643.

### `SH-3` — Signing scheme decision + scripts/sign_app.py + in-tree public key; Store verifies signature at install; ScanReport/consent payload gains signature {state,signer}; unsigned stays community-tier installable

**Status:** done

Session 2 — Signed manifests + registry trust / T2.1, T2.2, V2; Design S2; Contracts C2; owner task 2 (generate signing key)

**Done when:** signing scheme doc records rationale (minisign recommended); signing+verifying a sample bundle round-trips locally; signed first-party bundle shows 'signed by PersonalClaw'; tampered signature refused with reason; unsigned bundle installs at community tier; consent UI renders the signature state

**DONE (2026-08-15):** the scheme decision is **detached Ed25519 signatures in minisign's on-wire
format over a whole-tree digest manifest** — recorded with its rejected alternatives in
`docs/security/signing.md` (Sigstore keyless: moves the trust root to a cert chain + transparency
log, so verifying an install would want network on a path that must be offline/deterministic, and
its recovery story is worse for a solo maintainer; signing `app.json` alone: the classic swap hole,
worse than nothing because it renders as trust; stdlib HMAC: symmetric, so every verifier could
forge; PyNaCl: a new wheel when `cryptography` was already transitive; hand-rolled Ed25519: never;
minisign's scrypt-encrypted SECRET-key format: key derivation for no security gain — the public-key
and signature formats ARE minisign's, so the reference CLI interoperates both ways).

`src/personalclaw/signing.py` is the verifier: a signed bundle carries `.pclaw-signature.sha256`
(canonical `pclaw-sig-v1` + sorted `sha256  relpath` for **every** file) and
`.pclaw-signature.sha256.minisig` over that file's exact bytes. Verification re-derives the manifest
from the tree and requires **byte equality**, so modified / **added** / removed / renamed files are
one comparison — the added-file case is the one a plain digest list misses. Nothing is excluded but
the two signature files; symlinks are refused rather than skipped, because an uncovered tree entry
IS the hole. Every failure path returns `invalid` with a reason (missing half, malformed base64,
short block, unknown algorithm, absent trusted comment, unknown key, non-verifying signature,
tampered trusted comment, manifest drift, and a missing Ed25519 backend — a signature that cannot be
checked is refused, not accepted).

`apps/app_manager.py::_signature_gate` runs it at step 3 of `install()` **and** `update()`, on the
quarantined staged copy, before the content scan and before the commit — `update()` included, or
"update" would be the way around signing. `invalid` is terminal and `confirm=True` does not override
it (consent covers risk, not tampering); `signed` raises a `community` origin to `official` and never
lowers a tier; `unsigned` installs unchanged at community tier. `ScanReport` gained
`signature: SignatureInfo` serializing `{state, signer, reason}`, defaulting to `unsigned` so a path
that never verified cannot render "signed by". The consent surface renders all three states
(`installConsent.tsx::SignatureRow`), and `useGuardedInstall.ts::terminalRefusalReason` replaced the
three copied `verdict === 'dangerous'` checks with one predicate, so the second terminal cause could
not be forgotten on one of the three install surfaces.

`scripts/sign_app.py` is the maintainer half (`gen-key` / `sign` / `verify`); the tests import THAT
module rather than a parallel test signer, so a broken verifier cannot be agreed with by a
sympathetic fixture. **No private key material is in the repo** — the trust store
(`src/personalclaw/trusted_keys/`, packaged via `pyproject.toml`) ships public halves only and tests
generate ephemeral keypairs at runtime, asserted by
`test_no_private_key_material_is_committed`. `cryptography>=42` is now a DECLARED core dep
(previously transitive via `pdfplumber`→`pdfminer.six`, so zero added install weight): asymmetric
signing is a requirement, not a preference, and a security control must not rest on someone else's
transitive dep.

`tests/security/test_app_signature.py` — 34 tests. The load-bearing one is `TestSwapTheUnsignedHalf`
(swap the payload / add an unlisted file / remove a signed file after signing → refused), with a
meta-assertion that builds the weak manifest-only check in-process and proves it WOULD pass the swap.
Ordering is measured, not asserted: the gate is instrumented to record step order and whether the
live app dir existed at verify time, and a tampered bundle's `setup.onInstall` marker file proves the
payload never executed. Falsified with 7 mutations, each reding ≥1 test: unconditional `signed` (24
red), manifest-only coverage (9), verify-after-scan (2, incl. "the scan ran after a terminal
signature refusal"), always-true Ed25519 (2), half-signature→`unsigned` (2), no tier elevation (1),
signer taken from the bundle's own comment (4).

**OWNER TASK 2 OUTSTANDING (not a blocker on this atom):** the production keypair is deliberately
NOT generated here — an agent must not create the private key it would then hand over. The trust
store therefore ships EMPTY, which is the safe direction: unknown key → refused, and unsigned
bundles are unaffected, so shipped behaviour is unchanged until the owner runs
`scripts/sign_app.py gen-key --signer PersonalClaw` and drops the `.pub` in. Until then "signed by
PersonalClaw" is provable only under an ephemeral key in tests. `SH-4` wires the release pipeline.
### `SH-4` — Release pipeline signs first-party app bundles + core release artifacts; registry records signer identity per listing

**Status:** todo

Session 2 T2.3

**Done when:** released bundles carry valid signatures verified in CI; registry validation script records signer per listing

### `SH-5` — Adversarial corpus harness (archive/integrity-race/verdict-evasion/invisible-char/degenerate-manifest) against SkillScanner/install_guarded + scanned==installed race invariant + nightly CI job + published methodology doc

**Status:** done

Session 3 — Adversarial gate testing / T3.1, T3.2, T3.3, V3; Design S3; Contracts C3; owner task 4 (approve publishing corpus)

**Done when:** each of the five attack classes has >=1 asserting test; a swap-after-scan race fixture proves scanned-bytes==installed-bytes holds; nightly job in full.yml runs the corpus; docs/security/scanner-testing.md lets an outsider reproduce; a deliberate scanner weakness on a branch turns the corpus red

**DONE (2026-08-15):** `tests/security/corpus/<class>/` carries 21 inert JSON cases across the
five classes named by SECURITY-HARDENING C3 (archive, integrity-race, verdict-evasion,
invisible-char, degenerate-manifest); `tests/security/test_scanner_adversarial.py` drives each
through `supply_chain.py::SkillScanner` and `skills/marketplace.py::install_scanned`, asserting the
specific refusal (not merely that a scan ran). The race invariant is measured, not argued: the
harness digests the quarantine tree at scan time (wrapping `supply_chain.py::scan_dir`, which
`install_scanned` resolves at call time) and asserts **map equality** against the installed tree
under three swap shapes — re-fetch, quarantine rewrite from a second thread, and in-memory payload
mutation — plus `fetch_calls == 1`. A `security-corpus` job in `full.yml` runs the corpus nightly.
`docs/security/scanner-testing.md` publishes the method, the five classes, the red-on-weakness
recipe table and four named residual risks. The weakness clause ships as a permanent meta-test
(`TestCorpusRedsOnAWeakenedScanner`): one control per class is weakened by monkeypatch and the
matching rail must red — no branch to remember to delete, and the shipped scanner is never weak.
Falsified on disk twice: blanking the `destructive_root` pattern reds 6 tests across three classes;
making the commit write bytes other than the scanned ones reds the race trio on
"installed bytes differ from scanned bytes". DEVIATION: no `hypothesis` strategies (T3.1 names
them) — it is not a dependency of this repo and adding one for a security-test atom is not worth
the supply-chain surface; the variant matrices in the corpus cases carry that coverage instead.

### `SH-6` — Baseline denylist as packaged data file (baseline_denylist.json + sha256) with integrity re-assert on read + periodic re-verify, SEL baseline_denylist_reasserted/_tamper_attempt events, strictly-additive user config, shared source with guardrails/denylist.py

**Status:** done

Amendment (2026-07-26) T3.4

**Done when:** mutating the in-memory denylist at runtime is healed on next denied_command_patterns() read and SEL-logged; effective set is provably a superset of the packaged baseline (property test); user additions still merge (dedupe, no shrink path); guardrails/denylist.py loads the same packaged source

### `SH-7` — Mode-independence matrix: baseline-matched command refused under default/auto/yolo/acceptEdits and trust simulators, deny-before-approval ordering regression-pinned, baseline-tamper corpus class added to S3 harness

**Status:** done

Amendment (2026-07-26) T3.5

**Done when:** every approval-mode fixture refuses a baseline-matched command; reordering the deny check below the approval gate turns CI red; a baseline-tamper corpus class is added to the S3 harness

### `SH-8` — SEL audit surface: paginated /api/security/audit (caller/operation/outcome/downstream_service/time filters) + /api/security/audit/verify wrapping verify_integrity + 'What did my agent do' Settings page with credential-safe JSONL export

**Status:** done

Session 4 — SEL surface + external review / T4.1, T4.2, V4; Design S4; Contracts C4

**Done when:** filters work; verify endpoint returns (checked, ok) with a tamper fixture showing ok=false; page renders real SEL events and shows a deliberately-broken chain link; export reuses redact and excludes secrets (fixture-verified); both themes/WCAG; export round-trips

### `SH-9` — External-review scoping doc: five high-risk paths, commissioned-vs-self-audit format, publication plan

**Status:** blocked (deliverable written; the remaining two done_when clauses are owner actions)

Session 4 T4.3; Design S4 (external review); owner task 3 (decide external review)

**Done when:** docs/security/review-scope.md lists the five high-risk paths, the review format, and the publication plan; scope approved by owner (owner task 3); review executed or scheduled with a date

**DONE (2026-08-15):** `docs/security/review-scope.md` written as a sibling of
`threat-model.md`/`limitations.md`. The five paths are the plan's own S4 list, each
grounded in modules verified to exist: webhook auth
(`dashboard/handlers/hooks.py::_verify_hook_token`), the app reverse-proxy token model
(`dashboard/handlers/apps.py::api_app_proxy` + `dashboard/token_auth.py`), scanner
bypasses (`apps/app_manager.py::install` + `supply_chain.py`), egress-guard layering
(`net/client.py` + `net/guard.py` + `net/policy.py::egress_policy_for`), and the inbound
MCP surface (`inbound/mcp_http.py`/`auth.py`/`caps.py`). Format section carries the
commissioned-vs-self-audit choice, a per-finding field table, a boundary-graded severity
scale, a two-required/one-preferred evidence bar, and a three-rule dispute resolution
(scope → `SECURITY.md`; enforcement → an executing test; severity → toward the reviewer
absent a named rail), with unresolved disputes published as disputes. Publication plan
publishes report + fix status + negatives + coverage gaps, withholds only unpatched
Critical/High until a fix ships (and exploit code permanently), lands at
`docs/security/reviews/<date>-<format>.md`, and publishes a dated slip note if a fix
passes the 30-day `SECURITY.md` window. **Owner-gated remainder:** the `done_when`'s
"scope approved by owner" + "executed or scheduled with a date" is owner task 3 — the
doc's "Approval and schedule" table carries the three unchecked decisions rather than
faking an approval.

### `SH-10` — Security panel: baseline denylist shown read-only with version + verified-hash indicator and 'N user additions'; anti-drift/anti-LLM-tamper (not anti-owner) limitation documented

**Status:** done

Amendment (2026-07-26) T4.4

**Done when:** the /api/security/denied-commands payload + security settings page render the baseline-verified state; a tamper fixture flips the indicator; 'N user additions' shown; the anti-drift/anti-LLM-tamper-not-anti-owner threat model is documented in docs/security/

