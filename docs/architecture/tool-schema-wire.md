# Tool-schema wire fidelity

What happens to a tool's **parameter schema** and **description** between its `ToolDefinition`
and the model provider. The invariant this page (and the rail, `tests/test_tool_schema_portability.py`)
protects: **no model request carries a tool definition a mainstream provider can reject, so one
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
  (`strict` is never sent). Tool-name rules are [tool-name-wire.md](tool-name-wire.md)'s.

## A description is never empty

Converse declares `toolSpec.description` with a minimum length of 1, and the AWS client refuses a
request carrying `""` before anything is sent: one MCP server listing a tool with no description
failed every Bedrock turn. The seam (row 3 below) therefore offers a tool whose source gave no
description, or only whitespace, with one written from what is known
(`portable_schema.derived_description`): the tool's real name, which for an external MCP tool
carries its server's name, that no description came with it, and its declared inputs, each with its
type, whether it is required and the first line of its own description (the first 12 inputs, each
line cut short). It logs once per process, naming the app and the tool. The Tools page shows the
same text, so the page and the model never disagree about what a tool is. Built-in tools describe
themselves as declared (`tests/test_tool_schema_portability.py`).

## The tools a turn defers are listed, the platform's own first

A turn whose tools do not all fit sends the schemas that matter (`ToolRetriever.select`) and lists
the rest in a catalog of at most 6,000 characters, last line included (`ToolRetriever.catalog`,
worded by `CATALOG_NOTE`). Each of PersonalClaw's own groups, the platform's and those of every
provider it ships (`catalog_groups`, `registry.ships_with_core`), comes first: its name, what it
is for in its app's own words, and every tool it has by name. The MCP servers, one group each, and
the installed apps share what is left: first the tools the request is about, then one tool a group
in turn. A last line names what was left out and points at `tool_search`. Where the turn's ranking
ties, the platform's own tool goes first, for the schemas and in `tool_search`'s answer, which is
cut at its limit only after the tools the run is not shown are left out
(`tests/test_the_tool_catalog_names_the_platforms_own_tools.py`).

## The wire map

| # | Hop | What happens | Where |
|---|-----|--------------|-------|
| 1 | Built-in declaration | must satisfy the profile **as declared** — never lean on a repair | rail: `tests/test_tool_schema_portability.py` |
| 2 | Registration | an app's tool provider is registered with the app's name, so later lines can name it | `tool_providers/registry.register_provider(…, app=…)`, `providers/registry.ToolTypeHandler` |
| 3 | Assembly — **the tool seam** | every tool, built-in or app, is conformed: a portable tool passes unchanged, a repairable one is repaired, an undescribed one gets a written description, and one with no name or an unrepairable schema stays out of the schema **and** the dispatch index. Then the name census leaves out a tool whose model-safe name another tool already has ([tool-name-wire.md](tool-name-wire.md)). One log line per tool per process names the app, the tool and the defect | `NativeAgentRuntime._build_catalog` → `portable_schema.offered_tool_definitions`, then `tool_names.build_sanitized_index` |
| 4 | Serialization | every function carries its model-safe name, a non-empty description and a `parameters` schema; a tool that takes no arguments sends the empty object schema (see "not encoded" above). An adapter whose API shapes schemas differently maps it there | `agents/native/tools.tool_definitions_to_openai_schema`; `llm/anthropic._translate_tools` |
| 5 | The catalog | a tool that cannot be offered stays in the Tools page's catalog but is recorded as a load failure, so the page says why the model never sees it; a tool with no description is listed with the one models are shown | `tool_providers/registry.list_all_tools`, `dashboard/handlers/tools.api_tools_list` |
| 6 | A rejection anyway | the error is mapped back to the tool this request sent (an index is only trusted when the property the error names is one that tool declares), raised as `ToolSchemaRejected` — one sentence naming the tool as PersonalClaw's bug, plus (unless the tool is core-locked) the way to keep going: turn it off and send the message again, since an open session rebuilds its toolset at the next turn when the tools change — and not retried | `portable_schema.tools_named_in_rejection`, `agents/native/runtime.py`, `llm_helpers.humanize_provider_error` |

