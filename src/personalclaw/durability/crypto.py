"""End-to-end encryption for sync shard objects (DURABILITY-AND-SYNC §4.4, DAS-8).

The codec that makes *untrusted* storage a valid sync transport. It sits at exactly one
seam — the transport boundary — so every byte a transport ever sees is ciphertext, and
nothing above the boundary (merge, registry, cursor, outbox) knows encryption exists:

    push:  export_shards → _objects_for → **encrypt_objects** → transport.push
    pull:  transport.pull → **decrypt_objects** → _materialize → import_shards

**What stays plaintext, and why.** §4.4's rule is that *routing* metadata must be readable
without the key, so sync logic works on a machine that has not been given the passphrase and
so an operator can inspect a remote store. Three things are therefore never encrypted:

* the object **key** itself (``machines/<machine_id>/seq-NNNN/<rel>``) — the machine id, the
  seq and the shard's name are the routing plane; they are what ``list_remote`` sorts on,
* ``registry.json`` — the machine/seq/ancestor ledger the CAS loop compares shas on,
* the **salt object** — a public parameter by construction; a secret salt buys nothing.

Everything else — every shard byte, i.e. every row of the user's tasks, memory, knowledge —
is AES-256-GCM ciphertext before it leaves the process.

**Key schedule.** Two stages, because the two jobs are different:

1. **Stretch** the user passphrase into a 32-byte master key with **Argon2id** over the
   shared salt. §4.4 says "HKDF from a user passphrase"; HKDF alone is a *fast* KDF, so a
   passphrase run through it directly is an offline-brute-force gift to whoever holds the
   bucket — the exact adversary this feature exists for. The stretch is a strengthening of
   the plan's shape, not a departure from it: same inputs (passphrase + the first-write-wins
   salt), same machine-agnostic property, one ~50 ms cost per process (the master key is
   cached in the :class:`SyncCodec` and never persisted).
2. **Expand** per shard with **HKDF-Expand**, ``info`` = the object key. Per-object keys mean
   two shards never share a key, so the nonce-reuse blast radius is one object rather than
   the whole store, and a ciphertext lifted from one key and dropped at another decrypts
   under neither.

**Nonce.** 12 random bytes per encryption from ``os.urandom``, carried in the header. Never a
counter: a counter that restarts (a new process, a re-export, a restored home) reuses a nonce
under the same key, which is the one mistake AES-GCM does not survive.

**AAD.** The header (magic + version + nonce) *and* the object key are authenticated. Binding
the key means an attacker with write access to the bucket cannot replay machine A's
``tasks`` shard as machine B's, or an old seq's as a new one: the tag fails.

**Plaintext in an encrypted store is a permanent skip, both directions** (§4.4). On receive, a
non-ciphertext object is dropped with a log and never retried — the seq it belonged to fails
validation and lands as ``payload-bad``, which the cursor advances past (§4.1's rule), so a
contract violation can never become an error loop. On send, the encrypt pass re-checks its own
output and refuses to hand a transport any non-routing plaintext object.

**Key custody, and what a lost passphrase costs.** The passphrase lives in the credential
store under one name (:data:`PASSPHRASE_CREDENTIAL`) — never ``config.json``, never an
``app.json``, never a provider settings field, so it is never in an API response, a SEL
record or an export. The *derived* keys live in a :class:`SyncCodec` for the life of a cycle
and are never written anywhere. Losing the passphrase does **not** lose the user's data: the
local home is authoritative and untouched: what is lost is the ability to read the *remote*
copies, and the recovery is a fresh sync root. There is deliberately **no key rotation
mechanism** — rotating would mean re-encrypting every historical object under a new salt, and
that is a plan-level design decision, not something to improvise here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from personalclaw.durability.registry import REGISTRY_KEY
from personalclaw.sync_transports.base import SyncObject, SyncTransportProvider

logger = logging.getLogger(__name__)

# ── format constants ──────────────────────────────────────────────────────────

#: Header magic. Present so a *receiving* machine can tell "encrypted" from "plaintext"
#: without the key — the §4.4 both-direction rejection needs that distinction to exist
#: before any key material is involved.
MAGIC = b"PCLAWSYN"
#: Format version, inside the authenticated header so a downgrade is tampering.
VERSION = b"\x01"
NONCE_BYTES = 12
SALT_BYTES = 16
KEY_BYTES = 32  # AES-256
_HEADER_BYTES = len(MAGIC) + len(VERSION) + NONCE_BYTES

#: The first-write-wins salt object, at the sync root. Plaintext, and readable by a machine
#: without the passphrase — a salt is a public parameter.
SALT_KEY = "encryption-salt"

#: Object keys that stay plaintext because sync logic must read them keyless (§4.4).
ROUTING_KEYS: frozenset[str] = frozenset({REGISTRY_KEY, SALT_KEY})

#: The ONE credential name the sync passphrase is stored under. A name, never a value, is
#: what any log/API/journal record may carry.
PASSPHRASE_CREDENTIAL = "PERSONALCLAW_SYNC_PASSPHRASE"

# Argon2id cost — the same RFC 9106-informed profile `auth/credentials.py` uses for login,
# for the same reason: paid once (per process, cached), invisible to a human, expensive to a
# brute-forcer.
_TIME_COST = 3
_MEMORY_COST = 65536  # 64 MiB
_PARALLELISM = 4

#: Per-transport encryption defaults (§4.4). Third-party storage defaults ON; `git-sync`
#: defaults OFF because encryption destroys the human-diffable `git log -p` history that is
#: the entire reason to choose that transport, and a private repo is user-owned storage.
#: `rsync-sync` is not named by §4.4; the two stated criteria (ON for storage the user does
#: not control, OFF where encryption destroys a feature) do not decide it — an ssh target IS
#: user-controlled, but an rsync'd shard tree was never diffable — so the tie goes to the
#: secure default: ON.
DEFAULT_ENCRYPT_BY_TRANSPORT: dict[str, bool] = {
    "s3-sync": True,
    "dir-sync": True,
    "rsync-sync": True,
    "git-sync": False,
}

#: The default for a transport this table does not name — a third-party `type: "sync"` app.
#:
#: **OFF, deliberately, and it is the uncomfortable one.** ON is the safer posture in the
#: abstract, but it is not what §4.4 says (it enumerates a CLOSED set of defaults) and it is
#: measurably worse in practice: with ON, installing any third-party transport turns sync into
#: a hard stop until the user stores a passphrase, which breaks criterion 10's "a third-party
#: transport app registers, configures, and *syncs* with zero core changes". Choosing ON here
#: would let this atom silently disable a shipped capability.
#:
#: OFF is also non-regressive — before this module nothing was encrypted at all — and the safer
#: posture stays one value away (`durability.sync_encrypt: "on"`). What makes OFF honest rather
#: than quiet is that it is never invisible: :func:`encryption_enabled_for` logs the resolution
#: once per transport, and `durability/service.status()` reports the RESOLVED verdict, so
#: "is my bucket readable?" always has an answer that is not the word "auto".
#:
#: This is the one genuinely under-specified security default in DAS-8 and is flagged for the
#: owner in the plan's execution log rather than quietly settled here.
DEFAULT_ENCRYPT_UNKNOWN_TRANSPORT = False

#: Transports whose `auto` resolution has already been logged, so a per-cycle resolution does
#: not reprint the same line every 15 minutes.
_LOGGED_AUTO: set[str] = set()


# ── typed refusals ────────────────────────────────────────────────────────────


class SyncEncryptionError(Exception):
    """Base for every refusal this module raises. Never carries a secret value."""


class MissingPassphrase(SyncEncryptionError):
    """Encryption is on but no passphrase is stored — a hard setup error, not a fallback.

    Falling back to plaintext here is the failure mode the whole feature exists to prevent:
    the user asked for an encrypted store and would get an unencrypted one.
    """


class MissingSalt(SyncEncryptionError):
    """Encryption is on and the sync root has no salt object that could be read or written.

    §4.4: *never fabricate a salt*. A fabricated salt derives keys no other machine can
    reproduce, which silently forks the store into two mutually unreadable halves.
    """


# ── the primitives ────────────────────────────────────────────────────────────


def is_ciphertext(data: bytes) -> bool:
    """Whether ``data`` carries this codec's header. Cheap, keyless, total."""
    return len(data) > _HEADER_BYTES and data[: len(MAGIC)] == MAGIC


