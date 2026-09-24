# Tool-name wire fidelity (SM-12)

What happens to a tool's name between its `ToolDefinition` and the moment the
runtime dispatches a call back to it. The invariant this page (and the census
rail, `tests/test_tool_name_wire_fidelity.py`) protects: **a chat turn that
references a tool by any form the wire can produce dispatches to exactly that
tool, on this turn and on every later turn.**

## The wire map

| # | Hop | Transform | Owner |
|---|-----|-----------|-------|
| 1 | `ToolDefinition.name` → OpenAI-shape schema (`tool_definitions_to_openai_schema`, `agents/native/tools.py`) | **verbatim** | us |
| 2 | Schema → provider adapter (`llm/openai.py` passthrough; `llm/anthropic.py` `_translate_tools` hoists fields) | **verbatim** — shape changes, the name string does not | us |
| 3 | Adapter → provider API | **may rewrite or reject**: hosted APIs constrain names (commonly `[a-zA-Z0-9_-]`, ≤64 chars). We do not control this hop; `_sanitized_tool_key` (`agents/native/runtime.py`) mirrors it | provider |
| 4 | Model → tool call | the model echoes either the real name or the provider-rewritten form — including a form it saw in an EARLIER turn's history (the chat turn boundary) | model |
| 5 | Call → dispatch (`_resolve_name`) | **exact match first, always**; only a name that is neither a real tool nor a meta-tool consults the sanitized(real)→real healing map | us |

Turn history stores whatever form the model used at hop 4; nothing re-writes it
on replay, so hop 5 is the single healing point for every later turn too.

## The one lossy spot, and its rail

`build_sanitized_index` refuses to heal a sanitized form that two real names
share (dispatching a guess would be worse than failing) and refuses to remap a
form that shadows a real exact name. Those tools stay callable by their exact
names, but a provider rewrite of them cannot be healed — the only loss on the
wire. It is therefore:

- **loud**: `_load_tools` logs a warning naming the colliding real names;
- **railed**: the census test asserts the full shipped tool census is
  collision-free under `_sanitized_tool_key`, so a new tool whose name would
  collide fails CI instead of shipping a heal gap.

Renaming, not cleverer healing, is the fix for a collision: the rewrite at
hop 3 belongs to the provider, so no local scheme can make two identical
rewritten forms distinguishable on the way back.
