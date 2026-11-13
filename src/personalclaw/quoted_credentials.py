"""A credential's value written in quotes, found by its name.

``security.redact_credentials`` masks a ``name = value`` credential by its name, and the value it
reads after one of these names ends at a quote, so a value in quotes, as JSON, YAML, an env file or
code writes one, was never masked. :func:`redact` masks what the quotes hold and keeps the name and
the quotes, so a JSON document still parses and the reader still sees which field it was.
"""

import re

#: The generic names a credential's value is masked by, whatever its shape, so the value's format
#: does not have to be known: an unknown provider's key is what a pattern for a shape misses.
#: ``security`` reads a value written without quotes after the same names.
NAMES = (
    r"api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r"|password|passwd|private[_-]?key"
)

#: AWS's names for a secret key and a session token, read in any case, here and by ``security``:
#: the AWS CLI's JSON writes ``SecretAccessKey``, a credentials file ``aws_secret_access_key``, an
#: env file, ``printenv`` and the CLI's env export ``AWS_SECRET_ACCESS_KEY``, and the SDKs still
#: read the session token's older name, ``AWS_SECURITY_TOKEN``.
AWS_NAMES = (
    r"SecretAccessKey|aws_secret_access_key|SessionToken|aws_session_token|aws_security_token"
)

#: After an AWS name, a value that starts with ``$``, after any escapes and an opening quote or
#: backtick, names where a secret is kept, and both passes leave it whole: a CI workflow's
#: ``${{ secrets.X }}``, a shell's ``$(...)``, ``${VAR}`` and ``$VAR``, a default that is empty or
#: another reference (``${VAR:-}``) and a substring (``${VAR:0:4}``). A ``$`` that opens a literal
#: is read as a value: ANSI-C and locale quoting (``$'...'``, ``$"..."``), a literal default
#: (``${VAR:-word}``, ``${VAR-word}``, ``${VAR:=word}``) and a placeholder's (``${KEY:word}``). A
#: password can start with ``$``, so the generic names don't take the guard.
NOT_A_REFERENCE = (
    r"(?!\\*+[\"'`]?\$(?![\"'])(?!\{\w+:?[-=+](?![}$]))" r"(?!\{[\w.-]+:(?![-=+?}$\s(]|\d+[:}])))"
)


def first_letters(names: str) -> str:
    """The letters *names*' alternatives start with, lower case, for a lookahead that lets the
    regex engine pass over a position no name starts at. Every alternative must open with a letter
    and hold no group: a set built from one that doesn't could miss a name, with no error."""
    alternatives = names.split("|")
    if not all(a[:1].isalpha() and "(" not in a for a in alternatives):
        raise ValueError(f"a name alternative opens with no letter or holds a group: {names}")
    return "".join(sorted({a[0].lower() for a in alternatives}))


_FIRSTS = first_letters(f"{AWS_NAMES}|{NAMES}")

#: How a name sets its value, in the generic and the AWS name patterns: ``:`` or ``=``, Go's and
#: Make's ``:=``, and Make's ``::=``, ``:::=``, ``?=`` and ``+=``. The passes for a value without
#: quotes never read an ``=`` that opens ``=>`` before whitespace, an escaped quote or the end of
#: the text, so the quoted pass reads the value after a PHP, Perl or Ruby map's ``=>`` under every
#: name, and a bare ``name => expression``, as often a JavaScript arrow function as a map, stays
#: whole. Each is atomic, so a ``:=`` is never read back as a ``:`` with a value that starts with
#: ``=``.
ASSIGN = r"(?>:{1,3}=|[?+]=|:|=(?!>(?:\s|\\+[\"']|\Z)))"
QUOTED_ASSIGN = r"(?>:{1,3}=|[?+]=|=>|[:=])"

