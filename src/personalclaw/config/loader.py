"""Configuration loader for PersonalClaw.

Config location: ~/.personalclaw/config.json (overridden by PERSONALCLAW_HOME)
Credentials:    ~/.personalclaw/.env (overridden by PERSONALCLAW_HOME)

Supports session timeouts, hook rules, and dashboard port via the config
file. The native in-process loop is the default agent runtime; ACP
(``acp:<cli>``) is the opt-in external-CLI backend.
"""

import json
import logging
import os
import re as _re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from personalclaw.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    DEFAULT_PUSH_TO_TALK_CHORD,
)

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = ".personalclaw"

# Credential keys loaded from .env / environment.
# CRED_SLACK_* name the slack-channel APP's credential keys flowing through the
# generic cred store (.env). They are deliberately defined HERE (the store's
# home, below every other layer) and re-exported on the app-facing SDK surface
# (sdk/channel.py) — gateway and the setup CLI import from here, apps import
# from the SDK, nothing imports from gateway. The names stay SLACK_* because
# they are the literal .env keys users already have; renaming would break
# existing installs for zero architectural gain.
CRED_SLACK_APP_TOKEN = "SLACK_APP_TOKEN"
CRED_SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
CRED_OWNER_ID = "PERSONALCLAW_OWNER_ID"
_CREDENTIAL_KEYS = (CRED_SLACK_APP_TOKEN, CRED_SLACK_BOT_TOKEN, CRED_OWNER_ID)

DEFAULT_SESSION_TIMEOUT = 3600  # 60 min

# Single source of truth for the dashboard/API port. Every other module
# (origin, token_auth, snapshot, state, cli*, schedule_script, …) derives from
# this — never re-hardcode the literal. Runtime override is PERSONALCLAW_PORT.
_DEFAULT_PORT = 10000

# PERSONALCLAW_PORT is validated at CLI entry (cli.py main()).
# By the time loader.py is imported the env var is a valid int or absent.
DASHBOARD_PORT: int = int(os.environ.get("PERSONALCLAW_PORT", _DEFAULT_PORT))


# Cross-platform workspace root for LLM working directories.
# Override: PERSONALCLAW_WORKSPACE env var or ~/.personalclaw/workspace_dir
# Default: ~/workplace/personalclaw-workspace
_WORKSPACE_DIR_NAME = "personalclaw-workspace"


def _workspace_dir_file() -> Path:
    """Return the path to the saved workspace_dir file, respecting PERSONALCLAW_HOME."""
    return config_dir() / "workspace_dir"


def _default_workspace_base() -> Path:
    """Return the platform-specific default base for the workspace."""
    return Path.home() / "workplace"


def workspace_root() -> Path:
    """Return the top-level workspace root for LLM sessions and tasks.

    Resolution order:
    1. ``PERSONALCLAW_WORKSPACE`` env var (used as-is, no subdirectory appended)
    2. Saved path in ``config_dir()/workspace_dir`` (written by ``personalclaw setup``)
    3. Platform default with ``personalclaw-workspace`` subdirectory
    """
    override = os.environ.get("PERSONALCLAW_WORKSPACE")
    if override:
        root = Path(override)
        root.mkdir(parents=True, exist_ok=True)
        return root
    if _workspace_dir_file().is_file():
        try:
            saved = _workspace_dir_file().read_text(encoding="utf-8").strip()
            if saved:
                root = Path(saved)
                root.mkdir(parents=True, exist_ok=True)
                return root
        except OSError:
            pass
    base = _default_workspace_base()
    root = base / _WORKSPACE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _surface_mode_default(value: object) -> str:
    """Coerce the new-def surfacing default, refusing anything but the three declared modes.

    An unknown value reads as `off`, matching `DefMetadata.from_dict`'s per-field rule: a typo must
    not silently START surfacing every newly authored def, which is the direction that spends tokens
    and injects text nobody asked for. One tolerance rule, applied in both places — a second, looser
    one here would let a config typo do what the def-level parser refuses.
    """
    word = str(value or "").strip().lower()
    return word if word in {"off", "passive", "suggest"} else "off"


def _workspace_default_mode(value: object) -> str:
    """Coerce the default run-workspace mode, refusing anything the engine does not implement.

    An unknown value reads as `scratch`, NOT as the declared value and NOT as `in_place`. The
    reason is the same one `workspace.parse_workspace` gives for making an unknown mode fatal in a
    template: the modes differ in exactly the way that matters, and `in_place` is the one where a
    destructive step runs against the user's real tree. A config typo must not be what puts a run
    there. `container` is accepted as a WORD (it is in the enum) but degrades to an isolated
    scratch dir at provisioning time — §4.4 is deferred, and rejecting the word here would make
    the config disagree with the enum.
    """
    word = str(value or "").strip().lower()
    return word if word in {"scratch", "worktree", "in_place", "container"} else "scratch"


def _safe_int(value: object, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


def _safe_choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    """Coerce *value* to one of *allowed* (case/space-insensitive), else *default*.

    The ``_safe_int`` sibling for closed string enums. An off-scale value resolving to the
    shipped default rather than raising keeps one typo from making config.json unloadable —
    and for a security knob the default must be the SAFE end of the scale, which is the
    caller's choice of *default*, never the first listed value.
    """
    try:
        candidate = str(value).strip().lower()
    except Exception:  # noqa: BLE001 — an unstringable value is just an invalid one
        return default
    return candidate if candidate in allowed else default


def _safe_float(value: object, default: float) -> float:
    """Convert *value* to float, returning *default* on failure (the ``_safe_int`` sibling).

    A malformed number in config.json must degrade to the shipped default, not raise — a config
    typo should never make the whole file unloadable.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _compose_voice(voice: str, system_prompt: str) -> str:
    """Prepend an agent's VOICE layer (#42) high-priority to its operating rules.

    WHO the agent is (tone/opinions/persona) goes BEFORE the system prompt so its
    personality survives a long operating-rules prompt. Empty voice → prompt as-is.

    The VOICE framing lives in the prompt system (bundled ``agent-voice-layer``
    snippet); the agent's voice + system_prompt are user-authored values rendered
    into it. Falls back to the inline framing if the prompt system can't resolve."""
    v = (voice or "").strip()
    if not v:
        return system_prompt or ""
    try:
        from personalclaw.prompt_providers.runtime import render_snippet_block

        rendered = render_snippet_block(
            "agent-voice-layer", {"voice": v, "system_prompt": system_prompt or ""}
        )
        if rendered:
            return rendered.rstrip()
    except Exception:
        pass
    return f"[VOICE — speak and decide as this persona]\n{v}\n\n{system_prompt or ''}".rstrip()


OUTBOX_DIR_NAME = "outbox"


def outbox_dir() -> Path:
    """Return the outbox directory for agent-to-user file delivery."""
    d = workspace_root() / OUTBOX_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# Paths we've already ensured exist this process. config_dir() is one of the
# hottest helpers in the codebase (~120 call sites, several per request), and
# each call used to issue a mkdir() syscall even though the directory almost
# always already exists. We still re-read PERSONALCLAW_HOME live every call (so
# tests that monkeypatch the env between calls keep working), but skip the
# mkdir once we've created a given resolved path — turning a per-call syscall
# into a one-time cost per distinct home. Keyed by the resolved path string.
_ensured_dirs: set[str] = set()


def _ensure_dir(p: Path) -> Path:
    """mkdir ``p`` once per process (idempotent, syscall only on first sight)."""
    key = str(p)
    if key not in _ensured_dirs:
        p.mkdir(parents=True, exist_ok=True)
        _ensured_dirs.add(key)
    return p


def config_dir() -> Path:
    override = os.environ.get("PERSONALCLAW_HOME")
    if override:
        p = Path(override).expanduser().resolve()
        # Refuse root or system directories as config home
        if p == Path("/") or p.parts[:2] in (("/", "usr"), ("/", "System"), ("/", "etc")):
            logger.warning("PERSONALCLAW_HOME=%s is a system directory, ignoring", override)
        else:
            return _ensure_dir(p)
    d = Path.home() / CONFIG_DIR_NAME
    return _ensure_dir(d)


def config_path() -> Path:
    return config_dir() / "config.json"


_MEMORY_ROOT_DIR_NAME = "workspace"


def _slug_cwd(cwd: str) -> str:
    """Turn an absolute working-directory path into a stable, fs-safe slug.

    Used to partition memory by working directory. The slug is the realpath
    with separators collapsed to ``_``; very long paths get a short hash
    suffix to stay within filesystem name limits while remaining unique.
    """
    real = os.path.realpath(os.path.expanduser(cwd))
    flat = _re.sub(r"[^A-Za-z0-9._-]+", "_", real).strip("_") or "root"
    if len(flat) > 120:
        import hashlib

        digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
        flat = flat[:107] + "_" + digest
    return flat


def memory_dir_for_cwd(cwd: str | None = None) -> Path:
    """Resolve the filesystem-fallback memory directory for a working dir.

    Memory is partitioned by the session's working directory: every distinct
    cwd gets its own isolated memory under ``~/.personalclaw/workspace/_ext/``.
    An empty/unset cwd maps to a shared ``_default`` partition. This is the
    fallback store used when an agent has no explicit ``memory_store`` provider.
    """
    root = config_dir() / _MEMORY_ROOT_DIR_NAME
    if not cwd:
        return root / "_ext" / "_default"
    return root / "_ext" / _slug_cwd(cwd)


def default_workspace_dir() -> str:
    """Return the default working directory for a new session.

    The default cwd is the agent workspace root (``workspace_root()`` —
    ``PERSONALCLAW_WORKSPACE`` or the platform default), if it exists and is
    not a sensitive path. Returns ``""`` when no safe default is available.
    """
    from personalclaw.security import is_sensitive_path  # circular import

    try:
        root = os.path.realpath(str(workspace_root()))
        if os.path.isdir(root) and not is_sensitive_path(root):
            return root
    except Exception:
        pass
    return ""


def env_path() -> Path:
    return config_dir() / ".env"


# ── Credential backend selector (SECURITY-HARDENING C1) ────────────────────
#
# Two backends sit behind ``save_credential`` / ``get_credential`` /
# ``AppConfig.load_credentials``. Callers never name one:
#
#   ``keychain``  the OS secret service via the OPTIONAL ``keyring`` extra
#                 (macOS Keychain, Linux Secret Service, Windows Credential
#                 Locker). Opt-in with ``PERSONALCLAW_CREDENTIAL_BACKEND=keychain``.
#   ``dotenv``    ``~/.personalclaw/.env`` at mode 0600 — the default, and the
#                 FAIL-CLOSED destination whenever the keychain is unavailable
#                 or errors. There is no third location: a headless box that
#                 asked for a keychain and has none keeps its secrets in that
#                 same 0600 file and is TOLD SO by ``doctor`` — never a new
#                 plaintext file somewhere else, never looser permissions.
#
# The write/read asymmetry is deliberate:
#   * WRITES go to the ACTIVE backend only (``credential_backend()``), falling
#     back to ``.env`` 0600 if the keychain write fails.
#   * READS are the UNION of both stores, keychain preferred, regardless of
#     which backend is active. That is what makes reads backend-transparent:
#     flipping the env var back to ``dotenv`` must not make an already-stored
#     secret vanish, and SECURITY-HARDENING's later ``credentials_to_keychain``
#     migration needs both halves readable while it moves keys across.
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

    Public so the doctor probe can show request *and* outcome side by side without
    re-parsing the env var (and drifting on how a typo is read).
    """
    raw = (os.environ.get(CREDENTIAL_BACKEND_ENV) or "").strip().lower()
    if raw == "keychain":
        return "keychain"
    if raw and raw != "dotenv":
        logger.warning(
            "%s=%r is not a credential backend (keychain|dotenv); using .env",
            CREDENTIAL_BACKEND_ENV,
            raw,
        )
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


def _dotenv_save_credential(key: str, value: str) -> None:
    """Upsert ``KEY=VALUE`` into ``~/.personalclaw/.env`` at mode 0600.

    Preserves other lines and comments. 0600 is the floor this backend exists to
    hold — do not relax it.
    """
    ep = env_path()
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
    ep.write_text("\n".join(lines) + "\n")
    try:
        ep.chmod(0o600)
    except OSError:
        logger.warning("Cannot enforce permissions on %s", ep)


def _dotenv_credentials() -> dict[str, str]:
    """Parse ``~/.personalclaw/.env`` into a dict, repairing loose permissions."""
    creds: dict[str, str] = {}
    ep = env_path()
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


def resolve_agent_config_path() -> Path:
    """Return defaults.json, preferring project-dir override for development.

    All modules that need the agent config path should call this instead
    of reimplementing the resolution chain.
    """
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if proj:
        p = Path(proj) / "agents" / "defaults.json"
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "defaults.json"


def _meta(label: str, help: str, **kwargs: object) -> dict:
    """Helper to build field metadata dicts with safe defaults."""
    return {"label": label, "help": help, **kwargs}


def _slug_username(value: object) -> str:
    """Normalize ``dashboard.username`` on load (TEAM-SHARED-ENTITIES §1).

    Imported lazily so the config loader keeps no module-level dependency on
    anything that might import it back, and degrades to "" rather than raising —
    an unreadable handle must not stop the whole config from loading.
    """
    try:
        from personalclaw.identity import slugify_username

        return slugify_username(str(value or ""))
    except Exception:
        return ""


# Guard-flag spellings that DISABLE a guard; anything else (missing/unknown/typo)
# stays ENABLED. Mirrors ``guardrails.flags.guard_flag`` but is defined locally to
# keep the config loader free of a guardrails import (avoids an import cycle).
_GUARD_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def _voice_phrases(value: object) -> list[str]:
    """Normalize a voice phrase list: strings only, trimmed, blanks dropped."""

    if not isinstance(value, list):
        return []
    return [p.strip() for p in value if isinstance(p, str) and p.strip()]


def _guard_flag(value: object) -> bool:
    """Parse a guard-class flag fail-safe: missing/unknown ⇒ ``True`` (enabled).

    Only an explicit bool ``False``, ``0``, or a known falsy token disables. See the
    §5 fail-safe tenet — a guard's ambiguity must fail ON.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _GUARD_FALSE
    return True


# Exposure-flag spellings that ENABLE a surface. The inverse polarity of
# ``_GUARD_FALSE``: for anything that opens an attack surface, ambiguity must fail
# OFF, so ONLY these exact spellings turn it on. `bool("false")` is True in Python,
# which is precisely the trap this avoids.
_EXPOSE_TRUE = frozenset({"1", "true", "yes", "on", "enable", "enabled", "y", "t"})


def _expose_flag(value: object) -> bool:
    """Parse an exposure flag fail-CLOSED: missing/unknown/garbage ⇒ ``False``.

    Use for any flag whose ``True`` opens a network surface or widens access. The
    mirror of :func:`_guard_flag`, which fails ON because a guard's ambiguity must
    keep protecting.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _EXPOSE_TRUE
    return False


# The readable-vault modes (MEMORY-GRAPH-AND-VAULT §5.1). Declared here, next to the
# field, and imported by `memory_vault` + the memory settings handler so the three
# readers cannot drift into three spellings of "two-way".
MEMORY_VAULT_MODES = ("off", "mirror", "two_way")


def _vault_mode(memory_data: dict) -> str:
    """Resolve ``memory.vault_mode``, back-reading the retired ``vault_enabled`` flag.

    ``vault_enabled: true`` was the whole vault control before §5.1 split it three ways,
    so an existing install that turned the mirror on must keep it on across the upgrade —
    the same back-read shape as ``conductor_skill`` → ``orchestrator_skill``. The legacy
    flag maps to ``mirror``, never to ``two_way``: reading a user's files back into memory
    is a new capability and must be chosen, not inherited.

    An unrecognized ``vault_mode`` falls back to the legacy read rather than to ``off``,
    so a typo in a hand-edited config cannot silently stop mirroring a vault the user is
    already browsing.
    """
    raw = str(memory_data.get("vault_mode", "") or "").strip().lower()
    if raw in MEMORY_VAULT_MODES:
        return raw
    return "mirror" if bool(memory_data.get("vault_enabled", False)) else "off"


_BOT_NAME_MAX = 50
_BOT_NAME_RE = _re.compile(r"[^a-zA-Z0-9 _\-.]")


def _sanitize_bot_name(raw: str) -> str:
    """Sanitize bot_name: strip markdown, braces, limit length."""
    if not isinstance(raw, str):
        return ""
    name = raw.strip()[:_BOT_NAME_MAX]
    name = name.replace("{", "").replace("}", "")
    return _BOT_NAME_RE.sub("", name)


@dataclass
class AgentConfig:
    approval_mode: str = field(
        default="auto",
        metadata=_meta(
            "Approval Mode",
            "Tool approval mode. 'trust_reads' auto-approves read-only tools "
            "and asks for everything else.",
            enum=["auto", "interactive", "trust_reads"],
        ),
    )
    provider: str = field(
        default="native",
        metadata=_meta(
            "Provider",
            "Default agent runtime backend for agents that don't set their own: "
            "'native' (in-process loop, governed by Settings → Models), 'acp' "
            "(external CLI), or 'acp:<cli>' to pin a specific connected runtime "
            "(e.g. 'acp:claude-code'). Per-agent 'provider' overrides this. Not a "
            "closed enum — the acp:<cli> space is open over connected runtimes, "
            "mirroring the per-agent AgentProfile.provider field.",
        ),
    )
    sandbox: str = field(
        default="auto",
        metadata=_meta("Sandbox", "Sandbox mode for ACP provider.", enum=["auto", "off"]),
    )
    yolo: bool = field(
        default=False,
        metadata=_meta("YOLO Mode", "Skip tool approval confirmations."),
    )
    acp_concurrent_sessions: bool = field(
        default=False,
        metadata=_meta(
            "ACP Concurrent Sessions",
            "Run multiple ACP chat sessions on ONE backend process (multiplexing) "
            "instead of one process per session — for backends that support session "
            "interleaving. Off by default; the per-backend capability gate must also "
            "allow it.",
        ),
    )
    prompt_cache_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Prompt Caching",
            "Ask providers that support it to cache the stable prompt prefix. Reduces "
            "cost and latency on multi-turn work. No effect on providers without cache "
            "support. Turn it off to rule caching out when debugging a provider — the "
            "served prompt's ordering is unchanged either way.",
        ),
    )
    bot_name: str = field(
        default="",
        metadata=_meta(
            "Bot Name",
            "Custom name the bot identifies as in conversations. Leave empty for default.",
        ),
    )
    orchestrator_skill: bool = field(
        default=False,
        metadata=_meta(
            "Orchestrator Skill",
            "Enable agent delegation — loads the orchestrator skill with the agent roster.",
        ),
    )
    max_subagents: int = field(
        default=3,
        metadata=_meta(
            "Max SubAgents",
            "Maximum concurrent subagents. 0 = auto-size from host CPU + memory.",
        ),
    )
    spawn_min_memory_gb: float = field(
        default=4.0,
        metadata=_meta(
            "Spawn Min Memory GB",
            "Minimum available memory (GB) required to spawn a subagent. 0 disables the check.",
        ),
    )
    subagent_max_turns: int = field(
        default=100,
        metadata=_meta("SubAgent Max Turns", "Default tool-call budget per subagent."),
    )
    subagent_timeout_secs: int = field(
        default=1800,
        metadata=_meta(
            "SubAgent Timeout (seconds)",
            "Wall-clock timeout per subagent execution. 0 uses hardcoded default (1800s).",
        ),
    )
    subagent_cwd_allowed_roots: list[str] = field(
        default_factory=lambda: ["~/workspace", "~/workplace"],
        metadata=_meta(
            "SubAgent CWD Allowed Roots",
            "Directory roots under which subagent_run's cwd parameter is permitted. "
            "Values support ~ expansion. Empty list disables cwd overrides.",
        ),
    )
    log_level: str = field(
        default="WARNING",
        metadata=_meta(
            "Log Level",
            "Persistent log level for the backend logger. "
            "Applied at startup; overridden by --verbose CLI flag.",
            enum=["DEBUG", "INFO", "WARNING", "ERROR"],
        ),
    )
    soft_stop_budget_secs: float = field(
        default=10.0,
        metadata=_meta(
            "Soft-Stop Budget",
            "Seconds to wait for cooperative cancel before hard-killing the session.",
        ),
    )
    unattended_requires_verified_adapter: bool = field(
        default=False,
        metadata=_meta(
            "Unattended Requires Verified Adapter",
            "Refuse an UNATTENDED spawn — a cron fire, a loop-cycle worker, a "
            "subagent, the background/heartbeat session, an inbox or side sweep, a "
            "channel delivery, or a trigger dispatch — onto an "
            "external agent runner whose ACP adapter has no verified provenance: an "
            "`npx -y` fetch-at-launch, an adapter that changed since it was "
            "provisioned, or a runner with no catalog row. Interactive chat is never "
            "gated: a human is present to see what launched. Off by default; turn it "
            "on to require that background work only ever runs a proven adapter.",
        ),
    )
    runner_health_check_secs: int = field(
        default=3600,
        metadata=_meta(
            "Runner Health Check Interval",
            "How long a runner's measured health evidence stays current, in seconds. "
            "Past this, Settings → Agents marks the row's check overdue instead of "
            "presenting an old reading as the present state. Probing is never "
            "automatic — this only decides when a stored measurement stops counting "
            "as an answer.",
        ),
    )

    def __post_init__(self) -> None:
        # Clamp to [0.5, 60.0] to match ``AppConfig.load()`` behavior
        # (dashboard PATCH and YAML loader both clamp rather than raise).
        clamped = max(0.5, min(60.0, float(self.soft_stop_budget_secs)))
        if clamped != self.soft_stop_budget_secs:
            logger.warning(
                "soft_stop_budget_secs=%s out of range [0.5, 60.0]; clamped to %s",
                self.soft_stop_budget_secs,
                clamped,
            )
            self.soft_stop_budget_secs = clamped


@dataclass
class SessionConfig:
    timeout_secs: int = field(
        default=DEFAULT_SESSION_TIMEOUT,
        metadata=_meta("Session Timeout", "Idle session timeout in seconds."),
    )
    autocompact_pct: float = field(
        default=90.0,
        metadata=_meta(
            "Auto-Compact Threshold",
            "Context usage percentage at which auto-compaction triggers (5-90).",
        ),
    )
    pool_size: int = field(
        default=0,
        metadata=_meta(
            "Warm Pool Size",
            "Number of pre-spawned ACP agent processes kept ready for instant "
            "session start. 0 disables. Only useful for ACP agents (subprocess "
            "spawn is the cost); the native runtime starts in-process with no "
            "subprocess, so the pool is unnecessary for native agents.",
        ),
    )
    pool_agent: str = field(
        default="",
        metadata=_meta(
            "Warm Pool Agent",
            "Agent name for warm pool processes. Empty string uses default_agent.",
        ),
    )
    pool_ttl_secs: int = field(
        default=1800,
        metadata=_meta(
            "Warm Pool TTL",
            "Max age in seconds for pooled processes. Stale processes are discarded at claim time. 0 disables.",  # noqa: E501
        ),
    )
    auto_archive_days: int = field(
        default=30,
        metadata=_meta(
            "Auto-Archive After",
            "Days of inactivity after which a conversation moves to Archived. "
            "Archived chats leave the active list but stay fully searchable and can "
            "be restored at any time — nothing is deleted. 0 turns auto-archive off. "
            "Pin a chat with 'never archive' to exempt it.",
        ),
    )


@dataclass
class AmbientConfig:
    """Ambient-surfaces settings (AMBIENT-SURFACES — the composable home).

    The knobs the dashboard-as-views registry, the generative-UI layer, the
    layered-surface overlay, and the menu-bar companion read. All default to the
    conservative shipped behavior so an untouched install is exactly today's
    dashboard: ``tiles_enabled`` gates the composable home, ``max_tiles`` caps a
    view's pinned tiles, ``default_refresh_ttl_secs`` is the pre-substrate tile
    refresh cadence, ``genui_enabled`` gates the generative-UI renderer,
    ``surfaces_max_layer`` is the safe-mode ceiling (0 = pure-L0), and
    ``tray_enabled`` gates the macOS tray companion.
    """

    tiles_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Composable home",
            "Enable the composable home — pin saved artifacts as self-refreshing "
            "dashboard tiles. Off leaves the dashboard as its fixed default layout.",
        ),
    )
    max_tiles: int = field(
        default=12,
        metadata=_meta(
            "Max tiles per view",
            "Cap on how many artifact tiles a single view can hold — an unbounded "
            "home is an unreadable one.",
        ),
    )
    default_refresh_ttl_secs: int = field(
        default=900,
        metadata=_meta(
            "Default tile refresh (seconds)",
            "How often a TTL-mode tile re-runs its bound data workflow (the "
            "pre-substrate refresh cadence). A view-trigger binding overrides this.",
        ),
    )
    genui_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Generative UI",
            "Enable the generative-UI layer — agent-authored widgets render through "
            "the typed component registry alongside markdown.",
        ),
    )
    surfaces_max_layer: int = field(
        default=2,
        metadata=_meta(
            "Surface layers",
            "The layered-surface ceiling (0 = pure launcher, 1 = + tiles, 2 = full). "
            "The safe-mode knob — force 0 to disable the ambient surface overlay.",
        ),
    )
    tray_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Menu-bar companion",
            "Enable the macOS menu-bar tray companion (a thin client app over the "
            "existing gateway APIs). Off by default; macOS only.",
        ),
    )


@dataclass
class CompanionConfig:
    """Companion-app settings (COMPANION-APPS — native clients over the gateway).

    The gateway can advertise itself on the local network so a companion client
    (phone/desktop) can find and pair with it without a typed URL. ``discovery_enabled``
    gates that advertisement — OFF by default, because announcing a service on the LAN is
    a posture choice the user must opt into, not a silent behavior. ``instance_name`` is
    the friendly label a client shows for this gateway; empty means the advertiser (CA-5)
    falls back to the machine hostname.
    """

    discovery_enabled: bool = field(
        default=False,
        metadata=_meta(
            "LAN discovery",
            "Advertise this gateway on the local network so companion apps can find it "
            "without a typed URL. Off by default — announcing a service on your LAN is an "
            "opt-in.",
        ),
    )
    instance_name: str = field(
        default="",
        metadata=_meta(
            "Instance name",
            "Friendly name companion apps show for this gateway. Empty falls back to the "
            "machine hostname.",
        ),
    )


@dataclass
class LocalModelsConfig:
    """Local model-manager knobs (LOCAL-MODEL-MANAGER-V2 §9).

    ``pressure_warn_pct`` is where the loaded-models widget's memory bar starts warning —
    a threshold, not a limit: nothing is ever blocked or unloaded automatically, because
    on a single-user machine the person watching the bar is the one who gets to decide
    what to close. ``sidecar_restart_max`` bounds how many times in a row a crashed
    sidecar child is respawned before the runner stops trying; without a bound, a provider
    whose venv is genuinely broken becomes a respawn busy-loop instead of one honest
    error.
    """

    pressure_warn_pct: int = field(
        default=85,
        metadata=_meta(
            "Memory pressure warning",
            "Percent of system RAM in use at which the loaded-models bar warns. Advisory "
            "only — nothing is unloaded for you.",
        ),
    )
    sidecar_restart_max: int = field(
        default=3,
        metadata=_meta(
            "Sidecar restart limit",
            "How many times in a row a crashed model sidecar is respawned before the "
            "runner gives up and reports the failure instead.",
        ),
    )


@dataclass
class SourcesConfig:
    """Watched-source engine settings (WATCHED-SOURCES §Plug-in Map, SC#12).

    The knobs the :class:`~personalclaw.knowledge.source_engine.SourceEngine` reads each
    tick. Defaults keep an untouched install conservative: polling on, a one-hour default
    interval, a 15-minute network floor (the R1-class rate discipline — a source polls
    someone else's server, so a too-frequent poll is abusive and scraper-like), and modest
    per-poll caps. ``enabled`` is the master switch — off parks the loop so no source is
    ever fetched.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Watched sources",
            "Enable the watched-source poll engine — feeds, pages and directories you "
            "add are polled on their schedule. Off parks the loop; nothing is fetched.",
        ),
    )
    poll_interval_default_secs: int = field(
        default=3600,
        metadata=_meta(
            "Default poll interval (seconds)",
            "How often a source is polled when it does not set its own interval. Clamped "
            "up to the network floor below.",
        ),
    )
    network_floor_secs: int = field(
        default=900,
        metadata=_meta(
            "Network poll floor (seconds)",
            "The fastest any network source is polled, regardless of its own setting. A "
            "too-frequent poll is abusive to the target server and looks like a scraper — "
            "this is the rate floor that prevents it.",
        ),
    )
    max_sources: int = field(
        default=100,
        metadata=_meta(
            "Max active sources",
            "Cap on how many enabled sources the engine arms per tick. A runaway config "
            "cannot schedule unbounded polling.",
        ),
    )
    max_items_per_poll: int = field(
        default=50,
        metadata=_meta(
            "Max items per poll",
            "How many new items one poll may ingest before the rest wait for the next "
            "cycle — a burst of back-fill cannot flood the ingestion queue in one tick.",
        ),
    )
    daily_request_budget: int = field(
        default=288,
        metadata=_meta(
            "Daily request budget per source",
            "Upper bound on network requests one source may make in a rolling day. Without "
            "it, a handful of short-interval watches is thousands of daily requests at a "
            "third party from a machine left running (enforced by the fetching providers).",
        ),
    )


@dataclass
class AppsConfig:
    """App Store settings that are not per-app (ECOSYSTEM-TOOLING T2.2).

    Today one knob: whether the curated app registry ships as a default git source. It is a
    *seed* switch, not a live filter — on first start with it on, the registry URL is written
    into ``apps/app-sources.json`` as an ordinary, removable row (see
    :func:`personalclaw.apps.catalog.seed_default_git_sources`). Turning it off later does not
    retract an already-seeded row (remove it in the Store, which persists); turning it on later
    seeds on the next start. A source only ever contributes Store LISTINGS — the scanner-gated
    install path is unchanged, so nothing from the registry runs without per-app consent.
    """

    registry_source_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Registry app source",
            "Ship the curated app registry as a default source in the Store, so community "
            "apps are discoverable out of the box. Seeded once as a removable source; off "
            "means it is never added. Listing only — installing still runs the scanner.",
        ),
    )