def is_routing_key(key: str) -> bool:
    """Whether ``key`` is routing metadata that must stay plaintext (§4.4)."""
    return key in ROUTING_KEYS


def derive_master(passphrase: str, salt: bytes) -> bytes:
    """Stretch ``passphrase`` into a 32-byte master key with Argon2id over ``salt``.

    Machine-agnostic by construction: the only inputs are the passphrase the user types on
    every machine and the salt object every machine pulls from the shared root, so every
    machine derives the identical master key and can read every other's shards (§4.4).
    """
    if not passphrase:
        raise MissingPassphrase(
            "sync encryption is enabled but no passphrase is stored; save one under "
            f"the credential name {PASSPHRASE_CREDENTIAL}"
        )
    if len(salt) != SALT_BYTES:
        raise MissingSalt(f"salt must be exactly {SALT_BYTES} bytes, got {len(salt)}")
    from argon2.low_level import Type, hash_secret_raw

    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )


def shard_key(master: bytes, object_key: str) -> bytes:
    """HKDF-Expand the master key into this object's own 32-byte AES key.

    ``info`` is the object key, so ``machines/A/seq-0001/tasks/entities.jsonl`` and
    ``machines/B/seq-0009/memory/semantic_memory.jsonl`` never share a key.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

    return HKDFExpand(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        info=b"personalclaw-sync-shard\x00" + object_key.encode("utf-8"),
    ).derive(master)


def encrypt_object(obj: SyncObject, master: bytes) -> SyncObject:
    """AES-256-GCM one shard object. The KEY is unchanged (routing stays plaintext)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(NONCE_BYTES)
    header = MAGIC + VERSION + nonce
    # The object key is authenticated alongside the header, so a ciphertext relocated to a
    # different remote key (another machine, another seq) fails its tag instead of decrypting.
    aad = header + obj.key.encode("utf-8")
    ct = AESGCM(shard_key(master, obj.key)).encrypt(nonce, obj.data, aad)
    return SyncObject(key=obj.key, data=header + ct)


