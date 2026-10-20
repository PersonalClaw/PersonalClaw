"""What an agent's own instructions ask of every answer, checked once the answer is written.

An agent's instructions reach its model whole and first (``ContextBuilder.build_message``), and a
model can still write past them: an agent told "Keep the answer under 200 words unless I ask for
more" answered in 242. A rule a program can check is checked after the answer, and the chat says
so under it. The answer itself is never cut: a cut answer can end mid-sentence or lose its
conclusion, and nothing would show that it had been cut. A word limit is the one rule checked so.

A limit is read the way an Inbox draft reads one (:func:`personalclaw.reply_grounding.
word_limit_in`), from a sentence of the instructions that is about the answer, one that names the
answer, the reply or the response, so a limit set for something else (a title, a commit message)
is not taken for the answer's, and a limit for each of its parts is not taken for its whole.
Instructions that set different limits for the answer set none a note could state. A limit she
gives in her own message is hers for that answer, and the agent's standing one is not checked
against it. Her exception ("unless I ask for more") cannot be judged by a program, so the note
quotes the agent's sentence, and its words stay true when she did ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from personalclaw.reply_grounding import word_count, word_limit_in

#: Where one sentence of a run of text ends.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
#: A list item's marker, which is not part of what the sentence says.
_LIST_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
#: The answer itself, named.
_ABOUT_THE_ANSWER = re.compile(
    r"\b(?:answers?|repl(?:y|ies)|respon(?:d|ds|se|ses))\b", re.IGNORECASE
)
#: A limit for each part of the answer ("three bullets of under 20 words each"), not for its whole.
_PER_PART = re.compile(r"\b(?:each|per)\b", re.IGNORECASE)


@dataclass(frozen=True)
class WordLimit:
    """The most words an agent's instructions allow an answer, and the sentence that says so."""

    words: int
    sentence: str


def _sentences(instructions: str) -> list[str]:
    """The sentences of *instructions*, a wrapped line joined to the line it continues: a blank
    line, a heading and a list item each start a new run of text."""
    runs: list[list[str]] = []
    starts_run = True
    for line in (instructions or "").splitlines():
        text = line.strip()
        if not text:
            starts_run = True
            continue
        if starts_run or text.startswith("#") or _LIST_MARKER.match(text):
            runs.append([])
        runs[-1].append(_LIST_MARKER.sub("", text))
        starts_run = text.startswith("#")
    return [s for run in runs for s in _SENTENCE_END.split(" ".join(run)) if s]


def declared_word_limit(instructions: str) -> WordLimit | None:
    """The one word limit *instructions* set for the answer, or ``None``."""
    found: dict[int, str] = {}
    for sentence in _sentences(instructions):
        if not _ABOUT_THE_ANSWER.search(sentence) or _PER_PART.search(sentence):
            continue
        limit = word_limit_in(sentence)
        if limit:
            found.setdefault(limit, sentence)
    if len(found) != 1:
        return None
    ((words, sentence),) = found.items()
    return WordLimit(words, sentence)


def over_limit_notice(agent: str, instructions: str, answer: str, request: str) -> str:
    """What the chat says under *answer* when it runs past the word limit the *agent*'s own
    *instructions* set, or ``""``: within it, no limit set, or a limit of her own in *request*.
    *agent* is the agent's name as she picks it ("" names it "The agent")."""
    limit = declared_word_limit(instructions)
    if limit is None or word_limit_in(request) is not None:
        return ""
    words = word_count(answer)
    if words <= limit.words:
        return ""
    who = agent.strip() or "The agent"
    return f"This answer is {words:,} words. {who}'s instructions say: “{limit.sentence}”"
