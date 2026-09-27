#!/usr/bin/env python3
"""Committed planning-id census: the ratchet that keeps roadmap and planning ids out of new text.

The roadmap is private planning state kept outside this repository, so an id that points into it
— a work-item id, a plan's name, a session or task number, a section of a plan, the ledger's own
vocabulary — tells a reader of the public tree nothing, and it tells the world what the private
plan is called. The tree still carries a known population of them in comments and docs: where
removing one would break its sentence, and in docstrings the product serves. Later cleanups
remove those. This census makes sure nothing ADDS to them.

It counts the ids in every tracked file's comments, docstrings, test titles and documentation
prose, per file, into the committed ``planning-id-baseline.json``; ``tests/test_planning_id_
baseline.py`` renders it again and requires every per-file count to be at most the committed one.

WHERE IT LOOKS — the text a reader reads, never code:

- Python: comments, and every docstring (module, class, function).
- JavaScript and TypeScript: comments, and the titles of ``describe``/``it``/``test`` in tests.
- CSS: comments. Shell, YAML, TOML, Makefiles and the like: ``#`` comments.
- Markdown: every line outside a fenced code block. HTML and SVG: ``<!-- -->`` comments.

Never code, string literals, fixtures, lockfiles, other baselines, the generated references
(each is compared byte for byte with its generator), or the product data shipped under ``src/``
that is not Python. That is exactly the text the history rewrite of this repository cleans, so
the census and that rewrite agree on what "a comment or a doc" is.

WHAT IT LOOKS FOR. Two halves:

- The generic shapes are written here in plain text, because a shape says nothing private: a
  session or task number, a section sign pointing into a plan, "plan" and a number, a revision,
  finding, gap, seam, slice, criterion or ledger number, and the ledger's vocabulary words.
- The private half — the prefixes of the ledger's work-item ids and the names of its plans — is
  known to this file ONLY as salted digests (``planning_id_rule`` in
  ``publication-hygiene-baseline.json``, the same digest and salt the internal-reference rule
  uses). The text is folded into candidates of each id shape, each distinct candidate is
  digested once, and the digests are compared. A work-item id counts only up to its prefix's
  number ceiling, so an algorithm or a standard named like one is not an id. Add a new prefix or
  plan with ``python scripts/check_publication_hygiene.py --digest KIND TEXT``.

A per-file count is the unit, never the ids themselves: listing what was found would publish the
very names the private half keeps out of the tree.

⚠️  FORBIDDEN-TO-RAISE RULE — "fix the text, not the baseline": when the ratchet reds because a
    count ROSE, say what the code does or why without the id. Never regenerate
    ``planning-id-baseline.json`` to bless the higher number. Regenerate only when counts
    legitimately FELL (an id removed, a file with ids deleted), or when a file moved and took its
    ids with it — the total never rises — in that same commit.

⚠️  SHIPPED AT THE MEASURED POPULATION, NOT AT ZERO: the ratchet forbids growth; driving the
    population down is the cleanup's job.

The render is DETERMINISTIC: sorted keys and paths, POSIX repo-relative paths, no timestamps,
``json.dumps(..., indent=2, sort_keys=True)`` and a trailing newline.

Regenerate in place (only on a legitimate shrink or a move) with::

    python scripts/generate_planning_id_baseline.py
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from check_publication_hygiene import (  # noqa: E402
    internal_reference_digest,
    load_baseline,
)

GENERATED_FROM = "scripts/generate_planning_id_baseline.py"

# ── where it looks ───────────────────────────────────────────────────────────────────────────

PY_EXT = {".py", ".pyi"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".astro"}
CSS_EXT = {".css", ".scss"}
HASH_EXT = {
    ".sh",
    ".bash",
    ".zsh",
    ".yml",
    ".yaml",
    ".toml",
    ".nix",
    ".cfg",
    ".ini",
    ".spec",
    ".in",
    ".rules",
    ".backend",
    ".web",
    ".template",
    ".example",
    ".dockerignore",
    ".gitignore",
}
HASH_NAMES = {
    "Makefile",
    "Dockerfile",
    ".gitignore",
    ".dockerignore",
    "pre-commit",
    "pre-push",
    "prepare-commit-msg",
    "commit-msg",
    "CODEOWNERS",
    "install",
    ".env.example",
    "MANIFEST.in",
}
MD_EXT = {".md", ".mdx", ".markdown"}
HTML_EXT = {".html", ".htm", ".svg", ".xml", ".vue"}

#: Never read: invented data, generated locks, other baselines, the generated references, and
#: the non-Python product data under ``src/``.
NEVER = re.compile(
    r"(?:^|/)(?:fixtures|tests_fixtures|__snapshots__)/"
    r"|(?:^|/)(?:uv\.lock|package-lock\.json|flake\.lock)$"
    r"|-baseline\.json$|\.snap$"
    r"|^docs/reference/|^src/personalclaw/reference/"
    r"|^src/(?!.*\.py$)"
)

_TEST_PATH = re.compile(
    r"(?:^|/)tests?/|(?:^|/)test_[^/]*\.py$|_test\.py$|\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)e2e/"
)
_PY_PRAGMA = re.compile(r"#\s*(?:noqa|type:|pragma|nosec|fmt:|pyright:|mypy:|ruff:|isort:|pylint:)")
_JS_PRAGMA = re.compile(
    r"/[/*]\s*(?:eslint|@ts-|prettier|istanbul|c8|@vitest|#region|#endregion|biome)"
)
_HASH_PRAGMA = re.compile(r"#\s*(?:shellcheck|noqa|yamllint|type:|nosec)")
_TEST_TITLE = re.compile(
    r"\b(?:describe|it|test|suite)(?:\.(?:each|only|skip|todo|concurrent|sequential|fails"
    r"|runIf\([^)]*\)|skipIf\([^)]*\)))*\s*\(\s*(['\"])((?:\\.|(?!\1)[^\\\n])*)\1"
)
_FENCE = re.compile(r"^(```|~~~)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KW = {
    "return",
    "typeof",
    "instanceof",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "case",
    "do",
    "else",
    "yield",
    "await",
}

Span = tuple[str, str]  # (kind, text): kind is comment, docstring, test-title or prose


def language(path: str) -> str:
    """Which extractor reads *path*: ``py``, ``js``, ``css``, ``hash``, ``md``, ``html`` or
    ``other`` (read by none)."""
    p = PurePosixPath(path)
    ext = p.suffix.lower()
    for lang, exts in (("py", PY_EXT), ("js", JS_EXT), ("css", CSS_EXT), ("md", MD_EXT)):
        if ext in exts:
            return lang
    if ext in HTML_EXT:
        return "html"
    if (
        p.name.startswith("Dockerfile")
        or p.name in HASH_NAMES
        or ext in HASH_EXT
        or (p.parent.name in (".githooks", "hooks") and not ext)
    ):
        return "hash"
    return "other"


def _python_spans(text: str) -> list[Span]:
    out: list[Span] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        if tok.start[0] == 1 and tok.string.startswith("#!"):
            continue
        if not _PY_PRAGMA.match(tok.string):
            out.append(("comment", tok.string))
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.append(("docstring", doc))
    return out


def _js_spans(path: str, text: str, *, css: bool = False) -> list[Span]:
    """Comments (and, in a test file, test titles), found by a lexer that steps over strings,
    template literals and regular-expression literals, so a ``//`` inside one is not a comment."""
    found: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    last_sig, last_word = "", ""
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/" and not css:
            j = text.find("\n", i)
            j = n if j == -1 else j
            found.append((i, j, "comment"))
            i = j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            found.append((i, j, "comment"))
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    break
                j += 1
            i, last_sig = j + 1, c
            continue
        if c == "`" and not css:
            i, last_sig = _skip_template(text, i + 1) + 1, "`"
            continue
        if (
            c == "/"
            and not css
            and (last_sig in _REGEX_PREV or not last_sig or last_word in _REGEX_KW)
        ):
            end = _regex_end(text, i + 1)
            if end is not None:
                i = end
                while i < n and text[i].isalpha():
                    i += 1
                last_sig, last_word = "/", ""
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last_word, last_sig, i = text[i:j], "a", j
            continue
        last_sig, last_word = c, ""
        i += 1
    if not css and _TEST_PATH.search(path):
        for m in _TEST_TITLE.finditer(text):
            if not any(s <= m.start(2) < e for s, e, _ in found):
                found.append((m.start(2), m.end(2), "test-title"))
    return [(kind, text[s:e]) for s, e, kind in sorted(found) if not _JS_PRAGMA.match(text[s:e])]


