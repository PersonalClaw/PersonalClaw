"""An automation's credential settings are kept in the credential store, never in ``triggers.json``.

An action provider's manifest declares which of its settings are credentials (``x-meta.sensitive``
on its ``settingsSchema``), as every app's settings do. App settings kept those in the credential
store; an automation's action config, written by the same schema-driven form, did not: a webhook
URL or an ``Authorization`` header typed into an automation was saved in ``triggers.json`` as
typed, and every snapshot and export carried it. Now each such field is held the way an app's
setting is (:mod:`personalclaw.config.secret_refs`):

* **Save** (:func:`hold`, from every write of the store): each field the action's app declares
  sensitive, or that is named like a credential, is moved into the credential store under a key
  this automation owns (``secret_refs.trigger_owner``), and the file keeps ``{{secret:…}}``. A
  value that already references a secret of the user's (``{{secret:NAME}}``) is left as it is.
  One given as an object or a list (``headers`` sent through the API or an agent's tool) is held
  as its JSON text, which the action reads back the same.
* **Read** (:func:`shown`): the reference is shown as the mask, and a form that sends the mask
  back keeps the stored value (:func:`kept_masks`). A value that is a web address keeps where it
  goes in sight (:func:`url_origin`): its scheme, host and port, the rest masked, so an owner asked
  to allow an edit sees where it now posts. An edit inside that mask is refused, so changing the
  host shown cannot keep the hidden rest.
* **Dispatch** (:func:`fill`): the automation's own references are filled as the action is handed
  its config, and a key that is gone refuses the fire, naming the field.
* **Delete** (:func:`purge`) deletes its keys; **boot** (:func:`migrate`) moves what an earlier
  release left in the file.

An action that is a model turn or that starts a run is left alone: its config is the turn's or the
run's, which keeps references by name and fills them where they are used.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personalclaw.config import secret_refs
from personalclaw.config.credentials import delete_credential, get_credential

logger = logging.getLogger(__name__)


class _Gone(Exception):
    """Why an automation's own reference does not fill: its key is no longer stored. Read by
    ``cannot_run.missing_secret`` as a refusal's cause and remedy."""

    cause = "the value this automation kept for it is no longer in the credential store"
    remedy = "open the automation and enter that setting again"


def owner(trigger_id: str) -> secret_refs.SecretOwner:
    """Whose keys an automation's held settings are stored under: that automation's alone."""
    return secret_refs.trigger_owner(trigger_id)


def _action(row: dict[str, Any]) -> dict[str, Any] | None:
    """The action an automation row declares, as stored (``workflow.inline`` or the flat
    ``workflow``), the dict its ``provider`` and ``config`` are in; ``None`` for none."""
    workflow = row.get("workflow")
    if not isinstance(workflow, dict):
        return None
    inline = workflow.get("inline")
    action = inline if isinstance(inline, dict) else workflow
    return action if action.get("provider") else None


def _declared(provider_name: str) -> set[str] | None:
    """The fields *provider_name*'s app declares sensitive; ``None`` for a core action, an action
    whose config is a model turn's or a run's, or one no provider here answers to (held as it is:
    the workflow lint goes on flagging a credential typed into a core action's config)."""
    from personalclaw.action_providers.registry import action_origin, get_action_provider

    provider = get_action_provider(provider_name)
    if provider is None:
        return None
    if getattr(provider, "hands_config_to_a_model", False) or getattr(
        provider, "hands_config_to_a_run", False
    ):
        return None
    origin = action_origin(provider_name)
    return secret_refs.declared_app_fields(origin.app) if origin is not None else None


def _user_reference_inside(value: Any) -> bool:
    """Whether *value* is text holding a reference to one of the user's secrets but is not itself
    one whole reference (``Bearer {{secret:TOKEN}}``): the credential is already in the store."""
    from personalclaw.triggers.secrets import SECRET_REF_RE

    return (
        isinstance(value, str)
        and bool(SECRET_REF_RE.search(value))
        and (secret_refs.ref_key(value) is None)
    )


def _as_text(value: Any) -> Any:
    """A held field's *value* as the store keeps it: an object or a list as its JSON text (the
    store holds text only, and the actions read either shape), anything else as it is."""
    return json.dumps(value) if isinstance(value, (dict, list)) and value else value


def held_fields(provider_name: str, config: dict[str, Any]) -> set[str] | None:
    """The fields of *config* kept in the credential store, ``None`` when none are held."""
    declared = _declared(provider_name)
    if declared is None:
        return None
    return {
        name
        for name in secret_refs.secret_fields(config, declared)
        if name in config and not _user_reference_inside(_as_text(config[name]))
    }


