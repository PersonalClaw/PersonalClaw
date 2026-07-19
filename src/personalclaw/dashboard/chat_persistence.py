"""Session persistence — save, restore, history prefix."""

import json
import logging
import re
import time
import uuid

from personalclaw.agent import AGENTS_DIR
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_utils import (
    _history_key_for,
    _normalize_model,
    _sync_dashboard_sessions,
    resolve_history_key,
)
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.security import redact_credentials, redact_exfiltration_urls


def _load_providers_raw() -> list[dict]:
    """Load the raw providers array from config.json."""
    try:
        from personalclaw.config.loader import config_path
        path = config_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("providers", [])
    except Exception:
        pass
    return []


def _build_agent_model_map() -> dict[str, str]:
    """Map each agent's name and file-stem to its configured model.

    Lets sessions without a persisted ``model`` resolve the model their agent
    would use. Keyed by both ``name`` and filename stem so either form found in
    session metadata resolves.
    """
    model_map: dict[str, str] = {}
    try:
        for f in AGENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                model = data.get("model", "")
                if data.get("name"):
                    model_map[data["name"]] = model
                model_map[f.stem] = model
            except (json.JSONDecodeError, OSError):
                continue
    except Exception:
        logger.debug("Failed to build agent model map", exc_info=True)
    return model_map


def _active_provider_model() -> str:
    """Return the model configured on the active provider, or '' if unavailable."""
    providers = _load_providers_raw()
    if providers:
        return providers[0].get("model", "")
    return ""


def _model_matches_provider(model: str) -> bool:
    """Check if a persisted session model is compatible with the active provider.

    On restore, a session may carry a model pinned by a provider the user has since
    swapped out (e.g. a ``claude-*`` model when the active provider now speaks a
    different family) — that model would fail at call time, so the caller replaces
    it with the active provider's model instead.

    The check is provider-agnostic: it asks whether the active provider TYPE serves
    the model's family, using the shared, data-driven family→type map in
    :func:`personalclaw.llm.catalog.model_family_provider_types` (no vendor name is
    hard-coded here). When the family is unknown, or the active provider's type isn't
    in the map, the model is accepted (permissive — never strand a restorable model
    on a guess)."""
    if not model:
        return True
    from personalclaw.llm.catalog import model_family_provider_types

    owning_types = model_family_provider_types(model)
    if not owning_types:
        return True  # unrecognized family → accept
    providers = _load_providers_raw()
    if not providers:
        return True
    active_type = providers[0].get("type", "")
    # ``acp`` agent-runtimes can front any model family (they proxy a CLI), so they
    # never disqualify a persisted model.
    return active_type in owning_types or active_type == "acp"


def _redact_value(v: object) -> object:
    """Redact a single value recursively (str, dict, list, or passthrough)."""
    if isinstance(v, str):
        v, _ = redact_exfiltration_urls(v)
        v, _ = redact_credentials(v)
        return v
    if isinstance(v, dict):
        return _redact_meta(v)
    if isinstance(v, list):
        return [_redact_value(i) for i in v]
    return v


def _redact_meta(meta: dict) -> dict:
    """Recursively redact string values in meta dict (credentials + URLs)."""
    out: dict = {}
    for k, v in meta.items():
        out[k] = _redact_value(v)
    return out


logger = logging.getLogger(__name__)

_MAX_HISTORY_CHARS = 8000

# Reasoning-effort is no longer a fixed PClaw scale — each backend declares its
# OWN effort options (native: low/medium/high/max; ACP: whatever configOptions.
# effort advertises, e.g. minimal/xhigh). Persisted JSON is untrusted input and
# the value flows into a subprocess CLI arg / set_config_option value, so instead
# of a value allowlist we enforce a strict FORMAT: a short lowercase-alnum token
# (no spaces/shell metachars), which admits any real backend value while blocking
# injection. "" = default. Re-exported by chat_handlers for the API validator.
_REASONING_EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")

# Kept as a name for callers that want the native ladder (composer fallback).
_REASONING_EFFORT_VALUES = frozenset({"", "low", "medium", "high", "max"})


