"""Session templates — save a chat's setup as a reusable starter (SESSION-MANAGEMENT S3).

A template captures the *setup* of a conversation, never its content: which agent, which
model, the reasoning effort, and an optional opening prompt. Reusing a setup is the whole
value — "the chat where I have the research agent on the big model with my review
checklist" is a configuration a user rebuilds by hand today, every time.

Deliberately NOT captured: the transcript, the workspace binding, loaded skills, and
knowledge context. A template that dragged a workspace path along would silently point a
new chat at a directory the user wasn't thinking about; the plan's own open question
defers loaded-context capture to a v2, and copying only the four declared fields is what
keeps "new from template" predictable.

Stored in ``entity_settings/session_templates.json`` (one JSON object keyed by template
id), so it rides the existing durability inventory's ``entity_settings`` item and needs no
new backup wiring.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_ENTITY = "session_templates"

#: A template is four setup fields plus bookkeeping. The dict doubles as the key
#: ALLOWLIST and the type schema, mirroring ``INBOX_DEFAULTS`` in
#: ``providers/entity_routes.py`` — an unknown key is dropped rather than stored, so a
#: future field can't be smuggled in by a client and silently persisted.
_FIELDS: dict[str, type] = {
    "name": str,
    "agent": str,
    "model": str,
    "reasoning_effort": str,
    "first_prompt": str,
}

#: Bounds. Generous enough for a real checklist prompt, small enough that the file stays
#: a settings file rather than a content store.
_MAX_NAME = 80
_MAX_PROMPT = 4000
_MAX_TEMPLATES = 100

_VALID_EFFORTS = frozenset({"", "low", "medium", "high"})


def _path() -> Path:
    return config_dir() / "entity_settings" / f"{_ENTITY}.json"


def _load_raw() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt settings file must not break the chat page; an empty template list
        # is a degraded surface, a 500 on session create is a broken product.
        logger.warning("session_templates.json unreadable; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _slug(name: str) -> str:
    """A readable id stem from the template name, with a uuid tail for uniqueness."""
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return f"{stem or 'template'}-{uuid.uuid4().hex[:8]}"


def list_templates() -> list[dict[str, Any]]:
    """Every stored template, newest first. Never raises."""
    out: list[dict[str, Any]] = []
    for tid, entry in _load_raw().items():
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "id": tid,
                **{k: entry.get(k, "") for k in _FIELDS},
                "created_at": entry.get("created_at", 0.0),
            }
        )
    out.sort(key=lambda t: float(t.get("created_at") or 0.0), reverse=True)
    return out


def get_template(template_id: str) -> dict[str, Any] | None:
    """One template by id, or None."""
    entry = _load_raw().get(template_id)
    if not isinstance(entry, dict):
        return None
    return {
        "id": template_id,
        **{k: entry.get(k, "") for k in _FIELDS},
        "created_at": entry.get("created_at", 0.0),
    }


def validate(fields: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Coerce + bound a client-supplied template. Returns ``(clean, error)``.

    Unknown keys are dropped (the allowlist), every value is forced to ``str``, and the
    two free-text fields are length-bounded. ``error`` is non-empty when the input can't
    be stored at all — a blank name being the only hard failure, since a template with no
    name is unfindable in the picker.
    """
    clean: dict[str, Any] = {}
    for key in _FIELDS:
        raw = fields.get(key, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, (str, int, float)):
            return {}, f"{key} must be a string"
        clean[key] = str(raw).strip()

    if not clean["name"]:
        return {}, "name is required"
    clean["name"] = clean["name"][:_MAX_NAME]
    clean["first_prompt"] = clean["first_prompt"][:_MAX_PROMPT]
    if clean["reasoning_effort"] not in _VALID_EFFORTS:
        return {}, f"reasoning_effort must be one of {sorted(_VALID_EFFORTS - {''})} or empty"
    return clean, ""


def save_template(fields: dict[str, Any]) -> tuple[str, str]:
    """Store a new template. Returns ``(template_id, error)``."""
    clean, err = validate(fields)
    if err:
        return "", err
    data = _load_raw()
    if len(data) >= _MAX_TEMPLATES:
        return "", f"template limit reached ({_MAX_TEMPLATES}); delete one first"
    tid = _slug(clean["name"])
    clean["created_at"] = time.time()
    data[tid] = clean
    _save_raw(data)
    return tid, ""


def update_template(template_id: str, fields: dict[str, Any]) -> str:
    """Replace a template's fields in place. Returns an error string ("" = ok)."""
    data = _load_raw()
    existing = data.get(template_id)
    if not isinstance(existing, dict):
        return "not found"
    clean, err = validate(fields)
    if err:
        return err
    # created_at is bookkeeping the client doesn't own — preserve it across an edit so
    # the picker's ordering doesn't jump when a name is fixed.
    clean["created_at"] = existing.get("created_at", time.time())
    data[template_id] = clean
    _save_raw(data)
    return ""


def delete_template(template_id: str) -> bool:
    """Remove a template. True when one was removed."""
    data = _load_raw()
    if template_id not in data:
        return False
    del data[template_id]
    _save_raw(data)
    return True
