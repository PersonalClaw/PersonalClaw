"""A private key block, found whole so it can be masked as one.

``security.redact_credentials`` masks every block this finds before its other passes run, so none
of them takes part of a key and leaves the rest; the inverses (``security._mask_pairs``) read a
masked block back by the same pattern, since its span holds line breaks, and the literal after it
is often one.
"""

import re

#: A line break between a block's lines: LF, CRLF or a lone CR, or one a JSON string escapes, at
#: any depth of escaping (a key file inside a tool's JSON input is escaped twice).
_LINE_BREAK = r"(?:\r\n?|\n|\\++r(?:\\++n)?+|\\++n)"
#: What may begin a line after its break: its indent, an escaped tab, and the mark a quote (``>``),
#: a ``#`` comment or a diff (``-``) puts there. A diff's ``-`` is never the first dash of an END
#: line, and a diff's ``+`` is a base64 character, so a body line takes it.
_MARKS = r"(?:[ \t#>]|\\++t|-(?!----E))*+"
#: Where two string literals in code join: a quote, space or line breaks, a ``+`` or none, a quote.
_JOINT = r"[\"']\s*+(?:\+\s*+)?+[\"']"
#: One break between a block's lines: a line break and what begins the next line, a space or tab (a
#: key pasted onto one line), or a joint between string literals.
_BREAK = rf"(?:{_LINE_BREAK}{_MARKS}|[ \t]|{_JOINT})"
#: A body character: base64, or a ``/`` a JSON writer escapes.
_BODY = r"(?:[A-Za-z0-9+/=]|\\++/)"
#: A body line: 16 or more body characters, so a shorter word is never read as one.
_LINE = rf"{_BODY}{{16,}}+"
#: A last line base64 pads with ``=``, of any length.
_PAD = r"(?:[A-Za-z0-9+/]|\\++/){0,14}+={1,2}+"
#: An OpenPGP key's checksum line, between its body and its END line.
_CRC = rf"{_BREAK}++=[A-Za-z0-9+/]{{4}}"
#: A header before the body: a legacy key's ``Proc-Type`` and ``DEK-Info``, or an OpenPGP key's
#: ``Version`` and ``Comment``. On a line of its own its value runs to the end of the line. On a key
#: run onto one line it runs across spaces up to the next header's name, a run of 16 body characters
#: (the body) or five dashes (the END line), so the text after the block stays.
_NAME = r"[A-Z][A-Za-z-]*+"
_VALUE = rf"[^\s\\]++(?:[ \t]++(?!{_NAME}: |{_BODY}{{16}}|-----)[^\s\\]++)*+"
_HEADER = (
    rf"(?:(?:[ \t]*+(?:{_LINE_BREAK}{_MARKS}|{_JOINT}))++{_NAME}: [^\r\n\\]*+"
    rf"|[ \t]++{_NAME}: {_VALUE})"
)

#: A whole private key block, header to END line, whatever its label: RSA, DSA, EC and OPENSSH,
#: PKCS#8's bare ``PRIVATE KEY`` and ``ENCRYPTED PRIVATE KEY``, and OpenPGP's ``PGP PRIVATE KEY
#: BLOCK``. The header words alone were all the mask took, which left the key itself, its base64
#: body, in the text; and a decoded body holds no pattern, so the base64 pass could not take it
#: either.
#:
#: Its lines break as :data:`_BREAK` reads them. Headers count only where a body line follows them,
#: so a header line with no key after it, a key cut right after it, keeps the lines that follow.
#: The body runs on past a padded line when another body line follows, as a diff of a replaced key
#: shows the old body and then the new one. With no END line, a key cut short, the block runs
#: through its last body line, or its last line padded with ``=``; a shorter line is taken only
#: before the END line. Every repeat after the header line is possessive, and no part can take
#: what begins the next, so the scan stays linear in the length of the text and its memory does
#: not grow with it.
BLOCK_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )?PRIVATE KEY(?: BLOCK)?)-----"
    rf"(?:{_HEADER}*+{_BREAK}*+{_LINE}(?:{_BREAK}++(?:{_LINE}|{_PAD}(?={_BREAK}++{_LINE})))*+)?+"
    rf"(?:{_BREAK}++(?:{_BODY}{{1,15}}+(?=(?:{_CRC})?+{_BREAK}*+\+?+-----END )|{_PAD}))?+"
    rf"(?:{_CRC}(?={_BREAK}*+\+?+-----END ))?+"
    rf"(?:{_BREAK}*+\+?+-----END (?P=label)-----)?+"
)

#: What stands for a whole block. A kind of its own, so the inverses know which masks to read back
#: by :data:`BLOCK_RE`.
TAG = "[REDACTED: private key]"

#: A key's header words where no whole block is left to mask: a key named in text, or one a base64
#: chunk holds once it is decoded. ``URL_HEADER`` is the same in a URL's query, its spaces as they
#: are, ``+`` or ``%20``.
HEADER_WORDS = r"BEGIN[\s](?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP)[\s])?PRIVATE[\s]KEY"
_URL_SPACE = r"(?:[\s+%]|%20)"
URL_HEADER = (
    rf"BEGIN{_URL_SPACE}(?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP){_URL_SPACE})?"
    rf"PRIVATE{_URL_SPACE}KEY"
)


def redact(text: str) -> tuple[str, list[str]]:
    """*text* with each whole private key block replaced by :data:`TAG`, and a warning for each."""
    warnings: list[str] = []

    def _tag(m: "re.Match[str]") -> str:
        warnings.append(f"Redacted a private key block ({m.group('label')})")
        return TAG

    return BLOCK_RE.sub(_tag, text), warnings


def masked_span_end(mask: str, stored: str, pos: int, following: str) -> int:
    """Where the span that *mask*, at *pos* of a masked text, stands for ends in *stored*: up to
    where the masked text's next literal, *following*, resumes, or the end of *stored* when there is
    none (``security._mask_pairs``). A block spans lines, so that literal (often one line break) can
    occur inside it: a :data:`TAG` is read by :data:`BLOCK_RE` where a block starts at *pos* and
    *following* resumes right after it. ``-1`` when *following* is nowhere after *pos*."""
    block = BLOCK_RE.match(stored, pos) if mask == TAG else None
    if block is not None and stored.startswith(following, block.end()):
        return block.end()
    return stored.find(following, pos) if following else len(stored)
