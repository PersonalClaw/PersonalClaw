"""The credential store — two backends behind one API.

🔴 **WHY THIS IS ITS OWN MODULE.** It began as a section of ``config/loader.py``. That file
sits at the top of ``scripts/generate_structural_baseline.py``'s ``SIZE_CEILING_LINES``
watch band, with ~100 lines of headroom, and ``tests/test_structural_baseline.py`` names
this exact scenario in its docstring: "adding one boolean toggle would red CI". The keychain
backend adds a boolean toggle (``security.credential_keychain``), a keychain delete and a
``.env`` removal path, so the cohesive section those belong to moved out to pay for them.
Same reasoning and same shape as ``agents/native/decision_tool_defs.py``, extracted from
``builtin_tools.py`` for the same rail. **There is no re-export shim in ``loader``** —
importers were updated.

Two backends sit behind :func:`save_credential` / :func:`get_credential` /
:func:`credential_names` / :func:`delete_credential` / ``AppConfig.load_credentials``. Callers
never name one:

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
    not make an already-stored secret vanish, and the ``credentials_to_keychain`` move
    needs both halves readable while it carries keys across.

🔴 **EACH HOME HAS ITS OWN KEYCHAIN NAMESPACE.** The OS keychain is the machine's, not a
home's: every PersonalClaw home on the machine reaches the same one. So the keychain half of a
home's store is filed under a service name that belongs to that home, and
:func:`keychain_service` is the one place the name is made — every keychain read, write, delete
and index entry passes it (``tests/test_keychain_namespace_census.py``). The default home keeps
the name every earlier release used, ``personalclaw``, so an existing install's secrets stay
where they are. Any other home is named ``personalclaw-<id>``, from an id minted on its first
keychain write and kept in the home (:data:`KEYCHAIN_NAMESPACE_FILE`) — never from the home's
path, which can move. One name for every home let a scratch or dev home list the default home's
secrets, read them, mirror them into the environment its children inherit, overwrite them and
delete them.

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
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personalclaw.config import loader as _loader

logger = logging.getLogger(__name__)


CredentialBackend = Literal["keychain", "dotenv"]

#: Where :func:`find_credential` found a value; ``""`` when it found none.
CredentialLocation = Literal["keychain", "file", ""]

#: Opt-in request. Only ``keychain`` turns the keychain on; anything else (unset,
#: empty, ``dotenv``, or a typo) resolves to ``dotenv``, which is the fail-closed
#: direction — an unreadable request must never be read as "use the fancier store".
CREDENTIAL_BACKEND_ENV = "PERSONALCLAW_CREDENTIAL_BACKEND"

#: The keychain service name the DEFAULT home files its credentials under: the name every release
#: before per-home namespaces filed every home's credentials under, kept so an existing install's
#: secrets stay where they are. Only :func:`keychain_service` hands it to a keychain.
_DEFAULT_HOME_SERVICE = "personalclaw"

#: The file in a home other than the default one that holds the id its keychain namespace is
#: named from (:func:`keychain_service`). Minted on the home's first keychain write and never
#: rewritten: a new id would leave every item filed under the old one out of the home's reach.
KEYCHAIN_NAMESPACE_FILE = "keychain_namespace"

#: What that file holds: one ``uuid4().hex``.
_NAMESPACE_ID = re.compile(r"[0-9a-f]{32}")

#: What makes a namespace file that holds no id usable again, in the words every surface uses. No
#: snapshot carries the file, but the keychain's own item names still spell the id.
_UNREADABLE_NAMESPACE_FIX = (
    f"write the id back into this home's {KEYCHAIN_NAMESPACE_FILE} file (its keychain items are "
    f"filed under {_DEFAULT_HOME_SERVICE}-<id>), or delete the file to start a new, empty namespace"
)

#: Each namespace holds one entry per credential plus this index entry, whose value is a
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

#: Key prefix of every credential OWNED by a settings record — a provider instance's API key,
#: an app setting declared ``x-meta.sensitive`` — rather than stored by name through the
#: Secrets panel or an app's own ``save_credential`` call. The settings FILE holds a
#: ``{{secret:<key>}}`` reference to it (``config.secret_refs``), which is the only way it is
#: read, so an owned key is deliberately NOT mirrored into ``os.environ``: nothing resolves it
#: by environment name, and exporting it would hand every provider key to every child the
#: gateway spawns. The vault hides the prefix because the owning settings surface manages it,
#: and deleting it from the Secrets panel would leave that surface pointing at nothing.
OWNED_KEY_PREFIX = "PCSECRET_"


def is_owned_key(key: str) -> bool:
    """Whether ``key`` is owned by a settings record (see :data:`OWNED_KEY_PREFIX`)."""
    return key.startswith(OWNED_KEY_PREFIX)


def mirrored_into_the_environment(key: str) -> bool:
    """Whether a stored credential named ``key`` is mirrored into the process environment, for
    the trusted children that read it there: every NAMED credential, except one whose name decides
    which programs a child runs (``env.PROGRAM_RESOLUTION_NAMES``: ``PATH``, the loader's and the
    interpreters' start-up variables), and except a project's secret.

    Such a secret is stored, listed and resolved through its ``{{secret:…}}`` reference like any
    other. Mirrored, it would replace the ``PATH`` (or the libraries, or the code an interpreter
    loads first) of every program the process starts after it, whatever the secret was saved for.
    A project's secret (``secrets_vault.is_project_key``) is read only by that project's runs,
    through the resolver (``llm.credentials.resolve_secret``), which reads it from the store.
    Mirrored, it would reach every child of every run — an MCP server, a cron script, another
    project's agent — and no child reads it by its stored key anyway.
    """
    from personalclaw.env import PROGRAM_RESOLUTION_NAMES
    from personalclaw.secrets_vault import is_project_key

    return not is_owned_key(key) and not is_project_key(key) and key not in PROGRAM_RESOLUTION_NAMES


#: Whether this process keeps the OS keychain out entirely (:func:`keychain_off`).
_keychain_off = False


def keychain_off() -> Callable[[], None]:
    """Keep the OS keychain out of this process, and return the call that lets it back in.

    For a test process on a developer's machine. The OS keychain is the machine's, not a home's:
    a scratch ``PERSONALCLAW_HOME`` keeps a test out of the default home's items (each home has a
    namespace of its own, :func:`keychain_service`), but not out of the keychain. This module
    still writes the machine's keychain and deletes from it whenever ``keyring`` is importable,
    and a test that stands in the default home reads the owner's own secrets there.
    After this call every one of those finds no keychain (:func:`_usable_keyring`), and
    credentials live in ``<home>/.env`` alone. Call it before anything resolves a credential,
    and call what it returns when the process is done; each restore puts back what its own call
    found, so nested calls unwind in order. Apps reach it as
    ``personalclaw.sdk.testing.keychain_off``.
    """
    global _keychain_off
    found = _keychain_off
    _keychain_off = True

    def restore() -> None:
        global _keychain_off
        _keychain_off = found

    return restore


def _usable_keyring() -> object | None:
    """Return the ``keyring`` module iff it is importable AND backed by a real store.

    ``keyring`` is an OPTIONAL extra: absent module → ``None``, and every caller
    degrades to ``.env``. Deliberately NOT cached — a cache would have to be reset
    by every test that blocks the import, and this runs at startup/doctor time, not
    in a hot loop. Every keychain read, write and delete in this module asks here, which is
    what makes :func:`keychain_off` complete.
    """
    if _keychain_off:
        return None
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


#: Why there is no keychain to use: this install has no keychain support (the optional
#: ``keyring`` package), or it has, and no OS secret service answers — a container has none.
KEYCHAIN_NOT_INSTALLED = "not_installed"
KEYCHAIN_NO_SERVICE = "no_service"


def keychain_absence() -> str:
    """``""`` when a keychain answers, else why none does: :data:`KEYCHAIN_NOT_INSTALLED` or
    :data:`KEYCHAIN_NO_SERVICE`. What Settings says in words beside the keychain switch."""
    if _usable_keyring() is not None:
        return ""
    if _keychain_off:
        return KEYCHAIN_NO_SERVICE
    try:
        import keyring  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return KEYCHAIN_NOT_INSTALLED
    return KEYCHAIN_NO_SERVICE


# ── the home's keychain namespace ────────────────────────────────────────────

#: Whose namespace a home's keychain items are filed under: ``default`` (the default home's,
#: ``personalclaw``), ``own`` (a home named from the id it keeps), ``unnamed`` (a home with no id
#: yet, which holds nothing in the keychain until its first keychain write names it), or
#: ``unreadable`` (a home whose id file holds no id: no keychain is used for it).
KeychainNamespaceScope = Literal["default", "own", "unnamed", "unreadable"]


@dataclass(frozen=True)
class KeychainNamespace:
    """The keychain service name a home's credentials are filed under (``""`` when the home has
    none to use), and whose it is."""

    service: str
    scope: KeychainNamespaceScope


def _is_the_default_home(home: Path | None) -> bool:
    """Whether *home* — the active home when ``None`` — is the default one."""
    if home is None:
        return _loader.uses_default_home()
    return Path(home).resolve() == _loader.default_config_dir().resolve()


def _namespace_file(home: Path | None) -> Path:
    """``<home>/keychain_namespace``, worked out without making the home."""
    base = Path(home) if home is not None else _loader.resolve_config_dir()
    return base / KEYCHAIN_NAMESPACE_FILE


def _recorded_namespace_id(path: Path) -> str | None:
    """The id *path* holds: ``None`` when there is no such file, ``""`` when it holds no id."""
    try:
        raw = path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        logger.debug("keychain namespace file %s is unreadable", path, exc_info=True)
        return ""
    value = raw.strip()
    return value if _NAMESPACE_ID.fullmatch(value) else ""


def _mint_namespace_id(path: Path) -> str | None:
    """Give the home *path* is in its id, once, and return the id it holds afterwards.

    The id is written whole to a file of its own (0600), then LINKED to its name, which fails
    when the name exists. So the name appears with the whole id or not at all, and of two
    processes naming one home at the same moment exactly one id lands: the other reads it back
    and files under it, rather than writing an item under an id the home never reads again.
    """
    from personalclaw.atomic_write import atomic_write

    fresh = uuid.uuid4().hex
    staged = path.with_name(f".{path.name}.{fresh}.tmp")
    try:
        atomic_write(staged, fresh + "\n", mode=0o600, fsync=True)
        try:
            os.link(staged, path)
        except FileExistsError:
            pass  # another process named the home first, and its id is the home's
    finally:
        staged.unlink(missing_ok=True)
    return _recorded_namespace_id(path)


def _namespace(home: Path | None, *, mint: bool) -> KeychainNamespace:
    """What :func:`keychain_service` and :func:`keychain_namespace` answer: the name *home*'s
    items are filed under and whose it is, the home given its id first when *mint*."""
    try:
        if _is_the_default_home(home):
            return KeychainNamespace(_DEFAULT_HOME_SERVICE, "default")
        path = _namespace_file(home)
        recorded = _recorded_namespace_id(path)
        if recorded is None and mint:
            recorded = _mint_namespace_id(path)
    except OSError:
        # Fail closed: a home whose namespace cannot be worked out uses no keychain at all.
        logger.warning("this home's keychain namespace could not be read", exc_info=True)
        return KeychainNamespace("", "unreadable")
    if recorded is None:
        return KeychainNamespace("", "unnamed")
    if not recorded:
        return KeychainNamespace("", "unreadable")
    return KeychainNamespace(f"{_DEFAULT_HOME_SERVICE}-{recorded}", "own")


def keychain_service(home: Path | None = None, *, mint: bool = False) -> str:
    """The keychain service name *home*'s credentials are filed under — the active home's when
    none is named — or ``""`` when that home has none to use.

    🔴 The one place that name is made. Every keychain read, write, delete and index entry passes
    what this returns, and nothing else does (``tests/test_keychain_namespace_census.py``).

    The default home answers ``personalclaw``, the name every home used before each had its own.
    Any other home answers ``personalclaw-<id>``, the id read from its
    :data:`KEYCHAIN_NAMESPACE_FILE`. A home with no id yet holds nothing in the keychain, so it
    answers ``""`` and a read finds nothing — unless *mint*, which only a keychain WRITE passes:
    the home is given its id first. A file that holds no id answers ``""`` and is left as it is.
    Replacing it would put the items filed under the old id out of this home's reach for good,
    and the default home's name in its place is the leak this function exists to close.
    """
    return _namespace(home, mint=mint).service


def keychain_namespace() -> KeychainNamespace:
    """The active home's keychain namespace, for the surfaces that name it: ``personalclaw
    doctor``, its probe and Settings → Secrets. Never mints."""
    return _namespace(None, mint=False)


def keychain_namespace_summary(namespace: KeychainNamespace) -> str:
    """*namespace* by name and whose it is, in the words both doctor surfaces print."""
    if namespace.scope == "default":
        return f"{namespace.service} — the default home's"
    if namespace.scope == "own":
        return f"{namespace.service} — this home's own"
    if namespace.scope == "unnamed":
        return "none yet — this home's own is named when it first stores a secret in the keychain"
    return (
        f"unreadable — this home's {KEYCHAIN_NAMESPACE_FILE} file holds no namespace id, so no "
        f"keychain is used here: {_UNREADABLE_NAMESPACE_FIX}"
    )


def requested_credential_backend() -> CredentialBackend:
    """The backend the operator ASKED for — intent, not outcome.

    Two spellings of the same intent, in precedence order:

    1. ``PERSONALCLAW_CREDENTIAL_BACKEND`` — the process-scoped override. Honoured in
       BOTH directions: an explicit ``dotenv`` turns the keychain off for this process
       even when the config field is on, which is the recovery lever for a machine whose
       secret service has stopped answering.
    2. ``security.credential_keychain`` in ``config.json`` — the PERSISTED opt-in the
       Settings toggle writes. Without it the env var would have to be re-exported
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

    ``keychain`` only when it was asked for AND an OS secret service answers AND this home has
    a namespace there it can use (:func:`keychain_service`); otherwise ``dotenv``. Everything
    that reports the backend to a human must call THIS, so a box that asked for a keychain it
    does not have never claims to have one.
    """
    if requested_credential_backend() != "keychain" or not keychain_available():
        return "dotenv"
    return "dotenv" if keychain_namespace().scope == "unreadable" else "keychain"


def credential_backend_warning() -> str:
    """The one-line doctor warning for a keychain request that fell back, else ``""``.

    Single source of truth for both doctor surfaces (``cli_doctor`` and the
    ``security.credential_backend`` probe) so they can never disagree about whether
    the fallback happened.
    """
    if requested_credential_backend() != "keychain" or credential_backend() != "dotenv":
        return ""
    if keychain_available():
        return (
            f"keychain requested but this home's {KEYCHAIN_NAMESPACE_FILE} file holds no "
            "namespace id — credentials stay in .env at mode 0600 (never plaintext elsewhere)"
        )
    return (
        "keychain requested but no usable OS keyring backend is available — "
        "credentials stay in .env at mode 0600 (never plaintext elsewhere)"
    )


@dataclass(frozen=True)
class CredentialStoreState:
    """What an inspection of the credential store OBSERVED — never what it promises.

    ``env_mode`` is the mode that was actually read off the file, so it is empty unless
    ``env_exists`` is true. When ``env_readable`` is false the file could not be inspected
    at all and NOTHING else here is established about it — absent and unreadable are
    different observations, and a caller must not render either as a mode.
    """

    backend: CredentialBackend
    requested: CredentialBackend
    env_path: str
    env_exists: bool
    env_mode: str
    env_readable: bool
    #: This home's keychain namespace when an OS keychain answers (reads consult it whichever
    #: backend is active), else ``None``.
    keychain: KeychainNamespace | None = None

    @property
    def env_group_or_world_readable(self) -> bool:
        """True only when a mode was READ and it grants group/other any bit."""
        return bool(self.env_mode) and bool(int(self.env_mode, 8) & 0o077)

    @property
    def keychain_summary(self) -> str:
        """The keychain namespace by name and whose it is, as both doctor surfaces print it
        (:func:`keychain_namespace_summary`), or ``""`` when no keychain answered."""
        return keychain_namespace_summary(self.keychain) if self.keychain else ""

    @property
    def keychain_fix(self) -> str:
        """What makes the keychain usable again after a request for it fell back
        (:func:`credential_backend_warning`): an OS keyring for this process, or, when one
        answered, a namespace id this home can read."""
        if self.keychain is not None:
            return _UNREADABLE_NAMESPACE_FIX
        return "make an OS keyring available to this process"


def credential_store_state() -> CredentialStoreState:
    """Inspect the credential store and report only what the inspection established (#2922).

    Both doctor surfaces render from this one function — ``cli_doctor``'s ``credentials:``
    row and the ``security.credential_backend`` probe — for the same reason
    :func:`credential_backend_warning` is shared: two surfaces that each re-derive the same
    facts eventually disagree about them.

    🔴 **THE DISTINCTION THIS TYPE EXISTS FOR.** 0600 is the mode the ``.env`` fallback
    PROMISES, and both surfaces used to print it as though they had measured it — the CLI
    row hardcoded the literal without stat-ing anything, and the probe rendered
    ``mode or '0600'``. So a fresh install with no credentials and no ``.env`` reported
    "credentials stored in .env at mode 0600" and named a path that did not exist, and a
    ``.env`` sitting at 0640 reported 0600 as well. Reporting the promise as an observation
    is the defect; separating ``env_exists`` / ``env_readable`` from ``env_mode`` is what
    makes it unstatable.

    Reads no secret VALUE and repairs nothing — the next ``load_credentials()`` owns the
    0600 repair, and a diagnostic that silently changed permissions would be reporting on
    its own side effect.
    """
    ep = _loader.env_path()
    exists = False
    mode = ""
    readable = True
    try:
        exists = ep.exists()
        if exists:
            mode = format(ep.stat().st_mode & 0o777, "04o")
    except OSError:
        logger.debug("credential file state could not be read", exc_info=True)
        exists, mode, readable = False, "", False
    return CredentialStoreState(
        backend=credential_backend(),
        requested=requested_credential_backend(),
        env_path=str(ep),
        env_exists=exists,
        env_mode=mode,
        env_readable=readable,
        keychain=keychain_namespace() if keychain_available() else None,
    )


def _keychain_index() -> list[str]:
    """Credential key names the active home's keychain namespace holds, and empty when it holds
    nothing or the home has no namespace yet."""
    kr = _usable_keyring()
    if kr is None:
        return []
    service = keychain_service()
    if not service:
        return []
    try:
        raw = kr.get_password(service, _KEYCHAIN_INDEX_KEY)  # type: ignore[attr-defined]
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


def _keychain_get(key: str, home: Path | None = None) -> str:
    """One credential out of *home*'s keychain namespace (the active home's when none is
    named), or ``""`` when it is absent, no keychain answers, or the home has no namespace."""
    kr = _usable_keyring()
    if kr is None:
        return ""
    service = keychain_service(home)
    if not service:
        return ""
    try:
        return kr.get_password(service, key) or ""  # type: ignore[attr-defined]
    except Exception:
        logger.debug("keychain read failed for %s", key, exc_info=True)
        return ""


def _keychain_credentials() -> dict[str, str]:
    """Every credential the active home's keychain namespace holds, keyed by name."""
    out: dict[str, str] = {}
    for key in _keychain_index():
        value = _keychain_get(key)
        if value:
            out[key] = value
    return out


