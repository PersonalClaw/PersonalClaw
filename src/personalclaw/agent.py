"""PersonalClaw agent configuration.

Generates and installs ``personalclaw.json`` into ``~/.personalclaw/agents/``.

Configuration files (edit these, then ``personalclaw setup --agent-only``):

  ``personalclaw/config/defaults.json``
      Base agent config — tools, model, allowedTools, toolsSettings, etc.

  ``personalclaw/config/prompts/chat.md``
      Default (chat) system prompt — the prompt-provider seeds ``system-chat``
      from it, and it is the ultimate fallback when the provider can't resolve.

  ``~/.personalclaw/agent.json``
      User overrides merged on top of defaults (optional).

  ``~/.personalclaw/prompt.md``
      User prompt override (optional, takes priority over the shipped prompt).

Dynamic fields resolved at install time:
  - ``prompt`` — ``file://`` URI pointing to the prompt file
  - ``mcpServers.personalclaw-schedule.command`` — absolute path to ``personalclaw`` binary
"""

import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.security import is_sensitive_path, redact
from personalclaw.sel import (  # circular import: sel imports config which imports agent
    SecurityEvent,
    sel,
)

logger = logging.getLogger(__name__)


def _atomic_json_write(path: Path, data: dict) -> None:
    """Write JSON atomically via tmp+rename to prevent read-of-partial-file.

    ACP agent reads agent configs at spawn and set_mode.  Non-atomic writes
    (truncate-then-write) can deliver empty or partial JSON, crashing the
    ACP process with exit code 1.  rename() is atomic on Linux when source
    and destination are on the same filesystem.

    Uses mkstemp for a unique temp file per call so concurrent writers
    to the same path don't clobber each other's temp files.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except FileNotFoundError:
                mode = 0o644
            os.fchmod(f.fileno(), mode)
            json.dump(data, f, indent=2)
            f.write("\n")
        try:
            os.replace(tmp_name, path)
        except OSError:
            # Fallback for container bind mounts where rename fails with EBUSY
            import shutil
            shutil.copy2(tmp_name, path)
            os.unlink(tmp_name)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Honor PERSONALCLAW_HOME for AGENTS_DIR / _USER_MCP_JSON. config_dir() in
# personalclaw.config.loader respects the env var; using it here keeps a single
# source of truth so containerized deployments writing to /data don't end up
# splitting agent state between /data and ~/.personalclaw/.
def _user_dir() -> Path:
    from personalclaw.config.loader import config_dir as _cd
    return _cd()


AGENTS_DIR = _user_dir() / "agents"
AGENT_FILENAME = "personalclaw.json"
_USER_MCP_JSON = _user_dir() / "mcp.json"

# Bundled fallback — inside the personalclaw.config package
_BUNDLED_CFG_DIR = Path(__file__).resolve().parent / "config"


def _project_dir() -> Path | None:
    """Return the project root from PERSONALCLAW_PROJECT_DIR, or None."""
    val = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if val:
        p = Path(val)
        if p.is_dir():
            return p
    return None


def _shipped_defaults() -> Path:
    """Return defaults.json, preferring project-dir override for development."""
    proj = _project_dir()
    if proj:
        candidate = proj / "agents" / "defaults.json"
        if candidate.is_file():
            return candidate
    return _BUNDLED_CFG_DIR / "defaults.json"


def _shipped_prompt() -> Path:
    """Return the default (chat) system prompt source, preferring a project-dir
    override for development. This is the ultimate fallback when the prompt
    provider can't resolve a bound prompt; the bundled source is the same
    ``config/prompts/chat.md`` the provider seeds the ``system-chat`` prompt from."""
    proj = _project_dir()
    if proj:
        candidate = proj / "agents" / "prompt.md"
        if candidate.is_file():
            return candidate
    return _BUNDLED_CFG_DIR / "prompts" / "chat.md"


# User overrides — honor PERSONALCLAW_HOME via config_dir()
_USER_DIR = _user_dir()
_USER_PROMPT = _USER_DIR / "prompt.md"
_USER_OVERRIDES = _USER_DIR / "agent.json"

# personalclaw binary path — resolved lazily to handle gateway restarts
# where PATH may not include the virtualenv at import time.
_PERSONALCLAW_BIN: str | None = None

def _bin_is_usable(path: Path) -> bool:
    """Return False if the binary cannot run in the current environment.

    Currently a stub that only fails on unreadable files; callers also
    validate with ``is_file()`` and ``os.access(X_OK)`` separately.
    """
    try:
        with open(path, "rb") as f:
            f.read(1)
    except OSError:
        return False
    return True


def _resolve_personalclaw_bin() -> str:
    """Resolve the absolute path of the ``personalclaw`` executable.

    Resolution order (first existing + executable wins):

    1. Same install as the current process: walk up from ``personalclaw.__file__``
       looking for a ``bin/personalclaw`` sibling. Covers source-tree dev installs
       and venv-based installs whose bin/ sits above the package.
    2. Alongside the running interpreter: ``dirname(sys.executable)/personalclaw``.
       A console_script (``[project.scripts]``) is installed next to the venv's
       ``python``, so this is the canonical location — and it's the ONLY reliable
       one when the venv lives OUTSIDE the source tree (e.g. a repo-root ``.venv``
       with the package under ``PersonalClaw/src/``: the step-1 walk from
       ``src/personalclaw`` never crosses the sibling ``.venv/bin``). This gap
       dropped the ``personalclaw-schedule``/``-core`` MCP servers on every boot.
    3. ``shutil.which('personalclaw')`` — respects PATH order (when the venv bin
       IS on PATH).
    4. Bare ``"personalclaw"`` — last resort, may fail but surfaces the problem
       instead of caching a known-bad absolute path.

    Every candidate is validated with ``is_file()`` and ``os.access(X_OK)``
    before being returned, so stale paths from previous installs are skipped.
    """
    global _PERSONALCLAW_BIN
    if _PERSONALCLAW_BIN:
        return _PERSONALCLAW_BIN

    def _usable(p: str | Path) -> bool:
        sp = str(p)
        if not (sp and os.path.isfile(sp) and os.access(sp, os.X_OK)):
            return False
        return _bin_is_usable(Path(sp))

    # 1. Walk up from the running package to find bin/personalclaw
    try:
        # In-function import is intentional — importing backend at module
        # load time would be circular (personalclaw.agent is loaded *during*
        # the backend package initialization). Deferring it here resolves
        # after the package is fully loaded.
        import personalclaw as _mc  # noqa: PLC0415  circular import

        pkg_dir = Path(_mc.__file__).resolve().parent
        for parent in pkg_dir.parents:
            candidate = parent / "bin" / "personalclaw"
            if _usable(candidate):
                _PERSONALCLAW_BIN = str(candidate)
                return _PERSONALCLAW_BIN
            if (parent / "pyvenv.cfg").exists():
                break  # reached venv root without finding the binary
    except Exception:
        logger.debug("personalclaw bin walk failed", exc_info=True)

    # 2. Next to the running interpreter (the venv's Scripts/bin dir). Reliable
    #    when the venv is outside the source tree, which the step-1 walk misses.
    #    Do NOT resolve() sys.executable — the venv's ``python`` is a SYMLINK to the
    #    base interpreter (e.g. homebrew), and resolving it jumps OUT of the venv bin
    #    to a dir with no console scripts. Use the unresolved exe dir plus
    #    ``sys.prefix`` (the venv root) — both point at the venv's ``bin``/``Scripts``.
    try:
        bin_name = "Scripts" if os.name == "nt" else "bin"
        exe_dirs = [Path(sys.executable).parent, Path(sys.prefix) / bin_name]
        seen_dirs: set[str] = set()
        for exe_dir in exe_dirs:
            key = str(exe_dir)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            for candidate in (exe_dir / "personalclaw", exe_dir / "personalclaw.exe"):
                if _usable(candidate):
                    _PERSONALCLAW_BIN = str(candidate)
                    return _PERSONALCLAW_BIN
    except Exception:
        logger.debug("personalclaw interpreter-adjacent lookup failed", exc_info=True)

    # 3. PATH lookup (also validated)
    found = shutil.which("personalclaw")
    if found and _usable(found):
        _PERSONALCLAW_BIN = found
        return _PERSONALCLAW_BIN

    # 3. Last resort — don't cache, so a future call can retry
    logger.warning(
        "Could not resolve personalclaw binary to an existing file; "
        "falling back to bare 'personalclaw' (MCP probes may fail)"
    )
    return "personalclaw"


# ---------------------------------------------------------------------------
# Managed MCP servers — single source of truth.
#
# Every server here is dynamically injected into the agent config at install
# time (both fresh and existing configs).  Adding a new managed server =
# one entry here.
# ---------------------------------------------------------------------------
_MANAGED_MCP_SERVERS: dict[str, dict] = {
    "personalclaw-schedule": {"command_fn": _resolve_personalclaw_bin, "args": ["mcp-schedule"]},
    "personalclaw-core": {"command_fn": _resolve_personalclaw_bin, "args": ["mcp-core"]},
}


def _prompt_path(mode: str = "") -> Path:
    """Return user prompt if it exists, otherwise shipped prompt."""
    if _USER_PROMPT.is_file():
        return _USER_PROMPT
    return _shipped_prompt()


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file, returning ``{}`` on any error or non-dict root.

    ``~/.claude.json`` in particular is user-owned and could theoretically
    contain a top-level array after a hand-edit.  Normalizing to an empty
    dict here means every caller can safely do ``_load_json(p).get(key)``
    without an ``isinstance`` check at each call site.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring invalid %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring %s: top-level JSON is not an object", path)
        return {}
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge *override* into *base* (one level deep for dicts)."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = {**merged[key], **val}
        else:
            merged[key] = val
    return merged


def _all_skill_paths() -> list[str]:
    """Discover all skill directories.

    Returns directories containing SKILL.md files from (in priority order):
    - ``PERSONALCLAW_PROJECT_DIR/skills`` (project-level, highest priority)
    - ``~/.personalclaw/skills`` (user-created)
    - ``~/.agents/skills/`` (agentskills.io cross-client standard)
    - Package-bundled skills (lowest priority, always available)
    """
    paths: set[str] = set()
    # Project-level skills (highest priority — project overrides user)
    proj = _project_dir()
    if proj:
        sd = proj / "skills"
        if sd.is_dir():
            paths.add(str(sd))
    # User-created skills under the configured config dir (respects PERSONALCLAW_HOME).
    # Must match the path used by SkillsLoader (personalclaw/skills/loader.py) so
    # skills written by api_skills_create are visible to api_skills_list.
    from personalclaw.skills.loader import skills_dir as _user_skills_dir
    user_skills = _user_skills_dir()
    if user_skills.is_dir():
        paths.add(str(user_skills))
    # agentskills.io cross-client standard path (~/.agents/skills/)
    agents_skills = Path.home() / ".agents" / "skills"
    if agents_skills.is_dir():
        paths.add(str(agents_skills))
    # Package-bundled skills (always available as baseline)
    from personalclaw.skills.native import _bundled_root
    bundled = _bundled_root()
    if bundled.is_dir():
        paths.add(str(bundled))
    return sorted(paths)




def _inject_skill_paths(bm: dict, skill_paths: list[str]) -> None:
    """Strip existing --skill-paths from ACP agent args and inject valid ones."""
    args = list(bm.get("args", []))
    clean: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--skill-paths":
            skip = True
            continue
        clean.append(a)
    valid = [p for p in skill_paths if Path(p).is_dir()]
    if valid:
        clean.extend(["--skill-paths", ",".join(valid)])
    bm["args"] = clean


_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9/_.\-]+$")
_SAFE_MATCHER_RE = re.compile(r"^[a-zA-Z0-9_.*\-]+$")
_MAX_MATCHER_LEN = 200


def _validate_hook_command(command: str, event: str) -> str | None:
    """Validate a user-supplied hook command path.

    Returns the resolved absolute path if safe, or None on failure.
    Since config.json is LLM-writable, this guards against indirect
    command injection.  Uses an allowlist regex for path characters.
    """
    if not _SAFE_PATH_RE.match(command):
        logger.warning("agent_hooks[%s]: command contains disallowed characters: %r", event, command)
        return None
    if not os.path.isabs(command):
        logger.warning("agent_hooks[%s]: command must be absolute path, got %r", event, command)
        return None
    resolved = str(Path(command).resolve())
    if not _SAFE_PATH_RE.match(resolved):
        logger.warning(
            "agent_hooks[%s]: resolved path contains disallowed characters: %r", event, resolved
        )
        return None
    if is_sensitive_path(resolved):
        logger.warning(
            "agent_hooks[%s]: command points to sensitive path %r, skipping", event, command
        )
        return None
    if not os.path.isfile(resolved):
        logger.warning("agent_hooks[%s]: command not found: %s", event, command)
        return None
    return resolved


def _sel_hook_rejected(event: str, command: str, reason: str) -> None:
    """Emit a SEL audit event when a user hook entry is rejected."""
    try:
        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="config_hooks_merge",
                caller_identity="agent_install",
                agent="personalclaw",
                source="cli",
                operation="agent_hooks_rejected",
                outcome="rejected",
                resources=redact(f"event={event} command={command[:200]}"),
                error=reason,
            )
        )
    except Exception:
        logger.debug("SEL audit for rejected hook failed", exc_info=True)


_VALID_HOOK_EVENTS = frozenset(
    {"preToolUse", "postToolUse", "userPromptSubmit", "agentSpawn", "stop"}
)
_MAX_USER_HOOKS_PER_EVENT = 10
_MAX_TOTAL_USER_HOOKS = 20

# ACP agent hook events use PascalCase (PreToolUse, PostToolUse, ...).
# The agent config stores them in camelCase (preToolUse, ...).  Script headers
# ("# event: PreToolUse") use PascalCase convention; this map
# normalizes both casings back to the canonical camelCase form.
_HOOK_EVENT_CANONICAL = {
    "pretooluse": "preToolUse",
    "posttooluse": "postToolUse",
    "userpromptsubmit": "userPromptSubmit",
    "agentspawn": "agentSpawn",
    "stop": "stop",
}

# Default hooks directory.
_DEFAULT_HOOKS_DIR = _user_dir() / "hooks"

# Recognize hook event from filename suffix when no "# event:" header is set.
# Ordering matters: check more specific suffixes first.
_FILENAME_EVENT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("-post.sh", "postToolUse"),
    ("-prompt.sh", "userPromptSubmit"),
    ("-spawn.sh", "agentSpawn"),
    ("-stop.sh", "stop"),
    ("-pre.sh", "preToolUse"),
)

# Header parsing — only inspect the first few lines so the scan stays O(K).
_HOOK_HEADER_SCAN_LINES = 5
_HOOK_HEADER_RE = re.compile(r"^\s*#\s*(event|matcher)\s*:\s*(\S.*?)\s*$", re.IGNORECASE)


def _parse_hook_script_headers(path: Path) -> tuple[str | None, str | None]:
    """Read the first few lines of a hook script and extract ``# event:`` / ``# matcher:`` directives.

    Returns ``(event_header, matcher_header)``.  Either may be ``None`` if not present.
    Values are returned unparsed; callers normalize/validate them.
    """
    event_header: str | None = None
    matcher_header: str | None = None
    try:
        # Read at most a handful of lines; hook scripts can be large, and we
        # only care about headers immediately after the shebang.
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= _HOOK_HEADER_SCAN_LINES:
                    break
                m = _HOOK_HEADER_RE.match(line)
                if not m:
                    continue
                key = m.group(1).lower()
                val = m.group(2)
                if key == "event" and event_header is None:
                    event_header = val
                elif key == "matcher" and matcher_header is None:
                    matcher_header = val
    except OSError:
        logger.debug("agent_hooks_autoimport: could not read %s for headers", path, exc_info=True)
    return event_header, matcher_header


def _infer_hook_event(script_path: Path, event_header: str | None) -> str | None:
    """Resolve a script's agent hook event.

    Precedence:
      1. Explicit ``# event:`` header (normalized to camelCase).  Unknown values
         return ``None`` so the caller can WARN and skip.
      2. Filename suffix convention (``*-post.sh`` -> ``postToolUse`` etc.).
      3. Default: ``preToolUse``.
    """
    if event_header is not None:
        canonical = _HOOK_EVENT_CANONICAL.get(
            event_header.lower().replace("-", "").replace("_", "")
        )
        return canonical  # None if unknown -- caller decides what to do

    name = script_path.name.lower()
    for suffix, event in _FILENAME_EVENT_SUFFIXES:
        if name.endswith(suffix):
            return event
    return "preToolUse"


def _autoimport_agent_hooks(hooks_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Scan ``hooks_dir`` for executable ``*.sh`` files and return an agent hooks dict.

    Each discovered script becomes an entry under its resolved event (camelCase).
    Returns an empty dict if the directory is missing or contains no usable scripts.

    Security parity with the explicit config path:
      * Each script's resolved path goes through ``_validate_hook_command``.
      * ``# matcher:`` headers are validated against ``_SAFE_MATCHER_RE`` / ``_MAX_MATCHER_LEN``.
      * Non-executable files are skipped (INFO log).
      * Sensitive paths are skipped (via ``_validate_hook_command``).

    Final dedup, per-event cap, and total cap are enforced by ``_merge_agent_hooks``
    which runs on the returned dict.  That keeps explicit config precedence correct:
    callers should invoke ``_merge_agent_hooks`` with the already-merged ``hooks``
    (bundled + explicit) so auto-imported scripts that duplicate an explicit entry
    are deduped out rather than taking its session.
    """
    result: dict[str, list[dict[str, str]]] = {}
    try:
        resolved_hooks_dir = hooks_dir.resolve()
    except (OSError, ValueError):
        # OSError: ENAMETOOLONG, ELOOP, EACCES on a path component.
        # ValueError: null bytes (``"\x00"``) reject at Path construction.
        # Emit SEL audit so an auditor sees a distinct "hooks_dir
        # unresolvable" signal — same symmetry principle as the
        # per-entry ``cannot resolve entry`` branch below.
        logger.debug(
            "agent_hooks_autoimport: cannot resolve %s, skipping", hooks_dir, exc_info=True
        )
        _sel_hook_rejected("autoimport", str(hooks_dir), "cannot resolve hooks_dir")
        return result
    try:
        entries = sorted(resolved_hooks_dir.iterdir())
    except FileNotFoundError:
        logger.debug("agent_hooks_autoimport: directory %s does not exist, skipping", hooks_dir)
        return result
    except OSError:
        logger.warning("agent_hooks_autoimport: cannot read %s, skipping", hooks_dir, exc_info=True)
        # Emit SEL audit so an auditor reconstructing agent-install
        # activity sees a distinct "hooks dir unreadable" signal rather
        # than only the merge-summary ``requested_autoimport=0`` (which
        # looks identical to the no-scripts-configured case).  Same
        # symmetry principle as the per-script rejection branches.
        _sel_hook_rejected("autoimport", str(hooks_dir), "cannot read hooks_dir")
        return result

    loaded = 0
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".sh":
            continue

        # Resolve once up-front and reuse the resolved path for all subsequent
        # checks (stat, validation).  This closes two issues:
        # * TOCTOU: repeated resolve() in _validate_hook_command could race
        #   with an attacker swapping the symlink target between calls.
        # * Symlink escape: entry.is_file() follows symlinks, so a symlink
        #   inside the hooks dir pointing at /tmp/attacker.sh would otherwise
        #   pass (not in _SENSITIVE_HOME_DIRS).  Require the resolved target
        #   to stay under the resolved hooks dir.
        try:
            resolved_entry = entry.resolve()
        except (OSError, ValueError):
            # OSError: typical filesystem failures.  ValueError: filename
            # from ``iterdir()`` carries a null byte or other malformed
            # character that ``Path.resolve()`` rejects.  Without this
            # catch, a maliciously-named file in hooks_dir crashes agent
            # bootstrap.
            logger.warning(
                "agent_hooks_autoimport: cannot resolve %s, skipping", entry, exc_info=True
            )
            _sel_hook_rejected("autoimport", str(entry), "cannot resolve entry")
            continue
        if (
            resolved_entry != resolved_hooks_dir
            and resolved_hooks_dir not in resolved_entry.parents
        ):
            logger.warning(
                "agent_hooks_autoimport: %s resolves outside %s (to %s), skipping",
                entry,
                resolved_hooks_dir,
                resolved_entry,
            )
            _sel_hook_rejected("autoimport", str(entry), "resolved path escapes hooks dir")
            continue

        try:
            mode = resolved_entry.stat().st_mode
        except OSError:
            logger.warning("agent_hooks_autoimport: cannot stat %s, skipping", entry)
            _sel_hook_rejected("autoimport", str(entry), "cannot stat entry")
            continue
        if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
            logger.info("agent_hooks_autoimport: %s is not executable, skipping", entry)
            # Audit parity with the other rejection branches
            # (symlink-escape, cannot-resolve, cannot-stat,
            # failed-validation, unknown-event, invalid-matcher,
            # cannot-read-dir): the non-executable skip is also a
            # permission decision — it determines that a discovered
            # ``.sh`` file will NOT be loaded as a hook — so it must
            # emit a SEL audit event the security-audit policy
            # rule.  Without this call, an auditor reconstructing
            # agent-install activity from SEL would not see scripts
            # that were skipped for lacking the execute bit.
            _sel_hook_rejected("autoimport", str(entry), "not executable")
            continue

        # Defense-in-depth: run the full validation (including
        # is_sensitive_path) BEFORE any file I/O on the script.  The
        # symlink-escape check above already rejects most attacks, but
        # running _validate_hook_command first keeps the "no reads on
        # sensitive paths" invariant intact even if the resolved-path
        # check is ever loosened.  The ``"autoimport"`` event label
        # below is a log tag only - _validate_hook_command uses ``event``
        # solely for log formatting, never as a policy key (e.g. it is
        # never matched against _VALID_HOOK_EVENTS).  The real event is
        # computed from headers after this call succeeds.
        validated_command = _validate_hook_command(str(resolved_entry), "autoimport")
        if validated_command is None:
            # _validate_hook_command already emitted a WARNING with the reason.
            _sel_hook_rejected("autoimport", str(entry), "failed validation")
            continue

        event_header, matcher_header = _parse_hook_script_headers(resolved_entry)
        event = _infer_hook_event(entry, event_header)
        if event is None:
            logger.warning(
                "agent_hooks_autoimport: %s declares unknown event %r, skipping",
                entry,
                event_header,
            )
            # Match the other three rejection branches in this function
            # (symlink-escape, failed-validation, invalid-matcher): every
            # rejection must emit a SEL audit event per the security-audit
            # policy.  Without this call, an auditor
            # reconstructing agent-install activity from SEL would not
            # see scripts that were dropped for declaring unknown event
            # names, which defeats the purpose of the audit trail.
            _sel_hook_rejected("autoimport", str(entry), "unknown event header")
            continue

        entry_dict: dict[str, str] = {"command": validated_command}
        if matcher_header is not None:
            if len(matcher_header) > _MAX_MATCHER_LEN or not _SAFE_MATCHER_RE.match(matcher_header):
                # An invalid matcher is treated as a validation failure:
                # promoting a tool-scoped hook to unscoped (firing on every
                # tool call) would be a silent privilege expansion.
                logger.warning(
                    "agent_hooks_autoimport: %s matcher %r is invalid, skipping script",
                    entry,
                    matcher_header,
                )
                _sel_hook_rejected("autoimport", str(entry), "invalid matcher")
                continue
            entry_dict["matcher"] = matcher_header

        result.setdefault(event, []).append(entry_dict)
        loaded += 1

    if loaded:
        logger.info("agent_hooks_autoimport: loaded %d scripts from %s", loaded, hooks_dir)
    else:
        logger.debug("agent_hooks_autoimport: no scripts loaded from %s", hooks_dir)
    return result


