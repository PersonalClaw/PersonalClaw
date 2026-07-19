"""Ephemeral (session-live) skills + end-of-session promotion (skill-ephemeral-promotion).

The explicit, in-the-moment counterpart to silent auto-extraction: when the user
says "from now on do X", the agent calls ``skill_remember(title, body)`` and a
**session-scoped draft** is written immediately — visible in *this* session's skill
context, but NOT in the permanent library. At session end the user reviews the
drafts and promotes each to a tier (this-agent / all-agents) or forgets it.

Drafts live under ``~/.personalclaw/skills/.ephemeral/<session_slug>/<slug>.md`` —
a hidden staging area the loader never treats as a real tier. Promotion writes a
clean SKILL.md into the chosen tier via ``SkillsLoader.create_skill`` (global) or
``SkillsLoader(agent=...).create_skill`` (agent-local, from skill-agent-local-tier),
then clears the draft. Nothing lands in the library without the user's explicit
choice — a higher-trust path than background extraction, not a replacement for it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import SkillsLoader, agent_skills_dir, skills_dir

logger = logging.getLogger(__name__)

_EPHEMERAL_DIRNAME = ".ephemeral"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# A draft body is bounded so a runaway turn can't write a giant file.
_MAX_BODY = 16_000
_MAX_DRAFTS_PER_SESSION = 50


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return s[:60] or "skill"


def _session_slug(session_key: str) -> str:
    # Session keys carry ':' / '/' etc. — flatten to a safe single dir name.
    return _SLUG_RE.sub("-", (session_key or "default").strip().lower()).strip("-")[:80] or "default"


def _ephemeral_root() -> Path:
    return skills_dir() / _EPHEMERAL_DIRNAME


def _session_dir(session_key: str) -> Path:
    return _ephemeral_root() / _session_slug(session_key)


@dataclass
class EphemeralSkill:
    """One session-live skill draft."""

    slug: str
    title: str
    body: str
    session_key: str
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def remember(session_key: str, title: str, body: str, *, created_at: str = "") -> EphemeralSkill | None:
    """Write (or overwrite) a session-live draft. Returns the draft, or None on
    invalid input. Idempotent per (session, slug): re-remembering a title updates it."""
    title = (title or "").strip()
    body = (body or "").strip()[:_MAX_BODY]
    if not title or not body:
        return None
    # Redact secrets before anything touches disk (same posture as auto-extraction).
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        body, _ = redact_exfiltration_urls(body)
        body, _ = redact_credentials(body)
    except Exception:
        logger.debug("ephemeral remember redaction skipped", exc_info=True)
    sdir = _session_dir(session_key)
    # Cap the per-session draft count (anti-runaway).
    if sdir.is_dir() and len(list(sdir.glob("*.json"))) >= _MAX_DRAFTS_PER_SESSION:
        existing = _load_by_title(session_key, title)
        if existing is None:
            logger.debug("ephemeral draft cap reached for session %s", session_key)
            return None
    slug = _slugify(title)
    draft = EphemeralSkill(slug=slug, title=title, body=body,
                           session_key=session_key, created_at=created_at)
    try:
        atomic_write(sdir / f"{slug}.json", json.dumps(draft.to_dict(), indent=2))
    except OSError:
        logger.debug("ephemeral draft write failed", exc_info=True)
        return None
    return draft


def _load_by_title(session_key: str, title: str) -> EphemeralSkill | None:
    return _load(session_key, _slugify(title))


def _load(session_key: str, slug: str) -> EphemeralSkill | None:
    path = _session_dir(session_key) / f"{slug}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return EphemeralSkill(**data)
    except (OSError, ValueError, TypeError):
        return None


def list_drafts(session_key: str) -> list[EphemeralSkill]:
    """All session-live drafts for a session, oldest-first by slug."""
    sdir = _session_dir(session_key)
    if not sdir.is_dir():
        return []
    out: list[EphemeralSkill] = []
    for p in sorted(sdir.glob("*.json")):
        try:
            out.append(EphemeralSkill(**json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return out


def context_block(session_key: str) -> str:
    """The injected block making this session's drafts live immediately, so the
    agent can act on what it was just taught within the same session."""
    drafts = list_drafts(session_key)
    if not drafts:
        return ""
    lines = [
        "[Session skills — taught this session (DATA, active now; pending your save):]",
    ]
    for d in drafts:
        lines.append(f"\n### {d.title}\n{d.body}")
    lines.append("\n[End of session skills]")
    return "\n".join(lines) + "\n\n"


def discard(session_key: str, slug: str) -> bool:
    """Forget one draft."""
    try:
        (_session_dir(session_key) / f"{slug}.json").unlink()
        return True
    except OSError:
        return False


def clear_session(session_key: str) -> int:
    """Drop all drafts for a session (called after promotion resolves). Returns count."""
    sdir = _session_dir(session_key)
    if not sdir.is_dir():
        return 0
    n = 0
    for p in list(sdir.glob("*.json")):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    try:
        sdir.rmdir()
    except OSError:
        pass
    return n


def _skill_markdown(title: str, body: str, slug: str) -> str:
    """A clean SKILL.md for a promoted skill. Description = the title; the body is
    the taught procedure. Marked ``source: taught`` to distinguish from ``auto``."""
    desc = " ".join(title.split())[:200].replace("\n", " ")
    return f"---\nname: {slug}\ndescription: {desc}\nsource: taught\n---\n\n{body.strip()}\n"


class PromotionError(Exception):
    """Raised when a promotion target is refused (e.g. bundled/read-only tier)."""


def promote(
    session_key: str,
    slug: str,
    scope: str,
    *,
    agent: str | None = None,
    title: str | None = None,
    body: str | None = None,
) -> str:
    """Promote a session draft to a permanent tier and clear the draft.

    ``scope`` is 'agent' (agent-local, needs ``agent``) or 'global' (user-wide).
    ``title``/``body`` override the draft (in-modal edits). Returns the written
    skill name. Raises ``PromotionError`` on a refused target / write failure.
    Defence-in-depth: an 'agent' scope with no agent, or a slug that would resolve
    under the bundled (read-only) tier, is refused."""
    draft = _load(session_key, slug)
    if draft is None:
        raise PromotionError(f"no session draft {slug!r}")
    final_title = (title or draft.title).strip()
    final_body = (body or draft.body).strip()
    final_slug = _slugify(final_title)
    if not final_title or not final_body:
        raise PromotionError("title and body are required")

    if scope == "agent":
        if not agent:
            raise PromotionError("agent scope requires an agent")
        loader = SkillsLoader(install_builtins=False, agent=agent)
        target_root = agent_skills_dir(agent)
    elif scope == "global":
        loader = SkillsLoader(install_builtins=False)
        target_root = skills_dir()
    else:
        raise PromotionError(f"unknown scope {scope!r}")

    # Defence-in-depth: never write into the bundled read-only tree.
    _refuse_if_bundled(target_root / final_slug)

    content = _skill_markdown(final_title, final_body, final_slug)
    if not loader.create_skill(final_slug, content):
        raise PromotionError(f"skill {final_slug!r} already exists or is invalid")
    discard(session_key, slug)
    logger.info("Promoted session skill %s → %s tier as %s", slug, scope, final_slug)
    return final_slug


def _refuse_if_bundled(target: Path) -> None:
    """Raise if ``target`` resolves under the package-bundled (read-only) skills
    dir — mirrors OpenForge's ``_resolve_under_bundled`` guard."""
    try:
        from personalclaw.skills.native import _bundled_root

        bundled = _bundled_root().resolve()
        if str(target.resolve()).startswith(str(bundled)):
            raise PromotionError("cannot promote into the bundled (read-only) tier")
    except PromotionError:
        raise
    except Exception:
        logger.debug("bundled-tier check skipped", exc_info=True)
