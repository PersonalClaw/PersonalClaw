# Authoring workflow templates

A template is a reusable workflow spec: a tree of typed nodes plus declared
inputs and metadata. This guide is for writing one that works, reads well, and
survives the next person who edits it. The engine itself is documented in
[`docs/architecture/workflows.md`](../architecture/workflows.md).

## The shape of a template

```json
{
  "name": "produce-and-audit",
  "description": "Research a subject, produce an artifact, then audit it against a read-only QC gate.",
  "inputs": {
    "subject": {"type": "string", "required": true, "help": "What to produce."},
    "acceptance": {"type": "string", "default": "", "help": "What 'good' means here."}
  },
  "metadata": {
    "risk": "low",
    "steering_examples": [
      {"event": "kickoff", "description": "Produce a one-page migration plan for …"},
      {"event": "mutation", "description": "The audit found three Major findings — re-run …"}
    ]
  },
  "root": { "kind": "sequence", "id": "…", "children": [ … ] }
}
```

Four things are worth getting right before the graph:

**The description is the picker.** It is the only line a user sees when choosing,
so it has to distinguish this template from its neighbours. Under ~40 characters
and the lint says so.

**Every input needs `help`.** The run dialog builds its fields from these; an
input with no help shows a bare snake_case field name and the user has to read
the spec to learn what it wants.

**A required input has no default.** They contradict each other — a default
means it can be omitted. The lint treats this as an error.

**Steering examples are not decoration.** The widget surfaces them and
`workflow_plan` uses them as few-shot. Two kinds matter: a `kickoff` example
(what driving this looks like) and a `mutation` example (what editing it
mid-flight looks like) — the second is what teaches a model that editing a
*running* workflow is a normal thing to do.

## Choose the cheapest node that does the job

| Need | Use | Not |
|---|---|---|
| a classification, a score, a rewrite | `infer` | `stage` — you would pay for a session and a lane slot |
| real work with tools, files, spawning | `stage` | |
| reshaping data you already have | `transform` | `infer` — it is zero tokens |
| running a command, writing an artifact | `action` | `stage` — a subagent to paste text is pure waste |
| a decision between subgraphs | `branch` | a `stage` that "decides" and then does the work inline |

A five-judge panel built from `infer` is five bounded calls; the same panel built
from `stage` is five concurrent subagent sessions. That difference is the reason
the two kinds exist.

## Action arguments go under `config.with`

```json
{"kind": "action", "id": "baseline",
 "config": {"provider": "bash", "with": {"command": "make test"}}}
```

Not flat beside `provider`. A flat argument reaches the provider as an *empty*
config; it then reports its own required field missing for a value that is
visibly present in the spec, every downstream binding fails, and the run dies
reporting "deadlocked". Validation refuses the shape now so that cannot happen.

## Macros: the patterns, as one-liners

Four ship, and they expand into core nodes **at definition time** — so what is
stored, validated and run is one tree, and you can expand a macro then hand-edit
the result when you outgrow the pattern.

| Macro | Expands to | Use when |
|---|---|---|
| `judge_panel` | `parallel[infer × lenses]` → `transform` | a subject needs independent scoring |
| `verify_panel` | `foreach(pipeline)[infer(refute)]` → `transform` | a finding list needs adversarial checking |
| `route` | `infer(classify)` → `branch` | the work depends on what kind of thing this is |
| `research_sweep` | `parallel[stage × modes]` → `transform` → `foreach[infer]` | one search angle will not find everything |

```json
{"macro": "judge_panel", "id": "review",
 "config": {"subject": "{{inputs.design}}",
            "lenses": [
              {"name": "correctness", "prompt": "What cases does it not handle?"},
              {"name": "feasibility", "prompt": "Which part will blow the estimate?"}
            ]}}
```

Give each lens its own prompt. N identical prompts catch a flaky answer; N
different lenses catch a failure mode the others are blind to — and the latter is
what makes a panel worth its tokens.

`verify_panel` asks the model to **refute**, defaulting to refuted when
uncertain. A verifier asked "is this real?" agrees, because agreeing is the
locally plausible answer.

`route` classifies at the `fast` tier by default: choosing between three paths is
a cheap judgment, and paying reasoning-tier prices to route *into* a
reasoning-tier branch doubles the cost of the decision for nothing.

## Shared blocks: cite, do not restate

```json
{"kind": "infer", "id": "audit",
 "config": {"prompt": "Audit this.\n\nReturn JSON: {\"findings\": [Finding]}.\n\n{{block:finding-record}}"}}
```

Three blocks ship in `bundled/shared/`:

| Block | What it defines |
|---|---|
| `finding-record` | the canonical Finding shape and severity ladder |
| `safety-tiers` | the read-only → additive → reversible → destructive ladder |
| `gap-honesty` | say what you could not establish, do not fill it |

