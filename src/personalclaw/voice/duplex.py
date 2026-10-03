"""Pure decision functions for the hands-free (duplex) voice loop.

Four rules, no I/O, no model calls — every one of them is a
string decision the STT/TTS endpoints and the frontend mic hook need to make
(the one read is the shipped phrase table, once, at import):

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
buffer). Matching is logic, so it exists in both languages; its behaviour is
pinned once in ``web/src/ui/composer/duplexPhraseCases.json``, which both
languages' tests run. The shipped phrase lists are data, read by both sides from
``phrases.json`` beside this module. The echo filter and the speech cleaner are
backend-only and have no mirror.
"""

import json
import re
from pathlib import Path

# The confirmation must land near the end of the dictated chunk. Wide enough for
# "go ahead please" or "ok do it then", narrow enough that a confirmation phrase
# buried at the start of a long dictation does not fire a turn.
TAIL_WINDOW_WORDS = 6

# Consecutive-word run that marks a transcript as the assistant's own speech.
# Three is the smallest run that is not routinely produced by two people
# discussing the same subject, so it filters speaker bleed without eating a
# genuine short reply.
ECHO_MIN_RUN = 3

# The shipped phrase lists, the defaults of ``voice.confirmation_phrases`` /
# ``voice.exit_phrases``. The browser falls back to the same table when it cannot read
# the config, so it is data rather than a literal here. A table that is missing or has
# lost a list fails the import: an empty exit list would leave hands-free voice with no
# way to discard a dictated turn.
_PHRASE_TABLE = Path(__file__).with_name("phrases.json")


def _shipped_phrases(table: object, key: str) -> tuple[str, ...]:
    phrases = table.get(key) if isinstance(table, dict) else None
    if not (
        isinstance(phrases, list)
        and phrases
        and all(isinstance(p, str) and p.strip() for p in phrases)
    ):
        raise RuntimeError(f"{_PHRASE_TABLE} has no {key} phrases")
    return tuple(phrases)


_SHIPPED_TABLE = json.loads(_PHRASE_TABLE.read_text(encoding="utf-8"))
DEFAULT_CONFIRMATION_PHRASES: tuple[str, ...] = _shipped_phrases(_SHIPPED_TABLE, "confirmation")
DEFAULT_EXIT_PHRASES: tuple[str, ...] = _shipped_phrases(_SHIPPED_TABLE, "exit")

# Appended to a dictated turn so the model self-corrects on garbled
# homophones instead of confidently misreading them. One line, no hedging.
VOICE_DISCLAIMER = "(Transcribed from voice; transcription may be inaccurate.)"

# The desktop shell's default push-to-talk chord. An Electron
# accelerator string; the shell parses and binds it, and refuses a chord without a
# modifier because a bare global key is taken from every other app on the machine.
# Kept here beside the other voice defaults so the config dataclass, the docs and
# the frontend all read one value.
DEFAULT_PUSH_TO_TALK_CHORD = "CommandOrControl+Shift+Space"

# A word is a run of letters, digits and the apostrophes inside a contraction; it is
# compared lowercased with its apostrophes removed, so "don't", "don’t" and "dont" are
# one word. Spaces, hyphens and punctuation only separate words.
_WORD_RE = re.compile(r"[A-Za-z0-9'\u2018\u2019\u02bc]+")
_APOSTROPHE_RE = re.compile(r"['\u2018\u2019\u02bc]")

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
    """Word tokens, lowercased with apostrophes removed; punctuation and markup discarded."""

    folded = (_APOSTROPHE_RE.sub("", m.group(0)).lower() for m in _WORD_RE.finditer(text))
    return [w for w in folded if w]


def _spells_phrase_in_tail(
    tokens: list[str], phrase: list[str], tail_words: int, *, split_words: bool
) -> bool:
    """True when a run of whole ``tokens`` inside the trailing window spells ``phrase``.

    A run spells a phrase when the two are equal with the spaces between their words
    taken out: speech-to-text writes "never mind" as "Nevermind", "never-mind" or
    "Never mind.", and each of them is the phrase. Only whole words count, so "remind"
    or "whenever" never supplies part of one. With ``split_words`` a single word of the
    phrase may also arrive as two ("never mind" for a "nevermind" phrase); without it a
    run may only join the phrase's own words together.
    """

    key = "".join(phrase)
    if not key:
        return False
    # Where the phrase itself has a boundary between two of its words, as offsets into key.
    bounds: set[int] = set()
    offset = 0
    for part in phrase[:-1]:
        offset += len(part)
        bounds.add(offset)
    for i in range(len(tokens)):
        run = ""
        for j in range(i, len(tokens)):
            run += tokens[j]
            if not key.startswith(run):
                break
            if len(run) == len(key):
                # The window must stretch to hold a run longer than tail_words itself.
                if i >= len(tokens) - max(tail_words, j - i + 1):
                    return True
                break
            if not split_words and len(run) not in bounds:
                break
    return False


def _phrase_in_tail(text: str, phrases: object, tail_words: int, *, split_words: bool) -> bool:
    """True when any phrase is spelled by a run of words inside the trailing window."""

    if not isinstance(phrases, (list, tuple, set, frozenset)):
        return False
    tokens = _words(text)
    if not tokens:
        return False
    return any(
        isinstance(phrase, str)
        and _spells_phrase_in_tail(tokens, _words(phrase), tail_words, split_words=split_words)
        for phrase in phrases
    )


def is_confirmation(
    text: str,
    phrases: object = DEFAULT_CONFIRMATION_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should fire the buffered turn.

    Matching ignores case, punctuation, hyphens, apostrophes and the spaces
    between the phrase's words, and is anchored to the trailing ``tail_words``
    words, so "go ahead and tell me what you think about the plan" does not
    execute — only a trailing "go ahead" does.

    A confirmation is matched more strictly than an exit: its words may run
    together ("Sendit.") but a word of the phrase is never assembled from two
    words that were said ("Goa head" is not "go ahead"). A false confirmation
    sends a half-finished thought; a false exit only discards one.
    """

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words, split_words=False)


def is_exit(
    text: str,
    phrases: object = DEFAULT_EXIT_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should clear the buffer."""

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words, split_words=True)


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
