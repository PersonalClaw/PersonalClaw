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

**Say where the job goes.** Workflows › **Start from template** asks what you want to do,
picks a template for it, and opens that template's run form with your sentence already in the
input marked `"loop_field": "task"` — or, when no input carries the marker, in the first required
text input. Mark the input a plain-language request belongs in, so it lands there.

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
The keys the engine reads on any step (`on_error`, `retry`, `success_when` and the
like) are not arguments, and stay beside `provider`.

**A step's `payload` carries its own inputs, never whose work it is.** Each key
reaches the provider as written (a `bash` step reads them as its environment), and
the engine adds the step's identity from the run that executes it: `run_id`,
`node_id`, `instance_path`, `project_id`, `workspace` and `idempotency_key`.
Providers attribute and confine their work by those (`artifact_inspect` reads
only its own run's artifacts), so a template cannot set them: saving one that
writes any of them is refused with `WF_PAYLOAD_RUN_IDENTITY`, naming the key, and
so is starting a run of one that reached you another way.

**A step's arguments don't name whose work it is either.** An automation's action can
name the project its run belongs to, the session its agent works in, or the folders
and session of a second opinion; a workflow step's `with` can't, because for a step
each is its own run's. A `run-workflow` step starts its run in the step's own
project, a `run-prompt` step's agent answers to no chat, and a `second-opinion`
step works and writes its brief in its run's folder. So `run-workflow`'s
`project_id`, `run-prompt`'s `session` and `second-opinion`'s `session_key`,
`workspace` and `brief_dir` are refused in a step with `WF_ARGUMENT_RUN_IDENTITY`,
naming the key, when you save, check or start the template, and at the step when
another step's output hands them in.

A step that runs PersonalClaw itself says `personalclaw <command>`, never
`python3 -m personalclaw…`. In a `bash` step that name is this install's own
program, wherever it is installed, while the `python3` on `PATH` has no PersonalClaw
in a `uv tool` install or the desktop app, so a step written that way fails there on
its first run.

**A command that fails says why in its answer.** Print one JSON object whose `error`
is the reason, and exit non-zero: that reason is the step's failure on the run page
and in the run's ending. A command that says nothing is named by its exit status.

**A step whose failure must stop the run says so.** Steps after a failed step run by
default (`on_error: null_continue`). A step whose failure means nothing after it may
run, such as a preflight that refuses its inputs, declares `"on_error": "fail_run"`:
the run then ends there, failed, with the step's reason ("“Check the inputs” failed:
…, so nothing after it ran"). A step a safety control refused before it ran (the
action denylist, an app's limits on a run that is its work) stops the run the same
way, whatever it declares. `null_continue` and `fail_run` are the only two values, and
validation refuses any other.

## Reading what a container produced

`{{nodes.<id>.output}}` reads what a step recorded, and a container records only
what its kind gives it:

| Kind | Its output |
|---|---|
| `loop` | its last cycle's output, as `{{last.output}}` reads a cycle, once it ends done; nothing when it is handed to you instead, and a step reading it is then skipped |
| `branch` | `{"case": "<the case it took>", "produced": <what that case produced>}`, with `produced` once that case has ended in success, read as `{{last.output}}` reads a cycle; a step reading it is skipped when the case failed |
| `sequence`, `parallel`, `foreach` | nothing: validation refuses the read when you save (`WF_UNSATISFIABLE_OUTPUT_REF`), so read the step inside whose output you need |

A loop's output is its last cycle's, so a loop that should hand on a running account
has each cycle return one, built on `{{last.output.…}}`.

Read a branch's result as `{{nodes.<branch>.output.produced}}`, or a field of it
(`{{nodes.<branch>.output.produced.angles}}`), never by the case's own step id: a step
that reads a case that was not taken is skipped. Validation refuses a field a branch never
has, such as `{{nodes.<branch>.output.angles}}`, and names the read that works. Under
`produced` it refuses a field that a case the branch can take does not produce, wherever
that case's output is known when you save (a transform's object, a stage with no schema,
which answers `{"result": …}`), because the read fails every time that case runs.

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
its tokens. The step after the branch reads what the chosen entry found as
`{{nodes.gather.output.produced}}`, whichever case it was.

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

## A document the steps keep: `{{run.document}}`

