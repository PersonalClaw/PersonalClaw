"""Process-global, in-memory screen-frame slot — ONE frame per session, latest-wins.

MULTIMODAL-IO §5.3/§5.4. When a user shares their screen into a chat, the browser
POSTs a single JPEG/PNG frame per turn to ``/api/chat/screen-frame``. That frame is
held HERE — in a bounded dict of process memory — until the next turn drains it, and
then it is gone.

Three properties this module exists to guarantee, each of them load-bearing:

* **Never on disk.** Nothing in this file opens, writes, or names a file, and the
  frame bytes never reach a log record. A screenshot of someone's screen is the most
  intimate payload the platform handles; the only way to be sure it isn't sitting in
  ``~/.personalclaw`` after the fact is for the code that holds it to have no write
  path at all. Persistence exists only via the user's explicit "Pin frame" action,
  which goes through the ordinary uploads store — a different module, a different
  verb, and a deliberate one.
* **Latest-wins, exactly one.** :func:`stage` REPLACES; it never appends. A slow turn
  followed by three more captures leaves one frame — the newest — so a drain can
  never hand the model a stale view of a screen the user has since navigated away
  from. There is no queue to grow and nothing to flush.
* **One-shot drain.** :func:`drain` POPS. A second drain on the same session returns
  ``None``, so a frame captured for "what's wrong with this diff?" cannot silently
  ride along on the unrelated question that follows it.

Slots die with the process. That is the feature, not a limitation: a gateway restart
is a hard reset of everything the assistant could see.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Bounded so a long-running gateway that opened many sessions can't grow the map
# without limit. Frames are large, so this is deliberately much smaller than the
# session registries in ``session_restrictions`` — 32 live shares is already far
# beyond a personal-scale instance.
_MAX_SESSIONS = 32

# DECODED-payload ceiling — the check converts the base64 length before comparing.
# A 1568px-long-edge JPEG (the downscale the frontend
# applies before encoding) lands well under 1 MiB; 6 MiB leaves room for a
# lossless PNG of a 4K window without letting a client pin arbitrary memory.
MAX_FRAME_BYTES = 6 * 1024 * 1024

# The only image types accepted. A closed set, because "whatever the client said"
# would let a caller stage an SVG (which can carry script) or an arbitrary blob and
# have it forwarded to a model as an image part.
ALLOWED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class FrameRejected(ValueError):
    """A staged frame was malformed, oversized, or of a disallowed type."""


@dataclass(frozen=True)
class ScreenFrame:
    """One captured frame, held in memory only.

    ``b64`` is the base64 payload WITHOUT a data-URL prefix. ``data_url()`` builds
    the wire form on demand, so the joined string exists only for as long as the
    provider call that needs it.
    """

    media_type: str
    b64: str

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.b64}"

    @property
    def byte_len(self) -> int:
        """Decoded size, for audit records. Safe to log — a length is not content."""
        return (len(self.b64) * 3) // 4


_slots: OrderedDict[str, ScreenFrame] = OrderedDict()


def parse_frame(raw: str) -> ScreenFrame:
    """Validate a client-supplied frame payload into a :class:`ScreenFrame`.

    Accepts a ``data:<media-type>;base64,<payload>`` URL (what ``canvas.toDataURL``
    produces) or a bare base64 payload, which is then assumed to be JPEG — the
    frontend's encode format.

    Raises :class:`FrameRejected` with a reason that never quotes the payload. An
    error message that echoed the frame back would defeat the whole point of not
    logging it, since error strings land in SEL records and gateway logs.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise FrameRejected("empty frame")
    payload = raw.strip()
    media_type = "image/jpeg"
    if payload.startswith("data:"):
        head, _, body = payload.partition(",")
        if not body:
            raise FrameRejected("malformed data URL")
        meta = head[len("data:") :]
        if not meta.endswith(";base64"):
            raise FrameRejected("frame must be base64-encoded")
        media_type = meta[: -len(";base64")].strip().lower()
        payload = body
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise FrameRejected(f"unsupported media type: {media_type}")
    # Length check BEFORE decoding: a caller must not be able to make the gateway
    # materialise 500 MiB just to be told the frame was too big.
    if (len(payload) * 3) // 4 > MAX_FRAME_BYTES:
        raise FrameRejected("frame exceeds size limit")
    try:
        base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FrameRejected("frame is not valid base64") from exc
    return ScreenFrame(media_type=media_type, b64=payload)


