"""What a learning proposal's Accept and Reject do, said before either is pressed.

One kind-aware table over ``learning.installers.branch_for``. It is read by the card when a
proposal is filed (``learning.cards``) and by the review route when its panel opens. So a card can
draw its own pair, named as its panel names them, without a read.

``accept`` is ``{effect, verb, sentence, confirm}``:
- ``effect`` is one of:
  - ``installs``: it writes something new, such as a workflow, a skill or a principle;
  - ``applies``: it changes something there, such as a template's next version;
  - ``records``: it records that you agree, and changes nothing the agent reads;
  - ``none``: nothing here can accept it, and ``sentence`` says why.
- ``verb`` names the press ("Save template").
- ``sentence`` is what it does ("Saves the template weekly-status-report to Workflows").
- ``confirm`` is true where the Accept replaces or removes what is there, so it asks once more.

The server's answer to the press is still the authority: a refused accept answers 409 and stays
pending.
"""

from __future__ import annotations

import re
from typing import Any

INSTALLS = "installs"
APPLIES = "applies"
RECORDS = "records"
NONE = "none"

#: What an inferred lesson's Accept does. It records your agreement and nothing reads it yet, so
#: it never promises "so it isn't proposed again": a decision's fingerprint covers this one body.
RECORDS_SENTENCE = "Accepting records that you agree. It changes nothing the agent reads yet."

NO_ACCEPT: dict[str, Any] = {
    "effect": NONE,
    "verb": "",
    "sentence": "This can't be accepted from here.",
    "confirm": False,
}

_NONE_SENTENCES = {
    "retirement": "Nothing removes it from here yet.",
    "tier": "Nothing makes this change from here yet.",
    "tier.wider_scope": "Nothing makes this change from here yet.",
    "knowledge": "A knowledge draft can't be accepted from here yet.",
}


def _accept(effect: str, verb: str, sentence: str, *, confirm: bool = False) -> dict[str, Any]:
    return {"effect": effect, "verb": verb, "sentence": sentence, "confirm": confirm}


def accept_for(prop: Any, *, variant: str = "") -> dict[str, Any]:
    """What accepting *prop* does (the module docstring says each field). *variant* is its card's
    (``learning.cards.variant_of``), read again when not given."""
    from personalclaw.learning import cards, installers, template_install

    data = prop.to_dict() if hasattr(prop, "to_dict") else dict(prop)
    variant = variant or cards.variant_of(prop)
    branch = installers.branch_for(data)
    if not branch:
        reason = (
            template_install.why_not(data) if template_install.is_template_proposal(data) else ""
        )
        return _accept(NONE, "", reason or _NONE_SENTENCES.get(variant, NO_ACCEPT["sentence"]))
    if branch in (installers.NOTHING, installers.TEMPLATE_DIFF):
        if variant == "template_diff":
            workflow = str(data.get("target") or "")
            manifest = data.get("change_manifest") or {}
            ops = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
            count = len(ops) if isinstance(ops, list) else 0
            noun = "change" if count == 1 else "changes"
            these = "this" if count == 1 else f"these {count}"
            return _accept(
                APPLIES,
                "Apply changes",
                f"Saves {workflow} as a new version with {these} {noun}",
            )
        if variant == "lesson.captures":
            return _accept(RECORDS, "Accept them", RECORDS_SENTENCE)
        return _accept(RECORDS, "Accept", RECORDS_SENTENCE)
    built = cards._BUILDERS.get(variant)
    _headline, _detail, name = built(prop) if built else ("", "", "")
    return _installs(variant, data, name)