def _validate_reasoning_effort(raw: object) -> str:
    """Return *raw* if it's a safe reasoning_effort token, else "".

    Enforces a format (not a fixed value set) so any backend-declared effort is
    accepted while a tampered/corrupted metadata file cannot smuggle spaces or
    shell metacharacters into a subprocess ``--effort`` arg / config value.
    """
    if raw == "" or raw is None:
        return ""
    if isinstance(raw, str) and _REASONING_EFFORT_RE.match(raw):
        return raw
    if raw:  # truthy but malformed — log so we notice corruption
        logger.warning("Discarding invalid persisted reasoning_effort: %r", raw)
    return ""


def save_all_sessions_to_history(state: DashboardState) -> None:
    """Save all active sessions to history. Called on gateway shutdown."""
    for session in list(state._sessions.values()):
        try:
            _save_session_to_history(state, session, force=True)
        except Exception:
            logger.error("Shutdown: failed to save session %s", session.key, exc_info=True)


def _attach_variants(session: _ChatSession, m: dict) -> None:
    """Copy variant history from a persisted message onto the session's last message, with redaction."""
    if m.get("variants"):
        session.messages[-1]["variants"] = [  # type: ignore[assignment]
            {**v, "content": redact_credentials(redact_exfiltration_urls(v.get("content", ""))[0])[0]}
            for v in m["variants"] if isinstance(v, dict)
        ]
        session.messages[-1]["variant_idx"] = m.get("variant_idx", 0)


def _rehydrate_session_from_history(state: DashboardState, session_name: str) -> _ChatSession | None:
    """Rehydrate a single dashboard session from persisted history.

    Unlike ``state.get_or_create_session`` (which creates a fresh, empty session with
    default ``memory_mode='persistent'``), this helper reads the session's
    metadata and messages from ``conversation_log`` so the restored session has
    the original title/agent/model/memory_mode and its message history
    populated. Returns ``None`` if the session does not exist on disk (so
    callers can fall through to other delivery paths without creating a
    phantom empty tab).

    Intended for targeted resume paths (e.g. cron→origin injection after
    gateway restart). Bulk startup restore still uses ``restore_recent_sessions``.
    """
    if not state.conversation_log:
        return None
    if session_name in state._sessions:
        return state._sessions[session_name]
    # Resolve the canonical persisted key provider-agnostically: a channel-provider
    # thread (Slack/Discord/…) persists under its own bare key; a dashboard session
    # under the dashboard: namespace. Ask the log which one actually has metadata
    # rather than assuming a key shape.
    history_key = resolve_history_key(state.conversation_log, session_name)
    if not history_key:
        return None
    meta = state.conversation_log.get_metadata(history_key)
    # No metadata → session was never persisted. Don't create a phantom session.
    if not meta:
        return None
    if meta.get("closed"):
        return None
    try:
        _restore_cfg = AppConfig.load()
    except Exception:
        _restore_cfg = None
    provider_model_map = _build_agent_model_map()
    session = state.get_or_create_session(session_name)
    # Pull display fields from session listing for title parity with bulk restore.
    sessions = state.conversation_log.list_sessions()
    session_info = next(
        (s for s in sessions if s.get("key") == history_key),
        {},
    )
    # Titles may have been auto-generated by an LLM (_generate_title_via_provider)
    # and are surfaced on the dashboard, so apply the same redaction passes
    # used on assistant content before setting. Defence-in-depth — the title
    # author is trusted-ish (our own agent process), but the generation input
    # is user content, so a prompt injection could craft a title with an
    # exfiltration URL or leaked credential.
    raw_title = session_info.get("title") or meta.get("title") or session_name
    raw_title, _ = redact_exfiltration_urls(raw_title)
    raw_title, _ = redact_credentials(raw_title)
    session.title = raw_title
    session._titled = bool(session_info.get("title") or meta.get("title"))
    if meta.get("created_at"):
        session.created_at = meta["created_at"]
    if meta.get("agent"):
        session.agent = meta["agent"]
    if meta.get("model"):
        normalized = _normalize_model(meta["model"])
        if _model_matches_provider(normalized):
            session.model = normalized
        else:
            session.model = _active_provider_model()
    elif session.agent:
        try:
            pc = _restore_cfg.agents.get(session.agent) if _restore_cfg else None
            provider_name = pc.provider_agent if pc and pc.provider_agent else session.agent
            session.model = provider_model_map.get(provider_name, "")
        except Exception:
            logger.debug("Failed to resolve model for rehydrated session %s", session_name, exc_info=True)
    if meta.get("reasoning_effort"):
        session.reasoning_effort = _validate_reasoning_effort(meta["reasoning_effort"])
    # Ephemeral discovered-ACP-agent override (per-session, never in config).
    _acp_prov = meta.get("acp_provider")
    if isinstance(_acp_prov, str) and _acp_prov.startswith("acp:"):
        session.acp_provider = _acp_prov
        _acp_pa = meta.get("acp_provider_agent")
        session.acp_provider_agent = _acp_pa if isinstance(_acp_pa, str) else ""
    if meta.get("workspace_dir"):
        session.workspace_dir = meta["workspace_dir"]
    if meta.get("mode"):
        session.mode = meta["mode"]
    if meta.get("folder_id"):
        session.folder_id = meta["folder_id"]
    if meta.get("pinned"):
        session.pinned = True
    if meta.get("color_index") is not None:
        session.color_index = meta["color_index"]
    raw_tags = meta.get("tags")
    if isinstance(raw_tags, list):
        session.tags = [str(t) for t in raw_tags if isinstance(t, str) and t]
    mm = meta.get("memory_mode", "persistent")
    session.memory_mode = mm
    if mm != "persistent":
        state._restricted_keys.add(f"dashboard:{session_name}")
    if meta.get("forked_from") is not None:
        session.forked_from = meta["forked_from"]
    # Restore the persisted side-chat buffer (transcript only; settled state).
    _side_meta = meta.get("side")
    if isinstance(_side_meta, dict) and _side_meta.get("messages"):
        from personalclaw.dashboard.side_state import SideState
        session._side = SideState.from_dict(_side_meta)
    messages = state.conversation_log.read_messages(history_key)
    for m in messages[-200:]:
        role = m.get("role", "assistant")
        cls = m.get("cls") or ("msg msg-u" if role == "user" else "msg msg-a")
        content = m.get("content", "")
        if role != "user":
            content, _ = redact_exfiltration_urls(content)
            content, _ = redact_credentials(content)
        session.append(role, content, cls, ts=m.get("ts", ""), meta=_redact_meta(m["meta"]) if m.get("meta") else None)
        _attach_variants(session, m)
    session.drain()
    session._resumed_count = len(session.messages)
    session._dirty = False
    logger.info("Rehydrated session %s (%s) from history", session_name, session.title)
    return session