When the steps of a run carry state in a file from one round to the next (a
report that grows, the sources already read), hand them `{{run.document}}`: the
absolute path of the run's declared document (its kind's, or the template's own
top-level `"document"`) in the run's own documents folder. Never a bare file
name. A project-less run's steps work in the shared workspace every session
defaults to, so a relative `NOTES.md` is one file that every run of the template
reads and rewrites, and a step told only a name searches the disk for it. The
judge, the judge's `artifact_exists` check and the Document panel read the same
path, and the steps' file tools reach that folder and no other run's.

A run starts with no document unless it continues another. An input named
`continue_from` names the run whose document this one starts from a copy of;
declare it, and let a prompt read it so the step knows where it started. A fork
says it continues its parent there on its own, and a start that names a run it
cannot continue is refused, as is a subworkflow node that does. It is read once,
when the run starts, so an edit of a run that would change it is refused too.
`deep-research` is the worked example.

## A search held to a budget: `optimize-harness`

The bundled `optimize-harness` template is the worked example of a loop that spends
money on purpose. It proposes an edit to one of your templates, measures it, keeps it
only if it is better, and files the best as a proposal in **Proposals** for you to
review. It installs nothing.

- **Its budget is what its model calls cost.** `budget_usd` is required. Before every
  cycle, and before every scoring, the search reads what the run's own model calls have
  cost so far (the usage rows booked to the run) and stops when the next cycle or
  scoring would pass the budget. It ends with a sentence saying so and what it kept:
  "The search stopped at its budget: it has spent $0.80 of its $1.00 budget, and a
  cycle of the search has cost up to $0.40, so another would pass it. It kept candidate
  2, which scored 0.67." A model with no price stops it too, because spend it cannot
  price is spend it cannot hold: give the model a price in Settings → Usage → Model
  prices, $0 for one that costs nothing.
- **A score is measured, never claimed.** The scoring step runs the candidate against
  the target's own runs that finished, the newest five: the candidate's prompts are
  answered for each run's recorded inputs and the eval judge decides each answer. A
  case whose answer or verdict could not be had is left out rather than scored 0, and
  a score rests on at least three judged runs. Nothing the proposer says about its own
  candidate is read as a score.
- **A candidate is kept only when it beats the best so far.** It must clear
  `suite_threshold` and score above the best so far, which rises with every candidate
  kept, so a later, worse candidate never replaces a better one.
- **It halts on what it was told.** `max_iterations` (12 unless you say, up to 50),
  `no_improvement_halt` (that many candidates without a better score) and
  `hypothesis_abandon_after` (the same fix tried that many times without being kept),
  besides the budget.
- **It refuses before it spends.** The preflight refuses a run with no budget, a
  `max_iterations` above 50, a sandbox inside the template it is improving, and a
  target with fewer than three finished runs to score against, saying which.

The scoring step is an action that runs in the gateway (`optimize-score`) rather
than a command in a `bash` step, and that is the general rule for any step whose
spend a template must hold to a budget: a model step's calls, and those of an action
that runs in the gateway, are booked to the run and shown on its page, while a model
call made by a command that a `bash` step runs is not.

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

From chat, `workflow_check` does the same thing: it takes the spec
`workflow_author` saves and writes nothing, so it only reads and never asks for
approval. `workflow_plan` with `template: "<name>"` hands you an existing
template's expanded tree to start from.

In the dashboard, open the definition (**Workflows → Definitions**) and choose
**Edit**, or **Edit a copy** for a shipped template, which is read-only and so is
saved as a copy under a name of yours. The editor's **Steps** view lists every step
in the order it runs and edits the settings each one has, bindings included, and
the declared run inputs; its **JSON** tab holds the whole definition, for adding,
removing or moving a step. **Check** is the same `save: false` dry run, and each
issue it returns is shown at the step it names, in the engine's own words. Every
save is a new version, and a version's **Restore** opens the editor on it.

A read of a definition never shows a value held under a name that looks like a
credential — `api_key`, but also `max_tokens` or an `authors` field — and sends a
`_has_<key>: true` flag in its place. Send the flag back as it is: the save
restores the value from the definition the edit came from (`based_on`, or
`based_on_version` for a restore), and refuses the save with
`WF_HIDDEN_VALUE_UNMATCHED`, at that step, if there is nothing to restore it from.

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
