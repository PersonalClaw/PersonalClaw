"""Abstract base for sync transport providers (DURABILITY-AND-SYNC §4.3).

A sync transport carries durability shard objects between machines through one remote —
a git repo, a synced folder, later an object store. Every method is insert-only and
idempotent on the object key, because the sync cycle retries freely on a CAS race and a
retried push of an object already present must be a no-op, never a duplicate or an
overwrite (§4.1). A transport owns credentials and byte movement ONLY; the merge, the
machine-seq registry contents, and the outbox all live above it in
:mod:`personalclaw.durability.sync`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

    @abstractmethod
    def push(self, objects: list[SyncObject]) -> PushResult:
        """Write objects to the remote. Insert-only and idempotent on ``key``: an object
        whose key already exists is skipped, not overwritten, so a retry is free."""

    @abstractmethod
    def list_remote(self, prefix: str = "") -> list[RemoteRef]:
        """Every remote object under ``prefix`` (empty = all), cheaply — refs, not bytes."""

    @abstractmethod
    def pull(self, refs: list[RemoteRef]) -> list[SyncObject]:
        """Fetch the bytes for the given refs. A ref the remote no longer has is dropped
        from the result rather than raising — the caller reconciles against what it asked
        for."""

    @abstractmethod
    def cas_registry(self, expected_sha: str | None, data: bytes) -> bool:
        """Compare-and-swap the shared ``registry.json``. Writes ``data`` only if the
        remote registry's current sha equals ``expected_sha`` (``None`` = expect absent).
        Returns True on success, False on a lost race — the caller re-pulls and retries.
        A transport without atomic CAS (dir-sync) degrades to rename-based locking."""

    @abstractmethod
    def test(self) -> ConnectionResult:
        """Cheap reachability + auth probe. Never raises — a failure is a ``ConnectionResult``
        with ``ok=False`` and a human ``detail``."""