def resolve_session(state: DashboardState, name: str):
    """Return the in-memory session for *name*, rehydrating from disk on miss.

    Org actions (tag/folder/pin/color) target sessions chosen from the chat
    history list, which now surfaces disk-only sessions (not in memory after a
    restart). A bare ``state._sessions.get`` 404s on those, so editing tags on
    an older chat silently fails. Returns None only if never persisted.
    """
    return state._sessions.get(name) or _rehydrate_session_from_history(state, name)


def restore_recent_sessions(state: DashboardState, window_minutes: int = 30, *, folders_only: bool = False) -> int:
    """Restore sessions as chat sessions."""
    if not state.conversation_log:
        return 0
    cutoff = time.time() - (window_minutes * 60) if window_minutes > 0 else None
    restored = 0

    provider_model_map = _build_agent_model_map()
    try:
        _restore_cfg = AppConfig.load()
    except Exception:
        _restore_cfg = None
    for s in state.conversation_log.list_sessions():
        key = s.get("key", "")
        if key.startswith("dashboard:"):
            session_name = key.removeprefix("dashboard:")
        elif key.startswith("dashboard_"):
            session_name = key.removeprefix("dashboard_")
        else:
            continue
        if session_name in state._sessions:
            continue
        meta = state.conversation_log.get_metadata(key)
        has_folder = bool(meta.get("folder_id"))
        has_pin = bool(meta.get("pinned"))
        if folders_only and not has_folder and not has_pin:
            continue
        if meta.get("closed"):
            continue
        if not has_folder and not has_pin:
            if cutoff is not None and s.get("modified", 0) < cutoff:
                continue
        session = state.get_or_create_session(session_name)
        # Titles can be LLM-generated (auto-title) and are surfaced on the
        # dashboard — apply the same redaction as assistant content. Matches
        # the treatment in _rehydrate_session_from_history above.
        raw_title = s.get("title", session_name)
        raw_title, _ = redact_exfiltration_urls(raw_title)
        raw_title, _ = redact_credentials(raw_title)
        session.title = raw_title
        session._titled = bool(s.get("title"))
        if meta.get("created_at"):
            session.created_at = meta["created_at"]
        if meta.get("agent"):
            session.agent = meta["agent"]
        if meta.get("model"):
            normalized = _normalize_model(meta["model"])
            if _model_matches_provider(normalized):
                session.model = normalized
            else:
                session.model = _active_provider_model()
        elif session.agent:
            try:
                pc = _restore_cfg.agents.get(session.agent) if _restore_cfg else None
                provider_name = pc.provider_agent if pc and pc.provider_agent else session.agent
                session.model = provider_model_map.get(provider_name, "")
            except Exception:
                logger.debug(
                    "Failed to resolve model for restored session %s", session_name, exc_info=True
                )
        if meta.get("reasoning_effort"):
            session.reasoning_effort = _validate_reasoning_effort(meta["reasoning_effort"])
        if meta.get("workspace_dir"):
            session.workspace_dir = meta["workspace_dir"]
        if meta.get("mode"):
            session.mode = meta["mode"]
        if meta.get("folder_id"):
            session.folder_id = meta["folder_id"]
        if meta.get("pinned"):
            session.pinned = True
        if meta.get("color_index") is not None:
            session.color_index = meta["color_index"]
        if meta.get("color_theme"):
            session.color_theme = meta["color_theme"]
        raw_tags = meta.get("tags")
        if isinstance(raw_tags, list):
            session.tags = [str(t) for t in raw_tags if isinstance(t, str) and t]
        mm = meta.get("memory_mode", "persistent")
        session.memory_mode = mm
        if mm != "persistent":
            state._restricted_keys.add(f"dashboard:{session_name}")
        if meta.get("forked_from") is not None:
            session.forked_from = meta["forked_from"]
        _side_meta = meta.get("side")
        if isinstance(_side_meta, dict) and _side_meta.get("messages"):
            from personalclaw.dashboard.side_state import SideState
            session._side = SideState.from_dict(_side_meta)
        tab_id = meta.get("tab_id")
        if not tab_id:
            tab_id = uuid.uuid4().hex[:12]
            state.conversation_log.update_metadata(key, {"tab_id": tab_id})
        session._tab_id = tab_id
        messages = state.conversation_log.read_messages_chained(key)
        session._disk_older_count = max(0, len(messages) - 500)
        for m in messages[-500:]:
            role = m.get("role", "assistant")
            cls = m.get("cls") or ("msg msg-u" if role == "user" else "msg msg-a")
            content = m.get("content", "")
            if role != "user":
                content, _ = redact_exfiltration_urls(content)
                content, _ = redact_credentials(content)
            session.append(role, content, cls, ts=m.get("ts", ""), meta=_redact_meta(m["meta"]) if m.get("meta") else None)
            _attach_variants(session, m)
        session.drain()
        session._resumed_count = len(session.messages)
        session._dirty = False
        restored += 1
        logger.info("Restored session %s (%s)", session_name, session.title)
    _sync_dashboard_sessions(state)
    return restored


