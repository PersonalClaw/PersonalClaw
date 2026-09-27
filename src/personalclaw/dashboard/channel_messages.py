"""What the dashboard sends the owner on a chat channel, and the link back to it.

A message read outside the dashboard (the `channel_dm` notification target, an approval asked on
a channel) needs an absolute link, because a bare `#/…` route leads nowhere there. The link's
base is the dashboard's declared public URL (`exposure.public_url`), else its configured
`dashboard.url`, normalized by `origin.dashboard_origin`, which is why this lives with them
rather than in `notification_rules`, which decides where a note goes and never how the dashboard
is addressed.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.config import loader as config_loader

logger = logging.getLogger(__name__)


def dashboard_link(fragment: str) -> str:
    """An absolute link to ``fragment`` (``#/…``) on this dashboard, or "" when none is known.

    For a message read OUTSIDE the dashboard (a channel DM), where a bare hash route leads
    nowhere. The base is the declared public URL, else ``dashboard.url`` — the URL an owner sets
    for links sent to chat channels. With neither, there is no honest link to give."""
    if not fragment:
        return ""
    try:
        from personalclaw.dashboard.exposure import public_url
        from personalclaw.dashboard.origin import dashboard_origin

        base = dashboard_origin(
            public_url() or str(config_loader.AppConfig.load().dashboard.url or "")
        )
    except Exception:  # noqa: BLE001 - a link is a courtesy; the message goes without it
        logger.debug("no dashboard base URL for a link", exc_info=True)
        return ""
    return f"{base}/{fragment.lstrip('/')}" if base else ""


def channel_dm_text(note: dict[str, Any]) -> str:
    """What the ``channel_dm`` target sends for ``note``: its title, its body, and its link.

    Redacted here as every outbound channel text is (a channel app redacts again), because this
    leaves the machine."""
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    parts = [str(note.get("title") or "").strip(), str(note.get("body") or "").strip()]
    text = "\n\n".join(p for p in parts if p)
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    link = dashboard_link(str(note.get("statusUrl") or ""))
    return f"{text}\n{link}" if link else text