def _keychain_save(key: str, value: str) -> bool:
    """Write one credential into the active home's keychain namespace + index it. False on any
    failure, so the caller falls back. A home's first keychain write names its namespace."""
    kr = _usable_keyring()
    if kr is None:
        return False
    service = keychain_service(mint=True)
    if not service:
        logger.warning(
            "this home has no keychain namespace for %s; falling back to .env (0600)", key
        )
        return False
    try:
        kr.set_password(service, key, value)  # type: ignore[attr-defined]
        index = _keychain_index()
        if key not in index:
            kr.set_password(  # type: ignore[attr-defined]
                service,
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

    Two callers: the credential migration's rollback, and :func:`delete_credential` — the
    public chokepoint the secrets vault's ``DELETE /api/secrets`` needs. Until that route
    existed there was deliberately no public delete verb, because a deletion path with no
    consented caller is a liability; the vault is that caller, and it goes through the
    chokepoint rather than reaching in here.
    """
    kr = _usable_keyring()
    if kr is None:
        return False
    service = keychain_service()
    if not service:
        # A home never named holds nothing in the keychain, so the key is absent there; a home
        # whose namespace cannot be read cannot say whether it is.
        return keychain_namespace().scope == "unnamed"
    ok = True
    try:
        kr.delete_password(service, key)  # type: ignore[attr-defined]
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
                service,
                _KEYCHAIN_INDEX_KEY,
                json.dumps(sorted(k for k in index if k != key)),
            )
    except Exception:
        logger.warning("keychain index update failed after deleting %s", key)
        ok = False
    return ok


def _dotenv_remove_credentials(keys: Iterable[str]) -> list[str]:
    """Delete ``KEY=VALUE`` lines from ``~/.personalclaw/.env``; return what was removed.

    The mirror of :func:`_dotenv_save_credentials` and it shares that function's write
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


#: What a value written after ``KEY=`` may not contain, start with, or end with and still be
#: read back as itself by BOTH readers of this file: :func:`_dotenv_credentials`, and
#: python-dotenv, which ``personalclaw``'s CLI loads the same file with at startup
#: (``cli.main``). A newline would end the line, surrounding whitespace is stripped, a leading
#: quote starts python-dotenv's quoted form, and ``#`` after whitespace starts its comment.
_DOTENV_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}
_DOTENV_UNESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}


