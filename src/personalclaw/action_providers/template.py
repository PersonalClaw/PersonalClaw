"""Shared ``$payload`` template renderer for action providers.

The webhook provider renders ``body_template`` with ``string.Template``, and the
native providers (notify / send-message / create-task) need the exact same
substitution for their own ``*_template`` fields. Factoring it here keeps one
renderer with one set of placeholder semantics:

* ``$EVENT``   → the lifecycle event name (e.g. ``Stop``)
* ``$CONTEXT`` → the free-form context string
* ``$<key>``   → any key from the structured event payload (``ctx.payload``)

``safe_substitute`` is used so a missing key leaves the ``$placeholder`` verbatim
rather than raising — a hook template can never crash a lifecycle event.
"""

from __future__ import annotations

from string import Template

from personalclaw.action_providers.base import ActionContext


def render_template(tmpl: str, ctx: ActionContext) -> str:
    """Substitute ``$EVENT`` / ``$CONTEXT`` / ``$<payload-key>`` in ``tmpl``.

    Safe (no ``KeyError``): unknown placeholders are left untouched.
    """
    if not tmpl:
        return ""
    mapping: dict[str, str] = {"EVENT": ctx.event, "CONTEXT": ctx.context}
    mapping.update({k: str(v) for k, v in (ctx.payload or {}).items()})
    try:
        return Template(tmpl).safe_substitute(mapping)
    except Exception:
        # A malformed template (e.g. a lone ``$``) must not break firing — return
        # the raw string so the action still does something sensible.
        return tmpl