Cite them rather than writing the text again. Six templates once defined the
Finding record three separate times, and copies do not stay identical — a gate
predicate like "no open Critical" stops meaning the same thing once one stage
grades on a different ladder. An unknown block name is an **error**, never a
passthrough: a literal `{{block:…}}` reaching a model is a convention silently
not applied.

## The conventions that keep a library coherent

**Triage first.** Open with an `infer` classification whose output drives a
`branch` between entry subgraphs, so a small task skips the deep path:

```json
{"kind": "infer", "id": "triage",
 "config": {"model_tier": "fast",
            "prompt": "Classify … Return JSON: {\"tier\": \"light|standard|deep\"}",
            "schema": {"tier": "string"}}},
{"kind": "branch", "id": "gather",
 "config": {"on": "{{nodes.triage.output.tier}}", "enum": ["light", "standard", "deep"]},
 "cases": { … }}
```

Declare the `enum`. Validation then catches an uncovered case at save time
instead of raising a binding error mid-run, after the classifier already spent
its tokens.

**Capture a baseline before you mutate.** A code-flavoured template runs its
validation *before* the first mutating node, so a failure afterwards can be told
apart from one that was already there — otherwise someone debugs the wrong
commit.

**Findings use the canonical record.** `{severity, location, problem, why,
recommended_fix, status}`, severities `Critical|Major|Minor|Nit`. `location` must
be specific enough to act on. `why` is the consequence, not a restatement.

**An empty findings list is a valid answer.** Say so in the prompt. Inventing a
finding to look thorough makes gates fire on noise, makes an until-dry loop run
forever, and teaches the reader to ignore the output.

## Long-horizon templates: hand off, do not compact

For a `loop` or `foreach` body that runs many iterations, have the node return a
handoff and let the next iteration start from it:

```json
{
  "handoff": {
    "verified_state": "auth.py:40-88 reviewed, no injection paths",
    "changes": "added a null check at line 52",
    "unverified": "the OAuth path was never reached",
    "next_action": "review handlers.py"
  },
  "carryover": {"files_touched": [{"path": "auth.py", "lines": "40-88"}]},
  "decision": {"choice": "reuse the existing store",
               "rejected_alternatives": ["a new sqlite file"]}
}
```

`session: fresh` (the default) prepends these to the next iteration's prompt.
`session: continuous` injects nothing, because a continuous session already
holds the previous iteration in its transcript.

Return them only when you have something to say. A fabricated handoff is worse
than none — the next iteration would trust it.

## Concurrency

`max_concurrency` on a `foreach` caps how many *items* are in flight. Set it when
each item holds something scarce (a checkout, a lock, a rate-limited endpoint);
leave it unset for a handful of cheap items. It must be a whole number — `1.5`
and `true` are rejected rather than coerced, because a coerced `1` would silently
serialize the fan-out and the run would still succeed.

`pipeline: true` is accepted and documented, but the engine already streams:
each item's body is an independent subtree, so an item advances as soon as its
own previous stage finishes.

## Nesting

`subworkflow` runs another workflow as a real child run — its own journal, state
and history, so it can be rewound and inspected on its own:

```json
{"kind": "subworkflow", "id": "nested",
 "config": {"ref": "child-workflow", "inputs": {"payload": "{{nodes.prep.output}}"}}}
```

Inputs are resolved against the *parent* before the child is created, because
the child cannot interpret `{{nodes.…}}` from a graph it is not part of. Depth is
capped at 3, and a workflow that references itself is refused before anything is
created. Bind to the result via `{{nodes.nested.output.status}}` and
`{{nodes.nested.output.outputs}}`.

## Before you ship it

Validate without saving. Every save path attaches the lint's findings, and
`save: false` is a real dry run — it validates and returns the issues without
writing, so you can iterate before committing anything:

```bash
# HTTP
curl -X POST localhost:10000/api/workflows \
  -H 'content-type: application/json' \
  -d '{"name": "my-template", "root": {…}, "save": false}'
```

From chat, `workflow_author` with `save=false` does the same thing, and
`workflow_plan` with `template: "<name>"` hands you an existing template's
expanded tree to start from.

| Code | Means |
|---|---|
| `WFL_INLINE_CONVENTION` | you restated a shared block; cite it instead |
| `WFL_UNKNOWN_BLOCK` | a `{{block:…}}` names something that does not exist |
| `WFL_REQUIRED_WITH_DEFAULT` | an input is both required and defaulted |
| `WFL_THIN_DESCRIPTION` | the picker cannot distinguish this template |
| `WFL_UNDOCUMENTED_INPUT` | an input has no `help` |
| `WFL_NO_KICKOFF_EXAMPLE` / `WFL_NO_MUTATION_EXAMPLE` | no steering example |

A user's own workflow is only *advised*. The bundled library is held to zero
findings including warnings, because a warning that ships propagates to every
template copied from it.

Then validate strictly (`strict: true` rejects on warnings too) and drive it
once for real. A template that validates and has never run is a template with an
unmeasured prompt.
