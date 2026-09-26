"""``send-message`` hook provider — deliver a message to a channel on an event.

Non-blocking native action. ``action_config`` shape::

    {
        "text_template": "Agent done: $CONTEXT",  # required; $EVENT/$CONTEXT/$<key>
        "channel": "C123...",  # optional channel id; else…
        "user": "U123...",     # optional user (DM); else owner DM
        "title": "Agent"       # optional heading
    }

Delivery goes through the provider-agnostic
:class:`~personalclaw.channel_delivery.ChannelDelivery` — the provider is
vendor-neutral about *which* channel backend. The owner's DM (no ``channel`` or
``user``) goes to the first connected channel that reaches the owner
(``channel_delivery.deliver_to_owner``), and to the Inbox, saying why, when none
does. When no channel is configured it falls back to a dashboard notification so
the action still surfaces. Text is redacted (credentials + exfiltration URLs)
before send.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw import notification_kinds
from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.services import get_action_services
from personalclaw.action_providers.template import render_template

logger = logging.getLogger(__name__)


class SendMessageActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "send-message"

    @property
    def display_name(self) -> str:
        return "Send Message"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        text = render_template(action_config.get("text_template", ""), ctx).strip()
        if not text:
            return ActionResult(success=False, error="send-message hook is missing 'text_template'")
        title = (action_config.get("title") or "").strip()

        services = get_action_services()
        if services is None:
            return ActionResult(
                success=False, error="send-message hook: services unavailable (startup not wired)"
            )
        state = services.state

        # Redact before anything leaves the process.
        try:
            from personalclaw.security import redact_credentials, redact_exfiltration_urls

            text, _ = redact_exfiltration_urls(text)
            text, _ = redact_credentials(text)
        except Exception:
            logger.debug("send-message: redaction unavailable", exc_info=True)

        body = f"*{title}*\n{text}" if title else text
        channel = (action_config.get("channel") or "").strip()
        user = (action_config.get("user") or "").strip()

        delivery = getattr(state, "channel_delivery", None)
        if delivery is None:
            # No channel backend — fall back to a dashboard notification so the
            # action is never a silent no-op.
            try:
                state.notify(notification_kinds.INFO, title or "Agent message", text)
            except Exception as exc:  # noqa: BLE001
                return ActionResult(
                    success=False, error=f"send-message: no channel + notify failed: {exc}"
                )
            return ActionResult(
                success=True, exit_code=0, stdout="no channel provider; delivered as notification"
            )

        try:
            if not channel and not user:
                # The owner's DM, on the first channel that reaches the owner with the id that
                # channel keeps for them — else the Inbox, saying why. It used to DM the one
                # shared id through whichever channel sorted first.
                from personalclaw.channel_delivery import deliver_to_owner

                owner = await deliver_to_owner(
                    lambda owner_delivery, dm: owner_delivery.deliver_text(dm, body),
                    title=title or "Agent message",
                    text=text,
                    state=state,
                )
                if owner.inboxed:
                    return ActionResult(
                        success=True,
                        exit_code=0,
                        stdout=f"delivered to the Inbox: {owner.sentence()}",
                    )
                if not owner.delivered:
                    return ActionResult(
                        success=False,
                        error=f"send-message: {owner.sentence() or 'no channel is connected'}",
                    )
                return ActionResult(success=True, exit_code=0, stdout=f"sent: {text[:80]}")
            target = channel or await delivery.open_dm(user)
            if not target:
                return ActionResult(
                    success=False, error="send-message: could not resolve a delivery target"
                )
            await delivery.deliver_text(target, body)
        except Exception as exc:  # noqa: BLE001 - error result, never raise
            return ActionResult(success=False, error=f"send-message failed: {exc}")
        return ActionResult(success=True, exit_code=0, stdout=f"sent: {text[:80]}")


def create_provider(config: dict[str, Any] | None = None) -> "SendMessageActionProvider":
    return SendMessageActionProvider()