def _merge_agent_hooks(hooks: dict, user_hooks: dict) -> dict:
    """Append user-defined agent_hooks to bundled hooks (per event type).

    Bundled hooks are always first.  User hooks are appended, deduped by
    ``(command, matcher)`` tuple so the same hook doesn't fire twice.
    Malformed entries (missing ``command``) are silently skipped.
    Commands are validated: must be absolute paths to existing files,
    with no shell metacharacters and not in sensitive locations.
    """
    if not isinstance(user_hooks, dict):
        logger.warning("agent_hooks is not a dict, ignoring")
        return hooks
    merged = dict(hooks)
    total_added = 0
    for event, entries in user_hooks.items():
        if event not in _VALID_HOOK_EVENTS:
            logger.warning("agent_hooks: unknown event type %r, skipping", event)
            # Audit parity with every other rejection branch in this
            # function: the security-audit policy, rejecting an
            # entire event-bucket is a permission decision that must be
            # SEL-audited.  Use the (invalid) event name as the tag so
            # auditors can correlate with the config input.
            _sel_hook_rejected(str(event), str(entries)[:200], "unknown event type")
            continue
        if not isinstance(entries, list):
            logger.warning("agent_hooks[%s] is not a list, skipping", event)
            # Same audit-parity rationale: dropping a non-list
            # entries-bucket removes all configured hooks for that
            # event.  SEL must record the decision so auditors can
            # distinguish "0 configured" from "N dropped as non-list".
            _sel_hook_rejected(event, str(entries)[:200], "entries not a list")
            continue
        existing = list(merged.get(event, []))
        existing_keys = {
            (e.get("command"), e.get("matcher")) for e in existing if isinstance(e, dict)
        }
        added = 0
        for entry in entries:
            if added >= _MAX_USER_HOOKS_PER_EVENT:
                logger.warning(
                    "agent_hooks[%s]: limit of %d reached, ignoring remaining",
                    event,
                    _MAX_USER_HOOKS_PER_EVENT,
                )
                # Audit parity with every other rejection branch in this
                # function (missing command, failed validation, non-string
                # matcher, invalid matcher): hitting the per-event cap is
                # a permission decision - configured hooks are being
                # prevented from loading - and must emit a SEL audit
                # event the security-audit policy.  Without
                # this, an auditor cannot distinguish "user configured 15
                # preToolUse hooks and 5 were cap-dropped" from "user
                # configured 10 and all loaded".
                _sel_hook_rejected(
                    event,
                    str(entry.get("command", ""))[:200]
                    if isinstance(entry, dict)
                    else str(entry)[:200],
                    "per-event limit exceeded",
                )
                break
            if total_added >= _MAX_TOTAL_USER_HOOKS:
                logger.warning(
                    "agent_hooks: global limit of %d reached, ignoring remaining",
                    _MAX_TOTAL_USER_HOOKS,
                )
                # Same audit-parity rationale as the per-event cap above:
                # hitting the global cap drops remaining hooks across all
                # events, and auditors need a SEL signal to distinguish
                # "25 configured, 5 cap-dropped" from "20 configured, all
                # loaded".
                _sel_hook_rejected(
                    event,
                    str(entry.get("command", ""))[:200]
                    if isinstance(entry, dict)
                    else str(entry)[:200],
                    "global limit exceeded",
                )
                break
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("command"), str)
                or not entry["command"]
            ):
                logger.warning("agent_hooks[%s]: skipping entry without command", event)
                _sel_hook_rejected(event, str(entry)[:200], "missing or invalid command")
                continue
            resolved = _validate_hook_command(entry["command"], event)
            if resolved is None:
                _sel_hook_rejected(event, entry["command"], "failed validation")
                continue
            matcher = entry.get("matcher")
            if matcher is not None and not isinstance(matcher, str):
                logger.warning("agent_hooks[%s]: matcher must be a string, skipping", event)
                _sel_hook_rejected(event, entry["command"], "non-string matcher")
                continue
            if isinstance(matcher, str) and (
                len(matcher) > _MAX_MATCHER_LEN or not _SAFE_MATCHER_RE.match(matcher)
            ):
                logger.warning(
                    "agent_hooks[%s]: matcher contains disallowed characters or is too long, skipping",
                    event,
                )
                _sel_hook_rejected(event, entry["command"], "invalid matcher")
                continue
            key = (resolved, matcher)
            if key not in existing_keys:
                sanitized = {"command": resolved}
                if isinstance(matcher, str):
                    sanitized["matcher"] = matcher
                existing.append(sanitized)
                existing_keys.add(key)
                added += 1
                total_added += 1
        merged[event] = existing
    return merged