@dataclass
class SkillCatalogConfig:
    """One external skill-catalog source (AGENT-PACKS §6, the ``packs.skill_catalogs`` list).

    A catalog is a named index of installable skills (a GitHub "tap" repo, a
    ``/.well-known/skills/index.json`` site). AP-6 registers each as a
    :class:`CatalogMarketplace` on the shared skills registry at COMMUNITY tier and installs
    through the same ``install_guarded`` chokepoint. AP-3 only wires the config surface; the
    ``list[dataclass]`` precedent is :class:`ProjectionRuleConfig`, so each element field
    carries ``_meta`` for the schema-reachability tests.
    """

    name: str = field(
        default="",
        metadata=_meta("Catalog name", "A short label for this skill catalog."),
    )
    url: str = field(
        default="",
        metadata=_meta(
            "Catalog URL",
            "The catalog's index endpoint or repo URL. Fetched under the CONNECTOR egress "
            "profile when the catalog is browsed (AP-6); never spawned or executed.",
        ),
    )
    kind: str = field(
        default="index",
        metadata=_meta(
            "Catalog kind",
            "How the URL is read: 'index' (a JSON skill index) or 'tap' (a git repo of "
            "skills/<slug>/SKILL.md).",
        ),
    )


@dataclass
class PacksConfig:
    """Portable-pack + skill-catalog + connector-catalog settings (AGENT-PACKS §8).

    The knobs the pack importer and the (later) catalog importer + fingerprint scanner read.
    ``skill_catalogs`` is the AP-6 list of external skill-catalog sources (each a
    :class:`SkillCatalogConfig`); ``fingerprint_enabled`` is the AP-7 project-fingerprint
    master switch (guard-flag-safe: a missing/garbage value stays ON so the propose-only
    surface is never silently disabled); ``connector_catalog_url`` is the optional published
    URL the seeded ``connector_catalog.json`` refreshes from. Defaults keep an untouched
    install conservative — no catalogs configured, fingerprinting on (it only ever
    *proposes*), no remote catalog refresh.
    """

    skill_catalogs: list[SkillCatalogConfig] = field(
        default_factory=list,
        metadata=_meta(
            "Skill catalogs",
            "External skill-catalog sources browsed + installed through the guarded skills "
            "chokepoint (AP-6). Empty by default.",
        ),
    )
    fingerprint_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Project fingerprinting",
            "Let the zero-LLM fingerprint scanner PROPOSE matching packs for a project "
            "(AP-7). It only ever proposes — never auto-installs. Off stops scanning.",
        ),
    )
    connector_catalog_url: str = field(
        default="",
        metadata=_meta(
            "Connector catalog URL",
            "Optional published URL the local connector catalog refreshes from (fetched "
            "under the CONNECTOR egress profile). Empty keeps the seeded bundled set only.",
        ),
    )


@dataclass
class LegibilityConfig:
    """Platform-legibility features (Platform-Legibility §5-§7).

    Two independent, user-facing toggles. ``discover_tips`` gates the dashboard
    "Discover" section and the Discover hub (§6) — a curated, propose-don't-write
    tour of the system that never enables anything on its own. ``context_adapters``
    gates writing routed-context adapter files (CLAUDE.md/AGENTS.md/.cursorrules)
    into an opted-in project's bound workspace (§7) — off by default because it
    writes into user project dirs.
    """

    discover_tips: bool = field(
        default=True,
        metadata=_meta(
            "Discover tips",
            "Show the Discover section on the dashboard and the Discover hub — a "
            "curated tour of the parts of PersonalClaw you haven't tried yet, each a "
            "deep link into the feature. It only points; it never enables anything.",
        ),
    )
    context_adapters: bool = field(
        default=False,
        metadata=_meta(
            "Context Adapters",
            "When on, PersonalClaw renders routed-context adapter files "
            "(CLAUDE.md / AGENTS.md / .cursorrules) into each opted-in project's "
            "bound workspace directory, fenced by PCLAW markers. Off by default — "
            "it writes files into your project directories.",
        ),
    )


def _judge_axis(value: object) -> str:
    """Normalize ``loops.judge_use_case`` on load (WF2LOO-17).

    Fail-SAFE, not fail-open: an unknown/blank axis collapses to ``reasoning``
    rather than to the worker's ``loops`` axis, because the failure mode we are
    closing is exactly "the judge silently grades on the worker's binding". A
    typo in config.json must not re-create it. Imported lazily so the loader
    keeps no module-level dependency on the provider package.
    """
    axis = str(value or "").strip()
    if not axis:
        return "reasoning"
    try:
        from personalclaw.providers.use_cases import VALID_USE_CASES
    except Exception:
        return axis
    return axis if axis in VALID_USE_CASES else "reasoning"


def _stagnation_window(value: object) -> int:
    """Normalize ``loops.stagnation_window`` on load (WF2LOO-18).

    Clamped to [2, 50] and fail-safe to the default: a garbage or missing value keeps the
    stall detector at its shipped sensitivity rather than disabling it. The floor of 2 is
    structural — the two worker-independent signals compare findings BETWEEN cycles, so a
    window of 1 could only ever compare a cycle with itself and would detect nothing.
    """
    try:
        window = int(str(value).strip())
    except (TypeError, ValueError):
        return 5
    return max(2, min(50, window))


@dataclass
class LoopsConfig:
    """Settings for autonomous goal loops (the unified autonomous goal engine)."""

    max_cycles_hard_cap: int = field(
        default=100,
        metadata=_meta(
            "Max Cycles Hard Cap",
            "Absolute ceiling on a loop's cycle budget, regardless of the "
            "per-loop limit. Safety brake against runaway cost.",
        ),
    )
    default_idle_secs: int = field(
        default=120,
        metadata=_meta(
            "Default Idle Seconds",
            "Default seconds between worker cycles (the autonudge idle timer) "
            "when a loop does not specify its own.",
        ),
    )
    trust_ttl_secs: int = field(
        default=24 * 3600,
        metadata=_meta(
            "Trust TTL Seconds",
            "How long a loop's worker keeps auto-approved tool trust before "
            "the supervisor expires it and requires re-authorization.",
        ),
    )
    judge_use_case: str = field(
        default="reasoning",
        metadata=_meta(
            "Judge Model Axis",
            "Which model axis the loop JUDGE rides — deliberately not the "
            "`loops` axis the worker rides. Default `reasoning`: the judge that "
            "certifies done-ness gets its own (typically stronger) binding, so a "
            "reviewer mistake is not correlated with the mistake it is reviewing. "
            "Set to `loops` to put judge and worker back on one binding.",
        ),
    )
    stagnation_window: int = field(
        default=5,
        metadata=_meta(
            "Stagnation Window",
            "How many consecutive cycles of no progress stall a loop. The "
            "supervisor compares the last N findings: byte-identical cycle "
            "reports, an identical set of sources checked, or N cycles reporting "
            "no new findings all mean the loop is spinning. Lower is more "
            "trigger-happy; higher spends more cycles before asking for "
            "direction. Minimum 2 — a window of 1 can only compare a cycle "
            "with itself.",
        ),
    )
    check_work_stages: bool = field(
        default=False,
        metadata=_meta(
            "Check Work After Stage Gates",
            "After an SDLC stage's gate passes, re-derive 2-4 executable checks from "
            "what the stage CLAIMED and run them (the `check-work` skill's module). "
            "Catches the 'gate command passed but the claim was broader than the "
            "command' case — e.g. a deliverable file the stage said it wrote but "
            "didn't. Off by default: it adds a filesystem pass per stage advance.",
        ),
    )
    worktree_sparse: bool = field(
        default=True,
        metadata=_meta(
            "Sparse Task Worktrees",
            "When a parallel task's plan names the files it will touch, hydrate only "
            "those directories in its git worktree instead of the whole repo. On a "
            "large codebase this is most of a worktree's setup cost. Safe by "
            "construction: a task that writes outside its stated scope widens its own "
            "worktree automatically, a task with no usable scope gets a full checkout, "
            "and the merged result is identical either way. Turn off to always hydrate "
            "the full repo.",
        ),
    )


@dataclass
class MemoryConfig:
    semantic_confidence_threshold: float = field(
        default=0.8,
        metadata=_meta(
            "Semantic Confidence Threshold",
            "Minimum similarity score for semantic search results.",
        ),
    )
    episodic_dedup_threshold: float = field(
        default=0.88,
        metadata=_meta(
            "Episodic Dedup Threshold",
            "Similarity threshold for deduplicating episodic memories.",
        ),
    )
    episodic_max_results: int = field(
        default=8,
        metadata=_meta("Episodic Max Results", "Maximum episodic memory results per query."),
    )
    episodic_max_count: int = field(
        default=10_000,
        metadata=_meta("Episodic Max Count", "Maximum total episodic memories stored."),
    )
    semantic_keys: list[str] = field(
        default_factory=list,
        metadata=_meta("Semantic Keys", "Keys to index for semantic search."),
    )
    l1_manifest: bool = field(
        default=True,
        metadata=_meta(
            "L1 Memory Manifest",
            "Inject only a small always-on manifest of your most-recalled facts; "
            "the agent pulls deeper memory on demand via the memory_recall tool. "
            "Off = inject full semantic + episodic memory every turn (legacy).",
        ),
    )
    active_recall: bool = field(
        default=True,
        metadata=_meta(
            "Active Recall",
            "On an interactive turn, surface query-relevant memory just before the "
            "reply (grounding it at the natural moment) — bounded by a timeout + "
            "circuit breaker. Skipped for temporary/incognito/headless turns.",
        ),
    )
    proactive_commitments: bool = field(
        default=False,
        metadata=_meta(
            "Proactive Check-ins (experimental)",
            "Let the agent infer future check-ins from conversation ('you said the "
            "migration ships Friday — I'll check Monday') and deliver ONE natural "
            "reminder per window via the heartbeat. OFF by default: a wrong check-in "
            "is intrusive, so this is opt-in. High-confidence only; capped per day; "
            "scoped to the exact agent + channel; one-tap dismiss.",
        ),
    )
    proactive_commitments_max_per_day: int = field(
        default=3,
        metadata=_meta(
            "Proactive Check-ins — Daily Cap",
            "Hard maximum active proactive check-ins per agent per day.",
        ),
    )
    active_recall_timeout_ms: int = field(
        default=1500,
        metadata=_meta(
            "Active Recall Timeout (ms)",
            "Hard budget for the pre-reply recall step; on timeout the turn "
            "proceeds without it (and the circuit breaker trips after repeats).",
        ),
    )
    auto_promote_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Auto-Promote Memory",
            "Periodically promote repeated episodic memories into durable semantic "
            "facts (the self-learning loop), unattended — guarded by a per-run cap "
            "+ a min-interval + single-flight. Off = promotion only via the button.",
        ),
    )
    auto_promote_every_n: int = field(
        default=10,
        metadata=_meta(
            "Auto-Promote Every N Consolidations",
            "Run promotion after every Nth history consolidation (lower = more "
            "frequent). Combined with the min-interval guard.",
        ),
    )
    auto_promote_max_per_run: int = field(
        default=5,
        metadata=_meta(
            "Auto-Promote Max Per Run",
            "Cap on clusters promoted in a single autonomous run (anti-runaway).",
        ),
    )
    history_idle_hours: float = field(
        default=3.0,
        metadata=_meta(
            "History Idle Hours",
            "Hours of inactivity before history consolidation.",
        ),
    )
    history_max_days: int = field(
        default=365,
        metadata=_meta("History Max Days", "Maximum days of history to retain."),
    )
    migrated: bool = field(
        default=False,
        metadata=_meta("Migrated", "Whether memory has been migrated to vector store."),
    )
    vault_mode: str = field(
        default="off",
        metadata=_meta(
            "Memory Vault (Obsidian)",
            "off = no vault. mirror = write memory out as a browsable markdown vault "
            "(Obsidian-compatible: YAML frontmatter + [[wikilinks]] + graph view), "
            "regenerated from the store and never read back. two_way = also read your "
            "edits back into memory on the next sync — a page you change wins over the "
            "stored value, and a page the sync cannot parse is left untouched and "
            "reported in Memory → Health rather than overwritten.",
        ),
    )
    vault_path: str = field(
        default="memory-vault",
        metadata=_meta(
            "Vault Path",
            "Where the markdown vault is written. Relative paths resolve under "
            "the PersonalClaw config dir (~/.personalclaw); absolute paths are used as-is.",
        ),
    )
    graph_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Entity Graph",
            "Link memories to the people, projects and tools they mention, so "
            "'what do I know about X?' can be answered by following links instead "
            "of hoping similarity search finds everything. Matching is exact-name "
            "and costs no tokens or LLM calls. Off = every graph surface falls back "
            "to today's search behavior.",
        ),
    )
    push_context: bool = field(
        default=False,
        metadata=_meta(
            "Volunteer Related Memory",
            "When a message mentions someone or something the entity graph knows, "
            "offer up to 3 linked memories for that turn — even when they share no "
            "words with what you typed. Costs no tokens or LLM calls beyond the small "
            "block it adds. Off by default because it puts context in front of the "
            "model you didn't ask for; the Health tab reports how often what it "
            "volunteered actually got used.",
        ),
    )
    push_min_confidence: float = field(
        default=0.7,
        metadata=_meta(
            "Volunteer Confidence",
            "How sure the match must be before memory is volunteered. Higher = only "
            "explicit aliases and exact names; lower also admits looser matches "
            "(more offered, more of it irrelevant).",
        ),
    )
    graph_topology_in_context: bool = field(
        default=False,
        metadata=_meta(
            "Topology Orientation Block",
            "At the start of a new session, add a tiny map of the neighbourhoods in "
            "your memory graph ('people around project X', 'the tools cluster') so the "
            "assistant knows which areas exist before it searches. Off by default: it "
            "spends a little context on every new session, and it says nothing useful "
            "until the graph has enough links to form distinct groups.",
        ),
    )
    slot_size_cap: int = field(
        # MUST equal `memory_slots.SLOTS_BLOCK_MAX_CHARS`. Spelled as a literal because
        # config.loader is imported by nearly everything and must not pull the memory
        # subsystem in at import time; `test_memory_slots_config.py` asserts they agree, so
        # the two cannot drift silently.
        default=1400,
        metadata=_meta(
            "Slots Budget (characters)",
            "How much of every session's context the always-injected Slots block may "
            "cost. Slots are the registers read on every session regardless of what you "
            "asked (persona, preferences, pending items…), so this is a spend you pay "
            "constantly: raising it buys the assistant more standing context, lowering it "
            "truncates the block. Clamped to 200-4000 — the per-slot caps that decide "
            "which individual register is full are fixed in code, not here.",
        ),
    )
    holder_attribution: bool = field(
        default=False,
        metadata=_meta(
            "Attribute Claims to Who Said Them",
            "Record WHOSE claim a memory is (you, the assistant, a named person, or an "
            "outside source) instead of storing everything as plain fact, and render it "
            "that way in context ('Alex believes…'). Second-hand claims are capped "
            "lower, and a lower-authority claim can never retire something you said. "
            "Off by default — with it off, every memory is stored unattributed exactly "
            "as before.",
        ),
    )


