"""The codebase graph — a symbol index the agent queries instead of grepping blind
(CONTEXT-ECONOMY §5.5).

Finding where a function is defined and who calls it currently costs three to five
tool calls: a `grep` for the name, a `read_file` or two to see context, another grep
for callers. Each round-trip is tokens. This package answers the same question in
one call from a SQLite index built by tree-sitter.

**Fail-soft is the whole contract.** No index, an unparseable file, a missing
parser, a rebuild that runs long — every one of these degrades to "no graph", and
the agent goes back to grep/read exactly as before. The graph is an accelerator,
never a dependency. That is why nothing here raises into a caller: an indexing bug
must never be able to break a code session.

Deliberately NOT a language server: definitions, imports, and name-based
references, with no type inference, no scope resolution, and no cross-file symbol
binding. A "reference" here means "this file mentions this name", which is
grep-grade precision with graph-grade speed — honest about what it is, and enough to
replace the grep it stands in for.
"""

from personalclaw.codegraph.index import (
    CodeGraphIndex,
    IndexStats,
    default_db_path,
    index_workspace,
    workspace_key,
)
from personalclaw.codegraph.parse import (
    LANGUAGE_BY_SUFFIX,
    Definition,
    Reference,
    parse_source,
    parser_available,
)

__all__ = [
    "CodeGraphIndex",
    "Definition",
    "IndexStats",
    "LANGUAGE_BY_SUFFIX",
    "Reference",
    "default_db_path",
    "index_workspace",
    "parse_source",
    "parser_available",
    "workspace_key",
]