def decrypt_object(obj: SyncObject, master: bytes) -> SyncObject:
    """Reverse :func:`encrypt_object`. Raises :class:`SyncEncryptionError` on a wrong key,
    a relocated object, or a modified byte.

    Those three are ONE refusal deliberately: AES-GCM cannot distinguish them, and inventing
    a distinction would tell whoever holds the bucket which of the three they achieved.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_ciphertext(obj.data):
        raise SyncEncryptionError(f"{obj.key}: not encrypted with this codec")
    header = obj.data[:_HEADER_BYTES]
    if header[len(MAGIC) : len(MAGIC) + len(VERSION)] != VERSION:
        raise SyncEncryptionError(f"{obj.key}: unsupported sync encryption version")
    nonce = header[len(MAGIC) + len(VERSION) :]
    aad = header + obj.key.encode("utf-8")
    try:
        plain = AESGCM(shard_key(master, obj.key)).decrypt(nonce, obj.data[_HEADER_BYTES:], aad)
    except InvalidTag as exc:
        raise SyncEncryptionError(
            f"{obj.key}: wrong passphrase, relocated object, or modified bytes"
        ) from exc
    return SyncObject(key=obj.key, data=plain)


# ── the first-write-wins salt object ──────────────────────────────────────────


def read_salt(transport: SyncTransportProvider) -> bytes | None:
    """The shared root's salt, or ``None`` when the root has none yet."""
    refs = [r for r in transport.list_remote(SALT_KEY) if r.key == SALT_KEY]
    if not refs:
        return None
    for obj in transport.pull(refs):
        if obj.key == SALT_KEY and len(obj.data) == SALT_BYTES:
            return obj.data
    return None


def ensure_salt(transport: SyncTransportProvider) -> bytes:
    """The shared salt, creating it once if the root is brand new — first-write-wins (§4.4).

    First-write-wins falls straight out of the transport contract: ``push`` is insert-only and
    idempotent on key, so a racing second machine's salt write is *skipped*, not applied. The
    re-read after the push is what makes that authoritative rather than hopeful — whatever is
    on the remote afterwards is the salt, including when the winner was someone else.

    A root that still has no salt after a successful-looking push is a :class:`MissingSalt`
    hard error. Fabricating a local salt instead would fork the store irrecoverably.
    """
    existing = read_salt(transport)
    if existing is not None:
        return existing
    transport.push([SyncObject(key=SALT_KEY, data=os.urandom(SALT_BYTES))])
    winner = read_salt(transport)
    if winner is None:
        raise MissingSalt(
            "the sync root has no encryption salt and one could not be written; refusing "
            "to fabricate a local salt (it would derive keys no other machine can reproduce)"
        )
    return winner