@dataclass
class DashboardConfig:
    url: str = field(
        default="",
        metadata=_meta(
            "Dashboard URL",
            "Public URL for the dashboard (used in links delivered to external channels).",
        ),
    )
    public_url: str = field(
        default="",
        metadata=_meta(
            "Public URL",
            "Set this ONLY if you reach this dashboard from the internet through a "
            "TLS-terminating tunnel or reverse proxy (e.g. https://pc.example.com). It "
            "hardens the session cookie (Secure), allows wss:// to that host, and is a "
            "precondition for trusting proxy headers. Distinct from Dashboard URL, which "
            "is only used for links.",
        ),
    )
    trusted_proxies: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Trusted Proxies",
            "Addresses or CIDR blocks of the proxy/tunnel in front of this gateway. "
            "X-Forwarded-Proto / X-Forwarded-For are honored ONLY from these peers — "
            "anyone else can forge them. Empty (the default) trusts none.",
        ),
    )
    restore_sessions: bool = field(
        default=False,
        metadata=_meta(
            "Restore Sessions",
            "Re-open recently active sessions on startup.",
        ),
    )
    restore_window_minutes: int = field(
        default=30,
        metadata=_meta(
            "Restore Window Minutes",
            "Time window (minutes) for session restoration (0-1440). 0 = restore all.",
        ),
    )
    user_name: str = field(
        default="",
        metadata=_meta(
            "Operator Name",
            "How the system addresses the operator. Set during first-run onboarding; "
            "instance-level (single-user, self-hosted) so it follows the user across "
            "browsers/machines. Empty = onboarding not yet completed.",
        ),
    )
    username: str = field(
        default="",
        metadata=_meta(
            "Username",
            "Short attribution handle stamped onto records you create (tasks, "
            "comments, memories) — lowercase letters, digits, '-' and '_'. It is a "
            "label, NOT a credential: nothing authenticates or authorizes against "
            "it. Suggested from your operator name at first run. Renaming affects "
            "future writes only; existing records keep the name they were written "
            "with. Empty = writes carry no attribution (the default behavior).",
        ),
    )
    merge_queued_messages: bool = field(
        default=False,
        metadata=_meta(
            "Merge Queued Messages",
            "Concatenate follow-up messages while the agent is busy instead of queueing them separately.",  # noqa: E501
        ),
    )
    auto_tag_sessions: bool = field(
        default=True,
        metadata=_meta(
            "Auto-Tag Sessions",
            "When a chat's title is auto-generated, also propose and assign tags in the "
            "same pass — existing tags where they fit, at most 1-2 new ones otherwise. "
            "Never touches chats you've already tagged, or incognito/temporary chats.",
        ),
    )
    mcp_probe_timeout_secs: int = field(
        default=15,
        metadata=_meta(
            "MCP Probe Timeout",
            "Seconds to wait for MCP server handshake during probe (5-120).",
        ),
    )
    widget_density: str = field(
        default="more",
        metadata=_meta(
            "Widget Density",
            "How aggressively the agent uses inline widgets. "
            "'more' encourages widgets for any visual content; "
            "'less' limits to only when markdown is clearly insufficient.",
            enum=["more", "less"],
        ),
    )
    # Message display preferences. Server-stored (not browser localStorage) so the
    # chat surface behaves identically across the operator's browsers/machines.
    send_on_enter: bool = field(
        default=True,
        metadata=_meta(
            "Send on Enter",
            "Enter sends the message (Shift+Enter for newline). When off, Enter "
            "inserts a newline and Cmd/Ctrl+Enter sends.",
        ),
    )
    show_timestamps: bool = field(
        default=False,
        metadata=_meta("Show Timestamps", "Display a timestamp on each chat message."),
    )
    show_thinking_inline: bool = field(
        default=False,
        metadata=_meta(
            "Show Thinking Inline",
            "Show intermediate reasoning between tool calls instead of collapsing it.",
        ),
    )
    simplified_tool_names: bool = field(
        default=False,
        metadata=_meta(
            "Simplified Tool Names",
            "Inline tool pills show a simplified purpose instead of the exact command.",
        ),
    )
    followup_chips: bool = field(
        default=True,
        metadata=_meta(
            "Follow-up suggestions",
            "After each reply, show 2-3 suggested next messages (one small background model "
            "call; never blocks the turn). Skipped for temporary/incognito chats; silent when "
            "no model is bound.",
        ),
    )
    offer_check_work: bool = field(
        default=True,
        metadata=_meta(
            "Offer 'Check this work'",
            "After a turn that claims a multi-step task is complete (3+ tool calls plus "
            "completion language), offer a 'Check this work' chip beside the follow-up "
            "suggestions. The chip only OFFERS — check-work runs when you click it, "
            "never automatically, so the cost and latency stay yours to spend.",
        ),
    )
    stream_reveal: str = field(
        default="smooth",
        metadata=_meta(
            "Streaming text reveal",
            "smooth: steady word-by-word reveal decoupled from network chunks (never lags). "
            "immediate: render each chunk the instant it arrives.",
            enum=["smooth", "immediate"],
        ),
    )
    confirm_close_session: bool = field(
        default=False,
        metadata=_meta(
            "Confirm Before Closing Session",
            "Ask for confirmation when closing a session from the sidebar.",
        ),
    )
    auto_open_browser: bool = field(
        default=True,
        metadata=_meta(
            "Auto Open Browser",
            "Open the dashboard URL in the default browser on gateway startup.",
        ),
    )
    update_dev_mode: bool = field(
        default=False,
        metadata=_meta(
            "Developer Update Mode",
            "Git checkouts only: update on every new commit on the current branch "
            "instead of only when a new release TAG exists. Off (default) means the "
            "in-app updater rides releases like every other install kind; on is the "
            "contributor 'track main' behavior. No effect on pip/container/desktop "
            "installs (they always update per release).",
        ),
    )
    screen_share_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Screen Sharing in Chat",
            "Master opt-in for the composer's 'Share screen' control (MULTIMODAL-IO "
            "§5). With it on, a chat turn can carry ONE frame of a screen or window "
            "you explicitly picked in the browser's own share dialog: the frame is "
            "held in memory for that single turn, never written to disk, and dropped "
            "the moment it is used. OFF by default — with it off the control is "
            "hidden AND the server refuses any frame, so nothing can share your "
            "screen by asking nicely.",
        ),
    )
    terminal: dict = field(
        default_factory=lambda: {"enabled": True},
        metadata=_meta(
            "Terminal",
            "Terminal panel configuration. Enabled by default (powers the CLI "
            "panel + per-provider Sign-in terminal); set enabled=false to hide.",
        ),
    )
    dashboard_layout: dict = field(
        default_factory=dict,
        metadata=_meta(
            "Dashboard Layout",
            "The home dashboard's customized widget layout ({widgets:[{id,x,y,w,h,"
            "hidden}], v}). Empty = the curated default layout. Persisted per-user "
            "so the home follows the operator across browsers/machines.",
        ),
    )


@dataclass
class AgentProfile:
    provider: str = field(
        default="",
        metadata=_meta(
            "Provider",
            "Agent runtime backend: 'native' (in-process loop, governed by "
            "Settings → Models) or 'acp:<cli>' (external CLI). Empty inherits the "
            "global agent.provider default.",
        ),
    )
    provider_agent: str = field(
        default="",
        metadata=_meta("Provider Agent", "ACP provider agent name (modeId for session/set_mode)."),
    )
    acp_mode: str = field(
        default="",
        metadata=_meta(
            "ACP Mode",
            "ACP permission/operating mode for adapters that expose one "
            "(claude-code/codex: default, acceptEdits, plan, dontAsk, "
            "bypassPermissions; set via session/set_config_option). Distinct from "
            "Approval Mode (the host gate). Empty inherits the adapter default; "
            "ignored by runtimes with no separate mode axis (the default dialect).",
        ),
    )

    default_dir: str = field(
        default="",
        metadata=_meta(
            "Default Directory",
            "Optional working directory this agent opens in. Empty inherits the "
            "workspace root. Overridable per-session.",
        ),
    )
    memory_store: str = field(
        default="",
        metadata=_meta(
            "Memory Store",
            "Optional memory provider for this agent. Empty uses the filesystem "
            "fallback scoped by working directory.",
        ),
    )
    description: str = field(
        default="",
        metadata=_meta("Description", "Human-readable agent description."),
    )
    system_prompt: str = field(
        default="",
        metadata=_meta("System Prompt", "System prompt injected at session start for this agent."),
    )
    voice: str = field(
        default="",
        metadata=_meta(
            "Voice",
            "WHO the agent is — tone, opinions, bluntness, persona — kept separate "
            "from the operating rules (System Prompt) and injected high-priority so "
            "personality survives long prompts.",
        ),
    )
    model: str = field(
        default="",
        metadata=_meta("Model", "Default model for this agent. Overridable per-chat."),
    )
    approval_mode: str = field(
        default="",
        metadata=_meta(
            "Approval Mode", "Tool approval mode: auto, interactive, or empty (inherit global)."
        ),
    )
    skills: list = field(
        default_factory=list,
        metadata=_meta("Skills", "List of skill names loaded for this agent."),
    )
    tools: list = field(
        default_factory=list,
        metadata=_meta("Tools", "List of allowed tool name patterns for this agent."),
    )
    triggers: list = field(
        default_factory=list,
        metadata=_meta(
            "Triggers",
            "Referenced lifecycle-trigger IDs. A lifecycle trigger fires ONLY for "
            "agents that list it here — there is no global firing. Empty = no "
            "triggers for this agent.",
        ),
    )
    source: str = field(
        default="personalclaw",
        metadata=_meta("Source", "Agent origin: personalclaw, marketplace, or builtin."),
    )
    # Agent routing (AGENT-ROUTING S1) — suggest-first specialist routing metadata.
    # Both optional; empty = "not a routing candidate" (opt-in per agent, zero
    # behavior change for existing agents).
    specialty: str = field(
        default="",
        metadata=_meta(
            "Specialty",
            "One line: what this agent is the specialist for. Drives the routing "
            "suggestion's embedding match. Empty = never suggested.",
        ),
    )
    route_hints: str = field(
        default="",
        metadata=_meta(
            "Routing Hints",
            "Comma-separated example utterances / trigger phrases that should route "
            "to this agent (the same authoring vocabulary as workflow match text).",
        ),
    )


@dataclass
class MemoryStoreConfig:
    description: str = field(
        default="",
        metadata=_meta("Description", "Human-readable purpose of this memory store."),
    )


@dataclass
class SkillsConfig:
    max_triggered: int = field(
        default=3,
        metadata=_meta("Max Triggered", "Maximum number of skills to load per message (≥1)."),
    )
    # ── Auto skill creation ──
    # All fields default to OFF so upgrades are zero-impact. Enable via
    # ``personalclaw config set skills.auto_create_from_sessions true`` or the
    # dashboard Settings → Skills panel (future).
    auto_create_from_sessions: bool = field(
        default=False,
        metadata=_meta(
            "Auto-Create Skills",
            "When true, analyze each session after completion and synthesize a reusable "
            "SKILL.md when a non-trivial multi-step procedure is detected. Generated "
            "skills live under skills/auto/ so they never collide with hand-authored "
            "skills. Disabled by default.",
        ),
    )
    auto_refine_on_deviation: bool = field(
        default=False,
        metadata=_meta(
            "Auto-Refine Skills",
            "When true, update an existing auto-created skill if the agent succeeds "
            "via a different tool sequence than documented. Requires "
            "auto_create_from_sessions. Disabled by default.",
        ),
    )
    auto_min_tool_calls: int = field(
        default=5,
        metadata=_meta(
            "Auto Min Tool Calls",
            "Minimum tool calls in a session for it to qualify for skill extraction "
            "(≥2). Lower values produce more skills but reduce quality.",
        ),
    )
    auto_similarity_threshold: float = field(
        default=0.85,
        metadata=_meta(
            "Auto Similarity Threshold",
            "Skip creation when an existing skill's description has keyword overlap "
            "≥ this fraction with the synthesized description (0.0-1.0). Prevents "
            "near-duplicate skills.",
        ),
    )
    progressive_disclosure_threshold: int = field(
        default=8,
        metadata=_meta(
            "Progressive Disclosure Threshold",
            "When more than this many skills match a turn, inject only their compact "
            "INDEX (name + description) and let the agent pull full bodies on demand "
            "via skill_invoke — instead of inlining every matched body. Token "
            "efficiency at scale; 0 disables (always inline). Default 8.",
        ),
    )

    def __post_init__(self) -> None:
        if self.max_triggered < 1:
            logger.warning("max_triggered %d < 1, using 1", self.max_triggered)
            object.__setattr__(self, "max_triggered", 1)
        if self.auto_min_tool_calls < 2:
            logger.warning("auto_min_tool_calls %d < 2, using 2", self.auto_min_tool_calls)
            object.__setattr__(self, "auto_min_tool_calls", 2)
        if not 0.0 <= self.auto_similarity_threshold <= 1.0:
            logger.warning(
                "auto_similarity_threshold %.2f out of range [0.0, 1.0], using 0.85",
                self.auto_similarity_threshold,
            )
            object.__setattr__(self, "auto_similarity_threshold", 0.85)
        if self.auto_refine_on_deviation and not self.auto_create_from_sessions:
            logger.warning(
                "auto_refine_on_deviation requires auto_create_from_sessions; "
                "disabling auto_refine_on_deviation"
            )
            object.__setattr__(self, "auto_refine_on_deviation", False)
        if self.progressive_disclosure_threshold < 0:
            object.__setattr__(self, "progressive_disclosure_threshold", 0)


@dataclass
class LearningConfig:
    """Per-turn self-improvement review (learn-after-turn-review).

    After a learning-worthy turn (a correction signal, or ≥min_tool_calls), a
    bounded background review may persist a memory fact. Distinct from
    consolidation (batched, session-end) — this is continuous + correction-timely.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "After-Turn Learning",
            "Run a quick background review after a learning-worthy turn to capture "
            "user corrections/preferences as durable memory — continuous (vs the "
            "session-end consolidation). Skipped for incognito/temporary sessions.",
        ),
    )
    min_tool_calls: int = field(
        default=4,
        metadata=_meta(
            "Learning Min Tool Calls",
            "A turn with at least this many tool calls qualifies for review even "
            "without a correction signal (substantial work worth learning from).",
        ),
    )
    correction_heuristic: bool = field(
        default=True,
        metadata=_meta(
            "Correction Heuristic",
            "Treat a user message that negates/corrects the prior turn (no, don't, "
            "actually, instead, wrong…) as a first-class learning signal.",
        ),
    )
    surface_chip: bool = field(
        default=True,
        metadata=_meta(
            "Surface Learned Chip",
            "Show a quiet 'Learned: …' chip in chat when something is captured.",
        ),
    )
    skill_ladder: bool = field(
        default=True,
        metadata=_meta(
            "Skill-Ladder Review",
            "On a learning-worthy turn, run a bounded background LLM review that may "
            "PROPOSE a reusable skill (refine an existing one before minting a new "
            "one). Proposals land in the Skill-proposals inbox for your approval — "
            "never installed automatically. Off = memory-only learning.",
        ),
    )
    min_evidence: int = field(
        default=3,
        metadata=_meta(
            "Minimum Evidence",
            "How many separate occurrences a pattern needs before it can be proposed "
            "as durable learning. One is an anecdote and two a coincidence; this same "
            "floor is shared by the promotion ladder, pattern synthesis, and inferred "
            "proposals, so they cannot disagree about what counts as evidence.",
        ),
    )
    staging_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Capture Staging Log",
            "Record every capture pass in an append-only log with an explicit outcome "
            "(produced / nothing-found / error). This is what makes a silently broken "
            "capture path visible — without it, a pass that crashes looks exactly like "
            "a quiet day. Off = capture still runs, but its failures are invisible.",
        ),
    )
    self_model_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learn From What Works",
            "Notice working patterns and OFFER them as behavioral principles. Every other "
            "learning path only sees corrections and failures, so this is the one that can "
            "learn from what quietly succeeds. It never installs anything: a pattern that "
            "recurs and keeps working becomes a proposal you review, capped at a handful of "
            "principles so it cannot grow without displacing one you already accepted.",
        ),
    )
    min_session_score: float = field(
        default=0.0,
        metadata=_meta(
            "Minimum Session Score",
            "Sessions scoring below this (0.0-1.0, weighted toward decisions rather "
            "than raw turn count) are skipped by the session-end consolidation pass. "
            "0 = score every session; raise it to stop paying to learn from thin ones.",
        ),
    )
    context_budget_tokens: int = field(
        default=4000,
        metadata=_meta(
            "Learning Context Budget",
            "Token budget for the ranked learning block (lessons, skills, memory, "
            "retrieved context) injected each turn. Only retrieved context is ever "
            "trimmed — lessons and instructions are never crowded out, and an item "
            "that does not fit is dropped whole rather than cut mid-sentence.",
        ),
    )
    curator_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learning Curator",
            "Age the learned library (skills, templates) on the consolidation cadence: "
            "unused items go stale, then archived. Never deletes, always reversible, and "
            "refuses any pass that would cut more than half the library. Off = the "
            "library grows without grooming.",
        ),
    )
    propose_quota_per_run: int = field(
        default=5,
        metadata=_meta(
            "Proposals Per Run",
            "How many proposals one learning pass may file. A pass that files twenty "
            "is not being thorough, it is being unreadable — and a queue nobody "
            "finishes reading is a queue that stops being read at all.",
        ),
    )
    run_end_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learn From Run Failures",
            "When a workflow run ends, mine its Run Ledger for failed steps and file "
            "lesson proposals (never auto-written) keyed by failure mode, plus a "
            "procedural-outcome record per failed step. Off = terminal runs leave no "
            "learning trace. Only fires when a memory service is available.",
        ),
    )
    attribution_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Grade Accepted Changes",
            "After you accept a proposal, watch its target's Run Ledger. Once enough runs "
            "have accumulated, grade whether the change delivered what it predicted — and if "
            "it only made things worse, file a revert proposal (never auto-applied) that names "
            "what broke. Off = accepted changes are never measured against their promise.",
        ),
    )


@dataclass
class KnowledgeConfig:
    """Knowledge-store semantics (WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS §2.1).

    The knobs here all govern how much a synthesis loop is allowed to write and how long
    what it wrote stays trusted. They are config rather than constants because the right
    answer depends on how the owner uses the store: a research-heavy user wants larger
    reports, and someone tracking fast-moving facts wants shorter default expiry.
    """

    idempotent_persist: bool = field(
        default=True,
        metadata=_meta(
            "Idempotent Knowledge Writes",
            "Resolve a knowledge write by its logical identity (kind + title) and skip it "
            "entirely when the content is unchanged. This is what stops a retried, resumed "
            "or rewound synthesis node from writing a second near-identical article that "
            "later reads as independent corroboration. Off = every persist inserts.",
        ),
    )
    require_citations: bool = field(
        default=True,
        metadata=_meta(
            "Require Citations On Synthesis",
            "Refuse to store a synthesized item (insight, report, overview) with no "
            "citations unless it is explicitly marked unsourced. An unsourced synthesis is "
            "indistinguishable from a confident guess once it is being retrieved as fact.",
        ),
    )
    report_budget_chars: int = field(
        default=40_000,
        metadata=_meta(
            "Report Size Budget",
            "Largest a single `report` knowledge item may be, in characters. Exceeding it "
            "returns a condense-and-retry error rather than failing the run, so the "
            "synthesizing stage can shorten and try again.",
        ),
    )
    default_ttl: str = field(
        default="",
        metadata=_meta(
            "Default Knowledge Expiry",
            "Optional default expiry for newly persisted items (e.g. `30d`, `12h`). Blank "
            "means knowledge does not expire unless a write asks for it. Expiry demotes an "
            "item in retrieval rather than deleting it — a stale fact is still evidence of "
            "what was believed.",
        ),
    )
    max_mentions_per_claim: int = field(
        default=20,
        metadata=_meta(
            "Max Sources Per Claim",
            "How many independent sources a single claim will accumulate before it stops "
            "recording new ones. Confidence saturates long before this; the cap exists so a "
            "high-traffic claim cannot grow its evidence list without bound.",
        ),
    )
    synthesis_window: int = field(
        default=20,
        metadata=_meta(
            "Synthesis Window",
            "How many recent findings a long-running watcher's synthesis stage sees per cycle. "
            "Without a window, cycle 50 carries all 50 cycles of findings and every cycle costs "
            "more than the last — a run that gets slower and more expensive until it hits a "
            "context limit, with nothing indicating why.",
        ),
    )
    lint_every_n_persists: int = field(
        default=12,
        metadata=_meta(
            "Knowledge Lint Cadence",
            "Writes between semantic lint passes. Counted in WRITES rather than hours: a store "
            "nobody added to does not need linting, and a busy week needs it more than once.",
        ),
    )
    embed_batch_size: int = field(
        default=32,
        metadata=_meta(
            "Embedding Batch Size",
            "Chunk texts sent to the embedding provider per call. Higher is faster to import "
            "and cheaper in round trips; if a provider rejects a batch this large, the batch "
            "is split in half automatically rather than failing the import, so a wrong value "
            "costs a few extra calls rather than a broken library.",
        ),
    )
    embed_retry_budget: int = field(
        default=3,
        metadata=_meta(
            "Embedding Retry Budget",
            "Attempts per embedding call before a batch is split (or, for a single chunk, "
            "stored without a vector and logged). Exponential backoff between attempts, so 3 "
            "rides out a rate-limit blip without making a failed import look like a hang.",
        ),
    )
    maintenance_max_staleness_secs: int = field(
        default=900,
        metadata=_meta(
            "Graph Maintenance Staleness Window",
            "How long index work may wait while an import is still running. Graph maintenance "
            "normally waits for the ingest queue to drain, so a bulk import costs ONE pass "
            "instead of one per item — but a pipeline that never drains would starve it "
            "forever, so dirt older than this runs anyway. Lower = fresher graph during long "
            "imports; higher = fewer passes.",
        ),
    )
    consolidate_min_cluster: int = field(
        default=5,
        metadata=_meta(
            "Smallest Consolidation Cluster",
            "Fewest related items worth spending one model call to merge. Below about five, a "
            "summary loses more detail than it saves space.",
        ),
    )
    session_brief_max_tokens: int = field(
        default=800,
        metadata=_meta(
            "Session Brief Budget",
            "Token ceiling for the project digest injected at the start of every workflow run in "
            "a project. Small by default because it is paid on EVERY run — a generous budget "
            "becomes a permanent cost nobody attributes to the right feature. Items are dropped "
            "whole when the budget binds, and the brief says how many it left out.",
        ),
    )
    conflict_model_pass: bool = field(
        default=True,
        metadata=_meta(
            "Semantic Conflict Check",
            "After the free deterministic check, send claims it could not separate to one "
            "fast-model call to look for contradictions. Off leaves only the provable conflicts "
            "flagged — cheaper, and it still catches the numeric and polarity cases.",
        ),
    )
    consolidate_min_hours: int = field(
        default=6,
        metadata=_meta(
            "Hours Between Consolidation Passes",
            "Floor between consolidation sweeps. The pass is expensive and its input barely "
            "changes minute to minute, so a tighter cadence pays repeatedly for the same answer.",
        ),
    )
    auto_ingest_artifacts: bool = field(
        default=True,
        metadata=_meta(
            "Index Artifacts For Search",
            "Mirror text artifacts (markdown, HTML, text, JSON, CSV) into knowledge search so "
            "something you wrote into an artifact is findable from the one place you look for "
            "what you know. Artifacts stay in the Artifacts library — they are never listed as "
            "knowledge items, only found by a search. Indexing is local: a mirrored artifact "
            "never reaches a model. Off stops new artifacts being indexed and removes nothing "
            "already indexed.",
        ),
    )


@dataclass
class EgressConfig:
    """Operator overrides for the outbound egress guard (``personalclaw.net``).

    The guard blocks non-public destinations by default (loopback / RFC-1918 /
    link-local / IMDS / multicast / reserved) on every agent fetch, connector scrape,
    and webhook. These fields let a self-hoster relax that for THEIR environment —
    e.g. a homelab user whose webhook legitimately targets a LAN service — without
    weakening the default. A deny always wins over an allow.
    """

    allow_hosts: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Allowed Egress Hosts",
            "Hosts (bare domain covers subdomains) permitted to be reached even if "
            "they resolve to a private/LAN address. For homelab webhooks/services on "
            "your own network. Applies to all egress surfaces.",
        ),
    )
    deny_hosts: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Denied Egress Hosts",
            "Hosts (bare domain covers subdomains) the agent must never reach, even "
            "if public. A deny always overrides an allow.",
        ),
    )
    allow_private: bool = field(
        default=False,
        metadata=_meta(
            "Allow Private Networks",
            "When true, egress to private/LAN addresses is permitted globally (not "
            "just allow_hosts). Only enable on a fully trusted network — it removes "
            "SSRF protection for the whole LAN.",
        ),
    )


@dataclass
class BudgetConfig:
    """Default spend ceilings for unattended work (AUTONOMY-GUARDRAILS §1.1).

    Zero means UNLIMITED for that dimension — the conservative default so an
    existing user's unattended work is never suddenly capped on upgrade. A
    ceiling bites the ``run`` scope (one goal-loop / cron fire) and the ``day``
    scope (all unattended spend for a calendar day, per the ``spend.json`` meter).
    Per-trigger overrides arrive with AUTOMATION-SUBSTRATE (Trigger.gates); until
    then these globals apply to every unattended run.
    """

    max_tokens_per_run: int = field(
        default=0,
        metadata=_meta(
            "Max Tokens / Run",
            "Token ceiling for a single unattended run (goal-loop cycle, cron fire, "
            "subagent). 0 = unlimited. At the ceiling the run pauses into needs-input.",
        ),
    )
    max_tokens_per_day: int = field(
        default=0,
        metadata=_meta(
            "Max Tokens / Day",
            "Token ceiling for ALL unattended spend in a calendar day (across every "
            "trigger). 0 = unlimited. At the ceiling further unattended runs are "
            "skipped + paused until the next day.",
        ),
    )
    max_dollars_per_day: float = field(
        default=0.0,
        metadata=_meta(
            "Max Dollars / Day",
            "Estimated-dollar ceiling for all unattended spend in a calendar day. "
            "0 = unlimited. Estimates use provider-reported usage where available, "
            "else a conservative heuristic.",
        ),
    )


@dataclass
class BreakerConfig:
    """Per-provider circuit-breaker tuning (AUTONOMY-GUARDRAILS §2.3).

    Consumed by the model-call chokepoint's breaker registry. Defaults match the
    breaker module's built-ins; a value here overrides them for every provider.
    """

    failure_threshold: int = field(
        default=5,
        metadata=_meta(
            "Breaker Failure Threshold",
            "Consecutive failures before a provider's circuit breaker OPENs (fails "
            "fast during an outage instead of stacking timeouts).",
        ),
    )
    recovery_secs: float = field(
        default=30.0,
        metadata=_meta(
            "Breaker Recovery Seconds",
            "How long an OPEN breaker waits before allowing one HALF_OPEN probe.",
        ),
    )


@dataclass
class AutonomyConfig:
    """Earned-autonomy rung ladder thresholds (AUTONOMY-GUARDRAILS §5).

    The evidence bar one action type must clear before ``guardrails/autonomy.py``
    *proposes* its next rung. These are the operator-wide defaults; a type that
    declares its own ``PromotionRule`` on its spec keeps it.

    None of these is guard-class: no threshold here can grant autonomy on its own,
    because promotion is always a user click and the derived record only decides
    whether a proposal is filed. Demotion ignores every threshold except
    ``cooldown_days`` — it is immediate on the first rejection.
    """

    clean_approvals: int = field(
        default=10,
        metadata=_meta(
            "Clean Approvals Required",
            "How many times you must approve an action type unchanged before "
            "PersonalClaw offers to let it run at the next rung.",
        ),
    )
    min_days: int = field(
        default=7,
        metadata=_meta(
            "Minimum Track-Record Days",
            "The approvals must be spread over at least this many days — ten "
            "approvals in one afternoon is not a track record.",
        ),
    )
    max_rejections: int = field(
        default=0,
        metadata=_meta(
            "Rejections Tolerated",
            "How many rejections, undos or 👎 an action type may have in the "
            "evidence window and still be offered a promotion. 0 means none.",
        ),
    )
    cooldown_days: int = field(
        default=14,
        metadata=_meta(
            "Demotion Cooldown (days)",
            "After an action type is demoted it cannot be offered a promotion "
            "again for this many days.",
        ),
    )
    evidence_window_days: int = field(
        default=30,
        metadata=_meta(
            "Evidence Window (days)",
            "How far back the derived track record looks. Nothing older counts, "
            "so a type has to keep earning its rung.",
        ),
    )


@dataclass
class GuardrailsConfig:
    """The personal safety-floor substrate (AUTONOMY-GUARDRAILS).

    A *personal* safety floor — one user, one gateway, config plus one policy
    check per seam. Session 1 shipped the model-call chokepoint (breaker + hard
    timeout + audit + typed output); Session 2 adds spend metering + the outbound
    scan mode. Later sessions add the denylist, incident kill switch, and named
    safety profiles.
    """

    budgets: BudgetConfig = field(
        default_factory=BudgetConfig,
        metadata=_meta("Budgets", "Default spend ceilings for unattended work."),
    )
    breaker: BreakerConfig = field(
        default_factory=BreakerConfig,
        metadata=_meta("Circuit Breaker", "Per-provider model-call breaker tuning."),
    )
    autonomy: AutonomyConfig = field(
        default_factory=AutonomyConfig,
        metadata=_meta(
            "Earned Autonomy",
            "The evidence bar an action type must clear before PersonalClaw offers "
            "to let it run at the next rung.",
        ),
    )
    scan_mode: str = field(
        default="redact",
        metadata=_meta(
            "Outbound Scan Mode",
            "How the model-call seam handles secrets/PII in an outbound prompt bound "
            "for a REMOTE provider: 'warn' (log + proceed), 'redact' (substitute + "
            "proceed), or 'block' (refuse the call). Local-only providers always warn "
            "(the content never leaves the machine).",
            enum=["warn", "redact", "block"],
            # Guard-class (§5): the default must never be the leaky 'warn' (which
            # would send secrets to a remote provider). A config typo falls back to
            # this default, so it must be SAFE. Enforced by test_guardrails_flags.py.
            guard_class=True,
            safe_values=["redact", "block"],
        ),
    )


@dataclass
class RoutingWeightsConfig:
    """Score weights for the learned routing stage (MODEL-ROUTING-TELEMETRY §4.2)."""

    success: float = field(
        default=0.60,
        metadata=_meta(
            "Success Weight",
            "How much a model's observed success rate counts toward its routing score.",
        ),
    )
    feedback: float = field(
        default=0.40,
        metadata=_meta(
            "Feedback Weight",
            "How much observed output quality (ledger/judge feedback) counts toward a "
            "model's routing score. With no feedback recorded yet, this weight collapses "
            "onto the success rate rather than penalizing an unrated model.",
        ),
    )


@dataclass
class RoutingConfig:
    """Telemetry-driven model routing (MODEL-ROUTING-TELEMETRY §7).

    Routing REORDERS the models a user already bound to a use case; it never invents
    a model or changes what resolution means. ``enabled`` is the master switch and is
    OFF by default: with it off, resolution walks the bound order exactly as it always
    did. Per-use-case mode and pin deliberately live NOT here but in
    ``use_case_settings/{uc}.json`` + ``routing_policy.json`` — they are
    bindings-adjacent state, beside the use case's other behavior settings.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enable Routing",
            "Master switch. When off, every use case resolves in the exact order you "
            "bound its models. Turn it on to let PersonalClaw prefer a local model for "
            "work it handles well and fall back to a cloud model when it can't.",
        ),
    )
    local_timeout_secs: float = field(
        default=20.0,
        metadata=_meta(
            "Local Attempt Timeout (seconds)",
            "How long a local model gets before the call falls back to the next model "
            "you bound. Keeps a slow local model from stalling background work.",
        ),
    )
    min_samples: int = field(
        default=5,
        metadata=_meta(
            "Minimum Samples",
            "How many recorded calls a model needs for a kind of request before its "
            "measured score is allowed to influence order. Below this, the simple "
            "local-first rule stands.",
        ),
    )
    weights: RoutingWeightsConfig = field(
        default_factory=RoutingWeightsConfig,
        metadata=_meta("Score Weights", "How success and quality combine into one score."),
    )
    hysteresis: float = field(
        default=0.05,
        metadata=_meta(
            "Hysteresis Margin",
            "How much better a model's score must be before the order actually changes. "
            "Prevents routing flip-flopping between two near-equal models.",
        ),
    )
    cloud_quality_margin: float = field(
        default=0.10,
        metadata=_meta(
            "Cloud Quality Margin",
            "How much better a cloud model must score than a local one to be tried "
            "first. Free and private wins ties.",
        ),
    )
    energy_sampling: bool = field(
        default=False,
        metadata=_meta(
            "Energy Sampling",
            "Record a rough energy estimate for local calls, so local cost is visible "
            "as something other than $0.",
        ),
    )
    reproposal_cooldown_days: int = field(
        default=14,
        metadata=_meta(
            "Re-proposal Cooldown (days)",
            "After you reject a routing suggestion, how long before the same change "
            "may be suggested again.",
        ),
    )


