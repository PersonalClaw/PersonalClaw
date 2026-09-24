"""``credentials_to_keychain`` — the consented, snapshot-backed credential move (SH-2).

Moving a user's secrets between stores is the one operation in this codebase where the
project's clean-break licence does **not** apply. The pre-1.0 banner says data may break
without migration; it does not license *silently losing a credential*, which presents to the
user as "my agent stopped working" with no evidence of what happened. So this module is
built around one property:

    🔴 **NO KEY LEAVES ``.env`` UNTIL ITS VALUE HAS BEEN READ BACK OUT OF THE KEYCHAIN.**

Everything else here exists to make that property survivable:

* **A snapshot precedes the first write.** ``.env``'s exact bytes are copied to
  ``.env.pre-keychain`` (mode 0600, atomic) *before* any keychain write and *before* any
  ``.env`` rewrite. :func:`rollback_credentials_to_keychain` restores those bytes verbatim —
  a byte comparison, not a re-serialisation, because a "field-for-field" rewrite is exactly
  how a comment or an unparsed line goes missing.
* **It is idempotent, not re-entrant-by-luck.** A second run finds nothing left in ``.env``
  and returns ``moved=[]`` without touching the snapshot. Overwriting the snapshot on a
  second run would replace the pre-migration ``.env`` with the post-migration one and
  destroy the only thing rollback reads.
* **It is a no-op unless the keychain is genuinely ACTIVE.** ``credential_backend()`` — the
  resolved outcome, never the request — must say ``keychain``. A box that merely *asked* for
  one has nowhere to put the secrets, and emptying ``.env`` there is the data loss this
  module exists to prevent.
* **It never returns a value.** Every result field carries key NAMES only. A migration
  report that echoed a secret would put credentials in the API response, the SEL row and the
  browser's memory at once.

⚠️  **This is deliberately NOT a lifecycle gate/migration pair.** ``LIFECYCLE-DOCTRINE.md``
was deleted in PR #897 and there is no ``lifecycle/`` package; ``CONTRIBUTING.md`` keeps the
regime as "a mental model, not shipped machinery", deferred until the architecture stops
moving. Hand-rolling a ``m_*`` migration registry here would build a parallel mechanism that
the real one deletes. What SH-2's prose actually asks for — user-consented, snapshot-backed,
reversible, idempotent, verified — is expressed with the house patterns instead: an
``IF NOT EXISTS``-shaped precondition check, a tolerant reader, and an explicit consent flag.
Recorded as a DEVIATION in the plan's execution log.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.config import loader as _loader
from personalclaw.config.credentials import (
    _dotenv_credentials,
    _dotenv_remove_credentials,
    _keychain_delete,
    _keychain_get,
    _keychain_index,
    _keychain_save,
    credential_backend,
    requested_credential_backend,
)

logger = logging.getLogger(__name__)

#: The operation's name in the SEL audit trail and in the API payload. Spelled without the
#: deleted regime's ``m_*_`` prefix — see the module docstring.
MIGRATION_ID = "credentials_to_keychain"

#: Sits beside ``.env`` rather than in a subdirectory so the two are obviously the same
#: thing to anyone reading the home, and so the durability inventory can claim it with one
#: ``secret=True`` entry (which is also what keeps it out of every export — see
#: ``portability.EXPORT_EXCLUDE``, a projection of the inventory's secret set).
ROLLBACK_FILENAME = ".env.pre-keychain"


def rollback_snapshot_path() -> Path:
    """Where the pre-migration ``.env`` is kept while a migration is reversible.

    Derived from ``_loader.env_path()`` so it follows ``PERSONALCLAW_HOME`` and any test that
    redirects the config dir — never composed from ``Path.home()``. Reached through the MODULE,
    not a bound name: binding it would make ``patch("personalclaw.config.loader.env_path")`` miss
    this module, which is the mistake ``config/credentials.py``'s docstring records.
    """
    return _loader.env_path().with_name(ROLLBACK_FILENAME)


@dataclass
class MigrationResult:
    """What a migrate/rollback attempt did. **Key names only, never values.**"""

    ok: bool
    #: Empty on success. A refusal reason is a sentence for a human, not a code.
    reason: str = ""
    moved: list[str] = field(default_factory=list)
    #: Keys the keychain already held with the same value — the idempotent case.
    already: list[str] = field(default_factory=list)
    #: Keys that could NOT be verified in the keychain, so were LEFT in ``.env``.
    failed: list[str] = field(default_factory=list)
    rollback_available: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "moved": list(self.moved),
            "already": list(self.already),
            "failed": list(self.failed),
            "rollback_available": self.rollback_available,
        }


def pending_dotenv_keys() -> list[str]:
    """Credential names still sitting in ``.env`` — what a migration would move."""
    return sorted(_dotenv_credentials())


def credential_migration_status() -> dict:
    """The Settings surface's whole read: where secrets are, and what is reversible.

    Deliberately reports the **resolved** backend alongside the **request** so a machine
    that asked for a keychain it does not have cannot render as migrated-ready. ``blocked``
    is that mismatch named once here rather than re-derived in TypeScript.
    """
    backend = credential_backend()
    pending = pending_dotenv_keys()
    return {
        "migration": MIGRATION_ID,
        "backend": backend,
        "requested": requested_credential_backend(),
        "blocked": backend != "keychain",
        "pending_keys": pending,
        "pending": len(pending),
        "keychain_keys": len(_keychain_index()),
        "rollback_available": rollback_snapshot_path().exists(),
        "snapshot_name": ROLLBACK_FILENAME,
    }


def _audit(operation: str, outcome: str, resources: str, error: str = "") -> None:
    """Record the move in the SEL audit trail. Never fails the operation.

    ``resources`` carries key NAMES. A SEL write that raised would otherwise be able to
    abort a half-finished credential move, which is strictly worse than an unaudited one —
    so the log is best-effort and the credential path is not.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=os.environ.get("USER", "unknown"),
            operation=operation,
            outcome=outcome,
            source="credential_migration",
            resources=resources,
            error=error,
        )
    except Exception:  # pragma: no cover - audit must never break the migration
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _write_snapshot() -> None:
    """Copy ``.env``'s exact bytes to the rollback snapshot, once, at 0600.

    ``IF NOT EXISTS`` semantics on purpose: an existing snapshot is a still-reversible
    earlier migration, and clobbering it with a partly-migrated ``.env`` would leave a
    rollback that restores half the credentials.
    """
    from personalclaw.atomic_write import atomic_write_bytes

    snap = rollback_snapshot_path()
    if snap.exists():
        return
    atomic_write_bytes(snap, _loader.env_path().read_bytes(), mode=0o600, fsync=True)