def _encode_dotenv_value(value: str) -> str:
    """``value`` as it is written after ``KEY=``.

    Verbatim when a line holds it faithfully, which is every value this file held before, so an
    existing ``.env`` is unchanged. Otherwise double-quoted with backslash escapes: a PEM key or a
    service-account JSON is one line in the file and exactly itself when read.
    """
    if (
        value == value.strip()
        and not value.startswith(("'", '"'))
        and not any(ch in value for ch in "\n\r#")
    ):
        return value
    return '"' + "".join(_DOTENV_ESCAPES.get(ch, ch) for ch in value) + '"'


def _decode_dotenv_value(raw: str) -> str:
    """The value a ``KEY=`` line holds (``raw`` is the part after ``=``, stripped).

    The double-quoted form is python-dotenv's, decoded with its escapes, so the credential store
    and the CLI's loader read one string. Anything else is taken verbatim, as it always was,
    including a quote that does not close.
    """
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        return raw
    out: list[str] = []
    body = raw[1:-1]
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            return raw  # an unescaped quote inside: not the quoted form
        if ch == "\\" and i + 1 < len(body) and body[i + 1] in _DOTENV_UNESCAPES:
            out.append(_DOTENV_UNESCAPES[body[i + 1]])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _dotenv_save_credentials(values: Mapping[str, str]) -> None:
    """Upsert every ``KEY=VALUE`` in *values* into ``~/.personalclaw/.env`` at mode 0600, in one
    write.

    Preserves other lines and comments. 0600 is the floor this backend exists to
    hold — do not relax it. One line per credential, whatever the value holds
    (:func:`_encode_dotenv_value`).
    """
    ep = _loader.env_path()
    remaining = dict(values)
    lines: list[str] = []
    if ep.exists():
        for line in ep.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in remaining:
                    lines.append(f"{k}={_encode_dotenv_value(remaining.pop(k))}")
                    continue
            lines.append(line)
    lines.extend(f"{k}={_encode_dotenv_value(v)}" for k, v in remaining.items())
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
    # the same outage as never having written it. `apps/app_secret.py` mints the app proxy
    # secret the same way.
    from personalclaw.atomic_write import atomic_write

    atomic_write(ep, "\n".join(lines) + "\n", mode=0o600, fsync=True)


