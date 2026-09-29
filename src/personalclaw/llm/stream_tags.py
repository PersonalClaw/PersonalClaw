"""Streaming cross-chunk tag scrubber.

A stateful splitter that separates inline-tagged spans (``<think>…</think>``,
``<memory>…</memory>``, ``<widget>…</widget>``) from surrounding answer text in a
token stream, **holding partial tag fragments across chunk boundaries** so a tag
that splits mid-chunk (``…<thi`` | ``nk>…``) is never mis-emitted as visible
text. One utility serves every inline-tag concern:

- Thinking models that embed reasoning as ``<think>…</think>`` in the content
  stream (DeepSeek-R1, Qwen via OpenRouter, other OpenAI-compatible endpoints)
  — split reasoning from answer (this port).
- Models that wrap their reasoning in CHANNEL control tokens instead
  (``<|channel>thought\\n…<channel|>``, Gemma 4): the first line after the opener
  names the channel and is not content.
- Later: memory / widget tag fencing + the context-transparency window, which
  feed the same splitter a different tag→kind map.

Design: a small state machine over a held buffer. Outside any span we scan for an
opener; if the buffer's tail *could* be the start of a marker (``<``, ``<thi``,
``<think``) we hold it rather than emit, because the rest may arrive in the next
chunk. Inside a span we accumulate until its closer, holding partial markers the
same way. The markers are control tokens, never content: a closer seen with no span
open (a model closing a span it never opened, or a template that opened it in the
prompt) is removed, and so is an opener repeated inside its span. At ``flush`` any
held buffer is emitted as ordinary text (the safe default — an unterminated marker
fragment degrades to visible content rather than being silently swallowed).
"""

from __future__ import annotations

from dataclasses import dataclass

# Kind labels the splitter emits. ``OUTSIDE`` is ordinary answer text; the others
# are whatever the caller mapped a tag to. Callers map these to their own event
# types (e.g. OUTSIDE → EVENT_TEXT_CHUNK, "thinking" → EVENT_THINKING_CHUNK).
KIND_OUTSIDE = "text"

#: The kind reasoning is emitted as by :func:`make_think_splitter`.
KIND_THINKING = "thinking"

#: The channel control tokens: ``<|channel>name\n…<channel|>``. Gemma 4 opens its reasoning
#: channel with ``<|channel>thought\n`` and closes it with ``<channel|>`` — its chat template's
#: own ``strip_thinking`` splits on exactly these two strings.
CHANNEL_OPEN = "<|channel>"
CHANNEL_CLOSE = "<channel|>"

#: The longest channel name waited for after a channel opener before the text that follows is
#: read as content. A name is one short word ("thought"); a line longer than this is not one.
_MAX_CHANNEL_NAME = 32


@dataclass
class Segment:
    """One resolved span: ``kind`` (KIND_OUTSIDE or a tag's mapped kind) + text."""

    kind: str
    text: str


@dataclass(frozen=True)
class _Marker:
    """One kind of span: the strings that open and close it, and what its content is."""

    opener: str
    closer: str
    kind: str
    #: The opener is followed by a one-line NAME that is not content (a channel's name).
    named: bool = False


