# In-tree signing trust store

Every `*.pub` in this directory is a **minisign-format Ed25519 public key** that
`personalclaw.signing.verify_bundle()` will accept as a bundle signer. It ships inside
the wheel, so verifying an app bundle needs no network and no key-distribution protocol
— the keys travel with the code that uses them, at the same trust level as the code.

**The filename stem is the signer identity.** `PersonalClaw.pub` renders as
`signed by PersonalClaw` on the install-consent surface. The identity deliberately comes
from the packaged filename and never from a comment inside the key file, so a bundle
author cannot choose the name their bundle is attributed to.

A key id that appears in a signature but not here is an **unknown key** and the install
is refused. Adding a key is therefore a deliberate act of trust: a `.pub` landing here
means "signatures from this key install at official tier".

## No private key material lives in this repository

Not the maintainer key, not a test key, not an example key. Tests generate ephemeral
keypairs at runtime and monkeypatch `signing.trusted_keys_dir()` at a tmp dir, so the
whole verification path is exercised without a checked-in secret.

## Adding the maintainer key

See `docs/security/signing.md` for the full workflow. In short:

```sh
python3 scripts/sign_app.py gen-key --signer PersonalClaw --out-dir ~/keys
cp ~/keys/PersonalClaw.pub src/personalclaw/trusted_keys/
# then store ~/keys/PersonalClaw.seed in the password manager + the CI `release`
# environment secret, and delete the local copy.
```

Until a real key is added here the trust store is empty, which is the **safe** default:
no signature verifies (unknown key → refused), and unsigned bundles keep installing at
community tier exactly as before. Verification is live from the first install — it is the
signer list that the maintainer populates, not the mechanism.