def _env_file(home: Path | None) -> Path:
    """``<home>/.env`` for an explicit *home*, else the active home's (``loader.env_path``)."""
    return Path(home) / ".env" if home is not None else _loader.env_path()


def _dotenv_credentials(home: Path | None = None) -> dict[str, str]:
    """Parse ``<home>/.env`` into a dict, repairing loose permissions."""
    creds: dict[str, str] = {}
    ep = _env_file(home)
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
            creds[k.strip()] = _decode_dotenv_value(v.strip())
    return creds


def save_credential(key: str, value: str) -> None:
    """Persist one credential through the ACTIVE credential backend.

    Callers do not choose or learn the backend. With the keychain active the secret
    goes to the OS secret service; otherwise — and whenever a keychain write fails —
    it is upserted into ``~/.personalclaw/.env`` at mode 0600. A NAMED credential is
    then mirrored into the process environment so the running gateway and the trusted
    children that inherit ``os.environ`` see it immediately (sandboxed children are
    filtered by name in ``sandbox.py``, independent of the backend). An OWNED key
    (:func:`is_owned_key`) is not: it is read only through its settings reference. Nor is a
    project's secret, read only by that project's runs, nor a name that decides which programs
    run (:func:`mirrored_into_the_environment`).
    """
    save_credentials({key: value})