def _save_session_to_history(
    state: DashboardState,
    session: _ChatSession,
    messages: list[dict] | None = None,
    *,
    closed: bool = False,
    force: bool = False,
) -> None:
    """Persist session messages to JSONL history."""
    msgs = messages if messages is not None else session.messages
    if not state.conversation_log or not msgs:
        return
    if session._resumed_count > 0 and len(msgs) <= session._resumed_count:
        if not closed and not force:
            return
    # Save back under the key this session is actually persisted under: a
    # channel-provider thread keeps its own bare key; a dashboard session uses the
    # dashboard: namespace. resolve_history_key returns the existing persisted key
    # (channel thread) and falls back to the dashboard form for a brand-new session.
    history_key = resolve_history_key(state.conversation_log, session.key) or _history_key_for(session.key)
    try:
        existing_meta = state.conversation_log.get_metadata(history_key)

        path = state.conversation_log._path(history_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta_line: dict = {
            "_type": "metadata",
            "created_at": existing_meta.get("created_at") or session.created_at,
            "last_consolidated": existing_meta.get("last_consolidated", 0),
        }
        if closed:
            meta_line["closed"] = True
        meta_line["memory_mode"] = session.memory_mode
        if session.title and session.title != session.key:
            meta_line["title"] = session.title
        if session.agent:
            meta_line["agent"] = session.agent
        meta_line["model"] = session.model
        if session.reasoning_effort:
            meta_line["reasoning_effort"] = session.reasoning_effort
        if session.mode:
            meta_line["mode"] = session.mode
        if session.workspace_dir:
            meta_line["workspace_dir"] = session.workspace_dir
        if session.folder_id:
            meta_line["folder_id"] = session.folder_id
        if session.pinned:
            meta_line["pinned"] = True
        if session.color_index is not None:
            meta_line["color_index"] = session.color_index
        if session.color_theme:
            meta_line["color_theme"] = session.color_theme
        if session.tags:
            meta_line["tags"] = list(session.tags)
        if session.forked_from is not None:
            meta_line["forked_from"] = session.forked_from
        # Persist the side-chat buffer attached to the session (so it reloads with
        # it) — but only the transcript, never in `messages`. Falls back to the
        # existing persisted side if the in-memory buffer was dropped/closed.
        _side = getattr(session, "_side", None)
        if _side is not None and _side.messages:
            meta_line["side"] = _side.to_dict()
        elif existing_meta.get("side"):
            meta_line["side"] = existing_meta["side"]
        tab_id = getattr(session, "_tab_id", None) or existing_meta.get("tab_id")
        if tab_id:
            meta_line["tab_id"] = tab_id
        # Origin tag (loop/code/campaign worker vs manual chat) — persisted so the
        # history list can classify + filter a disk-only session without relying
        # solely on the key prefix.
        _app = getattr(session, "_app", "") or existing_meta.get("app", "")
        if _app:
            meta_line["app"] = _app
        lines = [json.dumps(meta_line) + "\n"]
        for m in msgs:
            role = m.get("role", "assistant")
            if role in ("chunk", "done", "streaming", "queued", "permission"):
                continue
            content = m.get("content", "")
            if role not in ("user", "system"):
                content, _ = redact_exfiltration_urls(content)
                content, _ = redact_credentials(content)
            entry: dict = {
                "role": role,
                "content": content,
                "ts": m.get("ts", ""),
                "source_thread": "dashboard",
                "source_user": "dashboard",
            }
            if m.get("variants"):
                redacted_variants: list[dict] = []
                for v in m["variants"]:
                    if not isinstance(v, dict):
                        continue
                    vc = v.get("content", "")
                    vc, _ = redact_exfiltration_urls(vc)
                    vc, _ = redact_credentials(vc)
                    redacted_variants.append({**v, "content": vc})
                entry["variants"] = redacted_variants
                entry["variant_idx"] = m.get("variant_idx", 0)
            cls_val = m.get("cls", "")
            if role == "system" and cls_val:
                entry["cls"] = cls_val
            if m.get("meta"):
                entry["meta"] = _redact_meta(m["meta"])
            lines.append(json.dumps(entry) + "\n")

        atomic_write(path, "".join(lines), fsync=True)
        state.conversation_log._invalidate_cache(history_key)
        state.conversation_log.invalidate_tab_id_cache()
    except Exception:
        logger.error("Failed to save session %s to history", session.key, exc_info=True)
        raise


def _build_history_prefix(session: _ChatSession) -> str:
    """Build a condensed history prefix from session messages for session re-injection."""
    lines: list[str] = []
    total = 0
    for m in session.messages:
        role = m.get("role", "")
        if role in ("chunk", "done", "streaming", "queued", "permission", "error", "tool"):
            continue
        label = "User" if role == "user" else "Assistant"
        text = m.get("content", "")[:500]
        line = f"{label}: {text}"
        if total + len(line) > _MAX_HISTORY_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (
        "[Previous chat history for this tab — session was reset after stop]\n"
        + "\n".join(lines)
        + "\n[End of history]\n\n"
    )
