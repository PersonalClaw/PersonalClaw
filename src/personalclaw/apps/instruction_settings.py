"""The instructions an app's settings hold for the owner, and the one test of a text handed over as
one.

An app's manifest may declare a setting an **instruction**: ``x-meta.instruction: true`` on a
string property of its settings schema, at any depth (a column of a table of rows included). Its
value is words the owner writes for the agent to follow, such as the prompt Mail Inbox runs for
mail to one of its addresses, and she writes it on the app's Configure page.

An app hands core such an instruction beside the text from outside it is about
(``IncomingMessage.instruction`` beside the message's ``text``), and that text reaches a model
fenced, as data, while the instruction reaches it outside any fence, as an instruction. So core
takes a text handed over as an instruction only when :func:`holds` finds it, word for word, in a
setting the app's manifest declares an instruction: as the owner's settings hold it now, or as the
manifest gives the setting by default (the app's own definition). Anything else is not an
instruction, whatever the app called it: text from outside cannot reach a model as one by being
handed over as one. It would have to be one of her settings first.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


def _declared(schema: Any, value: Any) -> Iterator[str]:
    """Every instruction *value* holds under *schema*, and every default *schema* declares for
    one, however deeply the schema nests them: an object's properties, an array's items. A value
    the settings do not hold is read as the schema's default for it."""
    if not isinstance(schema, dict):
        return
    if value is None:
        value = schema.get("default")
    meta = schema.get("x-meta")
    if isinstance(meta, dict) and meta.get("instruction") is True:
        if meta.get("sensitive"):
            # A secret is never words for the agent to follow, and its file holds a reference.
            return
        for text in (value, schema.get("default")):
            if isinstance(text, str):
                yield text
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        held = value if isinstance(value, dict) else {}
        for key, spec in properties.items():
            yield from _declared(spec, held.get(key))
    items = schema.get("items")
    if isinstance(items, dict):
        for element in value if isinstance(value, list) else []:
            yield from _declared(items, element)
        # What a row the owner has not written yet would hold: the item's own declared defaults.
        yield from _declared(items, None)


def instructions(app: str) -> frozenset[str]:
    """Every instruction *app*'s settings hold now, and every default its installed manifest gives
    a setting it declares an instruction, each without the spaces around it.

    Read from the manifest the owner installed and the settings file her Configure page writes
    (``providers.settings.load_stored``), across every schema that describes that one file: the
    app's ``setup.configSchema`` and each provider's ``settingsSchema``. An app that is not
    installed, or declares no instruction, holds none."""
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.providers.settings import load_stored

    manifest = _manifest_of(app) if app else None
    if manifest is None:
        return frozenset()
    schemas = [getattr(manifest.setup, "configSchema", None) or {}]
    schemas += [provider.settingsSchema or {} for provider in manifest.all_providers()]
    stored = load_stored(app)
    return frozenset(
        text.strip() for schema in schemas for text in _declared(schema, stored) if text.strip()
    )


def holds(app: str, text: str) -> bool:
    """Whether *text* is, word for word bar the spaces around it, an instruction *app* holds for
    the owner (:func:`instructions`): what core takes as hers when the app hands it over beside
    text from outside. Never raises: an app whose manifest or settings cannot be read holds
    nothing."""
    wanted = (text or "").strip()
    if not wanted:
        return False
    try:
        return wanted in instructions(app)
    except Exception:  # noqa: BLE001 - fail closed: what cannot be checked is not hers
        logger.warning("could not read %s's settings for an instruction", app, exc_info=True)
        return False