def stage(session_key: str, frame: ScreenFrame) -> None:
    """Put *frame* in *session_key*'s slot, REPLACING whatever was there.

    Latest-wins is the whole contract: no list, no append, no second frame.
    """
    if not session_key:
        return
    _slots[session_key] = frame
    _slots.move_to_end(session_key)
    while len(_slots) > _MAX_SESSIONS:
        _slots.popitem(last=False)


def drain(session_key: str) -> ScreenFrame | None:
    """Remove and return *session_key*'s staged frame, or ``None`` if empty.

    POP, not read: after this returns the frame is unreachable from here, so a
    later turn cannot re-serve it.
    """
    if not session_key:
        return None
    return _slots.pop(session_key, None)


def pending(session_key: str) -> bool:
    """True if a frame is staged for *session_key* (does not consume it)."""
    return bool(session_key) and session_key in _slots


def clear(session_key: str) -> None:
    """Drop *session_key*'s slot — share stopped, or session closed."""
    if session_key:
        _slots.pop(session_key, None)


def clear_all() -> None:
    """Drop every slot.

    Test-only. There is deliberately no shutdown hook calling this: slots live in
    process memory, so process exit already clears them, and a hook would imply the
    frames could otherwise outlive the process.
    """
    _slots.clear()


def live_sessions() -> int:
    """How many sessions currently hold a staged frame (for status/diagnostics)."""
    return len(_slots)


# ── Delivery routing (§5.3) ───────────────────────────────────────────────────
#
# Pure policy: given the platform's answer about images for the model a session is bound
# to (``providers.image_input`` — the provider type's ``supports_vision`` and the model's
# ``image_modality`` tag, the same record an attached image is decided by), decide HOW a
# frame can reach it. The route (which tells the UI whether to offer the control, and why
# not) and the chat runner (which actually delivers) both route through here, so a toggle
# cannot be enabled for a model that then can't read what it is sent.
#
# One honest caveat: the two callers cannot always supply the same answer. The runner asks
# about the LIVE runtime (``served_model_ref`` — the entry and model actually serving the
# turn); the route asks about that same runtime when the session already has one, and about
# the model the session's selection resolves to when it does not. The divergence, when there
# is one, is the gap between a selection and the model a fresh runtime then serves.

#: A frame rides the turn as a real image content part.
DELIVERY_NATIVE = "native"
#: The session's model can't read images, but something else reads them — the model bound
#: to image understanding, or with nothing bound a chat model that takes images
#: (``providers.image_input.image_reader``) — so the frame is described once and the
#: FENCED text is injected instead.
DELIVERY_DESCRIBED = "described"
#: Nothing can read the frame. The control is offered disabled, with the reason.
DELIVERY_NONE = "none"


async def resolve_delivery(accepts_images: bool) -> tuple[str, str]:
    """Return ``(mode, reason)`` for a frame on a session whose model ``accepts_images``.

    ``reason`` is empty for the two working modes and, for
    :data:`DELIVERY_NONE`, is the user-facing sentence the disabled control shows —
    so the UI never has to invent its own explanation for a decision made here. The
    described mode asks the same image reader the description itself is built from, so the
    control is never offered for a frame nothing would read.
    """
    if accepts_images:
        return DELIVERY_NATIVE, ""
    try:
        from personalclaw.providers.image_input import image_reader

        if (await image_reader()).ref:
            return DELIVERY_DESCRIBED, ""
    except Exception:  # noqa: BLE001 — a broken registry is "no vision", not a crash
        logger.debug("screen share: image-reader probe failed", exc_info=True)
    return DELIVERY_NONE, "Bind a vision model in Settings → Models to share your screen."
