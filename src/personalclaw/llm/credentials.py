"""Credential store: source of truth for resolving named secrets.

The store reads two files under ``<PERSONALCLAW_HOME>``:

* ``credentials.json`` — descriptor map keyed by credential name. Each
  descriptor declares a ``type`` (``api_key`` / ``static_token`` /
  ``oauth2`` / ``none``) and the resolution hint(s) appropriate for
  that kind.
* ``.env`` — legacy ``KEY=VALUE`` fallback. Same parser semantics as
  :func:`personalclaw.config.loader.AppConfigLoader.load_credentials`.

Resolution order for ``api_key`` / ``static_token`` / ``oauth2`` kinds
(Requirement R4.1) is:

1. Environment variable named in ``descriptor.value_env``.
2. Inline ``descriptor.value`` in ``credentials.json``.
3. ``<PERSONALCLAW_HOME>/.env`` keyed by the credential name.
4. Otherwise ``secret=None``, ``source="none"``.

For ``none`` kind, no secret exists; ``source="none"``.

Property 5 (Credential Non-Leakage) requires that :meth:`CredentialStore.list`
NEVER returns a populated ``secret``. The dashboard ``/api/credentials``
endpoint relies on this — it reads ``configured`` and ``source`` from the
returned :class:`Credential` instances and never touches a secret value.

Property 11 (Provider SDK Lazy Import) requires this module to import
only stdlib symbols; no ``httpx``, ``anthropic``, or ``openai`` imports
here.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


CredentialKind = Literal["none", "api_key", "static_token", "oauth2"]
"""Supported credential kinds. The store treats unknown kinds as opaque
descriptors and returns ``secret=None`` / ``source="none"`` from
:meth:`CredentialStore.resolve`; the caller can still inspect the raw
descriptor through :meth:`CredentialStore.list`."""

CredentialSource = Literal["env", "file", "none"]
"""Where a resolved secret came from. Surfaced through the dashboard
``/api/credentials`` endpoint per R4.5; never exposes the secret value
itself."""


_SECRET_BEARING_KINDS: frozenset[str] = frozenset({"api_key", "static_token", "oauth2"})


@dataclass(frozen=True)
class Credential:
    """Resolved credential descriptor.

    ``secret`` is populated only by :meth:`CredentialStore.resolve` for
    secret-bearing kinds when a value was found. :meth:`CredentialStore.list`
    NEVER populates ``secret`` (Requirement R4.4 / Property 5).

    ``source`` reflects which step of the resolution chain produced the
    value:

    * ``"env"`` — the env var named in ``descriptor.value_env`` was set.
    * ``"file"`` — the value came from inline ``descriptor.value`` or
      ``<PERSONALCLAW_HOME>/.env``.
    * ``"none"`` — no value found / kind has no secret.
    """

    name: str
    kind: "CredentialKind"
    secret: str | None = None
    source: "CredentialSource" = "none"


class CredentialStore:
    """Source of truth for credential lookup.

    The store is read-only with respect to descriptors loaded from disk;
    callers mutate state through :meth:`save`, which writes the file
    atomically with mode ``0o600`` (Requirement R4.6) and refreshes the
    in-memory descriptor map.

    Construction reads ``<home>/credentials.json`` and ``<home>/.env``
    if either exists, tightening their permissions to ``0o600`` on read
    when they are looser than ``0o600`` (mirrors the existing pattern in
    :mod:`personalclaw.config.loader`). Missing files are treated as empty.
    """

    CREDENTIALS_FILE = "credentials.json"
    ENV_FILE = ".env"
    FILE_MODE = 0o600

    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._credentials_path = self._home / self.CREDENTIALS_FILE
        self._env_path = self._home / self.ENV_FILE
        self._descriptors: dict[str, dict[str, object]] = {}
        self._env: dict[str, str] = {}
        self.reload()

    # ── Public API ────────────────────────────────────────────────────

    def reload(self) -> None:
        """Re-read ``credentials.json`` and ``.env`` from disk."""
        self._descriptors = self._load_descriptors()
        self._env = self._load_env_file()

    def has(self, name: str) -> bool:
        """Return True iff ``name`` appears in ``credentials.json``."""
        return name in self._descriptors

    def list(self) -> list[Credential]:
        """Return one :class:`Credential` per configured descriptor.

        Secrets are stripped (R4.4 / Property 5): ``secret is None`` for
        every returned entry. ``source`` reflects whether a secret would
        be available if resolved (``"env"``, ``"file"``, or ``"none"``)
        so the dashboard can surface a configured/source summary without
        exposing values.
        """
        out: list[Credential] = []
        for name in self._descriptors:
            resolved = self.resolve(name)
            # Defensive: strip secret regardless of resolve()'s return.
            out.append(
                Credential(
                    name=resolved.name,
                    kind=resolved.kind,
                    secret=None,
                    source=resolved.source,
                )
            )
        return out

    def resolve(self, name: str) -> Credential:
        """Resolve ``name`` to a :class:`Credential`.

        Raises :class:`KeyError` if ``name`` is not in ``credentials.json``
        (Requirement R4.2). The returned ``Credential`` may have
        ``secret=None`` / ``source="none"`` if a secret-bearing
        descriptor has no value configured anywhere in the chain — this
        is not an error condition; the provider factory will surface it
        via :class:`personalclaw.providers.registry.CredentialMissing` when
        it actually needs the value.
        """
        try:
            desc = self._descriptors[name]
        except KeyError as exc:
            raise KeyError(name) from exc

        kind = str(desc.get("type", "none"))

        if kind == "none":
            return Credential(name=name, kind="none", secret=None, source="none")

        if kind not in _SECRET_BEARING_KINDS:
            # Unknown kind — surface no secret, leave kind as configured
            # so callers can still introspect via list().
            logger.warning("credential %r has unknown kind %r; treating as no secret", name, kind)
            return Credential(name=name, kind=kind, secret=None, source="none")  # type: ignore[arg-type]  # noqa: E501

        # Secret-bearing kind — walk the resolution chain.
        # Step 1: env var named in value_env (R4.3 — env beats inline).
        env_var = desc.get("value_env")
        if isinstance(env_var, str) and env_var:
            env_val = os.environ.get(env_var)
            if env_val:
                return Credential(
                    name=name,
                    kind=kind,  # type: ignore[arg-type]
                    secret=env_val,
                    source="env",
                )

        # Step 2: inline value in credentials.json.
        inline = desc.get("value")
        if isinstance(inline, str) and inline:
            return Credential(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                secret=inline,
                source="file",
            )

        # Step 3: <PERSONALCLAW_HOME>/.env keyed by the credential name.
        env_file_val = self._env.get(name)
        if env_file_val:
            return Credential(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                secret=env_file_val,
                source="file",
            )

        # Step 4: nothing configured.
        return Credential(name=name, kind=kind, secret=None, source="none")  # type: ignore[arg-type]  # noqa: E501

    def save(self, descriptors: dict[str, dict[str, object]]) -> None:
        """Atomically write ``descriptors`` to ``credentials.json``.

        Writes to a sibling ``.tmp`` file, ``chmod`` ``0o600`` on it,
        ``os.replace`` into place, then re-applies ``0o600`` on the
        renamed file (some filesystems reset permissions across rename).
        Updates the in-memory descriptor map on success (R4.6).
        """
        self._home.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(descriptors, indent=2, sort_keys=True) + "\n"

        tmp_path = self._credentials_path.with_suffix(self._credentials_path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp_path, self.FILE_MODE)
        except OSError:
            logger.warning("Cannot chmod %s to 0o600", tmp_path)
        os.replace(tmp_path, self._credentials_path)
        try:
            os.chmod(self._credentials_path, self.FILE_MODE)
        except OSError:
            logger.warning("Cannot chmod %s to 0o600 after rename", self._credentials_path)

        # Take a defensive copy so callers can keep mutating their dict.
        self._descriptors = {k: dict(v) for k, v in descriptors.items()}

    # ── Internal helpers ──────────────────────────────────────────────

    def _load_descriptors(self) -> dict[str, dict[str, object]]:
        """Read ``credentials.json``, tightening permissions on read.

        Returns an empty dict if the file is missing or unreadable.
        Mirrors the permission-tightening behavior in
        :meth:`personalclaw.config.loader.AppConfigLoader.load_credentials`.
        """
        path = self._credentials_path
        if not path.is_file():
            return {}
        self._enforce_perms(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return {}
        if not isinstance(raw, dict):
            logger.warning("%s is not a JSON object; ignoring", path)
            return {}
        out: dict[str, dict[str, object]] = {}
        for name, desc in raw.items():
            if isinstance(desc, dict):
                out[str(name)] = dict(desc)
            else:
                logger.warning("descriptor for %r is not an object; ignoring", name)
        return out

    def _load_env_file(self) -> dict[str, str]:
        """Read ``<home>/.env``, tightening permissions on read.

        Parser semantics mirror
        :meth:`personalclaw.config.loader.AppConfigLoader.load_credentials`:
        ``KEY=VALUE`` per line, blanks and ``#`` comments ignored, no
        quote stripping. The store does NOT export values into
        :data:`os.environ` — that remains the legacy loader's job.
        """
        path = self._env_path
        if not path.is_file():
            return {}
        self._enforce_perms(path)
        out: dict[str, str] = {}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def _enforce_perms(self, path: Path) -> None:
        """If ``path`` is loose (group/world bits set), chmod it to ``0o600``."""
        try:
            mode = path.stat().st_mode
        except OSError:
            return
        if mode & 0o077:
            try:
                os.chmod(path, self.FILE_MODE)
            except OSError:
                logger.warning("Cannot enforce 0o600 permissions on %s", path)
