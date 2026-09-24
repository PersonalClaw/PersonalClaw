"""Artifact signing — the minisign-format verifier the Store runs BEFORE an install.

**Scheme (SH-3 decision):** detached **Ed25519 signatures in minisign's on-wire
format** over a **whole-tree digest manifest**. Rationale, rejected alternatives and
the maintainer workflow live in ``docs/security/signing.md``; this module is the
verifying half and the format's single source of truth. ``scripts/sign_app.py`` is
the signing half.

Two facts make this safe rather than decorative:

1. **The signature covers every byte that gets installed, not just the manifest.**
   A signed bundle carries ``.pclaw-signature.sha256`` — a canonical, sorted
   ``sha256  relpath`` line per file in the tree — and ``.pclaw-signature.sha256.minisig``,
   the detached signature over that file's exact bytes. Verification re-derives the
   digest manifest from the tree on disk and requires **byte equality** with the signed
   one, so a changed file, an ADDED file, a removed file and a rename are all one
   comparison. Signing only ``app.json`` would leave ``scripts/`` unsigned and swappable;
   that is the hole this shape closes (see ``tests/security/test_app_signature.py``).
2. **Every failure is fail-closed.** Missing half, malformed base64, wrong lengths,
   unknown key, bad signature, digest mismatch, a symlink the manifest cannot honestly
   cover, or a missing Ed25519 backend all return :attr:`SignatureState.INVALID` with a
   reason. A parse error can never read as "valid" — the only path to
   :attr:`SignatureState.SIGNED` is every check passing. Absence of BOTH signature files
   is :attr:`SignatureState.UNSIGNED`, which is a *state*, not a verdict: unsigned
   bundles stay installable at community tier (graduated trust, SECURITY-HARDENING C2).

**Trust store.** ``personalclaw/trusted_keys/<Signer>.pub`` — minisign-format public
keys, shipped in-tree so a verifying user needs no network and no key-distribution
protocol. The **filename stem is the signer identity** ("PersonalClaw.pub" →
``signed by PersonalClaw``): it comes from the packaged tree, so an attacker cannot
choose the name their bundle is attributed to by editing a comment in their own file.
A key id present in a signature but absent from the store is an unknown key → refused.

**Scope: this verifies a STAGED bundle, not a live one.** The install gate calls
:func:`verify_bundle` on the quarantined copy, and those exact bytes are what moves into
place. A *live* app dir accumulates gateway-written state the signer never saw
(``installed.json``, the app's ``data/``), so re-verifying one legitimately reports drift
— that is not tampering, it is the wrong question. The manifest deliberately excludes no
path other than the two signature files: an exclusion list would be an unsigned region
inside a signed bundle, which is precisely the hole this design closes.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

# ── Format constants ────────────────────────────────────────────────────────────

#: The signed digest manifest, at the bundle root.
MANIFEST_FILENAME = ".pclaw-signature.sha256"
#: The detached minisign signature over :data:`MANIFEST_FILENAME`'s exact bytes.
SIGNATURE_FILENAME = MANIFEST_FILENAME + ".minisig"

#: First line of the digest manifest. A domain tag, so a signature over some other
#: sorted-digest-list artifact can never be replayed as a bundle signature.
MANIFEST_HEADER = "pclaw-sig-v1"

#: minisign algorithm ids. ``ED`` prehashes the message with BLAKE2b-512 (minisign's
#: default since 0.10 and the only mode ``rsign2`` writes); ``Ed`` signs the message
#: directly (legacy). Both are accepted: the algorithm bytes are not themselves signed,
#: but relabelling one as the other changes what Ed25519 is verified over, so a
#: relabelled signature simply fails — there is no downgrade to buy.
_ALG_PREHASHED = b"ED"
_ALG_LEGACY = b"Ed"

_KEY_ID_LEN = 8
_ED25519_PUBLIC_LEN = 32
_ED25519_SIG_LEN = 64

_TRUSTED_KEYS_DIRNAME = "trusted_keys"


class SignatureState(str, Enum):
    """The three states a bundle's signature can be in.

    Deliberately NOT a severity ladder: ``UNSIGNED`` is benign (most community apps),
    ``INVALID`` is terminal. Anything that is not provably ``SIGNED`` and not provably
    absent is ``INVALID``."""

    SIGNED = "signed"
    UNSIGNED = "unsigned"
    INVALID = "invalid"


@dataclass
class SignatureInfo:
    """Contract C2's ``signature: {state, signer}``, plus the ``reason`` the atom's
    "tampered signature refused **with reason**" clause requires.

    Defaults to ``unsigned``: a :class:`~personalclaw.supply_chain.ScanReport` produced
    by a path that never looked for a signature is truthfully reporting that it saw
    none, not claiming a verified one."""

    state: SignatureState = SignatureState.UNSIGNED
    signer: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "signer": self.signer, "reason": self.reason}

    @property
    def is_invalid(self) -> bool:
        return self.state is SignatureState.INVALID


@dataclass(frozen=True)
class TrustedKey:
    """One in-tree public key. ``signer`` is the file stem, never file content."""

    signer: str
    key_id: bytes
    public_key: bytes


@dataclass
class _Minisig:
    algorithm: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes


# ── Trust store ─────────────────────────────────────────────────────────────────


def trusted_keys_dir() -> Path:
    """The packaged trust store. A function, not a constant, so a test can monkeypatch
    it to a tmp dir holding an EPHEMERAL keypair — no private key material ever lives
    in this repository, so the shipped store is populated by the maintainer (owner task
    2) and every test generates its own."""
    return Path(__file__).resolve().parent / _TRUSTED_KEYS_DIRNAME


def load_trusted_keys() -> dict[bytes, TrustedKey]:
    """Parse every ``*.pub`` in the trust store, keyed by key id.

    Unreadable or malformed key files are SKIPPED, not fatal: a corrupt key file must
    not be able to disable verification for the keys that parse fine. The consequence
    of skipping is strictly stricter (its signatures become "unknown key" → refused)."""
    keys: dict[bytes, TrustedKey] = {}
    root = trusted_keys_dir()
    if not root.is_dir():
        return keys
    for path in sorted(root.glob("*.pub")):
        try:
            blob = _decode_key_line(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if len(blob) != 2 + _KEY_ID_LEN + _ED25519_PUBLIC_LEN:
            continue
        if blob[:2] not in (_ALG_PREHASHED, _ALG_LEGACY):
            continue
        key_id = blob[2 : 2 + _KEY_ID_LEN]
        keys[key_id] = TrustedKey(
            signer=path.stem,
            key_id=key_id,
            public_key=blob[2 + _KEY_ID_LEN :],
        )
    return keys


def _decode_key_line(text: str) -> bytes:
    """minisign public-key file: an ``untrusted comment:`` line then one base64 line."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty key file")
    return base64.b64decode(lines[-1].strip(), validate=True)


