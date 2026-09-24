"""Tree-sitter extraction: definitions, imports, references (CONTEXT-ECONOMY §5.5).

One function does the work — :func:`parse_source` — and it never raises. Every
failure mode (no parser installed, a syntax error, a file that isn't really code)
returns an empty result, because the index is an accelerator and a broken file must
cost nothing but that file.

The node-type tables below were read off the real grammars rather than guessed;
they differ per language in ways that are easy to get wrong (Rust has both
`function_item` and `function_signature_item`; Go separates `method_declaration`
from `function_declaration`; TypeScript's arrow functions hide their name on the
enclosing declarator).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Which grammar to use per file suffix. Five languages in v1 — the plan's set.
LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
}

# Node types that DEFINE a named symbol, mapped to the kind we record.
_DEFINITION_NODES: dict[str, str] = {
    # Python
    "function_definition": "function",
    "class_definition": "class",
    # TypeScript / JavaScript
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "method_definition": "method",
    # Rust
    "function_item": "function",
    "function_signature_item": "function",
    "struct_item": "struct",
    "trait_item": "trait",
    "enum_item": "enum",
    "mod_item": "module",
    # Go
    "method_declaration": "method",
    "type_spec": "type",
}

# Node types that carry an import/use edge.
_IMPORT_NODES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "use_declaration",
        "import_spec",
    }
)

# Nodes whose presence means "we are inside a class/impl body", so a function found
# below one is recorded as a method with its owner attached.
_CONTAINER_NODES: dict[str, str] = {
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "impl_item": "impl",
    "trait_item": "trait",
}

# A reference candidate: an identifier used in a call or attribute position. Kept
# narrow on purpose — indexing every identifier would make "references" mean
# nothing.
_REFERENCE_PARENTS = frozenset({"call", "call_expression", "attribute", "member_expression"})

# Names too generic to be worth indexing as references.
_NOISE_NAMES = frozenset(
    {
        "self",
        "this",
        "super",
        "len",
        "str",
        "int",
        "dict",
        "list",
        "set",
        "print",
        "type",
        "format",
        "append",
        "get",
        "new",
        "make",
        "err",
        "ok",
        "nil",
        "None",
        "true",
        "false",
    }
)

_MAX_FILE_BYTES = 1_000_000  # a megabyte of one file is generated, not authored


@dataclass(frozen=True)
class Definition:
    """A named symbol defined at a location."""

    name: str
    kind: str
    line: int
    end_line: int
    owner: str = ""  # enclosing class/impl, when any
    signature: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name


@dataclass(frozen=True)
class Reference:
    """A mention of a name — grep-grade precision, index-grade speed."""

    name: str
    line: int


@dataclass(frozen=True)
class ParseResult:
    definitions: tuple[Definition, ...] = ()
    references: tuple[Reference, ...] = ()
    imports: tuple[str, ...] = ()
    language: str = ""


def language_for(path: str) -> str:
    """The grammar name for a path, or "" when we don't index that suffix."""
    lowered = str(path).lower()
    for suffix, language in LANGUAGE_BY_SUFFIX.items():
        if lowered.endswith(suffix):
            return language
    return ""


@dataclass(frozen=True)
class ParserStatus:
    """Whether a grammar can be loaded — and, when it cannot, WHY.

    ``available=False`` stays a normal answer (see :func:`parser_available`), but a
    bare False is undiagnosable after the fact: the reason the load failed used to be
    swallowed by a bare ``except``, so a CI run that lost its grammar reported nothing
    but the absence. ``reason`` carries ``"<ExceptionType>: <message>"``.
    """

    language: str
    available: bool
    reason: str = ""  # "" exactly when available


# The one actionable remedy every grammar-load failure names. The parser wheels are
# DECLARED dependencies, so "not installed" is rarely the real story: the language
# pack keeps each grammar as a shared library in a per-user cache and fetches it on
# first use, which makes a cold cache's first load a network operation.
PARSER_REMEDY = (
    "No tree-sitter grammar could be loaded for this language. The parser wheels "
    "(tree-sitter, tree-sitter-language-pack) are declared dependencies, and the "
    "language pack downloads each grammar's shared library into a per-user cache on "
    "first use — so a cold cache needs network access. Pre-fetch the grammars where "
    'the network is available: python -c "from tree_sitter_language_pack import '
    "download; download(['python'])\"."
)

# Grammars this indexer actually asks for. A load failure for one of these is worth a
# warning; a caller probing some other grammar is not.
_INDEXED_LANGUAGES = frozenset(LANGUAGE_BY_SUFFIX.values())

# language -> the last recorded load-failure reason. A logging ledger, not a cache of
# the answer: it exists so the same failure is described once per language instead of
# once per file, and a later success clears it.
_load_failures: dict[str, str] = {}


def _grammar_cache_dir() -> str:
    """Where the language pack keeps downloaded grammars, or "" if it won't say."""
    try:
        from tree_sitter_language_pack import cache_dir

        return str(cache_dir())
    except Exception:  # noqa: BLE001 — an optional diagnostic, never a failure
        return ""


