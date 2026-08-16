# Artifact signing

How PersonalClaw decides whether an app bundle is **from who it says it is**, and what
that buys. This is provenance, not content safety — the [supply-chain
scanner](./scanner-testing.md) is the separate question of whether the code is malicious.
A bundle can be validly signed and dangerous, or unsigned and harmless.

## The decision (SH-3)

**Detached Ed25519 signatures, in [minisign](https://jedisct1.github.io/minisign/)'s
on-wire format, over a whole-tree digest manifest.** One keypair, one public key shipped
in-tree, no certificate authority, no transparency log, no network at verify time.

### What gets signed

A signed bundle carries two files at its root:

| File | Contents |
|---|---|
| `.pclaw-signature.sha256` | The **signed payload**: `pclaw-sig-v1`, then one `<sha256>  <relpath>` line per file in the bundle, sorted by path. |
| `.pclaw-signature.sha256.minisig` | The detached minisign signature over the file above, byte for byte. |

The signature therefore covers **every byte of every file**, indirectly but completely.
This is the whole design, and it is a deliberate rejection of the obvious shape:

> **Signing `app.json` alone is worse than not signing.** It verifies, it renders "signed
> by PersonalClaw", and `scripts/setup.sh` — the file that actually executes as
> `setup.onInstall` — stays attacker-controlled. Signing one half of an artifact
> advertises trust the signature does not cover.

Verification re-derives the digest manifest from the tree on disk and requires **byte
equality** with the signed one. One comparison catches all four tamper shapes:

* a **modified** file (its digest changes),
* an **added** file (an extra line — the case a plain digest list misses, because every
  listed digest still matches),
* a **removed** file,
* a **renamed** file.

Nothing is excluded from the manifest except the two signature files themselves, which
cannot cover themselves. An exclusion list would be an unsigned region inside a signed
bundle — the same hole one level down. For the same reason a signed bundle may not contain
a **symlink**: a digest line describes content, and a symlink would either go uncovered or
be hashed through to a target outside the bundle. Unsigned bundles are unaffected by that
rule.

### Where verification runs

`personalclaw/apps/app_manager.py` → `install()` and `update()`, at step 3, on the
**quarantined staged copy**, before the content scan and before anything is committed to
the live app tree. Those staged bytes are exactly what `shutil.move`s into place; nothing
re-fetches in between. A `setup.onInstall` hook is remote-code-execution by design, so
"before the hook runs" is the property that matters, and
`tests/security/test_app_signature.py::test_a_tampered_bundle_never_runs_its_install_hook`
asserts it with a marker file the hook would have written.

`verify_bundle()` answers a question about a **staged** bundle. A *live* app dir
accumulates state the signer never saw (`installed.json`, the app's `data/`), so
re-verifying one legitimately reports drift; that is the wrong question, not tampering.

### The three states, and what each one does

| State | Meaning | Install behaviour |
|---|---|---|
| `signed` | Ed25519 verifies against an in-tree key, and the tree matches the signed manifest. | Installs. A `community`-origin bundle is raised to **`official`** trust tier — proven provenance buys exactly what the curated registry already has. Never lowers a tier. |
| `unsigned` | Neither signature file present. | **Installs, at community tier.** Graduated trust (contract C2): unsigned is a *state*, not a verdict, and never a hard wall. Most community apps are unsigned and that is fine. |
| `invalid` | Anything else. | **Refused, terminal.** `confirm=True` does **not** override it. |

`invalid` is deliberately not consentable. A warning verdict asks the user to weigh a
risk; a broken signature says the artifact is not the bytes its signature covers, and
there is nothing for a user to weigh about that. Every refusal carries a `reason` that
names the offending path, and the install-consent surface renders it.

Fail-closed cases that all land on `invalid`: one signature file present without the
other (deleting one is exactly how you would demote a signed bundle to the permissive
path), malformed base64, wrong block lengths, an unknown algorithm, a missing trusted
comment, a key id absent from the trust store, a signature that does not verify, a
tampered trusted comment, a digest-manifest mismatch, a symlink, and **a missing Ed25519
backend** — a signature that cannot be checked is not a signature that is accepted.

### The trust store

`src/personalclaw/trusted_keys/<Signer>.pub`, shipped in the wheel. The **filename stem
is the signer identity**: `PersonalClaw.pub` renders as `signed by PersonalClaw`. The
identity comes from the packaged tree and never from a comment inside the signature or
key file, so a bundle author cannot choose the name their bundle is attributed to.

A key id in a signature that is not in the store is an unknown key → refused. Adding a
`.pub` here is therefore a deliberate act of trust.

**No private key material lives in this repository** — not the maintainer key, not a test
fixture, not an example. Tests generate ephemeral keypairs at runtime and point
`signing.trusted_keys_dir()` at a tmp dir.

## Rejected alternatives

**Sigstore keyless (OIDC + Fulcio + Rekor).** The design note offered it as the
alternative and it is the stronger *supply-chain* story: no long-lived private key to
lose, and a public transparency log. Rejected for this atom on three counts. (1) It moves
the trust root from one in-tree public key to a certificate chain plus a log, so verifying
an app install would want network access — unacceptable on a path that must work offline
and deterministically, which is the same constraint that keeps the scanner LLM-free. (2)
Its recovery story is worse for a solo-maintained project: the failure mode of the simple
scheme is "generate a new keypair, ship the new `.pub`", which a single maintainer can
actually execute. (3) It binds the trust model to a CI identity provider, which is a
key-distribution *policy* commitment, and this atom's job is the mechanism.
Sigstore remains a reasonable later migration; it is not a prerequisite.

**Signing `app.json` only.** Covered above: it is the classic swap hole, and it is worse
than nothing because it renders as trust.

**HMAC-SHA256 over `hashlib`/`hmac` (stdlib only).** Tempting — no dependency at all — and
wrong by construction. Symmetric MACs make the verifying key the signing key, so every
user's machine would hold everything needed to forge a "signed by PersonalClaw" bundle.
Asymmetric signing is not a preference here, it is the requirement.

**PyNaCl for Ed25519.** A new wheel, when `cryptography` was already present
transitively (`pdfplumber` → `pdfminer.six` → `cryptography`). No advantage.

**A hand-rolled pure-Python Ed25519.** Never on a signature-verification path.

**minisign's scrypt-encrypted secret-key file format.** The *public key* and *signature*
formats are minisign's, so the reference CLI interoperates in both directions. The secret
key deliberately is not: parsing minisign's encrypted secret key would mean shipping key
derivation and passphrase handling for no security gain, when the actual protection is
"the seed lives in a password manager and a CI secret, never on disk in CI" — and a CI
secret holds a base64 blob anyway. `scripts/sign_app.py` reads a `<Signer>.seed` file: a
mode-0600 base64 `key_id || ed25519_seed`.

## Maintainer workflow

```sh
# 1. Generate the keypair. Refuses to overwrite existing key material.
python3 scripts/sign_app.py gen-key --signer PersonalClaw --out-dir ~/pclaw-keys

# 2. Ship the PUBLIC half.
cp ~/pclaw-keys/PersonalClaw.pub src/personalclaw/trusted_keys/

# 3. Store the SECRET half: password manager + the CI `release` environment secret.
#    Then delete the local copy. Losing it breaks the trust chain for signed artifacts.

# 4. Sign a bundle (prints a round-trip self-check).
python3 scripts/sign_app.py sign apps/my-app --seed ~/pclaw-keys/PersonalClaw.seed

# 5. Verify with the shipped verifier — exit 0 only on `signed`.
python3 scripts/sign_app.py verify apps/my-app
```

Interoperability check with the reference implementation, if you have it installed:

```sh
minisign -Vm apps/my-app/.pclaw-signature.sha256 -p src/personalclaw/trusted_keys/PersonalClaw.pub
```

### Current state

The mechanism is live on every app install and update. The trust store ships **empty**
until the maintainer signing key is generated (SECURITY-HARDENING owner task 2), which is
the safe direction: no signature verifies, so nothing is falsely attributed, and unsigned
bundles keep installing at community tier exactly as before. Wiring the release pipeline
to sign first-party bundles is `SH-4`.

## What this does not do

* It does not make a signed bundle safe. The scanner still runs, and a `dangerous` verdict
  still refuses regardless of who signed it.
* It does not protect the live app tree after install. Post-install tampering is a
  different control (see [the threat model](./threat-model.md)).
* It is not anti-owner. A user with write access to their own installed wheel can add a
  key to the trust store. The threat modelled here is a malicious or compromised *bundle
  source*, not a hostile local administrator — the same boundary
  [`limitations.md`](./limitations.md) draws.
