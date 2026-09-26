"""The registry of ``WF_UPPER_SNAKE`` codes — the workflows service-result vocabulary.

**The third vocabulary, and until #3499 the only one without a registry.** This repo has
three error-code vocabularies and the case of a code says which surface you are on:

============================================  ====================  =================
vocabulary                                    registry              rail
============================================  ====================  =================
:data:`personalclaw.errors.ERROR_CODES`       yes                   append-only
:data:`personalclaw.http_errors.HTTP_ERROR_CODES`  yes              append-only + emitter
``WF_UPPER_SNAKE`` (here)                     **this module**       both-directions
============================================  ====================  =================

``http_errors`` already stated the relationship: the WF codes are "a third,
transport-independent vocabulary … translated into [the wire one] by
``workflows/handlers.py``'s ``_STATUS_MAP``; it is not a wire vocabulary and does not
belong here." True — and it left the WF codes with no home at all. A third party authoring
a workflow template is exactly the audience these codes exist for: they hit
``WF_SUPERVISOR_UNKNOWN_CONVERGENCE_FIELD`` or ``WF_UNORDERED_DEP`` with nothing to look
it up in, no statement of meaning beyond whichever message happened to accompany it that
day, and no guarantee the name survives. A code is a *contract identifier* — something a
caller is invited to branch on — and an unregistered one is a contract nobody can depend
on. Before this module, exactly ONE of them was documented anywhere in the repository
(``WF_MISSING_EXPR``, in ``docs/architecture/workflows.md``).

The vocabulary is **162 codes across ten modules**. #3499 measured 159; ``WF_INPUT_BAD_LOOP_FIELD``,
``WF_INPUT_DUPLICATE_LOOP_FIELD`` and ``WF_LOOP_KIND_NO_TASK_INPUT`` arrived from PP-16 on ``main``
while this registry was being written, and the rail below is what caught their absence.

**Every meaning below is derived from the code that raises it** — the guard that fires plus
the message it emits — never from the name. A registry of plausible-sounding guesses would
be worse than none, because it reads as authoritative: an author would believe a meaning
the engine never implemented. Where a raise site could not be read, the row's meaning is
left EMPTY rather than invented; the rail permits that and the emptiness is the honest
signal. (As shipped, every row is derived; none is empty.)

**The rail is BOTH directions** (``tests/test_wf_error_codes_registry.py``):

1. every code a core module raises has a row here, and
2. every row here is still raised by a core module.

Direction 2 is the half that usually gets skipped, and it is what keeps the registry from
rotting into a list of codes that no longer exist. It is also why this module is EXCLUDED
from the scan that answers it: these 162 keys are string literals in core, so counting them
as raise sites would make direction 2 true by construction — a rail that proves itself.

**Not a message fallback, unlike HTTP_ERROR_CODES.** ``json_error`` has one emitter, so its
registry row can double as the default response text. The WF codes have many emitters
(``workflows/service.py``'s ``_service_failure``, ``validator.py``'s ``_add``,
``mutations.py``'s ``Issue``, ``preflight.py``'s ``Finding``, ``controller.py``'s inline
result dicts, ``mcp_*.py``'s ``tool_failure``) and every one already carries a
per-instance message naming the offending node, key or run. So the meaning here is the
*stable contract* — what the code will always mean — and the message stays the concrete
detail. Rewording a meaning is allowed where rewording a wire code is not: the meaning
describes the code, it is not itself a shipped string a client reads.

**Severity is not in this table, deliberately.** The same code can arrive as an error or a
warning depending on where it fires (``validator.py``'s ``_add`` takes a severity), and
``preflight.py``'s ``WF_PRE_*`` findings are a mix of blocking gaps and honest
"could-not-check" admissions. Folding severity in would make the registry claim something
the emitters decide per call.
"""

from __future__ import annotations

