"""``send-message`` hook provider — deliver a message to a channel on an event.

Non-blocking native action. ``action_config`` shape::

    {
        "text_template": "Agent done: $CONTEXT",  # required; $EVENT/$CONTEXT/$<key>
        "channel": "C123...",  # optional channel id; else…
        "user": "U123...",     # optional user (DM); else owner DM
        "via": "telegram",     # optional: the chat channel it goes out on, and no other
        "title": "Agent"       # optional heading
    }

Delivery goes through the provider-agnostic
:class:`~personalclaw.channel_delivery.ChannelDelivery` — the provider is
vendor-neutral about *which* channel backend. The owner's DM (no ``channel`` or
``user``) goes to the channel ``via`` names, else the first connected channel that
reaches the owner (``channel_delivery.deliver_to_owner``), and to the Inbox, saying
why, when that one or none does. A ``channel`` or ``user`` id goes out on the channel
``via`` names, else on the one channel it belongs to (``channel_delivery.channel_of_id``);
an id more than one channel set up here could have issued, or none, is refused with the
channels to choose from, where the trigger is saved (:func:`config_problem`) and when it
fires. When no channel is configured and none is named it falls back to a dashboard
notification so the action still surfaces. Text is redacted (credentials + exfiltration
URLs) before send.
"""

from __future__ import annotations

from typing import Any

from personalclaw import notification_kinds
from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.services import get_action_services
from personalclaw.action_providers.template import render_for_a_person


def route_of(action_config: dict[str, Any] | None, *, transports: Any = None) -> tuple[str, str]:
    """The chat channel this action sends on, as ``(key, "")``, or ``("", why it can't)``.

    The one ``via`` names, whose own ids a ``channel`` id must be; else the one a ``channel`` or
    ``user`` id belongs to. ``("", "")`` for the owner's DM with no channel named, which goes to the
    first channel that reaches the owner. *transports* is the chat channels to look in, by key; the
    registered ones when None (a process without the gateway builds its own).
    """
    from personalclaw.channel_delivery import channel_of_id, named_chat_channel, target_problem

    config = action_config or {}
    via = str(config.get("via") or "").strip()
    channel = str(config.get("channel") or "").strip()
    user = str(config.get("user") or "").strip()
    if via:
        # The name as the Triggers page may hold it ("Telegram").
        key, problem = named_chat_channel(via, transports=transports)
        if not problem and channel:
            problem = target_problem(key, channel, transports=transports)
        return ("", problem) if problem else (key, "")
    if channel or user:
        return channel_of_id(channel or user, user=not channel, transports=transports)
    return "", ""


def config_problem(action_config: dict[str, Any] | None, *, transports: Any = None) -> str:
    """Why this action could not send, or ``""`` when it could. Asked where the trigger is saved
    (`dashboard/handlers/triggers._action_problem`, `triggers.tools`), so an action every fire
    would refuse is refused in the form that wrote it: a ``via`` that names no chat channel set up
    here, an id that channel does not take, an id without ``via`` that no channel set up here, or
    more than one, takes. *transports* as :func:`route_of` reads it."""
    return route_of(action_config, transports=transports)[1]


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
        text = render_for_a_person(action_config.get("text_template", ""), ctx).strip()
        if not text:
            return ActionResult(success=False, error="send-message hook is missing 'text_template'")
        title = (action_config.get("title") or "").strip()

        services = get_action_services()
        if services is None:
            return ActionResult(
                success=False, error="send-message hook: services unavailable (startup not wired)"
            )
        state = services.state

        # Redact before anything leaves the process; a text the redactor fails on is withheld.
        from personalclaw.security import redact_or_withhold

        text = redact_or_withhold(text)

        body = f"*{title}*\n{text}" if title else text
        channel = (action_config.get("channel") or "").strip()
        user = (action_config.get("user") or "").strip()
        from personalclaw.channel_delivery import channel_shown_as, deliver_to_owner, delivery_for

        # The channel it goes out on: the one `via` names, else the one its id belongs to. An id
        # used to go to whichever channel sorted first, so another platform's id was posted there.
        via, problem = route_of(action_config)
        if problem:
            return ActionResult(success=False, error=f"send-message: {problem}")

        if via:
            delivery = delivery_for(via)
        else:
            # An id goes out only on its own channel, and with no chat channel set up it has none.
            delivery = None if channel or user else getattr(state, "channel_delivery", None)
        if delivery is None and not via:
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
                # The owner's DM, on the channel named, else the first channel that reaches the
                # owner, with the id that channel keeps for them — else the Inbox, saying why. It
                # used to DM the one shared id through whichever channel sorted first.
                owner = await deliver_to_owner(
                    lambda owner_delivery, dm: owner_delivery.deliver_text(dm, body),
                    title=title or "Agent message",
                    text=text,
                    state=state,
                    only=via,
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
            if delivery is None:
                shown = channel_shown_as(via)
                return ActionResult(
                    success=False,
                    error=f"send-message: {shown} isn't connected, so it was not sent",
                )
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
