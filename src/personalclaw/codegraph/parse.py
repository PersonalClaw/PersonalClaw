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


def parser_available(language: str) -> bool:
    """Whether a parser for ``language`` can be loaded right now.

    False is a normal answer, not an error: a stripped environment without the
    parser wheels simply gets no graph.
    """
    if not language:
        return False
    try:
        from tree_sitter_language_pack import get_parser

        get_parser(language)  # type: ignore[arg-type]
        return True
    except Exception:  # noqa: BLE001
        return False


def _get_parser(language: str):
    from tree_sitter_language_pack import get_parser

    return get_parser(language)  # type: ignore[arg-type]


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
        tree = _get_parser(language).parse(source)
    except Exception:  # noqa: BLE001 — no parser / bad bytes ⇒ no graph for this file
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