@dataclass
class RemediationConfig:
    """Health-scored self-remediation engine tuning (PLATFORM-RESILIENCE §4).

    The engine runs as one heartbeat-driven maintenance job. ``enabled`` is guard-class
    only in the sense that disabling it restores today's heartbeat maintenance (kept
    callable), so it defaults ON but is a plain toggle. The caps are the stopping
    conditions: reach ``target_score`` or spend ``max_cost_usd`` (per run), whichever
    first. Cadence adapts: healthy → ``idle_minutes_healthy`` between runs, degraded →
    ``tick_minutes_degraded``.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Remediation Engine",
            "Run the health-scored maintenance engine (FTS/embedding re-index, orphan "
            "prune, skill aging) as one background job. Disabling it falls back to the "
            "legacy per-tick heartbeat maintenance.",
        ),
    )
    target_score: int = field(
        default=90,
        metadata=_meta(
            "Target Health Score",
            "The engine stops a run once the health score reaches this (0-100).",
        ),
    )
    max_cost_usd: float = field(
        default=1.0,
        metadata=_meta(
            "Max Cost / Run",
            "Dollar ceiling for judgment-lane (model-touching) remediation work in one "
            "run. Deterministic jobs (re-index, prune) are free and never blocked.",
        ),
    )
    idle_minutes_healthy: int = field(
        default=60,
        metadata=_meta("Idle Cadence (healthy)", "Minutes between runs when healthy (score ≥95)."),
    )
    tick_minutes_degraded: int = field(
        default=5,
        metadata=_meta("Tick Cadence (degraded)", "Minutes between runs when degraded."),
    )


@dataclass
class ResilienceConfig:
    """Platform-resilience knobs (PLATFORM-RESILIENCE §7).

    Two guard-class switches: the Doctor health surface and the no-model
    degraded-mode indicator. Both are **guard-class** — a missing or unknown value
    parses as ENABLED (fail-safe, §5 tenet): a config typo must not silently hide the
    Doctor or the degraded chip, which are the surfaces that make a degraded system
    legible. Plus the platform default mid-turn message policy (§6). The
    remediation-engine sub-config (target-score / max-cost / idle cadence) is a later
    session's field.
    """

    doctor_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Doctor",
            "Show the Doctor health surface (Settings → Doctor + GET /api/doctor). "
            "Guard-class: a missing/unknown value keeps it ON.",
            guard_class=True,
            safe_values=[True],
        ),
    )
    degraded_indicator: bool = field(
        default=True,
        metadata=_meta(
            "Degraded-Mode Indicator",
            "Show the no-model degraded-mode chip in the shell (and GET "
            "/api/resilience/degraded) when a model-dependent surface is running on its "
            "LLM-free floor. Guard-class: a missing/unknown value keeps it ON.",
            guard_class=True,
            safe_values=[True],
        ),
    )
    mid_turn_policy: str = field(
        default="queue",
        metadata=_meta(
            "Mid-Turn Message Policy",
            "What happens to a follow-up message sent while a turn is still "
            "generating: 'queue' (deliver it next turn — the default, safe behavior), "
            "'steer' (inject it into the answer being written, when the running agent "
            "supports that — otherwise it queues), or 'cancel_and_replace' (cancel the "
            "in-flight answer and start fresh with the new message). Applies to "
            "interactive turns only; unattended work (loops, cron, subagents) always "
            "queues. A per-channel override wins over this platform default.",
            enum=["queue", "steer", "cancel_and_replace"],
        ),
    )
    cancel_replace_min_interval_secs: float = field(
        default=2.0,
        metadata=_meta(
            "Cancel-and-Replace Debounce",
            "Minimum seconds between cancel-and-replace actions on one session, so a "
            "burst of rapid follow-ups produces ONE cancel + the last message (the "
            "intermediate ones coalesce) rather than N cancels.",
        ),
    )
    remediation: RemediationConfig = field(
        default_factory=RemediationConfig,
        metadata=_meta("Remediation Engine", "Health-scored maintenance engine tuning."),
    )


@dataclass
class AuthConfigSection:
    """Owner-login settings (REMOTE-USER-AUTH C4).

    Login is **opt-in and off by default**. That default is load-bearing: a local install
    should keep working exactly as it does today — the `?token=` link, `personalclaw token`,
    the loopback paths — without anyone opting into a password. Turning this on ADDS a second
    issuer of the same session token; it never replaces the existing ones.

    The credential itself is NOT here. The username/hash live in `auth/credentials.json` and
    the TOTP secret in the credential store, because `config.json` is a settings file people
    read, diff and paste into issues.
    """

    login_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enable Login",
            "Offer a username/password login page as an additional way in. Off by default; "
            "the local token link keeps working either way, and remains the escape hatch if "
            "login is ever misconfigured.",
        ),
    )
    session_ttl: str = field(
        default="30d",
        metadata=_meta(
            "Session Lifetime",
            "How long a browser session lasts before you log in again (e.g. 30d, 12h). "
            "Explicitly-minted CLI tokens are unaffected.",
        ),
    )
    require_totp: bool = field(
        default=False,
        metadata=_meta(
            "Require 2FA Code",
            "Also require a time-based code at login. Set the secret up first with "
            "`personalclaw auth totp setup`, or login will be impossible.",
        ),
    )
    lockout_threshold: int = field(
        default=5,
        metadata=_meta(
            "Lockout After",
            "Failed login attempts before logins are temporarily refused.",
        ),
    )
    lockout_window: str = field(
        default="15m",
        metadata=_meta(
            "Lockout Window",
            "How long the lockout lasts, and the window failures are counted over.",
        ),
    )


@dataclass
class SecurityConfig:
    """Security controls for the agent's shell access.

    The built-in credential-exfiltration / destructive-command denylist lives in
    :mod:`personalclaw.security` (always enforced, read-only). ``denied_commands``
    here holds USER-added regexes, appended to the built-ins at screening time.
    """

    denied_commands: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Denied Commands",
            "User-added regexes for shell commands the agent must never run, "
            "appended to the always-on built-in denylist. Matched case-insensitively "
            "against the full command string.",
        ),
    )
    egress: EgressConfig = field(
        default_factory=EgressConfig,
        metadata=_meta(
            "Egress Policy",
            "Operator overrides for the outbound network guard (allow/deny hosts, "
            "private-network opt-in).",
        ),
    )
    autonomy_denylist: list[dict] = field(
        default_factory=list,
        metadata=_meta(
            "Autonomy Denylist",
            "Path/action deny rules for autonomous action-provider runs "
            "(AUTONOMY-GUARDRAILS §1.2). Each rule is "
            "{paths:[glob], actions:[class], verdict: block|needs_human}. Enforced at "
            "every action-dispatch seam, so an app-contributed provider inherits it. "
            "Composes with (never overrides) the always-on built-in denylists.",
        ),
    )


@dataclass
class WorkflowsConfig:
    """Workflow engine config (WORKFLOWS-V2).

    The old shape held surfacing knobs (`match_threshold` for the embedding matcher);
    that feature was deleted, and the namespace is reused rather than renamed — the
    plan's clean-break/namespace-reuse call. `match_threshold` RETURNS here with a new
    owner (WF2UNI-11): the UNIVERSAL-PLANNING tiered matcher's T4 embedding tie-breaker
    now reads it as the cosine floor below which an embedding is too weak to unseat a
    keyword tie. It is a real reader this time — not the inert knob it was under the old
    SOP feature — so the field is live and wired through all four config points.
    `enabled` keeps its meaning as the feature kill-switch; the engine's own keys
    (max_active_runs, per-lane max_concurrent_nodes, model_tiers, retention.*) arrive
    with Slice 0, each wired through all four config points."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Enabled",
            "Master switch for the workflow engine. Turning it off stops new runs "
            "from starting without touching stored definitions.",
        ),
    )
    max_active_runs: int = field(
        default=10,
        metadata=_meta(
            "Max Active Runs",
            "How many workflow runs may execute at once. A trigger firing faster than "
            "its runs finish would otherwise stack them without bound.",
        ),
    )
    max_concurrent_nodes: int = field(
        default=6,
        metadata=_meta(
            "Max Concurrent Nodes",
            "Total node slots per run, partitioned across typed lanes (llm/io/compute) "
            "so a long local-model action cannot block the run's model calls.",
        ),
    )
    default_node_timeout_total_secs: int = field(
        default=900,
        metadata=_meta(
            "Node Timeout — Total",
            "Wall-clock cap for one node, in seconds. 0 disables it.",
        ),
    )
    default_node_timeout_stall_secs: int = field(
        default=300,
        metadata=_meta(
            "Node Timeout — Stall",
            "Kill a node after this many seconds with NO progress, even when it is "
            "under the total cap. Progress events reset the clock, so a slow-but-"
            "working node survives while a wedged one does not.",
        ),
    )
    retention_per_def: int = field(
        default=100,
        metadata=_meta(
            "Runs Kept Per Workflow",
            "Oldest runs beyond this are pruned. Matches the per-job cap schedules use.",
        ),
    )
    max_concurrent_llm_nodes: int = field(
        default=4,
        metadata=_meta(
            "Lane Cap — Model Calls",
            "How many model-backed nodes (stage/infer) may run at once in one workflow.",
        ),
    )
    max_concurrent_io_nodes: int = field(
        default=2,
        metadata=_meta(
            "Lane Cap — Actions",
            "How many action nodes may run at once. Kept low on purpose: a fan-out over "
            "minutes-long local-model actions would otherwise starve the run's model "
            "calls behind it.",
        ),
    )
    model_tier_reasoning: str = field(
        default="reasoning",
        metadata=_meta(
            "Model Tier — Reasoning",
            "Which model use case a node asking for the `reasoning` tier resolves to. "
            "Templates name an intent, never a model, so they stay portable.",
        ),
    )
    model_tier_standard: str = field(
        default="orchestration",
        metadata=_meta(
            "Model Tier — Standard",
            "Use case for the `standard` tier. Distinct from `fast` on purpose: if both "
            "collapsed to one use case the three tiers would be decorative, and a node "
            "asking for a mid-capability model would silently get the cheapest one.",
        ),
    )
    model_tier_fast: str = field(
        default="background",
        metadata=_meta("Model Tier — Fast", "Use case for the `fast` tier."),
    )
    # WF2UNI-11: the tiered matcher's T4 embedding tie-break floor. A cosine below this is too weak
    # to unseat a deterministic keyword tie — the demotion is that a cosine number no longer decides
    # everything, so a weak one does not either. Live-editable: it is the dial a user turns when the
    # matcher is composing too readily (raise it) or ignoring a genuine semantic near-match (lower
    # it), which is exactly the kind of tuning done while watching, not after a restart.
    match_threshold: float = field(
        default=0.62,
        metadata=_meta(
            "Template Match Threshold",
            "How confident the embedding tie-breaker must be to override a keyword tie when two "
            "templates score alike (0-1). Higher composes more readily; lower lets a semantic "
            "near-match win. Only consulted on a tie — keyword matches always decide first.",
        ),
    )
    # TASKS-SOPS §8 (S61k): the four fields the plan names, each wired through all four points.
    surface_mode_default: str = field(
        default="off",
        metadata=_meta(
            "New Workflow Surfacing",
            "What a NEWLY authored workflow does before you opt it in: `off` never surfaces "
            "itself (explicit /workflow always works), `passive` injects its guidance, `suggest` "
            "may propose running itself. Defaults to off — auto-trigger-by-default is the mistake "
            "that made pasted content fire workflows.",
            choices=["off", "passive", "suggest"],
        ),
    )
    max_materialized_per_foreach: int = field(
        default=20,
        metadata=_meta(
            "Task Fan-Out Cap",
            "The most Tasks one foreach node may put on your board. A 200-item fan-out would "
            "otherwise bury every other task; the run still executes all items — only the board "
            "rows are capped, and the run reports what it withheld.",
        ),
    )
    confirmation_ttl_secs: int = field(
        default=7 * 24 * 3600,
        metadata=_meta(
            "Approval Lifetime",
            "How long a pending approval stays live. A week, because the realistic case is being "
            "away — a gate expiring overnight turns travel into lost work. 0 means never expires. "
            "A destructive confirmation auto-REJECTS on expiry; an ordinary one keeps waiting.",
        ),
    )
    lease_ttl_secs: int = field(
        default=900,
        metadata=_meta(
            "Task Claim Lifetime",
            "How long a session's exclusive claim on a task lasts before another may take it. "
            "Deliberately short: a worker that needs longer renews, which proves it is alive, "
            "whereas a long lease only delays discovering that it is not. Capped at one hour.",
        ),
    )
    default_quiet_windows: str = field(
        default="",
        metadata=_meta(
            "Default Quiet Hours",
            "A quiet window applied to new automations that do not set their own, as "
            "`HH:MM-HH:MM` (e.g. `22:00-08:00`). Empty means no default — an automation you "
            "created deliberately should run when you told it to, so this only fills a gap you "
            "left. A window may wrap midnight. Per-trigger settings always win.",
        ),
    )
    duty_gate_default: str = field(
        default="",
        metadata=_meta(
            "Default Duty Gate",
            "The is-the-user-on-duty check applied to new automations that name none. Empty "
            "means no gate. `manual` is the built-in on/off toggle; apps can supply others (a "
            "calendar, for instance). The gate always fails OPEN — if it cannot answer, the "
            "automation still fires, so a broken calendar app can never silence everything.",
        ),
    )
    workspace_default_mode: str = field(
        default="scratch",
        metadata=_meta(
            "Default Workspace Mode",
            "Where a run works when its template declares no `workspace.mode`: `scratch` (a "
            "per-run directory), `worktree` (a git worktree of the project's workspace) or "
            "`in_place` (the real tree, no isolation). A template's own declaration always "
            "wins. `scratch` is the default because being wrong about isolation should cost a "
            "copy, not the original — and `in_place` is deliberately never the default, since "
            "that is the mode in which a destructive step runs against real state.",
        ),
    )
    workspace_teardown_on_expiry: bool = field(
        default=True,
        metadata=_meta(
            "Run Teardown Before Deletion",
            "Run a workspace's declared `teardown` command before its directory is deleted by "
            "retention or an explicit delete. On, because teardown's whole job is to stop "
            "services and sync work out while the directory still exists — turning it off "
            "leaves a `docker compose` up after the run that started it is gone.",
        ),
    )

    def lane_caps(self) -> dict[str, int]:
        """Per-lane admission caps for the frontier (WF2-R21). `compute` is unmetered —
        a transform is microseconds of pure data reshaping, so capping it adds only
        latency."""
        return {
            "llm": self.max_concurrent_llm_nodes,
            "io": self.max_concurrent_io_nodes,
            "compute": 64,
        }

    def model_tiers(self) -> dict[str, str]:
        """The tier → use-case slot map (WF2-R16)."""
        return {
            "reasoning": self.model_tier_reasoning,
            "standard": self.model_tier_standard,
            "fast": self.model_tier_fast,
        }

    def __post_init__(self) -> None:
        # Clamp rather than reject: a nonsensical value from a hand-edited config must
        # not stop the gateway booting, and 0 concurrency would deadlock every run.
        if self.max_active_runs < 1:
            object.__setattr__(self, "max_active_runs", 1)
        if self.max_concurrent_nodes < 1:
            object.__setattr__(self, "max_concurrent_nodes", 1)
        if self.default_node_timeout_total_secs < 0:
            object.__setattr__(self, "default_node_timeout_total_secs", 0)
        if self.default_node_timeout_stall_secs < 0:
            object.__setattr__(self, "default_node_timeout_stall_secs", 0)
        if self.retention_per_def < 1:
            object.__setattr__(self, "retention_per_def", 1)
        if self.max_concurrent_llm_nodes < 1:
            object.__setattr__(self, "max_concurrent_llm_nodes", 1)
        if self.max_concurrent_io_nodes < 1:
            object.__setattr__(self, "max_concurrent_io_nodes", 1)
        # An empty tier mapping would resolve to no use case at all, so fall back to a
        # real axis rather than letting a node fail at dispatch time.
        for name, fallback in (
            ("model_tier_reasoning", "reasoning"),
            ("model_tier_standard", "orchestration"),
            ("model_tier_fast", "background"),
        ):
            if not str(getattr(self, name, "") or "").strip():
                object.__setattr__(self, name, fallback)


