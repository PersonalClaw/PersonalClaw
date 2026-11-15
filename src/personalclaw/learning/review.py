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
    if branch == installers.NOTHING:
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
