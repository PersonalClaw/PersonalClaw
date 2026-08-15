"""Pure decision functions for the hands-free (duplex) voice loop.

MULTIMODAL-IO §4. Four rules, no I/O, no model calls — every one of them is a
string decision the STT/TTS endpoints and the frontend mic hook need to make:

* :func:`is_confirmation` / :func:`is_exit` — hands-free gating. A dictated
  transcript accumulates in the frontend and only becomes a turn once the
  operator says a confirmation phrase; an exit phrase clears the buffer. A
  half-finished thought must never become an executed instruction.
* :func:`is_echo` — the assistant's own spoken reply bleeding back through the
  microphone. Any transcript sharing a run of ``ECHO_MIN_RUN`` consecutive
  words with the last synthesized text is the speaker, not the operator.
* :func:`clean_for_speech` — pre-synthesis text cleaning. The chat transcript
  keeps the full text; only the *audio* drops code, URLs, paths, and flags,
  which are noise when read aloud.

Both phrase matchers are **tail-anchored** (:data:`TAIL_WINDOW_WORDS`): the
confirmation is the last thing the operator says, so scanning the whole buffer
would let a "go ahead" uttered mid-thought fire the turn early.

``web/src/ui/composer/duplex.ts`` mirrors the two phrase matchers for the
frontend accumulation buffer (the frontend owns the mic, so it owns the
buffer). Keep the rules in the two files in step; the echo filter and the
speech cleaner are backend-only and have no mirror.
"""

import re

# The confirmation must land near the end of the dictated chunk. Wide enough for
# "go ahead please" or "ok do it then", narrow enough that a confirmation phrase
# buried at the start of a long dictation does not fire a turn.
TAIL_WINDOW_WORDS = 6

# Consecutive-word run that marks a transcript as the assistant's own speech.
# Three is the smallest run that is not routinely produced by two people
# discussing the same subject, so it filters speaker bleed without eating a
# genuine short reply.
ECHO_MIN_RUN = 3

DEFAULT_CONFIRMATION_PHRASES: tuple[str, ...] = (
    "do it",
    "go ahead",
    "send it",
    "execute",
)

DEFAULT_EXIT_PHRASES: tuple[str, ...] = (
    "cancel",
    "never mind",
    "forget it",
)

# Appended to a dictated turn (§4.4) so the model self-corrects on garbled
# homophones instead of confidently misreading them. One line, no hedging.
VOICE_DISCLAIMER = "(Transcribed from voice; transcription may be inaccurate.)"

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"(?:```|~~~).*\Z", re.DOTALL)
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`+([^`]*)`+")
_URL_RE = re.compile(r"\b(?:https?|ftp)://([^\s/?#]+)\S*|\bwww\.([^\s/?#]+)\S*", re.IGNORECASE)
_FLAG_RE = re.compile(r"(?<!\S)--?[A-Za-z][\w-]*(?:=\S*)?")
_PATH_RE = re.compile(r"(?<!\S)~?[\w.@+-]*(?:/[\w.@+-]+)+/?")
_EMPHASIS_RE = re.compile(r"(\*\*|__|~~|\*|_)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>+\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_RULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_REPEATED_PUNCT_RE = re.compile(r"([,.;:!?])(?:\s*\1)+")

# Spoken stand-in for a fenced code block. A code-heavy answer must still
# produce audio — going silent reads as a broken TTS runtime.
_CODE_BLOCK_SPOKEN = " code block. "


def _words(text: str) -> list[str]:
    """Lowercase word tokens, punctuation and markup discarded."""

    return _WORD_RE.findall(text.lower())


def _phrase_in_tail(text: str, phrases: object, tail_words: int) -> bool:
    """True when any phrase appears as a word run inside the trailing window."""

    if not isinstance(phrases, (list, tuple, set, frozenset)):
        return False
    tokens = _words(text)
    if not tokens:
        return False
    for phrase in phrases:
        if not isinstance(phrase, str):
            continue
        needle = _words(phrase)
        if not needle:
            continue
        # The window must be able to hold the phrase itself, otherwise a
        # multi-word phrase could never match a tail shorter than the phrase.
        window = tokens[-max(tail_words, len(needle)) :]
        for start in range(len(window) - len(needle) + 1):
            if window[start : start + len(needle)] == needle:
                return True
    return False


def is_confirmation(
    text: str,
    phrases: object = DEFAULT_CONFIRMATION_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should fire the buffered turn.

    Matching is case- and punctuation-insensitive and anchored to the trailing
    ``tail_words`` words, so "go ahead and tell me what you think about the
    plan" does not execute — only a trailing "go ahead" does.
    """

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words)


def is_exit(
    text: str,
    phrases: object = DEFAULT_EXIT_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should clear the buffer."""

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words)


def is_echo(transcript: str, last_tts_text: str, *, min_run: int = ECHO_MIN_RUN) -> bool:
    """True when ``transcript`` is the assistant's own speech coming back.

    Compares word ``min_run``-grams. Sharing a single run of that many
    consecutive words in either direction is enough — n-gram overlap is
    symmetric, so the check covers a transcript that is a fragment of the
    spoken text and one that contains it.

    A transcript shorter than ``min_run`` words can never match, which is the
    intended conservative behavior: short utterances ("yes", "stop") stay live.
    """

    if not isinstance(transcript, str) or not isinstance(last_tts_text, str):
        return False
    if min_run < 1:
        return False
    heard = _words(transcript)
    spoken = _words(last_tts_text)
    if len(heard) < min_run or len(spoken) < min_run:
        return False
    spoken_runs = {tuple(spoken[i : i + min_run]) for i in range(len(spoken) - min_run + 1)}
    return any(
        tuple(heard[i : i + min_run]) in spoken_runs for i in range(len(heard) - min_run + 1)
    )


def _domain(match: re.Match[str]) -> str:
    host = match.group(1) or match.group(2) or ""
    host = host.split("@")[-1].split(":")[0]
    if host.lower().startswith("www."):
        host = host[4:]
    return host


def _filename(match: re.Match[str]) -> str:
    token = match.group(0).rstrip("/")
    tail = token.rsplit("/", 1)[-1]
    return tail or token


def clean_for_speech(text: str) -> str:
    """Reduce ``text`` to something worth hearing.

    Applied on the synthesis path only, **after** redaction: fenced code
    becomes a spoken marker, markdown decoration and backticks are dropped,
    URLs collapse to their domain, file paths to their filename, and CLI flags
    disappear. The chat transcript keeps the original text.

    Returns ``""`` only for input that is entirely unspeakable; callers decide
    what to do with that rather than having a policy imposed here.
    """

    if not isinstance(text, str) or not text.strip():
        return ""

    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = _FENCE_RE.sub(_CODE_BLOCK_SPOKEN, out)
    # An unterminated fence (streamed or truncated text) still hides code.
    out = _OPEN_FENCE_RE.sub(_CODE_BLOCK_SPOKEN, out)
    out = _MD_LINK_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    out = _URL_RE.sub(_domain, out)
    out = _FLAG_RE.sub(" ", out)
    out = _PATH_RE.sub(_filename, out)
    out = _RULE_RE.sub(" ", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _BULLET_RE.sub("", out)
    out = _EMPHASIS_RE.sub(r"\2", out)
    out = out.replace("|", " ")
    out = re.sub(r"\s+", " ", out)
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _REPEATED_PUNCT_RE.sub(r"\1", out)
    return out.strip()