def _installs(variant: str, data: dict[str, Any], name: str) -> dict[str, Any]:
    from personalclaw.learning import lesson_conflicts, project_context_review, template_install

    if variant in ("template.named", "template.recurring_path", "template.card"):
        named = name or template_install.template_name(data)
        return _accept(INSTALLS, "Save template", f"Saves the template {named} to Workflows")
    if variant == "prompt.card":
        return _accept(INSTALLS, "Save prompt", f"Saves the prompt {name}")
    if variant == "agent.card":
        return _accept(INSTALLS, "Save agent", f"Saves the agent {name}")
    if variant == "lesson.working_habit":
        return _accept(
            INSTALLS, "Save principle", "Saves this as a working principle the agent follows"
        )
    if variant == "lesson.conflict":
        manifest = data.get("change_manifest") or {}
        fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
        held = fix[0] if isinstance(fix, list) and fix and isinstance(fix[0], dict) else {}
        where = lesson_conflicts.memory_words(str(held.get("memory") or "")) or "your memory"
        return _accept(
            INSTALLS, "Use the learned one", f"It replaces yours in {where}.", confirm=True
        )
    if variant == "skill.promoted":
        return _accept(INSTALLS, "Install skill", f"Installs {name} with this procedure")
    if variant == "retirement.text_not_typed":
        count = sum(1 for line in str(data.get("body") or "").splitlines() if line.startswith("- "))
        noun = "item" if count == 1 else "items"
        these = "this" if count == 1 else f"these {count}"
        return _accept(INSTALLS, "Remove them", f"Removes {these} {noun} from memory", confirm=True)
    if variant in ("project_instruction", "project_file", "project_skill"):
        project_id, file = project_context_review._decode_target(str(data.get("target") or ""))
        project = project_context_review.project_name(project_id)
        if not project:
            # Its install would refuse: the project no longer resolves.
            return _accept(NONE, "", "Its project was removed, so it can't be accepted here.")
        if variant == "project_instruction":
            return _accept(
                INSTALLS,
                "Add instruction",
                f"Adds this instruction to {project}'s agent instructions",
            )
        if variant == "project_file":
            there = project_context_review.context_file_exists(str(data.get("target") or ""))
            tail = ", replacing what is there" if there else ""
            return _accept(
                INSTALLS,
                "Write file",
                f"Writes context/{file} in {project}{tail}",
                confirm=there,
            )
        return _accept(INSTALLS, "Create skill", f"Creates the skill {file}")
    return _accept(INSTALLS, "Accept", "Accepting installs it.")


def reject_for(prop: Any, *, variant: str = "") -> dict[str, str]:
    """What rejecting *prop* does: ``{verb, sentence}``."""
    from personalclaw.learning import cards

    variant = variant or cards.variant_of(prop)
    if variant == "lesson.conflict":
        return {"verb": "Keep mine", "sentence": "Yours stays; the learned one isn't saved."}
    if variant in ("retirement.text_not_typed",):
        return {"verb": "Keep them", "sentence": "They stay in memory as they are."}
    if variant == "lesson.captures":
        return {"verb": "Reject them", "sentence": "None of them is kept."}
    return {"verb": "Reject", "sentence": "Nothing changes."}


# ── the review a panel reads (GET /api/learning/proposals/{id}/review) ──────────────────────────

#: How much of a long part a review serves before it says it was cut: a diff or an error, cut on a
#: line boundary, never inside a mask.
PART_CHARS = 8000
#: How much of one capture a review serves, and how many captures.
CAPTURE_CHARS = 600
CAPTURES_SHOWN = 50
#: How many runs a review links.
RUNS_SHOWN = 10

_WHY_RE = re.compile(r"^Why:\s*(.+?)\s*$", re.M)


def _masked(text: object) -> str:
    from personalclaw.security import redact_for_display

    return redact_for_display(str(text or ""))


def _cut_lines(text: str, limit: int = PART_CHARS) -> tuple[str, bool]:
    """*text* up to *limit*, cut at the last line break before it and never inside a mask, with
    whether anything was cut."""
    from personalclaw.security import cut_outside_masks

    if len(text) <= limit:
        return text, False
    head = text[:limit]
    at = head.rfind("\n")
    end = at if at > limit // 2 else limit
    return text[: cut_outside_masks(text, end)].rstrip(), True


