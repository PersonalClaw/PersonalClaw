"""Abstract base for sync transport providers.

A sync transport carries durability shard objects between machines through one remote —
a git repo, a synced folder, later an object store. A push is insert-only and idempotent on
the object key, because the sync cycle retries freely on a CAS race and a retried push of an
object already present must be a no-op, never a duplicate or an overwrite. A transport owns
credentials and byte movement ONLY; the merge, the machine-seq registry contents, the outbox,
and which copies the remote keeps all live above it in :mod:`personalclaw.durability`.

Each sync sends this machine's records as one whole copy, and the cycle removes the copies a
newer one has replaced (``durability.published``) through :meth:`SyncTransportProvider.remove`.
A transport that can't remove an object — or whose remote keeps every version anyway, as a git
history does — leaves ``removes_old_copies`` False, and its remote keeps every copy, which the
Backups page says.

A transport whose remote is a folder on this machine — a synced folder, a clone — reads, writes
and removes nothing outside it. Whoever else writes that folder can put anything in it, a link to
any file of this machine's included, and a key followed through one reads that file as another
machine's object, writes an object over it, or removes it. So a key that names a path outside the
folder, or that leads out of it through a link, is refused with :class:`KeysRefused`, and nothing
is read, written or removed through it (``personalclaw.sdk.sync.is_path_in_store`` is the rule).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class KeysRefused(Exception):
    """A transport would not read or write these keys, and the call it refused them in did nothing.

    Each names a path outside the transport's remote, or one that leads out of it through a
    link. ``refused`` is each key with why, as the sync report says it; the message says what
    was refused and what to do about it. The sync cycle refuses a peer's change a key of it is
    refused in, whole, and a registry, salt or push that is refused fails the cycle; either way
    the report names the keys.

    Not an ``OSError``: a transport says a failed read or write of its remote as a retryable
    failure, and a refusal is neither a failure nor retryable.
    """

    def __init__(self, message: str, refused: dict[str, str]) -> None:
        super().__init__(message)
        self.refused = dict(refused)


@dataclass
class SyncObject:
    """One shard object to move — an opaque key plus its bytes.

    ``key`` is the remote-relative path (e.g. ``machines/<id>/seq-0007/tasks/tasks.jsonl``);
    the transport must preserve it exactly so ``list_remote`` → ``pull`` round-trips.
    """

    key: str
    data: bytes


@dataclass
class RemoteRef:
    """A remote object as the transport sees it, without fetching its bytes.

    ``fingerprint`` is whatever the transport can cheaply produce (mtime, etag, or sha) —
    the sync cycle only compares it for change, never parses it, so its format is the
    transport's business.
    """

    key: str
    size: int = 0
    fingerprint: str = ""


@dataclass
class PushResult:
    """Outcome of one push. ``outcome`` is the typed deliverer verdict the outbox reads:
    ``delivered`` (all objects landed), ``transient`` (retryable — a lock/race/network
    blip), or ``permanent`` (a bad payload or auth failure that retrying will not fix).
    """

    pushed: int = 0
    skipped: int = 0
    outcome: str = "delivered"
    detail: str = ""


@dataclass
class ConnectionResult:
    """The reachability probe result — the ``test_connection`` precedent other providers
    follow, so the Store can show a green/red dot without a full sync."""

    ok: bool = False
    detail: str = ""
    extra: dict = field(default_factory=dict)


class SyncTransportProvider(ABC):
    """One remote's transport. Subclasses are installed as ``sync`` provider apps."""

    #: Stable identifier, matched to the app name; the registry keys on it.
    name: str = ""
    #: Human label for the Store / doctor.
    display_name: str = ""
    #: True for a transport that removes objects from its remote (:meth:`remove`), so a sync
    #: keeps only this machine's newest copies there. Default False: its remote keeps every copy
    #: a sync ever sent, and the Backups page says so.
    removes_old_copies: bool = False

    @abstractmethod
    def push(self, objects: list[SyncObject]) -> PushResult:
        """Write objects to the remote. Insert-only and idempotent on ``key``: an object
        whose key already exists is skipped, not overwritten, so a retry is free. A key it
        won't write is raised (:class:`KeysRefused`), and none of the objects is left written."""

    @abstractmethod
    def list_remote(self, prefix: str = "") -> list[RemoteRef]:
        """Every remote object under ``prefix`` (empty = all), cheaply — refs, not bytes. A
        link it won't follow under ``prefix``, or on the way to it, is raised
        (:class:`KeysRefused`), never listed as an object."""

    @abstractmethod
    def pull(self, refs: list[RemoteRef]) -> list[SyncObject]:
        """Fetch the bytes for the given refs. A ref the remote no longer has is dropped
        from the result rather than raising — the caller reconciles against what it asked
        for. A key it won't read is raised (:class:`KeysRefused`), and none is returned."""

    @abstractmethod
    def cas_registry(self, expected_sha: str | None, data: bytes) -> bool:
        """Compare-and-swap the shared ``registry.json``. Writes ``data`` only if the
        remote registry's current sha equals ``expected_sha`` (``None`` = expect absent).
        Returns True on success, False on a lost race — the caller re-pulls and retries.
        A transport without atomic CAS (dir-sync) degrades to rename-based locking. A
        registry it won't read or write (:class:`KeysRefused`) is raised."""

    @abstractmethod
    def test(self) -> ConnectionResult:
        """Cheap reachability + auth probe. Never raises — a failure is a ``ConnectionResult``
        with ``ok=False`` and a human ``detail``."""

    def remove(self, keys: list[str]) -> int:
        """Remove objects from the remote, and return how many were there to remove. Idempotent
        on ``key``: one already gone is not an error. A key it won't remove is raised
        (:class:`KeysRefused`), and none of them is removed; a remote it can't reach raises, as
        a listing does.

        The sync cycle calls this only on a transport that sets ``removes_old_copies``, and only
        for this machine's own copies that a newer one replaced. One that doesn't remove keeps
        this default."""
        raise NotImplementedError(f"{self.display_name or self.name} keeps every copy it is sent")
