#!/usr/bin/env python3
"""Maintainer tool — sign an app bundle so the Store verifies it at install (SH-3).

Three subcommands:

  gen-key   generate an Ed25519 keypair: ``<Signer>.pub`` (minisign format, goes in
            ``src/personalclaw/trusted_keys/``) + ``<Signer>.seed`` (the SECRET, 0600).
  sign      write ``.pclaw-signature.sha256`` (a canonical whole-tree digest manifest)
            and ``.pclaw-signature.sha256.minisig`` (the detached signature over it).
  verify    run the shipped verifier (``personalclaw.signing.verify_bundle``) against a
            bundle and print the state/signer/reason. The round-trip check.

**Why the signature covers a digest manifest and not just ``app.json``:** signing the
manifest alone leaves ``scripts/`` unsigned, so an attacker swaps the unsigned half and
the signature still checks out. The manifest lists a sha256 for EVERY file, and the
verifier re-derives it from the tree and demands byte equality, so a changed, added,
removed or renamed file all fail one comparison.

**Signature/public-key format is minisign's**, so ``minisign -Vm`` can verify what this
writes and ``minisign -G`` can produce keys this reads. The SECRET key file is
deliberately *not* minisign's scrypt-encrypted format: that would mean shipping key
derivation and passphrase handling for no security gain, when the real protection is
"the seed lives in a password manager and a CI secret, never on disk in CI". A raw
base64 seed is what a CI secret holds anyway. Full rationale + rejected alternatives:
``docs/security/signing.md``.

This script never reads the trust store and never installs anything. It is a maintainer
tool; nothing in the runtime imports it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from personalclaw.signing import (  # noqa: E402  (path bootstrap must precede the import)
    MANIFEST_FILENAME,
    SIGNATURE_FILENAME,
    ManifestError,
    TrustedKey,
    build_digest_manifest,
    verify_bundle,
)

_ALG_PREHASHED = b"ED"
_SEED_HEADER = "# PersonalClaw signing SEED — SECRET. Never commit. Never share."


# ── key generation ──────────────────────────────────────────────────────────────


def _ed25519() -> tuple[object, object]:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - maintainer environment
        raise SystemExit(
            "cryptography is required to sign (it is a core dependency: pip install -e .)"
        ) from exc
    return Ed25519PrivateKey, Ed25519PublicKey


def gen_key(signer: str, out_dir: Path) -> tuple[Path, Path]:
    """Generate a keypair. Refuses to overwrite: silently replacing a signing key would
    invalidate every artifact already signed with the old one."""
    private_cls, _ = _ed25519()
    from cryptography.hazmat.primitives import serialization

    out_dir.mkdir(parents=True, exist_ok=True)
    pub_path = out_dir / f"{signer}.pub"
    seed_path = out_dir / f"{signer}.seed"
    for path in (pub_path, seed_path):
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing key material: {path}")

    seed = secrets.token_bytes(32)
    private = private_cls.from_private_bytes(seed)  # type: ignore[attr-defined]
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    key_id = secrets.token_bytes(8)

    blob = _ALG_PREHASHED + key_id + public
    pub_path.write_text(
        f"untrusted comment: PersonalClaw signing key {signer} "
        f"(key id {key_id.hex()})\n{base64.b64encode(blob).decode()}\n",
        encoding="utf-8",
    )
    seed_path.write_text(
        f"{_SEED_HEADER}\n# signer: {signer}\n# key id: {key_id.hex()}\n"
        f"{base64.b64encode(key_id + seed).decode()}\n",
        encoding="utf-8",
    )
    os.chmod(seed_path, 0o600)
    return pub_path, seed_path


def _read_seed(path: Path) -> tuple[bytes, bytes]:
    """Return ``(key_id, seed)`` from a ``.seed`` file."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    body = [ln for ln in lines if not ln.lstrip().startswith("#")]
    if not body:
        raise SystemExit(f"no key material in {path}")
    raw = base64.b64decode(body[-1].strip(), validate=True)
    if len(raw) != 40:
        raise SystemExit(f"expected a 40-byte key id + seed in {path}, got {len(raw)}")
    return raw[:8], raw[8:]