# ── Digest manifest ─────────────────────────────────────────────────────────────


class ManifestError(ValueError):
    """The tree cannot be honestly described by a digest manifest (e.g. a symlink)."""


def build_digest_manifest(root: Path) -> bytes:
    """Canonical whole-tree digest manifest for ``root`` — the bytes that get signed.

    Deterministic by construction: POSIX relative paths, sorted, one
    ``<sha256hex>  <relpath>`` line each, the two signature files themselves excluded
    (they cannot cover themselves). Every other file is covered, hidden ones included.

    **Symlinks raise.** A digest line describes file CONTENT, so a symlink would either
    be silently skipped (an uncovered tree entry — the swap hole) or hashed through to a
    target outside the bundle (a lie about what was signed). Neither is acceptable on a
    signature path, so a signed bundle may not contain one. Unsigned bundles are
    untouched by this rule."""
    lines = [MANIFEST_HEADER]
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ManifestError(f"bundle contains a symlink, which cannot be signed: {rel}")
        if not path.is_file():
            continue
        if rel in (MANIFEST_FILENAME, SIGNATURE_FILENAME):
            continue
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# ── Verification ────────────────────────────────────────────────────────────────


def verify_bundle(root: Path, *, keys: dict[bytes, TrustedKey] | None = None) -> SignatureInfo:
    """Verify ``root``'s signature. **Call this before the bundle is copied live.**

    Returns :class:`SignatureInfo`; never raises for untrusted input (a malformed
    attacker-supplied file is a refusal, not a traceback). ``keys`` defaults to the
    packaged trust store."""
    manifest_path = root / MANIFEST_FILENAME
    sig_path = root / SIGNATURE_FILENAME
    have_manifest = manifest_path.is_file()
    have_sig = sig_path.is_file()

    if not have_manifest and not have_sig:
        return SignatureInfo(state=SignatureState.UNSIGNED)
    # Half a signature is not "unsigned": deleting one file is exactly how an attacker
    # would try to demote a signed bundle to the permissive path.
    if not have_manifest:
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason=f"signature present but {MANIFEST_FILENAME} is missing",
        )
    if not have_sig:
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason=f"{MANIFEST_FILENAME} present but {SIGNATURE_FILENAME} is missing",
        )

    try:
        signed_manifest = manifest_path.read_bytes()
        sig_text = sig_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SignatureInfo(
            state=SignatureState.INVALID, reason=f"signature files unreadable: {exc}"
        )

    try:
        sig = _parse_minisig(sig_text)
    except ValueError as exc:
        return SignatureInfo(state=SignatureState.INVALID, reason=f"malformed signature: {exc}")

    store = load_trusted_keys() if keys is None else keys
    key = store.get(sig.key_id)
    if key is None:
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason=f"unknown signing key {sig.key_id.hex()} (not in the in-tree trust store)",
        )

    verify = _ed25519_verifier()
    if verify is None:
        # Fail CLOSED: a signature we cannot check is not a signature we accept.
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason="cannot verify signature: no Ed25519 backend available",
        )

    if sig.algorithm == _ALG_PREHASHED:
        signed_bytes = hashlib.blake2b(signed_manifest, digest_size=64).digest()
    else:
        signed_bytes = signed_manifest
    if not verify(key.public_key, sig.signature, signed_bytes):
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason=f"signature does not verify against {key.signer}'s key",
        )
    # minisign's second signature binds the trusted comment to the first one; skipping it
    # would let anyone rewrite the "signed by" prose that a UI might show.
    if not verify(
        key.public_key,
        sig.global_signature,
        sig.signature + sig.trusted_comment.encode("utf-8"),
    ):
        return SignatureInfo(
            state=SignatureState.INVALID, reason="trusted-comment signature does not verify"
        )

    # The signature is authentic over the manifest. Now prove the manifest describes the
    # tree we are about to install — byte equality catches changed/added/removed/renamed
    # files in one comparison.
    try:
        actual = build_digest_manifest(root)
    except ManifestError as exc:
        return SignatureInfo(state=SignatureState.INVALID, reason=str(exc))
    except OSError as exc:
        return SignatureInfo(state=SignatureState.INVALID, reason=f"cannot digest bundle: {exc}")
    if actual != signed_manifest:
        return SignatureInfo(
            state=SignatureState.INVALID,
            reason=_describe_manifest_drift(signed_manifest, actual),
        )

    return SignatureInfo(state=SignatureState.SIGNED, signer=key.signer)


