"""Which channel thread a chat continues, and on which channel, read from the session store.

The one reading of a chat's link (``session_map.json``, through ``SessionManager``), for every
layer: the dashboard's ``channel_links.chat_link`` and ``chat_channel`` answer with it, and core's
readers that may not import the dashboard (the triage digest's channel lane, ``proactive.collect``)
ask it directly. Read from the store each time it is asked, whoever wrote the link, and a store
that cannot be read answers no link.
"""

from __future__ import annotations

from typing import Any

from personalclaw.constants import dashboard_history_key


def chat_link(sessions: Any, name: str) -> tuple[str, str]:
    """The channel thread the chat *name* continues on, as ``(thread, channel id)``, from the
    session store *sessions*, or ``("", "")`` when it is on none or the store cannot be read."""
    if sessions is None:
        return "", ""
    try:
        thread, channel_id = sessions.get_channel_link(dashboard_history_key(name))
    except Exception:  # noqa: BLE001 - see the module docstring
        return "", ""
    if not isinstance(thread, str) or not thread:
        return "", ""
    return thread, channel_id if isinstance(channel_id, str) else ""


def chat_channel(sessions: Any, name: str) -> str:
    """The channel the chat *name* continues on, as its link names it (``"slack"``), from the
    session store *sessions*, or ``""`` when it is on no thread, its link names no channel, or the
    store cannot be read."""
    if sessions is None:
        return ""
    try:
        provider = sessions.get_channel_provider(dashboard_history_key(name))
    except Exception:  # noqa: BLE001 - see the module docstring
        return ""
    return provider if isinstance(provider, str) else ""