def _skip_template(text: str, j: int) -> int:
    """The index of the backtick that closes a template literal opened just before *j*."""
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == "`":
            return j
        if text[j] == "$" and j + 1 < n and text[j + 1] == "{":
            depth, k = 1, j + 2
            while k < n and depth:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                elif text[k] in "'\"`":
                    quote = text[k]
                    k += 1
                    while k < n and text[k] != quote:
                        k += 1 + (text[k] == "\\")
                k += 1
            j = k
            continue
        j += 1
    return n


def _regex_end(text: str, j: int) -> int | None:
    """One past the ``/`` closing a regular-expression literal that starts before *j*, if one
    closes on this line."""
    in_class = False
    while j < len(text) and text[j] != "\n":
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return j + 1
        j += 1
    return None


def _hash_spans(text: str) -> list[Span]:
    out: list[Span] = []
    for number, raw in enumerate(text.split("\n"), 1):
        if number == 1 and raw.startswith("#!"):
            continue
        quote = None
        for idx, ch in enumerate(raw):
            if quote:
                quote = None if ch == quote else quote
                continue
            if ch in "'\"":
                quote = ch
            elif ch == "#" and (idx == 0 or raw[idx - 1] in " \t"):
                if not _HASH_PRAGMA.match(raw[idx:]):
                    out.append(("comment", raw[idx:]))
                break
    return out


