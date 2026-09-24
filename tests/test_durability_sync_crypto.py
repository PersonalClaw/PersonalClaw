"""DAS-8 — sync end-to-end encryption + the SYNC egress derivation (DURABILITY-AND-SYNC §4.4/§4.3).

Every claim here is asserted on an ARTIFACT rather than on a call: the plaintext bytes are
absent from the ciphertext, a wrong key REFUSES rather than returning garbage, a flipped byte
is detected, nonces are unique across many shards, the derived egress policy actually refuses a
non-pinned host through the real guard, and the planted passphrase appears in none of the
artifacts the failure path produces. A test that only checked "encrypt was called" would stay
green through every one of those defects.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

from personalclaw.durability import crypto as sc
from personalclaw.durability.cursor import PAYLOAD_BAD, PREREQ_ABSENT
from personalclaw.durability.registry import REGISTRY_KEY
from personalclaw.net.guard import evaluate
from personalclaw.net.policy import SYNC, SyncEndpointRefused, sync_egress_policy
from personalclaw.sync_transports.base import (
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

# A value that must never appear in a ciphertext, a log, an exception or a pushed object.
CANARY_ROW = b"CANARY-ROW-alice@example.com-salary-142000"
CANARY_PASSPHRASE = "CANARY-PASSPHRASE-do-not-log-2f7a1c"

SALT = b"0123456789abcdef"
SHARD_KEY = "machines/A/seq-0001/tasks/entities.jsonl"


def _master(passphrase: str = "a correct horse battery staple") -> bytes:
    return sc.derive_master(passphrase, SALT)


# ── a REAL on-disk transport (the dir-sync algorithm), so proofs land on real bytes ──


class FolderTransport(SyncTransportProvider):
    """Insert-only atomic writes into a shared folder — the same shape the convergence
    e2e test uses, so "the remote" is a directory whose bytes a test can read directly."""

    name = "dir-sync"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def push(self, objects):
        pushed = skipped = 0
        for obj in objects:
            target = self._root / obj.key
            if target.exists():
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(obj.data)
            pushed += 1
        return PushResult(pushed=pushed, skipped=skipped, outcome="delivered")

    def list_remote(self, prefix: str = ""):
        if not self._root.is_dir():
            return []
        refs = []
        for dirpath, _dirs, files in os.walk(self._root):
            for fn in files:
                full = Path(dirpath) / fn
                key = full.relative_to(self._root).as_posix()
                if key.startswith(prefix):
                    refs.append(RemoteRef(key=key, size=full.stat().st_size))
        return refs

    def pull(self, refs):
        out = []
        for ref in refs:
            try:
                out.append(SyncObject(key=ref.key, data=(self._root / ref.key).read_bytes()))
            except OSError:
                continue
        return out

    def cas_registry(self, expected_sha, data):
        target = self._root / REGISTRY_KEY
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return True

    def test(self):
        from personalclaw.sync_transports.base import ConnectionResult

        return ConnectionResult(ok=True)


# ── the crypto claims, asserted on bytes ─────────────────────────────────────


class TestCiphertextIsActuallyCiphertext:
    def test_plaintext_bytes_are_absent_from_the_encrypted_object(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        assert CANARY_ROW not in enc.data, "the plaintext row survived into the ciphertext"
        # ...and no non-trivial slice of it either (a partial leak is still a leak).
        for size in (8, 16, 24):
            assert CANARY_ROW[:size] not in enc.data
        assert sc.is_ciphertext(enc.data)

    def test_the_object_key_stays_plaintext_because_it_is_routing(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        # §4.4: the machine id and seq must be readable without the key.
        assert enc.key == SHARD_KEY

    def test_a_wrong_key_refuses_rather_than_returning_garbage(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        wrong = sc.derive_master("a different passphrase entirely", SALT)
        with pytest.raises(sc.SyncEncryptionError):
            sc.decrypt_object(enc, wrong)

    def test_a_wrong_salt_refuses_too(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        with pytest.raises(sc.SyncEncryptionError):
            sc.decrypt_object(enc, sc.derive_master("a correct horse battery staple", b"f" * 16))

    @pytest.mark.parametrize("offset", [0, 4, 9, 14, 20, -1])
    def test_one_tampered_byte_is_detected(self, offset):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        raw = bytearray(enc.data)
        raw[offset] ^= 0x01
        with pytest.raises(sc.SyncEncryptionError):
            sc.decrypt_object(SyncObject(key=SHARD_KEY, data=bytes(raw)), _master())

    def test_a_ciphertext_relocated_to_another_key_is_detected(self):
        """The object key is authenticated, so whoever holds the bucket cannot replay
        machine A's shard as machine B's or an old seq's as a new one."""
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        moved = SyncObject(key="machines/B/seq-0009/tasks/entities.jsonl", data=enc.data)
        with pytest.raises(sc.SyncEncryptionError):
            sc.decrypt_object(moved, _master())

    def test_a_truncated_object_is_refused_not_parsed(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        for cut in (4, len(sc.MAGIC), len(sc.MAGIC) + 5, len(enc.data) - 1):
            with pytest.raises(sc.SyncEncryptionError):
                sc.decrypt_object(SyncObject(key=SHARD_KEY, data=enc.data[:cut]), _master())

    def test_a_version_downgrade_is_refused(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        raw = bytearray(enc.data)
        raw[len(sc.MAGIC)] = 0x00
        with pytest.raises(sc.SyncEncryptionError, match="version"):
            sc.decrypt_object(SyncObject(key=SHARD_KEY, data=bytes(raw)), _master())

    def test_round_trip_returns_the_exact_bytes(self):
        m = _master()
        for payload in (b"", b"x", CANARY_ROW, os.urandom(200_000)):
            enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=payload), m)
            assert sc.decrypt_object(enc, m).data == payload


