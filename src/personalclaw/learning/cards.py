"""A learning proposal's card: what it asks, in the words its producer wrote.

The Inbox row, the bell, a phone notification and Needs you's card used to lead with the kind's
label ("New template proposed: weekly-status-re…"), cut at about 40 characters, so two proposals of
one kind read alike. :func:`card_for` reads what each producer wrote and returns
``{kind, variant, headline, detail, name, accept, label}``:
- ``headline`` is what it does or what went wrong ("Draft the Friday status email from the week's
  merged changes");
- ``detail`` is one line under it ("3 steps: Fetch … → Group … → Draft …");
- ``name`` is the thing it is about when the headline leads with what it does (a template's or a
  skill's slug, a workflow, a project), else ``""``;
- ``accept`` is what its Accept does (:func:`personalclaw.learning.review.accept_for`);
- ``label`` is the kind's label (``proposals._KIND_LABELS``, its words unchanged), now the tag.

Every field is masked for display, then cut on a word with the masks kept whole (120 characters for
the headline, 160 for the detail). Fence markers go first, so a fenced body reads as its text. It
never raises: a body it can't read falls back to the masked title.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

HEADLINE_CHARS = 120
DETAIL_CHARS = 160

#: A run failure's title, as ``learning.run_end`` writes it.
_RUN_FAILURE_RE = re.compile(r"^Run failure in (?P<template>[^/]+)/(?P<step>[^:]+): (?P<mode>.+)$")
#: A traceback's exception line: its last ``Name: message`` line.
_EXCEPTION_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w.]*)(?:Error|Exception|Warning|Exit|Interrupt)\b.*$"
)
_FENCE_RE = re.compile(r"^\s*```[\w-]*\s*$", re.M)
_MARKER_RE = re.compile(r"\[(?:stumble|skill-ladder)[^\]]*\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def _plain(text: object) -> str:
    """*text* with fence lines and a producer's own markers taken out, then masked for display."""
    from personalclaw.security import redact_for_display

    value = _MARKER_RE.sub("", _FENCE_RE.sub("", str(text or "")))
    return redact_for_display(value)


def _clip(text: str, limit: int) -> str:
    from personalclaw.textfmt import clip_words

    return clip_words(text, limit)


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _first_sentence(text: str) -> str:
    """*text*'s first sentence, on one line, without its closing period (a card's lines are
    labels, not prose)."""
    line = " ".join(str(text or "").split())
    parts = _SENTENCE_END.split(line, maxsplit=1)
    first = parts[0] if parts else ""
    return first[:-1] if first.endswith(".") and not first.endswith("..") else first


def _undash(text: str) -> str:
    """A producer's ``name — description`` joined with ``: ``, the way this work writes it."""
    return str(text or "").replace(" — ", ": ")


def _tags(prop: Any) -> list[str]:
    return [str(t) for t in (getattr(prop, "tags", None) or [])]


def _steps(body: str) -> list[str]:
    from personalclaw.learning.template_install import template_steps

    return template_steps(body)


def _arrowed(steps: list[str]) -> str:
    return " → ".join(steps)


# ── the variants ─────────────────────────────────────────────────────────────────────────────────

Built = tuple[str, str, str]  # (headline, detail, name)


def _template_named(prop: Any) -> Built:
    from personalclaw.learning.template_install import template_name

    data = prop.to_dict()
    title = str(prop.title or "")
    name = template_name(data)
    steps = _steps(prop.body)
    headline = title if not title.startswith("Template:") else ""
    detail = f"{len(steps)} steps: {_arrowed(steps)}" if steps else ""
    return headline, detail, name


_PLAN_SHAPE_COUNT_RE = re.compile(r"^(\d+) recent run")


def _template_plan_shape(prop: Any) -> Built:
    match = _PLAN_SHAPE_COUNT_RE.match(str(prop.body or ""))
    count = int(match.group(1)) if match else int(getattr(prop, "reinforcements", 0) or 0)
    runs = "run" if count == 1 else "runs"
    return str(prop.title or ""), f"{count} recent {runs} built this same plan", ""