def migrate_credentials_to_keychain(*, confirm: bool = False) -> MigrationResult:
    """Move every ``.env`` credential into the OS keychain. Idempotent and reversible.

    ``confirm`` is the consent flag, not a formality: the caller has to have shown the
    snapshot step. It is checked FIRST so an unconsented call cannot even read the store.

    Every ``.env`` key is moved, not a curated subset. ``.env`` *is* the credential store
    (``durability.inventory`` declares it ``secret=True``, "provider credentials") and
    ``load_credentials()`` already treats every key in it as a credential and propagates it
    to ``os.environ`` — from either store, identically. A classifier that moved "only the
    secrets" would need a registry that does not exist, and its false negatives would be
    secrets left behind in a file the user was told had been emptied.
    """
    if not confirm:
        return MigrationResult(
            ok=False,
            reason="confirmation required: this moves stored credentials between stores",
            rollback_available=rollback_snapshot_path().exists(),
        )
    backend = credential_backend()
    if backend != "keychain":
        # Fail CLOSED. Without a usable secret service there is nowhere for the secrets to
        # go, and the honest outcome is to leave `.env` exactly as it is.
        reason = (
            "the OS keychain is not the active credential backend — turn on "
            "Settings → Security → 'Store credentials in the OS keychain' first"
            if requested_credential_backend() != "keychain"
            else "no usable OS keyring backend is available on this machine; "
            "credentials stay in .env at mode 0600"
        )
        _audit(MIGRATION_ID, "refused", "", reason)
        return MigrationResult(
            ok=False, reason=reason, rollback_available=rollback_snapshot_path().exists()
        )

    env = _dotenv_credentials()
    if not env:
        # The idempotent second run. The snapshot is NOT touched and NOT deleted: a
        # completed migration stays reversible until the user rolls it back.
        return MigrationResult(ok=True, rollback_available=rollback_snapshot_path().exists())

    _write_snapshot()

    moved: list[str] = []
    already: list[str] = []
    failed: list[str] = []
    for key in sorted(env):
        value = env[key]
        if _keychain_get(key) == value:
            # Already there with the same bytes — a resumed run after a partial failure.
            already.append(key)
            continue
        if not _keychain_save(key, value):
            failed.append(key)
            continue
        # 🔴 VERIFY BEFORE DELETE. `_keychain_save` returning True means the backend did not
        # raise; it does not mean the value is retrievable. keyring's `null` backend is
        # refused upstream, but a quota, a locked collection or a length limit can all
        # accept a write and hand back something else — and the next step deletes the only
        # other copy.
        if _keychain_get(key) != value:
            # 🔴 AND UNDO THE BAD WRITE. Leaving the key in `.env` is not enough on its own:
            # reads are the UNION of both stores with the KEYCHAIN PREFERRED, so a keychain
            # entry holding the wrong bytes would be served in place of the good `.env` copy
            # and the credential would be lost in practice while sitting on disk. Measured:
            # without this line the lying-backend test still found `.env` intact and
            # `get_credential` returning the corrupted value.
            logger.warning("keychain read-back mismatch for %s; leaving it in .env", key)
            _keychain_delete(key)
            failed.append(key)
            continue
        moved.append(key)

    removable = [*moved, *already]
    if removable:
        _dotenv_remove_credentials(removable)

    outcome = "ok" if not failed else "partial"
    _audit(MIGRATION_ID, outcome, ",".join(removable), ",".join(failed))
    return MigrationResult(
        ok=not failed,
        reason=(
            ""
            if not failed
            else f"{len(failed)} credential(s) could not be verified in the keychain and "
            "were left in .env: " + ", ".join(failed)
        ),
        moved=moved,
        already=already,
        failed=failed,
        rollback_available=rollback_snapshot_path().exists(),
    )