# ── The WF code registry ───────────────────────────────────────────────────
#
# code → one-line meaning, derived from the raise site. Grouped by the module that owns
# the failure, because that is where the derivation can be re-checked.
#
# ADD a row in the same change that ships a new code; the rail fails otherwise. REMOVE a
# row when its last raise site goes, for the same reason — a code nothing raises is a
# contract that silently stopped existing, and that is what direction 2 catches.
WF_ERROR_CODES: dict[str, str] = {
    # ── workflows/validator.py — template-author authoring errors ──────────
    # The 65 codes a workflow-template author can hit while validating a spec. Every one
    # is produced by `_add(res, code, message, path, severity)`, so the message quoted in
    # each meaning below is that call's second argument.
    "WF_NOT_AN_OBJECT": "The spec is not a JSON object, so nothing about it can be validated.",
    "WF_BAD_NAME": (
        "The spec's `name` is not lowercase alphanumeric with hyphens, 1-63 characters."
    ),
    "WF_MISSING_ROOT": "The spec has no `root` node, so there is no tree to run.",
    "WF_UNKNOWN_NODE_KIND": (
        "A node declares a `kind` the node model does not know, so the tree cannot be built."
    ),
    "WF_BAD_BINDING": (
        "A binding expression in the spec could not be parsed while building the node tree."
    ),
    "WF_SPEC_TOO_LARGE": "The spec declares more nodes than the per-spec node cap allows.",
    "WF_SPEC_TOO_DEEP": "The spec's node nesting is deeper than the per-spec depth cap allows.",
    "WF_DUPLICATE_NODE_ID": (
        "Two nodes in the spec share one node id; the message names where it was first used."
    ),
    "WF_EMPTY_CONTAINER": "A container node (sequence/parallel/…) has no children.",
    "WF_BAD_JOIN": "A parallel node declares a `join` mode that is not one the engine has.",
    "WF_BAD_QUORUM": (
        "A parallel node's `quorum` is not an integer within 1..<number of children>."
    ),
    "WF_MISSING_BODY": "A foreach or loop node has no `body` to execute per iteration.",
    "WF_MISSING_ITEMS": "A foreach node has no `items` binding, so there is nothing to iterate.",
    "WF_BAD_ITEM_ERROR": (
        "A foreach node's `on_item_error` is not one of the per-item failure policies."
    ),
    "WF_BAD_LOOP_MODE": "A loop node declares a `mode` the engine does not implement.",
    "WF_BAD_LOOP_COUNT": "A counted loop's `n` is absent or not a positive integer.",
    "WF_MISSING_CONDITION": "An `until` loop has no `condition`, so it has no exit test.",
    "WF_BAD_STREAK": "An `until_dry` loop's `streak` is absent or not a positive integer.",
    "WF_UNREAPABLE_WATCHER": (
        "An `until_cancelled` loop has neither a `join: any`/`quorum` parallel sibling that "
        "can reap it nor a `max_iterations` cap, so the run would never end."
    ),
    "WF_WATCHER_NO_WAIT": (
        "An `until_cancelled` body contains no `wait`, so it would cycle as fast as the model "
        "answers and exhaust the run budget."
    ),
    "WF_MISSING_ON": "A branch node has no `on` binding, so there is no value to switch on.",
    "WF_EMPTY_BRANCH": "A branch node declares no cases.",
    "WF_BRANCH_COVERAGE": (
        "A branch's `on` value can take a case the spec does not handle and there is no "
        "`default`, so that value would fall through unhandled."
    ),
    "WF_MISSING_PROMPT": (
        "A node that drives a model — or a judge gate — has no `prompt`, so there is nothing "
        "to send."
    ),
    "WF_BAD_MODEL_TIER": "A node's `model_tier` is not one of reasoning|standard|fast.",
    "WF_MISSING_EXPR": (
        "A transform node has no `expr` binding (and no `skeleton` artifact to render), or an "
        "expression gate has no `expr`."
    ),
    "WF_MISSING_DATA": "A visualize node has no `data` binding to chart.",
    "WF_MISSING_PROVIDER": "An action node names no `provider` to dispatch to.",
    "WF_ACTION_ARGS_NOT_NESTED": (
        "An action node writes its arguments FLAT beside `provider` instead of under "
        "`config.with`, where the engine reads them; the message names the keys to move. The "
        "run would reach the provider with an empty config and the provider would report its "
        "own field missing for a value visibly present in the spec."
    ),
    "WF_ACTION_NO_ARGS": (
        "An action node declares no `config.with` at all — legitimate for a provider that "
        "needs no arguments, so this is a warning rather than a refusal."
    ),
    "WF_BAD_SEAL": (
        "A wait node's `seal` is not an object, or a buffer seal has neither a positive "
        "`threshold` (items) nor `tokens`."
    ),
    "WF_SEAL_NO_FLUSH": (
        "A buffer seal has no `flush_stale_after_secs`, so a trickle of items would never "
        "reach the threshold and never synthesize."
    ),
    "WF_MISSING_WAIT": "A wait node declares none of `duration_secs`, `until_ts` or `seal`.",
    "WF_BAD_GATE_KIND": "A gate node declares a `kind` the gate vocabulary does not contain.",
    "WF_MISSING_VERIFY": "A gate that verifies something has no `verify` block.",
    "WF_MISSING_CRITERIA": "A ladder gate has an absent or empty `criteria` list.",
    "WF_MISSING_REF": "A subworkflow node has no `ref` naming the definition to run.",
    "WF_BAD_REF": "A subworkflow node's `ref` is not a valid workflow definition name.",
    "WF_SUPERVISOR_NOT_OBJECT": "The spec's `supervisor` is present but not an object.",
    "WF_SUPERVISOR_UNKNOWN_FIELD": (
        "`supervisor` carries a field that is not in the closed supervisor field set."
    ),
    "WF_SUPERVISOR_BAD_TIER": (
        "`supervisor.judge_model_tier` is not one of reasoning|standard|fast."
    ),
    "WF_SUPERVISOR_BAD_RUNG": (
        "`supervisor` names an escalation rung that is not in the escalation ladder."
    ),
    "WF_SUPERVISOR_BAD_FAILURE_CLASS": (
        "`supervisor` names a failure class the supervisor policy does not define."
    ),
    "WF_SUPERVISOR_BAD_HITL": "`supervisor.hitl_posture` is not one of afk|hitl.",
    "WF_SUPERVISOR_CONVERGENCE_NOT_OBJECT": (
        "`supervisor.convergence` is present but not an object."
    ),
    "WF_SUPERVISOR_UNKNOWN_CONVERGENCE_FIELD": (
        "`supervisor.convergence` carries a field that is not in the closed convergence "
        "field set."
    ),
    "WF_SUPERVISOR_BAD_DONE_SIGNAL": (
        "`supervisor.convergence`'s done signal is not one of the recognised signals."
    ),
    "WF_UNKNOWN_PIPE": "A binding applies a pipe name the template language does not define.",
    "WF_BAD_PIPE": (
        "A binding calls a known pipe in a way resolution can never evaluate — not "
        "`name(...)` syntax, an argument that is not a literal (quoted string, number, "
        "true/false, null), or more arguments than the pipe takes — so every resolution of it "
        "fails."
    ),
    "WF_HANDROLLED_FENCE": (
        "A binding writes the `<untrusted_content>` fence as literal text. A hand-written "
        "fence neutralises neither an embedded close marker nor a chat-template role token — "
        "the value must go through `| fenced(...)` or `| fenced_sources` instead."
    ),
    "WF_UNFENCED_UNTRUSTED": (
        "Untrusted input flows into a node unsanitized: no `fenced`/`fenced_sources`/"
        "`xml_escape`/`truncate`/`json` pipe stands between the source and the consumer."
    ),
    "WF_UNRESOLVED_BLOCK": (
        "A shared-block reference was not substituted — the block does not exist, or this "
        "spec bypassed the authoring path that resolves blocks."
    ),
    "WF_UNKNOWN_BINDING_ROOT": (
        "A binding's root segment is not one of the known binding sources."
    ),
    "WF_INLINE_SECRET": (
        "A binding's literal value looks like a credential; it must be `{{secret:KEY}}` " "instead."
    ),
    "WF_UNKNOWN_NODE_REF": "A binding references a node id that no node in this spec declares.",
    "WF_UNKNOWN_NEEDS": "A node's `needs` names a node id that no node in this spec declares.",
    "WF_CYCLE": (
        "The dependency graph contains a cycle, so no admission order exists; the message "
        "names the nodes involved."
    ),
    "WF_UNORDERED_DEP": (
        "A node reads `{{nodes.<other>...}}` but nothing guarantees `<other>` has finished "
        "when it is admitted — neither a `needs` edge nor sequence sibling order."
    ),
    "WF_UNSATISFIABLE_NEEDS": (
        "A node declares a `needs` edge that can never be honoured (the named node cannot "
        "have completed by the time this one is admitted)."
    ),
    "WF_REDUNDANT_NEEDS": (
        "A node declares `needs` on a node it also binds `{{nodes.<id>...}}` from — the "
        "binding already orders it. Kept as a distinct code because `needs` is still the "
        "right tool for ordering that is not dataflow."
    ),
    "WF_UNSATISFIABLE_OUTPUT_REF": (
        "A node reads `{{nodes.<id>.output.<key>}}` for a key the producer's "
        "`output_contract.required_keys` does not guarantee, so the binding cannot resolve."
    ),
    "WF_UNCONTRACTED_OUTPUT_REF": (
        "A node whose output is read declares no `output_contract`, so nothing checks the "
        "paths read from it."
    ),
    "WF_WIP_CONTRADICTION": (
        "`single_active_feature` declares WIP=1 while a foreach declares a higher "
        "`max_concurrency`; the engine runs one item at a time, so one of the two "
        "declarations is false."
    ),
    "WF_INPUT_BAD_LOOP_FIELD": (
        "An input declaration's `loop_field` marker is not one of the closed "
        "`loop_aliases.LOOP_INTAKE_FIELDS` (`task`|`success_criteria`). Closed deliberately, so a "
        "typo surfaces as this named authoring error rather than as a marker that parses to "
        "nothing and silently leaves the input unfilled at launch."
    ),
    "WF_INPUT_DUPLICATE_LOOP_FIELD": (
        "Two input declarations claim the same `loop_field`, and only one input can receive it."
    ),
    # ── workflows/service.py — definition authoring + run lifecycle ────────
    # Emitted by `_service_failure(code, message, **detail)`; the codes handlers.py's
    # `_STATUS_MAP` translates into the wire vocabulary.
    "WF_DEF_NAME_REQUIRED": "The request named no workflow definition.",
    "WF_DEF_NAME_INVALID": (
        "The definition name is not lowercase letters, digits and hyphens (it becomes a "
        "directory name)."
    ),
    "WF_DEF_NAME_RESERVED": (
        "The name belongs to a read-only bundled template. A copy saved under the author's "
        "own name survives upgrades; a shadow of a bundled name would be ignored at run time."
    ),
    "WF_DEF_ROOT_REQUIRED": "The authoring request carried no `root` node object.",
    "WF_DEF_NOT_FOUND": (
        "No workflow definition by that name exists — or, on a write path, none that is "
        "writable."
    ),
    "WF_DEF_MACRO_INVALID": (
        "Macro expansion or shared-block resolution of the submitted spec raised: a macro or "
        "block reference in it could not be expanded."
    ),
    "WF_DEF_INLINE_SECRET": (
        "The submitted spec contains literal credentials. Refused rather than warned: once "
        "saved the value is on disk. Use `{{secret:KEY}}`."
    ),
    "WF_DEF_INVALID": "The submitted spec did not pass validation, so it was not saved.",
    "WF_DEF_NO_WRITABLE_PROVIDER": (
        "No writable workflow-definition provider is registered, so there is nowhere to save."
    ),
    "WF_DEF_SAVE_FAILED": "Writing the definition failed; the message carries the cause.",
    "WF_DEF_DELETE_FAILED": "Deleting the definition failed; the message carries the cause.",
    "WF_LOOP_KIND_UNKNOWN": "No workflow template replaces the named loop kind.",
    "WF_LOOP_KIND_NOT_PORTED": (
        "The named loop kind resolves to a template, but its behaviour has not been ported to "
        "that template yet, so it still runs on the loop path."
    ),
    "WF_LOOP_KIND_NO_TASK_INPUT": (
        "The template a loop kind resolves to declares no input marked `loop_field: task`, so "
        "there is nowhere to put the loop's task. A DIFFERENT fact from "
        "`WF_DEF_NOT_FOUND` — the template is present and is missing the marker — and refused "
        "rather than guessed at, because starting a run whose worker was handed nothing is "
        "harder to debug than not starting one and saying why."
    ),
    "WF_RUN_MISSING_INPUTS": "The run request omits inputs the definition declares as required.",
    "WF_RUN_INPUT_TYPE": "One or more supplied inputs do not match their declared type.",
    "WF_RUN_PREFLIGHT_FAILED": (
        "Preflight found a blocking gap, so the run was refused before it started; the "
        "message carries the findings."
    ),
    "WF_NO_SUPERVISOR": (
        "The workflow supervisor is unavailable. On start the run is created but not started; "
        "on a draft launch it cannot start at all."
    ),
    "WF_RUN_LAUNCH_FAILED": "Starting the run raised; the message carries the cause.",
    "WF_RUN_NOT_FOUND": "No run with that id exists.",
    "WF_RUN_NO_SPEC": "The run exists but its stored spec cannot be read.",
    "WF_RUN_BAD_SPEC": "The run's stored spec was read but does not parse.",
    "WF_NODE_NOT_FOUND": "The named node id is not in this run's spec.",
    "WF_NODE_NOT_RUN": "The named node exists but has produced no output yet.",
    "WF_NODE_NOT_TERMINAL": (
        "The named node has not reached a terminal state, so there is nothing to reconstruct "
        "from it yet."
    ),
    "WF_POLICY_KEY_UNKNOWN": (
        "A policy-override key is not in the overridable set; the message names both."
    ),
    "WF_RUN_NOT_PRELAUNCH": (
        "The run has already launched, so the operation is refused: its policy overlay is "
        "frozen (the engine's own saves would revert a live edit), and there is nothing left "
        "to start."
    ),
    "WF_RUN_ALREADY_TERMINAL": (
        "The run has already finished, so it cannot be cancelled, paused, steered or resumed."
    ),
    "WF_RUN_NOT_TERMINAL": (
        "The run is still live, so it cannot be deleted; cancel it and delete once it reports "
        "a terminal status."
    ),
    "WF_RUN_DELETE_REFUSED": (
        "The resolved run path lies outside the runs root, so the delete was refused."
    ),
    "WF_RUN_NOT_LIVE": (
        "The run has no live controller, so there is nothing to apply the request to — it has "
        "not started, or it is no longer running."
    ),
    "WF_STEER_EMPTY": "A steering instruction carried no text.",
    "WF_NO_PENDING_GATE": "The run has no gate awaiting an answer.",
    "WF_AMBIGUOUS_GATE": (
        "More than one gate is awaiting an answer, so a bare answer cannot be routed; answer "
        "one by its resume token from the `pending` list."
    ),
    "WF_CONFIRM_VERB_INVALID": (
        "The confirmation verb is not one the resolution vocabulary admits. Refused rather "
        "than treated as a reject, because a typo silently declining an approval would "
        "decline work the user meant to allow."
    ),
    "WF_FORK_FAILED": "Forking the run raised; the message carries the cause.",
    "WF_WORKSPACE_UNREADABLE": "The run's workspace could not be read.",
    "WF_DROP_DISABLED": (
        "This run does not accept dropped files; the message carries the policy's own reason."
    ),
    "WF_DROP_APPROVAL_REQUIRED": (
        "The dropped file needs explicit approval before it is accepted (its type or the "
        "run's policy requires it); the pending detail names the file."
    ),
    "WF_DROP_LIMIT": "The run already holds the maximum number of dropped files.",
    "WF_DROP_WRITE_FAILED": "Storing the dropped file failed; the message carries the cause.",
    # ── workflows/mutations.py — mid-flight spec edits ─────────────────────
    # Emitted as `Issue(code=..., message=...)` from op parsing and application.
    "WF_MUT_UNKNOWN_OP": "A mutation op could not be parsed into a typed op.",
    "WF_MUT_NO_OPS": "The mutation request carried no ops.",
    "WF_MUT_MIXED_ADDRESSING": (
        "One batch mixes `node_id` addressing with `parent_id`+index addressing. One scheme "
        "per batch, so indices cannot shift under an anchor."
    ),
    "WF_MUT_OVERLAPPING_EDITS": (
        "Two or more ops in one batch target the same node; combine them into one."
    ),
    "WF_MUT_NO_TARGET": "An op that edits a node names no `node_id`.",
    "WF_MUT_EMPTY_OVERRIDES": "A `set_input` op carries no overrides.",
    "WF_MUT_INSERT_NO_NODE": "An `insert` op carries no `node` payload.",
    "WF_MUT_INSERT_BAD_NODE": "An `insert` op's `node` payload is not a valid node spec.",
    "WF_MUT_EMPTY_UPDATE": "An `update_node` op names no fields to change.",
    "WF_MUT_FROZEN_NODE": (
        "The target node's state does not permit editing; rewind it first to change it."
    ),
    "WF_MUT_MOVE_NO_PARENT": "A `move` op names no new parent.",
    "WF_MUT_MOVE_INTO_SELF": (
        "A `move` op would place a node inside its own subtree, detaching the graph."
    ),
    "WF_MUT_NO_ROOT": "The spec being mutated has no root node.",
    "WF_MUT_BAD_SPEC": "The spec being mutated could not be read; the message carries why.",
    "WF_MUT_UNKNOWN_NODE": "The op's target node is not in the spec being mutated.",
    "WF_MUT_UNKNOWN_PARENT": "The op's target parent node is not in the spec being mutated.",
    "WF_MUT_BAD_CONFIG": "The target node's `config` is not an object, so it cannot be patched.",
    "WF_MUT_IMMUTABLE_FIELD": (
        "The op would change a field that is the node's identity on a live node, which is not "
        "permitted."
    ),
    "WF_MUT_NOT_A_CONTAINER": "The op would give children to a node kind that cannot hold them.",
    "WF_MUT_DELETE_FAILED": "Removing the node from the tree failed.",
    "WF_MUT_MOVE_FAILED": "Detaching the node from its current parent failed.",
    "WF_MUT_UNSUPPORTED": "The op names a mutation the engine does not implement yet.",
    "WF_MUT_INVALID_RESULT": (
        "Applying the batch would produce a spec that does not validate. The fallback code "
        "for such a failure: the validator's own code is used when it has one, and this "
        "stands in when it does not."
    ),
    "WF_MUT_VERSION_MISMATCH": (
        "The submitted `expect_version` does not match the run's current spec version — "
        "another edit landed first, so refetch and reapply."
    ),
    "WF_MUT_CONFIRM_REQUIRED": (
        "The batch would re-run already-completed nodes, so it needs an explicit "
        "`confirm_cascade`; the message names the nodes that would re-run."
    ),
    "WF_MUT_UNKNOWN_CHECKPOINT": (
        "A fork op names a checkpoint the run does not have; journaled as a rejected mutation."
    ),
    # ── workflows/controller.py — gate resume, revise, replan ──────────────
    # Returned as inline result dicts from `resume`/`_resume_revise`/`_converge_loop`.
    "WF_RESUME_NOT_OWNER": (
        "The responder or channel is not permitted to answer this gate. Checked before the "
        "token is touched, and deliberately terse — echoing the gate's content to a shared "
        "channel would leak it to everyone in it."
    ),
    "WF_RESUME_UNKNOWN_TOKEN": "No continuation exists for that resume token on this run.",
    "WF_RESUME_EXPIRED": (
        "The continuation behind the resume token has expired; the token is consumed and the "
        "ask is re-published."
    ),
    "WF_RESUME_INVALID_ANSWER": (
        "The answer failed the ask's own validation. Checked BEFORE the token is consumed, so "
        "the token survives and the user can correct it."
    ),
    "WF_RESUME_ALREADY_USED": (
        "Another resume claimed the continuation first; exactly one answer applies to a gate."
    ),
    "WF_RESUME_STALE_EPOCH": (
        "The node was rewound while the token was outstanding, so applying the answer would "
        "land it in the wrong epoch."
    ),
    "WF_REVISE_NOT_ALLOWED": (
        "This run's state or policy does not permit a `revise` answer; the message carries why."
    ),
    "WF_REVISE_NO_SPEC": "The run has no readable spec to revise.",
    "WF_REVISE_NO_STEP_REF": "A `revise` answer did not name the step to change (`step_ref`).",
    "WF_REVISE_UNKNOWN_STEP": (
        "A `revise` answer's `step_ref` names no step in this run; the message lists the "
        "steps that exist."
    ),
    "WF_REVISE_AMBIGUOUS_STEP": (
        "A `revise` answer's `step_ref` matches more than one step; it must name exactly one."
    ),
    "WF_REVISE_NO_COMMENT": "A `revise` answer carried no `comment` saying what to change.",
    "WF_REVISE_NOT_APPLICABLE": "The named step's kind cannot carry a revision comment.",
    "WF_REVISE_REJECTED": (
        "The revision patch did not apply to the spec; the message carries the rejections."
    ),
    "WF_REPLAN_NO_TARGET": (
        "Convergence decided to replan but produced no mutation ops, so there is no replan to "
        "queue and the loop is surfaced instead of retried."
    ),
    # ── workflows/preflight.py — pre-launch readiness findings ─────────────
    # Emitted as `Finding(code=..., detail=...)`. Two classes, deliberately distinct: a
    # `_MISSING`/`_UNRESOLVED`/`_UNKNOWN` names a real gap, while an `_UNVERIFIABLE`/
    # `_UNCHECKED` admits preflight could not establish the fact at all. Collapsing them
    # would let "we did not look" read as "we looked and it was fine".
    "WF_PRE_CREDENTIAL_MISSING": "A credential the spec requires is not set.",
    "WF_PRE_CREDENTIAL_REFUSED": (
        "The spec names a key a provider's or an app's own setting keeps its secret under, which "
        "no workflow can read by name."
    ),
    "WF_PRE_CREDENTIALS_UNVERIFIABLE": (
        "Preflight could not check the required credentials because the credential store is "
        "unavailable — not a claim that they are absent."
    ),
    "WF_PRE_BINARY_MISSING": "A binary the spec requires is not on PATH.",
    "WF_PRE_MODEL_UNRESOLVED": "No model resolves for a use case the spec needs.",
    "WF_PRE_MODELS_UNVERIFIABLE": (
        "Preflight could not check model availability at all — not a claim that a model is "
        "missing."
    ),
    "WF_PRE_PROVIDER_UNKNOWN": "An action provider the spec names is not registered.",
    "WF_PRE_PROVIDERS_UNVERIFIABLE": (
        "Preflight could not check action providers at all — not a claim that one is missing."
    ),
    "WF_PRE_PROVIDER_BOUND_UNCHECKED": (
        "An action provider is named by a binding, so which provider runs resolves at "
        "dispatch and preflight cannot check it in advance."
    ),
    "WF_PRE_PROVIDER_REQUIREMENTS_UNCHECKED": (
        "Preflight did not check what the named action providers themselves require, because "
        "the action-provider contract declares no requirements."
    ),
    # ── workflows/review_service.py — workspace review triage ─────────────
    "WF_TRIAGE_BAD_DECISIONS": (
        "The triage request's decisions payload could not be parsed; the message carries the "
        "problem."
    ),
    # ── mcp_workflows.py / mcp_core.py — the MCP tool surface ─────────────
    # Emitted as `tool_failure(message, code=...)`, i.e. argument-shape refusals raised
    # before any service call, plus the render-time fallback.
    "WF_NO_NODE_IDS": "The tool call's `node_ids` argument is absent or an empty array.",
    "WF_REFINE_NO_OPS": (
        "The refine tool call's `ops` argument is absent or not a non-empty array of typed ops."
    ),
    "WF_PLAN_GOAL_REQUIRED": "The plan tool call carried no `goal`.",
    "WF_PLAN_SESSION_NOT_MINEABLE": (
        "The named session has no transcript with usable turns to mine a goal from, so `goal` "
        "must be passed explicitly."
    ),
    "WF_PLAN_TEMPLATE_NOT_FOUND": (
        "The plan tool call names a workflow definition that does not exist; the message "
        "lists the available ones."
    ),
    "WF_ERROR": (
        "The fallback code when a service result reports failure but carries no code of its "
        "own, so the model always has something to branch on rather than parsing prose."
    ),
}
