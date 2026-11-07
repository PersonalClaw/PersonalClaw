"""`{{secret:KEY}}` in a trigger's action config.

**🔴 THE DEFECT THIS CLOSES.** Workflows have carried `{{secret:KEY}}` — the validator
rejects an inline credential and tells the author to use that form, and three separate surfaces say
so in their error text. A TRIGGER action did not resolve it. Driven before writing a line:

    bash action, command "echo tok={{secret:MY_KEY}}"
      → stdout: tok={{secret:MY_KEY}}        # the literal placeholder reached the shell

So a user following the documented pattern got a broken command, and the only way to make a trigger
authenticate was to paste the credential into `triggers.json` — a file that is world-readable in the
home, copied into every snapshot, and echoed into run records and the
UI. The guidance and the mechanism disagreed, and the mechanism won.

**Why this module and not `workflows/secrets.py`.** That module's resolution lives inside the
workflow engine's binding context (`BindingContext.secret_resolver`, reached through `_walk_path`
and the pipe grammar). A trigger action config is a flat `{provider, config}` dict resolved at
dispatch with no binding tree, so reusing it would mean building a fake context around two lines of
string substitution. What IS shared is the thing that matters: both resolve through the one resolver
(`llm.credentials.resolve_secret`). An automation's own action reads the gateway's environment
first; a workflow run reads the store alone, and so does what checks the references a run is handed
(:func:`check`), as that run will read them. An automation
runs in no project, so its action reads only the global secrets: a project's secret is read by that
project's runs alone (a workflow run an automation starts in a project reads it in its own steps).

**The disciplines, and why each one is here:**

* **Resolution is at DISPATCH, never at save.** The stored config keeps the placeholder, so the
  secret is not in `triggers.json`, not in a snapshot, and not in the run record.
* **An unresolved key is an ERROR, not an empty string.** Substituting "" would run
  `curl -H "Authorization: Bearer "` — a request that fails somewhere remote with a 401 the user
  cannot trace back to a missing credential. Refusing names the key.
* **Resolved values NEVER travel back.** The resolved dict is handed to the provider and dropped;
  nothing writes it. What the action answers is masked of every value filled in before anything
  keeps or shows it (`filled_secrets.masked_answer`): a command can print what it was handed.
* **An action that starts a workflow run is handed its references unfilled.** Its config is the
  run's inputs, which the run's record keeps, so filling them here wrote the value into the run,
  its ledger and the run list (`resolve_for`). The run fills them where a step uses them.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: `{{secret:KEY}}`, with optional inner whitespace so `{{ secret:KEY }}` works too — a user who
#: pads the braces has expressed the same intent, and failing on whitespace would be the kind of
#: silent near-miss that sends someone back to pasting the credential inline.
SECRET_REF_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")


class UnresolvedSecret(Exception):
    """A trigger's action references a credential that is not configured, or one it may not read.

    Carries the KEY, because "a secret is missing" without the name leaves the user checking every
    credential they have. ``refused`` is the store's refusal of a key nothing reads by name
    (``llm.credentials.SecretNameRefused``: a setting's own key, or a project's stored key): that
    key is set, so the sentence says why no action can read it instead of calling it missing.
    """

    def __init__(self, key: str, *, refused: Exception | None = None) -> None:
        self.key = key
        self.refused = refused
        if refused is not None:
            message = f"the action references {{{{secret:{key}}}}}, but {refused}"
        else:
            message = (
                f"the action references {{{{secret:{key}}}}}, which the credential store does "
                "not hold. Store it in Settings → Secrets, or remove the reference."
            )
        super().__init__(message)


def references(value: Any) -> list[str]:
    """Every secret key referenced anywhere in `value`, in first-seen order.

    Walks dicts, lists and strings, because an action config nests (`{"headers": {"Authorization":
    "Bearer {{secret:X}}"}}` is the shape a webhook action actually has). Order is stable so an
    error message and a doctor finding name keys the same way twice.
    """
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for key in SECRET_REF_RE.findall(node):
                if key not in found:
                    found.append(key)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return found


def default_resolver(key: str) -> str:
    """Resolve one key as an automation reads it: the global secret, through the one resolver
    (`llm.credentials.resolve_secret`, with no project). "" when unset.

    The same resolver and the same empty-on-missing contract a workflow step has. An automation's
    action reads the gateway's environment first, where a workflow step reads the store alone
    (`workflows.node_bindings._secrets_for`). The caller decides what "" means; `resolve()` below
    treats it as a refusal.
    """
    from personalclaw.llm import credentials as llm_credentials

    try:
        return llm_credentials.resolve_secret(key).secret
    except KeyError:
        return ""
    except Exception:  # noqa: BLE001 - an unreadable store is a missing secret, not a crash
        logger.debug("credential store unreadable while resolving %r", key, exc_info=True)
        return ""


def resolve(config: Any, *, resolver: Callable[[str], str] | None = None) -> Any:
    """`config` with every `{{secret:KEY}}` replaced by its value. Raises `UnresolvedSecret`.

    Returns the input UNCHANGED (same object) when it holds no reference at all, so the common case
    costs one regex scan and allocates nothing.

    A reference that is the WHOLE string yields the raw value; one embedded in a larger string is
    substituted in place (`"Bearer {{secret:X}}"`). Both matter: the first carries a token as a
    field, the second builds a header.

    `resolver` is injected so a test never touches real credentials — the same seam the workflow
    engine uses, and the reason this module's tests need no credential store.
    """
    return _resolved(config, resolver)[0]


def _resolved(config: Any, resolver: Callable[[str], str] | None) -> tuple[Any, tuple[str, ...]]:
    """:func:`resolve`'s answer, and the values it filled in."""
    keys = references(config)
    if not keys:
        return config, ()

    from personalclaw.llm.credentials import name_refusal

    fn = resolver or default_resolver
    values: dict[str, str] = {}
    for key in keys:
        refused = name_refusal(key)
        if refused is not None:
            # Refused before any resolver reads it: an owned key is read only through the
            # settings record that references it, and a project's secret only by that project's
            # runs, by its own name — never by its stored key from an action or a command.
            raise UnresolvedSecret(key, refused=refused)
        value = fn(key)
        if not value:
            # 🔴 REFUSE, do not substitute "". An empty Authorization header produces a remote 401
            # the user cannot trace to a missing credential; naming the key is the whole point.
            raise UnresolvedSecret(key)
        values[key] = value

    def _sub(node: Any) -> Any:
        if isinstance(node, str):
            whole = SECRET_REF_RE.fullmatch(node.strip())
            if whole:
                return values[whole.group(1)]
            return SECRET_REF_RE.sub(lambda m: values[m.group(1)], node)
        if isinstance(node, dict):
            return {k: _sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_sub(v) for v in node]
        if isinstance(node, tuple):
            return tuple(_sub(v) for v in node)
        return node

    return _sub(config), tuple(values.values())


