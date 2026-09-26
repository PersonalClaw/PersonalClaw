"""Read a credential by NAME from the one credential store.

A secret stored under a name (in Settings → Secrets, with ``personalclaw setup --credential``, by
a connector pack) is read here by everything that names it: a provider entry's ``credential``
(through the ``credential_store`` build kwarg every model factory receives), ``{{secret:NAME}}``
in a workflow step or a trigger action, a knowledge connector pack, and an app through
``personalclaw.sdk.credentials``. The store is :mod:`personalclaw.config.credentials` (the OS
keychain when it is on, else ``<home>/.env`` at 0600), the one Settings → Secrets writes. This
module reads nothing else.

🔴 **Until this release there was a second store.** Every reader above read
``<home>/credentials.json``: a map of descriptors, each with an inline ``value`` or a
``value_env`` naming an environment variable. It fell back to ``.env`` only for a name it held a
descriptor for, and never to the keychain. Settings → Secrets never wrote it, so a secret saved
there never reached a workflow step, a trigger or a provider's ``credential``. And
``setup --credential`` wrote only the file, so Settings → Secrets never listed what the CLI
stored. And any code could write a descriptor (``CredentialStore.save`` was published on the
SDK), so an app could name another owner's key and read it out of ``.env``.
:func:`move_credentials_file` moves what the file held into the store at the first start, and
deletes the file once every value reads back from there.

**Resolving a name:** the process environment (a container passes a secret that way, and the
store mirrors every named secret into it), then the keychain, then ``<home>/.env``. An OWNED key
(``PCSECRET_…``, :func:`personalclaw.config.credentials.is_owned_key`) is refused with
:class:`OwnedCredentialRefused`: it belongs to the settings record that references it, and that
reference (``config.secret_refs``, owner-checked since #3626) is the only way it is read.

Property 11 (Provider SDK Lazy Import): stdlib and ``personalclaw.config`` imports only, so no
provider SDK is pulled in through here.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


CredentialKind = Literal["none", "api_key", "static_token", "oauth2"]
"""What a credential is. Everything :meth:`CredentialStore.resolve` returns is an ``api_key``;
a provider that builds a :class:`Credential` itself (an inline key, a subscription token) says
which kind it holds."""

CredentialSource = Literal["env", "keychain", "file", "none"]
"""Where a resolved secret came from: the process environment, the OS keychain, or ``.env``
(``file``). ``none`` is for a credential built with no secret. Never the value itself."""


@dataclass(frozen=True)
class Credential:
    """A secret and where it came from. ``secret`` is ``None`` for a credential with none."""

    name: str
    kind: CredentialKind
    secret: str | None = None
    source: CredentialSource = "none"


class OwnedCredentialRefused(KeyError):
    """*name* is an owned key (``PCSECRET_…``), which nothing reads by name.

    A :class:`KeyError`, so every caller that already treats an unknown name as "not configured"
    refuses this one too, with no value read. ``str()`` is the sentence a user reads, and it
    names the key, never its value.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
        #: Why it is refused, and what to do instead: the two halves a failure record keeps.
        self.cause = (
            f"{name} is where a provider's or an app's own setting keeps its secret, and only "
            "that setting can read it"
        )
        self.remedy = (
            "store the secret under a name of your own in Settings → Secrets and refer to that "
            "name"
        )

    def __str__(self) -> str:
        return f"{self.cause}. To use a secret here, {self.remedy}."


class CredentialStore:
    """Read a credential by name from the credential store of *home*.

    The ``.env`` half is ``<home>/.env``; the OS keychain has one namespace for every home. Each
    :meth:`resolve` reads the store as it is now, so a secret saved or deleted in Settings →
    Secrets takes effect on the next read.
    """

    def __init__(self, home: Path) -> None:
        self._home = Path(home)

    def resolve(self, name: str) -> Credential:
        """*name*'s value, from the environment, the keychain or ``.env``, in that order.

        Raises :class:`KeyError` when no credential of that name is stored, and
        :class:`OwnedCredentialRefused` (a ``KeyError``) for an owned key, before any value is
        read.
        """
        from personalclaw.config.credentials import find_credential, is_owned_key

        if is_owned_key(name):
            raise OwnedCredentialRefused(name)
        value = os.environ.get(name, "")
        if value:
            return Credential(name=name, kind="api_key", secret=value, source="env")
        value, where = find_credential(name, home=self._home)
        if not value:
            raise KeyError(name)
        source: CredentialSource = "keychain" if where == "keychain" else "file"
        return Credential(name=name, kind="api_key", secret=value, source=source)


# ── the one-time move of credentials.json (gateway boot) ────────────────────────────

#: The descriptor file this module read until this release. Nothing writes it any more; it
#: exists on a home only until :func:`move_credentials_file` has moved what it held.
CREDENTIALS_FILE = "credentials.json"


@dataclass(frozen=True)
class Leftover:
    """A name in ``credentials.json`` the move could not settle, and what to do about it.

    ``reason`` is a sentence for the user. It names the credential and never its value.
    """

    name: str
    reason: str


@dataclass(frozen=True)
class CredentialsFileMove:
    """What :func:`move_credentials_file` did: the names it stored, the ones it could not
    settle, and whether it deleted the file."""

    stored: list[str] = field(default_factory=list)
    leftovers: list[Leftover] = field(default_factory=list)
    removed: bool = False


def _config_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