def _template_recurring_path(prop: Any) -> Built:
    from personalclaw.learning.template_install import WORKFLOW_TAG, tag_value

    workflow = tag_value(_tags(prop), WORKFLOW_TAG)
    steps = _steps(prop.body)
    path = " → ".join(steps)
    where = workflow or "ad-hoc work"
    count = int(getattr(prop, "reinforcements", 0) or 0)
    noun = "run" if count == 1 else "runs"
    return (
        f"Recurring path in {where}: {path}",
        f"{count} successful {noun} took exactly this path",
        "",
    )


def _card_payload(prop: Any) -> dict[str, Any]:
    from personalclaw.packs import prompt_cards

    try:
        return prompt_cards.payload_from_body(str(prop.body or ""))
    except Exception:
        return {}


def _pasted_template(prop: Any) -> Built:
    payload = _card_payload(prop)
    steps = [s for s in payload.get("steps") or [] if isinstance(s, dict)]
    return (
        str(payload.get("description") or prop.title or ""),
        f"{len(steps)} steps · from a card you pasted",
        str(payload.get("name") or ""),
    )


def _pasted_prompt(prop: Any) -> Built:
    payload = _card_payload(prop)
    names = [
        str(v.get("name"))
        for v in payload.get("variables") or []
        if isinstance(v, dict) and v.get("name")
    ]
    uses = "Uses " + ", ".join("{{" + n + "}}" for n in names) + " · " if names else ""
    return (
        str(payload.get("description") or prop.title or ""),
        f"{uses}from a card you pasted",
        str(payload.get("name") or ""),
    )


def _pasted_agent(prop: Any) -> Built:
    payload = _card_payload(prop)
    return (
        str(payload.get("description") or prop.title or ""),
        _first_sentence(str(payload.get("system_prompt") or "")),
        str(payload.get("name") or ""),
    )


_EXCEPTION_NAME_RE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt):\s+")


def _error_line(text: str) -> str:
    """What went wrong, from a run's error text: a traceback's exception line, else its first,
    in its words ("The metrics query returned an empty result", not "ValueError: …")."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    found = next((line for line in reversed(lines) if _EXCEPTION_RE.match(line)), "")
    line = found or (lines[0] if lines else "")
    return _EXCEPTION_NAME_RE.sub("", line) or line


def _lesson_failed_run(prop: Any) -> Built:
    match = _RUN_FAILURE_RE.match(str(prop.title or ""))
    step = match.group("step") if match else ""
    error = _error_line(getattr(prop, "source_excerpt", "") or "")
    if step and error:
        return f"{step}: {error}", "Accepting it changes nothing yet", ""
    return str(prop.title or ""), "Accepting it changes nothing yet", ""


def _lesson_outcome(prop: Any) -> Built:
    return str(prop.title or ""), _first_sentence(prop.body), ""


def _working_habit(prop: Any) -> Built:
    pattern = str(prop.title or "").removeprefix("Observed working habit: ")
    route, _sep, tools = pattern.partition(" + ")
    names = [t for t in tools.split(",") if t and t != "no-tools"]
    if names:
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        headline = f"{(route or 'direct').capitalize()} work with {listed}"
    else:
        headline = f"{(route or 'direct').capitalize()} work with no tools"
    turns = int(getattr(prop, "reinforcements", 0) or 0)
    noun = "turn" if turns == 1 else "turns"
    return headline, f"Seen in {turns} {noun} before it was proposed", ""


def _lesson_conflict(prop: Any) -> Built:
    manifest = getattr(prop, "change_manifest", None) or {}
    fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
    held = fix[0] if isinstance(fix, list) and fix and isinstance(fix[0], dict) else {}
    yours = [str(t) for t in held.get("yours") or [] if str(t).strip()]
    learned = str(held.get("rule") or "")
    if not (yours and learned):
        return str(prop.title or ""), "", ""
    from personalclaw.learning import lesson_conflicts

    who = lesson_conflicts.learned_from_words(str(getattr(prop, "source_cadence", "") or ""))
    where = lesson_conflicts.memory_words(str(held.get("memory") or ""))
    detail = " · ".join(part for part in (f"In {where}" if where else "", who) if part)
    return f"“{yours[0]}” → “{learned}”", detail, ""


def _captures(prop: Any) -> Built:
    lines = [
        line[2:].strip() for line in str(prop.body or "").splitlines() if line.startswith("- ")
    ]
    count = len(lines) or len(getattr(prop, "staging_refs", None) or [])
    first = next((line for line in lines if not line.startswith("capture_turn")), "")
    if first:
        more = f" · +{count - 1} more" if count > 1 else ""
        headline = f"{first}{more}"
    else:
        headline = f"{count} turns captured"
    noun = "capture" if count == 1 else "captures"
    return headline, f"{count} {noun} saved while no model was connected", ""


def _skill_promoted(prop: Any) -> Built:
    from personalclaw.learning import skill_promotion

    slug, description = skill_promotion._decode_target(str(prop.target or ""))
    return (
        description or str(prop.title or ""),
        str(prop.title or ""),
        f"auto/{slug}" if slug else "",
    )


#: A template change's risk tier (``learning.refiner.RiskTier``), as its card says it.
_TIER_WORDS = {"low": "low risk", "review": "needs a read", "manual_only": "manual only"}


def _tier_of(prop: Any, ops: Any) -> str:
    """A template change's tier: the one its tags name (filed beside ``refiner``), else the
    riskiest of its ops. By membership, never by a tag's position."""
    for tag in _tags(prop):
        if tag in _TIER_WORDS:
            return tag
    from personalclaw.learning.refiner import risk_tier

    return risk_tier([o for o in ops or [] if isinstance(o, dict)]) if ops else ""