# ── the codec used at the transport boundary ──────────────────────────────────


@dataclass
class SkipReport:
    """Objects the codec would not pass through, by key, with one reason each.

    Split into two buckets because they deserve OPPOSITE cursor verdicts, and conflating
    them silently loses data:

    * ``keys`` — **permanent**: a plaintext object in an encrypted store, or an unsupported
      format version. These can never become readable, so §4.4 says skip permanently and the
      cursor must ADVANCE past them or the seq is re-pulled forever.
    * ``unreadable`` — **not our decision**: a well-formed ciphertext whose tag failed. That
      is a wrong passphrase OR tampering, and AES-GCM cannot tell us which. Advancing here
      was a measured data-loss bug: one cycle run with a mistyped passphrase permanently
      skipped every peer seq, and later fixing the passphrase did NOT re-pull them. So these
      HOLD instead — the seq stays unconsumed, the cycle reports it, and the moment the right
      passphrase is stored it merges. A hold is visible and recoverable; the advance was
      neither.
    """

    keys: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def add(self, key: str, reason: str) -> None:
        self.keys.append(key)
        self.reasons.append(f"{key}: {reason}")

    def add_unreadable(self, key: str, reason: str) -> None:
        self.unreadable.append(key)
        self.reasons.append(f"{key}: {reason}")

    def __bool__(self) -> bool:
        return bool(self.keys or self.unreadable)

    def __len__(self) -> int:
        return len(self.keys) + len(self.unreadable)


@dataclass
class SyncCodec:
    """Holds the process-lifetime master key and applies it at the transport boundary.

    Built by :func:`codec_for`. The master key is an in-memory field and is never persisted,
    logged or returned — ``repr`` is overridden so a codec caught in a traceback or a debug
    log cannot leak it.
    """

    master: bytes

    def __repr__(self) -> str:  # never render key material
        return f"SyncCodec(master=<{len(self.master) * 8}-bit key withheld>)"

    def encrypt_for_push(self, objects: list[SyncObject]) -> tuple[list[SyncObject], SkipReport]:
        """Encrypt every non-routing object, then re-check the result (§4.4 send side).

        The re-check is not paranoia theatre: it is the send-side half of "plaintext in an
        encrypted store is rejected". An object that is somehow still plaintext after the
        encrypt pass is dropped rather than pushed, so the invariant is enforced on the bytes
        that actually reach the transport, not on the intention of the code above.
        """
        out: list[SyncObject] = []
        skipped = SkipReport()
        for obj in objects:
            if is_routing_key(obj.key):
                out.append(obj)
                continue
            enc = obj if is_ciphertext(obj.data) else encrypt_object(obj, self.master)
            if not is_ciphertext(enc.data):
                skipped.add(obj.key, "would have been pushed as plaintext")
                continue
            out.append(enc)
        if skipped:
            logger.error("sync encryption: refused to push %d plaintext object(s)", len(skipped))
        return out, skipped

    def decrypt_after_pull(self, objects: list[SyncObject]) -> tuple[list[SyncObject], SkipReport]:
        """Decrypt every non-routing object, sorting the failures into the two buckets.

        Dropped rather than raised on purpose, so the caller can choose a cursor verdict per
        bucket: PLAINTEXT is a permanent skip the cursor advances past (§4.4 — it can never
        become readable, and re-pulling it forever is the error loop the plan forbids), while
        a FAILED TAG holds (see :class:`SkipReport` — a mistyped passphrase must be a
        recoverable mistake, not permanent data loss).
        """
        out: list[SyncObject] = []
        skipped = SkipReport()
        for obj in objects:
            if is_routing_key(obj.key):
                out.append(obj)
                continue
            if not is_ciphertext(obj.data):
                skipped.add(obj.key, "plaintext object in an encrypted store")
                continue
            try:
                out.append(decrypt_object(obj, self.master))
            except SyncEncryptionError as exc:
                reason = str(exc).split(": ", 1)[-1]
                if "version" in reason:
                    skipped.add(obj.key, reason)
                else:
                    skipped.add_unreadable(obj.key, reason)
        if skipped.keys:
            logger.warning(
                "sync encryption: permanently skipped %d object(s): %s",
                len(skipped.keys),
                "; ".join(skipped.reasons[:5]),
            )
        if skipped.unreadable:
            logger.warning(
                "sync encryption: %d object(s) did not decrypt — wrong passphrase, or the "
                "store was modified. HOLDING them (not skipping): store the right passphrase "
                "under %s and the next cycle will merge them. First few: %s",
                len(skipped.unreadable),
                PASSPHRASE_CREDENTIAL,
                "; ".join(skipped.reasons[:3]),
            )
        return out, skipped