class StreamingTagSplitter:
    """Cross-chunk tag splitter. Feed stream chunks; get resolved segments.

    ``tags`` maps a tag name (without angle brackets) to the kind emitted for its
    inner content — e.g. ``{"think": "thinking"}``. Tag names are matched
    case-insensitively. Only one span may be open at a time (the models that embed
    reasoning don't nest); an opener repeated inside an open span is a control token
    and is removed.

    ``channel`` is the kind a channel span (:data:`CHANNEL_OPEN` … :data:`CHANNEL_CLOSE`) is
    emitted as; ``""`` leaves channel tokens as ordinary text. ``inside`` starts the splitter
    inside a span of that kind — for a stream that is all one kind until a closer says
    otherwise, like a provider's separate reasoning field — so its text is that kind, and what
    follows a closer is ordinary text.
    """

    def __init__(self, tags: dict[str, str], *, channel: str = "", inside: str = "") -> None:
        self._markers = [
            _Marker(f"<{name.lower()}>", f"</{name.lower()}>", kind) for name, kind in tags.items()
        ]
        if channel:
            self._markers.append(_Marker(CHANNEL_OPEN, CHANNEL_CLOSE, channel, named=True))
        self._openers = tuple(m.opener for m in self._markers)
        self._all_closers = tuple(m.closer for m in self._markers)
        self._buf = ""
        # The open span: its kind (None = outside) and the closers that end it.
        self._kind: str | None = None
        self._closers: tuple[str, ...] = ()
        # Inside a named span, before its name line has ended.
        self._naming = False
        if inside:
            self._enter(inside, tuple(m.closer for m in self._markers if m.kind == inside))

    def feed(self, chunk: str) -> list[Segment]:
        """Feed a stream chunk; return the segments now fully resolved.

        Text whose classification can't yet be decided (a partial marker prefix) is
        retained internally and resolved on a later ``feed`` or at ``flush``. Adjacent
        segments of the same kind are coalesced so callers see one span per contiguous run.
        """
        if not chunk:
            return []
        self._buf += chunk
        out: list[Segment] = []
        while True:
            seg, consumed = self._step()
            if seg is not None:
                out.append(seg)
            if not consumed:
                break
        return _coalesce(out)

    def flush(self) -> list[Segment]:
        """Emit any held buffer at stream end as text (unterminated fragment → visible)."""
        text, naming = self._buf, self._naming
        self._buf = ""
        self._kind = None
        self._closers = ()
        self._naming = False
        # A channel's name with nothing after it is not content; anything else left — a
        # dangling ``<`` or a marker fragment — is surfaced so nothing is silently dropped.
        if not text or naming:
            return []
        return [Segment(KIND_OUTSIDE, text)]

    # ── internals ──

    def _enter(self, kind: str, closers: tuple[str, ...], *, named: bool = False) -> None:
        self._kind = kind
        self._closers = closers
        self._naming = named

    def _step(self) -> tuple[Segment | None, bool]:
        """Resolve as much of the buffer as is currently decidable.

        Returns ``(segment_or_None, made_progress)``. ``made_progress`` is True
        when the buffer shrank (so the caller loops again); False when the
        remainder must be held for more input.
        """
        if self._kind is None:
            return self._step_outside()
        if self._naming:
            return self._step_name()
        return self._step_inside()

    def _step_outside(self) -> tuple[Segment | None, bool]:
        idx = self._buf.find("<")
        if idx == -1:
            # No marker start at all — emit everything as text.
            text, self._buf = self._buf, ""
            return (Segment(KIND_OUTSIDE, text) if text else None, False)
        # Text before the '<' is unambiguously outside; emit it first.
        if idx > 0:
            text = self._buf[:idx]
            self._buf = self._buf[idx:]
            return (Segment(KIND_OUTSIDE, text), True)
        # Buffer starts with '<'. An opener opens its span.
        for marker in self._markers:
            if _startswith_ci(self._buf, marker.opener):
                self._buf = self._buf[len(marker.opener) :]
                self._enter(marker.kind, (marker.closer,), named=marker.named)
                return (None, True)
        # A closer with no span open is a control token, not text: removed.
        for closer in self._all_closers:
            if _startswith_ci(self._buf, closer):
                self._buf = self._buf[len(closer) :]
                return (None, True)
        # No full marker matched. Is the buffer a *prefix* of one (hold it)?
        if self._could_be_marker_prefix():
            return (None, False)  # hold; need more input
        # A '<' that can't start any known marker — emit it as literal text and move
        # on (so '<' in normal prose isn't held forever).
        self._buf = self._buf[1:]
        return (Segment(KIND_OUTSIDE, "<"), True)

    def _step_name(self) -> tuple[Segment | None, bool]:
        """Drop a named span's name line (``thought\\n`` after a channel opener)."""
        newline = self._buf.find("\n")
        close_at, closer = _earliest(self._buf, self._closers)
        if (
            close_at != -1
            and close_at <= _MAX_CHANNEL_NAME
            and (newline == -1 or close_at < newline)
        ):
            # A channel closed on its name line: empty, as a template prefills one to turn
            # reasoning off (``<|channel>thought\n<channel|>`` without its newline).
            self._buf = self._buf[close_at + len(closer) :]
            self._enter_outside()
            return (None, True)
        if newline != -1 and newline <= _MAX_CHANNEL_NAME:
            self._buf = self._buf[newline + 1 :]
            self._naming = False
            return (None, True)
        if len(self._buf) > _MAX_CHANNEL_NAME:
            # No name line: what follows the opener is content.
            self._naming = False
            return (None, True)
        return (None, False)  # hold; the name may still be arriving

    def _step_inside(self) -> tuple[Segment | None, bool]:
        assert self._kind is not None
        kind = self._kind
        close_at, closer = _earliest(self._buf, self._closers)
        open_at, opener = _earliest(self._buf, self._openers)
        if close_at != -1 and (open_at == -1 or close_at <= open_at):
            # Found the close. Emit inner content, drop the close marker, go outside.
            inner = self._buf[:close_at]
            self._buf = self._buf[close_at + len(closer) :]
            self._enter_outside()
            return (Segment(kind, inner) if inner else None, True)
        if open_at != -1:
            # An opener repeated inside its span: the content before it stays, the marker
            # (and a channel's name line after it) does not.
            inner = self._buf[:open_at]
            self._buf = self._buf[open_at + len(opener) :]
            self._naming = any(m.named for m in self._markers if m.opener == opener)
            return (Segment(kind, inner) if inner else None, True)
        # No marker yet. Emit content up to the largest point that can't be a partial
        # marker, holding the possible-marker tail.
        hold = max(
            (_max_suffix_overlap(self._buf, m) for m in (*self._closers, *self._openers)),
            default=0,
        )
        if hold == len(self._buf):
            return (None, False)  # whole buffer might be a partial marker
        emit = self._buf[: len(self._buf) - hold]
        self._buf = self._buf[len(self._buf) - hold :]
        return (Segment(kind, emit) if emit else None, bool(emit) and hold == 0)

    def _enter_outside(self) -> None:
        self._kind = None
        self._closers = ()
        self._naming = False

    def _could_be_marker_prefix(self) -> bool:
        """True if the buffer is a proper prefix of some marker (opener or closer)."""
        low = self._buf.lower()
        return any(
            len(low) < len(marker) and marker.lower().startswith(low)
            for marker in (*self._openers, *self._all_closers)
        )