class TestNonceUniqueness:
    def test_nonces_are_unique_across_many_shards(self):
        """A per-shard counter that restarts is the classic GCM catastrophe, so this
        asserts uniqueness over a population, not merely that a nonce exists."""
        m = _master()
        nonces = set()
        n = 500
        for i in range(n):
            enc = sc.encrypt_object(
                SyncObject(key=f"machines/A/seq-{i:04d}/tasks/entities.jsonl", data=CANARY_ROW), m
            )
            head = len(sc.MAGIC) + 1
            nonces.add(enc.data[head : head + sc.NONCE_BYTES])
        assert len(nonces) == n, f"nonce reuse: only {len(nonces)} distinct nonces over {n} objects"

    def test_the_same_object_encrypted_twice_gets_a_fresh_nonce(self):
        m = _master()
        obj = SyncObject(key=SHARD_KEY, data=CANARY_ROW)
        a, b = sc.encrypt_object(obj, m), sc.encrypt_object(obj, m)
        assert a.data != b.data, "identical bytes produced identical ciphertext — nonce reused"

    def test_nonce_is_the_declared_width(self):
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
        assert sc.NONCE_BYTES == 12
        assert len(enc.data) == len(sc.MAGIC) + 1 + sc.NONCE_BYTES + len(CANARY_ROW) + 16


class TestPerShardKeys:
    def test_two_object_keys_derive_different_keys(self):
        m = _master()
        a = sc.shard_key(m, "machines/A/seq-0001/tasks/entities.jsonl")
        b = sc.shard_key(m, "machines/A/seq-0001/memory/semantic_memory.jsonl")
        assert a != b and len(a) == len(b) == 32

    def test_derivation_is_machine_agnostic(self):
        """§4.4: every machine with the passphrase derives the identical key, so it can
        read every other machine's shards."""
        machine_a = sc.derive_master("shared passphrase", SALT)
        machine_b = sc.derive_master("shared passphrase", SALT)
        assert machine_a == machine_b
        enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), machine_a)
        assert sc.decrypt_object(enc, machine_b).data == CANARY_ROW

    def test_an_empty_passphrase_is_refused_not_silently_keyed(self):
        with pytest.raises(sc.MissingPassphrase):
            sc.derive_master("", SALT)

    def test_a_wrong_width_salt_is_refused(self):
        with pytest.raises(sc.MissingSalt):
            sc.derive_master("x" * 20, b"short")


class TestFirstWriteWinsSalt:
    def test_a_fresh_root_gets_one_salt(self, tmp_path):
        t = FolderTransport(tmp_path / "remote")
        salt = sc.ensure_salt(t)
        assert len(salt) == sc.SALT_BYTES
        assert (tmp_path / "remote" / sc.SALT_KEY).read_bytes() == salt

    def test_a_racing_second_machine_loses_and_adopts_the_winner(self, tmp_path):
        """First-write-wins falls out of insert-only push; the re-read after the push is
        what makes it authoritative rather than hopeful."""
        remote = tmp_path / "remote"
        first = sc.ensure_salt(FolderTransport(remote))
        second = sc.ensure_salt(FolderTransport(remote))
        assert first == second, "the second machine minted its own salt and forked the store"

    def test_the_salt_object_stays_plaintext_and_is_routing(self, tmp_path):
        t = FolderTransport(tmp_path / "remote")
        sc.ensure_salt(t)
        assert sc.is_routing_key(sc.SALT_KEY)
        on_disk = (tmp_path / "remote" / sc.SALT_KEY).read_bytes()
        assert not sc.is_ciphertext(on_disk)

    def test_a_root_that_cannot_hold_a_salt_is_a_hard_error_never_a_fabrication(self, tmp_path):
        """§4.4: never fabricate a salt. A fabricated one derives keys no peer can
        reproduce, silently forking the store into two unreadable halves."""

        class Amnesiac(FolderTransport):
            def push(self, objects):  # accepts, stores nothing
                return PushResult(pushed=len(objects), outcome="delivered")

        with pytest.raises(sc.MissingSalt):
            sc.ensure_salt(Amnesiac(tmp_path / "remote"))


class TestRoutingFieldsStayPlaintext:
    def test_registry_and_salt_pass_through_while_shards_are_encrypted(self):
        codec = sc.SyncCodec(master=_master())
        objects = [
            SyncObject(key=REGISTRY_KEY, data=b'{"machines": {"A": {"seq": 1}}}'),
            SyncObject(key=sc.SALT_KEY, data=SALT),
            SyncObject(key=SHARD_KEY, data=CANARY_ROW),
        ]
        out, skipped = codec.encrypt_for_push(objects)
        assert not skipped
        by_key = {o.key: o.data for o in out}
        assert by_key[REGISTRY_KEY] == b'{"machines": {"A": {"seq": 1}}}'
        assert by_key[sc.SALT_KEY] == SALT
        assert sc.is_ciphertext(by_key[SHARD_KEY])
        assert CANARY_ROW not in by_key[SHARD_KEY]

    def test_the_routing_key_set_is_exactly_registry_and_salt(self):
        assert sc.ROUTING_KEYS == frozenset({REGISTRY_KEY, sc.SALT_KEY})


