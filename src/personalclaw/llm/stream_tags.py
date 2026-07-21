"""Streaming cross-chunk tag scrubber.

A stateful splitter that separates inline-tagged spans (``<think>…</think>``,
``<memory>…</memory>``, ``<widget>…</widget>``) from surrounding answer text in a
token stream, **holding partial tag fragments across chunk boundaries** so a tag
that splits mid-chunk (``…<thi`` | ``nk>…``) is never mis-emitted as visible
text. One utility serves every inline-tag concern:

- Thinking models that embed reasoning as ``<think>…</think>`` in the content
  stream (DeepSeek-R1, Qwen via OpenRouter, other OpenAI-compatible endpoints)
  — split reasoning from answer (this port).
- Later: memory / widget tag fencing + the context-transparency window, which
  feed the same splitter a different tag→kind map.

Design: a small state machine over a held buffer. Outside any tag we scan for an
opening ``<tag>``; if the buffer's tail *could* be the start of one (``<``,
``<thi``, ``<think``) we hold it rather than emit, because the rest may arrive in
the next chunk. Inside a tag we accumulate until the closing ``</tag>``, holding
partial closes the same way. At ``flush`` any held buffer is emitted as ordinary
text (the safe default — an unterminated tag degrades to visible content rather
than being silently swallowed).
"""

from __future__ import annotations

from dataclasses import dataclass

# Kind labels the splitter emits. ``OUTSIDE`` is ordinary answer text; the others
# are whatever the caller mapped a tag to. Callers map these to their own event
# types (e.g. OUTSIDE → EVENT_TEXT_CHUNK, "thinking" → EVENT_THINKING_CHUNK).
KIND_OUTSIDE = "text"


@dataclass
class Segment:
    """One resolved span: ``kind`` (KIND_OUTSIDE or a tag's mapped kind) + text."""

    kind: str
    text: str


class StreamingTagSplitter:
    """Cross-chunk tag splitter. Feed stream chunks; get resolved segments.

    ``tags`` maps a tag name (without angle brackets) to the kind emitted for its
    inner content — e.g. ``{"think": "thinking"}``. Tag names are matched
    case-insensitively. Only one tag may be open at a time (nested/overlapping
    tags are not supported — the models that embed ``<think>`` don't nest); a
    second opening while inside a tag is treated as inner content.
    """

    def __init__(self, tags: dict[str, str]) -> None:
        # Normalize tag names to lower-case for case-insensitive matching.
        self._tags = {name.lower(): kind for name, kind in tags.items()}
        self._buf = ""
        self._open: str | None = None  # currently-open tag name, or None

    def feed(self, chunk: str) -> list[Segment]:
        """Feed a stream chunk; return the segments now fully resolved.

        Text whose classification can't yet be decided (a partial ``<tag``
        prefix, or a partial ``</tag`` close) is retained internally and resolved
        on a later ``feed`` or at ``flush``. Adjacent segments of the same kind
        are coalesced so callers see one span per contiguous run.
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
        """Emit any held buffer at stream end as text (unterminated tag → visible)."""
        if not self._buf:
            self._open = None
            return []
        # Whatever is left — an unclosed tag's content or a dangling ``<`` — is
        # surfaced as ordinary text so nothing is silently dropped.
        text = self._buf
        self._buf = ""
        self._open = None
        return [Segment(KIND_OUTSIDE, text)]

    # ── internals ──

    def _step(self) -> tuple[Segment | None, bool]:
        """Resolve as much of the buffer as is currently decidable.

        Returns ``(segment_or_None, made_progress)``. ``made_progress`` is True
        when the buffer shrank (so the caller loops again); False when the
        remainder must be held for more input.
        """
        if self._open is None:
            return self._step_outside()
        return self._step_inside()

    def _step_outside(self) -> tuple[Segment | None, bool]:
        idx = self._buf.find("<")
        if idx == -1:
            # No tag start at all — emit everything as text.
            text, self._buf = self._buf, ""
            return (Segment(KIND_OUTSIDE, text) if text else None, False)
        # Text before the '<' is unambiguously outside; emit it first.
        if idx > 0:
            text = self._buf[:idx]
            self._buf = self._buf[idx:]
            return (Segment(KIND_OUTSIDE, text), True)
        # Buffer starts with '<'. Try to match a known opening tag.
        opened = self._match_open()
        if opened is not None:
            return (None, True)  # consumed the tag; loop to read its content
        # No full opening matched. Is the buffer a *prefix* of one (hold it)?
        if self._could_be_open_prefix():
            return (None, False)  # hold; need more input
        # A '<' that can't start any known tag — emit it as literal text and move
        # on (so '<' in normal prose isn't held forever).
        self._buf = self._buf[1:]
        return (Segment(KIND_OUTSIDE, "<"), True)

    def _step_inside(self) -> tuple[Segment | None, bool]:
        assert self._open is not None
        kind = self._tags[self._open]
        close = f"</{self._open}>"
        idx = _find_ci(self._buf, close)
        if idx == -1:
            # No full close yet. Emit content up to the largest point that can't
            # be a partial close, holding the possible-close tail.
            hold = _max_suffix_overlap(self._buf, close)
            if hold == len(self._buf):
                return (None, False)  # whole buffer might be a partial close
            emit = self._buf[: len(self._buf) - hold]
            self._buf = self._buf[len(self._buf) - hold :]
            return (Segment(kind, emit) if emit else None, bool(emit) and hold == 0)
        # Found the close. Emit inner content, drop the close tag, go outside.
        inner = self._buf[:idx]
        self._buf = self._buf[idx + len(close) :]
        self._open = None
        return (Segment(kind, inner) if inner else None, True)

    def _match_open(self) -> str | None:
        """If the buffer begins with a known ``<tag>``, consume it and open."""
        for name in self._tags:
            opener = f"<{name}>"
            if _startswith_ci(self._buf, opener):
                self._buf = self._buf[len(opener) :]
                self._open = name
                return name
        return None

    def _could_be_open_prefix(self) -> bool:
        """True if the buffer is a proper prefix of some ``<tag>`` opener."""
        low = self._buf.lower()
        for name in self._tags:
            opener = f"<{name}>"
            if len(low) < len(opener) and opener.startswith(low):
                return True
        return False


def make_think_splitter() -> "StreamingTagSplitter":
    """A splitter configured for inline ``<think>`` reasoning (the common case).

    Self-gating: a stream that never contains ``<think>`` passes through entirely
    as ``KIND_OUTSIDE`` text, so it's safe to run unconditionally on every
    OpenAI-compatible / Ollama stream regardless of model.
    """
    return StreamingTagSplitter({"think": "thinking"})


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


def _max_suffix_overlap(s: str, target: str) -> int:
    """Length of the longest suffix of ``s`` that is a prefix of ``target``.

    Used to decide how much of the buffer to hold back as a possible partial
    closing tag. E.g. for target ``</think>`` and buffer ``…ans</thi`` the tail
    ``</thi`` overlaps, so 5 chars are held until the next chunk resolves them.
    """
    low_s = s.lower()
    low_t = target.lower()
    max_len = min(len(low_s), len(low_t) - 1) if len(low_t) > 1 else 0
    for k in range(max_len, 0, -1):
        if low_s.endswith(low_t[:k]):
            return k
    return 0