class ActionRefused(UnresolvedSecret):
    """A trigger's action whose config names a secret where its provider refuses one (Browse's start
    page: ``browse.credentials.start_page_refusal``), refused as an unset one is, in its own
    words."""

    def __init__(self, message: str) -> None:
        Exception.__init__(self, message)
        self.key = ""
        self.refused = None
        #: What the run's record says of it, in its own words (``cannot_run.missing_secret``).
        self.said = message


def resolve_for(provider: Any, config: Any, *, trigger_id: str = "") -> tuple[Any, tuple[str, ...]]:
    """*config* as the action *provider* is handed it, by either dispatch (a fire, and a run by
    hand or from outside), and the values filled into it: every `{{secret:KEY}}` filled
    (:func:`resolve`, which raises `UnresolvedSecret`), except in an action that IS a model turn
    (`ActionProvider.hands_config_to_a_model`) or that starts a workflow run with its config
    (`ActionProvider.hands_config_to_a_run`). The dispatch masks those values out of what the
    action answers before anything keeps or shows it (`filled_secrets.masked_answer`).

    A model turn's config is what an agent's model is handed, so a reference there stays the name
    and the agent's tools fill it when they run; resolved here, it would put the value in the
    model's context. A run's inputs are what its record keeps, so a reference there stays the
    reference and the run fills it where a step uses it; resolved here, the record would hold the
    value. The second is checked all the same, against the secrets that run reads (:func:`check`),
    so a fire whose run could not read one is refused as any other such fire is.

    *trigger_id* names the automation whose config this is: what it keeps in the credential store
    (``triggers.held_secrets``) is filled first, under its own key, and counted among the values
    filled, and a key of its that is gone refuses the fire."""
    page = getattr(provider, "start_page_key", None) if isinstance(config, dict) else None
    written = config.get(page) if page else None
    if page:
        from personalclaw.browse.credentials import start_page_refusal

        if (refused := start_page_refusal(written)) is not None:
            raise ActionRefused(f"{refused[0]}: {refused[1]}")
    kept: dict[str, Any] = {}
    if getattr(provider, "hands_config_to_a_model", False):
        keys = getattr(provider, "model_text_keys", None)
        if not keys or not isinstance(config, dict):
            return config, ()
        # Only the keys its model reads keep their references (Browse's goal); the rest it runs.
        kept = {key: config[key] for key in keys if key in config}
        config = {key: value for key, value in config.items() if key not in kept}
    if getattr(provider, "hands_config_to_a_run", False):
        project = config.get("project_id", "") if isinstance(config, dict) else ""
        check(references(config), project_id=str(project or ""))
        return config, ()
    from personalclaw.triggers.held_secrets import fill

    config, held = fill(trigger_id, config)
    config, filled = _resolved(config, None)
    if page and isinstance(config, dict):
        from personalclaw.browse.credentials import start_page_moved

        if (moved := start_page_moved(written, config.get(page))) is not None:
            raise ActionRefused(f"{moved[0]}: {moved[1]}")
    return ({**config, **kept} if kept else config), held + filled


def handed(provider: Any, config: Any) -> tuple[str, ...]:
    """The secrets whose references *config* hands on to the run *provider* starts with it, for
    `ActionContext.secret_references`: every one, since a trigger's config is all its author's
    text. ``()`` for any other provider, whose dispatch fills its references or keeps them as
    names."""
    if not getattr(provider, "hands_config_to_a_run", False):
        return ()
    return tuple(references(config))


def check(keys: list[str] | tuple[str, ...], *, project_id: str = "") -> None:
    """Raise `UnresolvedSecret` for the first of *keys* a run of *project_id* could not read: a
    name nothing reads by name, or one neither its project's secrets nor the global ones hold.

    Through the one resolver (`llm.credentials.resolve_secret`), from the store alone, as the run
    will read each when a step uses it: a name only the gateway's environment holds is not one the
    run reads by name. The value it reads is dropped here."""
    from personalclaw.llm import credentials as llm_credentials

    for key in keys:
        refused = llm_credentials.name_refusal(key)
        if refused is not None:
            raise UnresolvedSecret(key, refused=refused)
        try:
            read = llm_credentials.resolve_secret(key, project_id=project_id, environment=False)
            present = bool(read.secret)
        except KeyError:
            present = False
        except Exception:  # noqa: BLE001 - an unreadable store is a missing secret, not a crash
            logger.debug("credential store unreadable while checking %r", key, exc_info=True)
            present = False
        if not present:
            raise UnresolvedSecret(key)