# ---------------------------------------------------------------------------
# Validation helpers — used by AppConfig.load()
# ---------------------------------------------------------------------------


def _lookup_schema_node(schema: dict, dot_path: str) -> dict | None:
    """Walk the JSON Schema tree to find the node for a dot-separated path."""
    parts = dot_path.split(".")
    node = schema
    for part in parts:
        props = node.get("properties", {})
        if part in props:
            node = props[part]
        else:
            return None
    return node


def _is_sensitive_path(schema: dict, dot_path: str) -> bool:
    """Return True if the field at *dot_path* is marked sensitive."""
    node = _lookup_schema_node(schema, dot_path)
    if node is None:
        return False
    return node.get("x-meta", {}).get("sensitive", False)


def _mask_value(value: object, sensitive: bool) -> str:
    """Return a display string for a value, masking if sensitive."""
    if sensitive:
        return '"***"'
    return repr(value)


def _dot_path_from_json_path(path: list) -> str:
    """Convert a jsonschema error path (deque of keys) to a dot-separated string."""
    return ".".join(str(p) for p in path)


def _actual_type_name(value: object) -> str:
    """Return a human-readable type name for a JSON value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _apply_field_default(data: dict, dot_path: str) -> None:
    """Remove the invalid value at *dot_path* so the loader falls back to defaults.

    Only handles top-level and one-level nested paths (e.g. ``agent.provider``).
    """
    parts = dot_path.split(".")
    if len(parts) == 1:
        data.pop(parts[0], None)
    elif len(parts) == 2:
        section = data.get(parts[0])
        if isinstance(section, dict):
            section.pop(parts[1], None)


def _validate_config_data(data: dict) -> dict:
    """Validate *data* against the config JSON Schema.

    Logs warnings for any issues found and mutates *data* in-place to
    remove invalid values (so the loader falls back to field defaults).
    Always returns *data* — never raises.
    """
    if not _HAS_JSONSCHEMA:
        return data

    # Lazy import to avoid circular import at module level
    from personalclaw.config.schema import JSON_SCHEMA, SCHEMA_REGISTRY

    # 1. Detect unrecognized top-level keys. The SCHEMA_REGISTRY is generated from the
    # AppConfig dataclass, but two legitimate top-level sections are read DIRECTLY off
    # the raw config dict (not modeled as AppConfig fields), so they aren't in the
    # registry — allowlist them or the loader spuriously warns on every load (the config
    # is loaded very frequently → a real log flood):
    #   • providers — the LLM-provider registry (llm/registry, providers/use_cases,
    #     knowledge/embedder, the providers handler all read data["providers"]).
    #   • meta — config-file provenance written by the FS-roundtrip layer
    #     (lastTouchedVersion/lastTouchedAt).
    #   • slack — app-owned opaque data: channel-app config that its
    #     migrate_from_core() lifts into the app store on boot. Core doesn't parse
    #     it (save() preserves it verbatim until the app deletes it). Allowlisted so
    #     the frequently-called loader doesn't log-flood a warning on a
    #     mid-migration config.
    _DIRECT_READ_TOP_KEYS = {"providers", "meta", "slack"}
    # Retired fields (removed from AppConfig with zero consumers). Silently drop
    # them so a pre-removal config.json doesn't warn on every load; the next
    # save() rewrites the file without them (self-heal).
    data.pop("default_memory_store", None)
    if isinstance(data.get("agent"), dict):
        data["agent"].pop("streaming", None)
        # agent.model: the global model is governed by active_models.json
        # (Settings → Models) + per-agent AgentProfile.model — the config-level
        # field was read by nothing.
        data["agent"].pop("model", None)
    if isinstance(data.get("inbox"), dict):
        # quick_reactions: echoed by the status API, rendered nowhere.
        # message_provider: sources are contributed by channel apps now; the
        # native/filesystem fallback chain in inbox_providers is the mechanism.
        data["inbox"].pop("quick_reactions", None)
        data["inbox"].pop("message_provider", None)
    known_top_keys = {e.path for e in SCHEMA_REGISTRY if "." not in e.path and e.path != "*"}
    known_top_keys |= _DIRECT_READ_TOP_KEYS
    unknown = sorted(set(data.keys()) - known_top_keys)
    if unknown:
        logger.warning("Config: unrecognized top-level keys: %s", ", ".join(unknown))

    # 2. Detect deprecated fields and log warnings
    for entry in SCHEMA_REGISTRY:
        if not entry.deprecated:
            continue
        parts = entry.path.split(".")
        # Check if the deprecated key is present in data
        node = data
        found = True
        for p in parts:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                found = False
                break
        if found:
            logger.warning(
                "Config: deprecated field '%s': %s",
                entry.path,
                entry.help,
            )

    # 3. Normalize case-insensitive enum fields before validation
    agent = data.get("agent")
    if isinstance(agent, dict) and isinstance(agent.get("log_level"), str):
        agent["log_level"] = agent["log_level"].upper()

    # 4. Run jsonschema validation
    try:
        jsonschema.validate(data, JSON_SCHEMA)
    except jsonschema.ValidationError:
        # Collect all errors (including nested ones)
        validator_cls = jsonschema.validators.validator_for(JSON_SCHEMA)
        validator = validator_cls(JSON_SCHEMA)
        for err in validator.iter_errors(data):
            dot_path = _dot_path_from_json_path(err.absolute_path)
            if not dot_path:
                # Root-level schema error — skip
                continue

            sensitive = _is_sensitive_path(JSON_SCHEMA, dot_path)
            value = err.instance
            display_val = _mask_value(value, sensitive)

            # Determine error type
            if err.validator == "enum":
                allowed = err.schema.get("enum", [])
                logger.warning(
                    "Config: enum violation at '%s': " "allowed values %s, got %s; using default",
                    dot_path,
                    allowed,
                    display_val,
                )
                _apply_field_default(data, dot_path)
            elif err.validator == "type":
                expected = err.schema.get("type", "unknown")
                actual = _actual_type_name(value)
                logger.warning(
                    "Config: type mismatch at '%s': "
                    "expected %s, got %s (value: %s); using default",
                    dot_path,
                    expected,
                    actual,
                    display_val,
                )
                _apply_field_default(data, dot_path)
            else:
                # Generic validation error
                logger.warning(
                    "Config: validation error at '%s': %s; using default",
                    dot_path,
                    err.message,
                )
                _apply_field_default(data, dot_path)

    return data


def resolve_memory_store_config(
    top_level_memory: dict,
    store_overrides: dict,
) -> dict:
    """Deep-merge store overrides onto top-level memory defaults.

    Merge happens at the raw dict level BEFORE dataclass construction.
    A store that overrides only some keys inherits the rest from the
    top-level config, not from MemoryConfig defaults.
    """
    merged = dict(top_level_memory)
    for key, value in store_overrides.items():
        if key == "description":
            continue  # description is store-only metadata, not a memory setting
        if value != "" and value is not None:
            merged[key] = value
    return merged


@dataclass
class ResolvedBindings:
    """Resolved bindings for a session, from the selected Agent Definition.

    Carries the working dir, memory store, provider agent, AND the agent's
    behavioral fields (system_prompt/tools/skills/approval_mode) so the runtime
    honors what the Agents UI edits.
    """

    workspace_dir: Path
    memory_store_name: str
    effective_memory_config: dict
    provider_agent: str
    # ACP permission/operating mode (claude-code/codex). Distinct from
    # approval_mode (the host gate). Empty = adapter default; ignored by runtimes
    # with no separate mode axis (the default dialect). Threaded to the acp factory as acp_mode.
    acp_mode: str = ""
    system_prompt: str = ""
    tools: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    approval_mode: str = ""
    # Referenced lifecycle-trigger IDs: the ONLY triggers that fire for this
    # agent's lifecycle. Empty = nothing fires (the seeded default ships triggers=[]).
    triggers: list = field(default_factory=list)
    # The agent-runtime backend for this agent: "native" | "acp:<cli>" | "acp".
    # Empty resolves to the global default at the bridge; a per-agent provider
    # supersedes the global AgentConfig.provider.
    provider: str = ""


@dataclass
class InboxConfig:
    """Inbox — reads your messages, drafts replies, presents for approval."""

    enabled: bool = field(
        default=False,
        metadata=_meta("Enabled", "Enable Inbox background polling."),
    )
    user_id: str = field(
        default="",
        metadata=_meta("User ID", "Your user ID on the message source (set during setup)."),
    )
    watched_channels: list[str] = field(
        default_factory=list,
        metadata=_meta("Watched Channels", "Channel IDs to monitor."),
    )
    poll_interval_seconds: int = field(
        default=60,
        metadata=_meta("Poll Interval", "Seconds between polls."),
    )
    style_rules: list[str] = field(
        default_factory=list,
        metadata=_meta("Style Rules", "Initial communication style rules for drafting."),
    )
    # NOTE: auto_cleanup_enabled / retention live in the inbox ENTITY settings
    # store (entity_settings/inbox.json via /api/inbox/settings), not here —
    # one store, read by retention maintenance at runtime. Alerting moved OUT
    # of the inbox entirely in plan 42 S3: it is now a `conditions` block on any
    # notification rule (entity_settings/notification_rules.json), so the same
    # keyword/name-mention escalation works for every kind, not just messages.
    test_mode: bool = field(
        default=False,
        metadata=_meta("Test Mode", "Include own messages in inbox (for testing)."),
    )
    engagement_ranking_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Engagement Ranking",
            "Rank the inbox by how much you engage with each channel/sender (favorites, opens, "
            "replies boost; dismisses lower) on top of recency. Off = pure newest-first.",
        ),
    )
    engagement_half_life_days: float = field(
        default=0.0,
        metadata=_meta(
            "Engagement Half-life (days)",
            "How fast an engagement boost fades (0 = the default ~6.6 days). Lower = more reactive "
            "to recent behavior; higher = longer memory.",
        ),
    )


@dataclass
class ProjectionRuleConfig:
    """A user-taught tool-output projection rule (TokenJuice, OP6 + §2.3). Output whose
    head matches ``match_regex`` is projected with ``strategy`` (a builtin content type:
    log/diff/json/test/csv/code) — or, when any op field is set (head/tail/keep/skip/
    count), shaped by the declarative ops interpreter instead. Pure data; no user code
    runs."""

    name: str = field(
        default="",
        metadata=_meta("Rule Name", "A short label for this projection rule."),
    )
    match_regex: str = field(
        default="",
        metadata=_meta(
            "Match Regex",
            "Regex matched against the start of a tool's output; a match selects this rule's strategy.",  # noqa: E501
        ),
    )
    strategy: str = field(
        default="log",
        metadata=_meta("Strategy", "The builtin projector to apply (log/diff/json/test/csv/code)."),
    )
    head: int = field(
        default=0,
        metadata=_meta(
            "Keep Head Lines",
            "Keep the first N lines (0 = off). Op — overrides the strategy projector.",
        ),  # noqa: E501
    )
    tail: int = field(
        default=0,
        metadata=_meta(
            "Keep Tail Lines",
            "Keep the last N lines (0 = off). Op — overrides the strategy projector.",
        ),  # noqa: E501
    )
    keep: str = field(
        default="",
        metadata=_meta("Keep Lines Matching", "Keep only lines matching this regex (empty = off)."),
    )
    skip: str = field(
        default="",
        metadata=_meta("Skip Lines Matching", "Drop lines matching this regex (empty = off)."),
    )
    count: str = field(
        default="",
        metadata=_meta(
            "Fold Lines Matching",
            "Fold lines matching this regex into one 'N elided' note (empty = off).",
        ),
    )


@dataclass
class DurabilityConfig:
    """Scheduled backup + retention + drills (DURABILITY-AND-SYNC §3)."""

    auto_backup: bool = field(
        default=True,
        metadata=_meta(
            "Automatic backups",
            "Take a nightly snapshot and an hourly incremental export in the "
            "background, so losing work never depends on remembering to run a "
            "backup. Off means backups only happen when you run them by hand.",
        ),
    )
    keep_daily: int = field(
        default=14,
        metadata=_meta(
            "Keep daily snapshots",
            "How many days of nightly snapshots to retain before thinning to " "weeklies.",
        ),
    )
    keep_weekly: int = field(
        default=8,
        metadata=_meta("Keep weekly snapshots", "How many weeks to keep one snapshot each."),
    )
    keep_monthly: int = field(
        default=12,
        metadata=_meta("Keep monthly snapshots", "How many months to keep one snapshot each."),
    )
    restore_drills: bool = field(
        default=True,
        metadata=_meta(
            "Monthly restore drill",
            "Once a month, restore the newest snapshot into a temporary directory "
            "and verify it — a backup nobody has restored is a hope, not a backup. "
            "Never touches live data; reports pass or fail.",
        ),
    )
    # ── time-travel (DURABILITY-AND-SYNC §5) ──
    time_travel: bool = field(
        default=True,
        metadata=_meta(
            "Workspace time travel",
            "Keep a local, minute-by-minute history of the things you and the "
            "assistant edit — configuration, skills, prompts, project context and "
            "the memory notes — so a bad edit is an undo rather than a restore. "
            "The history stays on this machine and is never synced or exported. "
            "Secrets are excluded from it entirely.",
        ),
    )
    # ── sync (DURABILITY-AND-SYNC §4) — off by default; needs a configured transport ──
    sync_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Sync between machines",
            "Keep this PersonalClaw in sync with your other machines through a shared "
            "remote (a git repo or a synced folder). Off by default; turning it on also "
            "requires choosing a sync transport. Sync never overwrites — it merges, and "
            "your local data is always kept.",
        ),
    )
    sync_transport: str = field(
        default="",
        metadata=_meta(
            "Sync transport",
            "Which installed sync transport to use (e.g. git-sync, dir-sync, rsync-sync, "
            "s3-sync). Empty means no transport is chosen yet, so sync stays idle even "
            "if enabled.",
        ),
    )
    sync_stale_after_secs: int = field(
        default=900,
        metadata=_meta(
            "Sync staleness window (seconds)",
            "How long to trust a recent remote check before pulling again. A short "
            "window syncs sooner at the cost of more remote polls; the default (15 "
            "minutes) balances freshness against chattiness.",
        ),
    )
    sync_encrypt: str = field(
        default="auto",
        metadata=_meta(
            "Encrypt synced data",
            "Encrypt everything before it leaves this machine, so a shared bucket or "
            "folder holds unreadable data without your passphrase. 'auto' picks the "
            "right default per transport — on for cloud storage and shared folders, "
            "off for a private git repo, where a readable history is the whole point. "
            "'on' and 'off' override that. Encryption needs a passphrase saved in the "
            "credential store; without one, sync stops rather than sending plain data.",
        ),
    )


@dataclass
class EvalsConfig:
    """The offline eval substrate (EVALUATION-SUBSTRATE §10).

    Off by default: the substrate runs nothing until a study/benchmark/matrix is
    invoked. Everything it produces is a file under ``~/.personalclaw/evals/``;
    there is no daemon. ``bakeoff_capture_enabled`` is a privacy-sensitive capture
    flag kept OFF by default and out of the one-click PATCH allowlist (like
    ``inbound.mcp.allow_remote``) — flipping it is a deliberate config-file edit.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Evals enabled",
            "Turn on the offline eval substrate — pre-registered studies, ablation "
            "reports, the retrieval/judge benchmarks. Off by default; nothing runs "
            "until you invoke a study or benchmark. Results are files under "
            "~/.personalclaw/evals/, never a background service.",
        ),
    )
    study_default_k: int = field(
        default=5,
        metadata=_meta(
            "Study runs per arm (k)",
            "How many paired runs per arm a template A/B study takes by default. "
            "k≈5 is the smallest paired design that survives judge noise; higher k "
            "buys confidence at a linear cost in runs and judge calls.",
        ),
    )
    judge_agreement_floor: float = field(
        default=0.6,
        metadata=_meta(
            "Judge agreement floor",
            "Below this position-swap agreement rate a study's verdict is "
            "'judge_unreliable' — it files a judge-calibration item instead of a "
            "template verdict, so a noisy judge never produces a fake win.",
        ),
    )
    ablation_cadence_days: int = field(
        default=30,
        metadata=_meta(
            "Ablation cadence (days)",
            "How often the harness-ablation runner picks one component to measure "
            "keep/remove/lighten. Monthly by default — component payoff drifts on the "
            "timescale of model upgrades, not days.",
        ),
    )
    bakeoff_capture_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Bake-off input capture",
            "Let the model bake-off capture real per-use-case inputs to score "
            "candidate models on your actual traffic. OFF by default and privacy-"
            "sensitive: captured inputs are redacted and the capture auto-expires. "
            "Flipping it is a deliberate config-file edit, not a one-click toggle.",
        ),
    )
    default_budget_usd: float = field(
        default=0.0,
        metadata=_meta(
            "Default eval budget (USD)",
            "The default hard spend cap a matrix/study run refuses to exceed. 0 means "
            "no default cap — each study still declares its own budget at registration.",
        ),
    )


@dataclass
class ProactiveConfig:
    """Proactive triage + the decision journal (PROACTIVE-ASSISTANT §"Config Map").

    Two switches are OFF by default and stay that way: ``triage_enabled`` (nothing
    collects or spends until you ask for a digest) and ``auto_execute_enabled``
    (the digest proposes; it does not act). That pairing is the plan's soul
    guardrail — proactive behaviors propose, they never silently write — so the
    defaults are fail-closed on purpose. Do not "helpfully" flip them.
    """

    triage_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Proactive triage",
            "Let the scheduled triage digest run — collect what accumulated across "
            "inbox, channels and background runs, then propose what to do about it. "
            "Off by default: nothing is collected and no model is called until you "
            "turn this on and install a schedule.",
        ),
    )
    digest_schedule: str = field(
        default="0 8 * * *",
        metadata=_meta(
            "Digest schedule",
            "When the triage digest fires, as a cron expression in your configured "
            "timezone. 08:00 daily by default — a morning digest. Quiet hours still "
            "apply: an info-ranked digest defers rather than waking you.",
        ),
    )
    auto_execute_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Auto-execute trivial actions",
            "Let trivial-tier proposals and patterns you explicitly taught with "
            "'always yes' execute without another confirmation. Off by default. Even "
            "on, external sends stay drafts until that rule is individually "
            "graduated, and every auto-execution is a ledger row with one-click undo.",
        ),
    )
    max_auto_actions_per_run: int = field(
        default=5,
        metadata=_meta(
            "Max auto-actions per digest",
            "Hard cap on how many actions one digest may auto-execute; the rest queue "
            "as pending regardless of tier. This is a ceiling, not a target — it "
            "bounds the blast radius of one bad classification.",
        ),
    )
    classifier_gate_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Classifier gate",
            "Filter collected items through the lightweight relevance gate before the "
            "proposal stage. On by default: it is what keeps a quiet window from "
            "spending anything, and turning it off sends every item to the model.",
        ),
    )
    decision_default_horizon_days: int = field(
        default=90,
        metadata=_meta(
            "Decision review horizon (days)",
            "How far out a logged decision schedules its review when you do not name "
            "a horizon. 90 days by default — long enough for an outcome to exist, "
            "short enough that you still remember the reasoning.",
        ),
    )

    def __post_init__(self) -> None:
        # A cap of 0 means "auto-execute nothing", which is a coherent position; a
        # negative cap is not, and would read as "unbounded" to a `<` check.
        if self.max_auto_actions_per_run < 0:
            self.max_auto_actions_per_run = 0
        if self.decision_default_horizon_days < 1:
            self.decision_default_horizon_days = 1


@dataclass
class InboundSurfaceConfig:
    """One inbound surface's switches (MCP-READONLY-INBOUND §C4).

    Both default to the CLOSED position. `enabled` in particular is fail-closed by
    design: a missing or corrupt value reads False, because an inbound network
    surface that turns itself on when config is unreadable fails in the wrong
    direction. Do not "fix" this to be lenient."""

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enabled",
            "Expose this read-only inbound surface. Off by default; also requires a "
            "surface token (personalclaw inbound token create mcp). Loopback-only "
            "unless allow_remote is on AND inbound.public_url is set.",
        ),
    )
    allow_remote: bool = field(
        default=False,
        metadata=_meta(
            "Allow remote",
            "Permit non-loopback callers. Requires inbound.public_url, and the "
            "request's Host must match it exactly. Discouraged until the hardened "
            "inbound layer lands — prefer an SSH tunnel to loopback.",
        ),
    )


@dataclass
class InboundConfig:
    """Curated read-only ways IN (MCP-READONLY-INBOUND). Off unless configured."""

    mcp: InboundSurfaceConfig = field(
        default_factory=InboundSurfaceConfig,
        metadata=_meta("MCP surface", "POST /mcp — JSON-RPC read-only tool surface."),
    )
    public_url: str = field(
        default="",
        metadata=_meta(
            "Public URL",
            "The URL this instance answers to (e.g. https://pc.example.com). Required "
            "for any non-loopback inbound access; the request Host must match it.",
        ),
    )


@dataclass
class FeedbackConfig:
    """Feedback Signal (plan 58) — 👍/👎 capture on AI judgment outputs + the
    deterministic per-producer accuracy thresholds. No LLM anywhere; zero telemetry."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Feedback",
            "Show 👍/👎 on AI judgment outputs (inbox classifications, drafts, digests, "
            "loop findings) and track per-source accuracy. Off = thumbs never render.",
        ),
    )
    retire_threshold: float = field(
        default=0.4,
        metadata=_meta(
            "Retire Threshold",
            "A judgment source whose accuracy falls below this (with enough verdicts) "
            "stops surfacing and gets a 'retire this rule?' proposal.",
        ),
    )
    min_n: int = field(
        default=5,
        metadata=_meta(
            "Minimum Verdicts",
            "Verdicts required before a source's accuracy is shown or acted on.",
        ),
    )
    window_days: int = field(
        default=90,
        metadata=_meta(
            "Attribution Window (days)",
            "How far back verdicts count toward a source's rolling accuracy.",
        ),
    )


@dataclass
class PlanningConfig:
    """Planner entry surfaces (WORKFLOWS-V2-UNIVERSAL-PLANNING UP-R18) — the watched
    scratchpad. Empty by default: an unset path reads no files at all, so ambient capture
    is something the user opts into by naming one local file, never a default that starts
    scanning their notes."""

    scratchpad_path: str = field(
        default="",
        metadata=_meta(
            "Scratchpad path",
            "A local notes file to scan for jotted todos. Each actionable line becomes a "
            "PROPOSED plan in your inbox with a link back to the source line — never run "
            "automatically. Checked (- [x]) and struck-through lines are ignored. "
            "Empty = off.",
        ),
    )


@dataclass
class AgentsRoutingConfig:
    """Agent routing (AGENT-ROUTING) — suggest-first specialist routing. Deterministic
    classification (keyword + embedding, no LLM); a non-blocking chip proposes, the
    user consents. Silent auto-routing is explicitly out of scope."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Agent routing suggestions",
            "When a message in a default-agent chat fits an installed specialist, "
            "show a one-click 'route to <agent>?' chip. Off = never suggested.",
        ),
    )
    min_confidence: float = field(
        default=0.62,
        metadata=_meta(
            "Routing confidence",
            "Minimum embedding-match confidence before a routing chip appears.",
        ),
    )
    cooldown_hours: float = field(
        default=24.0,
        metadata=_meta(
            "Routing dismiss cooldown (hours)",
            "After dismissing a suggestion for an agent, suppress it for this long "
            "(three cumulative dismissals mute the agent until you re-enable it).",
        ),
    )