class TestPlaintextRejectedBothDirections:
    def test_receive_side_skips_a_plaintext_object_permanently(self):
        codec = sc.SyncCodec(master=_master())
        good = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), codec.master)
        planted = SyncObject(key="machines/A/seq-0001/notifications/2026.jsonl", data=b"raw row")
        out, skipped = codec.decrypt_after_pull([good, planted])
        assert [o.key for o in out] == [SHARD_KEY]
        assert skipped.keys == [planted.key]
        assert "plaintext object in an encrypted store" in skipped.reasons[0]

    def test_receive_side_withholds_an_undecryptable_object(self):
        """Withheld from the merge, but in the HOLD bucket — not the permanent one."""
        codec = sc.SyncCodec(master=_master())
        enc = sc.encrypt_object(
            SyncObject(key=SHARD_KEY, data=CANARY_ROW), sc.derive_master("x" * 20, SALT)
        )
        out, skipped = codec.decrypt_after_pull([enc])
        assert out == []
        assert skipped.unreadable == [SHARD_KEY]
        assert skipped.keys == [], "a failed tag must not be filed as permanent"

    def test_send_side_refuses_to_push_plaintext(self, monkeypatch):
        """The re-check enforces the invariant on the bytes that reach the transport, not
        on the intention of the code above it."""
        codec = sc.SyncCodec(master=_master())
        monkeypatch.setattr(sc, "encrypt_object", lambda obj, master: obj)
        out, skipped = codec.encrypt_for_push([SyncObject(key=SHARD_KEY, data=CANARY_ROW)])
        assert out == []
        assert skipped.keys == [SHARD_KEY]
        assert "plaintext" in skipped.reasons[0]

    def test_a_plaintext_object_makes_the_seq_payload_bad_not_held(self, tmp_path, monkeypatch):
        """A permanent skip must ADVANCE the cursor. Holding would be the error loop §4.4
        forbids: the object can never become decryptable, so re-pulling it is the bug."""
        from personalclaw.durability import pull_engine

        remote = tmp_path / "remote"
        t = FolderTransport(remote)
        t.push([SyncObject(key="machines/P/seq-0001/tasks/entities.jsonl", data=b"raw row")])
        codec = sc.SyncCodec(master=_master())
        outcome = pull_engine._pull_one_seq(t, tmp_path / "home", "P", 1, None, codec=codec)
        assert outcome.verdict == PAYLOAD_BAD, (
            f"a permanent skip must advance the cursor; got {outcome.verdict!r} "
            f"({PREREQ_ABSENT!r} would re-pull the same undecryptable object forever)"
        )
        assert "encrypted-store violation" in outcome.detail

    def test_a_wrong_passphrase_HOLDS_so_the_typo_is_recoverable(self, tmp_path):
        """MEASURED DEFECT, now pinned. The first implementation advanced the cursor on any
        decrypt failure, so one cycle run with a mistyped passphrase permanently skipped every
        peer seq — and storing the right passphrase afterwards did NOT bring them back. A
        failed tag is "wrong key OR tampering", which the user can fix; only PLAINTEXT is
        genuinely permanent."""
        from personalclaw.durability import pull_engine

        remote = tmp_path / "remote"
        t = FolderTransport(remote)
        right = _master("the right passphrase")
        t.push(
            [
                sc.encrypt_object(
                    SyncObject(key="machines/P/seq-0001/tasks/entities.jsonl", data=CANARY_ROW),
                    right,
                )
            ]
        )
        wrong = sc.SyncCodec(master=_master("a typo passphrase"))
        outcome = pull_engine._pull_one_seq(t, tmp_path / "home", "P", 1, None, codec=wrong)
        assert outcome.verdict == PREREQ_ABSENT, (
            f"a wrong passphrase must HOLD, not advance; got {outcome.verdict!r} — advancing "
            "permanently loses every peer seq pulled during the typo"
        )
        assert "did not decrypt" in outcome.detail and "held" in outcome.detail

    def test_the_two_failure_buckets_are_reported_separately(self):
        codec = sc.SyncCodec(master=_master())
        plaintext = SyncObject(key="machines/A/seq-0001/a.jsonl", data=b"raw")
        undecryptable = sc.encrypt_object(
            SyncObject(key="machines/A/seq-0001/b.jsonl", data=CANARY_ROW),
            _master("other passphrase"),
        )
        _out, skipped = codec.decrypt_after_pull([plaintext, undecryptable])
        assert skipped.keys == [plaintext.key], "plaintext must be the PERMANENT bucket"
        assert skipped.unreadable == [undecryptable.key], "a failed tag must be the HOLD bucket"
        assert len(skipped) == 2


# ── the egress derivation ────────────────────────────────────────────────────


def _fake_dns(host: str):
    """A resolver that maps every host to one public IP, so the guard's verdict is about
    the POLICY, not about whether a name happens to resolve in CI."""
    return ["93.184.216.34"]


