"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_rendering_registry_parity.py``

Its checks:
``tests/rails/test_rendering_registry_parity_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


_WEB = _REPO / "web" / "src"


def _code_only(text: str) -> str:
    """`text` with TS/TSX comment bodies blanked out, string literals preserved.

    🪤 The scans below look for a literal token. A bare ``in`` test counts the token where an
    author merely NAMES it — and the file that most needs to name ``dangerouslySetInnerHTML`` is
    the sanitizer's own test suite, which explains in prose that it "injects the output exactly
    as `dangerouslySetInnerHTML` does". That docstring plus one inline comment made
    ``ui/content/sanitize.test.ts`` the sole offender, so the rail reported a sanitizer-bypass
    risk in the very file proving the sanitizer works.

    A state machine rather than a regex, because both regex shortcuts are wrong here: stripping
    ``//`` to end-of-line also truncates any line holding a ``https://`` URL, which silently
    HIDES a real token later on that line (a false negative in a security rail), and stripping
    ``/* */`` first corrupts a ``"/*"`` inside a string. Tracking the three states — in-string,
    in-line-comment, in-block-comment — is the only version with neither hole.

    Comment characters are replaced with spaces rather than deleted so reported line numbers and
    offsets stay faithful to the file as the author wrote it.
    """
    out = []
    i, n = 0, len(text)
    quote = None  # the open string/template delimiter, or None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:  # an escape consumes the next char, quote or not
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


#: The ways a string becomes live markup in the web app, each matched in CODE only
#: (`_code_only`), so an author naming one in a comment is not a site.
_MARKUP_SINKS = {
    "renderer import": re.compile(
        r"""from\s+['"](?:react-markdown|rehype-raw|remark-rehype|hast-util-raw|"""
        r"""hast-util-to-jsx-runtime|mdast-util-to-hast|micromark|marked|markdown-it|dompurify|"""
        r"""sanitize-html)['"]"""
    ),
    "markup into the page": re.compile(
        # The prop being SET (`=` in JSX, `:` in an object), so an author naming it in prose
        # that `_code_only` cannot tell from code (after a regex literal) is not a site.
        r"dangerouslySetInnerHTML\s*[=:]|\.(?:inner|outer)HTML\s*=(?!=)|insertAdjacentHTML\s*\("
        r"|createContextualFragment\s*\(|document\.write(?:ln)?\s*\("
    ),
    "parsed into a document": re.compile(r"\bparseFromString\s*\("),
    "a document for a frame or file": re.compile(
        r"""\bsrcDoc=|setAttribute\(\s*['"]srcdoc['"]|type:\s*['"]text/html"""
    ),
}


def _sink_counts(code: str) -> dict[str, int]:
    counts = {name: len(pattern.findall(code)) for name, pattern in _MARKUP_SINKS.items()}
    return {name: n for name, n in counts.items() if n}
