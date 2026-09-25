# Tool-schema wire fidelity

What happens to a tool's **parameter schema** between its `ToolDefinition` and the model
provider. The invariant this page (and the rail, `tests/test_tool_schema_portability.py`)
protects: **no model request carries a tool schema a mainstream provider can reject, so one
tool can never take down a turn.** The sibling page for tool *names* is
[tool-name-wire.md](tool-name-wire.md).

## Why a single schema matters

A provider validates the whole tool block of a request, and one schema it cannot accept fails
the request — not the tool. The incident: a user chatting with Gemini through an
OpenAI-compatible router got `400 … function_declarations[15].parameters.properties[deliverables].items:
missing field` on every turn, because `project_run_create` declared five arrays with no
`items`. A turn surfaces up to 48 of the catalog's tools (`tool_retrieval.DEFAULT_K`), so a
different message would have surfaced a different broken tool and failed the same way.

## The portable profile

`src/personalclaw/tool_providers/portable_schema.py` holds the rules, their `rule` ids and
their sources; that docstring is the as-built statement. In short, a schema is portable when:

- the root is an object schema, with no top-level `anyOf`/`oneOf`/`allOf`/`not`/`enum`;
- every node has exactly one `type` from `object`/`array`/`string`/`number`/`integer`/`boolean`
  (or is an `anyOf` of such nodes);
- every array declares `items`, and every nested object declares at least one property;
- `required` names declared properties, `enum` is a non-empty list of distinct non-empty strings
  on a string, and `format` is `date-time` or absent;
- it uses only `type`, `description`, `title`, `default`, `enum`, `format`, `properties`,
  `required`, `items`, `anyOf` and the per-type bounds.

The rules are the intersection of what the strictest mainstream consumers accept, so the core
never branches on which provider is bound. Where they come from:

| Source | What it establishes |
|---|---|
| Google's published `Schema` message (`google/ai/generativelanguage/v1beta/content.proto`) | the exact field set Gemini's parser accepts |
| Probing the live Gemini endpoint with synthetic schemas and **no credentials** (the JSON→proto parse runs before auth: a clean parse answers 403, a bad one 400 naming the field) | `additionalProperties`, `$ref`, `$defs`, `$schema`, `const`, `exclusiveMinimum`, `multipleOf`, `uniqueItems`, `examples`, `patternProperties`, `readOnly`, `deprecated` and `x-*` are rejected by name; type lists, numeric enum values, tuple/boolean `items` and boolean property schemas are rejected by value |
| Gemini's semantic rejections, as users report them | `items: missing field`, `properties: should be non-empty for OBJECT type` (on a nested object), `enum: only allowed for STRING type`, `required[i]: property is not defined`, `format: only 'enum' and 'date-time' are supported` |
| OpenAI and Anthropic function-calling rules | an object root with no top-level combinator; `items` on every array |
| OpenRouter's request contract (`FunctionDescription`) and Mistral's (its SDK's `Function` model) | `parameters` is required on every function |

Deliberately not encoded:

- **Gemini's rejection of an empty root object.** Gemini's native API wants a tool with no
  arguments to omit `parameters`; OpenRouter and Mistral require it. No single wire form
  satisfies all three, and PersonalClaw reaches Gemini only through OpenAI-compatible layers,
  so an argument-less tool sends `{"type": "object", "properties": {}}`. The owner's failing
  request fits this: its log named only `project_run_create`'s five arrays, while a local
  reproduction of that turn also carries the argument-less `memory_list`. A route found to
  enforce the native rule is fixed in its own adapter.
- OpenAI *strict mode*'s every-property-required and `additionalProperties: false` rules
  (`strict` is never sent), and tool-name rules.

## The wire map

| # | Hop | What happens | Where |
|---|-----|--------------|-------|
| 1 | Built-in declaration | must satisfy the profile **as declared** — never lean on a repair | rail: `tests/test_tool_schema_portability.py` |
| 2 | Registration | an app's tool provider is registered with the app's name, so later lines can name it | `tool_providers/registry.register_provider(…, app=…)`, `providers/registry.ToolTypeHandler` |
| 3 | Assembly — **the tool seam** | every tool, built-in or app, is conformed: a portable tool passes unchanged, a repairable one is repaired, an unrepairable one stays out of the schema **and** the dispatch index. One log line per tool per process names the app, the tool and the defect | `NativeAgentRuntime.start` → `portable_schema.offered_tool_definitions` |
| 4 | Serialization | every function carries a `parameters` schema; a tool that takes no arguments sends the empty object schema (see "not encoded" above). An adapter whose API shapes schemas differently maps it there | `agents/native/tools.tool_definitions_to_openai_schema`; `llm/anthropic._translate_tools` |
| 5 | The catalog | a tool that cannot be offered stays in the Tools page's catalog but is recorded as a load failure, so the page says why the model never sees it | `tool_providers/registry.list_all_tools` |
| 6 | A rejection anyway | the error is mapped back to the tool this request sent (an index is only trusted when the property the error names is one that tool declares), raised as `ToolSchemaRejected` — one sentence naming the tool as PersonalClaw's bug, plus (unless the tool is core-locked) the way to keep going: turn it off and start a new chat or room, since an open session keeps the toolset it started with — and not retried | `portable_schema.tools_named_in_rejection`, `agents/native/runtime.py`, `llm_helpers.humanize_provider_error` |

**What "repairable" means.** A repair never changes what the tool accepts: it drops a keyword
that only narrows or annotates (the handler still enforces its own input), inlines a local
`$ref`, turns a string `const` into a one-value enum, narrows `[T, "null"]` to `T` (the
argument can be omitted instead), or drops a `required` name that is not a property. Anything
that would change the accepted values — an array with no `items`, a free-form object, an
untyped value, a multi-type union — is not repairable, so the tool is left out.

## Free-form values travel as JSON text

A free-form object, a map, or an untyped value has no portable schema. Such a parameter is
declared a `string` whose description says "as JSON text", and the value is decoded on the way
in:

- tools with a `validation.ToolSchema` get it for free: `validate_field` decodes JSON text for a
  `dict`/`list` field (the same tolerance it already applies to a quoted number);
- the rest decode with `validation.decode_json_text`, which passes an already-structured value
  through untouched (a caller that is not a model may still send one).

Built-in parameters carried this way: `workflow_author.root`/`inputs`, `workflow_start.inputs`,
`workflow_edit.ops`, `workflow_resume.answer`, `automation_create.spec`,
`automation_update.patch`, `notify.blocks`, `propose_template_diff.ops`, `prompt_render.vars`,
`sheet_create.sheets`/`rows` (JSON keeps a number a number) and `visualize.data`. Where the
shape IS known it is declared instead (`project_run_create.stage_plan`, `deck_create.slides`,
the task tools' `exit_criteria`/`action_plan`, `reset_tools.groups`).

## For an app author

Write the schema the profile accepts and nothing is repaired or excluded: give every array
`items`, give every object `properties`, use one `type` per node, keep `enum` to strings, and
carry anything free-form as JSON text. The log line (and the Tools page) names exactly what to
change when a schema falls outside it.