class TestSyncEgressPinning:
    def test_the_base_profile_reaches_nothing_until_it_is_pinned(self):
        """Host-pinned-by-absence: a transport that forgets to pin cannot inherit
        STRICT's whole-public-internet reach."""
        assert SYNC.allow_only is True and SYNC.allow_hosts == ()
        d = evaluate("https://s3.amazonaws.com/bucket/key", SYNC, resolver=_fake_dns)
        assert d.allow is False

    def test_the_pinned_host_is_allowed(self):
        p = sync_egress_policy("https://s3.us-west-2.amazonaws.com")
        d = evaluate("https://s3.us-west-2.amazonaws.com/bucket/obj", p, resolver=_fake_dns)
        assert d.allow is True, d.reason

    def test_a_non_pinned_host_is_REFUSED(self):
        """The assertion that a policy which allows everything would fail."""
        p = sync_egress_policy("https://s3.us-west-2.amazonaws.com")
        for other in (
            "https://evil.example.com/bucket/obj",
            "https://s3.eu-central-1.amazonaws.com/bucket/obj",
            "https://amazonaws.com/bucket/obj",
        ):
            d = evaluate(other, p, resolver=_fake_dns)
            assert d.allow is False, f"{other} was reachable under a host-pinned SYNC policy"

    def test_max_bytes_is_raised_deliberately_not_removed(self):
        p = sync_egress_policy("https://minio.example.com")
        assert p.max_bytes == 200_000_000, "the sync cap must be a real number, not unbounded"
        assert p.max_bytes > 5_000_000, "a whole-DB shard needs more than the STRICT page cap"
        assert p.timeout_s == 120.0

    def test_ip_pinning_and_redirect_recheck_survive_the_derivation(self):
        p = sync_egress_policy("https://minio.example.com")
        assert p.pin_resolved_ip is True
        assert p.max_redirects == 5
        assert p.on_violation == "deny"

    def test_operator_allow_hosts_cannot_widen_the_pin(self, monkeypatch):
        """`egress_policy_for` UNIONs the operator's allow_hosts into any base. For an
        exclusive policy that would let hosts listed for OTHER surfaces become valid S3
        endpoints, so the pin is applied last."""
        from personalclaw.net import policy as np

        class _Eg:
            allow_hosts = ("evil.example.com", "lan.internal")
            deny_hosts = ()
            allow_private = False

        monkeypatch.setattr(
            np,
            "egress_policy_for",
            lambda base: base.with_overrides(
                allow_hosts=tuple([*base.allow_hosts, *_Eg.allow_hosts]),
                allow_private=_Eg.allow_private,
            ),
        )
        p = np.sync_egress_policy("https://minio.example.com")
        assert p.allow_hosts == ("minio.example.com",)
        assert evaluate("https://evil.example.com/x", p, resolver=_fake_dns).allow is False

    def test_operator_deny_hosts_still_wins_over_the_configured_endpoint(self, monkeypatch):
        """An operator who banned a host has banned it as a sync target too. Refused at
        derivation rather than returned as a policy whose one permitted host the guard
        rejects on every request — the same fact, said at the point a human can act on it."""
        from personalclaw.net import policy as np

        monkeypatch.setattr(
            np,
            "egress_policy_for",
            lambda base: base.with_overrides(deny_hosts=("minio.example.com",)),
        )
        with pytest.raises(SyncEndpointRefused, match="deny list"):
            np.sync_egress_policy("https://minio.example.com")

    def test_an_operator_deny_of_a_DIFFERENT_host_is_carried_into_the_policy(self, monkeypatch):
        from personalclaw.net import policy as np

        monkeypatch.setattr(
            np,
            "egress_policy_for",
            lambda base: base.with_overrides(deny_hosts=(*base.deny_hosts, "banned.example.com")),
        )
        p = np.sync_egress_policy("https://minio.example.com")
        assert "banned.example.com" in p.deny_hosts
        assert "169.254.169.254" in p.deny_hosts, "the built-in metadata deny was dropped"

    @pytest.mark.parametrize("bad", ["", "   ", "https://", "file:///etc/passwd", "ftp://h/x"])
    def test_an_unpinnable_endpoint_is_refused_not_widened(self, bad):
        with pytest.raises(SyncEndpointRefused):
            sync_egress_policy(bad)

    @pytest.mark.parametrize(
        "metadata",
        [
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://100.100.100.200/latest/meta-data/",
        ],
    )
    def test_the_cloud_metadata_service_can_never_be_the_sync_endpoint(self, metadata):
        """The one private address where "reach whatever the operator configured" becomes
        credential theft. Refused at derivation, so it never reaches the guard."""
        with pytest.raises(SyncEndpointRefused, match="deny list"):
            sync_egress_policy(metadata)

    def test_metadata_stays_denied_even_from_a_legitimate_endpoint_policy(self):
        """A deny is evaluated before the allow-list, so the pin cannot smuggle it back."""
        p = sync_egress_policy("https://minio.example.com")
        assert "169.254.169.254" in p.deny_hosts
        d = evaluate("http://169.254.169.254/latest/meta-data/", p, resolver=_fake_dns)
        assert d.allow is False and "deny list" in d.reason

    def test_a_self_hosted_private_endpoint_IS_reachable_once_pinned(self):
        """MEASURED, and the reason SYNC's comment was rewritten: the pinned host is its own
        allow-list entry, and the guard waives the private-range block for a listed host. So a
        LAN/loopback MinIO works with no extra opt-in. Pinned here so the posture is a decision
        on record rather than a surprise."""
        p = sync_egress_policy("http://127.0.0.1:9000")
        assert p.allow_private is False, "the base stance is unchanged..."
        d = evaluate("http://127.0.0.1:9000/bucket/obj", p, resolver=lambda h: ["127.0.0.1"])
        assert d.allow is True, f"...yet the pinned private host is reachable: {d.reason}"
        # And still nothing else on the private range.
        other = evaluate("http://192.168.1.50:9000/b/o", p, resolver=lambda h: ["192.168.1.50"])
        assert other.allow is False

    def test_a_bare_host_is_accepted_and_pinned(self):
        assert sync_egress_policy("minio.example.com:9000").allow_hosts == ("minio.example.com",)

    def test_the_profile_is_registered_by_name(self):
        from personalclaw.net.policy import get_policy

        assert get_policy("sync") is SYNC