def _apply_user_agent_hooks(config: dict, pc_cfg: dict) -> None:
    """Merge user-defined agent_hooks from personalclaw config into *config* (additive).

    Two sources, explicit first then auto-discovered:

      1. ``agent.agent_hooks`` in ``~/.personalclaw/config.json`` -- explicit entries
         the user wrote by hand.  Unchanged behavior.
      2. ``agent.agent_hooks_autoimport`` (default true): scan
         ``agent.agent_hooks_dir`` (default ``~/.personalclaw/hooks``) for executable
         ``*.sh`` scripts and merge each as a hook entry.  Event is parsed from
         an optional ``# event:`` header, inferred from a filename suffix, or
         defaults to ``preToolUse``.  Optional ``# matcher:`` header gives the
         same tool-name matcher as explicit entries.

    Autoimport runs in a single merge pass with explicit entries listed first,
    so autoimported scripts that duplicate an explicit entry are deduped out
    (explicit wins) and caps (``_MAX_USER_HOOKS_PER_EVENT`` and
    ``_MAX_TOTAL_USER_HOOKS``) are enforced across both sources combined,
    not per-source.
    """
    agent_cfg = pc_cfg.get("agent") if isinstance(pc_cfg.get("agent"), dict) else {}
    user_hooks = agent_cfg.get("agent_hooks") if isinstance(agent_cfg, dict) else None
    autoimport_enabled = True
    hooks_dir = _DEFAULT_HOOKS_DIR
    if isinstance(agent_cfg, dict):
        if "agent_hooks_autoimport" in agent_cfg:
            autoimport_enabled = bool(agent_cfg.get("agent_hooks_autoimport"))
        custom_dir = agent_cfg.get("agent_hooks_dir")
        if isinstance(custom_dir, str) and custom_dir:
            # config.json is LLM-writable; a malicious override could point
            # hooks_dir at /tmp, a world-writable mount, or ~/Downloads.
            # Require the resolved path to live under the user's HOME and
            # not match a sensitive location.  On any failure, log + SEL
            # audit and fall back to the default (~/.personalclaw/hooks) rather
            # than turning autoimport off entirely - the safe default is
            # still available.
            requested = Path(os.path.expanduser(custom_dir))
            try:
                resolved = requested.resolve()
                home = Path.home().resolve()
            except (OSError, ValueError):
                # OSError: ENAMETOOLONG, ELOOP (symlink loop), EACCES.
                # ValueError: Path() / resolve() reject strings with null
                # bytes (``"\x00"``) or similar malformed Unicode.  An
                # LLM-writable ``agent_hooks_dir: "\x00"`` would otherwise
                # propagate ValueError up through rebuild_agent_config() and
                # crash agent bootstrap (denial of service).
                resolved = None
                home = None
            if (
                resolved is None
                or home is None
                # Strict containment: require ``resolved`` to be *under*
                # HOME, not equal to it.  ``~`` alone would otherwise scan
                # the entire home directory for executable ``*.sh`` files,
                # auto-registering anything a user (or attacker) drops
                # anywhere under ``$HOME``.  ``Path.parents`` of e.g.
                # ``/home/user`` is ``(/, /home)`` and does NOT include
                # ``/home/user`` itself, so a bare ``home not in parents``
                # rejects ``resolved == home``.
                or home not in resolved.parents
                or is_sensitive_path(str(resolved))
            ):
                logger.warning(
                    "agent_hooks_autoimport: agent_hooks_dir %r rejected "
                    "(must resolve under %s and not be sensitive), "
                    "falling back to %s",
                    custom_dir,
                    home,
                    _DEFAULT_HOOKS_DIR,
                )
                _sel_hook_rejected(
                    "autoimport", str(requested), "agent_hooks_dir outside HOME or sensitive"
                )
            else:
                # Store the already-resolved path, not the unresolved
                # ``requested``.  Keeping ``requested`` would leave a
                # symlink-swap window: a path component could be swapped
                # between this resolve() and the one inside
                # _autoimport_agent_hooks, bypassing the HOME containment
                # check we just performed.
                hooks_dir = resolved

    explicit_hooks: dict = user_hooks if isinstance(user_hooks, dict) and user_hooks else {}
    has_explicit = bool(explicit_hooks)
    if not has_explicit and not autoimport_enabled:
        return

    before = sum(len(v) for v in config.get("hooks", {}).values() if isinstance(v, list))

    # Collect both sources up-front and merge in a SINGLE ``_merge_agent_hooks``
    # pass.  Rationale: ``_merge_agent_hooks`` initializes ``total_added = 0`` on
    # each call, so invoking it twice would allow the per-call
    # ``_MAX_TOTAL_USER_HOOKS`` cap (20) to apply to each source independently —
    # yielding up to 40 user hooks total instead of the intended 20.  A single
    # pass enforces the per-event cap AND the total cap across the combined
    # set.  Explicit entries are listed first in each event's list so they
    # claim the dedup key before any duplicate from autoimport, preserving the
    # "explicit wins" precedence.
    # Count explicit entries AND audit any non-list buckets as we go.
    # Using a plain loop rather than a generator expression so we can
    # emit WARNING + SEL audit for each dropped event bucket -- dropping
    # a whole event's hooks is a permission decision per the security-audit
    # policy, and the caller-side filter must audit it
    # (``_merge_agent_hooks``'s internal defensive check never fires here
    # because this filter runs first).
    requested_explicit = 0
    for event, entries in explicit_hooks.items():
        if isinstance(entries, list):
            requested_explicit += len(entries)
        else:
            logger.warning("agent_hooks[%s] is not a list, skipping", event)
            _sel_hook_rejected(str(event), str(entries)[:200], "entries not a list")
    requested_autoimport = 0
    discovered: dict[str, list[dict[str, str]]] = {}
    if autoimport_enabled:
        discovered = _autoimport_agent_hooks(hooks_dir)
        requested_autoimport = sum(
            len(v) for v in discovered.values() if isinstance(v, list)
        )

    if requested_explicit == 0 and requested_autoimport == 0:
        # Nothing to merge; keep config["hooks"] untouched (or create empty
        # dict for shape consistency if it wasn't there).
        if "hooks" not in config:
            config["hooks"] = {}
        return

    combined_user_hooks: dict[str, list[dict[str, str]]] = {}
    for src in (explicit_hooks, discovered):
        if not isinstance(src, dict):
            continue
        for event, entries in src.items():
            if not isinstance(entries, list):
                # Already WARN+SEL-audited in the ``requested_explicit``
                # loop above (for explicit_hooks) or filtered out at
                # return-time of ``_autoimport_agent_hooks`` (discovered
                # never contains non-list values).  Defensive continue.
                continue
            combined_user_hooks.setdefault(event, []).extend(entries)

    config["hooks"] = _merge_agent_hooks(config.get("hooks", {}), combined_user_hooks)

    after = sum(len(v) for v in config["hooks"].values() if isinstance(v, list))
    added = after - before
    try:
        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="config_hooks_merge",
                caller_identity="agent_install",
                agent="personalclaw",
                source="cli",
                operation="agent_hooks_merge",
                outcome="completed",
                resources=redact(
                    f"requested_explicit={requested_explicit} "
                    f"requested_autoimport={requested_autoimport} added={added}"
                ),
            )
        )
    except Exception:
        logger.debug("SEL audit for agent_hooks merge failed", exc_info=True)