def _why(prop: Any, variant: str) -> str:
    """Why it was proposed, only what its producer wrote: a template's closing ``Why:`` line, a
    promoted skill's or a project proposal's rationale (its title). A correction refine has none
    beyond your words, and a lesson's reason is its lesson."""
    if variant in ("template.named", "template.recurring_path"):
        found = _WHY_RE.findall(str(prop.body or ""))
        return _masked(found[-1]) if found else ""
    if variant in ("skill.promoted", "project_instruction", "project_file", "project_skill"):
        return _masked(prop.title)
    return ""


def _runs(prop: Any) -> list[str]:
    """The runs a proposal rests on, as ids its panel links (``#/workflows/runs/<id>``). A
    template the save tool or the reviewer filed holds its name or the chat's key as its run id,
    which is no run."""
    from personalclaw.learning.template_install import template_name

    data = prop.to_dict() if hasattr(prop, "to_dict") else {}
    not_runs = {str(getattr(prop, "session_key", "") or ""), template_name(data)}
    seen: list[str] = []
    for rid in [getattr(prop, "run_id", "")] + list(getattr(prop, "evidence_refs", None) or []):
        rid = str(rid or "")
        if rid and rid not in seen and rid not in not_runs and ":" not in rid:
            seen.append(rid)
    return seen[:RUNS_SHOWN]


def _content(prop: Any, variant: str, *, capture: str = "") -> dict[str, Any]:
    """The thing itself, by type, masked; what its panel draws first."""
    from personalclaw.learning import cards
    from personalclaw.learning.template_install import template_steps

    body = str(prop.body or "")
    if variant in ("template.named", "template.recurring_path"):
        return {"type": "steps", "steps": [_masked(s) for s in template_steps(body)]}
    if variant == "template.plan_shape":
        first, _sep, _rest = body.partition("\n\n")
        return {"type": "plan_shape", "sentence": _masked(first), "runs": _runs(prop)[:8]}
    if variant in ("template.card", "prompt.card", "agent.card"):
        payload = cards._card_payload(prop)
        pasted, cut = _cut_lines(_masked(body.split("```json", 1)[-1].rsplit("```", 1)[0]))
        out: dict[str, Any] = {"type": variant.split(".")[0], "pasted": pasted, "pasted_cut": cut}
        if variant == "template.card":
            out["type"] = "spec"
            out["steps"] = [
                {"id": str(s.get("id") or ""), "prompt": _masked(s.get("prompt"))}
                for s in payload.get("steps") or []
                if isinstance(s, dict)
            ]
        elif variant == "prompt.card":
            out["text"] = _masked(payload.get("content"))
            out["variables"] = [
                str(v.get("name")) for v in payload.get("variables") or [] if isinstance(v, dict)
            ]
        else:
            out["text"] = _masked(payload.get("system_prompt"))
            out["voice"] = _masked(payload.get("voice"))
        return out
    if variant == "lesson.failed_run":
        lesson = body.split("\n\n", 1)[0]
        error, cut = _cut_lines(_masked(getattr(prop, "source_excerpt", "")))
        return {
            "type": "lesson",
            "lesson": _masked(lesson),
            "capsule": _masked(body.split("\n\n", 2)[-1]),
            "error": error,
            "error_cut": cut,
        }
    if variant == "lesson.conflict":
        manifest = getattr(prop, "change_manifest", None) or {}
        fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
        held = fix[0] if isinstance(fix, list) and fix and isinstance(fix[0], dict) else {}
        from personalclaw.learning import lesson_conflicts

        return {
            "type": "choice",
            "yours": [_masked(t) for t in held.get("yours") or []],
            "memory": lesson_conflicts.memory_words(str(held.get("memory") or "")),
            "learned": _masked(held.get("rule")),
            "negative": _masked(held.get("negative")),
            "who": lesson_conflicts.learned_from_words(str(prop.source_cadence or "")),
        }
    if variant == "lesson.captures":
        return _captures(prop, capture=capture)
    if variant == "skill.promoted":
        text, cut = _cut_lines(_masked(body))
        return {"type": "procedure", "text": text, "cut": cut}
    if variant == "template_diff":
        manifest = getattr(prop, "change_manifest", None) or {}
        ops = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
        from personalclaw.security import redact_values_for_display

        return {
            "type": "ops",
            "ops": redact_values_for_display([o for o in ops or [] if isinstance(o, dict)]),
            "predicted_fixes": (
                [_masked(p) for p in manifest.get("predicted_fixes") or []]
                if isinstance(manifest, dict)
                else []
            ),
        }
    if variant == "retirement.text_not_typed":
        items = []
        for line in body.splitlines():
            if not line.startswith("- "):
                continue
            head, _sep, source = line[2:].partition(" (the same as in ")
            kind, _sep, text = head.partition(": ")
            items.append(
                {"kind": kind, "text": _masked(text), "source": _masked(source.rstrip(")"))}
            )
        return {"type": "items", "items": items}
    text, cut = _cut_lines(_masked(body))
    return {"type": "markdown", "text": text, "cut": cut}