# ── signing ─────────────────────────────────────────────────────────────────────


def sign_bundle(bundle: Path, seed_path: Path, *, trusted_comment: str = "") -> Path:
    """Write the digest manifest + its detached minisign signature into ``bundle``."""
    private_cls, _ = _ed25519()
    key_id, seed = _read_seed(seed_path)
    private = private_cls.from_private_bytes(seed)  # type: ignore[attr-defined]

    # Remove any stale signature first, so the manifest is built over the same tree the
    # verifier will see (a leftover .minisig is excluded from the manifest either way,
    # but a stale manifest FILE would otherwise be regenerated from itself).
    (bundle / MANIFEST_FILENAME).unlink(missing_ok=True)
    (bundle / SIGNATURE_FILENAME).unlink(missing_ok=True)

    try:
        manifest = build_digest_manifest(bundle)
    except ManifestError as exc:
        raise SystemExit(f"cannot sign: {exc}") from exc
    (bundle / MANIFEST_FILENAME).write_bytes(manifest)

    comment = trusted_comment or f"timestamp:0\tfile:{MANIFEST_FILENAME}"
    signature = private.sign(hashlib.blake2b(manifest, digest_size=64).digest())
    global_signature = private.sign(signature + comment.encode("utf-8"))

    sig_path = bundle / SIGNATURE_FILENAME
    sig_path.write_text(
        "untrusted comment: signature from PersonalClaw signing key\n"
        f"{base64.b64encode(_ALG_PREHASHED + key_id + signature).decode()}\n"
        f"trusted comment: {comment}\n"
        f"{base64.b64encode(global_signature).decode()}\n",
        encoding="utf-8",
    )
    return sig_path


def _own_key(seed_path: Path) -> dict[bytes, TrustedKey]:
    """A one-entry trust store derived from the seed — for the sign-time round-trip."""
    private_cls, _ = _ed25519()
    from cryptography.hazmat.primitives import serialization

    key_id, seed = _read_seed(seed_path)
    private = private_cls.from_private_bytes(seed)  # type: ignore[attr-defined]
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {key_id: TrustedKey(signer=seed_path.stem, key_id=key_id, public_key=public)}


# ── CLI ─────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("gen-key", help="generate a signing keypair")
    p_gen.add_argument("--signer", required=True, help="signer identity, e.g. PersonalClaw")
    p_gen.add_argument("--out-dir", type=Path, required=True)

    p_sign = sub.add_parser("sign", help="sign an app bundle directory")
    p_sign.add_argument("bundle", type=Path)
    p_sign.add_argument("--seed", type=Path, required=True, help="path to the .seed secret")

    p_verify = sub.add_parser("verify", help="verify a bundle with the shipped verifier")
    p_verify.add_argument("bundle", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "gen-key":
        pub, seed = gen_key(args.signer, args.out_dir)
        print(f"public key: {pub}")
        print(f"SECRET seed: {seed}  (mode 0600 — move it to your password manager)")
        print(f"install the public half: cp {pub} src/personalclaw/trusted_keys/")
        return 0

    if args.cmd == "sign":
        if not args.bundle.is_dir():
            raise SystemExit(f"not a directory: {args.bundle}")
        sig = sign_bundle(args.bundle, args.seed)
        print(f"signed {args.bundle} → {sig.name}")
        # Round-trip against the key we just signed with, NOT the packaged trust store:
        # this asserts the crypto, and the next line separately reports whether the
        # verifying end actually trusts this signer yet.
        info = verify_bundle(args.bundle, keys=_own_key(args.seed))
        print(f"self-check: state={info.state.value} signer={info.signer or '-'} {info.reason}")
        store = verify_bundle(args.bundle)
        print(f"packaged trust store: state={store.state.value} {store.reason or '(trusted)'}")
        return 0 if info.state.value == "signed" else 1

    if not args.bundle.is_dir():
        raise SystemExit(f"not a directory: {args.bundle}")
    info = verify_bundle(args.bundle)
    print(f"state={info.state.value} signer={info.signer or '-'} reason={info.reason or '-'}")
    return 0 if info.state.value == "signed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