# ── credentials: where they come from, and that they do not leak ─────────────


class TestPassphraseCustody:
    def test_the_passphrase_comes_from_the_credential_store(self, monkeypatch):
        seen = {}

        def _fake_get(key):
            seen["key"] = key
            return CANARY_PASSPHRASE

        monkeypatch.setattr("personalclaw.config.credentials.get_credential", _fake_get)
        assert sc.load_passphrase() == CANARY_PASSPHRASE
        assert seen["key"] == sc.PASSPHRASE_CREDENTIAL

    def test_no_config_field_can_carry_a_passphrase(self):
        """Reading it from config.json would put it in GET /api/config, in every config
        export, and in the time-travel git history."""
        from dataclasses import fields

        from personalclaw.config.loader import DurabilityConfig

        names = {f.name for f in fields(DurabilityConfig)}
        for suspicious in ("sync_passphrase", "passphrase", "sync_key", "encryption_key"):
            assert suspicious not in names
        blob = json.dumps(
            {f.name: getattr(DurabilityConfig(), f.name) for f in fields(DurabilityConfig)}
        )
        assert "passphrase" not in blob.lower()

    def test_the_passphrase_is_absent_from_every_failure_path_artifact(self, monkeypatch, caplog):
        """Plant the canary, drive the failure, then sweep the exception text, the log
        records and the codec's repr for it."""
        monkeypatch.setattr(sc, "load_passphrase", lambda: CANARY_PASSPHRASE)
        artifacts: list[str] = []

        with caplog.at_level(logging.DEBUG):
            master = sc.derive_master(CANARY_PASSPHRASE, SALT)
            codec = sc.SyncCodec(master=master)
            artifacts.append(repr(codec))
            artifacts.append(str(codec))
            # a wrong-key decrypt, a tampered object, and a plaintext skip
            enc = sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), master)
            try:
                sc.decrypt_object(enc, sc.derive_master("other passphrase", SALT))
            except sc.SyncEncryptionError as exc:
                artifacts.append(str(exc))
                artifacts.append(repr(exc))
            _out, skipped = codec.decrypt_after_pull(
                [SyncObject(key="machines/A/seq-0001/x/y.jsonl", data=b"raw")]
            )
            artifacts.extend(skipped.reasons)
            artifacts.append(enc.data.decode("latin-1"))
        artifacts.extend(r.getMessage() for r in caplog.records)

        blob = "\n".join(artifacts)
        assert CANARY_PASSPHRASE not in blob, "the passphrase leaked into a failure artifact"
        assert master.hex() not in blob, "the derived key leaked into a failure artifact"
        assert CANARY_ROW.decode() not in blob, "a plaintext row leaked into a failure artifact"

    def test_encryption_on_without_a_passphrase_fails_closed(self, monkeypatch, tmp_path):
        """Falling back to plaintext here is the exact failure the feature prevents."""
        monkeypatch.setattr(sc, "load_passphrase", lambda: "")
        with pytest.raises(sc.MissingPassphrase):
            sc.codec_for(FolderTransport(tmp_path / "remote"), setting="on")

    def test_the_derived_key_is_never_rendered(self):
        codec = sc.SyncCodec(master=_master())
        assert codec.master.hex() not in repr(codec)
        assert "withheld" in repr(codec)


# ── per-transport defaults ───────────────────────────────────────────────────


class TestPerTransportDefaults:
    @pytest.mark.parametrize(
        "transport,expected",
        [("s3-sync", True), ("dir-sync", True), ("rsync-sync", True), ("git-sync", False)],
    )
    def test_auto_takes_the_per_transport_default(self, transport, expected):
        assert sc.encryption_enabled_for(transport, "auto") is expected

    def test_an_unknown_transport_defaults_off_but_says_so_out_loud(self, caplog):
        """The uncomfortable default (see the constant's note): ON would turn installing any
        third-party transport into a hard sync stop, breaking criterion 10. What makes OFF
        honest is that it is announced, not assumed."""
        sc._LOGGED_AUTO.discard("someones-webdav-app")
        with caplog.at_level(logging.WARNING):
            assert sc.encryption_enabled_for("someones-webdav-app", "auto") is False
        assert any(
            "someones-webdav-app" in r.getMessage() and "OFF" in r.getMessage()
            for r in caplog.records
        ), "an unencrypted third-party transport resolved silently"

    def test_a_shipped_transport_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING):
            sc.encryption_enabled_for("s3-sync", "auto")
        assert not [r for r in caplog.records if "encryption default" in r.getMessage()]

    def test_an_explicit_on_still_encrypts_an_unknown_transport(self):
        assert sc.encryption_enabled_for("someones-webdav-app", "on") is True

    @pytest.mark.parametrize("transport", ["s3-sync", "git-sync", "unknown"])
    def test_explicit_on_and_off_override_the_default(self, transport):
        assert sc.encryption_enabled_for(transport, "on") is True
        assert sc.encryption_enabled_for(transport, "off") is False

    @pytest.mark.parametrize("bad", ["", "  ", "yes", "true", "ON!", None])
    def test_an_off_scale_setting_resolves_to_auto_not_off(self, bad):
        """A typo in a security control must not quietly disable it."""
        assert sc.encryption_enabled_for("s3-sync", bad) is True

    def test_case_and_space_are_tolerated(self):
        assert sc.encryption_enabled_for("git-sync", " ON ") is True
        assert sc.encryption_enabled_for("s3-sync", "Off") is False

    def test_git_sync_is_off_because_diffability_is_the_feature(self):
        assert sc.DEFAULT_ENCRYPT_BY_TRANSPORT["git-sync"] is False
        assert sc.codec_for(_GitTransport(), setting="auto") is None