def save_credentials(values: Mapping[str, str]) -> None:
    """Persist several credentials at once, each with :func:`save_credential`'s contract.

    On the ``.env`` backend it is ONE atomic write of the file, so a crash leaves either every
    value stored or none of them. The keychain holds one entry per credential, so there each
    value is its own write, and one that fails falls back to ``.env`` with the rest.
    """
    pending = dict(values)
    if credential_backend() == "keychain":
        pending = {key: value for key, value in pending.items() if not _keychain_save(key, value)}
    if pending:
        _dotenv_save_credentials(pending)
    for key, value in values.items():
        if mirrored_into_the_environment(key):
            os.environ[key] = value


def find_credential(key: str, *, home: Path | None = None) -> tuple[str, CredentialLocation]:
    """One credential and where it was found: ``"keychain"``, ``"file"`` (``.env``), or
    ``("", "")`` when neither holds it.

    Keychain first (it is where a migrated or keychain-written secret lives), then ``.env``.
    Both halves are consulted whichever backend is active — see the selector note above for why
    reads are a union while writes are not. *home* names the store to read (the active home's by
    default): its ``.env``, and its own keychain namespace (:func:`keychain_service`).
    """
    value = _keychain_get(key, home)
    if value:
        return value, "keychain"
    value = _dotenv_credentials(home).get(key, "")
    return (value, "file") if value else ("", "")