def build_agent_config() -> dict:
    """Return the final agent config (shipped defaults + user overrides + dynamic fields).

    Security-critical fields (``deniedCommands``, ``hooks``) always use the
    bundled config as their base, even when a project-dir override is present.
    This prevents dev overrides from silently dropping security controls.
    User-defined ``agent_hooks`` from ``~/.personalclaw/config.json`` are then
    additively merged; bundled hooks always run first and cannot be removed.
    """
    config = _load_json(_shipped_defaults())
    config = _deep_merge(config, _load_json(_USER_OVERRIDES))

    # Hooks always come from the bundled config, even if a project-dir override
    # is stale. (Bash command screening is enforced natively in
    # ``personalclaw.security`` — not via a per-agent-file denylist.)
    bundled = _load_json(_BUNDLED_CFG_DIR / "defaults.json")
    bundled_hooks = bundled.get("hooks")
    if not bundled_hooks:
        raise RuntimeError("Cannot build agent config: hooks missing from bundled defaults")
    config["hooks"] = bundled_hooks

    # Merge user-defined agent_hooks from ~/.personalclaw/config.json (additive).
    from personalclaw.config import config_path as _pc_config_path

    pc_cfg = _load_json(_pc_config_path()) or {}
    _apply_user_agent_hooks(config, pc_cfg)

    # Dynamic fields — always resolved at install time
    config["prompt"] = f"file://{_prompt_path()}"
    mcp = config.setdefault("mcpServers", {})
    for name, spec in _MANAGED_MCP_SERVERS.items():
        cmd = spec.get("command") or spec["command_fn"]()
        entry = {"command": cmd, "args": list(spec["args"])}
        if "autoApprove" in spec:
            entry["autoApprove"] = list(spec["autoApprove"])
        mcp[name] = entry

    return config