class _GitTransport(FolderTransport):
    name = "git-sync"

    def __init__(self):  # never touches a remote — auto resolves to OFF before any I/O
        pass


# ── config round-trip ────────────────────────────────────────────────────────


class TestConfigRoundTrip:
    def test_default_is_auto(self):
        from personalclaw.config.loader import DurabilityConfig

        assert DurabilityConfig().sync_encrypt == "auto"

    def test_the_field_has_meta(self):
        from dataclasses import fields

        from personalclaw.config.loader import DurabilityConfig

        meta = {f.name: f.metadata for f in fields(DurabilityConfig)}["sync_encrypt"]
        assert meta.get("label") and meta.get("help")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("on", "on"),
            ("off", "off"),
            ("auto", "auto"),
            ("ON", "on"),
            (" Off ", "off"),
            ("nonsense", "auto"),
            ("", "auto"),
            (None, "auto"),
            (5, "auto"),
        ],
    )
    def test_load_coerces_to_the_closed_set_defaulting_safe(
        self, raw, expected, tmp_path, monkeypatch
    ):
        from personalclaw.config import loader as cl

        monkeypatch.setattr(cl, "config_dir", lambda: tmp_path)
        (tmp_path / "config.json").write_text(json.dumps({"durability": {"sync_encrypt": raw}}))
        cfg = cl.AppConfig.load()
        assert cfg.durability.sync_encrypt == expected

    def test_to_dict_carries_the_field(self, tmp_path, monkeypatch):
        from personalclaw.config import loader as cl

        monkeypatch.setattr(cl, "config_dir", lambda: tmp_path)
        (tmp_path / "config.json").write_text(json.dumps({"durability": {"sync_encrypt": "on"}}))
        assert cl.AppConfig.load().to_dict()["durability"]["sync_encrypt"] == "on"

    def test_it_is_in_the_patch_allowlist_with_a_closed_value_set(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        spec = _EDITABLE_CONFIG["durability.sync_encrypt"]
        assert spec["type"] == "str"
        assert set(spec["values"]) == {"auto", "on", "off"}

    def test_status_reports_the_resolved_verdict_not_the_tri_state(self, monkeypatch):
        from personalclaw.config.loader import DurabilityConfig
        from personalclaw.durability import service as svc

        monkeypatch.setattr(
            svc, "_cfg", lambda: DurabilityConfig(sync_transport="s3-sync", sync_encrypt="auto")
        )
        assert svc._resolved_encryption(svc._cfg()) is True
        monkeypatch.setattr(
            svc, "_cfg", lambda: DurabilityConfig(sync_transport="git-sync", sync_encrypt="auto")
        )
        assert svc._resolved_encryption(svc._cfg()) is False
        # No transport chosen → nothing is being sent, so nothing is "encrypted".
        assert svc._resolved_encryption(DurabilityConfig(sync_encrypt="on")) is False


# ── criterion 8, over a REAL on-disk transport ───────────────────────────────


def _seed_task(home: Path, tid: str, title: str) -> None:
    d = home / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"id": tid, "title": title}))


