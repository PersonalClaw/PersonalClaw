"""The credential store — two backends behind one API (SECURITY-HARDENING C1 + SH-2).

🔴 **WHY THIS IS ITS OWN MODULE.** It began as a section of ``config/loader.py``. That file
sits at the top of ``scripts/generate_structural_baseline.py``'s ``SIZE_CEILING_LINES``
watch band, with ~100 lines of headroom, and ``tests/test_structural_baseline.py`` names
this exact scenario in its docstring: "adding one boolean toggle would red CI". SH-2 adds a
boolean toggle (``security.credential_keychain``), a keychain delete and a ``.env`` removal
path, so the cohesive section those belong to moved out to pay for them. Same reasoning and
same shape as ``agents/native/decision_tool_defs.py``, extracted from ``builtin_tools.py``
for the same rail. **There is no re-export shim in ``loader``** — importers were updated.

Two backends sit behind :func:`save_credential` / :func:`get_credential` /
``AppConfig.load_credentials``. Callers never name one:

  ``keychain``  the OS secret service via the OPTIONAL ``keyring`` extra (macOS Keychain,
                Linux Secret Service, Windows Credential Locker). Opt-in with
                ``security.credential_keychain`` in ``config.json`` or
                ``PERSONALCLAW_CREDENTIAL_BACKEND=keychain``.
  ``dotenv``    ``~/.personalclaw/.env`` at mode 0600 — the default, and the FAIL-CLOSED
                destination whenever the keychain is unavailable or errors. There is no
                third location: a headless box that asked for a keychain and has none keeps
                its secrets in that same 0600 file and is TOLD SO by ``doctor`` — never a
                new plaintext file somewhere else, never looser permissions.

The write/read asymmetry is deliberate:
  * WRITES go to the ACTIVE backend only (:func:`credential_backend`), falling back to
    ``.env`` 0600 if the keychain write fails.
  * READS are the UNION of both stores, keychain preferred, regardless of which backend is
    active. That is what makes reads backend-transparent: flipping the gate back off must
    not make an already-stored secret vanish, and SH-2's ``credentials_to_keychain`` move
    needs both halves readable while it carries keys across.

The consented move itself lives in :mod:`personalclaw.config.credential_migration`, which
is a *one-time operation* on this store rather than part of it.

⚠️  **Nothing from ``loader`` is bound by name here** — the module is imported and every use
goes through ``_loader.env_path()`` / ``_loader.AppConfig``. That is deliberate, and the first
draft got it wrong: binding ``env_path`` made ``patch("personalclaw.config.loader.env_path")``
miss this module entirely (three tests in ``test_shepherd_fixes.py`` caught it, having patched
exactly that spelling for years). Attribute access defers the lookup, so BOTH the
``loader.config_dir`` and the ``loader.env_path`` patch spellings redirect us. The module import
is the safe direction of the cycle: ``loader`` imports THIS module only inside
``AppConfig.load_credentials``, so by the time any credential is read ``loader`` is complete.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from typing import Literal

from personalclaw.config import loader as _loader

logger = logging.getLogger(__name__)


CredentialBackend = Literal["keychain", "dotenv"]

#: Opt-in request. Only ``keychain`` turns the keychain on; anything else (unset,
#: empty, ``dotenv``, or a typo) resolves to ``dotenv``, which is the fail-closed
#: direction — an unreadable request must never be read as "use the fancier store".
CREDENTIAL_BACKEND_ENV = "PERSONALCLAW_CREDENTIAL_BACKEND"

#: Keyring service name every PersonalClaw credential is filed under.
_KEYCHAIN_SERVICE = "personalclaw"

#: Keyring holds one entry per credential plus this index entry, whose value is a
#: JSON list of the credential KEY NAMES stored there. The index exists because
#: ``keyring`` has no portable enumeration API, and ``load_credentials()`` must be
#: able to list what the keychain holds. It lives INSIDE the keychain rather than
#: in a sidecar file on purpose: key names travel with the secrets they describe,
#: and no new file appears under the config dir for a snapshot/export set to sweep.
#: The name is not a legal credential key (credential keys are env-var names), so
#: it can never collide with a real one.
_KEYCHAIN_INDEX_KEY = "__personalclaw_key_index__"

#: keyring installs these when there is no usable OS secret service. ``fail``
#: raises on every call; ``null`` SILENTLY DISCARDS what it is handed — treating
#: either as usable would be exactly the fail-open this contract forbids.
_UNUSABLE_KEYRING_BACKENDS = ("keyring.backends.fail.", "keyring.backends.null.")


def _usable_keyring() -> object | None:
    """Return the ``keyring`` module iff it is importable AND backed by a real store.

    ``keyring`` is an OPTIONAL extra: absent module → ``None``, and every caller
    degrades to ``.env``. Deliberately NOT cached — a cache would have to be reset
    by every test that blocks the import, and this runs at startup/doctor time, not
    in a hot loop.
    """
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        logger.debug("keyring is installed but no backend could be resolved", exc_info=True)
        return None
    qualified = f"{type(backend).__module__}.{type(backend).__name__}"
    if any(qualified.startswith(bad) for bad in _UNUSABLE_KEYRING_BACKENDS):
        return None
    return keyring


def keychain_available() -> bool:
    """True iff an OS secret service is present and usable through ``keyring``."""
    return _usable_keyring() is not None


def requested_credential_backend() -> CredentialBackend:
    """The backend the operator ASKED for — intent, not outcome.

    Two spellings of the same intent, in precedence order:

    1. ``PERSONALCLAW_CREDENTIAL_BACKEND`` — the process-scoped override. Honoured in
       BOTH directions: an explicit ``dotenv`` turns the keychain off for this process
       even when the config field is on, which is the recovery lever for a machine whose
       secret service has stopped answering.
    2. ``security.credential_keychain`` in ``config.json`` — the PERSISTED opt-in the
       Settings toggle writes (SH-2). Without it the env var would have to be re-exported
       for every process, and the persisted request the migration acts on would have
       nowhere to live.

    Public so the doctor probe can show request *and* outcome side by side without
    re-parsing either source (and drifting on how a typo is read).
    """
    raw = (os.environ.get(CREDENTIAL_BACKEND_ENV) or "").strip().lower()
    if raw == "keychain":
        return "keychain"
    if raw == "dotenv":
        return "dotenv"
    if raw:
        logger.warning(
            "%s=%r is not a credential backend (keychain|dotenv); falling back to the "
            "security.credential_keychain config gate",
            CREDENTIAL_BACKEND_ENV,
            raw,
        )
    # Config is read, not cached: the Settings toggle must take effect on the next
    # credential write without a gateway restart. `load()` never reads a credential, so
    # there is no recursion here; a broken config.json degrades to `dotenv` (fail-closed)
    # because `load()` already returns defaults on any parse failure.
    try:
        return "keychain" if _loader.AppConfig.load().security.credential_keychain else "dotenv"
    except Exception:  # pragma: no cover - defensive; load() itself is tolerant
        logger.debug("credential gate unreadable; using .env", exc_info=True)
        return "dotenv"


def credential_backend() -> CredentialBackend:
    """The ACTIVE credential backend — the resolved outcome, never the request.

    ``keychain`` only when it was asked for AND an OS secret service answers;
    otherwise ``dotenv``. Everything that reports the backend to a human must call
    THIS, so a box that asked for a keychain it does not have never claims to have one.
    """
    if requested_credential_backend() != "keychain":
        return "dotenv"
    return "keychain" if keychain_available() else "dotenv"


def credential_backend_warning() -> str:
    """The one-line doctor warning for a keychain request that fell back, else ``""``.

    Single source of truth for both doctor surfaces (``cli_doctor`` and the
    ``security.credential_backend`` probe) so they can never disagree about whether
    the fallback happened.
    """
    if requested_credential_backend() == "keychain" and credential_backend() == "dotenv":
        return (
            "keychain requested but no usable OS keyring backend is available — "
            "credentials stay in .env at mode 0600 (never plaintext elsewhere)"
        )
    return ""


def _keychain_index() -> list[str]:
    """Credential key names the keychain holds (empty when it holds nothing)."""
    kr = _usable_keyring()
    if kr is None:
        return []
    try:
        raw = kr.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_INDEX_KEY)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("keychain index unreadable", exc_info=True)
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("keychain key index is not valid JSON; treating the keychain as empty")
        return []
    if not isinstance(parsed, list):
        return []
    return [str(k) for k in parsed if str(k) and str(k) != _KEYCHAIN_INDEX_KEY]


def _keychain_get(key: str) -> str:
    """One credential out of the keychain, or ``""`` when absent/unavailable."""
    kr = _usable_keyring()
    if kr is None:
        return ""
    try:
        return kr.get_password(_KEYCHAIN_SERVICE, key) or ""  # type: ignore[attr-defined]
    except Exception:
        logger.debug("keychain read failed for %s", key, exc_info=True)
        return ""


def _keychain_credentials() -> dict[str, str]:
    """Every credential the keychain holds, keyed by name."""
    out: dict[str, str] = {}
    for key in _keychain_index():
        value = _keychain_get(key)
        if value:
            out[key] = value
    return out


def _keychain_save(key: str, value: str) -> bool:
    """Write one credential + index it. False on any failure, so the caller falls back."""
    kr = _usable_keyring()
    if kr is None:
        return False
    try:
        kr.set_password(_KEYCHAIN_SERVICE, key, value)  # type: ignore[attr-defined]
        index = _keychain_index()
        if key not in index:
            kr.set_password(  # type: ignore[attr-defined]
                _KEYCHAIN_SERVICE,
                _KEYCHAIN_INDEX_KEY,
                json.dumps(sorted([*index, key])),
            )
        return True
    except Exception:
        logger.warning("keychain write failed for %s; falling back to .env (0600)", key)
        return False


def _keychain_delete(key: str) -> bool:
    """Drop one credential from the keychain AND from the index. False on any failure.

    The index is updated even when ``delete_password`` raises ``PasswordDeleteError``
    (keyring's "there was nothing there" signal): an index naming a key the keychain no
    longer holds makes ``load_credentials`` report a credential that reads as ``""``, which
    is the shape of a lost secret. Removing the name is therefore treated as the operation
    and the entry deletion as best-effort, never the other way round.

    Only SH-2's rollback calls this. There is deliberately no ``delete_credential`` public
    chokepoint: nothing else in the product removes a stored credential, and adding the
    verb speculatively would be a deletion path with no consented caller.
    """
    kr = _usable_keyring()
    if kr is None:
        return False
    ok = True
    try:
        kr.delete_password(_KEYCHAIN_SERVICE, key)  # type: ignore[attr-defined]
    except Exception:
        # Absent is the post-condition this asks for, so a delete of a key that is not
        # there is not a failure — but a real backend error must not be reported as one
        # either, so the read-back below decides.
        if _keychain_get(key):
            logger.warning("keychain delete failed for %s", key)
            ok = False
    try:
        index = _keychain_index()
        if key in index:
            kr.set_password(  # type: ignore[attr-defined]
                _KEYCHAIN_SERVICE,
                _KEYCHAIN_INDEX_KEY,
                json.dumps(sorted(k for k in index if k != key)),
            )
    except Exception:
        logger.warning("keychain index update failed after deleting %s", key)
        ok = False
    return ok


def _dotenv_remove_credentials(keys: Iterable[str]) -> list[str]:
    """Delete ``KEY=VALUE`` lines from ``~/.personalclaw/.env``; return what was removed.

    The mirror of :func:`_dotenv_save_credential` and it shares that function's write
    contract exactly — ``atomic_write`` at 0600 with ``fsync``, comments and unrelated
    lines preserved. A truncated ``.env`` here would lose the credentials this operation
    exists to *keep*, so the in-place write that function's comment rejects is rejected
    twice as hard on the removal side.

    Returns the key names actually removed so the caller never claims to have moved a key
    that was not there.
    """
    ep = _loader.env_path()
    if not ep.exists():
        return []
    wanted = set(keys)
    kept: list[str] = []
    removed: list[str] = []
    for line in ep.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in wanted:
                removed.append(k)
                continue
        kept.append(line)
    if not removed:
        return []
    from personalclaw.atomic_write import atomic_write

    body = "\n".join(kept)
    atomic_write(ep, (body + "\n") if body else "", mode=0o600, fsync=True)
    return removed


def _dotenv_save_credential(key: str, value: str) -> None:
    """Upsert ``KEY=VALUE`` into ``~/.personalclaw/.env`` at mode 0600.

    Preserves other lines and comments. 0600 is the floor this backend exists to
    hold — do not relax it.
    """
    ep = _loader.env_path()
    ep.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    found = False
    if ep.exists():
        for line in ep.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k == key:
                    lines.append(f"{key}={value}")
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    # `atomic_write(mode=0o600)`, not write_text-then-chmod. Two defects in that pair:
    #
    #  • A CREATION WINDOW. `write_text` creates the file at the umask default (0644 under the
    #    common 022), and the `chmod` narrowed it only AFTER the secret was already on disk. On
    #    first creation the credential was world-readable for that window.
    #  • NO ATOMICITY. A crash or a full disk mid-write left the credential file TRUNCATED —
    #    every other key in it lost — because the target was written in place.
    #
    # `atomic_write` closes both: mkstemp creates the temp at 0600, fchmod pins the mode before
    # any content is visible, and `os.replace` swaps it in one step, so a reader sees either the
    # old file or the new one. `fsync=True` because losing a credential to a post-rename crash is
    # the same outage as never having written it. Same shape as apps/app_secret.py::_write_0600,
    # which names the umask hazard, plus the atomicity that one does not need and this one does.
    from personalclaw.atomic_write import atomic_write

    atomic_write(ep, "\n".join(lines) + "\n", mode=0o600, fsync=True)


def _dotenv_credentials() -> dict[str, str]:
    """Parse ``~/.personalclaw/.env`` into a dict, repairing loose permissions."""
    creds: dict[str, str] = {}
    ep = _loader.env_path()
    if not ep.exists():
        return creds
    try:
        if ep.stat().st_mode & 0o077:
            ep.chmod(0o600)
    except OSError:
        logger.warning("Cannot enforce permissions on %s", ep)
    for line in ep.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds


def save_credential(key: str, value: str) -> None:
    """Persist one credential through the ACTIVE credential backend (C1).

    Callers do not choose or learn the backend. With the keychain active the secret
    goes to the OS secret service; otherwise — and whenever a keychain write fails —
    it is upserted into ``~/.personalclaw/.env`` at mode 0600. Either way the value
    is mirrored into the process environment so the running gateway and the trusted
    children that inherit ``os.environ`` see it immediately (sandboxed children are
    filtered by name in ``sandbox.py``, independent of the backend).
    """
    if credential_backend() == "keychain" and _keychain_save(key, value):
        os.environ[key] = value
        return
    _dotenv_save_credential(key, value)
    os.environ[key] = value


def get_credential(key: str) -> str:
    """Read one credential, backend-transparently. ``""`` when it is not stored.

    Keychain first (it is where a migrated or keychain-written secret lives), then
    ``.env``. Both halves are consulted whichever backend is active — see the
    selector note above for why reads are a union while writes are not.
    """
    value = _keychain_get(key)
    if value:
        return value
    return _dotenv_credentials().get(key, "")