#: The pass for a value without quotes (``security``) reads these AWS names in any case, and the
#: access key id's, which only it reads.
AWS_KEY_NAMES = rf"{AWS_NAMES}|AccessKeyId|aws_access_key_id"
_KEY_FIRSTS = first_letters(AWS_KEY_NAMES)
#: Between an AWS name's ``:`` or ``=`` and its value: blanks, or a line break when the next line
#: isn't a key of its own. An empty value (``AWS_SESSION_TOKEN:`` in an env map, an empty
#: ``AWS_SESSION_TOKEN=`` line) is never read as the next line's key, which would leave the
#: credential after that key unread.
_AWS_GAP = (
    r"(?:[^\S\r\n]*+(?=\S)|[^\S\r\n]*+[\r\n]\s*+(?!\\*+[\"']?(?:[\w.-]+\\*+[\"']?[^\S\r\n]*+"
    r"(?:=>|[:=])(?=\s|\Z)|[A-Z_][A-Z0-9_]*=|[\w.-]*\[REDACTED:)))"
)
#: In that pass, an AWS name not inside ``${``, how it sets its value and the gap, up to a value
#: that is no reference (:data:`NOT_A_REFERENCE`).
AWS_NAMED = (
    rf"(?<!\$\{{)(?=(?i:[{_KEY_FIRSTS}]))(?i:{AWS_KEY_NAMES})\s*+{ASSIGN}{_AWS_GAP}"
    rf"(?!\[REDACTED:){NOT_A_REFERENCE}"
)
#: A literal default in ``${NAME:-default}`` under an AWS name, read up to its ``}``.
AWS_BRACED_DEFAULT = (
    rf"(?<=\$\{{)(?=(?i:[{_KEY_FIRSTS}]))(?i:{AWS_KEY_NAMES})"
    rf"\s*(?>:[-=?+]?|[-=])\s*(?![$}}])(?=[^\s}}]{{8}})[^\s}}]+"
)

#: One character of a quoted value: anything but whitespace, a quote or a backslash, or a run of
#: backslashes and the character they escape when that is none of them, as a JSON string inside
#: JSON escapes an escape. A value is one word, as one without quotes is, so a sentence in quotes
#: after a name (a label, a message) stays.
_CHAR = r"(?:[^\s\\\"']|\\++[^\s\\\"'])"

#: A name, the quote that closes it in JSON, :data:`QUOTED_ASSIGN` with any whitespace around it (a
#: line break included), and a value of eight or more characters that the same quote opens and
#: closes, at any depth of escaping (``"``, ``\"``, ``\\\"``), and after an AWS name not a
#: reference. A value on a line of its own is read only when what follows its closing quote is not
#: ``:`` or ``=>``: that is a key of its own, after a name whose value is empty. A mask holds a
#: space, so a masked value is never read as one again. The value can never hold a quote, so it ends
#: at the first one after its own, and the scan stays linear in the length of the text.
QUOTED_RE = re.compile(
    rf"(?=(?i:[{_FIRSTS}]))(?i:(?P<aws>{AWS_NAMES})|{NAMES})(?:\\*+[\"'])?+\s*+{QUOTED_ASSIGN}"
    rf"[^\S\r\n]*+(?P<nl>[\r\n]\s*+)?+(?P<quote>\\*+[\"'])"
    rf"(?(aws){NOT_A_REFERENCE})(?P<value>{_CHAR}{{8,}}+)(?P=quote)"
    rf"(?(nl)(?![^\S\r\n]*+(?::(?!:)|=>)))"
)

#: The mask for the value, the same as for a value written without quotes.
TAG = "[REDACTED: credential]"


def redact(text: str) -> tuple[str, list[str]]:
    """*text* with each quoted value :data:`QUOTED_RE` finds replaced by :data:`TAG`, its name and
    quotes kept, and a warning for each that names no part of the value."""
    warnings: list[str] = []

    def _tag(m: "re.Match[str]") -> str:
        warnings.append("Redacted a quoted credential value")
        return m.group()[: m.start("value") - m.start()] + TAG + m.group("quote")

    return QUOTED_RE.sub(_tag, text), warnings