def make_think_splitter(*, inside: bool = False) -> "StreamingTagSplitter":
    """A splitter for a model's reasoning: inline ``<think>`` tags and channel tokens.

    Self-gating: a stream that contains neither passes through entirely as
    ``KIND_OUTSIDE`` text, so it's safe to run unconditionally on every
    OpenAI-compatible / Ollama stream regardless of model. ``inside=True`` reads a
    stream that is reasoning from its first character — a provider's separate
    reasoning field — where only what follows a closing marker is the answer.
    """
    return StreamingTagSplitter(
        {"think": KIND_THINKING},
        channel=KIND_THINKING,
        inside=KIND_THINKING if inside else "",
    )


def _coalesce(segs: list[Segment]) -> list[Segment]:
    """Merge adjacent same-kind segments into one."""
    out: list[Segment] = []
    for s in segs:
        if out and out[-1].kind == s.kind:
            out[-1] = Segment(s.kind, out[-1].text + s.text)
        else:
            out.append(s)
    return out


def _startswith_ci(s: str, prefix: str) -> bool:
    return s[: len(prefix)].lower() == prefix.lower()


def _find_ci(s: str, sub: str) -> int:
    return s.lower().find(sub.lower())


def _earliest(s: str, markers: tuple[str, ...]) -> tuple[int, str]:
    """``(index, marker)`` of the first of *markers* in *s*, or ``(-1, "")``."""
    best, found = -1, ""
    for marker in markers:
        at = _find_ci(s, marker)
        if at != -1 and (best == -1 or at < best):
            best, found = at, marker
    return best, found


def _max_suffix_overlap(s: str, target: str) -> int:
    """Length of the longest suffix of ``s`` that is a prefix of ``target``.

    Used to decide how much of the buffer to hold back as a possible partial
    marker. E.g. for target ``</think>`` and buffer ``…ans</thi`` the tail
    ``</thi`` overlaps, so 5 chars are held until the next chunk resolves them.
    """
    low_s = s.lower()
    low_t = target.lower()
    max_len = min(len(low_s), len(low_t) - 1) if len(low_t) > 1 else 0
    for k in range(max_len, 0, -1):
        if low_s.endswith(low_t[:k]):
            return k
    return 0