def _markdown_spans(text: str) -> list[Span]:
    out: list[Span] = []
    fence = None
    for raw in text.split("\n"):
        m = _FENCE.match(raw.lstrip())
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if not fence and raw.strip():
            out.append(("prose", raw))
    return out


def spans(path: str, text: str) -> list[Span]:
    """The ``(kind, text)`` spans of *path* that are read: comments, docstrings, test titles and
    doc prose. Empty for a path the census never reads."""
    if NEVER.search(path):
        return []
    lang = language(path)
    if lang == "py":
        return _python_spans(text)
    if lang in ("js", "css"):
        got = _js_spans(path, text, css=lang == "css")
        if path.endswith(".astro"):
            got += [("comment", m.group(0)) for m in _HTML_COMMENT.finditer(text)]
        return got
    if lang == "hash":
        return _hash_spans(text)
    if lang == "md":
        return _markdown_spans(text)
    if lang == "html":
        return [("comment", m.group(0)) for m in _HTML_COMMENT.finditer(text)]
    return []


# ── what it looks for ────────────────────────────────────────────────────────────────────────

_BEFORE = r"(?<![A-Za-z0-9_./#-])"
_AFTER = r"(?![0-9A-Za-z_])"

#: A work-item id: a prefix of capitals and digits, a hyphen, a number, then an optional letter,
#: roman sub-part or ``/N``. Whether the prefix is the ledger's is decided by digest.
_WORK_ITEM = re.compile(
    _BEFORE + r"(?P<prefix>[A-Z][A-Z0-9]{1,5})-(?P<num>\d{1,3})[a-z]?"
    r"(?:-(?:i|ii|iii|iv|v|vi))?(?:/\d{1,2}[a-z]?)?" + _AFTER
)
#: Hyphenated names in capitals or in title case; every leading run of their parts is a
#: candidate plan name.
_UPPER_RUN = re.compile(_BEFORE + r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")
_TITLE_RUN = re.compile(_BEFORE + r"[A-Z][a-z0-9]*(?:-[A-Z][a-z0-9]*)+")
#: A single word in capitals: a candidate one-word plan name.
_CAPITAL_WORD = re.compile(_BEFORE + r"[A-Z][A-Z0-9]{1,15}" + _AFTER)
#: What makes a one-word plan name a reference: a section sign, a session or a task after it.
_IN_CONTEXT = re.compile(r"\s*§|\s+S\d|\s+T\d")
#: After a work-item-shaped token: an algorithm, not an id.
_ALGORITHM_AFTER = re.compile(
    r"[\s-]+(?:algorithm|spaced|interval|scheduler|scheduling|repetition)"
)
#: After a session-shaped token: storage, not a session.
_STORAGE_AFTER = re.compile(r"[\s-]+(?:bucket|object|API|storage|endpoint|compatible|remote)")
#: A plan name in capitals just before a session makes it a session even when it reads as storage.
_PLAN_BEFORE = re.compile(r"[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+`?\s+$")

_SESSION = re.compile(_BEFORE + r"S\d{1,3}(?:\.\d+)?(?:[a-z]\b)?" + _AFTER)
_SECTION = re.compile(_BEFORE + r"§\s*[A-Z0-9](?:[\w.]*\w)?")
#: The generic shapes. None names anything private, so each is written out.
_GENERIC = [
    re.compile(_BEFORE + r"[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+(?=\s*§)"),
    re.compile(_BEFORE + r"T\d{1,2}\.\d{1,2}(?:\s*[/–-]\s*T?\d{1,2}\.\d{1,2})?" + _AFTER),
    re.compile(
        _BEFORE
        + r"plan\s+\d{1,2}(?:\s+S\d{1,2}(?:[–-]S?\d{1,2})?)?(?:\s+T\d{1,2}\.\d{1,2})?"
        + _AFTER
    ),
    re.compile(_BEFORE + r"rev[- ]\d{1,2}" + _AFTER),
    re.compile(_BEFORE + r"F-\d{1,3}" + _AFTER),
    re.compile(_BEFORE + r"G\d{1,3}(?:\.\d+)?(?:\s*[–-]\s*G\d{1,3})?" + _AFTER),
    re.compile(_BEFORE + r"seam[- ]\d+[a-z]?" + _AFTER),
    re.compile(_BEFORE + r"Slice\s+\d+" + _AFTER),
    re.compile(_BEFORE + r"C\d\.\d(?:\s*[–-]\s*C\d\.\d)?" + _AFTER),
    re.compile(_BEFORE + r"ledger\s+\d{2,4}" + _AFTER),
    re.compile(_BEFORE + r"SC#?\d{1,2}" + _AFTER),
    re.compile(r"(?<![\"'`\w])(?:roadmap |ledger )?atoms?\b(?![\"'`]|\s*\(a\s)"),
    re.compile(r"\bATOM(?:'S|S)?\b"),
    re.compile(r"`{0,2}\bdone[_-]when\b`{0,2}"),
    re.compile(r"(?i:\bdeclared[- ]done\b)"),
    re.compile(r"\bclass-[BS] (?=change|entry|work)"),
]


class Vocabulary:
    """The private half, known only as digests: which prefixes, plan names and plan words are
    the ledger's. Each distinct candidate is digested once per process."""

    def __init__(self, rule: dict[str, Any], salt: str) -> None:
        denied = rule["denied"]
        self._salt = salt
        self._prefix_ceiling: dict[str, int] = dict(denied["work-item-prefix"])
        self._plan_names = frozenset(denied["plan-name"])
        self._plan_words: dict[str, str] = dict(denied["plan-word"])
        self._memo: dict[tuple[str, str], str] = {}

    def _digest(self, kind: str, candidate: str) -> str:
        key = (kind, candidate)
        got = self._memo.get(key)
        if got is None:
            got = self._memo[key] = internal_reference_digest(kind, candidate, self._salt)
        return got

    def prefix_ceiling(self, prefix: str) -> int | None:
        return self._prefix_ceiling.get(self._digest("work-item-prefix", prefix))

    def is_plan_name(self, name: str) -> bool:
        return self._digest("plan-name", name.lower()) in self._plan_names

    def plan_word_mode(self, word: str) -> str | None:
        return self._plan_words.get(self._digest("plan-word", word.lower()))


@lru_cache(maxsize=1)
def shipped_vocabulary() -> Vocabulary:
    policy = load_baseline()
    return Vocabulary(policy["planning_id_rule"], policy["internal_reference_rule"]["digest_salt"])


def _plan_name_hits(text: str, vocab: Vocabulary) -> Iterator[tuple[int, int]]:
    for run_pattern in (_UPPER_RUN, _TITLE_RUN):
        for run in run_pattern.finditer(text):
            parts = run.group(0).split("-")
            for count in range(len(parts), 1, -1):
                end = run.start() + len("-".join(parts[:count]))
                if end < len(text) and re.match(r"[0-9A-Za-z_]", text[end]):
                    continue
                if vocab.is_plan_name(text[run.start() : end]):
                    yield run.start(), end
                    break


def id_hits(text: str, kind: str, vocab: Vocabulary) -> list[tuple[int, int]]:
    """The ``(start, end)`` of every planning id in one span, overlaps merged. *kind* is the
    span's kind: in doc ``prose`` a bare section sign is the document's own numbered section, so
    only there it is not counted."""
    hits: list[tuple[int, int]] = []
    for m in _WORK_ITEM.finditer(text):
        ceiling = vocab.prefix_ceiling(m.group("prefix"))
        if ceiling is not None and int(m.group("num")) <= ceiling:
            if not _ALGORITHM_AFTER.match(text, m.end()):
                hits.append(m.span())
    hits.extend(_plan_name_hits(text, vocab))
    for m in _CAPITAL_WORD.finditer(text):
        mode = vocab.plan_word_mode(m.group(0))
        if mode == "any" or (mode == "in-context" and _IN_CONTEXT.match(text, m.end())):
            hits.append(m.span())
    for m in _SESSION.finditer(text):
        if m.group(0) == "S3" or _STORAGE_AFTER.match(text, m.end()):
            if not _PLAN_BEFORE.search(text[max(0, m.start() - 80) : m.start()]):
                continue
        hits.append(m.span())
    if kind != "prose":
        hits.extend(m.span() for m in _SECTION.finditer(text))
    for pattern in _GENERIC:
        hits.extend(m.span() for m in pattern.finditer(text))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(hits):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def count_ids(path: str, text: str, vocab: Vocabulary | None = None) -> int:
    """How many planning ids *path*'s read spans carry."""
    vocab = vocab or shipped_vocabulary()
    return sum(len(id_hits(span, kind, vocab)) for kind, span in spans(path, text))


# ── the census ───────────────────────────────────────────────────────────────────────────────


def tracked_texts(root: Path | None = None) -> Iterator[tuple[str, str]]:
    """``(path, text)`` for every tracked text file the census reads, in path order."""
    root = root or REPO
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    for path in sorted(p for p in listing.split("\0") if p):
        if NEVER.search(path) or language(path) == "other":
            continue
        full = root / path
        if not full.is_file():
            continue
        blob = full.read_bytes()
        if b"\0" in blob[:8192]:
            continue
        yield path, blob.decode("utf-8", "replace")


def census(files: Iterable[tuple[str, str]], vocab: Vocabulary | None = None) -> dict[str, Any]:
    """The inventory of *files* (``(path, text)`` pairs — a tree, from any source)::

    {"generated_from": ..., "per_file": {"<path>": N, ...}, "totals": {"files": F, "ids": T}}

    A file with no id is left out. Pure, so a tree that is not checked out can be measured too.
    """
    vocab = vocab or shipped_vocabulary()
    per_file: dict[str, int] = {}
    for path, text in files:
        found = count_ids(path, text, vocab)
        if found:
            per_file[path] = found
    return {
        "generated_from": GENERATED_FROM,
        "per_file": dict(sorted(per_file.items())),
        "totals": {"files": len(per_file), "ids": sum(per_file.values())},
    }


def build_inventory(root: Path | None = None) -> dict[str, Any]:
    return census(tracked_texts(root))


def render(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def regressions(committed: dict[str, int], current: dict[str, int]) -> list[str]:
    """Every file whose count ROSE over its committed count (a file not in the baseline had 0)."""
    return [
        f"{path}: planning ids rose {committed.get(path, 0)} -> {count}"
        for path, count in sorted(current.items())
        if count > committed.get(path, 0)
    ]


def stale_high(committed: dict[str, int], current: dict[str, int]) -> list[str]:
    """Every file whose committed count is above what it holds now: a shrink not recorded."""
    return [
        f"{path}: committed {count} > current {current.get(path, 0)}"
        for path, count in sorted(committed.items())
        if count > current.get(path, 0)
    ]


def baseline_path() -> Path:
    return REPO / "planning-id-baseline.json"


def main() -> None:
    path = baseline_path()
    path.write_text(render(build_inventory()), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