@dataclass
class ToolsConfig:
    """Tool-output handling config. Today: user-teachable projection rules that extend
    the builtin content-type dispatch for large tool outputs (TokenJuice, OP6)."""

    projection_rules: list[ProjectionRuleConfig] = field(
        default_factory=list,
        metadata=_meta(
            "Projection Rules",
            "User-taught rules mapping a tool-output content marker (regex) to a "
            "builtin projection strategy (log/diff/json/test/csv), so a large output "
            "the sniffer would blunt-cut as generic keeps its salient slice instead. "
            "Consulted before the heuristic sniff; a bad regex is skipped.",
        ),
    )
    # Background compression service (Context Economy §4) — the always-on complement
    # to on-demand projection: idle, at-rest session history is topic-segmented and
    # attention-weighted compressed on the maintenance cadence so long sessions stay
    # fast. Feature flag (missing = the DEFAULT, not fail-safe-off): a maintenance
    # nicety, not a guard.
    bg_compress_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Background compression",
            "Continuously compress old, idle conversation history in the background "
            "(topic-segmented, attention-weighted) so long sessions stay fast. Every "
            "dropped span is archived first (fully recoverable) and the summary names "
            "its archive. Incognito/temporary chats are never touched.",
        ),
    )
    bg_compress_idle_days: float = field(
        default=7.0,
        metadata=_meta(
            "Background compression idle window",
            "Only compress sessions untouched for at least this many days (at rest — "
            "an active session is never compressed).",
        ),
    )
    # Dynamic tool-group activation (Context Economy §5) — partition the tool
    # surface by provider so inactive groups cost one catalog line instead of
    # every schema. Off by default: with it off, and for interactive chat even
    # when on, the tool block is byte-identical to having no groups at all.
    groups_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Tool groups",
            "Partition tools into named groups (one per tool provider) that the "
            "agent activates on demand, so unused groups don't spend context on "
            "their schemas. Every tool stays callable by name and searchable via "
            "tool_search — this saves context, it does not restrict capability. "
            "Interactive chat keeps every group active; background/loop/subagent "
            "runs start focused (see the per-surface defaults).",
        ),
    )
    group_defaults: dict[str, list[str]] = field(
        default_factory=dict,
        metadata=_meta(
            "Tool groups per surface",
            'Which tool groups start active per surface, e.g. {"background": '
            '["core", "memory"]}. Keys are session axes (background, loops, '
            'orchestration, chat); "*" means all groups. A surface with no entry '
            "keeps every group active. Overrides the built-in defaults.",
        ),
    )


@dataclass
class SandboxConfig:
    """Resource ceilings applied to agent-influenced child processes (PHF-1).

    These are the numeric inputs to the post-exec ceiling shim
    (``personalclaw._spawn_exec_shim``): a soft NOFILE cap, a max-child-process count
    (RLIMIT_NPROC), and a max resident-set size in MB (RLIMIT_AS). The ``ResourceCeilings``
    profiles in ``sandbox.py`` translate these into the per-spawn policy — the ``tool``
    profile applies them fully with an OOM bias, ``session_host`` deliberately raises NOFILE
    to the inherited hard limit (an ACP host multiplexes many MCP pipes; a low cap causes
    EMFILE), and ``none`` applies nothing. ``0`` disables an individual limit."""

    nofile: int = field(
        default=4096,
        metadata=_meta(
            "Sandbox Max Open Files",
            "Soft RLIMIT_NOFILE ceiling for agent-influenced child processes (bash tools, "
            "app backends, MCP servers). 0 disables the NOFILE cap. ACP session hosts are "
            "exempt — they raise NOFILE to the inherited hard limit to multiplex many MCP "
            "pipes without hitting EMFILE.",
        ),
    )
    max_pids: int = field(
        default=0,
        metadata=_meta(
            "Sandbox Max Processes",
            "RLIMIT_NPROC ceiling for agent child processes. 0 disables it (the default). "
            "NOTE: RLIMIT_NPROC is a PER-USER limit that counts ALL of the user's existing "
            "processes, not just this child's subtree — so an absolute cap can break a busy "
            "host (git worktree, npm) with 'cannot fork'. Real per-subtree fork-bomb "
            "containment is the opt-in cgroup tier, not this rlimit; leave 0 unless you know "
            "the host's process budget.",
        ),
    )
    max_rss_mb: int = field(
        default=0,
        metadata=_meta(
            "Sandbox Max Memory (MB)",
            "RLIMIT_AS ceiling in megabytes for an agent child's address space. 0 disables "
            "the memory cap (the default — RLIMIT_AS is coarse and can break memory-mapped "
            "toolchains, so it is opt-in).",
        ),
    )
    env_passthrough: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Child Env Passthrough",
            "Extra environment variable NAMES that hook, cron-script and bash-action "
            "children inherit from the gateway, on top of the minimal base "
            "(sandbox.CHILD_ENV_BASE_NAMES: PATH, locale, home-equivalents, proxy/CA "
            "settings and the three PERSONALCLAW_* vars). Everything else is withheld — a "
            "child does not inherit the gateway's environment. Declare a name here when a "
            "script legitimately needs it (e.g. SLACK_BOT_TOKEN for a notifier, a language "
            "runtime's variable); the withheld names are listed in the debug log at each "
            "spawn. Names matching the credential floor (AWS_SECRET*, AWS_SESSION*, "
            "SSH_AUTH_SOCK, GNUPGHOME, GIT_ASKPASS) are refused even when declared.",
        ),
    )