def _refresh_dynamic_fields(config: dict) -> None:
    """Update security-critical and dynamic fields in an existing config.

    Called when ``personalclaw.json`` already exists so user customizations are
    preserved while security controls and runtime paths stay current.
    """
    # Prompt URI — always resolve at install time
    config["prompt"] = f"file://{_prompt_path()}"

    # Managed MCP servers — ensure present and up-to-date.
    # Only refresh command/args; preserve user customizations (e.g. autoApprove).
    mcp = config.setdefault("mcpServers", {})
    for name, spec in _MANAGED_MCP_SERVERS.items():
        is_new = name not in mcp
        entry = mcp.setdefault(name, {})
        entry["command"] = spec.get("command") or spec["command_fn"]()
        entry["args"] = list(spec["args"])
        # Seed autoApprove only for genuinely new entries; if the user
        # deliberately removed autoApprove from an existing entry we
        # must not re-add it on every refresh.
        if "autoApprove" in spec and is_new:
            entry["autoApprove"] = list(spec["autoApprove"])

    # Hooks always from bundled config. Hard-fail if missing — deny-by-default.
    # (Bash command screening is enforced natively in ``personalclaw.security``.)
    bundled = _load_json(_BUNDLED_CFG_DIR / "defaults.json")
    if bundled is None:
        raise RuntimeError(
            "Cannot refresh security fields: bundled defaults.json is missing or unreadable"
        )
    if not isinstance(bundled, dict):
        raise RuntimeError(
            "Cannot refresh security fields: bundled defaults.json is not a JSON object"
        )

    bundled_hooks = bundled.get("hooks")
    if not bundled_hooks:
        raise RuntimeError("Cannot refresh security fields: hooks missing from bundled defaults")
    config["hooks"] = bundled_hooks

    # Merge user-defined agent_hooks from ~/.personalclaw/config.json (additive).
    from personalclaw.config import config_path as _pc_config_path

    pc_cfg = _load_json(_pc_config_path()) or {}
    _apply_user_agent_hooks(config, pc_cfg)

    # Replace deprecated model names with their current equivalents.
    from personalclaw.dashboard.chat_utils import _normalize_model

    cur_model = config.get("model", "")
    normalized = _normalize_model(cur_model)
    if normalized != cur_model:
        config["model"] = normalized