**What "repairable" means.** A repair never changes what the tool accepts: it drops a keyword
that only narrows or annotates (the handler still enforces its own input), inlines a local
`$ref`, turns a string `const` into a one-value enum, narrows `[T, "null"]` to `T` (the
argument can be omitted instead), or drops a `required` name that is not a property. Anything
that would change the accepted values — an array with no `items`, a free-form object, an
untyped value, a multi-type union — is not repairable, so the tool is left out.

## A call is checked against what its tool declares

A call to a tool that asks before it runs is checked before anyone is asked, in one place every
approval passes (`NativeAgentRuntime._preflight`, from `_guard_and_invoke`, past the agent's tool
list, the deny-list, task mode, tool grants and hooks), so no surface (the web chat, a chat channel, the phone, a
subagent's parent) is asked about a call that cannot run. Two checks:

- **The arguments its input schema requires** (`tool_providers/arguments.missing_arguments`). A
  call missing one is answered at once with what is missing and the schema itself, and marked
  `not_run: missing_arguments`: approving it could run nothing, and the identical retry would ask
  again. The check reads the schema the tool DECLARED, never the repaired copy above. It refuses
  only a missing required argument, the one failure every tool that declares the schema refuses: a
  server built on a lax validator takes `"true"` for a boolean, the MCP client turns a number sent
  as text back into a number, and a built-in tool takes an object where it declares JSON text, so
  any other mismatch is the tool's to judge. It runs none of the schema's patterns, and a schema it
  cannot read (or one pointing at a document it will not fetch) refuses nothing.
- **What the tool declares it refuses** (`ToolProvider.preflight`): the refusal its `invoke` would
  give the call whatever anyone answers, from the same check `invoke` runs first, so the two cannot
  drift. The answer is the tool's own error and hint, marked `not_run: refused_by_tool`. The file
  tools declare where a path reaches (`_resolve`, the containment every file tool uses), a write
  with no content, an edit that changes nothing and the pre-edit read gate; the shell declares a
  `{{secret:NAME}}` nothing stored fills, a credential path, a denied pattern, what only the owner
  may change and a system-scheduler write; each in-process tool module declares a tool its leaf may
  not call and the arguments its schema refuses (`mcp_shared.preflight_refusal`), and `subagent_run`
  adds what it refuses before starting anything: no task, an `agents` list that does not match its
  tasks, and a batch the compiler refuses, in the compiler's findings
  (`mcp_subagents._spawn_refusal`, calling `batch_compile.compile_batch`); an MCP server's tool
  declares the types its input schema refuses, since that schema is the server's validator
  (`mcp_client.argument_refusal`, from the `mcp-tools` provider): judged with `type` alone (no
  pattern, enum or format, no `anyOf` branch, no closed-object rule), on the arguments as the client
  sends them, which turns a numeric string, or the JSON text of an array, an object or a boolean,
  into that value once where the field takes no string at all; and `notify` and `notify_attachment`
  declare what their send refuses: the gateway is asked with `/api/send-message`'s `dry_run`, which
  answers every refusal as sending would, marked as the check's, and sends nothing, and the file is
  read and checked as the send reads it. A check that cannot be made (the gateway unreachable, a
  check that raises) refuses nothing, and the call is asked about as before. `invoke` still checks
  when it runs, since what it checks may change while an approval waits.

Whatever refuses a call, the model is told why in the refuser's words, and only your Deny is told
as a decline. The runtime's own gates and the checks above answer with their reason. A screen the
chat runner applies to an approval request (a hook, the deny-list, the session's mode, a tool name
it will not record, an unattended run with nobody to ask) refuses with its reason
(`turn_endings.refuse`, which hands the runtime `refuse_tool(request_id, reason, kind=...)`), and
so does an approval that ended unanswered ("no one answered in time", "the turn was stopped"); the
runtime words it as `security.classify_denial` words that kind. A runtime says it can carry a reason (`AgentProvider.carries_refusal_reasons`,
the native runtime's); an agent CLI's permission answer has no room for one, so there such a
refusal is a reject, in the CLI's own words.

## An in-process tool is offered what its validator enforces

PersonalClaw's own in-process tools validate their arguments with field specs
(`validation.validate_tool_args`, from the one dispatch map each tool is in,
`validation.tool_field_schema`). The schema each is offered is generated from the same specs
(`validation.offered_schema`), on both surfaces a model reads: the native loop's catalog
(`InProcessMcpToolProvider.list_tools`) and the MCP server an agent CLI lists
(`mcp_core._aggregated_list_tools`). So a field's maximum length, its allowed values, a number's
bounds, a list's item limits and the fields a call requires are in the schema the model is
offered, and a call over one is refused before anyone is asked, with the validator's own words.
Where the hand-written schema and the spec both say something, the spec's is offered. A
constraint is written only on a field the schema names and only where its declared JSON type is
one the spec checks, so a list declared as JSON text gets no item limit; an empty allowed value
is left out of an enum (leaving the field out says the same); a spec's pattern is not offered,
since its dialect is Python's. `tests/test_a_call_its_tool_will_refuse_asks_nobody.py` holds
every in-process tool to it on both surfaces.

## Free-form values travel as JSON text

A free-form object, a map, or an untyped value has no portable schema. Such a parameter is
declared a `string` whose description says "as JSON text", and the value is decoded on the way
in:

- tools with a `validation.ToolSchema` get it for free: `validate_field` decodes JSON text for a
  `dict`/`list` field (the same tolerance it already applies to a quoted number);
- the rest decode with `validation.decode_json_text`, which passes an already-structured value
  through untouched (a caller that is not a model may still send one).

Built-in parameters carried this way: `workflow_author.root`/`inputs` (and `workflow_check`'s),
`workflow_start.inputs`, `workflow_edit.ops` (and `workflow_edit_preview`'s), `automation_create.spec`,
`automation_update.patch`, `notify.blocks`, `propose_template_diff.ops`, `prompt_render.vars`,
`sheet_create.sheets`/`rows` (JSON keeps a number a number) and `visualize.data`. Where the
shape IS known it is declared instead (`project_run_create.stage_plan`, `deck_create.slides`,
the task tools' `exit_criteria`/`action_plan`, `reset_tools.groups`).

A model sometimes sends even a declared array as the text of its JSON. The task tools and
`project_run_create` read that text as the list, through `tasks.models.decode_list_text`, which
takes what `decode_json_text` decodes only when it is a list: their list arguments, the task
store's list fields behind every task write (`tasks.models.TASK_FIELD_COERCERS`), and the run
planner's lists (`loop.code_classify`). Text that is not a JSON list stays the one item it is,
and each element of the list is checked as an element of a real list is.

A model sends a declared boolean as text just as often, and `bool()` takes `"false"` for yes.
Every boolean a core tool declares is read as the word it spells (`safety_flags.yes_or_no`:
`true`, `yes`, `on`, `1` and `false`, `no`, `off`, `0`, in any case), and
`tests/test_tool_boolean_census.py` pins each one with the module that reads it, so a tool that
starts declaring another fails until it is read the same way. A native tool reads it where it
reads the call: `edit_file.replace_all`, `grep.regex`, `glob` and `grep`'s `ignore_case`,
`knowledge_update.is_pinned` and `is_archived`, `code_map.refresh`, `reset_tools`' groups, an exit
criterion's `met`, a step's `completed`, `task_list_create.repeatable` and
`project_run_create.attended`. A blank, any other word and a number spell neither, and each field
reads that as its safe value (one match replaced, a literal search, no re-index, a group left off,
unmet, unfinished, not repeatable, attended) or, where it has none, refuses the call naming the
field (`ignore_case`, `knowledge_update`'s two switches). An in-process tool's `bool` field spec
refuses anything but a real boolean before the call runs, saying which field, and its handler
reads what passes through `yes_or_no` all the same. Consent (`confirm`, `confirm_cascade`) is the
JSON literal `true` and nothing else.

## For an app author

Write the schema the profile accepts and nothing is repaired or excluded: give every array
`items`, give every object `properties`, use one `type` per node, keep `enum` to strings, and
carry anything free-form as JSON text. The log line (and the Tools page) names exactly what to
change when a schema falls outside it.