@dataclass
class CheckpointsConfig:
    """Turn-bound file checkpointing bounds (EXECUTION-ISOLATION §6).

    The caps on ``turn_checkpoints``' per-session store: how many turns of pre-edit
    backups to keep, how many megabytes of file bodies, and how big a single body may be
    before it is recorded manifest-only. Guard-class note: these are BOUNDS, not a safety
    control — the secrecy floor (which files are never copied at all) is a code-level
    tuple in ``turn_checkpoints.NEVER_CAPTURE_GLOBS`` with no config field, deliberately,
    so no config edit can widen what the store is allowed to hold."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "File Checkpoints",
            "Back up a file's current bytes before an agent's first write to it in a turn, "
            "so /rewind-to-turn can restore it. Filesystem-only: it never rewinds the "
            "conversation. Off means a wrong edit is unrecoverable.",
        ),
    )
    max_mb: int = field(
        default=200,
        metadata=_meta(
            "Checkpoint Store Cap (MB)",
            "Maximum megabytes of backed-up file bodies kept per session. When a new backup "
            "would exceed this, the oldest turns are pruned until it fits. 0 disables the "
            "byte cap (the turn cap still applies).",
        ),
    )
    max_turns: int = field(
        default=50,
        metadata=_meta(
            "Checkpoint Turns Kept",
            "How many recent turns of backups to keep per session. Older turns are pruned, "
            "which makes rewinding past them impossible — the preview says so.",
        ),
    )
    max_file_mb: int = field(
        default=8,
        metadata=_meta(
            "Checkpoint Max File (MB)",
            "A single file larger than this is recorded in the turn manifest but its bytes "
            "are not copied, so a rewind reports it as 'not captured' instead of restoring "
            "it. Keeps one large binary write from consuming the whole store cap. 0 "
            "disables the per-file limit.",
        ),
    )


@dataclass
class VoiceConfig:
    """Hands-free voice-loop knobs (MULTIMODAL-IO §4.5).

    Guard-class note: the four booleans are convenience features, not safety
    guards — plain defaults, no fail-safe parsing. Turning one off degrades the
    voice loop's comfort (more echo, code read aloud), never its safety, so a
    config typo must not be second-guessed here.
    """

    confirmation_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONFIRMATION_PHRASES),
        metadata=_meta(
            "Confirmation Phrases",
            "In hands-free mode a dictated transcript accumulates and is only sent "
            "once one of these phrases ends what you just said, so a half-finished "
            "thought never becomes an executed instruction. Push-to-talk and typed "
            "input ignore this entirely.",
        ),
    )
    exit_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXIT_PHRASES),
        metadata=_meta(
            "Exit Phrases",
            "Saying one of these in hands-free mode clears the accumulated "
            "transcript without sending it.",
        ),
    )
    echo_filter_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Echo Filter",
            "Drop a transcription that shares three consecutive words with what the "
            "assistant just spoke — the speaker bleeding back into the microphone. "
            "Applies only to hands-free requests; the dashboard shows the drop "
            "instead of looking deaf.",
        ),
    )
    duplex_mute_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Mute While Speaking",
            "Suspend the microphone and discard queued audio while a spoken reply "
            "plays. This is what kills most echo; the filter above is the backstop.",
        ),
    )
    clean_for_speech_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Clean Text Before Speaking",
            "Strip code blocks, reduce URLs to their domain and paths to their "
            "filename, and drop CLI flags before synthesis. The chat transcript "
            "always keeps the full text — only the audio is cleaned.",
        ),
    )
    push_to_talk_chord: str = field(
        default=DEFAULT_PUSH_TO_TALK_CHORD,
        metadata=_meta(
            "Push-to-Talk Shortcut",
            "The global shortcut the desktop app binds for push-to-talk: press to "
            "start capturing the microphone, press again to stop and transcribe into "
            "the composer. Needs at least one modifier — a bare key would be taken "
            "from every other app on the machine. The desktop shell is what actually "
            "binds it, so an unusable or already-taken chord is refused there with a "
            "reason rather than failing silently. Ignored in a browser tab, which has "
            "no global shortcuts.",
        ),
    )
    voice_disclaimer_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Voice-Origin Disclaimer",
            "Append a one-line note to a dictated message telling the model the text "
            "came from speech recognition and may be misheard, so it self-corrects on "
            "garbled homophones instead of confidently misreading them.",
        ),
    )


@dataclass
class AppConfig:
    agent: AgentConfig = field(
        default_factory=AgentConfig,
        metadata=_meta("Agent", "Agent runtime configuration."),
    )
    sandbox: SandboxConfig = field(
        default_factory=SandboxConfig,
        metadata=_meta("Sandbox", "Resource ceilings for agent-influenced child processes."),
    )
    checkpoints: CheckpointsConfig = field(
        default_factory=CheckpointsConfig,
        metadata=_meta("File Checkpoints", "Bounds on turn-bound file checkpointing."),
    )
    session: SessionConfig = field(
        default_factory=SessionConfig,
        metadata=_meta("Session", "Session management settings."),
    )
    loops: LoopsConfig = field(
        default_factory=LoopsConfig,
        metadata=_meta("Autonomous", "Autonomous goal loop settings."),
    )
    memory: MemoryConfig = field(
        default_factory=MemoryConfig,
        metadata=_meta("Memory", "Memory and embedding configuration."),
    )
    skills: SkillsConfig = field(
        default_factory=SkillsConfig,
        metadata=_meta("Skills", "Skill loading and matching configuration."),
    )
    learning: LearningConfig = field(
        default_factory=LearningConfig,
        metadata=_meta("Learning", "Per-turn self-improvement review configuration."),
    )
    knowledge: KnowledgeConfig = field(
        default_factory=KnowledgeConfig,
        metadata=_meta("Knowledge", "Knowledge-store write semantics and expiry."),
    )
    workflows: WorkflowsConfig = field(
        default_factory=WorkflowsConfig,
        metadata=_meta("Workflows", "Workflow SOP surfacing configuration."),
    )
    security: SecurityConfig = field(
        default_factory=SecurityConfig,
        metadata=_meta("Security", "Shell-command security controls."),
    )
    auth: AuthConfigSection = field(
        default_factory=AuthConfigSection,
        metadata=_meta("Login", "Owner login — an additional front door, off by default."),
    )
    routing: RoutingConfig = field(
        default_factory=RoutingConfig,
        metadata=_meta(
            "Model Routing",
            "Which of your bound models handles which kind of request.",
        ),
    )
    guardrails: GuardrailsConfig = field(
        default_factory=GuardrailsConfig,
        metadata=_meta("Guardrails", "Autonomy safety floor — budgets, breaker, scan."),
    )
    resilience: ResilienceConfig = field(
        default_factory=ResilienceConfig,
        metadata=_meta("Resilience", "Doctor health surface + no-model degraded indicator."),
    )
    voice: VoiceConfig = field(
        default_factory=VoiceConfig,
        metadata=_meta("Voice", "Hands-free voice loop — gating, echo filter, spoken text."),
    )
    inbox: InboxConfig = field(
        default_factory=InboxConfig,
        metadata=_meta("Inbox", "Reads messages, drafts replies."),
    )
    tools: ToolsConfig = field(
        default_factory=ToolsConfig,
        metadata=_meta("Tools", "Tool-output handling — user-teachable projection rules."),
    )
    feedback: FeedbackConfig = field(
        default_factory=FeedbackConfig,
        metadata=_meta("Feedback", "👍/👎 capture on AI judgments + accuracy thresholds."),
    )
    inbound: InboundConfig = field(
        default_factory=InboundConfig,
        metadata=_meta("Inbound", "Curated read-only inbound surfaces (off by default)."),
    )
    agents_routing: AgentsRoutingConfig = field(
        default_factory=AgentsRoutingConfig,
        metadata=_meta("Agent Routing", "Suggest-first specialist routing."),
    )
    planning: PlanningConfig = field(
        default_factory=PlanningConfig,
        metadata=_meta("Planning", "Planner entry surfaces — the watched scratchpad."),
    )

    dashboard: DashboardConfig = field(
        default_factory=DashboardConfig,
        metadata=_meta("Dashboard", "Dashboard UI settings."),
    )
    legibility: LegibilityConfig = field(
        default_factory=LegibilityConfig,
        metadata=_meta(
            "Legibility", "Platform-legibility features — Discover tips + context adapters."
        ),
    )
    ambient: AmbientConfig = field(
        default_factory=AmbientConfig,
        metadata=_meta("Ambient", "Composable home + generative UI + tray companion settings."),
    )
    companion: CompanionConfig = field(
        default_factory=CompanionConfig,
        metadata=_meta(
            "Companion apps", "LAN discovery + instance name for native companion clients."
        ),
    )
    local_models: LocalModelsConfig = field(
        default_factory=LocalModelsConfig,
        metadata=_meta(
            "Local models", "Memory-pressure warning threshold + sidecar restart budget."
        ),
    )
    sources: SourcesConfig = field(
        default_factory=SourcesConfig,
        metadata=_meta("Watched sources", "Poll engine for watched feeds, pages and directories."),
    )
    packs: PacksConfig = field(
        default_factory=PacksConfig,
        metadata=_meta("Packs", "Pack import + skill-catalog + connector-catalog settings."),
    )
    apps: AppsConfig = field(
        default_factory=AppsConfig,
        metadata=_meta("Apps", "App Store settings that are not per-app (default sources)."),
    )
    hooks: dict = field(
        default_factory=dict,
        metadata=_meta("Hooks", "Script hook definitions keyed by hook ID."),
    )
    # Channel-agnostic history-buffer sizing (used by ChannelHistory). Per-channel
    # activation + all other channel behavior is the channel APP's own config.
    observe_max_messages: int = field(
        default=200,
        metadata=_meta("Observe Max Messages", "Max messages per observe-mode channel."),
    )
    observe_ttl_hours: float = field(
        default=168.0,
        metadata=_meta("Observe TTL Hours", "Hours to keep observe history."),
    )
    agents: dict[str, AgentProfile] = field(
        default_factory=dict,
        metadata=_meta("Agents", "Named PersonalClaw agent definitions."),
    )
    default_agent: str = field(
        default="",
        metadata=_meta("Default Agent", "Active PersonalClaw agent name from the agents section."),
    )
    memory_stores: dict[str, MemoryStoreConfig] = field(
        default_factory=dict,
        metadata=_meta("Memory Stores", "Named memory store definitions."),
    )
    auto_update: bool = field(
        default=True,
        metadata=_meta(
            "Auto Update",
            "Automatically apply updates when a new version is found "
            "(update checks always run; this gates the unattended "
            "pull + rebuild + restart).",
        ),
    )
    timezone: str = field(
        default="",
        metadata=_meta(
            "Timezone",
            "IANA timezone name (e.g. 'America/Los_Angeles'). "
            "Used to display cron schedules in local time.",
        ),
    )
    snapshot_dir: str = field(
        default="",
        metadata=_meta(
            "Snapshot Directory",
            "Directory for personalclaw snapshot output. "
            "Defaults to ~/.personalclaw/snapshots if empty.",
        ),
    )
    durability: "DurabilityConfig" = field(
        default_factory=lambda: DurabilityConfig(),
        metadata=_meta("Durability", "Scheduled backups, retention, and restore drills."),
    )
    evals: "EvalsConfig" = field(
        default_factory=lambda: EvalsConfig(),
        metadata=_meta(
            "Evals",
            "The offline eval substrate — studies, ablation, retrieval/judge benchmarks.",
        ),
    )
    proactive: "ProactiveConfig" = field(
        default_factory=lambda: ProactiveConfig(),
        metadata=_meta(
            "Proactive",
            "The scheduled triage digest, its auto-execution bounds, and the decision "
            "journal's default review horizon.",
        ),
    )

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from ~/.personalclaw/config.json, falling back to defaults."""
        path = config_path()
        if not path.exists():
            return cls()

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load config from %s: %s", path, e)
            return cls()

        # Must be a dict to proceed
        if not isinstance(data, dict):
            logger.warning("Config is not a JSON object, using defaults")
            return cls()

        # Validate against JSON Schema (advisory — never fatal)
        _validate_config_data(data)

        agent_data = data.get("agent", {})
        if not isinstance(agent_data, dict):
            agent_data = {}
        session_data = data.get("session", {})
        if not isinstance(session_data, dict):
            session_data = {}
        loops_data = data.get("loops", {})
        if not isinstance(loops_data, dict):
            loops_data = {}
        memory_data = data.get("memory", {})
        if not isinstance(memory_data, dict):
            memory_data = {}
        dashboard_data = data.get("dashboard", {})
        if not isinstance(dashboard_data, dict):
            dashboard_data = {}
        sandbox_data = data.get("sandbox", {})
        if not isinstance(sandbox_data, dict):
            sandbox_data = {}
        checkpoints_data = data.get("checkpoints", {})
        if not isinstance(checkpoints_data, dict):
            checkpoints_data = {}
        legibility_data = data.get("legibility", {})
        if not isinstance(legibility_data, dict):
            legibility_data = {}
        ambient_data = data.get("ambient", {})
        if not isinstance(ambient_data, dict):
            ambient_data = {}
        companion_data = data.get("companion", {})
        if not isinstance(companion_data, dict):
            companion_data = {}
        local_models_data = data.get("local_models", {})
        if not isinstance(local_models_data, dict):
            local_models_data = {}
        sources_data = data.get("sources", {})
        if not isinstance(sources_data, dict):
            sources_data = {}
        packs_data = data.get("packs", {})
        if not isinstance(packs_data, dict):
            packs_data = {}
        apps_data = data.get("apps", {})
        if not isinstance(apps_data, dict):
            apps_data = {}
        inbox_data = data.get("inbox", {})
        if not isinstance(inbox_data, dict):
            inbox_data = {}
        tools_data = data.get("tools", {})
        if not isinstance(tools_data, dict):
            tools_data = {}
        feedback_data = data.get("feedback", {})
        inbound_data = data.get("inbound", {}) or {}
        durability_data = data.get("durability", {}) or {}
        evals_data = data.get("evals", {}) or {}
        proactive_data = data.get("proactive", {}) or {}
        if not isinstance(proactive_data, dict):
            proactive_data = {}
        if not isinstance(feedback_data, dict):
            feedback_data = {}
        agents_routing_data = data.get("agents_routing", {})
        if not isinstance(agents_routing_data, dict):
            agents_routing_data = {}
        planning_data = data.get("planning", {})
        if not isinstance(planning_data, dict):
            planning_data = {}
        skills_data = data.get("skills", {})
        if not isinstance(skills_data, dict):
            skills_data = {}

        workflows_data = data.get("workflows", {})
        if not isinstance(workflows_data, dict):
            workflows_data = {}

        learning_data = data.get("learning", {})
        if not isinstance(learning_data, dict):
            learning_data = {}

        knowledge_data = data.get("knowledge", {})
        if not isinstance(knowledge_data, dict):
            knowledge_data = {}

        security_data = data.get("security", {})
        if not isinstance(security_data, dict):
            security_data = {}

        auth_data = data.get("auth", {})
        if not isinstance(auth_data, dict):
            auth_data = {}

        routing_data = data.get("routing", {})
        if not isinstance(routing_data, dict):
            routing_data = {}
        routing_weights_data = routing_data.get("weights", {})
        if not isinstance(routing_weights_data, dict):
            routing_weights_data = {}
        guardrails_data = data.get("guardrails", {})
        if not isinstance(guardrails_data, dict):
            guardrails_data = {}
        voice_data = data.get("voice", {})
        if not isinstance(voice_data, dict):
            voice_data = {}
        resilience_data = data.get("resilience", {})
        if not isinstance(resilience_data, dict):
            resilience_data = {}
        _remediation_data = resilience_data.get("remediation", {})
        if not isinstance(_remediation_data, dict):
            _remediation_data = {}
        budgets_data = guardrails_data.get("budgets", {})
        if not isinstance(budgets_data, dict):
            budgets_data = {}
        breaker_data = guardrails_data.get("breaker", {})
        if not isinstance(breaker_data, dict):
            breaker_data = {}
        autonomy_data = guardrails_data.get("autonomy", {})
        if not isinstance(autonomy_data, dict):
            autonomy_data = {}

        # Parse agents section into dict[str, AgentProfile]
        raw_agents = data.get("agents", {})
        agents: dict[str, AgentProfile] = {}
        if isinstance(raw_agents, dict):
            for name, entry in raw_agents.items():
                if isinstance(entry, dict):
                    agents[name] = AgentProfile(
                        provider=entry.get("provider", ""),
                        provider_agent=entry.get("provider_agent", ""),
                        acp_mode=entry.get("acp_mode", ""),
                        default_dir=entry.get("default_dir", ""),
                        memory_store=entry.get("memory_store", ""),
                        description=entry.get("description", ""),
                        system_prompt=entry.get("system_prompt", ""),
                        # Voice layer (#42) — MUST be read here (S6 loader-allowlist
                        # gotcha) or it's dropped on every config reload.
                        voice=entry.get("voice", ""),
                        model=entry.get("model", ""),
                        approval_mode=entry.get("approval_mode", ""),
                        skills=entry.get("skills", []),
                        tools=entry.get("tools", []),
                        # Renamed hooks→triggers (P4b). Migrate the legacy key on
                        # read so an existing personalclaw.json keeps its scoped
                        # lifecycle triggers; the write side only emits ``triggers``.
                        triggers=entry.get("triggers", entry.get("hooks", [])) or [],
                        source=entry.get("source", "personalclaw"),
                        # Agent routing metadata (AGENT-ROUTING S1) — MUST be read
                        # here (the loader-allowlist gotcha) or dropped on reload.
                        specialty=entry.get("specialty", ""),
                        route_hints=entry.get("route_hints", ""),
                    )

        # Parse memory_stores; synthesize default if missing
        raw_stores = data.get("memory_stores", {})
        memory_stores: dict[str, MemoryStoreConfig] = {}
        if isinstance(raw_stores, dict) and raw_stores:
            for name, entry in raw_stores.items():
                if isinstance(entry, dict):
                    memory_stores[name] = MemoryStoreConfig(
                        description=entry.get("description", ""),
                    )
        if not memory_stores:
            memory_stores["default"] = MemoryStoreConfig()

        # Parse top-level default_agent. The default agent is a single top-level
        # field; older config.json files also carried a hand-synced nested
        # ``agent.default_agent`` — migrate it (top-level wins; fall back to the
        # nested value for old files). The nested field is not re-read elsewhere
        # and is dropped on the next save (to_dict).
        default_agent_val = data.get("default_agent", "")
        if not isinstance(default_agent_val, str):
            default_agent_val = ""
        if not default_agent_val:
            _nested = agent_data.get("default_agent", "")
            if isinstance(_nested, str):
                default_agent_val = _nested

        cfg = cls(
            agent=AgentConfig(
                approval_mode=agent_data.get("approval_mode", "auto"),
                # Parse default is the in-process native loop (matches
                # AgentConfig.provider's field default). A config with no explicit
                # agent.provider is native, NOT the legacy "acp" — ACP is opt-in.
                provider=agent_data.get("provider", "native"),
                sandbox=agent_data.get("sandbox", "auto"),
                yolo=agent_data.get("yolo", False),
                acp_concurrent_sessions=agent_data.get("acp_concurrent_sessions", False),
                # Defaults ON (PROMPT-CACHE-SUBSTRATE §C6): caching is semantically
                # transparent — the model sees the same tokens either way and every
                # provider path degrades to a byte-identical no-op — so opt-out is the
                # honest default for a pure cost optimisation.
                prompt_cache_enabled=bool(agent_data.get("prompt_cache_enabled", True)),
                # Renamed conductor_skill → orchestrator_skill (2026-07). Back-read
                # the legacy key so a pre-rename install keeps the feature enabled;
                # the new key wins when both are present. Re-serialized under the new
                # name on the next save (to_dict uses the dataclass field).
                orchestrator_skill=agent_data.get(
                    "orchestrator_skill", agent_data.get("conductor_skill", False)
                ),
                max_subagents=agent_data.get("max_subagents", 3),
                spawn_min_memory_gb=float(agent_data.get("spawn_min_memory_gb", 4.0)),
                subagent_max_turns=agent_data.get("subagent_max_turns", 100),
                subagent_timeout_secs=agent_data.get("subagent_timeout_secs", 1800),
                subagent_cwd_allowed_roots=list(
                    agent_data.get("subagent_cwd_allowed_roots", ["~/workspace", "~/workplace"])
                ),
                log_level=agent_data.get("log_level", "WARNING").upper(),
                bot_name=_sanitize_bot_name(agent_data.get("bot_name", "")),
                soft_stop_budget_secs=max(
                    0.5, min(60.0, float(agent_data.get("soft_stop_budget_secs", 10.0)))
                ),
                # EXECUTION-ISOLATION §3.2 (EI-5). Read plainly, defaulting OFF: this
                # gate REFUSES work, so a `_guard_flag`-style fail-ON read would start
                # blocking every existing install's unattended ACP runs on upgrade.
                unattended_requires_verified_adapter=bool(
                    agent_data.get("unattended_requires_verified_adapter", False)
                ),
                # Clamped to the same [60, 86400] window ``_EDITABLE_CONFIG`` enforces
                # on the PATCH path, so a hand-edited config.json cannot express a
                # staleness window the dashboard would refuse to save.
                runner_health_check_secs=max(
                    60, min(86_400, int(agent_data.get("runner_health_check_secs", 3600)))
                ),
            ),
            session=SessionConfig(
                timeout_secs=session_data.get("timeout_secs", DEFAULT_SESSION_TIMEOUT),
                autocompact_pct=float(session_data.get("autocompact_pct", 90.0)),
                pool_size=int(session_data.get("pool_size", 0)),
                pool_agent=str(session_data.get("pool_agent", "")),
                pool_ttl_secs=int(session_data.get("pool_ttl_secs", 1800)),
                auto_archive_days=_safe_int(session_data.get("auto_archive_days"), 30),
            ),
            loops=LoopsConfig(
                max_cycles_hard_cap=loops_data.get("max_cycles_hard_cap", 100),
                default_idle_secs=loops_data.get("default_idle_secs", 120),
                trust_ttl_secs=loops_data.get("trust_ttl_secs", 24 * 3600),
                judge_use_case=_judge_axis(loops_data.get("judge_use_case", "reasoning")),
                stagnation_window=_stagnation_window(loops_data.get("stagnation_window", 5)),
                check_work_stages=bool(loops_data.get("check_work_stages", False)),
                worktree_sparse=bool(loops_data.get("worktree_sparse", True)),
            ),
            memory=MemoryConfig(
                semantic_confidence_threshold=memory_data.get("semantic_confidence_threshold", 0.8),
                episodic_dedup_threshold=memory_data.get("episodic_dedup_threshold", 0.88),
                episodic_max_results=memory_data.get("episodic_max_results", 8),
                episodic_max_count=memory_data.get("episodic_max_count", 10_000),
                semantic_keys=memory_data.get("semantic_keys", []),
                history_idle_hours=memory_data.get("history_idle_hours", 3.0),
                history_max_days=memory_data.get("history_max_days", 365),
                migrated=memory_data.get("migrated", False),
                # Behavior + injection flags — were silently dropped on load (the
                # explicit mapping omitted them), so a saved toggle never took
                # effect and always read its dataclass default. Map them through.
                l1_manifest=memory_data.get("l1_manifest", True),
                active_recall=memory_data.get("active_recall", True),
                active_recall_timeout_ms=memory_data.get("active_recall_timeout_ms", 1500),
                proactive_commitments=memory_data.get("proactive_commitments", False),
                proactive_commitments_max_per_day=memory_data.get(
                    "proactive_commitments_max_per_day", 3
                ),
                auto_promote_enabled=memory_data.get("auto_promote_enabled", True),
                auto_promote_every_n=memory_data.get("auto_promote_every_n", 10),
                auto_promote_max_per_run=memory_data.get("auto_promote_max_per_run", 5),
                # Readable vault (§5.1) — same map-it-through discipline as the behavior
                # flags above, else a saved setting reads its default. The mode
                # back-reads the retired `vault_enabled` bool; see `_vault_mode`.
                vault_mode=_vault_mode(memory_data),
                vault_path=memory_data.get("vault_path", "memory-vault"),
                graph_enabled=_guard_flag(memory_data.get("graph_enabled")),
                # Opt-in, so a plain read defaulting False — NOT `_guard_flag`, which
                # fails ON and would silently enable volunteering for every existing
                # user on upgrade. Same shape as `vault_mode` above, which defaults to
                # "off". `_expose_flag` is reserved for flags that open a network
                # surface; this one doesn't.
                push_context=bool(memory_data.get("push_context", False)),
                push_min_confidence=max(
                    0.0, min(1.0, float(memory_data.get("push_min_confidence", 0.7) or 0.7))
                ),
                # MEMORY-GRAPH-AND-VAULT §2.4 / §4.2 (MGAV-5). Both opt-in and both read
                # plainly, NOT through `_guard_flag`: a flag that fails ON would turn on
                # context spend (topology) and change how every fact renders (attribution)
                # for existing users on upgrade, which is the opposite of a safe default.
                graph_topology_in_context=bool(memory_data.get("graph_topology_in_context", False)),
                holder_attribution=bool(memory_data.get("holder_attribution", False)),
                # MGAV-9 — the Slots block budget. Read plainly and NOT clamped here: the
                # clamp lives in `memory_slots.resolve_block_limit`, at the one place the
                # value is consumed, so a hand-edited config.json cannot widen the block by
                # going around this loader (a CLI or a test that builds MemoryConfig
                # directly reaches the same ceiling).
                slot_size_cap=int(memory_data.get("slot_size_cap", 1400) or 1400),
            ),
            dashboard=DashboardConfig(
                url=dashboard_data.get("url", ""),
                public_url=str(dashboard_data.get("public_url", "") or ""),
                trusted_proxies=[
                    str(p)
                    for p in (dashboard_data.get("trusted_proxies", []) or [])
                    if isinstance(p, str) and str(p).strip()
                ],
                restore_sessions=dashboard_data.get("restore_sessions", False),
                restore_window_minutes=dashboard_data.get("restore_window_minutes", 30),
                user_name=dashboard_data.get("user_name", ""),
                # Normalized on READ as well as write, so a hand-edited config.json
                # can't introduce a non-canonical handle that then lands in records.
                username=_slug_username(dashboard_data.get("username", "")),
                merge_queued_messages=dashboard_data.get("merge_queued_messages", False),
                auto_tag_sessions=dashboard_data.get("auto_tag_sessions", True),
                mcp_probe_timeout_secs=_safe_int(
                    dashboard_data.get("mcp_probe_timeout_secs", 15), 15
                ),
                widget_density=dashboard_data.get("widget_density", "more"),
                send_on_enter=dashboard_data.get("send_on_enter", True),
                show_timestamps=dashboard_data.get("show_timestamps", False),
                show_thinking_inline=dashboard_data.get("show_thinking_inline", False),
                simplified_tool_names=dashboard_data.get("simplified_tool_names", False),
                followup_chips=dashboard_data.get("followup_chips", True),
                offer_check_work=bool(dashboard_data.get("offer_check_work", True)),
                stream_reveal=dashboard_data.get("stream_reveal", "smooth"),
                confirm_close_session=dashboard_data.get("confirm_close_session", False),
                auto_open_browser=dashboard_data.get("auto_open_browser", True),
                update_dev_mode=dashboard_data.get("update_dev_mode", False),
                # Opt-in, read with a False default: a config.json that is missing the
                # key (every existing install) must NOT arrive with screen capture
                # available. `bool()` so a truthy-string hand-edit can't smuggle a
                # non-bool into the gate the route reads.
                screen_share_enabled=bool(dashboard_data.get("screen_share_enabled", False)),
                terminal=dashboard_data.get("terminal", {"enabled": True}),
                dashboard_layout=dashboard_data.get("dashboard_layout", {}) or {},
            ),
            legibility=LegibilityConfig(
                discover_tips=bool(legibility_data.get("discover_tips", True)),
                context_adapters=bool(legibility_data.get("context_adapters", False)),
            ),
            ambient=AmbientConfig(
                tiles_enabled=bool(ambient_data.get("tiles_enabled", True)),
                max_tiles=_safe_int(ambient_data.get("max_tiles"), 12),
                default_refresh_ttl_secs=_safe_int(
                    ambient_data.get("default_refresh_ttl_secs"), 900
                ),
                genui_enabled=bool(ambient_data.get("genui_enabled", True)),
                surfaces_max_layer=_safe_int(ambient_data.get("surfaces_max_layer"), 2),
                # Opt-in, macOS-only: a plain read defaulting False — a tray that
                # turned itself on when config is unreadable would spawn a native
                # process unexpectedly.
                tray_enabled=bool(ambient_data.get("tray_enabled", False)),
            ),
            companion=CompanionConfig(
                # Opt-in: a plain read defaulting False — a gateway that advertised
                # itself on the LAN when config is unreadable would announce a service
                # the user never asked to expose.
                discovery_enabled=bool(companion_data.get("discovery_enabled", False)),
                instance_name=str(companion_data.get("instance_name", "") or ""),
            ),
            local_models=LocalModelsConfig(
                # Clamped to a real percentage: a threshold of 0 would warn permanently
                # and one above 100 could never warn, and both read as "the bar is broken".
                pressure_warn_pct=min(
                    100, max(1, _safe_int(local_models_data.get("pressure_warn_pct"), 85))
                ),
                # 0 is a coherent choice ("never respawn"); negative is not.
                sidecar_restart_max=max(
                    0, _safe_int(local_models_data.get("sidecar_restart_max"), 3)
                ),
            ),
            sources=SourcesConfig(
                enabled=bool(sources_data.get("enabled", True)),
                poll_interval_default_secs=_safe_int(
                    sources_data.get("poll_interval_default_secs"), 3600
                ),
                network_floor_secs=_safe_int(sources_data.get("network_floor_secs"), 900),
                max_sources=_safe_int(sources_data.get("max_sources"), 100),
                max_items_per_poll=_safe_int(sources_data.get("max_items_per_poll"), 50),
                daily_request_budget=_safe_int(sources_data.get("daily_request_budget"), 288),
            ),
            packs=PacksConfig(
                skill_catalogs=[
                    SkillCatalogConfig(
                        name=str(c.get("name", "")),
                        url=str(c.get("url", "")),
                        kind=str(c.get("kind", "index") or "index"),
                    )
                    for c in packs_data.get("skill_catalogs", [])
                    if isinstance(c, dict) and str(c.get("url", "")).strip()
                ],
                # Guard polarity (§5): the fingerprint surface only ever PROPOSES, so an
                # unreadable value must not silently disable it — missing/garbage ⇒ ON.
                fingerprint_enabled=_guard_flag(packs_data.get("fingerprint_enabled")),
                connector_catalog_url=str(packs_data.get("connector_catalog_url", "") or ""),
            ),
            apps=AppsConfig(
                # No _guard_flag/_expose_flag here, and that is measured, not lazy: the
                # schema type-gate earlier in load() has ALREADY replaced any non-bool at
                # this path with the field's dataclass default (`_apply_field_default`, the
                # "using default" warning), so by the time this line runs the value is a
                # real bool or the key is absent. A polarity helper could only ever see a
                # bool — a branch that cannot run is worse than no branch.
                #
                # The consequence, stated plainly because this flag's True is what adds a
                # default NETWORK source: a corrupted value resolves to the SHIPPED default
                # (registry on), not to off. That is the platform-wide config policy, not a
                # decision of this field — and the resulting source is visible in the Store
                # and removable there.
                registry_source_enabled=bool(apps_data.get("registry_source_enabled", True)),
            ),
            hooks=data.get("hooks", {}),
            agents=agents,
            default_agent=default_agent_val,
            memory_stores=memory_stores,
            auto_update=data.get("auto_update", True),
            timezone=data.get("timezone", ""),
            snapshot_dir=data.get("snapshot_dir", ""),
            durability=DurabilityConfig(
                # Guard polarity: losing scheduled backups because a value was
                # unreadable is the failure this whole plan exists to prevent.
                auto_backup=_guard_flag(durability_data.get("auto_backup")),
                keep_daily=_safe_int(durability_data.get("keep_daily"), 14),
                keep_weekly=_safe_int(durability_data.get("keep_weekly"), 8),
                keep_monthly=_safe_int(durability_data.get("keep_monthly"), 12),
                restore_drills=_guard_flag(durability_data.get("restore_drills")),
                # Time-travel is fail-OPEN like the backups above and for the same
                # reason: it is a purely local, secret-excluding history, so the risk
                # of it running when config is unreadable is a few git commits, while
                # the risk of it NOT running is the unrecoverable edit this plan exists
                # to prevent.
                time_travel=_guard_flag(durability_data.get("time_travel")),
                # Sync is fail-CLOSED (unlike backups): a sync surface that turns itself
                # on when config is unreadable would move data off-box unexpectedly, so a
                # missing/garbage value reads False, not True.
                sync_enabled=bool(durability_data.get("sync_enabled", False)),
                sync_transport=str(durability_data.get("sync_transport", "") or ""),
                sync_stale_after_secs=_safe_int(durability_data.get("sync_stale_after_secs"), 900),
                # Encryption is fail-CLOSED in the same spirit: an unreadable or off-scale
                # value falls back to "auto" (per-transport default, ON for third-party
                # storage), never to "off". A typo must not silently disable a security
                # control on a path that moves the whole home off-machine.
                sync_encrypt=_safe_choice(
                    durability_data.get("sync_encrypt", "auto"), ("auto", "on", "off"), "auto"
                ),
            ),
            proactive=ProactiveConfig(
                # Both switches are fail-closed: an unreadable value reads False, so a
                # corrupt config can never start collecting or acting on its own.
                triage_enabled=bool(proactive_data.get("triage_enabled", False)),
                digest_schedule=str(proactive_data.get("digest_schedule", "") or "0 8 * * *"),
                auto_execute_enabled=bool(proactive_data.get("auto_execute_enabled", False)),
                max_auto_actions_per_run=_safe_int(
                    proactive_data.get("max_auto_actions_per_run"), 5
                ),
                # The gate is the spend floor, so it fails OPEN (on) — an unreadable
                # value must not silently send every collected item to the model.
                classifier_gate_enabled=bool(proactive_data.get("classifier_gate_enabled", True)),
                decision_default_horizon_days=_safe_int(
                    proactive_data.get("decision_default_horizon_days"), 90
                ),
            ),
            evals=EvalsConfig(
                enabled=bool(evals_data.get("enabled", False)),
                study_default_k=_safe_int(evals_data.get("study_default_k"), 5),
                judge_agreement_floor=float(evals_data.get("judge_agreement_floor", 0.6) or 0.6),
                ablation_cadence_days=_safe_int(evals_data.get("ablation_cadence_days"), 30),
                bakeoff_capture_enabled=bool(evals_data.get("bakeoff_capture_enabled", False)),
                default_budget_usd=float(evals_data.get("default_budget_usd", 0.0) or 0.0),
            ),
            inbox=InboxConfig(
                enabled=bool(inbox_data.get("enabled", False)),
                user_id=str(inbox_data.get("user_id", "")),
                watched_channels=[
                    str(c) for c in inbox_data.get("watched_channels", []) if isinstance(c, str)
                ],
                poll_interval_seconds=max(30, int(inbox_data.get("poll_interval_seconds", 60))),
                style_rules=[
                    str(r) for r in inbox_data.get("style_rules", []) if isinstance(r, str)
                ],
                test_mode=bool(inbox_data.get("test_mode", False)),
                engagement_ranking_enabled=bool(
                    inbox_data.get("engagement_ranking_enabled", False)
                ),
                engagement_half_life_days=float(
                    inbox_data.get("engagement_half_life_days", 0.0) or 0.0
                ),
            ),
            tools=ToolsConfig(
                projection_rules=[
                    ProjectionRuleConfig(
                        name=str(r.get("name", "")),
                        match_regex=str(r.get("match_regex", "")),
                        strategy=str(r.get("strategy", "log")),
                        head=int(r.get("head", 0) or 0),
                        tail=int(r.get("tail", 0) or 0),
                        keep=str(r.get("keep", "")),
                        skip=str(r.get("skip", "")),
                        count=str(r.get("count", "")),
                    )
                    for r in tools_data.get("projection_rules", [])
                    if isinstance(r, dict) and str(r.get("match_regex", "")).strip()
                ],
                bg_compress_enabled=bool(tools_data.get("bg_compress_enabled", True)),
                bg_compress_idle_days=float(tools_data.get("bg_compress_idle_days", 7.0)),
                groups_enabled=bool(tools_data.get("groups_enabled", False)),
                group_defaults={
                    str(k): [str(g) for g in v if isinstance(g, str)]
                    for k, v in (tools_data.get("group_defaults") or {}).items()
                    if isinstance(k, str) and isinstance(v, list)
                },
            ),
            feedback=FeedbackConfig(
                enabled=bool(feedback_data.get("enabled", True)),
                retire_threshold=float(feedback_data.get("retire_threshold", 0.4)),
                min_n=int(feedback_data.get("min_n", 5)),
                window_days=int(feedback_data.get("window_days", 90)),
            ),
            inbound=InboundConfig(
                # Fail-CLOSED via `_expose_flag`: only an explicit true-spelling opens
                # the surface. Plain `bool()` would read the string "false" as True.
                mcp=InboundSurfaceConfig(
                    enabled=_expose_flag((inbound_data.get("mcp") or {}).get("enabled")),
                    allow_remote=_expose_flag((inbound_data.get("mcp") or {}).get("allow_remote")),
                ),
                public_url=str(inbound_data.get("public_url", "") or ""),
            ),
            agents_routing=AgentsRoutingConfig(
                enabled=bool(agents_routing_data.get("enabled", True)),
                min_confidence=float(agents_routing_data.get("min_confidence", 0.62)),
                cooldown_hours=float(agents_routing_data.get("cooldown_hours", 24.0)),
            ),
            planning=PlanningConfig(
                scratchpad_path=str(planning_data.get("scratchpad_path", "") or ""),
            ),
            skills=SkillsConfig(
                max_triggered=int(skills_data.get("max_triggered", 3)),
                auto_create_from_sessions=bool(skills_data.get("auto_create_from_sessions", False)),
                auto_refine_on_deviation=bool(skills_data.get("auto_refine_on_deviation", False)),
                auto_min_tool_calls=int(skills_data.get("auto_min_tool_calls", 5)),
                auto_similarity_threshold=float(skills_data.get("auto_similarity_threshold", 0.85)),
                progressive_disclosure_threshold=int(
                    skills_data.get("progressive_disclosure_threshold", 8)
                ),
            ),
            workflows=WorkflowsConfig(
                enabled=bool(workflows_data.get("enabled", True)),
                max_active_runs=_safe_int(workflows_data.get("max_active_runs", 10), 10),
                max_concurrent_nodes=_safe_int(workflows_data.get("max_concurrent_nodes", 6), 6),
                default_node_timeout_total_secs=_safe_int(
                    workflows_data.get("default_node_timeout_total_secs", 900), 900
                ),
                default_node_timeout_stall_secs=_safe_int(
                    workflows_data.get("default_node_timeout_stall_secs", 300), 300
                ),
                retention_per_def=_safe_int(workflows_data.get("retention_per_def", 100), 100),
                max_concurrent_llm_nodes=_safe_int(
                    workflows_data.get("max_concurrent_llm_nodes", 4), 4
                ),
                max_concurrent_io_nodes=_safe_int(
                    workflows_data.get("max_concurrent_io_nodes", 2), 2
                ),
                model_tier_reasoning=str(
                    workflows_data.get("model_tier_reasoning", "reasoning") or "reasoning"
                ),
                model_tier_standard=str(
                    workflows_data.get("model_tier_standard", "orchestration") or "orchestration"
                ),
                model_tier_fast=str(
                    workflows_data.get("model_tier_fast", "background") or "background"
                ),
                match_threshold=max(
                    0.0, min(1.0, float(workflows_data.get("match_threshold", 0.62) or 0.62))
                ),
                surface_mode_default=_surface_mode_default(
                    workflows_data.get("surface_mode_default")
                ),
                max_materialized_per_foreach=_safe_int(
                    workflows_data.get("max_materialized_per_foreach", 20), 20
                ),
                confirmation_ttl_secs=_safe_int(
                    workflows_data.get("confirmation_ttl_secs", 7 * 24 * 3600), 7 * 24 * 3600
                ),
                lease_ttl_secs=_safe_int(workflows_data.get("lease_ttl_secs", 900), 900),
                default_quiet_windows=str(
                    workflows_data.get("default_quiet_windows", "") or ""
                ).strip(),
                duty_gate_default=str(workflows_data.get("duty_gate_default", "") or "").strip(),
                workspace_default_mode=_workspace_default_mode(
                    workflows_data.get("workspace_default_mode")
                ),
                workspace_teardown_on_expiry=bool(
                    workflows_data.get("workspace_teardown_on_expiry", True)
                ),
            ),
            learning=LearningConfig(
                enabled=bool(learning_data.get("enabled", True)),
                min_tool_calls=int(learning_data.get("min_tool_calls", 4)),
                correction_heuristic=bool(learning_data.get("correction_heuristic", True)),
                surface_chip=bool(learning_data.get("surface_chip", True)),
                skill_ladder=bool(learning_data.get("skill_ladder", True)),
                min_evidence=int(learning_data.get("min_evidence", 3) or 3),
                staging_enabled=bool(learning_data.get("staging_enabled", True)),
                self_model_enabled=bool(learning_data.get("self_model_enabled", True)),
                min_session_score=float(learning_data.get("min_session_score", 0.0) or 0.0),
                context_budget_tokens=int(learning_data.get("context_budget_tokens", 4000) or 4000),
                curator_enabled=bool(learning_data.get("curator_enabled", True)),
                propose_quota_per_run=int(learning_data.get("propose_quota_per_run", 5) or 5),
                run_end_enabled=bool(learning_data.get("run_end_enabled", True)),
                attribution_enabled=bool(learning_data.get("attribution_enabled", True)),
            ),
            knowledge=KnowledgeConfig(
                idempotent_persist=bool(knowledge_data.get("idempotent_persist", True)),
                require_citations=bool(knowledge_data.get("require_citations", True)),
                report_budget_chars=int(knowledge_data.get("report_budget_chars", 40000) or 40000),
                default_ttl=str(knowledge_data.get("default_ttl", "") or ""),
                max_mentions_per_claim=int(knowledge_data.get("max_mentions_per_claim", 20) or 20),
                synthesis_window=int(knowledge_data.get("synthesis_window", 20) or 20),
                lint_every_n_persists=int(knowledge_data.get("lint_every_n_persists", 12) or 12),
                embed_batch_size=int(knowledge_data.get("embed_batch_size", 32) or 32),
                embed_retry_budget=int(knowledge_data.get("embed_retry_budget", 3) or 3),
                maintenance_max_staleness_secs=int(
                    knowledge_data.get("maintenance_max_staleness_secs", 900) or 900
                ),
                consolidate_min_cluster=int(knowledge_data.get("consolidate_min_cluster", 5) or 5),
                consolidate_min_hours=int(knowledge_data.get("consolidate_min_hours", 6) or 6),
                session_brief_max_tokens=int(
                    knowledge_data.get("session_brief_max_tokens", 800) or 800
                ),
                conflict_model_pass=bool(knowledge_data.get("conflict_model_pass", True)),
                auto_ingest_artifacts=bool(knowledge_data.get("auto_ingest_artifacts", True)),
            ),
            security=SecurityConfig(
                denied_commands=[
                    str(p) for p in security_data.get("denied_commands", []) if isinstance(p, str)
                ],
                egress=EgressConfig(
                    allow_hosts=[
                        str(h)
                        for h in (security_data.get("egress", {}) or {}).get("allow_hosts", [])
                        if isinstance(h, str)
                    ],
                    deny_hosts=[
                        str(h)
                        for h in (security_data.get("egress", {}) or {}).get("deny_hosts", [])
                        if isinstance(h, str)
                    ],
                    allow_private=bool(
                        (security_data.get("egress", {}) or {}).get("allow_private", False)
                    ),
                ),
                autonomy_denylist=[
                    d
                    for d in (security_data.get("autonomy_denylist", []) or [])
                    if isinstance(d, dict)
                ],
            ),
            auth=AuthConfigSection(
                login_enabled=bool(auth_data.get("login_enabled", False)),
                session_ttl=str(auth_data.get("session_ttl", "30d") or "30d"),
                require_totp=bool(auth_data.get("require_totp", False)),
                # Clamped, not rejected: a hand-edited 0 would mean "lock out on the zeroth
                # failure", i.e. nobody can ever log in. Floor at 1, and `_safe_int` so a
                # non-numeric typo falls back to the default instead of raising out of
                # load() — a config file that cannot be parsed is a bricked gateway.
                lockout_threshold=max(1, _safe_int(auth_data.get("lockout_threshold", 5), 5)),
                lockout_window=str(auth_data.get("lockout_window", "15m") or "15m"),
            ),
            # Routing (MODEL-ROUTING-TELEMETRY §7 wiring point (b)): explicit field-by-field
            # mapping — an omission here is a silently dropped setting, which is why the
            # round-trip test exists. Every number is floored so a typo degrades to something
            # workable instead of, say, a zero timeout that fails every local attempt.
            routing=RoutingConfig(
                enabled=bool(routing_data.get("enabled", False)),
                local_timeout_secs=max(
                    0.0, _safe_float(routing_data.get("local_timeout_secs", 20.0), 20.0)
                ),
                min_samples=max(1, _safe_int(routing_data.get("min_samples", 5), 5)),
                weights=RoutingWeightsConfig(
                    success=max(0.0, _safe_float(routing_weights_data.get("success", 0.60), 0.60)),
                    feedback=max(
                        0.0, _safe_float(routing_weights_data.get("feedback", 0.40), 0.40)
                    ),
                ),
                hysteresis=max(0.0, _safe_float(routing_data.get("hysteresis", 0.05), 0.05)),
                cloud_quality_margin=max(
                    0.0, _safe_float(routing_data.get("cloud_quality_margin", 0.10), 0.10)
                ),
                energy_sampling=bool(routing_data.get("energy_sampling", False)),
                reproposal_cooldown_days=max(
                    0, _safe_int(routing_data.get("reproposal_cooldown_days", 14), 14)
                ),
            ),
            guardrails=GuardrailsConfig(
                budgets=BudgetConfig(
                    max_tokens_per_run=max(0, int(budgets_data.get("max_tokens_per_run", 0))),
                    max_tokens_per_day=max(0, int(budgets_data.get("max_tokens_per_day", 0))),
                    max_dollars_per_day=max(
                        0.0, float(budgets_data.get("max_dollars_per_day", 0.0))
                    ),
                ),
                breaker=BreakerConfig(
                    failure_threshold=max(1, int(breaker_data.get("failure_threshold", 5))),
                    recovery_secs=max(0.0, float(breaker_data.get("recovery_secs", 30.0))),
                ),
                # §5 rung-ladder thresholds. `_safe_int` + a floor on each, so a typo
                # cannot produce a bar of zero approvals (which would offer a promotion
                # to a type with no track record at all).
                autonomy=AutonomyConfig(
                    clean_approvals=max(1, _safe_int(autonomy_data.get("clean_approvals", 10), 10)),
                    min_days=max(0, _safe_int(autonomy_data.get("min_days", 7), 7)),
                    max_rejections=max(0, _safe_int(autonomy_data.get("max_rejections", 0), 0)),
                    cooldown_days=max(0, _safe_int(autonomy_data.get("cooldown_days", 14), 14)),
                    evidence_window_days=max(
                        1, _safe_int(autonomy_data.get("evidence_window_days", 30), 30)
                    ),
                ),
                scan_mode=(
                    str(guardrails_data.get("scan_mode", "redact"))
                    if guardrails_data.get("scan_mode", "redact") in ("warn", "redact", "block")
                    else "redact"
                ),
            ),
            voice=VoiceConfig(
                # Convenience knobs, not guards: an empty/malformed phrase list falls
                # back to the shipped defaults so hands-free mode stays operable, and
                # each boolean parses as a plain bool with its documented default.
                confirmation_phrases=(
                    _voice_phrases(voice_data.get("confirmation_phrases"))
                    or list(DEFAULT_CONFIRMATION_PHRASES)
                ),
                exit_phrases=(
                    _voice_phrases(voice_data.get("exit_phrases")) or list(DEFAULT_EXIT_PHRASES)
                ),
                # A blank or non-string chord falls back to the shipped default rather
                # than storing "" — an empty accelerator binds nothing, which would make
                # push-to-talk silently do nothing with no error anywhere. The GRAMMAR is
                # not second-guessed here: the shell refuses an unbindable chord with a
                # reason the Settings control shows, which is a better place to say so
                # than a config load that has no user watching it.
                push_to_talk_chord=(
                    str(voice_data.get("push_to_talk_chord") or "").strip()
                    or DEFAULT_PUSH_TO_TALK_CHORD
                ),
                echo_filter_enabled=bool(voice_data.get("echo_filter_enabled", True)),
                duplex_mute_enabled=bool(voice_data.get("duplex_mute_enabled", True)),
                clean_for_speech_enabled=bool(voice_data.get("clean_for_speech_enabled", True)),
                voice_disclaimer_enabled=bool(voice_data.get("voice_disclaimer_enabled", True)),
            ),
            resilience=ResilienceConfig(
                # Guard-class (§5): parse fail-safe — missing/unknown ⇒ enabled.
                doctor_enabled=_guard_flag(resilience_data.get("doctor_enabled")),
                degraded_indicator=_guard_flag(resilience_data.get("degraded_indicator")),
                mid_turn_policy=(
                    str(resilience_data.get("mid_turn_policy", "queue"))
                    if resilience_data.get("mid_turn_policy", "queue")
                    in ("queue", "steer", "cancel_and_replace")
                    else "queue"
                ),
                cancel_replace_min_interval_secs=max(
                    0.0, float(resilience_data.get("cancel_replace_min_interval_secs", 2.0))
                ),
                remediation=RemediationConfig(
                    enabled=_guard_flag(_remediation_data.get("enabled")),
                    target_score=max(0, min(100, int(_remediation_data.get("target_score", 90)))),
                    max_cost_usd=max(0.0, float(_remediation_data.get("max_cost_usd", 1.0))),
                    idle_minutes_healthy=max(
                        1, int(_remediation_data.get("idle_minutes_healthy", 60))
                    ),
                    tick_minutes_degraded=max(
                        1, int(_remediation_data.get("tick_minutes_degraded", 5))
                    ),
                ),
            ),
            sandbox=SandboxConfig(
                nofile=max(0, _safe_int(sandbox_data.get("nofile", 4096), 4096)),
                max_pids=max(0, _safe_int(sandbox_data.get("max_pids", 0), 0)),
                max_rss_mb=max(0, _safe_int(sandbox_data.get("max_rss_mb", 0), 0)),
                env_passthrough=[
                    str(n).strip()
                    for n in (sandbox_data.get("env_passthrough") or [])
                    if str(n).strip()
                ],
            ),
            checkpoints=CheckpointsConfig(
                enabled=bool(checkpoints_data.get("enabled", True)),
                max_mb=max(0, _safe_int(checkpoints_data.get("max_mb", 200), 200)),
                max_turns=max(1, _safe_int(checkpoints_data.get("max_turns", 50), 50)),
                max_file_mb=max(0, _safe_int(checkpoints_data.get("max_file_mb", 8), 8)),
            ),
            observe_max_messages=max(1, int(data.get("observe_max_messages", 200))),
            observe_ttl_hours=max(0.0, float(data.get("observe_ttl_hours", 168.0))),
        )

        # Write-back: ensure a default agent exists; back up the original and
        # save the canonical version.  One-shot — subsequent loads skip.
        try:
            needs_migration = False

            # The in-process native loop is the default runtime; ACP must be
            # opted into explicitly with an ``acp:<cli>`` provider. When the
            # global default is ``acp``, flip it to native and clear the
            # ``personalclaw`` modeId on empty-provider agents (which would
            # otherwise route them to an external CLI). Only applied to an
            # ``acp``-default config — an already-native config is left
            # untouched, since "personalclaw" may be a real ACP modeId there.
            if getattr(cfg.agent, "provider", "") == "acp":
                cfg.agent.provider = "native"
                needs_migration = True
                for _prof in (cfg.agents or {}).values():
                    if (
                        not getattr(_prof, "provider", "")
                        and getattr(_prof, "provider_agent", "") == "personalclaw"
                    ):
                        _prof.provider = "native"
                        _prof.provider_agent = ""

            # Create default agent when none exists. The default is the
            # in-process NATIVE PersonalClaw agent (governed by Settings →
            # Models) — no external CLI required for first-run chat. ACP agents
            # are created only when the user explicitly adds an acp:<cli> one.
            if not cfg.agents:
                from personalclaw.agents.defaults import (
                    DEFAULT_NATIVE_AGENT_NAME,
                    make_default_native_profile,
                )

                cfg.agents[DEFAULT_NATIVE_AGENT_NAME] = make_default_native_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in goal-loop worker if absent. Idempotent
            # (add-if-missing, never overwrite a user edit) so it ships with the
            # package whenever the gateway runs — inert until a loop invokes it.
            # Kept out of the `if not cfg.agents` block so existing configs gain
            # it on next load.
            from personalclaw.agents.defaults import (
                CODE_PLANNER_AGENT_NAME,
                CODER_AGENT_NAME,
                LITE_AGENT_NAME,
                LOOP_PLANNER_AGENT_NAME,
                LOOP_WORKER_AGENT_NAME,
                TEMPLATE_REFINER_AGENT_NAME,
                make_code_planner_profile,
                make_coder_profile,
                make_lite_agent_profile,
                make_loop_planner_profile,
                make_loop_worker_profile,
                make_template_refiner_profile,
            )

            if LOOP_WORKER_AGENT_NAME not in cfg.agents:
                cfg.agents[LOOP_WORKER_AGENT_NAME] = make_loop_worker_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in Code worker (the SDLC engine) if absent. Same
            # idempotent add-if-missing contract — ships with the package, inert
            # until a code project invokes it.
            if CODER_AGENT_NAME not in cfg.agents:
                cfg.agents[CODER_AGENT_NAME] = make_coder_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in Code DEEP PLANNER (agentic intake planner, C163) if
            # absent. Tool-equipped so it investigates real context before planning;
            # inert until a code project requests a deep plan.
            if CODE_PLANNER_AGENT_NAME not in cfg.agents:
                cfg.agents[CODE_PLANNER_AGENT_NAME] = make_code_planner_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in goal-planner (intake brain) if absent. Same
            # idempotent add-if-missing contract — ships with the package, inert
            # until intake invokes it.
            if LOOP_PLANNER_AGENT_NAME not in cfg.agents:
                cfg.agents[LOOP_PLANNER_AGENT_NAME] = make_loop_planner_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in lite background worker if absent. Same idempotent
            # add-if-missing contract as the loop worker — the background chores
            # (titles/suggestions/consolidation) resolve a real profile instead
            # of falling through to an unnamed default.
            if LITE_AGENT_NAME not in cfg.agents:
                cfg.agents[LITE_AGENT_NAME] = make_lite_agent_profile(AgentProfile)
                needs_migration = True

            # Seed the built-in propose-only template refiner (WF2LEA-6) if absent. Same
            # idempotent add-if-missing contract — ships with the package, inert until the
            # `refine-template` workflow runs it over a template's run ledger.
            if TEMPLATE_REFINER_AGENT_NAME not in cfg.agents:
                cfg.agents[TEMPLATE_REFINER_AGENT_NAME] = make_template_refiner_profile(
                    AgentProfile
                )
                needs_migration = True

            # Prune retired system agents left behind in an existing config.json.
            # These pre-rename system agents have no profile in source anymore, so an
            # orphaned key just resolves to nothing. Scoped to the reserved
            # `personalclaw-` namespace (RETIRED_AGENT_NAMES) so a user-created agent is
            # never touched. One-time: the key is gone after the first write-back.
            from personalclaw.agents.defaults import RETIRED_AGENT_NAMES

            for _retired in RETIRED_AGENT_NAMES & set(cfg.agents):
                del cfg.agents[_retired]
                logger.info("Config migration: pruned retired system agent %r", _retired)
                needs_migration = True

            if not cfg.default_agent or cfg.default_agent not in cfg.agents:
                # Prefer "default" if it exists, otherwise use first available agent
                if "default" in cfg.agents:
                    cfg.default_agent = "default"
                elif cfg.agents:
                    cfg.default_agent = next(iter(cfg.agents))
                else:
                    cfg.default_agent = "default"
                needs_migration = True

            if needs_migration:
                backup = path.with_suffix(".json.bak")
                import shutil

                shutil.copy2(path, backup)
                logger.info(
                    "Config migrated — backup saved to %s",
                    backup,
                )
                cfg.save()
        except Exception as e:
            # Migration write-back is best-effort; never block startup.
            logger.warning("Config write-back failed: %s", e)

        return cfg

    def to_dict(self) -> dict:
        """Serialize config to the JSON structure used by config.json."""
        from dataclasses import asdict

        d: dict = {
            "agent": asdict(self.agent),
            "sandbox": asdict(self.sandbox),
            "checkpoints": asdict(self.checkpoints),
            "session": asdict(self.session),
            "memory": asdict(self.memory),
            "dashboard": asdict(self.dashboard),
            "legibility": asdict(self.legibility),
            "ambient": asdict(self.ambient),
            "companion": asdict(self.companion),
            "local_models": asdict(self.local_models),
            "sources": asdict(self.sources),
            "packs": asdict(self.packs),
            "apps": asdict(self.apps),
            "hooks": self.hooks,
            "agents": {name: asdict(agent_cfg) for name, agent_cfg in self.agents.items()},
            "default_agent": self.default_agent,
            "memory_stores": {name: asdict(ms_cfg) for name, ms_cfg in self.memory_stores.items()},
            "inbox": asdict(self.inbox),
            "tools": asdict(self.tools),
            "feedback": asdict(self.feedback),
            "inbound": asdict(self.inbound),
            "agents_routing": asdict(self.agents_routing),
            "planning": asdict(self.planning),
            "loops": asdict(self.loops),
            "skills": asdict(self.skills),
            "workflows": asdict(self.workflows),
            "learning": asdict(self.learning),
            "knowledge": asdict(self.knowledge),
            "security": asdict(self.security),
            "auth": asdict(self.auth),
            "guardrails": asdict(self.guardrails),
            "routing": asdict(self.routing),
            "resilience": asdict(self.resilience),
            "voice": asdict(self.voice),
            "timezone": self.timezone,
            "auto_update": self.auto_update,
            "snapshot_dir": self.snapshot_dir,
            "durability": asdict(self.durability),
            "evals": asdict(self.evals),
            "proactive": asdict(self.proactive),
            # Channel-agnostic observe-buffer sizing — top-level keys (Slack config
            # lives in the slack-channel app's own store, not here).
            "observe_max_messages": self.observe_max_messages,
            "observe_ttl_hours": self.observe_ttl_hours,
        }
        return d

    def save(self) -> None:
        """Write current config to ~/.personalclaw/config.json.

        Stamps a ``meta`` block with the current version and timestamp
        so we can tell which build last touched the file.
        Preserves ``providers``/``use_cases`` blocks (and a legacy ``slack`` block
        awaiting the slack-channel app's one-time migration) from the existing file
        so opaque app-owned data is never lost on write-back.
        """
        from datetime import datetime, timezone

        from personalclaw import __version__

        meta = {
            "lastTouchedVersion": __version__,
            "lastTouchedAt": datetime.now(timezone.utc).isoformat(),
        }
        d = {"meta": meta, **self.to_dict()}
        # Preserve opaque blocks that live outside to_dict(). "slack" is
        # app-owned data core doesn't parse — kept intact until the channel app's
        # migrate_from_core() lifts it into the app store and deletes it.
        p = config_path()
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
                for key in ("providers", "use_cases", "slack"):
                    if key in existing:
                        d[key] = existing[key]
            except Exception:
                pass
        p.parent.mkdir(parents=True, exist_ok=True)
        from personalclaw.atomic_write import atomic_write

        atomic_write(p, json.dumps(d, indent=2) + "\n")

    def load_credentials(self) -> dict[str, str]:
        """Load every stored credential, backend-transparently, plus env overrides.

        Union of both credential backends (C1): ``.env`` (KEY=VALUE per line, ``#``
        comments, no quotes required, permissions repaired to 0600 on read) merged
        under the keychain, which wins on the key a partly-migrated install holds in
        both. Environment variables still override, as they always did.
        """
        creds: dict[str, str] = dict(_dotenv_credentials())
        creds.update(_keychain_credentials())

        for key in _CREDENTIAL_KEYS:
            val = os.environ.get(key)
            if val:
                creds[key] = val

        # Propagate credentials into the process environment so spawned children
        # (sandboxed agents, MCP servers, cron-fired subprocesses) inherit them
        # via Popen's default env=os.environ.copy() — even when their view of
        # ~/.personalclaw/.env is a bind-mounted empty file. setdefault() preserves
        # any value the caller already set explicitly.
        for k, v in creds.items():
            if v:
                os.environ.setdefault(k, v)

        return creds

    def create_provider_factory(self) -> Callable:
        """Return a factory that creates ModelProvider instances from config.

        Provider resolution is delegated to the provider bridge, which reads the
        active model selected for the ``chat`` use case from
        ``~/.personalclaw/active_models.json`` and resolves it from the
        configured providers (config.json ``providers[]``). All model providers
        (ollama, anthropic, openai, vllm, bedrock) flow through that registry;
        ACP is the agent-runtime backend.
        """
        from personalclaw.providers.provider_bridge import (
            create_provider_factory as _create_ext_factory,
        )

        return _create_ext_factory("chat")


