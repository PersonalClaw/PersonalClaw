"""What an edit leaves of a trigger's action: the one rule every door that edits one saves by.

An edit sends the settings it changes. A setting it sends replaces the saved one, a setting it sends
as ``null`` is removed (its editor cleared it), and a setting it does not send stays as saved: a
form that does not show a setting must not change it. Every door used to replace the whole action
with what it was sent, so the Triggers page's schedule editor, which builds an Invoke Agent action
from the fields it draws, dropped the files its agent may change, its capability and its turn cap
on any save, one that moved only its time included, and the automation then ran read-only.

Naming another provider replaces the action, and the saved settings go with it: they belong to the
provider that no longer runs. A setting is replaced whole, never merged deeper: a list or an object
(the files it may change, a workflow's inputs) is edited as one value by the form that shows it.

An action is its provider and its config, the two things a fire reads (`schedule.normalize_action`
makes the same shape at create). The doors: the Triggers page's editor for a schedule, a store
trigger or a lifecycle trigger (`dashboard.handlers.triggers`), and the automation tools the chat's
`automation_update` and the CLI's `cron update` save through (`triggers.tools.update`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from personalclaw.triggers.wakeup import RESUME_TARGET_KEY

#: Why an edit's config cannot be read as settings, in the words the create path refuses it with.
CONFIG_NOT_AN_OBJECT = "action.config must be an object"


def action_in(workflow: Any) -> Mapping[str, Any]:
    """The action a trigger's ``workflow`` block holds, in either stored shape: the migrated
    ``{"inline": {provider, config}}`` or the flat ``{provider, config}`` the chat's tools write.
    A block with neither (a resume target) holds no provider and no config."""
    block = workflow if isinstance(workflow, Mapping) else {}
    nested = block.get("inline")
    return nested if isinstance(nested, Mapping) else block


def edited_action(stored: Any, edit: Mapping[str, Any]) -> dict[str, Any]:
    """The action (``{provider, config}``) an *edit* of the *stored* action leaves.

    The provider is the one the edit names, else the stored one. The config is the stored one with
    each setting the edit sends put over it, a ``null`` removing it; an edit naming another
    provider than the stored one starts from no settings, and a ``"config": null`` clears them all.

    Raises ``ValueError`` (:data:`CONFIG_NOT_AN_OBJECT`) for a config that is neither an object nor
    ``null``: no setting can be read from it, and saving it would leave an action nothing can run.
    """
    before = stored if isinstance(stored, Mapping) else {}
    was = str(before.get("provider") or "").strip()
    named = str(edit.get("provider") or "").strip()
    held = before.get("config")
    replaced = bool(named and was and named != was)
    config: dict[str, Any] = dict(held) if isinstance(held, Mapping) and not replaced else {}
    if "config" in edit:
        sent = edit["config"]
        if sent is None:
            config = {}
        elif not isinstance(sent, Mapping):
            raise ValueError(CONFIG_NOT_AN_OBJECT)
        else:
            for key, value in sent.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
    return {"provider": named or was, "config": config}


def edited_workflow(stored: Any, edit: Any) -> Any:
    """The ``workflow`` block an edit of a trigger leaves: the action it sends, in either shape,
    put over the action *stored* holds (:func:`edited_action`).

    The block keeps the shape it is stored in, so an edit that changes nothing leaves it as it was;
    a block that holds no action yet takes the edit's shape. A block that is not an object, or one
    naming a resume target, is no edit of an action (a resume target replaces the action), and is
    saved as sent. Raises ``ValueError`` as :func:`edited_action` does.
    """
    if not isinstance(edit, Mapping) or RESUME_TARGET_KEY in edit:
        return edit
    nested = edit.get("inline")
    if isinstance(nested, Mapping):
        sent: Mapping[str, Any] = nested
    elif "provider" in edit or "config" in edit:
        sent = edit
    else:
        return edit
    block = stored if isinstance(stored, Mapping) else {}
    action = edited_action(action_in(block), sent)
    if isinstance(block.get("inline"), Mapping):
        return {**block, "inline": action}
    if "provider" in block or "config" in block:
        return {**block, **action}
    return {"inline": action} if sent is nested else action