def get_credential(key: str) -> str:
    """Read one credential, backend-transparently. ``""`` when it is not stored."""
    return find_credential(key)[0]


def _dotenv_names() -> list[str]:
    """Credential key NAMES in ``~/.personalclaw/.env`` — the value side is never read.

    Not ``_dotenv_credentials().keys()``. That would build the whole ``{name: value}`` dict
    and then throw the values away, which puts every stored credential in a live local of a
    presence-only read path. Splitting the line and keeping only the left-hand side means the
    value never becomes a Python object at all — the structural half of "presence-only", as
    opposed to a value that is fetched and then filtered out downstream.
    """
    ep = _loader.env_path()
    if not ep.exists():
        return []
    names: list[str] = []
    try:
        lines = ep.read_text().splitlines()
    except OSError:
        logger.debug("credential .env unreadable while listing names", exc_info=True)
        return []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def credential_names() -> list[str]:
    """Every credential key name the store holds, sorted, with NO value read anywhere.

    The union of both backends — same union rule as :func:`get_credential`, so a key stored in
    either place is listed whichever backend is active. The keychain half already enumerates by
    name (``_keychain_index``), and the ``.env`` half goes through :func:`_dotenv_names`, so no
    call in this function can return a secret VALUE. That is what lets the secrets vault build
    its whole read model without a value-bearing call in its import graph
    (``tests/test_secrets_vault.py`` asserts exactly that, statically).

    Callers that need to know a key EXISTS must use this, not ``get_credential(k) != ""``: the
    second reads the value in order to discard it, and a route written that way leaks the moment
    someone returns the local it already has.
    """
    return sorted({*_keychain_index(), *_dotenv_names()})