# ---------------------------------------------------------------------------
# Agent resolver and provider_agent validation
# ---------------------------------------------------------------------------


def resolve_agent_bindings(
    config: AppConfig,
    agent_name: str | None = None,
) -> ResolvedBindings:
    """Resolve workspace, memory store, and provider agent for a session.

    Resolution:
    1. If agent_name is given and exists in config.agents → use its bindings
    2. Otherwise use config.default_agent (guaranteed to exist by load())
    """
    import dataclasses as _dc

    # Step 1: explicit agent_name
    if agent_name and agent_name in config.agents:
        agent_cfg = config.agents[agent_name]
    elif config.default_agent and config.default_agent in config.agents:
        # Step 2: default_agent (guaranteed valid by load())
        agent_cfg = config.agents[config.default_agent]
    elif config.agents:
        # Defensive: default_agent not in agents, use first available
        first_name = next(iter(config.agents))
        logger.warning(
            "default_agent '%s' not found in agents, using '%s'",
            config.default_agent,
            first_name,
        )
        agent_cfg = config.agents[first_name]
    else:
        # No agents at all — return safe defaults
        logger.warning("No agents configured, using bare defaults")
        return ResolvedBindings(
            workspace_dir=workspace_root(),
            memory_store_name="",
            effective_memory_config=_dc.asdict(config.memory),
            provider_agent=config.default_agent,
        )

    # Resolve the agent's default working directory: an explicit raw path if
    # set, otherwise the workspace root. Memory is scoped by this cwd downstream.
    ws_dir = Path(agent_cfg.default_dir) if agent_cfg.default_dir else workspace_root()

    # Resolve memory store (empty = filesystem fallback scoped by cwd).
    # An explicitly-named store that doesn't exist falls back to the filesystem
    # store rather than a phantom name.
    store_name = agent_cfg.memory_store
    if store_name and store_name not in config.memory_stores:
        logger.warning("Agent memory_store '%s' not found; using filesystem fallback", store_name)
        store_name = ""

    provider_agent = agent_cfg.provider_agent
    acp_mode = getattr(agent_cfg, "acp_mode", "")

    # Per-agent provider supersedes the global default; empty inherits it.
    provider = getattr(agent_cfg, "provider", "") or config.agent.provider

    # Build effective memory config via dict-level merge
    store_cfg = config.memory_stores.get(store_name)
    store_dict = _dc.asdict(store_cfg) if store_cfg else {}
    top_level_memory = _dc.asdict(config.memory)
    effective_memory = resolve_memory_store_config(top_level_memory, store_dict)

    return ResolvedBindings(
        workspace_dir=ws_dir,
        memory_store_name=store_name,
        effective_memory_config=effective_memory,
        provider_agent=provider_agent,
        acp_mode=acp_mode,
        system_prompt=_compose_voice(getattr(agent_cfg, "voice", ""), agent_cfg.system_prompt),
        tools=list(agent_cfg.tools or []),
        skills=list(agent_cfg.skills or []),
        approval_mode=agent_cfg.approval_mode,
        triggers=list(getattr(agent_cfg, "triggers", []) or []),
        provider=provider,
    )


def resolve_session_workspace(
    config: "AppConfig", agent_name: str | None, current: str = ""
) -> str:
    """The working directory a session should carry after binding *agent_name*.

    Implements ``AgentProfile.default_dir``'s declared contract verbatim — *"Empty
    inherits the workspace root. Overridable per-session."*:

    * a NON-EMPTY ``default_dir`` is the profile's own opinion and wins;
    * an EMPTY one INHERITS — so an explicit per-session ``current`` survives, and a
      session with none falls back to the resolved workspace root.

    ``resolve_agent_bindings().workspace_dir`` cannot express this on its own: it
    collapses both cases to a concrete path, so a caller assigning it unconditionally
    lets a profile that declared NO directory silently relocate a session the user
    had explicitly bound elsewhere — the G39 real-home escape, where the relocation
    also landed outside every configured home.
    """
    profile = (config.agents or {}).get(agent_name) if agent_name else None
    declared = str(getattr(profile, "default_dir", "") or "").strip() if profile else ""
    if declared:
        return declared
    return str(current or "").strip() or str(
        resolve_agent_bindings(config, agent_name).workspace_dir
    )