def _template_diff(prop: Any) -> Built:
    manifest = getattr(prop, "change_manifest", None) or {}
    ops = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
    runs = manifest.get("evidence_refs") if isinstance(manifest, dict) else None
    changes = len(ops) if isinstance(ops, list) else 0
    tier = _TIER_WORDS.get(_tier_of(prop, ops), "")
    count = len(set(runs or [])) if isinstance(runs, list) else 0
    parts = [f"{changes} change{'s' if changes != 1 else ''}"]
    if tier:
        parts.append(tier)
    if count:
        parts.append(f"from {count} run{'s' if count != 1 else ''}")
    return _first_sentence(prop.body), " · ".join(parts), str(prop.target or "")


def _composed_text(prop: Any) -> Built:
    items = [line[2:] for line in str(prop.body or "").splitlines() if line.startswith("- ")]
    first = items[0].split(" (the same as in ", 1)[0] if items else ""
    kind, _sep, text = first.partition(": ")
    headline = f"{kind}: “{text}”" if text else str(prop.title or "")
    noun = "thing" if len(items) == 1 else "things"
    return headline, f"Check {len(items)} {noun} learned from text you didn't type", ""


def _project(prop: Any) -> tuple[str, str]:
    from personalclaw.learning import project_context_review

    pid, name = project_context_review._decode_target(str(prop.target or ""))
    return project_context_review.project_name(pid) or "a removed project", name


def _project_instruction(prop: Any) -> Built:
    project, _name = _project(prop)
    return _first_line(prop.body), str(prop.title or ""), project


def _project_file(prop: Any) -> Built:
    project, name = _project(prop)
    from personalclaw.learning import project_context_review

    state = "updated" if project_context_review.context_file_exists(prop.target) else "new file"
    return f"context/{name}: {state}", str(prop.title or ""), project


def _project_skill(prop: Any) -> Built:
    project, name = _project(prop)
    return f"{name} for {project}" if name else str(prop.title or ""), str(prop.title or ""), ""


def _knowledge_draft(prop: Any) -> Built:
    title = str(prop.title or "")
    return _undash(title), _first_sentence(prop.body), ""


def _default(prop: Any) -> Built:
    return _undash(str(prop.title or "")), _first_sentence(prop.body), ""