class TestCriterion8EndToEnd:
    """ "An encrypted S3 sync store is useless without the passphrase, yet list_remote /
    registry operations work without the key; a plaintext object appearing in an encrypted
    store is skipped permanently and logged, never looped on."

    Driven over the real `FolderTransport` (bytes on disk), NOT a mocked codec. There is no
    real S3 bucket or ssh remote in this environment, so what is proven here is the codec +
    boundary behaviour against a real filesystem remote; the HTTP wire format of a signed
    S3 PUT is the s3-sync APP's concern and is not exercised.
    """

    def _cycle(
        self, home: Path, remote: Path, self_id: str, now: str, passphrase: str, monkeypatch
    ):
        from personalclaw.durability import crypto as crypto_mod
        from personalclaw.durability.shards import machine_id
        from personalclaw.durability.sync_cycle import run_sync_cycle

        monkeypatch.setattr(crypto_mod, "load_passphrase", lambda: passphrase)
        machine_id(home)  # materialise the id file before we pin self_id
        return run_sync_cycle(FolderTransport(remote), home, self_id=self_id, now=now, encrypt="on")

    def test_the_remote_holds_only_ciphertext_and_plaintext_routing(self, tmp_path, monkeypatch):
        remote, home = tmp_path / "remote", tmp_path / "A"
        _seed_task(home, "task-a", CANARY_ROW.decode())
        report = self._cycle(home, remote, "A", "t1", "shared passphrase", monkeypatch)
        assert report.ok, report.error

        shard_objects, routing = [], []
        for p in sorted(remote.rglob("*")):
            if not p.is_file():
                continue
            key = p.relative_to(remote).as_posix()
            (routing if sc.is_routing_key(key) else shard_objects).append((key, p.read_bytes()))

        assert shard_objects, "nothing was published — the proof would be vacuous"
        for key, data in shard_objects:
            assert sc.is_ciphertext(data), f"{key} landed on the remote as plaintext"
        # Criterion 7 + 8: no plaintext row anywhere in the store's bytes.
        every_byte = b"".join(d for _k, d in shard_objects + routing)
        assert CANARY_ROW not in every_byte
        assert b"task-a" not in every_byte, "a task id leaked in plaintext"

    def test_registry_and_listing_work_without_the_key(self, tmp_path, monkeypatch):
        remote, home = tmp_path / "remote", tmp_path / "A"
        _seed_task(home, "task-a", "hello")
        self._cycle(home, remote, "A", "t1", "shared passphrase", monkeypatch)

        # A keyless machine: no passphrase at all.
        from personalclaw.durability.sync_cycle import read_registry

        keyless = FolderTransport(remote)
        assert keyless.list_remote(""), "list_remote needed the key"
        registry = read_registry(keyless)
        assert registry.seq_of("A") >= 1, "the registry was unreadable without the key"
        assert sc.read_salt(keyless) is not None, "the salt was unreadable without the key"

    def test_the_store_is_useless_to_the_wrong_passphrase(self, tmp_path, monkeypatch):
        remote, home_a, home_b = tmp_path / "remote", tmp_path / "A", tmp_path / "B"
        _seed_task(home_a, "task-a", "from A")
        self._cycle(home_a, remote, "A", "t1", "shared passphrase", monkeypatch)
        # B has the WRONG passphrase: every pulled object fails its tag, so nothing merges.
        report = self._cycle(home_b, remote, "B", "t2", "a different passphrase", monkeypatch)
        assert report.ok, report.error
        assert not (
            home_b / "tasks" / "task-a.json"
        ).exists(), "the wrong passphrase still imported A's rows"

    def test_two_machines_with_the_passphrase_converge(self, tmp_path, monkeypatch):
        remote, home_a, home_b = tmp_path / "remote", tmp_path / "A", tmp_path / "B"
        _seed_task(home_a, "task-a", "from A")
        _seed_task(home_b, "task-b", "from B")
        for who, home, now in (
            ("A", home_a, "t1"),
            ("B", home_b, "t2"),
            ("A", home_a, "t3"),
            ("B", home_b, "t4"),
        ):
            r = self._cycle(home, remote, who, now, "shared passphrase", monkeypatch)
            assert r.ok, r.error
        assert (home_b / "tasks" / "task-a.json").exists(), "A's task did not converge onto B"
        assert (home_a / "tasks" / "task-b.json").exists(), "B's task did not converge onto A"

    def test_a_planted_plaintext_object_is_skipped_permanently_not_looped(
        self, tmp_path, monkeypatch, caplog
    ):
        remote, home_a, home_b = tmp_path / "remote", tmp_path / "A", tmp_path / "B"
        _seed_task(home_a, "task-a", "from A")
        self._cycle(home_a, remote, "A", "t1", "shared passphrase", monkeypatch)

        # An attacker (or a misconfigured peer) drops a PLAINTEXT object into A's seq.
        target = next(remote.glob("machines/A/seq-0001/**/*.jsonl"))
        target.write_bytes(b'{"id": "smuggled", "title": "plaintext"}\n')

        from personalclaw.durability.cursor import Cursor

        with caplog.at_level(logging.WARNING):
            r1 = self._cycle(home_b, remote, "B", "t2", "shared passphrase", monkeypatch)
        assert r1.ok, r1.error
        # The cursor ADVANCED past it — the permanent-skip contract.
        assert Cursor(home_b / "sync").seen().get("A") == 1, (
            "the cursor did not advance past a permanently-skipped seq — this is the loop "
            "§4.4 forbids"
        )
        assert any(
            "encrypt" in m.lower() or "skip" in m.lower()
            for m in (r.getMessage() for r in caplog.records)
        ), "the skip was not logged"
        # A second cycle does not re-pull it (no loop).
        r2 = self._cycle(home_b, remote, "B", "t3", "shared passphrase", monkeypatch)
        assert r2.ok
        assert Cursor(home_b / "sync").seen().get("A") == 1
        assert not (home_b / "tasks" / "smuggled.json").exists()

    def test_fail_closed_when_encryption_cannot_be_honored(self, tmp_path, monkeypatch):
        """No passphrase + encryption on ⇒ the cycle is SKIPPED, not downgraded to plaintext."""
        remote, home = tmp_path / "remote", tmp_path / "A"
        _seed_task(home, "task-a", CANARY_ROW.decode())
        report = self._cycle(home, remote, "A", "t1", "", monkeypatch)
        assert report.ok is False
        assert "encryption" in report.error
        pushed = [p for p in remote.rglob("*") if p.is_file()] if remote.exists() else []
        assert pushed == [], f"bytes reached the remote after a fail-closed refusal: {pushed}"


# ── criterion 7: secrets never reach a transport, encrypted or not ───────────