def _captures(prop: Any, *, capture: str = "") -> dict[str, Any]:
    """A captures batch's captures (up to :data:`CAPTURES_SHOWN`, each cut at
    :data:`CAPTURE_CHARS`, its source), or with *capture* that one capture whole."""
    from personalclaw.learning.staging import get_store

    uids = [str(u) for u in getattr(prop, "staging_refs", None) or []][:CAPTURES_SHOWN]
    try:
        store = get_store()
        sources = {s["uid"]: s for s in store.sources_for(uids)}
    except Exception:
        store, sources = None, {}
    lines = [line[2:] for line in str(prop.body or "").splitlines() if line.startswith("- ")]
    out = []
    for index, uid in enumerate(uids):
        text = lines[index] if index < len(lines) else ""
        whole = store.content_for(uid) if (capture == uid and store is not None) else None
        shown = _masked(whole if whole is not None else text)
        src = sources.get(uid) or {}
        out.append(
            {
                "uid": uid,
                "text": shown if capture == uid else shown[:CAPTURE_CHARS],
                "cut": capture != uid and len(shown) > CAPTURE_CHARS,
                "day": str(src.get("day") or ""),
                "cadence": str(src.get("cadence") or ""),
                "kind": str(src.get("kind") or ""),
                "kept": bool(src),
            }
        )
    return {
        "type": "lessons",
        "captures": out,
        "total": len(getattr(prop, "staging_refs", []) or []),
    }


def review_for(prop: Any, *, capture: str = "") -> dict[str, Any]:
    """What a learning proposal's panel reads: its card's lines, why it was proposed, the thing
    itself (``content``, by type), where it came from, and what Accept and Reject do, worked out
    now (a file there now makes Write file ask once more). Read-only: no write, no model call."""
    from personalclaw.learning import cards

    card = cards.card_for(prop)
    variant = card["variant"]
    session = str(getattr(prop, "session_key", "") or "")
    return {
        "id": prop.id,
        "kind": prop.kind,
        "variant": variant,
        "status": prop.status,
        "created_at": prop.created_at,
        "updated_at": prop.updated_at,
        "headline": card["headline"],
        "detail": card["detail"],
        "name": card["name"],
        "label": card["label"],
        "why": _why(prop, variant),
        "content": _content(prop, variant, capture=capture),
        "accept": accept_for(prop, variant=variant),
        "reject": reject_for(prop, variant=variant),
        "session_key": session,
        "source_gone": _source_gone(session),
        "runs": _runs(prop),
    }


def _source_gone(session: str) -> bool:
    """Whether the chat *session* names was deleted (a row filed before deleting a chat withdrew
    its proposals)."""
    if not session.startswith("dashboard:"):
        return False
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        log = getattr(get_dashboard_state(), "conversation_log", None)
        # The running gateway's log, read only: a review writes nothing, so it opens no log.
        # Either spelling of the chat's key, as the log may hold it.
        return log is not None and not (
            log.has_session(session) or log.has_session(session.removeprefix("dashboard:"))
        )
    except Exception:
        return False