# ── resolution: is encryption on, and with what key ───────────────────────────


def encryption_enabled_for(transport_name: str, setting: str) -> bool:
    """Whether encryption applies, resolving the ``durability.sync_encrypt`` tri-state (§4.4).

    ``on``/``off`` are the user's explicit override; ``auto`` (the default) takes the
    per-transport default from :data:`DEFAULT_ENCRYPT_BY_TRANSPORT` — ON for third-party
    storage, OFF for ``git-sync`` where diffability is the feature. An unrecognised setting
    resolves as ``auto`` rather than as ``off``: a typo in a security control must not quietly
    disable it.

    An ``auto`` resolution for a transport the table does NOT name is logged once per process.
    A default that nobody can see is how "encryption is on, right?" becomes an assumption; the
    log line plus ``status()['sync']['encrypted']`` are what keep it a fact.
    """
    value = (setting or "auto").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    name = (transport_name or "").strip()
    if name in DEFAULT_ENCRYPT_BY_TRANSPORT:
        return DEFAULT_ENCRYPT_BY_TRANSPORT[name]
    if name and name not in _LOGGED_AUTO:
        _LOGGED_AUTO.add(name)
        logger.warning(
            "sync: transport %r has no shipped encryption default; 'auto' resolves to "
            "encryption %s. Set durability.sync_encrypt='on' to encrypt shards for it.",
            name,
            "ON" if DEFAULT_ENCRYPT_UNKNOWN_TRANSPORT else "OFF",
        )
    return DEFAULT_ENCRYPT_UNKNOWN_TRANSPORT


def load_passphrase() -> str:
    """The sync passphrase from the credential store. ``""`` when none is stored.

    The credential store is the ONLY source. Reading it from ``config.json`` or a provider
    settings field would put it in `GET /api/config`, in every config export and in the
    time-travel git history — which is why this function takes no argument that could carry
    one in from elsewhere.
    """
    from personalclaw.config.loader import get_credential

    return get_credential(PASSPHRASE_CREDENTIAL) or ""


def codec_for(transport: SyncTransportProvider, *, setting: str = "auto") -> SyncCodec | None:
    """The codec for this transport, or ``None`` when encryption is off for it.

    Raises :class:`MissingPassphrase` / :class:`MissingSalt` when encryption IS on and cannot
    be honored — fail-closed, because the alternative is uploading a user's whole state in the
    clear to storage they chose to encrypt.
    """
    if not encryption_enabled_for(getattr(transport, "name", ""), setting):
        return None
    passphrase = load_passphrase()
    if not passphrase:
        raise MissingPassphrase(
            "sync encryption is enabled for transport "
            f"{getattr(transport, 'name', '')!r} but no passphrase is stored; save one under "
            f"the credential name {PASSPHRASE_CREDENTIAL}"
        )
    return SyncCodec(master=derive_master(passphrase, ensure_salt(transport)))


__all__ = [
    "MAGIC",
    "SALT_KEY",
    "ROUTING_KEYS",
    "PASSPHRASE_CREDENTIAL",
    "DEFAULT_ENCRYPT_BY_TRANSPORT",
    "SyncEncryptionError",
    "MissingPassphrase",
    "MissingSalt",
    "SkipReport",
    "SyncCodec",
    "is_ciphertext",
    "is_routing_key",
    "derive_master",
    "shard_key",
    "encrypt_object",
    "decrypt_object",
    "read_salt",
    "ensure_salt",
    "encryption_enabled_for",
    "load_passphrase",
    "codec_for",
]
