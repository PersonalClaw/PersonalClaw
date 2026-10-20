# Tool-name wire fidelity

What happens to a tool's name between its `ToolDefinition` and the moment the
runtime dispatches a call back to it. The invariant this page (and the census
rail, `tests/test_tool_name_wire_fidelity.py`) protects: **every request names
each tool in a form every provider accepts, and a chat turn that references a
tool by any form the wire can produce dispatches to exactly that tool, on this
turn and on every later turn.** The same journey for a tool's parameter SCHEMA
(and its description) is [tool-schema-wire.md](tool-schema-wire.md).

## The name every provider accepts

Converse, OpenAI function calling and the Messages API each take a tool name of
letters, digits, `_` and `-` only: at most 64 characters on Converse and OpenAI,
128 on the Messages API. A request with any other name fails as a whole, before
the model sees it. A tool's real name need not be one — an external MCP tool is
`mcp/<server>/<tool>` — so the request carries the tool's **model-safe form**
(`model_safe_name`, `agents/native/tool_names.py`): each other character becomes
`_`, cut to 64. A name already in that form is its own. The real name stays the
tool's identity everywhere else: the dispatch index, approvals, the user's
switches, the Tools page and the tool catalog the model reads.

## The wire map

| # | Hop | Transform | Owner |
|---|-----|-----------|-------|
| 1 | The name census (`NativeAgentRuntime._build_catalog`) | a tool whose model-safe form another tool of the same request (or one of the runtime's own) already has is **left out**, with one log line naming them (below) | us |
| 2 | `ToolDefinition.name` → OpenAI-shape schema (`tool_definitions_to_openai_schema`, `agents/native/tools.py`) | **the model-safe form** | us |
| 3 | Schema → provider adapter (`llm/openai.py` passthrough; `llm/anthropic.py` `_translate_tools` hoists fields; an app's adapter maps its own envelope) | **verbatim** — shape changes, the name string does not | us / the app |
| 4 | Model → tool call | the model echoes the form it was given, or the real name it read in the tool catalog or an earlier turn's history | model |
| 5 | Call → dispatch (`_resolve_name`) | **exact match first, always**; only a name that is neither a real tool nor a meta-tool consults the model-safe(real)→real map | us |

Turn history stores whatever form the model used at hop 4; nothing re-writes it
on replay, so hop 5 is the single mapping point for every later turn too. A
history entry naming a tool by its real name is a past call, not a definition,
and the providers' contracts for those are looser (the Messages API takes up to
200 characters of any kind there; an adapter that needs more maps it itself).

## Two tools that travel under one name

`build_sanitized_index` maps a model-safe form back to its real name only when
no other tool of the request shares the form: two tools a call names the same
way cannot be told apart, and dispatching a guess would run the wrong tool. A
request can carry a form only once, so of the tools that share one, only the one
whose real name IS the form (if one is) is offered; the rest are left out of the
request (`left_out`). The runtime's own tools (`tool_search`, `tool_schema`,
`reset_tools`) are in that census as names already taken: a provider's tool
called one of them, or sent under one, could never be called (the runtime
answers that name), and a request carrying both would name a tool twice. That
is:

- **loud**: the runtime logs one warning per shared form, naming the tools left
  out, and the Tools page records a clash between two of its tools as a load
  failure of their provider;
- **railed**: the census test asserts the full shipped tool census shares no
  form, so a new tool whose name would clash fails CI instead of shipping a tool
  no model can see.

Renaming is the fix for a clash: the model-safe form is the one name every
provider accepts, so no other scheme can make two identical forms
distinguishable on the way back.