def hold_config(
    owner_id: str, provider_name: str, config: dict[str, Any], before: dict[str, Any] | None
) -> dict[str, Any]:
    """*config* as stored for the automation *owner_id*: each held field's value in the credential
    store under the automation's own key. *before* is its config as stored before this write, so a
    field the write leaves (or sends back masked) keeps its key and one it clears or rotates drops
    the old.

    Raises :class:`ValueError` (``secret_refs.ForeignSecretReference`` included) for a value no
    credential can hold, or a reference to a key this automation does not hold, and nothing is
    stored."""
    fields = held_fields(provider_name, config) if owner_id else None
    if not fields:
        return config
    mine = owner(owner_id)
    before = before if isinstance(before, dict) else {}
    config = kept_masks(config, before)
    secret_refs.refuse_foreign(config, owner=mine)
    part = secret_refs.store(
        {name: _as_text(config[name]) for name in fields},
        owner=mine,
        declared=fields,
        previous={name: before[name] for name in fields if name in before},
        refuse_logins=False,
    )
    for name, value in before.items():
        # A field the write no longer holds (cleared, or now a reference of the user's) lets its
        # own key go, as a cleared setting's does.
        key = secret_refs.ref_key(value)
        if key is not None and mine.owns_directly(key) and part.get(name) != value:
            delete_credential(key)
    return {**config, **part}