def verify_credential_migration() -> tuple[bool, dict]:
    """Is the migration in a consistent state? ``(ok, evidence)``.

    Checks the two things a move can get wrong, for every key the pre-migration snapshot
    holds: the keychain answers for it, and ``.env`` no longer does. With no snapshot there
    is nothing to verify and the answer is a vacuous ``True`` with ``checked=0`` — reported
    as a count so a caller can tell "verified" from "verified nothing".
    """
    snap = rollback_snapshot_path()
    if not snap.exists():
        return True, {"checked": 0, "missing": [], "still_in_dotenv": []}
    expected = _parse_env_bytes(snap.read_bytes())
    live_env = _dotenv_credentials()
    missing = sorted(k for k, v in expected.items() if _keychain_get(k) != v)
    still = sorted(k for k in expected if k in live_env)
    return (not missing and not still), {
        "checked": len(expected),
        "missing": missing,
        "still_in_dotenv": still,
    }


def rollback_credentials_to_keychain(*, confirm: bool = False) -> MigrationResult:
    """Undo the move: restore ``.env`` from the snapshot and clear the keychain copies.

    ``.env`` is written from the snapshot's **exact bytes**, so comments, ordering and any
    line the parser does not understand come back unchanged. Only the keys the snapshot
    holds are removed from the keychain — a credential the user stored directly into the
    keychain after migrating is not this operation's business.
    """
    if not confirm:
        return MigrationResult(
            ok=False,
            reason="confirmation required: this rewrites .env from the pre-migration snapshot",
            rollback_available=rollback_snapshot_path().exists(),
        )
    snap = rollback_snapshot_path()
    if not snap.exists():
        reason = f"no pre-migration snapshot ({ROLLBACK_FILENAME}) — nothing to roll back to"
        _audit(f"{MIGRATION_ID}_rollback", "refused", "", reason)
        return MigrationResult(ok=False, reason=reason)

    from personalclaw.atomic_write import atomic_write_bytes

    raw = snap.read_bytes()
    keys = sorted(_parse_env_bytes(raw))
    atomic_write_bytes(_loader.env_path(), raw, mode=0o600, fsync=True)

    failed = [k for k in keys if not _keychain_delete(k)]
    # Mirror the values back into the running process: `save_credential` put them in
    # `os.environ` on the way in, and a stale keychain-era value there would outlive the
    # rollback for the lifetime of the gateway.
    restored = _parse_env_bytes(raw)
    for key, value in restored.items():
        os.environ[key] = value

    # The snapshot goes last, and only on a clean pass. Deleting it after a partial
    # keychain-clear would strand a plaintext-free rollback with keychain copies still live.
    if not failed:
        snap.unlink()
    _audit(f"{MIGRATION_ID}_rollback", "ok" if not failed else "partial", ",".join(keys))
    return MigrationResult(
        ok=not failed,
        reason=(
            ""
            if not failed
            else f"{len(failed)} keychain entr(y/ies) could not be removed: " + ", ".join(failed)
        ),
        moved=keys,
        failed=failed,
        rollback_available=bool(failed),
    )


def _parse_env_bytes(raw: bytes) -> dict[str, str]:
    """Parse snapshot bytes with ``.env``'s own rules. Reader only — never a writer.

    Shares :func:`personalclaw.config.credentials._dotenv_credentials`'s grammar
    (``#`` comments, ``KEY=VALUE``, trimmed) but reads BYTES rather than the live path, so
    verify and rollback can inspect the snapshot without pointing the live parser at it.
    """
    out: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out