def delete_credential(key: str) -> bool:
    """Remove one credential from BOTH backends. True when it was there and is now gone.

    Both halves unconditionally, not "the active backend": reads are a union
    (:func:`get_credential`), so deleting from only the active store would leave a partly
    migrated install still resolving the key from the other one — a delete that reports success
    and changes nothing the resolver sees. The process environment is cleared too, for the same
    reason ``save_credential`` sets it: a running gateway that keeps serving the value it was
    just told to forget has not deleted it.

    Absent is the post-condition, so deleting a key that was never stored returns ``False``
    without raising — the caller distinguishes "removed" from "there was nothing there" for its
    own 404, but neither is an error here.
    """
    mirrored = mirrored_into_the_environment(key)
    existed = key in credential_names() or (mirrored and key in os.environ)
    if _usable_keyring() is not None:
        _keychain_delete(key)
    _dotenv_remove_credentials([key])
    if mirrored:
        os.environ.pop(key, None)
    return existed


def owner_id_credential(provider: str) -> str:
    """The credential key a channel keeps its owner's user id under.

    ``PERSONALCLAW_OWNER_ID_<PROVIDER>`` — one key per channel, named by the channel's provider
    key (the string it passes to ``deliver_channel_inbound`` and the trust seam: ``slack``,
    ``telegram``). Slack, Telegram and Discord all wrote the ONE key ``PERSONALCLAW_OWNER_ID``,
    so setting up a second channel overwrote the first one's owner with an id from another
    platform, and an owner notification could go through one channel addressed to a user of
    another.
    """
    slug = "".join(ch if ch.isalnum() else "_" for ch in provider.strip()).strip("_").upper()
    if not slug:
        raise ValueError("a channel's owner id is keyed by its provider name, which is empty")
    return f"{_loader.CRED_OWNER_ID}_{slug}"