def hold(row: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """*row*, an automation in ``triggers.json``, with its action's config as :func:`hold_config`
    keeps it; *previous* is the row as stored before this write. The same row when nothing is
    held."""
    action = _action(row)
    config = action.get("config") if action is not None else None
    if action is None or not isinstance(config, dict):
        return row
    before_action = _action(previous) if isinstance(previous, dict) else None
    before = before_action.get("config") if before_action is not None else None
    stored = hold_config(
        str(row.get("id") or ""), str(action.get("provider") or ""), config, before
    )
    if stored is config:
        return row
    held = dict(row)
    workflow = dict(row["workflow"])
    if isinstance(workflow.get("inline"), dict):
        workflow["inline"] = {**workflow["inline"], "config": stored}
    else:
        workflow["config"] = stored
    held["workflow"] = workflow
    return held


def fill(trigger_id: str, config: Any) -> tuple[Any, tuple[str, ...]]:
    """*config* with the automation's own references filled, and the values filled into it, as
    the action is handed its config. A key that is gone raises
    :class:`~personalclaw.triggers.secrets.UnresolvedSecret` naming the field: an empty value would
    be sent as though it were the credential."""
    if not trigger_id or not isinstance(config, dict):
        return config, ()
    from personalclaw.config.credentials import get_credential
    from personalclaw.triggers.secrets import UnresolvedSecret

    mine = owner(trigger_id)
    out = dict(config)
    filled: list[str] = []
    for name, value in config.items():
        key = secret_refs.ref_key(value)
        if key is None or not mine.owns(key):
            continue
        secret = get_credential(key)
        if not secret:
            raise UnresolvedSecret(name, refused=_Gone())
        out[name] = secret
        filled.append(secret)
    return out, tuple(filled)


def _mask() -> str:
    from personalclaw.apps.secret_fields import SECRET_MASK

    return SECRET_MASK


def _held_value(value: Any) -> str | None:
    """What an automation-held reference stands for, ``None`` for any other value (or a key that is
    gone)."""
    key = secret_refs.ref_key(value)
    if key is None or not secret_refs.is_trigger_key(key):
        return None
    return get_credential(key)


def url_origin(value: Any) -> str:
    """Where *value* goes when it is an http(s) web address: its scheme, host and port
    (``https://hooks.example.com:8443``), else ``""``. The login it may carry is never part of it,
    and the host is written in ASCII (IDNA), so a name drawn to look like another reads as the one
    it is."""
    if not isinstance(value, str):
        return ""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
        host = (parts.hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return ""
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def _shown_value(value: Any) -> Any:
    """*value* as a read shows it: a held reference as the mask, a held web address as where it
    goes with the rest masked, anything else as it is."""
    if not secret_refs.is_trigger_key(secret_refs.ref_key(value)):
        return value
    origin = url_origin(_held_value(value))
    return f"{origin}/{_mask()}" if origin else _mask()


def shown(config: Any) -> Any:
    """*config* as a read shows it: every automation-held reference masked (:func:`_shown_value`),
    so neither the value nor the key it is kept under is handed out."""
    if not isinstance(config, dict):
        return config
    return {name: _shown_value(value) for name, value in config.items()}


def shown_row(row: Any) -> Any:
    """An automation row (``Trigger.to_dict``) as a read hands it out: its action's config as
    :func:`shown` shows it. What the agent's automation tools answer with."""
    action = _action(row) if isinstance(row, dict) else None
    if action is None or not isinstance(action.get("config"), dict):
        return row
    out = copy.deepcopy(row)
    target = _action(out)
    assert target is not None  # the same shape it was read from
    target["config"] = shown(action["config"])
    return out


def kept_masks(config: Any, stored: Any) -> Any:
    """*config* with each held setting it sends back as it was shown (the mask, or a web address's
    own partial mask) put back as the reference *stored* holds there, so every check after it and
    the save judge what is kept.

    Raises :class:`ValueError` for any other value that carries the mask: an edit inside it (the
    host of a partial mask changed, say) cannot keep the hidden rest, so nothing is saved."""
    if not isinstance(config, dict):
        return config
    before = stored if isinstance(stored, dict) else {}
    mask = _mask()
    out = dict(config)
    for name, value in config.items():
        if not isinstance(value, str) or mask not in value:
            continue
        kept = before.get(name)
        if secret_refs.is_trigger_key(secret_refs.ref_key(kept)) and value.strip() in (
            mask,
            _shown_value(kept),
        ):
            out[name] = kept
        elif value.strip() != mask:
            raise ValueError(
                f"{name}: part of a value kept hidden was changed, so the rest of it cannot be "
                "kept. Type the whole value again, or leave it as it was shown."
            )
    return out


def kept_action_masks(action: Any, stored: Any) -> Any:
    """*action* (``{provider, config}``) with its config as :func:`kept_masks` keeps it."""
    if not isinstance(action, dict) or not isinstance(action.get("config"), dict):
        return action
    config = stored.get("config") if isinstance(stored, dict) else None
    return {**action, "config": kept_masks(action["config"], config)}


def kept_workflow_masks(workflow: Any, stored: Any) -> Any:
    """*workflow* (an automation's, in either stored shape) with its action's config as
    :func:`kept_masks` keeps it against the action of *stored*."""
    action = _action({"workflow": workflow}) if isinstance(workflow, dict) else None
    if action is None or not isinstance(action.get("config"), dict):
        return workflow
    before = _action({"workflow": stored}) if isinstance(stored, dict) else None
    kept = kept_masks(action["config"], (before or {}).get("config"))
    if kept == action["config"]:
        return workflow
    out = copy.deepcopy(workflow)
    target = _action({"workflow": out})
    assert target is not None  # the same shape it was read from
    target["config"] = kept
    return out


def origins(config: Any) -> dict[str, str]:
    """Where each held web address in *config* goes (:func:`url_origin`), by setting: what an
    owner's yes to the action is a yes to send it to. A plain address (an edit not yet saved) counts
    as the held one it will be."""
    if not isinstance(config, dict):
        return {}
    found: dict[str, str] = {}
    for name, value in config.items():
        held = _held_value(value)
        origin = url_origin(held if held is not None else value)
        if origin:
            found[str(name)] = origin
    return found


def moved_origins(now: Any, allowed: Any) -> str:
    """The sentence that names where *now* sends each held web address the owner allowed to go
    elsewhere (*allowed*: ``{setting: origin}``, from :func:`origins`); ``""`` when none moved."""
    if not isinstance(allowed, dict) or not allowed:
        return ""
    current = origins(now)
    said: list[str] = []
    for name, was in sorted(allowed.items()):
        new = current.get(name, "")
        if new == was:
            continue
        if new:
            said.append(f"Its {name} now goes to {new}, not {was}.")
        else:
            said.append(f"Its {name} no longer goes to {was}.")
    return " ".join(said)


def shown_action(action: Any) -> Any:
    """*action* (``{provider, config}``) with its config as :func:`shown` shows it."""
    if not isinstance(action, dict) or not isinstance(action.get("config"), dict):
        return action
    return {**action, "config": shown(action["config"])}


def purge(trigger_id: str) -> int:
    """Delete every key the automation holds. How many there were."""
    return secret_refs.purge([owner(trigger_id).prefix]) if trigger_id else 0


def move_held_settings_at_start() -> list[str]:
    """Move what an earlier release left in plain text in ``triggers.json`` (:func:`migrate`) and
    ``hooks.json`` (``hooks.move_held_settings``) into the credential store, once at gateway boot,
    after the apps load and beside ``secret_refs.migrate_plaintext_secrets``. Here, above the
    store, so the store imports nothing from the automations; run through its fail-safe file mover
    (``secret_refs.move_files``), so one file that cannot be moved never blocks the other. Returns
    the home-relative paths it rewrote."""
    from personalclaw.config.loader import config_dir
    from personalclaw.config.secret_refs import move_files
    from personalclaw.hooks import move_held_settings

    home = config_dir()
    return move_files(
        [(home / "triggers.json", migrate), (home / "hooks.json", move_held_settings)]
    )


def migrate(path: Path) -> bool:
    """Move what an earlier release left in *path* (``triggers.json``) into the credential store:
    each automation's held fields, as :func:`hold` would on a save. ``True`` when the file changed.

    Called at boot after the apps load, so their schemas are known. Per automation: one whose
    action no provider here answers to, or whose value cannot be stored, is left exactly as it was,
    and the next boot tries again. Written through the store's own lock and writer."""
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(path.parent)
    with store._file_lock():
        rows = store._read_rows()
        moved: list[dict[str, Any]] = []
        changed = False
        for row in rows:
            try:
                held = hold(row, row) if isinstance(row, dict) else row
            except Exception:  # noqa: BLE001 - one automation must not hold the others
                logger.warning("could not move an automation's held settings", exc_info=True)
                held = row
            changed = changed or held != row
            moved.append(held)
        if changed:
            store._write(moved)
    return changed