def variant_of(prop: Any) -> str:
    """Which producer filed *prop*, as the card names it."""
    from personalclaw.learning.proposals import Kind

    kind = str(getattr(prop, "kind", "") or "")
    tags = _tags(prop)
    cadence = str(getattr(prop, "source_cadence", "") or "")
    if "prompt-card" in tags:
        return {Kind.PROMPT.value: "prompt.card", Kind.AGENT.value: "agent.card"}.get(
            kind, "template.card"
        )
    if kind == Kind.TEMPLATE.value:
        if "plan_similarity" in tags:
            return "template.plan_shape"
        if "positive_path" in tags and "trace_mining" in tags:
            return "template.recurring_path"
        return "template.named"
    if kind == Kind.LESSON_BATCH.value:
        if "lesson_conflict" in tags:
            return "lesson.conflict"
        if cadence == "self_model":
            return "lesson.working_habit"
        if "outcome" in tags:
            return "lesson.outcome"
        if "workflow_run" in tags:
            return "lesson.failed_run"
        if getattr(prop, "staging_refs", None):
            return "lesson.captures"
        return "lesson"
    if kind == Kind.SKILL.value:
        return "skill.promoted"
    if kind == Kind.RETIREMENT.value:
        from personalclaw.learning import composed_text

        return "retirement.text_not_typed" if composed_text.REVIEW_TAG in tags else "retirement"
    if kind == Kind.TIER_MIGRATION.value:
        return "tier.wider_scope" if "promotion" in tags else "tier"
    if kind == Kind.KNOWLEDGE_DRAFT.value:
        return "knowledge"
    return kind


_BUILDERS: dict[str, Callable[[Any], Built]] = {
    "template.named": _template_named,
    "template.plan_shape": _template_plan_shape,
    "template.recurring_path": _template_recurring_path,
    "template.card": _pasted_template,
    "prompt.card": _pasted_prompt,
    "agent.card": _pasted_agent,
    "lesson.failed_run": _lesson_failed_run,
    "lesson.outcome": _lesson_outcome,
    "lesson.working_habit": _working_habit,
    "lesson.conflict": _lesson_conflict,
    "lesson.captures": _captures,
    "skill.promoted": _skill_promoted,
    "template_diff": _template_diff,
    "retirement.text_not_typed": _composed_text,
    "project_instruction": _project_instruction,
    "project_file": _project_file,
    "project_skill": _project_skill,
    "knowledge": _knowledge_draft,
}


def card_for(prop: Any) -> dict[str, Any]:
    """The card of the learning proposal *prop* (the module docstring says what each field is).

    Never raises: a body it can't read gives the masked title as the headline."""
    from personalclaw.learning import proposals, review

    label = proposals._KIND_LABELS.get(str(getattr(prop, "kind", "") or ""), "Proposal")
    variant = "unknown"
    try:
        variant = variant_of(prop)
        headline, detail, name = _BUILDERS.get(variant, _default)(prop)
    except Exception:
        logger.debug("card_for: %s fell back to its title", getattr(prop, "id", "?"), exc_info=True)
        headline, detail, name = _default(prop) if _safe(prop) else ("", "", "")
    shown = _clip(_plain(headline), HEADLINE_CHARS) or _clip(
        _plain(getattr(prop, "title", "")), HEADLINE_CHARS
    )
    try:
        accept = review.accept_for(prop, variant=variant)
    except Exception:
        logger.debug("card_for: no accept for %s", getattr(prop, "id", "?"), exc_info=True)
        accept = review.NO_ACCEPT
    return {
        "kind": str(getattr(prop, "kind", "") or ""),
        "variant": variant,
        "headline": shown,
        "detail": _clip(_plain(detail), DETAIL_CHARS),
        "name": _clip(_plain(name), HEADLINE_CHARS),
        "accept": accept,
        "label": label,
    }


def _safe(prop: Any) -> bool:
    try:
        _default(prop)
        return True
    except Exception:
        return False