def _parse_minisig(text: str) -> _Minisig:
    """Parse minisign's four-line ``.minisig``. Strict: exact lengths, known algorithm,
    both signature lines required."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        raise ValueError("expected 4 lines (comment, signature, trusted comment, global signature)")
    try:
        blob = base64.b64decode(lines[1].strip(), validate=True)
        global_sig = base64.b64decode(lines[3].strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"base64 decode failed: {exc}") from exc
    if len(blob) != 2 + _KEY_ID_LEN + _ED25519_SIG_LEN:
        raise ValueError(f"signature block is {len(blob)} bytes, expected 74")
    if len(global_sig) != _ED25519_SIG_LEN:
        raise ValueError(f"global signature is {len(global_sig)} bytes, expected 64")
    algorithm = blob[:2]
    if algorithm not in (_ALG_PREHASHED, _ALG_LEGACY):
        raise ValueError(f"unsupported signature algorithm {algorithm!r}")
    marker = "trusted comment:"
    if not lines[2].startswith(marker):
        raise ValueError("missing trusted comment line")
    return _Minisig(
        algorithm=algorithm,
        key_id=blob[2 : 2 + _KEY_ID_LEN],
        signature=blob[2 + _KEY_ID_LEN :],
        trusted_comment=lines[2][len(marker) :].lstrip(),
        global_signature=global_sig,
    )


def _ed25519_verifier() -> "Callable[[bytes, bytes, bytes], bool] | None":
    """Return ``verify(public_key, signature, message) -> bool``, or ``None`` if no
    Ed25519 backend is importable.

    ``cryptography`` is a declared core dependency, so ``None`` means a broken
    environment rather than a supported configuration — and the caller treats it as a
    refusal, not a pass."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:  # pragma: no cover - exercised via monkeypatch, not a real env
        return None

    def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        except (InvalidSignature, ValueError):
            return False
        return True

    return verify


def _describe_manifest_drift(signed: bytes, actual: bytes) -> str:
    """A precise, bounded reason for a coverage mismatch — the difference between
    "refused" and "refused because scripts/setup.sh is not the file that was signed"."""
    signed_map = _manifest_map(signed)
    actual_map = _manifest_map(actual)
    added = sorted(set(actual_map) - set(signed_map))
    removed = sorted(set(signed_map) - set(actual_map))
    changed = sorted(p for p in set(signed_map) & set(actual_map) if signed_map[p] != actual_map[p])
    parts: list[str] = []
    if changed:
        parts.append("modified after signing: " + ", ".join(changed[:5]))
    if added:
        parts.append("not covered by the signature: " + ", ".join(added[:5]))
    if removed:
        parts.append("signed but missing: " + ", ".join(removed[:5]))
    if not parts:
        parts.append("digest manifest does not match the bundle")
    return "bundle contents do not match the signature — " + "; ".join(parts)


def _manifest_map(blob: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in blob.decode("utf-8", errors="replace").splitlines()[1:]:
        digest, _, rel = line.partition("  ")
        if rel:
            out[rel] = digest
    return out


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_HEADER",
    "SIGNATURE_FILENAME",
    "ManifestError",
    "SignatureInfo",
    "SignatureState",
    "TrustedKey",
    "build_digest_manifest",
    "load_trusted_keys",
    "trusted_keys_dir",
    "verify_bundle",
]