def get_shipped_tools() -> dict[str, list[str]]:
    """Return shipped tool lists. Public API for cross-module use."""
    shipped = _load_json(_shipped_defaults()) or {}
    return {k: shipped.get(k, []) for k in ("tools", "allowedTools")}


def _load_existing_config(path: Path) -> tuple[dict, bool]:
    """Load and refresh an existing personalclaw.json.

    Returns (config, fresh_install).  Falls back to build_agent_config()
    when the file is corrupt or refresh fails.
    """
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        return build_agent_config(), True
    try:
        _refresh_dynamic_fields(config)
    except (AttributeError, TypeError, RuntimeError) as exc:
        logger.error("Refresh failed, rebuilding from defaults: %s", exc)
        return build_agent_config(), True
    return config, False


def rebuild_agent_config(*, clean: bool = False) -> Path:
    """Rebuild and write the merged personalclaw.json to ~/.personalclaw/agents/.

    This is the single authoritative function for producing the agent config.
    It reads all source files, merges with correct priority, resolves commands,
    and injects fresh marketplace skill paths.

    Merge priority (highest wins):
      1. ~/.personalclaw/mcp.json (agent-specific overrides)
      2. ~/.personalclaw/mcp.json (user global, fills gaps)
      3. Existing personalclaw.json (preserves user customizations)
      4. Bundled defaults (security, managed servers)

    --skill-paths are always resolved fresh from marketplace manifests regardless
    of what any source file contains.

    When the config already exists and *clean* is False, the existing file
    is used as the base so that **all** user customizations are preserved.
    Only security-critical fields (``deniedCommands``, ``hooks``) and
    dynamic fields (``prompt`` URI, personalclaw MCP server commands) are
    refreshed from defaults.

    Args:
        clean: If True, ignore existing config and regenerate from defaults.
    """
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / AGENT_FILENAME

    # Managed MCP sync happens after config is fully built (see below).

    if not clean and path.exists():
        # Existing config — preserve user customizations, only refresh
        # security-critical and dynamic fields.
        config, fresh_install = _load_existing_config(path)
    else:
        config = build_agent_config()
        fresh_install = True

    # Skip managed servers — their command/args are set by _refresh_dynamic_fields().
    managed_names = set(_MANAGED_MCP_SERVERS)

    # ~/.personalclaw/mcp.json — user-configured MCP overrides (highest priority).
    personalclaw_mcp = _load_json(_USER_DIR / "mcp.json").get("mcpServers", {})
    for name, spec in personalclaw_mcp.items():
        if isinstance(spec, dict) and name not in managed_names:
            mcps = config.setdefault("mcpServers", {})
            if name in mcps and isinstance(mcps[name], dict):
                mcps[name].update(spec)
            else:
                mcps[name] = spec

    # Resolve MCP commands to absolute paths and validate
    valid_servers: dict[str, Any] = {}
    for name, spec in config.get("mcpServers", {}).items():
        if not isinstance(spec, dict):
            continue
        # Remote Streamable HTTP servers — preserve as-is (url-based, no command)
        if spec.get("url"):
            valid_servers[name] = spec
            continue
        cmd = spec.get("command", "")
        if not cmd:
            logger.warning("Dropping MCP server %r: no command", name)
            continue
        # Resolve using server's env PATH merged with system PATH.
        # Accept absolute paths directly if the file exists — shutil.which
        # can fail inside user-namespace sandboxes even when the file is fine.
        if os.path.isabs(cmd) and os.path.isfile(cmd) and os.access(cmd, os.X_OK):
            resolved = cmd
        else:
            env_path = spec.get("env", {}).get("PATH", "")
            search_path = (env_path + os.pathsep if env_path else "") + os.environ.get("PATH", "")
            resolved = shutil.which(cmd, path=search_path)
        if resolved:
            spec["command"] = resolved
            valid_servers[name] = spec
        else:
            logger.warning("Dropping MCP server %r: command not found: %s", name, cmd)
    config["mcpServers"] = valid_servers

    # Sync shared (user-installed) servers to tools/allowedTools.
    # These are explicitly added by the user via ~/.personalclaw/mcp.json
    # — unlike managed servers, they should always be registered regardless
    # of fresh/existing config state.
    _shared_added: list[str] = []
    _shared_removed: list[str] = []
    for name, spec in personalclaw_mcp.items():
        if not isinstance(spec, dict) or name in managed_names:
            continue
        ref = f"@{name}"
        if spec.get("disabled"):
            for key in ("tools", "allowedTools"):
                lst = config.get(key)
                if lst is not None and ref in lst:
                    lst.remove(ref)
                    if ref not in _shared_removed:
                        _shared_removed.append(ref)
        elif name in valid_servers:
            valid_servers[name].pop("disabled", None)
            for key in ("tools", "allowedTools"):
                if ref not in config.get(key, []):
                    config.setdefault(key, []).append(ref)
                    if ref not in _shared_added:
                        _shared_added.append(ref)
    if _shared_added:
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_added",
            outcome="ok",
            source="rebuild_agent_config",
            resources=f"{', '.join(_shared_added)} added to tools/allowedTools (shared)",
        )
    if _shared_removed:
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_removed",
            outcome="ok",
            source="rebuild_agent_config",
            resources=f"{', '.join(_shared_removed)} removed from tools/allowedTools (disabled)",
        )

    # On fresh installs, ensure managed MCP tools are in tools (but NOT
    # allowedTools — new MCPs may have destructive tools; user opts in).
    # On existing configs, don't touch tools/allowedTools — user controls those.
    if fresh_install:
        added_refs: list[str] = []
        for mcp_name in _MANAGED_MCP_SERVERS:
            ref = f"@{mcp_name}"
            if mcp_name in valid_servers:
                if ref not in config.get("tools", []):
                    config.setdefault("tools", []).append(ref)
                    added_refs.append(ref)
        if added_refs:
            sel().log_api_access(
                caller="system",
                operation="mcp_tools_added",
                outcome="ok",
                source="rebuild_agent_config",
                resources=f"{', '.join(added_refs)} added to tools (fresh install)",
            )

    # Final dedup (preserves order).
    for key in ("tools", "allowedTools"):
        config[key] = list(dict.fromkeys(config.get(key, [])))

    _atomic_json_write(path, config)
    logger.info("Installed agent config: %s", path)

    return path