class TestCriterion7SecretsNeverTransported:
    @pytest.mark.parametrize("encrypt", ["on", "off"])
    def test_no_secret_file_content_is_ever_pushed(self, tmp_path, monkeypatch, encrypt):
        """`secret=True` entries are excluded BEFORE any transport sees bytes, independent
        of encryption (§4.4's last line). Asserted on the pushed bytes, not on a list."""
        from personalclaw.durability import crypto as crypto_mod
        from personalclaw.durability.shards import machine_id
        from personalclaw.durability.sync_cycle import run_sync_cycle

        remote, home = tmp_path / "remote", tmp_path / "A"
        _seed_task(home, "task-a", "ordinary row")
        # Plant a real-shaped secret in every secret path the inventory declares.
        from personalclaw.durability import inventory as inv

        planted = []
        for rel in inv.secret_paths():
            p = home / rel
            if p.suffix or "." in p.name:
                p.parent.mkdir(parents=True, exist_ok=True)
                token = f"sk-ant-CANARY-{rel.replace('/', '-')}"
                p.write_text(f"SECRET={token}\n")
                planted.append(token)
        assert planted, "no secret paths were planted — the proof would be vacuous"

        monkeypatch.setattr(crypto_mod, "load_passphrase", lambda: "shared passphrase")
        machine_id(home)
        report = run_sync_cycle(
            FolderTransport(remote), home, self_id="A", now="t1", encrypt=encrypt
        )
        assert report.ok, report.error
        blob = b"".join(p.read_bytes() for p in remote.rglob("*") if p.is_file())
        assert blob, "nothing was pushed — the proof would be vacuous"
        for token in planted:
            assert token.encode() not in blob, f"{token} reached the transport"
        for marker in (b".local_secret", b"sel_hmac.key", b"telemetry_salt"):
            assert marker not in blob, f"{marker!r} named in a transported object"


# ── the SDK boundary a transport app builds against ─────────────────────────


class TestSdkSurface:
    """Drives each export the way a transport app would — through the facade, by name.

    Written as real USE rather than `hasattr` on purpose. The two transport apps that will
    consume these live in the sibling apps repo, so nothing in THIS repo would otherwise
    exercise them, and `scripts/generate_inert_surface_baseline.py` would (correctly) report
    seven declared-but-inert `sdk_export` surfaces. The right answer to that gate is a real
    consumer, not a widened baseline.
    """

    def test_an_object_store_transport_derives_its_policy_through_the_facade(self):
        from personalclaw.sdk.sync import SYNC, SyncEndpointRefused, sync_egress_policy

        assert SYNC.allow_only is True and SYNC.allow_hosts == ()
        policy = sync_egress_policy("https://minio.example.com:9000")
        assert policy.allow_hosts == ("minio.example.com",)
        assert evaluate("https://minio.example.com/b/o", policy, resolver=_fake_dns).allow
        assert not evaluate("https://elsewhere.example.com/b/o", policy, resolver=_fake_dns).allow
        with pytest.raises(SyncEndpointRefused):
            sync_egress_policy("")

    def test_a_transport_asks_the_facade_which_keys_stay_plaintext(self):
        from personalclaw.sdk.sync import ROUTING_KEYS, SALT_KEY, is_routing_key

        assert is_routing_key(SALT_KEY) and is_routing_key(REGISTRY_KEY)
        assert not is_routing_key(SHARD_KEY)
        assert SALT_KEY in ROUTING_KEYS

    def test_a_transport_can_name_the_credential_without_holding_it(self):
        from personalclaw.sdk.sync import PASSPHRASE_CREDENTIAL

        assert PASSPHRASE_CREDENTIAL == "PERSONALCLAW_SYNC_PASSPHRASE"
        assert "passphrase" not in PASSPHRASE_CREDENTIAL.lower().replace("_passphrase", "")

    def test_the_contract_types_are_importable_by_name(self):
        from personalclaw.sdk.sync import (
            ConnectionResult,
            PushResult,
            RemoteRef,
            SyncObject,
            SyncTransportProvider,
        )

        assert issubclass(FolderTransport, SyncTransportProvider)
        assert SyncObject(key="k", data=b"d").data == b"d"
        assert RemoteRef(key="k").size == 0
        assert PushResult().outcome == "delivered"
        assert ConnectionResult().ok is False

    def test_the_encrypt_decrypt_primitives_are_NOT_exported(self):
        """Encryption is applied above every transport. A transport that could encrypt for
        itself is a transport that could forget to."""
        from personalclaw.sdk import sync as sdk_sync

        for name in (
            "encrypt_object",
            "decrypt_object",
            "derive_master",
            "shard_key",
            "SyncCodec",
            "load_passphrase",
        ):
            assert name not in sdk_sync.__all__
            assert not hasattr(sdk_sync, name), f"sdk.sync leaks {name}"

    def test_sdk_net_exports_the_sync_policy(self):
        from personalclaw.sdk import net as sdk_net

        assert "sync_egress_policy" in sdk_net.__all__ and "SYNC" in sdk_net.__all__


def test_the_codec_needs_no_scratch_directory():
    """A temp-dir dependency would make the codec unusable inside the export's own
    TemporaryDirectory. Asserted by watching THIS process's open temp handles rather than
    the shared /tmp — a global-/tmp diff races every other xdist worker."""
    before = tempfile.tempdir
    sc.encrypt_object(SyncObject(key=SHARD_KEY, data=CANARY_ROW), _master())
    assert tempfile.tempdir == before
    assert "tempfile" not in sc.__dict__, "the codec reached for a scratch dir"
