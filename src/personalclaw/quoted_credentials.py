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

#: AWS's names for a secret key and a session token, in the case ``security`` reads them in: the
#: AWS CLI's JSON writes ``SecretAccessKey``, a credentials file ``aws_secret_access_key``.
_AWS_NAMES = r"SecretAccessKey|aws_secret_access_key|SessionToken|aws_session_token"

#: One character of a quoted value: anything but whitespace, a quote or a backslash, or a run of
#: backslashes and the character they escape when that is none of them, as a JSON string inside
#: JSON escapes an escape. A value is one word, as one without quotes is, so a sentence in quotes
#: after a name (a label, a message) stays.
_CHAR = r"(?:[^\s\\\"']|\\++[^\s\\\"'])"

#: A name, the quote that closes it in JSON, ``:`` or ``=`` with any whitespace around it (a line
#: break included), and a value of eight or more characters that the same quote opens and closes,
#: at any depth of escaping (``"``, ``\"``, ``\\\"``). A mask holds a space, so a masked value is
#: never read as one again. The value can never hold a quote, so it ends at the first one after its
#: own, and the scan stays linear in the length of the text.
QUOTED_RE = re.compile(
    rf"(?:(?i:{NAMES})|{_AWS_NAMES})(?:\\*+[\"'])?+\s*+[:=]\s*+(?P<quote>\\*+[\"'])"
    rf"(?P<value>{_CHAR}{{8,}}+)(?P=quote)"
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
