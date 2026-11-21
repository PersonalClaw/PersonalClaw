"""The passage a search shows of why a text matched: the words around the match, marked.

A direct read of a chat (``history.ConversationLog.search_sessions``), and the Library's direct
reads of its items, cut this passage from the text as it was written, the way the search index
marks its own hits.
"""

from __future__ import annotations

import re

#: How much of a message a direct read's snippet shows around the match, before and after it —
#: short before, so the match is on screen in the chat list's one truncated line.
_SNIPPET_BEFORE = 48
_SNIPPET_AFTER = 96


def match_snippet(texts: list[str], needle: str) -> str:
    """The passage around the first place *needle* (already casefolded) is said in *texts*, with
    the match marked ``<<…>>`` as the search index marks its own; ``""`` when no text says it.

    What a chat a direct read found shows of why it matched, the way an index hit does. Matched by
    full case folding, as the read counts matches, so the passage is cut from the text as it was
    written: where folding keeps a text's length every character folds to one and the offsets
    agree; otherwise each folded character is mapped back to the one it came from.
    """
    for text in texts:
        folded = text.casefold()
        at = folded.find(needle)
        if at < 0:
            continue
        if len(folded) == len(text):
            start, end = at, at + len(needle)
        else:
            origin = [i for i, ch in enumerate(text) for _ in ch.casefold()]
            start, end = origin[at], origin[at + len(needle) - 1] + 1
        lead = re.sub(r"\s+", " ", text[max(0, start - _SNIPPET_BEFORE) : start])
        tail = re.sub(r"\s+", " ", text[end : end + _SNIPPET_AFTER])
        # A word the cut went through is dropped rather than shown in part.
        cut_lead, cut_tail = start > _SNIPPET_BEFORE, end + _SNIPPET_AFTER < len(text)
        if cut_lead and " " in lead:
            lead = lead.split(" ", 1)[1]
        if cut_tail and " " in tail:
            tail = tail.rsplit(" ", 1)[0]
        before, after = ("…" if cut_lead else ""), ("…" if cut_tail else "")
        return f"{before}{lead}<<{text[start:end]}>>{tail}{after}"
    return ""