def _read_descriptors(path: Path) -> dict[str, Any] | None:
    """The file's descriptor map, or ``None`` when it is not a JSON object."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _target(name: str) -> str:
    """The store key a descriptor's value moves to. The web push key pair becomes core-owned
    keys (``push.MOVED_FROM_CREDENTIALS_FILE``); every other name keeps its own."""
    from personalclaw.push import MOVED_FROM_CREDENTIALS_FILE

    return MOVED_FROM_CREDENTIALS_FILE.get(name, name)


def _inline_value(descriptor: Any) -> str:
    value = descriptor.get("value") if isinstance(descriptor, dict) else None
    return value if isinstance(value, str) else ""


def _movable(name: str, descriptor: Any) -> bool:
    """Whether *descriptor* holds a value the move may store: an inline value under a name that
    is not an owned key. An owned name in this file is one anything could have written there, so
    it is never stored over the settings record that owns it."""
    from personalclaw.config.credentials import is_owned_key

    return bool(_inline_value(descriptor)) and not is_owned_key(name)


def _leftovers(descriptors: dict[str, Any], home: Path) -> list[Leftover]:
    """Every name in *descriptors* the credential store does not settle, with why."""
    from personalclaw.config.credentials import find_credential, is_owned_key

    out: list[Leftover] = []
    for name, descriptor in descriptors.items():
        inline = _inline_value(descriptor)
        if inline and is_owned_key(name):
            out.append(
                Leftover(
                    name,
                    "this name is reserved for a provider's or an app's own setting, so its "
                    "value was not moved. Remove the entry from credentials.json.",
                )
            )
        elif inline:
            held = find_credential(_target(name), home=home)[0]
            if held == inline:
                continue
            if held:
                reason = (
                    "Settings → Secrets already holds a different value under this name, and "
                    "that one is used everywhere now. If the value in credentials.json is the "
                    "one you want, save it in Settings → Secrets; if not, remove the entry "
                    "from credentials.json."
                )
            else:
                reason = (
                    "its value could not be stored in the credential store. Restart to try "
                    "again, or save it in Settings → Secrets under this name."
                )
            out.append(Leftover(name, reason))
        else:
            other = descriptor.get("value_env") if isinstance(descriptor, dict) else None
            if not isinstance(other, str) or not other or other == name:
                continue  # nothing to move: no secret, or read from the variable of its own name
            if os.environ.get(name) or find_credential(name, home=home)[0]:
                continue  # stored under its own name since
            out.append(
                Leftover(
                    name,
                    f"it read its value from the environment variable {other}, which is no "
                    f"longer read for it. Save the value in Settings → Secrets under {name}.",
                )
            )
    return out


def credentials_file_leftovers(home: Path | None = None) -> list[Leftover]:
    """What ``<home>/credentials.json`` still holds that the credential store does not: ``[]``
    when there is no such file. Read-only, and it reads no value into a result: the Doctor's
    ``security.credentials_file`` check lists these."""
    path = (Path(home) if home is not None else _config_dir()) / CREDENTIALS_FILE
    if not path.is_file():
        return []
    descriptors = _read_descriptors(path)
    if descriptors is None:
        return [
            Leftover(
                CREDENTIALS_FILE,
                "the file is not a JSON object, so nothing in it could be moved. Save each "
                "credential it held in Settings → Secrets, then delete the file.",
            )
        ]
    return _leftovers(descriptors, path.parent)


def move_credentials_file() -> CredentialsFileMove:
    """Move the active home's ``credentials.json`` into the credential store, then delete it.

    Called once at gateway boot. Every inline value is stored under its own name (the web push
    pair under core-owned keys), then read back from the store. The file is deleted only when
    every name in it is settled: its value reads back unchanged, it held no value, or it read
    from the variable of its own name. Anything else is a :class:`Leftover`, and then the file
    is kept exactly as it was, the names (never the values) are logged, and the Doctor lists
    them with what to do.

    Idempotent: a value already in the store is not written again, and without a file this does
    nothing. On the ``.env`` backend every value goes in one atomic write and the file is
    deleted after it, so a crash leaves either the old state or the new one. With the keychain
    on, each value is its own entry, so a crash can store some of them; the file is still there
    and the next start finishes the move.
    """
    from personalclaw.config.credentials import find_credential, save_credentials

    home = _config_dir()
    path = home / CREDENTIALS_FILE
    if not path.is_file():
        return CredentialsFileMove()
    descriptors = _read_descriptors(path)
    if descriptors is None:
        leftovers = credentials_file_leftovers(home)
        logger.warning("%s is not a JSON object; nothing in it was moved", path)
        return CredentialsFileMove(leftovers=leftovers)

    pending = {
        _target(name): _inline_value(descriptor)
        for name, descriptor in descriptors.items()
        if _movable(name, descriptor) and not find_credential(_target(name), home=home)[0]
    }
    if pending:
        try:
            save_credentials(pending)
        except OSError:
            logger.warning(
                "could not store %s from %s in the credential store",
                ", ".join(sorted(pending)),
                path,
                exc_info=True,
            )
    leftovers = _leftovers(descriptors, home)
    if leftovers:
        logger.warning(
            "kept %s: what it holds for %s is not in the credential store; the Doctor says why",
            path,
            ", ".join(leftover.name for leftover in leftovers),
        )
        return CredentialsFileMove(stored=sorted(pending), leftovers=leftovers)
    path.unlink()
    logger.info(
        "moved %s into the credential store and deleted it; stored: %s",
        path,
        ", ".join(sorted(pending)) or "nothing new",
    )
    return CredentialsFileMove(stored=sorted(pending), removed=True)
