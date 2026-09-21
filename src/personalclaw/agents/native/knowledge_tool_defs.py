"""The knowledge-library tool schemas, kept out of :mod:`builtin_tools`.

Extracted from :mod:`builtin_tools` for a STRUCTURAL reason, not a stylistic one, and in
the shape :mod:`personalclaw.agents.native.decision_tool_defs` already established (#3253).
``tests/test_structural_baseline.py::test_the_watch_band_is_not_sitting_on_a_cliff`` demands
>=100 lines of headroom below the 2800-line watch band, and ``builtin_tools.py`` at 2,667
lines held the controlling position with only 33 usable lines left — so an unrelated +34
reddened the rail. The band is NOT the thing to move; the file is.

Nothing about the tools changed. This module owns their SCHEMAS only: the category mapping
(``_CATEGORY_OF``), the ``_t_*`` dispatch methods and the call-site rails all stay in
``builtin_tools``, and ``_all_tool_defs`` splices these definitions back in at the same
position, so the advertised order is unchanged.
"""

from __future__ import annotations

from typing import Any

from personalclaw.tool_providers.base import RiskLevel, ToolDefinition


def _structural_verbs() -> list[str]:
    """The structural-retrieval verb vocabulary, read from the module that OWNS it.

    Copying the five strings in here would let the tool advertise a verb the retriever no
    longer accepts (or hide one it gained). ``builtin_tools`` reads the SAME constant for
    ``_t_knowledge_structural``'s own validation, so schema and handler cannot drift apart.
    Imported lazily because the registry is built on every session start and the knowledge
    package need not be touched until a knowledge tool is actually described.
    """
    from personalclaw.knowledge.structural import STRUCTURAL_VERBS

    return list(STRUCTURAL_VERBS)


def knowledge_tool_definitions(provider: str, s: dict[str, Any]) -> list[ToolDefinition]:
    """The six knowledge-library tools, in the order ``builtin_tools`` listed them."""
    return [
        ToolDefinition(
            name="knowledge_search",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description="Search the user's knowledge library (notes, bookmarks, docs). Args: query (str), optional limit (int, default 8).",  # noqa: E501
            parameters={
                **s,
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        ),
        ToolDefinition(
            name="knowledge_create",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Add an item to the user's knowledge library. Args: type "
                "('note'|'fleeting'|'journal'|'gist'|'bookmark', default 'note'), "
                "title (str), content (str — the note/gist body), url (str — for bookmark), "
                "optional tags (list of str), optional gist_language (str — the "
                "code language for a gist, e.g. 'python')."
            ),
            parameters={
                **s,
                "properties": {
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "url": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "gist_language": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="knowledge_get",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description="Fetch one knowledge item by id (title, type, content, tags, summary). Args: id (str).",  # noqa: E501
            parameters={**s, "properties": {"id": {"type": "string"}}, "required": ["id"]},
        ),
        ToolDefinition(
            name="knowledge_update",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Update an existing knowledge item and re-enrich it. Args: id (str, required), "
                "and any of title (str), content (str), tags (list of str), url (str), "
                "gist_language (str — only for gist items; sets the code language for syntax "
                "highlighting), is_pinned (bool), is_archived (bool). Editing content/url re-runs extraction."  # noqa: E501
            ),
            parameters={
                **s,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "url": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "gist_language": {"type": "string"},
                    "is_pinned": {"type": "boolean"},
                    "is_archived": {"type": "boolean"},
                },
                "required": ["id"],
            },
        ),
        ToolDefinition(
            name="knowledge_structural",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "Ask a STRUCTURAL question about the knowledge library and get the answer "
                "by traversing stored links — not by semantic similarity. Use this instead "
                "of knowledge_search whenever the question is about relations rather than "
                "topic; a similarity search answers 'what links to this' only by accident. "
                "Args: verb (str, required) — one of 'links_to' (what points AT an item: "
                "typed relations + citations), 'depends_on' (the outbound dependency chain), "
                "'tag_subtree' (everything under a tag and its child tags), 'changed_since' "
                "(what was updated after a timestamp), 'contradictions' (items recorded as "
                "contradicting each other); origin (str) — item id for links_to/depends_on, "
                "tag name for tag_subtree, optional item id to scope contradictions; since "
                "(str, ISO timestamp) for changed_since; depth (int, default 1, max 6) — how "
                "many hops to follow; limit (int, default 25); rank_query (str, optional) — "
                "orders the structural result by closeness to this text WITHOUT changing "
                "which items are in it. Every result carries the exact link path that "
                "reached it, so you can cite why. An empty answer states which relation is "
                "missing; it never silently degrades to a similarity guess."
            ),
            parameters={
                **s,
                "properties": {
                    "verb": {"type": "string", "enum": _structural_verbs()},
                    "origin": {"type": "string"},
                    "since": {"type": "string"},
                    "depth": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "rank_query": {"type": "string"},
                },
                "required": ["verb"],
            },
        ),
        ToolDefinition(
            name="knowledge_stats",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "Get an overview of the knowledge library for gap detection: total item "
                "count, a by-type breakdown, and the most common tags. No args."
            ),
            parameters={**s, "properties": {}},
        ),
    ]
