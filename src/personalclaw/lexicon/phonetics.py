"""Double Metaphone — a compact, vendored, pure-Python implementation (core LEX).

Deterministic phonetic keying (the locked decision) with NO third-party dependency —
heavy phonetics libs stay out of core. ``double_metaphone(word) -> (primary, secondary)``
returns up to two 4-char phonetic keys; the secondary is "" when there's no alternate
pronunciation. Two words "sound alike" when any of their keys match.

This is the standard Lawrence Philips Double Metaphone algorithm, trimmed to the subset
that matters for matching English + common tech/proper-noun vocabulary. It is not a full
i18n phonetics engine; it is a fast, dependency-free same-sound key good enough to drive
the Lexicon's post-decode correction (which is further gated by edit-distance + a
confidence filter, so an occasional coarse key is harmless).
"""

from __future__ import annotations

_VOWELS = frozenset("AEIOUY")


def _is_vowel(s: str, pos: int) -> bool:
    return 0 <= pos < len(s) and s[pos] in _VOWELS


def double_metaphone(word: str) -> tuple[str, str]:
    """Return (primary, secondary) phonetic keys (≤4 chars each) for *word*."""
    if not word:
        return "", ""
    s = "".join(ch for ch in word.upper() if ch.isalpha())
    if not s:
        return "", ""

    primary: list[str] = []
    secondary: list[str] = []
    length = len(s)
    pos = 0

    def add(p: str, sec: str | None = None) -> None:
        primary.append(p)
        secondary.append(p if sec is None else sec)

    def at(i: int) -> str:
        return s[i] if 0 <= i < length else ""

    def sub(i: int, j: int) -> str:
        return s[max(i, 0) : j]

    # Skip silent leading pairs.
    if sub(0, 2) in ("GN", "KN", "PN", "WR", "PS"):
        pos = 1
    if at(0) == "X":  # initial X → S
        add("S")
        pos = 1
    if sub(0, 5) == "MERCU":  # e.g. Mercury (help common vocab)
        pass  # no special-case; fall through

    while pos < length and (len("".join(primary)) < 4 or len("".join(secondary)) < 4):
        ch = at(pos)
        if ch in _VOWELS:
            if pos == 0:
                add("A")
            pos += 1
            continue
        if ch == "B":
            add("P")
            pos += 2 if at(pos + 1) == "B" else 1
        elif ch == "Ç" or ch == "C":
            if at(pos + 1) == "H":
                if sub(pos, pos + 4) == "CHAE" or sub(pos, pos + 2) == "CH":
                    (
                        add("K", "X")
                        if pos == 0 and sub(pos + 1, pos + 4) in ("HAR", "HOR")
                        else add("X", "K")
                    )
                else:
                    add("X", "K")
                pos += 2
            elif at(pos + 1) == "C" and not (pos == 1 and at(0) == "M"):
                if at(pos + 2) in "IEH" and sub(pos + 2, pos + 4) != "HU":
                    add("KS") if sub(pos + 1, pos + 3) in ("CI", "CE") else add("X")
                    pos += 3
                else:
                    add("K")
                    pos += 2
            elif at(pos + 1) in ("I", "E", "Y"):
                add("S")
                pos += 2
            else:
                add("K")
                pos += 1
        elif ch == "D":
            if at(pos + 1) == "G" and at(pos + 2) in "IEY":
                add("J")
                pos += 3
            else:
                add("T")
                pos += 2 if at(pos + 1) == "D" else 1
        elif ch == "F":
            add("F")
            pos += 2 if at(pos + 1) == "F" else 1
        elif ch == "G":
            if at(pos + 1) == "H":
                if pos > 0 and not _is_vowel(s, pos - 1):
                    add("K")
                    pos += 2
                elif pos == 0:
                    add("K" if at(pos + 2) != "I" else "J")
                    pos += 2
                else:
                    pos += 2  # silent gh
            elif at(pos + 1) == "N":
                add("KN", "N")
                pos += 2
            elif at(pos + 1) in ("I", "E", "Y"):
                add("J")
                pos += 2
            else:
                add("K")
                pos += 2 if at(pos + 1) == "G" else 1
        elif ch == "H":
            if (pos == 0 or _is_vowel(s, pos - 1)) and _is_vowel(s, pos + 1):
                add("H")
                pos += 1
            else:
                pos += 1  # silent
        elif ch == "J":
            add("J", "H" if pos == 0 else "J")
            pos += 2 if at(pos + 1) == "J" else 1
        elif ch == "K":
            add("K")
            pos += 2 if at(pos + 1) == "K" else 1
        elif ch == "L":
            add("L")
            pos += 2 if at(pos + 1) == "L" else 1
        elif ch == "M":
            add("M")
            pos += 2 if at(pos + 1) == "M" else 1
        elif ch == "N":
            add("N")
            pos += 2 if at(pos + 1) == "N" else 1
        elif ch == "P":
            if at(pos + 1) == "H":
                add("F")
                pos += 2
            else:
                add("P")
                pos += 2 if at(pos + 1) == "P" else 1
        elif ch == "Q":
            add("K")
            pos += 2 if at(pos + 1) == "Q" else 1
        elif ch == "R":
            add("R")
            pos += 2 if at(pos + 1) == "R" else 1
        elif ch == "S":
            if at(pos + 1) == "H":
                add("X")
                pos += 2
            elif at(pos + 1) in ("I", "Y") and at(pos + 2) == "O":
                add("S", "X")
                pos += 1
            else:
                add("S")
                pos += 2 if at(pos + 1) == "S" else 1
        elif ch == "T":
            if sub(pos, pos + 2) == "TH":
                add("0", "T")
                pos += 2
            elif at(pos + 1) in ("I", "Y") and at(pos + 2) == "O":
                add("X")
                pos += 1
            else:
                add("T")
                pos += 2 if at(pos + 1) == "T" else 1
        elif ch == "V":
            add("F")
            pos += 2 if at(pos + 1) == "V" else 1
        elif ch == "W":
            if at(pos + 1) == "H":
                add("A")
                pos += 2
            elif _is_vowel(s, pos + 1):
                add("A", "F")
                pos += 1
            else:
                pos += 1
        elif ch == "X":
            add("KS")
            pos += 2 if at(pos + 1) in ("C", "X") else 1
        elif ch == "Z":
            add("S")
            pos += 2 if at(pos + 1) == "Z" else 1
        else:
            pos += 1

    p = "".join(primary)[:4]
    sec = "".join(secondary)[:4]
    return p, (sec if sec != p else "")


def phonetic_keys(word: str) -> list[str]:
    """Return the distinct non-empty Double Metaphone keys for *word* (0, 1, or 2)."""
    p, s = double_metaphone(word)
    keys = [k for k in (p, s) if k]
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def sounds_like(a: str, b: str) -> bool:
    """True if *a* and *b* share any phonetic key (a cheap same-sound test)."""
    ka, kb = set(phonetic_keys(a)), set(phonetic_keys(b))
    return bool(ka and kb and ka & kb)