def _record_load_failure(language: str, exc: BaseException) -> str:
    """Remember WHY ``language``'s grammar would not load, and say it once.

    Idempotent per (language, reason): both the loader and :func:`parse_source`'s
    fail-soft path call this, and a per-file retry must not print a per-file line.
    """
    reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    if _load_failures.get(language) != reason:
        _load_failures[language] = reason
        if language in _INDEXED_LANGUAGES:
            logger.warning(
                "codegraph: no tree-sitter parser for %s — %s (grammar cache: %s). %s",
                language,
                reason,
                _grammar_cache_dir() or "unknown",
                PARSER_REMEDY,
            )
        else:
            logger.debug("codegraph: no tree-sitter parser for %s — %s", language, reason)
    return reason


def parser_status(language: str) -> ParserStatus:
    """Probe ``language``'s grammar now, recording the reason when it won't load."""
    if not language:
        return ParserStatus(language="", available=False, reason="no language requested")
    try:
        _get_parser(language)
    except Exception as exc:  # noqa: BLE001 — the absence is the answer; the reason is kept
        return ParserStatus(language, False, _record_load_failure(language, exc))
    return ParserStatus(language, True)


def parser_available(language: str) -> bool:
    """Whether a parser for ``language`` can be loaded right now.

    False is a normal answer, not an error: a stripped environment without the
    parser wheels simply gets no graph. Ask :func:`parser_status` when you need to
    know why — this function deliberately keeps its bool contract.
    """
    return parser_status(language).available


def _get_parser(language: str):
    """The parser for ``language``, or raise — recording the reason on the way out.

    Deliberately NOT cached. The language pack's own registry already memoizes the
    loaded grammar (measured: 5.8 ms for the first load, 0.1 ms for the next 200), so
    a local cache would save microseconds while turning today's per-call parser into
    one shared across the gateway's threads — and a ``Parser`` is not safe to drive
    from two threads at once.
    """
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(language)  # type: ignore[arg-type]
    except Exception as exc:
        _record_load_failure(language, exc)
        raise
    _load_failures.pop(language, None)
    return parser


def _text(node, source: bytes) -> str:
    try:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _named_child(node, source: bytes) -> str:
    """The declared name of a definition node.

    Prefers the grammar's own `name` field; falls back to the first identifier
    child, which is what Go's `type_spec` and Rust's items need.
    """
    try:
        field = node.child_by_field_name("name")
        if field is not None:
            return _text(field, source)
    except Exception:  # noqa: BLE001
        pass
    for child in node.children:
        if child.type in (
            "identifier",
            "type_identifier",
            "property_identifier",
            "field_identifier",
        ):
            return _text(child, source)
    return ""


def _signature(node, source: bytes) -> str:
    """A one-line signature: everything up to the body, whitespace-collapsed."""
    try:
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        raw = source[node.start_byte : min(end, node.start_byte + 400)]
        return " ".join(raw.decode("utf-8", errors="replace").split())
    except Exception:  # noqa: BLE001
        return ""


def parse_source(path: str, source: bytes) -> ParseResult:
    """Extract definitions, references and imports from one file. Never raises.

    ``path`` only selects the grammar; nothing is read from disk here, which keeps
    this pure and trivially testable.
    """
    language = language_for(path)
    if not language:
        return ParseResult()
    if not source or len(source) > _MAX_FILE_BYTES:
        return ParseResult(language=language)
    try:
        parser = _get_parser(language)
    except Exception as exc:  # noqa: BLE001 — no grammar ⇒ no graph for this file
        # Named, not swallowed: _record_load_failure logs it once per language, so a
        # 1,500-file pass says why once instead of failing silently 1,500 times.
        logger.debug("codegraph: no parser for %s — %s", path, _record_load_failure(language, exc))
        return ParseResult(language=language)
    try:
        tree = parser.parse(source)
    except Exception:  # noqa: BLE001 — bad bytes ⇒ no graph for this file
        logger.debug("codegraph: cannot parse %s", path, exc_info=True)
        return ParseResult(language=language)

    definitions: list[Definition] = []
    references: dict[tuple[str, int], Reference] = {}
    imports: list[str] = []

    def visit(node, owner: str) -> None:
        kind = _DEFINITION_NODES.get(node.type)
        next_owner = owner
        if kind:
            name = _named_child(node, source)
            if name:
                # A function inside a class/impl body is a method of it.
                effective = kind
                if owner and kind == "function":
                    effective = "method"
                definitions.append(
                    Definition(
                        name=name,
                        kind=effective,
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        owner=owner,
                        signature=_signature(node, source),
                    )
                )
                if node.type in _CONTAINER_NODES:
                    next_owner = name
        elif node.type in _CONTAINER_NODES:
            # impl blocks name a type rather than declaring a symbol.
            named = _named_child(node, source)
            if named:
                next_owner = named
        elif node.type in _IMPORT_NODES:
            text = " ".join(_text(node, source).split())
            if text:
                imports.append(text[:200])
        elif node.type in ("identifier", "type_identifier", "property_identifier"):
            parent = node.parent
            if parent is not None and parent.type in _REFERENCE_PARENTS:
                name = _text(node, source)
                if name and name not in _NOISE_NAMES and len(name) > 2:
                    line = node.start_point[0] + 1
                    references.setdefault((name, line), Reference(name, line))

        for child in node.children:
            visit(child, next_owner)

    try:
        visit(tree.root_node, "")
    except RecursionError:
        # A pathologically nested file: keep whatever we found rather than nothing.
        logger.debug("codegraph: recursion limit on %s", path)

    return ParseResult(
        definitions=tuple(definitions),
        references=tuple(references.values()),
        imports=tuple(dict.fromkeys(imports)),
        language=language,
    )