def owner_id_for(provider: str) -> str:
    """The owner's user id on ``provider``'s channel, or ``""`` when none is known.

    The channel's own key first. Then the one shared key the channels used before each had its
    own: it is what an app that still writes ``CRED_OWNER_ID`` stored, so reading it here keeps
    that channel's owner where it was, until core keeps the channel's owner itself (it paired
    one, or forgot one: ``channel_trust.shared_owner_retired``). Each key is looked up in the
    environment first (a container passes it that way), then in the store — the precedence
    ``load_credentials`` gives every named credential.
    """
    return owner_id_source(provider)[0]


def owner_id_source(provider: str) -> tuple[str, str]:
    """:func:`owner_id_for`, and which key answered: ``"channel"`` (the channel's own),
    ``"shared"`` (the one every channel wrote before each had its own), or ``("", "")``.

    The status a channel shows says which, because a shared id can be another platform's user
    id — the owner core then tries to reach on this channel is nobody here. The shared key is
    not read for a channel whose owner core has paired or forgotten: an owner core forgot must not
    come back through it, and a paired channel's owner is what its own key holds.
    """
    own = owner_id_credential(provider)
    value = os.environ.get(own) or get_credential(own)
    if value:
        return value, "channel"
    from personalclaw.channel_trust import shared_owner_retired

    if shared_owner_retired(provider):
        return "", ""
    value = os.environ.get(_loader.CRED_OWNER_ID) or get_credential(_loader.CRED_OWNER_ID)
    return (value, "shared") if value else ("", "")
