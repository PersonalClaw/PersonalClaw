# PersonalClaw: shared contracts

This document defines, once, every entity, field, state, event, route and rule that more than one of this
release's themes uses, so the theme designs and the tasks filed from them cannot drift apart. A task cites
it by section number (§0.3). Each section is marked `Status: planned` until the change that builds it turns
it into as-built text, and the code on `main` is the as-built authority wherever the two differ (§0.2).
Paths are core-relative unless they start with `apps:`; a cited line number is where the line was when the
section was written, and the code it names may have moved since.

---

## 0. How to use this document

### 0.1 What it fixes, and what it leaves to the theme designs

A concept is here when two or more themes read or write it. For each, this document fixes the data shape
(every field, its type, whether it is required), where it is stored
and its durability entry, how it is read and written (routes, socket frames, functions), its states and
transitions, its invariants and the rail that holds each one, the code it extends or replaces, and what
it must not do. A theme design may add fields, states or routes that only that theme reads; it may not
rename, retype or reinterpret anything defined here.

What stays with a theme's design: algorithms, module layout below the named entry points, UI
layout and copy (other than the words this document fixes because several surfaces say them), test
lists beyond the rails named here, and the order of its tasks.

### 0.2 Precedence and amendments

1. **Code on `main` is the as-built authority.** Where this document describes today's code and
   the code says otherwise when a design is written, the code wins and the design records the difference
   as an amendment (point 3).
2. **This document outranks a theme design, and a design outranks a task.** A task body that disagrees
   with a design, or a design that disagrees with this document, is wrong until the higher document
   changes first.
3. **An amendment is proposed, never assumed.** A design that needs a shared contract changed lists the
   change under its heading "Contract changes requested" (the section number, the old text, the new text,
   the reason, and every consuming theme it touches). It is applied here before any task is filed against
   it. A design never redefines a shared field locally "for now".
4. **Earlier planned work is a dependency, not a contract owned here.** Where a contract here extends
   planned work (#3833's phases, #3888's list, #3843's capability status, the mini apps' sealed host), the
   planned issue owns its part, this document owns this release's delta, and §0.5 says what this release
   does if the planned work ships differently or not at all.

### 0.3 How a task cites this document, and what the committed copy drops

A task body has the sections **Why**, **What**, **Done when**, **Out of scope**, **Depends on**, **Code
areas** and **Design**. The **Design** line names this document's sections first and
the theme design's after them:

```
**Design:** shared contracts §3.4 (tool part states), §5.3 (the journal record); the theme design §4.1.
Where this issue and the design disagree, the design wins: change the design first.
```

A task that builds a section turns it into as-built text in the same change, and removes its `Status:
planned` line then. The section numbers never change, so every task's citation stays true; a number with
no section (§0.4) is one this document does not use.

### 0.5 What this release assumes from earlier work

| Planned item | What this release takes from it | If it ships differently or not at all |
|---|---|---|
| #3833 one phase contract | the phase list, the phase module, the structured phase event (`{work, kind, phase, line, since}`) | the phase module and event are built to §4 in the first task that reads them; §4's added phases join whichever list exists; kind `subagent` maps a helper's state to phases (§4.4) |
| #3832, #3833, #3890 the buddy's capture sources and shown alert | the listening face and the alert the buddy holds | a voice call is a capture source of its own: while it holds the microphone the buddy shows its listening face and "In a call: {chat}", and it does not hold the shown alert; push-to-talk's listening still holds it |
| #3888 waiting on you | one list and one change event of everything waiting on the owner, each item with its answer route | the list is built to §11.2 before the dock |
| #3889 running now | one list of live work by work reference | §4's run state is a chat's row of it, served for chats here if #3889 has not shipped; each row carries `agent` (the agent doing the work; "" when none or several), which this release adds if #3889 ships without it; background commands are live work of kind `job` |
| #3821 comments on a change | the owner's comments on a diff and a plan | they take §17.7's `Comment`; if #3821 ships its own shape first, the comments task makes it that one in the same change |
| #3824 the strip above the composer | a region above the composer for live work | goal work's strip and the plan strip follow the as-built component |
| #3837 chats that open, brief and message each other | nothing in this release: they are later work | only the read half ships: `chat_read`; no row, command or tool opens, joins or messages another chat |
| PersonalClaw/PersonalClawApps#191 the Code Review app | its findings | recorded in §17.7's `Finding`, with an idempotent backfill of its log |
| PersonalClaw/PersonalClawApps#190, #197 the Research Lab | its page | rewritten to build on the one research engine, so there is one way to run research: the page becomes a view over research loops |
| #3805 ChatPage split into region controllers | the composer stage and transcript as regions | the typed turn's frontend half does not start until it lands; every ChatPage line cited here will move |
| #3807, #3808, #3813, #3814, #3820, #3822, #3824 | the orb, the composer pills, Home's lanes, sidebar pips, the thread layout, the keyboard model, the progress strip | each consuming theme follows the as-built component; §11 and §12 name the hooks |
| #3819 (with the command palette) the command registry | every new action is a registry row | themes add rows; no theme reads key events directly |
| #3909 the typed CI catalogue, #3911, #3918, #3939 | the per-job verdict JSON, the release gate, honest checks, one browser invocation | §14.3's verdict field is added to the catalogue's verdict JSON when it exists, and creates the module there if it does not |
| #3843 per-capability switch and status | `off`, `needs setup`, `starting`, `running`, `failed`, `unavailable` per app capability | §7.4 maps them; if #3843 has not shipped, apps report one status per app and the same map applies |
| #3849, #3850, #3851 | pinned commit and tree digest per app install; no `registry` origin; the withdrawal list | §15 reads them; the word "withdrawn" is shared with §16.2 |
| #3862 app sign-in | an app account's sign-in state (`needs sign-in`), and a feature that needs more access | §7 maps them; the sign-in ask in a chat (§11.2) covers MCP servers first and app accounts once #3862 ships |
| #3865 per-app cost | the app column of usage rows | §17.4 puts the automation columns beside it, in one rollup |
| PersonalClaw/PersonalClawApps#173 | a provider's allowance windows (its quota) | §7.4 reads a provider's quota through the `model:` producer when that work records it; until then nothing reports a quota |
| the mini apps (#3950) | the sealed-page host (`web/src/host/`, ADR-0001, ADR-0008), `GrantBook("miniapps")`, `approval_grants.MINIAPP_WRITES`, `owner_allow.MINIAPP_PREFIX`, the `miniapp_change` frame, the inventory flags `exported`, `sharded`, `partition_only`, the browse-files picker; one workbook reader: the linked files read through `documents/workbook_read.py` and `documents/cell_refs.py`, one parser and one set of caps | no contract here reuses a name the mini apps allocate (§1.1 lists their error codes); connected servers' views (§17.1) wait for the host; if the mini apps ship their own workbook reading first, the first task that reads a workbook moves it into `documents/workbook_read.py` and the mini apps import it; #3976's workbook reader is that shared reader, and #3996's write-back is the reader rail's third allowed row |

### 0.6 The decisions this document makes

Where two designs needed one shared concept in different shapes, or a design disagreed with the code, the
decision and its reason are in the concept's section. The index:

| # | Decision | § |
|---|---|---|
| 1 | The turn store stays the owner's record, separate from the run ledger, but takes the ledger's stamping rules | 3.6 |
| 2 | The chat record moves out of the transcript's first line into a sidecar, so the transcript can be append-only | 3.7 |
| 3 | Socket frames keep the lowercase-snake family (`turn_part`, not `part.updated`) | 1.6, 3.8 |
| 4 | A tool part has an `unknown` terminal state; "outcome unknown" is a state, not a parse of error text | 3.4, 6 |
| 5 | Reasoning text stays live-only, never written to the record | 3.3 |
| 6 | A user message's id is the client's `client_message_id`, so duplicate detection reads the record, with no TTL store | 3.9 |
| 7 | One phase frame (`work_phase`) carries a chat's run state; the routes serve its last value | 4 |
| 8 | Readiness has fifteen states, not seven; `needs_engine` folds into `needs_prerequisite` | 7.2 |
| 9 | "Would this call ask" is one pure function the real gates call, not a dry run through the gates | 8.3 |
| 10 | The approval text is composed by the server brief, not the frontend renderer registry | 11.4 |
| 11 | Chat grants persist with the chat across a restart; chat-wide Trust stays in memory | 9 |
| 12 | Revisions are one convention plus one small pure module; each store keeps its own storage | 10.1 |
| 13 | One closed author vocabulary for every definition's revisions | 2.5, 10 |
| 14 | "Seen" is its own small store, not a chat-record field and not a transcript event | 12 |
| 15 | A project's permission-mode default is limited to Normal or Trust reads | 13.3 |
| 16 | The verdict vocabulary has one home, the CI catalogue's module, which the harness imports | 14.3 |
| 17 | Rollback state leaves `config.json` for the machine-local run-state file | 16.2 |
| 18 | The boot-refusal record is machine-local and deliberately unclaimed, like the run-state file | 16.3 |
| 19 | `POST /api/chat` keeps `queue_mode` for delivery; no second field is added | 3.9 |
| 20 | The reply contract changes as a clean SDK break with a conformance kit, not a tolerant read of a bool | 6.4 |
| 21 | The author vocabulary for every revision is the one the workflow-version fix defines (`versions.SAVERS`), moved to one home | 2.5, 10.4 |
| 22 | "Who decided" uses the two vocabularies that exist (`decided_by` grant words, `refused_by` gate words), each made a closed set in code; no third is made | 8.2 |
| 23 | Whether a call may have changed something is `task_modes.reads_only`, the declaration the approval gate reads, fixed at call time | 3.3, 3.5 |
| 24 | A trigger run's id is minted at dispatch by the one id helper, so a work reference names one run | 2.2 |
| 25 | A change any author but the owner makes to what an allowed automation runs is held aside, and the automation as allowed keeps running | 10.5 |
| 26 | No row or route serves a revision number as `revision`, the stale-write digest's name | 10.3 |
| 27 | One MCP call result carries both what the server sent and §6's outcome: the MCP client owns the type, §6.1 the rule | 6.3 |
| 28 | `tool_search` names what is not ready instead of carrying readiness per tool | 7.6 |
| 29 | A refused start that writes the boot-refusal record exits 78; a start on a home another gateway serves exits 3 and writes nothing, and under a service it waits for the home | 16.3 |
| 30 | A blank slate the harness starts (`--test-mode`) draws no label and may take a fixed port, never the install's | 14.2 |
| 31 | The owner's uploads are `file` parts; a pick from the capability index is a `mention` part | 3.3 |
| 32 | A trigger's project is part of what its grant covers, for an action that starts work in a project | 13.5 |
| 33 | A native runtime that starts fresh is restored from the typed record, its calls and results included; agent CLIs started fresh and text readers read the fold as text | 3.12 |
| 34 | A result's `text` is what the model was given, never cut by the record, except that it keeps the whole result where the model was sent a one-line reference to an identical one | 3.3 |
| 35 | What a restart runs again is decided by class over a unit's parts; a unit of agent work is never `idempotent_key` | 5.9, 6.7 |
| 36 | One work queue holds every accepted unit that is not a chat's own turn; the journal holds chat turns; the queue's pass runs first | 5.9 |
| 37 | One restart notice per start lists every re-dispatch of every pass | 5.8 |
| 38 | A channel's sends go through one path: per-piece receipts and keys, and the agent's call key, on one send record | 6.3, 6.7 |
| 39 | Host answers are one set of four, written through one owner-only writer that keeps each entry's origin | 9.11 |
| 40 | A fenced command in a chat the owner watches reaches the allowed hosts and the package registries without a card | 9.11 |
| 41 | One typed rule for every standing allow, ask and deny; `hook_pattern` becomes `standing_rule`; a grant's lifetime is `lasts` | 9.3, 9.10, 8.2 |
| 42 | An `@agent` mention hands the message to that agent; the agent pill switches a chat's agent | 3.3 |
| 43 | `ToolResult.withheld` is a whole result held for the owner's Release, or an account-recovery screen's inline hold with nothing to release | 3.3 |
| 44 | Every timed wake in the gateway is a job of one scheduler | 17.15 |
| 45 | A named core feature leaves only with its contract, in a change whose CHANGELOG names the apps it refuses | 1.8 |
| 46 | "Group chat" is a messenger's many-person conversation; "groups" organise the chat list and the artifact library | 1.10 |

---

## 1. Conventions every design follows

### 1.1 Error codes

Status: planned

PersonalClaw has three code vocabularies, and the case of a code says which surface it is on
(`src/personalclaw/http_errors.py:25-34`; `src/personalclaw/workflows/error_codes.py:1-24`):

| Vocabulary | Registry | Rule | Rail |
|---|---|---|---|
| Wire codes, `lowercase_snake`, in the HTTP error envelope `{"error": {"code", "message", …}}` | `http_errors.HTTP_ERROR_CODES` (`http_errors.py:59-1337`), emitted only by `json_error(code, *, message, status, headers, error_extra, **extra)` (`:1340-1374`) | append-only; never reworded once released; the meaning doubles as the default message | `tests/test_http_error_codes_append_only.py` (append-only, and every literal passed to `json_error` is registered); `tests/test_wire_error_envelope_census.py` ratchets flat `{"error": "<prose>"}` answers down |
| Agent codes, `ERR_UPPER_SNAKE`, on `AgentError {code, what, why, fix, suggestions}` carried into a model's context | `errors.ERROR_CODES` (`src/personalclaw/errors.py:41-202`), `AgentError` (`:205-261`) | append-only; never reworded | `tests/test_error_codes_append_only.py` |
| Workflow service-result codes, `WF_UPPER_SNAKE` | `workflows/error_codes.WF_ERROR_CODES` | **both directions**: a code a core module raises has a row, and a row whose last raise site goes is removed in the same change; translated to wire codes by `workflows/handlers.py`'s `_STATUS_MAP` | `tests/test_wf_error_codes_registry.py` |

Rules for this release:

- A new failure a route can answer gets a wire code; a new failure a tool returns to an agent gets an
  agent code; a new workflow-engine result gets a `WF_` code. One failure that reaches two surfaces gets
  one code in each vocabulary, named for the same fact (`outcome_unknown` and `ERR_OUTCOME_UNKNOWN`).
- A refusal that asks the owner a consent question is not a new code: it is
  `http_errors.consent_required(field, consent, title=…)` (`http_errors.py:1382-1416`), the
  `400 confirmation_required` the dashboard's `withSecurityConsent` reads.
- A code's meaning in the registry is product copy the owner may read; it names what happened and that
  nothing changed when nothing did.
- No code here reuses a name the mini apps (#3950) append, whose meanings are written for mini apps: `unknown_method`, `not_declared`, `capability_revoked`, `miniapp_token_out_of_scope`,
  `page_missing`, `miniapp_page_not_deployable`, `too_large`, `busy`, `timeout`, `refused_by_guard`,
  `conflict`, `quota_exceeded`, `bulk_needs_owner`, `export_needs_owner`, `link_refused`,
  `import_refused`, `scan_refused`, `asset_type_refused`, `budget_exhausted`, `resume_needs_consent`,
  `internal`.

**The codes this document allocates**, each named by the section that uses it (a theme's other codes are
its design's to add, by these rules):

| Code | Vocabulary | Status | Owner § | Meaning (the registry row) |
|---|---|---|---|---|
| `invalid_message_id` | wire | 400 | 3.9 | A message id must be `msg_` followed by 26 letters and digits; nothing was sent. |
| `message_id_conflict` | wire | 409 | 3.9 | That message id was already sent in this chat with different text, so nothing was sent again. |
| `turn_not_found` | wire | 404 | 3.9 | No turn in this chat started with that message. |
| `turn_not_current` | wire | 409 | 3.9 | That turn is no longer running, so nothing was stopped. |
| `invalid_mention` | wire | 400 | 3.9 | A mention names something the composer cannot attach; nothing was sent. |
| `chats_not_converted` | wire | 503 | 3.10 | This version could not convert your chats yet, so they cannot be opened; the Doctor says why. |
| `nothing_to_continue` | wire | 409 | 5.5 | That turn ended with nothing left to continue, so no new turn was started. |
| `outcome_unknown` | wire | 502 | 6 | The request may have taken effect: its answer was lost after it was sent. Check before trying again. |
| `ERR_OUTCOME_UNKNOWN` | agent | | 6 | A write whose result could not be confirmed: it may have happened. Read the current state before trying it again; never retry it blind. |
| `reply_not_sent` | wire | 409 | 6.4 | The reply was not sent, and nothing left this machine; the message says why. |
| `inbox_source_not_connected` | wire | 503 | 6.4 | The reply's source is not connected, so nothing was sent. |
| `ERR_CAPABILITY_NOT_READY` | agent | | 7.6 | A capability the call needs is not ready; `fix` names the owner's next action and where it is taken. |
| `readiness_subject_unknown` | wire | 404 | 7.5 | Nothing installed or configured answers to that name. |
| `agent_not_found` | wire | 404 | 8.4 | No agent on this instance has that name. |
| `ERR_GOVERNANCE_SCOPE_UNDESCRIBED` | agent | | 8.5 | A governed scope has no label or notice; the ceiling cannot say what it bounds. |
| `chat_grant_pattern_refused` | wire | 400 | 9.4 | That pattern cannot be allowed for a whole chat; the message says why. Nothing was allowed. |
| `chat_grant_not_found` | wire | 404 | 9.5 | This chat holds no grant with that id. |
| `chat_grants_full` | wire | 409 | 9.3 | This chat already holds 50 grants. Remove one before adding another; nothing was allowed. |
| `revision_not_found` | wire | 404 | 10.3 | That definition has no revision with that number. |
| `held_change_out_of_date` | wire | 409 | 10.5 | The automation changed after this change was made, so nothing was changed. Look at it again. |
| `sign_in_ask_not_found` | wire | 404 | 11.2 | No sign-in request has that id. |
| `sign_in_ask_ended` | wire | 409 | 11.2 | This sign-in request ended: {how}. |
| `project_default_refused` | wire | 400 | 13.3 | A project can make new chats start in Normal or Trust reads only; nothing was saved. |
| `blank_slate_refused` | wire | 409 | 14.2 | This is a blank slate: it never updates itself, installs a service or removes anything. Nothing changed. |
| `incident_reason_required` | wire | 400 | 16.1 | Stopping unattended work needs a reason; nothing was changed. |
| `incident_not_found` | wire | 404 | 16.1 | No incident in the history has that id. |
| `ERR_BOOT_PORT_IN_USE` | agent | | 16.3 | The gateway's port is taken by another program, so it did not start. |
| `ERR_BOOT_HOME_NOT_CLAIMED` | agent | | 16.3 | This home's lock could not be taken, so no gateway may serve it and this one did not start. |
| `ERR_BOOT_KEEPS_CRASHING` | agent | | 16.3 | PersonalClaw stopped unexpectedly again and again, even with its side services held back, so it did not start. |
| `no_running_turn` | wire | 409 | 3.9 | No turn is running in this chat, so the steer was not taken. |
| `turn_not_steerable` | wire | 409 | 3.9 | The running turn takes no steer; the message says why. Nothing was taken. |
| `part_not_found` | wire | 404 | 3.9 | This chat has no part with that id. |
| `needs_approval` | wire | 409 | 3.9 | The turn is waiting for an approval, so it has no answer yet. |
| `handoff_refused` | wire | 409 | 3.9 | That agent can't be asked from this chat; the message says why. Nothing was sent. |
| `kept_thread_fixed` | wire | 409 | 3.7 | This is a kept agent's thread, so its agent and title stay as they are. Nothing changed. |
| `held_result_not_found` | wire | 404 | 3.3 | This chat holds no withheld result with that id. |
| `turn_superseded` | wire | 409 | 5.5 | A newer message came after that turn, so it was not continued. |
| `continue_already_scheduled` | wire | 409 | 5.5 | This chat already has a timed Continue; nothing new was scheduled. |
| `gateway_restarting` | wire | 503 | 5.8 | PersonalClaw is restarting, so this message was not taken. Send it again when PersonalClaw is back. |
| `helper_start_key_conflict` | wire | 409 | 5.9 | That start key was used for different work, so nothing was started. |
| `ERR_HELPER_START_KEY_CONFLICT` | agent | | 5.9 | That start key was used for different work, so no helper was started. |
| `ERR_WORK_NOT_RECORDED` | agent | | 5.9 | PersonalClaw could not record this work, so it did not start; the message says why. |
| `rule_refused` | wire | 400 | 9.10 | That rule cannot be kept; the message says why. Nothing was saved. |
| `rule_not_found` | wire | 404 | 9.10 | No rule has that id. |
| `rules_full` | wire | 409 | 9.10 | Your rules are at their limit. Remove one before adding another; nothing was saved. |
| `egress_host_refused` | wire | 400 | 9.11 | That is not a host PersonalClaw can allow or block; the message says why. Nothing changed. |
| `egress_host_denied` | wire | 409 | 9.11 | That host is on Denied hosts; remove it there first. Nothing changed. |
| `egress_host_above_ceiling` | wire | 409 | 9.11 | The operator ceiling bounds that host anyway; the message says how. Nothing changed. |
| `secret_request_not_found` | wire | 404 | 11.2 | No secret request has that id. |
| `secret_request_ended` | wire | 409 | 11.2 | This secret request ended: {how}. |
| `secret_request_not_saved` | wire | 409 | 11.2 | {name} is not in Settings › Secrets yet, so nothing was answered. |
| `ERR_FENCE_NOT_APPLIED` | agent | | 17.8 | The sandbox could not hold this command the way the setting that allowed it requires, so it was not run. |
| `memory_hold_out_of_date` | wire | 409 | 17.9 | What this would change was changed since it was held, so nothing was kept. Look at it again. |
| `private_chat_model_trains` | wire | 409 | 17.11 | This chat is private, and that model is offered on terms that let its vendor train on what you send. Pick another model for this chat. |
| `command_not_found` | wire | 404 | 17.14 | There is no command with that name here; nothing ran. |
| `command_refused` | wire | 403 | 17.14 | That command is not offered here, or not to you; nothing ran. |
| `command_busy` | wire | 409 | 17.14 | A reply is still being written here, so that command did not run. |

**Codes reserved for the designs that add them**, listed here so no other design reuses a name; each
meaning is written by the design that adds it, by the rules above (status in brackets where it is fixed):

| Reserved for | Wire codes | Agent codes |
|---|---|---|
| approvals, access and fill requests, secrets | `browse_submit_scope_refused` [400] (a browse step's `may_submit`); `app_source_above_ceiling` [409]; `cli_grant_refused` [403]; `access_request_not_found` [404], `access_request_ended` [409], `access_path_refused` [400]; `owner_action_moved` [409], `owner_action_not_found` [404]; `fill_request_not_found` [404], `fill_request_ended` [409], `fill_host_refused` [409]; `secret_value_too_short` [400], `secret_value_too_large` [400]; `device_signature_invalid` [401]; `trace_not_found` [404] | `ERR_BROWSE_SUBMIT_SCOPE` (the automation tools' form of the browse refusal), `ERR_APPROVAL_DRIFTED`, `ERR_ACCESS_REQUEST_REFUSED`, `ERR_CONSULT_REFUSED`, `ERR_COMPUTER_USE_ABOVE_TIER` |
| held changes and declined calls | `held_change_not_found` [404]; `keep_refused` [409], `keep_needs_a_model` [409]; `declined_call_not_found` [404], `declined_call_out_of_date` [409], `declined_call_answer_refused` [400]; `standing_allow_not_found` [404] | |
| skill, pack and folder-skill updates | `skill_update_not_offered` [409], `skill_update_unreviewed` [409], `skill_update_dangerous` [403], `skill_source_unreachable` [502], `revision_not_restorable` [409], `pack_source_refused` [400], `pack_source_unreachable` [502], `pack_update_not_offered` [409], `folder_skill_not_offered` [404], `folder_skill_unreviewed` [409], `folder_skill_dangerous` [403], `folder_instructions_unreviewed` [409], `folder_instructions_refused` [403] | `ERR_INSTALL_SUGGESTION_REFUSED` |
| channels, bindings and sends | `listen_mode_refused` [400], `binding_refused` [400], `agent_in_binding` [409], `home_chat_refused` [409], `channel_recipient_refused` [403], `send_not_found` [404], `send_not_resendable` [409], `draft_not_pending` [409], `share_refused` [400], `share_not_found` [404], `location_not_declared` [409], `sender_notes_off` [409] | `ERR_RECIPIENT_REFUSED` |
| worktrees, reviews and page previews | `worktree_not_available` [409], `worktree_not_for_this_chat` [409], `worktree_base_unknown` [400], `worktree_disk_space` [409], `worktree_folder_busy` [409], `worktree_no_identity` [409], `worktree_carry_too_large` [409], `worktree_not_found` [404], `worktree_not_ready` [409], `worktree_work_unsaved` [409], `worktree_apply_conflicts` [409], `invalid_comment` [400], `review_scope_empty` [409], `review_scope_too_large` [409], `review_not_available` [409], `review_not_found` [404], `finding_not_found` [404], `ways_not_available` [409], `ways_running` [409], `ways_not_found` [404], `way_not_found` [404], `preview_url_refused` [400], `preview_file_refused` [400], `preview_engine_missing` [503], `preview_engine_unavailable` [503], `previews_full` [409], `preview_not_found` [404], `pins_full` [409], `chat_sandbox_unavailable` [409] | |
| an app's Check, MCP sign-in and views | `app_check_not_declared` [409] (an app's Check); `mcp_sign_in_issuer_unknown`, `mcp_sign_in_scope_refused` [400] (sign-in choices); `mcp_views_off` [409], `mcp_view_not_found` [404], `mcp_view_resource_refused` [422], `mcp_view_call_refused` [403], `mcp_view_pin_changed` [409] (views from connected servers) | |
| compaction | `compaction_not_found` [404] | |
| kept conversations, result schemas and turn errors | `conversation_not_kept` [404]; `result_schema_invalid` [400], `invalid_limits` [400]; the turn errors carried in `Message.error.code`: `turn_failed`, `turn_stopped_repeating`, `step_limit_reached`, `spend_limit_reached`, `token_limit_reached`, `time_limit_reached`, `schema_unmet`, `prompt_delivery_unknown` | |
| the fenced shell and the agent's computer | `fence_hold_not_found` [404], `fence_cache_in_use` [409], `secret_hosts_invalid` [400], `chat_fence_not_applicable` [409], `computer_not_set_up` [409], `computer_busy` [409], `computer_tier_unsuitable` [409] | `ERR_NO_FENCE` |
| replies with files, and workbooks | `reply_not_offered`, `reply_in_flight`, `reply_file_refused`, `reply_takes_no_files`, `reply_outcome_known`, `sent_lookup_unavailable`, `formula_refused` [400] | `ERR_WORKBOOK_UNREADABLE`, `ERR_WORKBOOK_TOO_LARGE`, `ERR_SHEET_RANGE_INVALID`, `ERR_SHEET_SOURCE_NOT_FOUND`, `ERR_SHEET_EDIT_INVALID`, `ERR_FORMULA_REFUSED`, `ERR_WORKBOOK_FEATURES_DROPPED` |
| kept agents and their sign-ins | `agent_already_kept` [409], `agent_not_keepable` [400], `kept_agent_not_found` [404], `kept_agents_full` [409], `portrait_refused` [400], `portrait_in_progress` [409], `portrait_needs_image_model` [409], `agent_name_taken` [409], `sign_in_not_found` [404], `sign_in_share_refused` [400] | `ERR_BROWSE_HOLDER_NEEDS_LOCAL_BROWSER` |
| helpers and background commands | `helper_not_found` [404], `helper_not_running` [409], `helper_follow_up_closed` [409], `helper_not_interrupted` [409], `job_not_found` [404], `job_not_running` [409], `job_takes_no_input` [409] | `ERR_JOBS_FULL`, `ERR_HELPER_NOT_RUNNING`, `ERR_HELPER_FOLLOW_UP_CLOSED` |
| lessons and memory records | `lesson_not_found` [404], `lesson_standing_full` [409], `memory_record_not_found` [404] | |
| updates, recovery and uninstall | `update_unverified` [502], `recovery_not_active` [409], `recovery_service_unknown` [404], and the release, desktop-update and uninstall codes their design adds | |
| offers, the checklist, projects, goals and plans | `offer_not_found` [404], `offer_failed` [409], `checklist_item_unknown` [400], `project_default_invalid` [400], `project_setup_running` [409], `chat_has_goal` [409], `goal_refused` [400], `plan_not_found` [404], `already_in_library` [409], `text_link_invalid` [400] | |
| calls and the wake word | `call_not_available`, `call_in_progress`, `call_not_found`, `call_ended`, `call_socket_taken`, `wake_word_not_local`, `wake_word_off`, `wake_word_unavailable`, `wake_word_listener_taken` | |

Codes that already exist are reused, not allocated again:
`fresh_sign_in_required` (401, owner presence, §8.7), `retry_repeats_steps` (409, the Retry question,
§5.5) and `store_unreadable` (409, a store that cannot be read is never written over). So are today's
`project_not_found` (404, §8.4, §13.3, §13.5), `session_not_found` (404, §8.4), `approval_grant_refused`
(409, §9.5), `approval_owner_only` and `approval_owner_ended`, `revision_required` (428) and `stale_write`
(409, §10.3, §13.3), `confirmation_required` (400, through `consent_required`), `session_busy` (409),
`not_allowed` and `forbidden` (403), `paste_not_expanded` (400, a send whose paste kept its marker),
`lesson_refused`, `memory_partition_not_found`, `fire_held`,
`not_found`, `bad_request` and `invalid_request`. The served-home start has no code: it is no `AgentError`,
and its exit status says it (§16.3).

### 1.2 The config round trip, and when a setting applies

Status: planned

A new config field wires through five points (`AGENTS.md`, "Config round-trip"; the harness rule
`harness/specs/rules/config-four-points.md`): the dataclass field with
`metadata=_meta(label, help, **kwargs)` (`src/personalclaw/config/coercion.py:49-51`), `AppConfig.load()`'s
explicit mapping, `to_dict()`, a write path (an `_EDITABLE_CONFIG` entry in
`src/personalclaw/config/editable.py`, or a dedicated PUT), and, if the owner can see it, a Settings
control. `tests/test_config_roundtrip.py` (`test_every_leaf_field_survives_save_load`,
`test_load_fallbacks_match_dataclass_defaults`) holds it. `config.json` has one writer,
`config.transactions` (`tests/test_config_transactions.py`).

**`applies`**: every editable field's `_meta` carries
`applies=<next_call | next_turn | next_session | restart>`, saying when a change takes effect, and the
PATCH answers it per changed field, so Settings can say "Applies to your next message". `next_call` is
read per call; `next_turn` at a turn's start; `next_session` when a session's runtime is next built
(`SessionManager.get_or_create`, `src/personalclaw/session.py:961`; an agent edit marks live sessions for
it, `:1465-1478`); `restart` only at gateway start. A provider's or an app's settings answer by the
provider's type: `next_turn` for model, search, speech, embedding and media providers (a running turn
keeps the instance it started with), `next_call` for tool providers and channels (the next call or
message reaches the rebuilt one). That holds because a provider save builds the new instance before it
swaps the old one out, a failed build leaves the old one running and the save answers `applied: false`
with the reason, and a replaced instance is closed only when the last running turn holding it lets go
(`ProviderRegistry.rebuild` and `ProviderRegistry.hold`); `PATCH
/api/providers/{name}/config` and `PUT /api/apps/{name}/config` answer `applies` per changed setting.
**Every config field this release adds declares `applies` from its first commit**, whether or not the
`applies` work has landed (`_meta` takes keyword arguments today), and that work's rail then requires it
of every editable field.

**`_meta` also declares `direction` and `maturity`**: every security field declares
`direction=restricts | grants`, so a restriction that cannot be read withholds the grants it qualifies
(`config/validation.RESTRICTION_PAIRS`, `withheld_grants()`, read by `decide` as `Context.withheld`) instead
of reading as empty, and a table test fails on a security field with no direction; a preview feature
declares `maturity="preview"`, matched both ways with one table of previews. A `grants` field keeps falling
back to its narrow default.

**The config fields this document allocates:**

| Path | Type | Default | Applies | Write path | Owner § |
|---|---|---|---|---|---|
| `session.resume_after_restart` | bool | `true`: a dashboard chat with nothing waiting continues once after a restart (§5.4) | `restart` | `_EDITABLE_CONFIG`; Settings › Chat | 5 |
| `gateway.restart_drain_secs` | int, 0-600 | `120` | `next_call` | `_EDITABLE_CONFIG`; Settings › Updates ("How long a restart waits for running work") | 5.8 |
| `session.turn_time_limit_secs` | int, 600-86400 | `7200` | `next_turn` | `_EDITABLE_CONFIG`; Settings › Chat ("Longest a reply you asked for may run") | 3.9 |
| `updates.soak_hours` | int, 0-720 | `24` (`0` turns the wait off) | `next_call` | `_EDITABLE_CONFIG` (`{"type": "int", "min": 0, "max": 720}`); Settings › Updates | 16.2 |
| `security.trust_os_certificates` | bool | `false` | `restart` | `_EDITABLE_CONFIG` as a security control (`SecurityControl`, loosens when turned on); Settings › Security | its design's |
| `security.rules.limits` | [Rule] (deny and ask, at most 200) | `[]` | `next_call` | `_EDITABLE_CONFIG`, `direction: restricts`; Settings › Security › Rules, and `POST`/`DELETE /api/security/rules` | 9.10 |
| `security.rules.allows` | [Rule] (allow, at most 100) | `[]` | `next_call` | `_EDITABLE_CONFIG`, `direction: grants`; the same | 9.10 |
| `security.rules.dropped` | [str] | `[]` | `next_call` | the fold of the retired pattern keys writes it; Settings › Security's Dismiss writes `[]` | 9.10 |
| `security.shell_network` | `fenced` \| `open_when_attended` | `fenced` | `next_call` | `_EDITABLE_CONFIG` as a security control (loosens when `open_when_attended`); Settings › Security | 9.11, 17.8 |
| `security.shell_without_sandbox` | `refuse` \| `allow` | `refuse` | `next_call` | `_EDITABLE_CONFIG` as a security control (loosens when `allow`); Settings › Security | 17.8 |
| `learning.memory_review` | `off` \| `ask` | `off` | `next_call` | `_EDITABLE_CONFIG`, `direction: restricts` (an unreadable value reads `ask`); Settings › Memory | 17.9 |
| `memory.follow_up_proposals` | bool | `false` | `next_call` | `_EDITABLE_CONFIG`; Settings › Memory ("Suggest follow-ups"); the fold of `memory.proactive_commitments` carries the owner's opt-in | 17.9 |

There is no `chat` or `network` config section: a chat's settings live under `session`
(`config/loader.py:864`) and security controls under `security`. `updates.last_version` is **removed**
(§16.2), and so are `hooks.auto_approve_tools` and `hooks.auto_deny_tools` (folded, §9.10),
`memory.proactive_commitments` (folded into `memory.follow_up_proposals`) and
`memory.proactive_commitments_max_per_day` (dropped). No other shared config field is added; a theme's own
fields follow the five points and `applies`.

### 1.3 Where state lives

Status: planned

From `AGENTS.md` ("Shared conventions", "Storage") and the code, binding on every new store:

- Operator knobs go to `config.json`; per-entity preferences to `entity_settings/<entity>.json` through
  `providers/entity_routes._load_entity_settings` / `_save_entity_settings`
  (`src/personalclaw/providers/entity_routes.py:38-89`), whose reader tells absent (`{}`) from unreadable
  (`None`) and picks no fallback for the second; secrets to the credential store, never a config file or
  a record. **Machine state that is neither a preference nor a knob gets its own file** (the precedent:
  `update_run.json`, `self_update.py:99-103`).
- Everything lives under `config_dir()`, resolved by `config.loader`, never hard-coded and never at import.
- Writes go through `atomic_write` / `atomic_json_write`; a JSON store with several writers holds a lock
  file beside it (`record_files.locked`, which locks `.<stem>.lock`, `src/personalclaw/record_files.py:76-86`).
- An append-only JSONL log trims at twice its cap, keeping the newest rows by each row's own time
  (`bounded_log.trim_jsonl`, `src/personalclaw/bounded_log.py:99`; `tests/test_bounded_log_rail_trims_by_time.py`).
- A path built from an id goes through `record_ids.require_safe_record_id` / `record_path`
  (`src/personalclaw/record_ids.py:101-118`).
- Each read site states whether it fails open (availability surfaces) or closed (security surfaces, and
  any default that performs something irreversible or more permissive). Every store this document adds
  states its choice in §1.4.
- What an agent may not write is `owner_only`'s (landed): in the home, the files
  `OWNER_ONLY_FILES` (`config.json`, `mcp.json`) and the folders `OWNER_ONLY_DIRS` (`hooks`, `agents`,
  `grants`, the mini apps' `miniapps`, and `kept-agents`, since a kept agent's record holds what enters its
  prompts and its memory switch); git's own settings and hook scripts (kind `git`: a repository's
  `config`, `config.worktree`, `commondir`, `hooks/`, `.gitmodules`, a rebase's steps, a linked worktree's
  `.git` file, the owner's own git settings); and the owner's shell startup files (kind `shell`). The
  agent's sandbox, the tool-call screen and the write paths refuse writes to them. A store that holds the
  owner's yes lives in `grants/` through `GrantBook` (§17.1).
- A file a store cannot read is never written over (`record_files`' rule, landed): every write refuses
  (`409 store_unreadable` on a route) until it reads again, a copy of it as it was is kept beside it, and a
  reader that only lists may read it as empty. Every store this document adds takes the rule.

### 1.4 The durability inventory

Status: planned

Every path under the home is claimed by a `StateEntry` in `src/personalclaw/durability/inventory.py`
(`:88-183`) or listed in its ignored names with a written reason (`:2150-2215`); `audit_home`
(`:2553`) fails on anything else, and `tests/test_durability_inventory.py` runs it. An entry declares
`kind` (`json_entity_dir`, `json_file`, `jsonl_append`, `sqlite`, `tree`), `domain`, `merge`
(`union_by_id`, `lww_by_updated_at`, `append_dedup`, `sqlite_attach_ignore`, `replace_only`) and the flags
`secret`, `credential`, `derived`, `machine_local`, `machine_local_within` (globs inside a tree that never
leave the machine, `inventory.py:116`), `merged_in`, `records`, `arrives`, `compared`,
`edit_arrives`. An append-only folder of files (`jsonl_append` with a suffix-less path, such as
`sessions` and `cron-history`) is neither sent nor taken by a sync (`append_only_folder`, `:2409-2414`).

**The stores this document adds or changes:**

| Store | Path | Entry | Kind / merge / flags | Fails | Owner § |
|---|---|---|---|---|---|
| Transcript event log | `sessions/<safe_key>.jsonl` | `sessions` (`inventory.py:552-559`), unchanged id; `help` "chat transcripts: one event per line, and each chat's record beside it" | `jsonl_append`, `append_dedup` by event id | open on read (an unreadable line is skipped and counted); closed on write to another chat's file (today's guards) | 3.6 |
| Chat record | `sessions/<safe_key>.chat.json` | inside `sessions` | a JSON file beside the log, like today's `<safe_key>.summary.json` | an unreadable record opens the chat read-only, and nothing writes it | 3.7 |
| Worker conversations | `sessions/subagent_<id>.*`, `sessions/hook_<…>.*` | inside `sessions` | a chat's log and record: in snapshots, never synced | open | 3.7 |
| Set-aside transcripts | `sessions/.unconverted/` | inside `sessions` | kept as found | never read | 3.10 |
| Recovery journal | `turn_recovery.json` | new `turn_recovery` | `json_file`, domain `work`, `replace_only`, `machine_local=True`, `merged_in=False` (another home's cut-off turns are never resumed here) | closed: an unreadable journal resumes nothing | 5.3 |
| Seen | `chat_seen.json` | new `chat_seen` | `json_file`, domain `work`, `lww_by_updated_at`, `machine_local=True` | open: unreadable reads as nothing seen (every finished reply shows unread), and is logged | 12 |
| Incident history | `incidents.jsonl` | new `incident_history` | `jsonl_append`, domain `security`, `append_dedup` by `id`, `machine_local=True`, trimmed at twice a cap of 1,000 lines | open on read; the switch itself (`incident.json`) keeps its shape and gains fields while active (§16.1) | 16.1 |
| Trigger revisions | `trigger-revisions/<trigger_id>.jsonl` | new `trigger_revisions` | `jsonl_append` folder (suffix-less: no sync sends or takes it), domain `automation`, `append_dedup` by `n` | open on read ("revision not recorded") | 10.2 |
| Held changes | `trigger_held_changes.json` | new `trigger_held_changes` | `json_file`, domain `automation`, `replace_only`, `machine_local=True`, `merged_in=False` (an agent's proposal here is this home's to answer) | closed: an unreadable file holds nothing that can be allowed | 10.5 |
| Skill history | `<tier>/.history/<q(name)>/` in each skill tier PersonalClaw writes: `<n>.json` per revision, `blobs/<sha256>` per file body | inside `skills` (a tree), and inside the entry that holds agent-local skills, outside every skill's own folder so its integrity lock is untouched; `.history` joins each entry's `machine_local_within`, because a revision number belongs to one home's lock | tree | open: an unreadable history reads as none, and the next write takes the next free number | 10.2 |
| Chat groups | `groups.json` (renamed from `folders.json`) | `folders` entry renamed `groups` (`inventory.py:1244-1252`), help "chat groups in the sidebar" | `json_file`, `lww_by_updated_at`, `records=Shape()` | as today | 13.4 |
| Artifact groups | `artifacts/groups.json` (renamed from `artifacts/folders.json`) | inside the `artifacts` tree entry | as today | as today | 13.4 |
| Inbox reply state | `inbox.json` rows | the Inbox store's entry, unchanged; the reply fields of §6.4 join the rows' machine-local fields (`_TRIAGE_FIELDS`), never compared between homes | as today | as today | 6.4 |
| Reply attachments | `attachments/<row id>.reply/` | inside `attachments` (`inventory.py:601-609`), its help widened to "…and the files the owner attached to a reply" | tree | open: an unreadable file is not sent, and the panel says so | 6.4 |
| Boot refusal | `boot_refusal.json` | **ignored**, with the reason written beside `update_run.json`'s (`inventory.py:2185-2194`) | machine state whose restored copy would be wrong; replaced whole by each refusal | read as no record when absent, unreadable, or older than the latest start (the run state's `started_at` later than its `at`) | 16.3 |
| Gateway lock | `gateway.lock` | **ignored**, beside `tmux.sock` (landed): a process-scoped lock whose restored copy means nothing | an empty file, `0600`, never deleted; nothing opens it to check it, and a restart hands its claim to its new image | closed: a home the gateway cannot lock is not served | 16.3 |
| Run state of updates | `update_run.json` | already ignored (`inventory.py:2185-2194`); gains the fields of §16.2 | `record_files`' rule: never written over while it cannot be read | open for readers (`read_or_none`); writes under its lock | 16.2 |
| Work queue | `work_queue.db` | new `work_queue` | `sqlite`, domain `work`, `replace_only`, `machine_local=True`, `merged_in=False` | closed for recovery: an unreadable database re-dispatches nothing, is moved aside and named by the Doctor; open for new work: a new one takes it | 5.9 |
| Helper folders | `subagents/<id>/` | `subagents`, its help "each helper's streamed report while it runs" | from the work queue's change on, only `result.txt` | as today | 5.9 |
| Background command logs | `jobs/<job id>.log` in the chat's workspace | inside the chat's workspace | the first 64 KiB and the last 1 MiB, masked as written | kept and removed as the chat's large tool results | its design's |
| Tool-result store | a chat's stored results, inside `sessions` | inside `sessions` | bound at 500 entries and 64 MiB a chat, the least recently touched evicted first; deleted with the chat | open: a missing copy is read back from the chat's record (the handle fallback) | its design's |
| Background summaries | `sessions/**/*.summary.json` | inside `sessions` | deleted by one idempotent backfill, after the converter (§3.10) has run: the compaction record stands for them | | its design's |
| Reviews | `reviews/<safe chat key>/<rv id>.json` | new `reviews` | `tree`, domain `work`, `replace_only`, `machine_local=True`, `merged_in=False` (it names this machine's commits and paths) | open | its design's |
| Kept agents | `kept-agents/<ka id>/` (record, thread, portraits) | new `kept_agents`, help "the agents you keep: each one's record, portrait and thread" | `tree`, domain `platform`, `union_by_id`, `machine_local=True`; owner-only (§1.3) | an unreadable record reads as the agent not kept, and is never written over | its design's |
| A kept agent's memory | `workspace/_agents/*/memory_index.db`, `workspace/_agents/*/learning.db` | new `agent_memory_db`, `agent_learning_db`; the `workspace` tree's `machine_local_within` gains `_agents/*` | `sqlite`, domain `memory`, `machine_local=True` | as the memory stores | its design's |
| Channel addresses | `channel_addresses.json` | new `channel_addresses` | `json_file`, domain `platform`, `replace_only`, `machine_local`, `merged_in=False` | kept aside and read as empty: arrivals make new chats, and sends to an address it held send nothing | its design's |
| Channel sends | `channel_sends.json` | new `channel_sends` | `json_file`, domain `platform`, `replace_only`, `machine_local`, `merged_in=False`; holds the per-piece keys and the agent's call keys (§6.7) | closed for resends (none at start); open for new sends, logged | its design's |
| Refused while stopping | `inbound_refused.jsonl` | new `inbound_refused` | `jsonl_append`, domain `platform`, `replace_only`, `machine_local`, `merged_in=False`; rows past 7 days dropped | open on read | its design's |
| First-time lines | `channel_intros.json` | new `channel_intros` | `json_file`, domain `platform`, `replace_only`, `machine_local` | open | its design's |
| Sender notes | `sender_notes.json` | new `sender_notes` | `json_file`, domain `platform`, `replace_only`, `machine_local`, `merged_in=False` | closed: unreadable reads no notes and writes none | its design's |
| Channel preferences | `entity_settings/bindings.json`, `entity_settings/channel_conversations.json`, `entity_settings/channel_trust.json` (gains `listen`, `dm: off`, `sender_notes`) | inside `entity_settings` | as that entry | bindings closed (unreadable binds nothing); conversations open; sender trust closed, as today | its design's |
| Phone shares | `attachments/shares/<id>/` | inside `attachments`, its help widened ("…and what the phone shared, kept a day") | tree | open | its design's |
| Session map | `session_map.json` | `session_map` | loses its channel links | as today | its design's |
| Fence holds | `fence_holds.json` | new `fence_holds` | `json_file`, domain `security`, `replace_only`, `machine_local=True`, `merged_in=False` | closed: unreadable places every chat's runs `held_change_kept`, and the Doctor says why | its design's |
| Fence cache; the agent's computer | `fence-cache/<folder digest>/`; `computer.json` | **ignored**, each with its reason | | open | its design's |
| Egress host origins | `egress_host_origins.json` | new `egress_host_origins` | `json_file`, domain `config`, `replace_only`, exported on write and synced with `config.json`, whose lists it describes; one writer under its lock (§9.11) | open for display: an unreadable file shows no origins, and the lists stand | its design's |
| Import records | `onboarding/import_sync.json`, `onboarding/import_history.jsonl` | inside `onboarding`, machine-local | tree | open: a lost record reads `new` | its design's |
| Recordings | `recordings/` | new `recordings` | `tree`, `machine_local=True`, `secret=True`, never exported | open | its design's |
| Skill preferences | `entity_settings/skill_updates.json` (landed, gains `notified`), `folder_skills.json`, `folder_instructions.json`, `install_suggestions.json`, `capability_use.json`, `skill_invocation.json`, `composer.json`, `onboarding.json` | inside `entity_settings` | `lww` | open | its design's |
| Memory's own tables | `memory_sources` and `memory_applied` in every `memory.db`; `consolidation_passes` in `learning.db`; `sessions_tri` in the disposable search index | inside their entries; `source_session` is removed from `memory.db` | | `memory_sources` closed; `memory_applied` open (an unreadable table reads as unseen) | its design's |
| Learning proposals | the learning proposals store | unchanged entry; gains the kinds `memory_hold` and `follow_up` and a `payload` (a lesson's replacement is the landed `lesson_batch` question tagged `lesson_conflict`) | | as today | its design's |
| Releases cache | `update_releases.json` | already ignored; gains `view: 2` and the fields of §16.2 | | open (an empty list) | 16.2 |
| Desktop installers | `desktop-installers/<tag>/` | **ignored**: a cache refilled by a download, never in a snapshot; a name of its own, because an ignored name hides that name at every depth | | closed: an unverified file is deleted and never offered | 16.2 |
| Browse replays | `browse/replays/<replay>/` | under the ignored `browse` row, its reason widened: "and the screenshots a browse run kept of its steps, which show pages the owner was signed in to: kept on this machine only, never in a snapshot, an export or a sync" | | | its design's |
| A kept agent's sign-ins | `browse/agents/<ka id>/<site>/.meta.json` | under the ignored `browse` row, its reason widened again: "and each kept agent's own sign-in records under `browse/agents/`" | | | its design's |
| Owner activity | `owner_activity.json` | new `owner_activity` (the away check's last sign of the owner) | machine state | open | its design's |
| Owner's sealed yeses | `grants/<book>.json` | `owner_grants` (`inventory.py:1535-1543`): tree, `replace_only`, `secret=True` | new books: `folder_skills` (§15.3), `folder_instructions` (§17.1), `mcp_app_views` (§17.1), `automation_rules` (§9.10), `installers` (§17.1); `secret_hosts.json` (§17.8); the per-tool read-only trust's `mcp_read_only.json` and `mcp_tool_descriptions.json`, landed before this release, in the same folder | closed: unreadable grants nothing (`owner_grants.py:58-71`) | 17.1 |

A store a theme adds that is not in this table gets its entry in the same change that first writes it,
with its fail direction stated in code.

### 1.5 Clean break, backfills and converters

Status: planned

Pre-1.0 every persisted shape, route and field changes as a clean break: the old path is deleted in the
same change, with a headline-only CHANGELOG entry and `personalclaw snapshot` in the upgrade notes
(`AGENTS.md`, "Doctrine"). Where existing data needs reshaping, the change ships **one idempotent
backfill keyed on inspecting the data**, never a version gate or a dual path:

1. It runs once at gateway start, before anything reads the store.
2. It decides from the data whether a record needs converting (a converted record is left alone), so a
   second run changes nothing; a test runs it twice and asserts the second run writes nothing.
3. It writes the new form beside the old, verifies it, and only then replaces or retires the old file; a
   failure leaves the old data as it was and says so in the log and the Doctor.
4. It reports what it did once (counts per kind), and keeps no compatibility reader afterwards.

The typed-turn converter (§3.10) is the largest; the others are the chat record's move (§3.7), trigger
revision 1 (§10.2), the `folders.json` → `groups.json` rename and the artifact library's (§13.4), a
run-workflow action's project moving onto its trigger (§13.5), the provenance backfill of skill locks and
pack records (§15.1), and the rollback state's move (§16.2). There are also the work queue's
rows for helper folders and waiting spawns (§5.9); the deletion of `.summary.json` files, which runs after
the converter (§1.4); the fold of the retired pattern keys into rules (§9.10); memory's writer rows and its
inferred follow-ups turned into proposals (§17.9, §17.10); a chat-bound Auto-nudge row turned into a paused
goal (§3.3); the trigger review's `interrupted` kind turned into `unknown` (§5.9); and the
repository-keyed memory partitions (§13.2). Each keeps these four rules.

### 1.6 The wire: routes, envelopes, the API version, socket frames

Status: planned

- **Errors** use the envelope of §1.1. **Successes on new routes** take one shape: a collection answers `{items: [...], next_cursor: str |
  null}`; every time is a UTC instant field named `*_at`; a lifecycle is `state`; how one operation ended
  is `outcome`. Existing routes keep their shapes unless a section here changes them.
- **The API version.** `API_VERSION` and `MIN_SUPPORTED_API_VERSION` (`src/personalclaw/api_version.py`),
  negotiated through `X-PersonalClaw-API-Version`, with the bump rule in that module's docstring. Version 2
  shipped in 0.3.0 with the run state. Receipts (§3.9) are this release's first change that breaks the
  wire: they replace `POST /api/chat`'s answers and remove `meta.client_ts`, and set both numbers to 3 in
  the same change (the dashboard ships with its gateway). The typed turn's removal of socket frames and its
  change to the session detail's `messages` come later in the same release and do not bump again; each
  says so in its CHANGELOG headline. A new route, a new optional field or an appended enum member never
  bumps.
- **Socket frames** are `{type, data}` on the one `/api/ws` socket (`web/src/lib/useChatSocket.ts:4, :14-38`).
  New frame types are `lowercase_snake`, as every frame today is; dotted names belong to the app platform
  events of #3857, a different vocabulary. Every frame about one conversation names it in `data.session`
  (or registers its field in `FRAME_CONVERSATION_FIELDS`), so an app's socket hears only its own
  conversations (`src/personalclaw/dashboard/ws_state.py:35-46, :96-114`;
  `tests/test_apps_see_only_their_own_conversations.py`). A frame is a signal to re-read or a complete
  value, never a partial patch a lost frame would corrupt; every consumer re-reads on reconnect
  (`useChatSocket`'s required `onReconnect`).
- **The frames this document allocates:** `turn_message`, `turn_part`, `turn_delta` (§3.8), `work_phase` (§4),
  `posture_changed` (§8.6), `readiness_changed` (§7.5), `chat_seen` (§12), `job_changed {session, job}` (a
  background command changed: a signal to re-read, §5.9), and `wake_word` (sent to the owner's sockets only;
  it names no conversation). Retired with the typed turn: `chat_chunk`, `chat_thinking`, `chat_segment`,
  `tool_call`, `tool_result`, `chat_message`, `chat_done`, `chat_user_message`, `chat_variant_switch`,
  `token_usage`, `heartbeat`, `chat_status`, `turn_steerable`, `activity_event`'s `status` and turn-fact
  kinds, and `file_ready` (§3.8 lists what is kept).
- **One table of frame kinds.** `frame_kinds.py` maps every kind sent on `/api/ws` to `stored` (`turn_message`,
  `turn_part`), `live_only` (`turn_delta`, `work_phase`) or `not_turn` (the registries' frames, the queue's,
  `session_clear` and the rest), with each stored kind's caps; `broadcast_ws` refuses an unregistered kind under
  test and logs one in production, and a census of its calls fails both ways. The lists of
  §3.8 are this table's rows. A theme that adds a frame adds its row in the same change.
- **Per-purpose sockets.** A voice call's audio rides `/api/ws/call/{message_id}` and the wake word's
  stream `/api/ws/wake`, each owner only and carrying no turn frame; every other frame stays on
  `/api/ws`.
- **SSE events** published through `SseRegistry` are registered on both ends
  (`harness/specs/rules/sse-event-registered.md`).

### 1.7 The security log

Status: planned

There is one audit log, written through `sel()` (`log_tool_invocation`, `log_api_access`, or a raw
`SecurityEvent`); new `event_type`s are `lowercase_snake` (`AGENTS.md`). API-access rows name an
`operation` as `<area>.<verb_object>` (today: `approval.grant_refused`, `guardrails.ceiling_clamp`,
`guardrails.governance_boot`, `chat.regenerate`). Every refusal, every grant given or withdrawn, and
every resumption of unattended or interrupted work writes one row; a dry read writes none.

| Operation | Written when | Owner § |
|---|---|---|
| `approval.chat_grant_given`, `approval.chat_grant_revoked` | a chat grant is made (on a card, a channel prompt or a fork that carries it) or withdrawn | 9 |
| `approval.chat_grant_refused` | a requested pattern is refused (§9.4) | 9 |
| `chat.turn_continued` | a Continue (the owner's or the journal's) starts a turn | 5 |
| `chat.turn_recovery_skipped` | the start-up pass skips a journal record, with its reason | 5 |
| `chat.loop_breaker_extended` | the owner continues a turn with repeats allowed | 5.5 |
| `guardrails.governance_check` | `personalclaw governance check` validates a file, written from its own process, valid or not (path digest, outcome, code) | 8.5 |
| `incident.reviewed` | the owner adds the "What happened?" note | 16.1 |
| `update.withdrawn_skipped`, `update.rolled_back` | the updater skips a withdrawn release; an automatic return to the last healthy release | 16.2 |
| `update.installer_opened` | a verified desktop installer is handed to the shell to open (tag, SHA-256, size) | 16.2 |
| `system.uninstall_started` | the owner's confirmed uninstall is handed to its executor | 8.7 |
| `projects.defaults_changed` | a project's defaults are saved (the project, the keys changed, whether it loosened) | 13.3 |
| `triggers.project_changed` | the owner moves an automation between projects | 13.5 |
| `mcp.step_up_wanted` | a connected server asks for access its sign-in did not grant | 7.4 |
| `mcp.sign_in_ask_raised`, `mcp.sign_in_ask_ended` | a sign-in ask is raised in a chat; it ends (its outcome) | 11.2 |
| `mcp.view_allowed`, `mcp.view_tool_call`, `mcp.view_message`, `mcp.view_link_opened`, `mcp.view_pinned`, `mcp.view_pin_refreshed` | a connected server's view: its reach allowed; a call it made (its outcome, and whether a click preceded it); a message it sent into the chat; a link it opened; a pin; each unattended pin refresh | 17.1 |
| `chat.continue_scheduled` (`{continues, at}`), `gateway.restart_drained` (`{waited_secs, finished, cut}`); `chat.turn_continued` gains `mode` | a timed Continue is set; a restart's drain ends; a continuation starts, full or inspect-only | 5.5, 5.8 |
| `work.redispatched` (`{work, kind, class, generation, why}`), `work.held_for_review`, `work.interrupted`, `work.run_again`, `subagent.steered`, `subagent.follow_up_started`, `job.started` (`{job, command_digest}`), `job.ended`, `job.input_written`, `liveness.stuck_input_stopped`, `liveness.dead_ended`, `keep_awake.changed` | what the work queue's pass did with each row; a helper steered or followed up; a background command's life; a stuck or dead agent CLI ended; keep-awake held or let go | 5.9 |
| `security.rule_added`, `security.rule_removed` | a standing rule saved or removed | 9.10 |
| `security.egress_host_allowed`, `security.egress_host_blocked`, `security.egress_host_removed` | a host answered from now on, with its origin; an entry removed | 9.11 |
| `secret.request_raised`, `secret.request_ended` | a secret request is raised in a chat; it ends (its outcome; never a value) | 11.2 |
| `channel.command_ran`, `channel.command_refused` | an audited command runs; a command is refused (`{channel, row, who, outcome}`, never arguments) | 17.14 |
| `memory.hold_kept` | the owner keeps a held memory change (its kind and writer, never its text) | 17.9 |
| `sandbox.fence_refused`, `sandbox.ran_outside`, `sandbox.restored`, `sandbox.hold_raised`, `sandbox.hold_acknowledged`, `sandbox.unavailable`, `sandbox.chat_outside`; and `egress_fetch` rows from the door `shell` | the fence refused or put back something; a call ran outside it; a chat's agent CLI runs outside it; each connection a fenced command makes | 17.8 |

The designs that add them write these other operations, each naming kinds, ids and counts and never content:
`approval.note_given`, `approval.drifted`, `access.granted`, `access.refused`, `chat.consulted`,
`chat.owner_action_run`, `tool_result.released`, `browse.field_filled`, `browser_submit`,
`config.live_widened`, `device.key_enrolled`, `device.key_mismatch`; `export.sent` by the trace exporter app;
`trigger.change_held`, `trigger.held_change_allowed`, `trigger.held_change_discarded`,
`trigger.revision_restored`, `automation.declined_call_answered`, `automation.standing_allow_given`,
`automation.standing_allow_revoked`, `automation.standing_allow_dropped`, `automation.away_paused`,
`automation.away_kept`, `automation.redispatched`; `inbound.chats_allowed`, `inbound.chats_withdrawn`,
`inbound.chat_started`, `skills.update_applied`, `skills.update_refused`, `skills.update_declined`,
`skills.revision_restored`, `skills.deleted`, `skills.folder_allowed`, `skills.folder_declined`,
`skills.folder_revoked`, `skills.folder_refused`, `skills.recording_started`, `skills.recording_drafted`,
`instructions.folder_allowed`, `instructions.folder_declined`, `instructions.folder_revoked`,
`instructions.folder_refused`, `packs.update_applied`, `packs.update_refused`,
`capabilities.install_suggested`, `capabilities.install_suggestion_refused`; `channel.settings_imported`,
`channel.listen_changed`, `channel.binding_changed`, `channel.home_changed`, `channel.held_ran`,
`channel.reply_resent`, `channel.send_refused`, `channel.sender_notes_changed`, `device.location_shared`;
`worktree.created`, `worktree.moved`, `worktree.parked`, `worktree.brought_back`, `worktree.discarded`,
`review.started`, `ways.started`, `ways.kept`, `ways.discarded`, `preview.opened`, `preview.closed`;
`secrets.hosts_changed`, `computer.set_up`, `computer.updated`, `computer.recovered`, `computer.reset`,
`computer.removed`; `agent.kept`, `agent.unkept`, `agent.portrait_changed`, `agent.handed_off`,
`agent.duplicated`, `agent.memory_changed`, `browse.sign_in_shared`, `browse.sign_in_unshared`,
`browse.signed_out`, `memory.agent_copy_given`, `memory.moved_to_yours`; `memory.record_forgotten`,
`memory.chat_shared_forgotten`, `memory.app_forgotten`, `memory.follow_up_accepted`, `sessions.read` for a
refused read; `update.installer_allowed`, `gateway.recovery_start`, `gateway.recovery_service_started`,
`gateway.loop_stalled`; `onboarding.offer_used`, `projects.setup_accepted`, `research.report_saved`, and
`chat.group_create`, `chat.group_update`, `chat.group_delete`, `chat.session_group`, renamed from the folder
rows; `voice.call_started`, `voice.call_ended`, `voice.call_request`, `voice.call_tool_refused`,
`voice.wake_word_armed`, `voice.wake_word_disarmed`, `voice.wake_word_detected`, and the event type
`egress_socket`.
A compaction, a reference and a restore write
no row: they change what the model reads, not what runs.

### 1.8 The SDK and named core features

Status: planned

Apps import core only through `personalclaw.sdk.*` (`tests/test_apps_import_boundary.py`). Every published
signature is checked in (`src/personalclaw/sdk/signatures.json`, `make sdk-snapshot`), and a change names the
apps it affects in its CHANGELOG headline. A new contract an app relies on is a **named core feature** an app
declares in `requiresCoreFeatures` (`src/personalclaw/sdk/features.py:1-75`, `apps/core_features.py`),
kebab-case, never a contract version bump. A core that does not know a feature an app declares refuses its
install by name. **A feature name leaves only with its contract**, in a change whose CHANGELOG names the apps
it refuses (decision 45): the refusal by name is the honest answer for an app that would call a function no
longer there. The features that exist only for a channel that runs its own conversation
(`turns-name-their-channel`, `turns-name-who-asked`, `chat-trust`, `tool-call-screen`, `digest-replies`,
`links-name-their-channel`, and the landed `pre-tool-hooks`) leave this way once no first-party app imports
their names. A design adds its row here in the change that ships the feature. The features this release
adds:

| Feature | What it names | Owner § |
|---|---|---|
| `reply-outcomes` | `MessageSourceProvider.send_reply` and `OwnerReplyDelivery.deliver_reply` answer a `ReplyResult` (`sent`, `drafted`, `not_sent`, `unknown`), and a source answers `reply_plan` | 6.4 |
| `reply-files` | `send_reply` and `deliver_reply` take `files` within the plan's `ReplyFileLimits`; core reads whether a source takes files from its `ReplyPlan.files`, never from the declaration | 6.4 |
| `tool-outcome-unknown` | `sdk.tool.outcome_unknown(what, check) -> ToolResult`: an app tool's answer that its tool part reads as `unknown` | 6.3 |
| `delivery-refused` | `sdk.channel.DeliveryRefused(reason)`, raised by a channel's delivery only when nothing was posted | 6.3 |
| `app-check-needs` | `personalclaw.sdk.cli.DoctorLine.needs` (`setup`, `sign_in`, `access`, `prerequisite`): an app Check's failing line says what it needs | 7.4 |
| `chat-patterns` | a channel app that renders approval prompts, whichever side runs the turn, offers the `chat_pattern` answer the brief offers and sends back its key | 9.7 |
| `folder-skill-layouts` | an app manifest's `skillFolders`: the folder layouts a repository's skills are read from | 15.3 |
| `compact-tools` | `register_acp_cli_entry(..., tool_delivery="compact")`: an agent CLI app takes the compact tool list | 7.6 |
| `client-setup-lines` | an app manifest's `clientSetup`: the lines its agent tool uses to add PersonalClaw's MCP server | its design's |
| `first-run-offers` | a model app's `provider.firstRun` declaration, read by core to offer the app at first run | its design's |
| `idempotency-keys` | `ToolDefinition.takes_key` for an app's tool, and `sdk.tool.idempotency_key()` | 6.7 |
| `provider-failures` | `sdk.model.ProviderFailure`, raised by a model app by reason | 17.16 |
| `history-replay` | `personalclaw.sdk.model.OUTCOME_UNKNOWN_IN_HISTORY`, the result a model app's wire puts where a restored history has none | 3.12 |
| `action-side-effects` | `ActionProvider.side_effects` (`none`, `idempotent_key`, `unknown`; unstated reads `unknown`) | 5.9 |
| `folder-instruction-files`, `app-relevance` | the instruction files an app's layout names; an app's relevance signals | its design's |
| `channel-facts` | `ChannelCapabilities`' new fields (`approval_prompts`, `choice_slots`, `native_commands`, `command_prefix`, `mentions`, `timestamps`, `location`, `private_drafts`, `send_keys`, `voice_replies`) and `ChannelMessage`'s (`mentioned`, `account`, `group`, `location`) | its design's |
| `channel-commands` | `register_commands` and `sdk.channel.register_command` | 17.14 |
| `channel-sends` | `render_reply`, `post_piece(..., key=)`, `find_sent` and `PieceReceipt` | 6.3 |
| `channel-render`, `private-drafts` | the shared formatter's pieces; `show_draft` | its design's |
| `review-findings` | `personalclaw.sdk.review` and its kit | 17.7 |
| `model-training-facts` | `BrandedProviderSpec.trains_on_inputs` and `register_type(..., trains_on_inputs=)` | 17.11 |
| `fenced-agent-cli`, `sandbox-traits`, `agent-computer` | `FenceDeclaration`; `SandboxTraits`; `ComputerProvider` | 17.8 |
| `deny-notes` | a channel's prompt offers Deny with a note and Deny and stop | 11.2 |
| `asks-person` | `app.json` `mcpMarks.asksPerson`: a person-only key another tool ecosystem uses, declared by the app that brings that ecosystem (an agent CLI's app; no stand-alone app declares one) | 8.3 |
| `persistence-paths` | an app's own rows of the persistence table | 17.8 |
| `turn-spans` | `sdk.traces.span_tree(session, message_id)`, with the app permission `turnSpans` and the platform event `turn.finished` | 17.4 |
| `child-sessions`, `dependency-waits` | an agent CLI app's declaration of its CLI's own helpers; `personalclaw.sdk.limits` | its design's |
| `account-recovery-screen` | `personalclaw.sdk.inbox.withhold_account_recovery(text) -> Withheld` | 3.3 (`withheld`) |
| `realtime-voice`, `wake-word` | an app that provides voice calls; an on-device wake word engine | its design's |

No `send-keys` feature is added: a channel declares its keys in `ChannelCapabilities.send_keys` under
`channel-facts` and receives them through `post_piece` under `channel-sends`.

### 1.9 Rails

Status: planned

A contract ships with the rail that holds it, in the same change, and a rail proves itself with a planted
violation that fails it (the habit of the CI contract table in #3909, and of the inventory's
`audit_home`). Each concept section names its rails in its invariants table. Three rules apply to every
rail here:

- A rail that scans the tree reads the shared parsed view #3927 builds once it exists, and runs from
  `tests/rails` (#3928).
- A rail asserting a closed vocabulary fails both ways: a producer emitting a word outside the set, and a
  word in the set no producer emits (the both-directions habit of `tests/test_wf_error_codes_registry.py`).
- A rail never weakens to make a red green; a red is root-caused or the change waits.

### 1.10 Naming

Status: planned

| Thing | Rule | Example |
|---|---|---|
| Core modules | singular snake-case nouns, one concept each | `turns.py`, `turn_recovery.py`, `readiness.py`, `approval_decision.py`, `revisions.py`, `place.py`, `chat_record.py` |
| HTTP paths | `/api/<noun>[/{id}[/<noun>]]`, lowercase, `-` between words in one segment | `/api/chat/sessions/{session}/turns/{message_id}` |
| JSON fields in core routes and frames | `snake_case` | `message_id`, `seen_through` |
| JSON fields in `app.json` and `miniapp.json` | `camelCase`, the manifests' existing convention | `requiresCoreFeatures` |
| Socket frame types | `lowercase_snake` | `turn_part`, `work_phase` |
| Ids minted by core | `<prefix>_<ULID>`: the prefix, an underscore, 26 lowercase Crockford base32 characters | `msg_01j9x…` (§2.1) |
| Closed vocabularies | lowercase snake words, one module owns each, a rail holds it | `ready`, `waiting_for_allow` |
| Product words the owner reads | one word per concept across every surface | "Always in this chat", "Everything in this chat", "Continue", "Unread", "Needs you", "groups", "group chat", "Home", "helper", "Not sent yet", "May not have reached <channel>", "withdrawn", "revision" |

"Folder" means a directory and nothing else once §13.4 lands (the chat list's and the artifact library's
organisation are both "groups"); "group chat" is a messenger's many-person conversation (Sender trust's
"groups" are renamed so; decision 46); "Home" is the owner's one conversation across their channels; "helper"
is the owner-facing word for a subagent on every surface (the agent's tools keep `subagent_`); "place" is
§13's resolved scope of a session; "posture" is §8's effective-permissions view; "readiness" is §7's;
"revision" is §10's (a workflow keeps its existing word "version" in its own UI, and §10.4 maps it).

### 1.11 The public-artifact rule

Status: planned

Everything published (code, comments, tests, fixtures, docs, the CHANGELOG, commit messages, issue and PR
text) uses product terms only. It names no other agent product a study compared PersonalClaw with, no internal
name of any organisation, no workspace path and no private planning reference: no theme number, no research
id, no plan key, no ledger row, no ruling. It describes the behaviour and its reason instead. The names of the
services a feature connects to (Slack, Teams, Google Calendar and Drive, Microsoft 365, Signal, WhatsApp and
the rest) are product facts and may be named where the feature is. Fixtures use invented names and the domains
RFC 2606 reserves. Public issue numbers of PersonalClaw's own repositories may be cited. This is a practice
carried while writing and re-read in every diff, never a list of words to catch: a list publishes what it
lists and is never complete (`AGENTS.md`, "Nothing published names what is private").

---

## 2. Shared identifiers and references

### 2.1 Ids

Status: planned

| Id | Format | Minted by | Unique within | Notes |
|---|---|---|---|---|
| Message id | `msg_<ULID>` | the client (`client_message_id`) or the gateway | the chat, for its life | a client id in another format is refused with `invalid_message_id` |
| Part id | `p_<ULID>` | the gateway | the chat | a tool part keeps the runtime's `tool_call_id` as a field, never as its id: runtime ids repeat across turns |
| Store event id | `e_<ULID>` | the writer | the chat's log | the `append_dedup` key |
| Chat grant id | `cg_<ULID>` | the gateway | the chat | a fork that carries grants gives each a new id |
| Recovery record id | `rec_<ULID>` | the gateway | the journal | |
| Incident id | `inc_<ULID>` | the gateway | the history | |
| Incident note id | `note_<ULID>` | the gateway | the history | |
| Trigger run id | `run_<ULID>` | the dispatch, before the action runs; a refused or skipped run's when it is recorded, wherever it is recorded (the run recorder, the reaper, the service, a refused fire) | the home | names one run in a work reference (§2.2); a row recorded before this release keeps its id |
| Delegated chat id | `dlg_<ULID>` | the gateway, when an External access client starts a chat | the home | the chat's key is `inbound:<client_id>:dlg_<ULID>` (§3.7) |
| Sign-in ask id | `sia_<ULID>` | the gateway | the home | §11.2 |
| View id | `mv_<ULID>` | the gateway | the home | a prepared view of a connected server (§17.1) |
| View pin id | `mvp_<ULID>` | the gateway | the home | a view pinned to Home (§17.1) |
| Start id | `boot_<ULID>` | `run_state.begin_start` | the home's run state | the start mark a boot refusal names (§16.3); a start refused at the home's claim makes none, so its record's `boot_id` is "" |
| Blank-slate id | `blank_<ULID>` | the blank-slate launch | the machine's blank-slate folder | names the root (§14.2) |
| Evidence record id | `ev_<ULID>` | the harness's record writer | the evidence store | §14.1 |
| Kept agent id | `ka_<ULID>` | the gateway, when the owner keeps an agent | the home | the record, thread binding, portraits, memory partition and sign-ins are keyed by it, so a deleted agent's are never a new one's |
| Work-queue row id | `wq_<ULID>` | `work_queue.accept` | the work queue | §5.9 |
| Background command id | `job_<ULID>` | `jobs.start` | the home | its work reference is `job:job_<ULID>` (§2.2) |
| Standing rule id | `rule_<ULID>` | the rules writer | the home | §9.10 |
| Access request id | `acc_<ULID>` | the gateway | the home | a folder or host an agent asks the owner for (§11.2) |
| Fill request id | `fill_<ULID>` | the gateway | the home | a field only the owner fills, which an agent asks for (§11.2) |
| Owner action offer id | `off_<ULID>` | the gateway | the chat | an `offer` part (§3.3) |
| Secret request id | `srq_<ULID>` | the gateway | the home | §11.2 |
| Channel address, binding, owed send, phone share | `addr_`, `bind_`, `snd_`, `shr_<ULID>` | the gateway | the home | the channel stores (§1.4) |
| Worktree, review, finding, ways group, page preview | `wt_`, `rv_`, `fnd_`, `tw_`, `pv_<ULID>` | the gateway; a finding through `personalclaw.sdk.review.new_finding_id()` | the home | §17.7 |
| Recording | `demo_<ULID>` | the gateway | the home | a recorded demonstration (§1.4) |
| Revision number | integer ≥ 1 | the definition's store writer, under its lock | the definition | revision 1 is the first stored form, or the backfill's |

One helper mints them all: `personalclaw/ids.py: new_id(prefix: str) -> str` (48 bits of milliseconds,
then 80 random bits, the random part counting up within one millisecond in one process, so ids sort by
creation), with `stable_id(prefix: str, at: str, seed: str) -> str` for an id read from a row that never
had one (the time part from instant *at*, the 80 bits from SHA-256 of *seed*, so the same row reads as the
same id before and after a conversion) and `is_id(prefix, value) -> bool`. `ids.PREFIXES` is this table's
closed set, plus a prefix another release allocates (the mini apps' `ma`). If the mini apps (#3950)
ship their own ULID minting for `ma_` ids first, that helper becomes this one; there is one. A rail fails
on any other ULID minting in `src/`, and on a string built from a `PREFIXES` member and an underscore
outside `ids.py`. Tooling outside `src/` (the harness's record writer) imports the same helper. An id used
in a path goes through `record_ids`.

### 2.2 Work references

Status: planned

A **work reference** names one piece of live or finished work across surfaces: `<kind>:<id>`. The kinds
are closed:

| Kind | Id | Example | Today |
|---|---|---|---|
| `chat` | the chat key | `chat:chat-12-1759561200` | sessions are keyed by name (`dashboard/state.py:292-305`) |
| `loop` | the loop id | `loop:l-…` | |
| `workflow` | the workflow run id | `workflow:r-…` | `ScheduleRun.work_id` already writes `workflow:<id>` (`schedule_history.py:127-130`); `workflows/ownership.OWNED_PREFIX` |
| `subagent` | the subagent id | `subagent:…` | `ScheduleRun.work_id` writes `subagent:<id>` |
| `trigger` | a trigger run's id (§2.1) | `trigger:run_01j9…` | `ScheduleRun.run_id` (`schedule_history.py:88`) |
| `webhook` | the webhook turn's id | `webhook:…` | #3889 lists webhook turns as live work |
| `job` | a background command's id (§2.1) | `job:job_01j9…` | none: background commands are new (long-running work) |

A trigger run's id is minted at dispatch, before its action runs, by `ids.new_id("run")`, and handed to
the action (`ActionContext.run_id`) and to the run recorder, which no longer mints one; a refused or
skipped run's is minted by the same helper when it is recorded, wherever it is recorded. So one id names
one run across the home, and the run's identity exists while its work runs (for spend attribution and a
browse run's replay folder), and the run scope a per-run cap binds is keyed `trigger:<run id>`. Today four
places mint `fire-<ms>`, `manual-<ms>`, `refused-<ms>` or `<status>-<ms>` after the fact
(`triggers/run_record.py:320`, `:420`, `triggers/reaper.py:651` and the service), so two
automations firing in one millisecond share an id; every read today keys a run by its automation too
(`schedule_history.get_run(job_id, run_id)`), but a work reference names the run alone. How a run started
is the row's `source` (`triggers/run_source.py`, landed: `you`, `agent`, `app`, `automation`, `program` and
the fire kinds), and `trigger` stays its typed exit.

One parser, `personalclaw/workref.py`: `parse(name) -> WorkRef` (its `kind` and `id`), with
`work_ref(kind, id) -> str` to build one, refuses any other kind and an empty id, and keeps the colons
in an id. Every producer builds through `work_ref` and reads through `parse` (or `read`, for a reader
with a fallback, which answers no kind and no id where `parse` refuses). #3833's `work` field, #3888's
item owner, #3889's rows, §4's run state, §5's journal, §16.1's suppressed references and §14's evidence
all use this form. #3833 shipped the module first, so its spelling is the one: `WORK_KINDS` is this
table's kinds, `job` among them.

### 2.3 Times

Status: planned

Every new stored or served time is a UTC instant written by `personalclaw.instants.utc_now_iso()` or
`utc_iso(epoch)` (`src/personalclaw/instants.py:31-38`), shaped `2026-10-04T15:17:09.123456+00:00`. A new
field on an existing record imitates that record's representation: `ScheduleRun` stores epoch floats
(`schedule_history.py:98-100`), `update_run.json` writes `recorded_at` as one (`self_update.py:402-413`)
and an Inbox row keeps epoch floats, so their new time fields are epoch floats too; a route that serves
one of them still answers a new time field as an instant. Never a zone-less local reading.

### 2.4 Digests

Status: planned

Two canonical JSON forms exist today, and this release adds no third:

- **The compact form**, for every digest a yes is bound to: SHA-256 hex of `json.dumps(value,
  sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)` encoded as UTF-8. Both fixes
  that landed before this release use it: the workflow version digest an automation's Allow records
  (`workflows/versions.digest`) and a tool definition's digest (`mcp_read_only_trust.digest` over `name`,
  `description`, `inputSchema`, `annotations`). This release gives it one home,
  `ledger.hashing.canonical_digest(value)`, and those two become calls to it, byte for byte, so no
  recorded digest changes. Every new digest of this release uses it: a revision's (§10), a receipt's body (§3.9), a
  folder skill's seal (§15.3), a skill update's consent (§15.2), a view's and a pin's seal (§17.1), a
  browse submission's one-time allowance (§6.3), an uninstall plan's, an idempotency key (§6.7), a cut
  turn's posture digest (§5.3), a helper's result digest (§5.9), a work row's `params_digest` (§5.9) and a
  standing rule's key in its book (§9.10).
- **`stable_json`** (`src/personalclaw/ledger/hashing.py:16-22`: sorted keys and the JSON library's
  default separators) keeps hashing what it hashes today: resume caches and idempotency checks.
  `ledger.hashing.hash_value` truncates it to 16 characters and is for cache keys only, never for anything
  an owner's yes is sealed to.
- A file's or a bundle's digest is the SHA-256 of its bytes (the app bundle digest,
  `apps/disclosure.bundle_digest`, `apps/disclosure.py:132`; a skill's install record's per-file hashes).
- **A skill's digest over its files** is the landed `skills/marketplace.files_digest`: the SHA-256 of its
  sorted `<path>\0<sha256>` lines. It stays as built, never `canonical_digest`, because the
  recorded digests of versions that shipped before install records were kept depend on it
  (`skills/shipped.EARLIER_VERSIONS`); the record keeps the per-file `sha256` map and derives the digest,
  never storing it (§15.1).
- The owner's yes to content is sealed with `owner_grants.seal(content)` (`src/personalclaw/owner_grants.py:34-37`).
- The operator ceiling keeps its 16-character digest (`guardrails/ceiling.py:650`) as it is: it is a
  tamper-evidence stamp, compared only with itself.

A rail fails on a `sha256` of a `json.dumps(... separators=(",", ":"))` outside `ledger/hashing.py`.

### 2.5 Who: the author of a definition's change

Status: planned

Every revision (§10), every chat grant (§9) and every installed capability's record (§15) names who made
it with one closed vocabulary (decision 21). It is the one the workflow-version fix defined for a saved
version (`workflows/versions.SAVERS`, landed), set by the door a change came through and never by
what that door was handed; this release moves it to `personalclaw/revisions.py` as its one home (`AUTHORS`, the
constants `OWNER` … `BROUGHT_IN`, `UNKNOWN = ""`, `require_author`) and widens each word's meaning from
workflows to every definition:

| Author | Who | `ref` holds | Maps today's |
|---|---|---|---|
| `owner` | the owner, at their own door: an editor, a Settings page, the CLI | `dashboard` or `cli` | a workflow definition's `provenance: "user"` (`workflows/native_defs.py:99`, `workflows/handlers.py:411`) and a version's `SOURCE_USER` (`workflows/versions.py:35`), each only where the owner's own door saved it |
| `publish` | the owner's switch that publishes a workflow, which changes no step | `dashboard` | workflows only |
| `agent` | an agent's tool call: a chat turn, a subagent, a workflow step (a batch of helpers saves no definition, so writes no revision: landed) | the session key | `provenance: "chat"` (`workflows/service.py:364, :443`) |
| `refiner` | PersonalClaw's own learning passes: an accepted refiner proposal, the skill curator's change | the pass's name | `SOURCE_REFINER` (`workflows/versions.py:36`); `skills/curator.py:178, :202` |
| `import` | brought in from outside and accepted by the owner: a prompt card, an upstream update of an installed capability | the source pointer | `packs/update.py` |
| `app` | an app: a template it provides, a save made with its token | the app's name | |
| `shipped` | a template or default PersonalClaw ships, as the installed release has it | the release (`personalclaw.__version__`) | |
| `brought_in` | a definition no door here saved: another home's sync, a restore, a pack | the source (`sync`, `restore`, `pack:<name>`) | `triggers.store.arrived_from_another_home` (`triggers/store.py:540-557`) |
| `""` | unknown: recorded before the history said who saved each one, or written by a backfill | the backfill's name, or nothing | |

Every revision record carries its author's `ref`, a workflow version's included: `VersionRecord` gains
`ref`, set by each door that saves. Records are served with `by_words`, `ref_label` and `ref_link` from
`revisions.author_words` ("changed by an agent in “Plan the trip”", "changed by you in the editor"); no
page keeps a table of author words.

The owner's own change is consent; every other author's change to something the owner allowed waits for
the owner's yes before it widens what runs (§10.5). An installer that applies the owner's Yes to a proposal
(a held memory change, an inferred follow-up's reminder, a lesson's replacement) is the owner's door: a definition
it makes or changes is written `by: owner`, `ref: dashboard` (an Inbox answer) or `cli`, never as the pass
that proposed it. A data row's actor in a mini app (`page`, `owner`,
`agent`, `automation`, `import`, `migrator`, `merge`, `platform`) is the mini apps' separate vocabulary
for data changes, not definitions, and stays its own.

---

## 3. The typed turn and sent-turn receipts

### 3.1 What it is

Status: planned

A chat is an ordered list of messages. A **user message** starts a turn; an **assistant message**
answers it. A message owns ordered, typed **parts**, and a tool call is a part whose state moves through
one state machine. The store keeps append-only events; the gateway folds them into messages, and the
dashboard folds the live frames into the same messages, so a reload shows what the live page showed by
construction.

### 3.2 Today: what it replaces

Status: planned

- A transcript line is `{role, content, cls, ts, source_*, meta}` (`dashboard/state.py:581-611`). Roles
  in use: `user`, `assistant`, `tool`, `permission`, `error`, `notice`, `system` (the stop card and
  "Prompt blocked", `dashboard/chat_handlers.py:1350`, `chat_runner.py:2981`), `queued`, `nudge`,
  `inject`, `subagent`, `file` (a file a tool delivered, written into the most recently active chat and
  rendered nowhere), and the live-only `streaming` entry (`state.py:676`). The rows that start a turn are
  `user`, `inject`, `subagent` and `nudge` (`chat_persistence.py:1117`).
- `cls` is a CSS class string, except on permission rows, where it is JSON (`chat_runner.py:4512,
  :4625`) resolved by its one writer (`approval_state._mark_permission_resolved`,
  `approval_state.py:41-65`), on queued rows (`queue_id`, `dashboard/handlers/messaging.py:858-860`) and
  on `inject` rows (`{"cronLabel"}`).
- A tool row's `meta` is created at the call and changed in place after a reverse scan for its
  `tool_call_id` (`chat_runner.py:3857-3880`): "running" means "no `done`", "failed" means `ok: false`.
- The live page builds a turn from frames (`web/src/pages/chat/liveToolFrames.ts`) and a reload from rows
  (`hydrateTurns`, `web/src/pages/chat/chatTypes.ts:634`), held equal by
  `web/src/pages/chat/liveAndReloadAgree.test.tsx`; the session map rebuilds the marks a third time
  (`dashboard/chat_session_map.py`). The frontend's segment union is `text`, `tool`, `approval`,
  `question`, `activity`, `error`, `thinking` (`chatTypes.ts:14-172`); thinking "is a live-stream
  affordance, not part of the record" (`:165-170`).
- Every save rewrites the whole file from the buffer, the chat's record as its first line
  (`chat_persistence.py:788-1075`; `docs/architecture/chat-sessions.md:428-438`); a field missing from
  the record builder is clobbered at the next save (`chat_persistence.py:957-959`). Every role's text but
  a user's and a system row's is redacted as it is written (`:1045-1050`).
- A runtime's clear empties the buffer in memory and appends "Conversation cleared." (`chat_runner.py:4884-4887`);
  the overwrite guard then refuses a shorter save unless forced (`chat_persistence.py:837-908`). Undo
  (`chat_undo.py`, `POST /api/chat/sessions/{session}/undo`) truncates the history in memory and in the
  file; an edit-and-replay keeps the replaced tail on the edited message as its `rewound` chain
  (`chat_regenerate.py:281-306`).
- `POST /api/chat` (`chat_handlers.py:133-440`) takes `message`, `session`, `agent`, `queue_mode`
  (`:298-307`) and `meta`, whose `client_ts` the page matches its optimistic row by (`:159-172`); it has
  no idempotency key. A pick in the composer rides `meta.files` (an upload, or a workspace file the owner
  mentioned), `meta.knowledge` or `meta.artifacts`. WS mode answers `{ok, session}` at once
  (`:419-431`), a steer `{ok, steered}` (`:323`), a queued message `{ok, queued}` (`:339`); SSE mode
  streams (`:433-437`). A steer is handed to the runtime and recorded nowhere. Stop is chat-wide
  (`:1278`). The A2A door already makes a client's `messageId` its caller key (`inbound/a2a.py:35-37,
  :611-622`). `personalclaw run --format json` prints `"turns": 1` and pairs no tool result with its call
  (`cli_run.py:646-662`), marking the last call failed when a result contains "denied" (`:443-449`).
- How a turn ended is a closed set, `complete`, `stopped`, `error`, `interrupted`
  (`chat_runner.py:250-256`; `terminal_outcome_for_turn`, `:278-308`).
- The rule closest to "a completed side-effecting call": a turn whose agent connection dies after it
  made "calls nobody refused" is never resent on its own (`_steps_made`, `chat_runner.py:2609-2611`;
  `turn_endings.lost_after_steps_notice`, `dashboard/turn_endings.py:83-92`;
  `docs/architecture/chat-sessions.md:764`).

### 3.3 The message and its parts

Status: planned

**Message.**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | str | yes | §2.1 |
| `session` | str | yes | the chat key |
| `role` | `user` \| `assistant` | yes | closed |
| `dispatch` | `message` \| `inject` \| `subagent` \| `goal` \| `continue` \| `review` \| `ways` \| `job` \| `call` | user | what started the turn: a person's message (`source` says whose); an automation's or a scheduled note's injection; a helper's report; a goal loop's cycle in the chat it works in (`goal`, replacing Auto-nudge's `nudge`: goal loops replace Auto-nudge); a continuation (§5.5); a review of the owner's changes (`review`); the owner's message tried several ways at once (`ways`: its reply is one `ways` part, settled at once, and the ways run in chats of their own); a background command's report (`job`); a voice call (`call`: the owner held a call; the message starts no turn, has no reply and carries one `call` part) |
| `input_kind` | `owner` \| `person` \| `helper` \| `automation` \| `system` | user | whose words started it, set by the door from the spawner and never from text: the owner's own; someone else's (a channel's door sets `owner` or `person`); a helper's report; an automation's; PersonalClaw's own (a continuation, a goal cycle, a scheduled note, a background command's report). A kind but `owner` renders under a fixed header that gives its words no authority |
| `parent_id` | str | assistant | the user message it answers |
| `attempt` | int ≥ 1 | assistant | 1 for the first answer to its parent; Retry and Regenerate add an attempt, never delete one |
| `shown` | bool | assistant | exactly one shown attempt per parent; the variant switch moves it |
| `ts` | instant | yes | when the message was created (for a user message, the gateway's accept time) |
| `settled_at` | instant \| null | assistant | when the turn ended; null while it runs |
| `outcome` | `complete` \| `stopped` \| `error` \| `interrupted` \| null | assistant | today's closed endings; null while it runs |
| `source` | `{source_thread, source_user, source_channel, source_address}` | yes | where it came from, with today's field names (`turn_source.SOURCE_FIELDS`), recorded once when the message is taken in; `source_address` is the channel conversation's address (`addr_…`), so a reply goes back where it was asked; who asked for the turn is `turn_source.asked_by(source)` |
| `agent` | str | assistant | the agent that answered |
| `model_ref` | str | assistant | the model that answered (`"<entry>:<model>"`, as `ModelProvider.served_ref` stamps it) |
| `usage` | Usage \| null | assistant | the turn's usage (§17.4) |
| `error` | `{code, message, retryable: bool, settings: str, reason: str, retry_at: instant \| ""}` \| null | assistant | the turn's terminal error; `code` is a wire code (`private_chat_model_trains`, `step_limit_reached` and the rest); `settings` the Settings route the failure is fixed on, or ""; `reason` the provider-failure record's (§17.16) when a provider failed; `retry_at` only when the provider said when |
| `facts` | MessageFacts | no | below |
| `parts` | [Part] | yes | ordered |

**MessageFacts** (closed keys; each optional):

| Key | On | Type | Meaning, and where it lives today |
|---|---|---|---|
| `input_origin` | user | `typed` \| `voice` \| `call` | `meta.input_origin` (`chat_handlers.py:187-190`); `call` for a request made in a voice call, told apart structurally, never from its text |
| `own_words` | user | str | `own_words.OWN_WORDS` |
| `ran_prompt` | user | `{name, text}` | `own_words.RAN_PROMPT` |
| `original` | user | str | the text the owner typed when the message sent is a rewritten prompt: `meta.original` |
| `ui_label` | user | str | the label a widget action's message is shown by: `meta.ui_label` |
| `injected_label` | user | str | the automation or job a `dispatch: inject` message is shown with: an `inject` row's `cls.cronLabel` |
| `pastes` | user | `[{seq, lines, content}]` | `meta.pastes` (`chatTypes.ts:352`) |
| `skills_used` | user | `[{name, state, loaded_tokens}]` | rides the message that started the turn (`chatTypes.ts:194-200`; `state.py:541-547`) |
| `image_delivery`, `image_delivery_reason` | user | `{<name>: image \| text}`, str | `meta.image_delivery` (`chat_runner.py:1441`) |
| `receipt` | user | `{accepted_at, disposition, body_digest}` | none (§3.9) |
| `continues` | user | message id | none (§5.5) |
| `merged` | user | `[{message_id, source}]` | the queued messages one turn ran, on the message a merge drain made: `meta.merged` (`meta.queued_from` on a row written before it) |
| `steered_into` | user | message id | on a steer, the user message of the turn it joined (the landed steer fix writes a steer row where the turn took it) |
| `after_step` | user | int | on a steer, the step it joined after; on a compaction part's turn, where it sat (§3.6) |
| `caused_by` | user | `{work, message_id}` | on a turn the owner did not type (a helper's report, an inject, a goal cycle, a continuation, a trigger's run, a door's turn): the work and message that caused it, set from the spawner's running turn (`turn_lineage.current()`), never from a tool argument |
| `root` | user | `{session, message_id}` | the turn at the top of that lineage; a root's `root` is itself; usage rows carry it (§17.4) |
| `mode` | user | `full` \| `inspect_only` | on a continuation (§5.5) |
| `goal` | user | `{loop, cycle}` | on a goal cycle's message |
| `review`, `ways`, `comments`, `fix` | user | `{scope, focus}`; `{group}`; `{count, kinds}`; `{review, findings}` | a review's scope; a ways group; a message composed from the owner's comments; a message asking to fix findings (§17.7) |
| `call`, `heard` | user | message id; str (at most 1,000 characters) | on a call request: the call message; what the owner said, transcribed |
| `from_outside` | user | `{source, source_type, source_id, what}` | words taken in from outside (the record `turn_source.FROM_OUTSIDE` keeps on a row today), and an imported conversation's messages, so a restore passes them through the outside-text door again |
| `asked_for_by` | user | `{source_thread, source_user, source_channel}` | on a helper's report: whoever asked for the work it carries on, when it was not the owner (`turn_source.ASKED_FOR_BY`, which `turn_source.asked_by` reads) |
| `input_flagged` | user | bool | the door's screen flagged the words that started the turn (`outside_text.Admitted.flagged`): no setting answers its calls |
| `memory_citations` | assistant | as today's | `chat_runner.py:1113` |
| `context_fed` | assistant | `{kind: context \| context_without_memory, text}`: the footer line's kind and sentence | `chat_session_map.CONTEXT_FED_KEY` (`chat_session_map.py:122`) |
| `learned` | assistant | `[{origin, text, ref, state: kept \| waiting \| not_kept, applies?: always \| on_topic, retired?: [{key, rule, event_id}], why?}]` | `chat_session_map.LEARNED_KEY` (`:115`); `ref` names the held change when `state` is `waiting` (§17.9); the converter reads an old item as `state: kept` |
| `turn_telemetry` | assistant | as stamped today | `chat_session_map.TURN_TELEMETRY_KEY` (`:87`) |
| `summary` | assistant | str | `chat_session_map.TURN_SUMMARY_KEY` (`:94`) |
| `finish_reason` | assistant | `length` \| `incomplete`, or absent | `chat_session_map.FINISH_REASON_KEY` (`:101`); `incomplete` for an answer cut off before its provider finished it (the landed `AnswerCutOff`) |
| `model_substitution` | assistant | str: the substitution's sentence | `chat_session_map.MODEL_SUBSTITUTION_KEY` (`:109`) |
| `file_changes` | assistant | `[{path, before, after}]` | the files the turn's completed calls changed, for the change chip: `meta.file_changes` |
| `step_models` | assistant | `[{from_step, model_ref}]` | each step's model when it changed mid-turn; `model_ref` is the last step's |
| `read_untrusted`, `read_private` | assistant | [str] | the sources of outside text and private data the turn's results read, cleared at the owner's next message |
| `handed_off` | assistant | `{agent, work}` | the reply is a handed-off helper's answer (§3.12) |
| `checkpoint_turn` | assistant | int | the turn's rewind checkpoint |
| `checks` | assistant | `{changed, ran_after: [{command, kind, outcome}]}` | the files it changed and the checks that ran after its last change (§3.5's `code_checks`), absent on a turn that changed nothing |
| `instructions` | assistant | `[{kind: standing \| folder, path, digest}]` | the instructions the turn loaded |

The keys keep today's names, so `chat_session_map`'s stamps become fact writes and its readers read
`facts`. **A steer** is a user message with `dispatch: "message"`, `facts.steered_into` and
`facts.after_step`: it starts no turn and has no reply, it shows inside the turn it joined ("You steered:
…"), and a repeated steer is found by its id (§3.9). It is written only when the runtime takes it (the
landed steer fix: the runtime says so with `EVENT_STEER`, and `running_turn.take_steer` writes the row); a
steer the turn never takes is requeued in the owner's own words and written once, when it runs. A steer carries
the owner's pastes (`facts.pastes`) to the running turn as a message does (landed: every row a message becomes keeps
its pastes, and a send that still holds a paste's marker beside its block is refused, `400
paste_not_expanded`). **A merge drain**
makes one user message whose `facts.merged` names each queued message it took. **A handoff** (decision 42)
is a user message whose one agent `mention` part resolved `handed_off`: the chat's own agent does not
answer it; a helper of that agent does (§3.7, §5.9), and its answer is the turn's reply, an assistant
message with `facts.handed_off`. The agent pill, not a mention, switches a chat's agent.

**Every part** carries `id` (§2.1), `kind`, `at` (created) and `updated_at`. The kinds are closed:

| Kind | On | Stored |
|---|---|---|
| `text` | both | yes |
| `reasoning` | assistant | **no**: live only (decision 5) |
| `tool` | assistant | yes |
| `approval` | assistant | yes; its brief is §11.4's |
| `question` | assistant | yes |
| `sign_in` | assistant | yes |
| `access` | assistant | yes |
| `offer` | assistant | yes |
| `notice` | assistant | yes |
| `file` | both | yes: the files a turn made, and the owner's uploads |
| `mention` | user | yes |
| `plan` | assistant | yes |
| `helper` | assistant | yes; #3820's segment reads it |
| `compaction` | assistant | yes |
| `review` | assistant | yes |
| `ways` | assistant | yes |
| `call` | user | yes |

The owner's uploads (a file they attached, a screen capture) are `file` parts with `direction: in`; a pick
from the capability index (a workspace file, a knowledge item, a prompt, an artifact, a skill, a tool, an
app, an agent) is a `mention` part (decision 31). Each kind's builder is one function in
`turn_builder.py`, added by the change that adds the kind; `turns.PRODUCED_KINDS` lists the kinds with a
builder, which the both-ways rail reads.

**`text`**: `text: str`; `step: int`, the native step (inference) that wrote it, from 1, absent on an agent
CLI's turn, whose runtime keeps its own history, and on a row recorded before steps were. The record has
one step counter: the runtime reports it (`AgentEvent.step`), a tool part's `step` and a steer's
`facts.after_step` are the same number, and a compaction's coordinates read it (§3.3 `compaction`). Per native
step, its text parts joined in order are exactly the assistant words the runtime's history holds for that
step; a mechanism that would show words the history drops (a retried try's words, an overlap joined away at
a seam) adds, with itself, `held: false` on those parts, which a restore leaves out. No such field exists
before a mechanism needs it.

**`reasoning`**: `text: str`. Sent as deltas while it streams (§3.8), never written to the store and never
served by the detail route, as thinking is today.

**`tool`**:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `tool_call_id` | str | yes | the runtime's id, redacted as today (`chat_runner.py:3500-3504`) |
| `tool` | str | yes | the stable tool name. An MCP tool's is `mcp/<server>/<tool>`, as the MCP tools app names it (`apps/native/mcp-tools/provider.py:26, :64`); its readiness subject is `mcp:<server>/<tool>` (§7.3) |
| `title` | str | yes | the display title, masked |
| `detail` | str | no | one-line summary: the command, the file and range |
| `family` | Family | yes | §3.3.1 |
| `tool_kind` | str | no | an ACP adapter's kind; legibility only, never widens a gate (`chat_runner.py:4086-4092`) |
| `via` | str | no | `tool_run` when the call was made through the compact tool list's facade (§7.6); the part then records the inner call, its name in `tool` and its input in `input`, which is the call that was decided, shown and audited |
| `input` | str | yes | the masked input string (`tool_input_to_str`, then `redact_field`) |
| `input_obj` | object \| null | no | the structured input, where the runtime gives one: a native call's arguments as the model sent them, masked, whole up to 256 KiB |
| `input_cut` | bool | no | `input_obj` was cut at 256 KiB |
| `step` | int | no | the native step that made the call, as a `text` part's |
| `purpose` | str | no | |
| `risk` | `safe` \| `caution` \| `destructive` \| `unchecked` | yes | the effective risk at call time (`task_modes.read_call`, `task_modes.py:354`) |
| `effect` | `read` \| `write` | yes | `read` exactly when `task_modes.reads_only` (`task_modes.py:110-125`) held at call time, which is `risk` being `safe`: the tool declares it only reads, or its shell command was screened read-only. Every other call is `write`, a tool that declares nothing included (decision 23). Fixed when the part is created, never recomputed |
| `state` | §3.4 | yes | |
| `why` | `stopped` \| `steered` | on `cancelled` | the turn was stopped, or the model took a steer before the call ran |
| `decided_by` | str | once it ran or its ask was answered | §8.2's grant word that let it run, or `you` / `nobody` for an ask |
| `refused_by` | str | when a gate refused it | §8.2's gate word |
| `refused_rule` | str | when a gate refused it | the rule, in words |
| `grant_id` | str | no | the chat grant that approved it (§9) |
| `approval_part_id` | str | no | the approval part that asked about it |
| `started_at`, `ended_at` | instant \| null | yes | |
| `result` | ToolResult \| null | no | |
| `error` | `AgentError.to_dict()` \| null | no | on `failed` and `unknown` |
| `readiness` | Readiness \| null | no | when it failed with `ERR_CAPABILITY_NOT_READY` (§7.6) |
| `view` | `{server, resource_uri}` \| null | no | set when the call is made, for a tool whose definition names a view (§17.1), so the view renders after a reload even if the tool's definition changed since |
| `ungated` | str | no | the sentence for a call an agent CLI ran without asking |
| `notes` | [str] | no | step notes (`dashboard/step_notes.py`) |
| `idempotency_key` | str | no | the key core bound for a call to a tool that takes one (§6.7); "" otherwise |
| `key_honored` | bool | no | the writer that received the key recorded that its receiving side drops a repeat of it (§6.7) |
| `replay` | `{model_ref, data}` \| null | no | the per-call data a model app asks to have echoed back (a signature on a tool call), with the model that produced it; never served; a rebuild on another model leaves it out |
| `fence` | Fence \| null | no | how the call's command was held (§17.8): `{fenced, fence: full \| outside_only \| none, backend, why_not, network: all \| proxy \| none, refused: [str] (≤ 5, masked), hosts_refused: [str] (≤ 3), restored: [str] (≤ 10), outside_fence: bool}`; null for a call that runs no command |

**ToolResult**: `text` (str: what the model received, fenced where the gateway fenced it, kept whole:
projection bounds it at 60,000 characters, the raw kept under `raw_ref`, plus the loop breaker's notes, and
the record never cuts it; a card strips the fence for a person, `toolRenderers/registry.tsx:67-74`),
`received_length` (int | null: the characters the model received; null on a converted row), `content_type`
(str), `raw_ref` (str), `truncated` (bool), `original_length` (int | null), `structured` (object | null: an
MCP tool's structured content; a tool that made a file puts `made: {slug, version, kind, name, size, mime}`
here, never in prose), `blocks` (`[{kind: image | audio | resource_link | resource, name, mime, size, ref:
{kind: attachment | artifact | uri, id}}]`: non-text content kept as references), `recovery_hints`
([str]), `withheld` (below, or null).

- **What the model was sent, once** (decision 34). Where the model was sent a one-line reference to an
  identical earlier result, or to a skill it already holds, instead of the result, `text` keeps
  the full result and a restore computes the reference again; that is the one place `text` differs from
  what the model was sent.
- **Served forms.** Frames, the detail route and the turn document carry the first 8,000 characters of
  `text`; `GET /api/chat/sessions/{session}/parts/{part_id}` serves the whole part (§3.9).
- **`withheld`** (decision 43): `{by: screen | policy | account_recovery, reason, held_ref}`.
  - `by: screen` or `policy` holds the whole result: its `text`, `structured` and `blocks` are kept in the
    chat's tool-result store under `held_ref`, and none reaches a model (its images join no turn, its block
    lines are not written). The model is told "This result was withheld: {reason}. The owner can release
    it." The part shows Withheld and the reason. Release, the owner's only, is `POST
    /api/chat/sessions/{session}/tool-results/{held_ref}/release` (`404 held_result_not_found`); the next
    turn is told "The owner released the result of {tool}:" with the result through the outside-text door,
    still fenced (`tool_result.released`).
  - `by: account_recovery` is the account-recovery screen's inline hold: what looked like a one-time
    sign-in code, a password-reset link or a sign-in link is replaced in `text` by a marker the owner reads
    ("[withheld: …]"), the rest reaches the model, `held_ref` is "", and nothing releases it: the owner pastes
    what they want used.
  - An MCP result takes the shape. A tool-result door that fences without screening screens
    through `outside_text.admit`, and a result it refuses is withheld.

**`approval`**:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `request_id` | str | yes | the approval registry's id |
| `ask` | `call` \| `owner_allow` | yes | `owner_allow` for an ask through `workflows/owner_allow.ask` (a batch start, a workflow save, a mini app's bulk write), answered alone |
| `tool_part_id` | str | yes | the tool part it asks about; "" for `owner_allow` |
| `brief` | ApprovalBrief | yes | §11.4, as shown |
| `answers` | [ApprovalAnswer] | yes | what the card offered: `channel_delivery.ApprovalAnswer` (`{key, label, ends, word, promise}`, `channel_delivery.py:61-90`), with `pattern` on a `chat_pattern` answer (§9) |
| `queued` | bool | yes | asked by work this chat started (`ApprovalSegment.queued`, `chatTypes.ts:83-86`) |
| `grant_agent` | str | no | |
| `deny_effect` | str | no | |
| `state` | `pending` \| `approved` \| `rejected` \| `expired` \| `cancelled` | yes | `pending` plus the four endings (`approval_state.py:75-86`) |
| `answer` | str | no | the answer key chosen (`approved`, `trust`, `chat_pattern`, …) |
| `decided_by` | `you` \| `nobody` | once settled | |
| `grant_id` | str | no | the chat grant its answer made |
| `ended` | str | no | why it ended with no answer |

**`question`**: `question_id`, `tool_part_id` ("" when the call is not one the chat shows), `questions`
(as `owner_questions` defines them today), `asked_by`, `answerable` (bool), `note`, `state` (`pending` |
`answered` | `skipped` | `expired` | `cancelled`), `answers`, `ended` (`QuestionSegment`,
`chatTypes.ts:102-121`).

**`sign_in`**: `request_id` (the sign-in ask's `sia_` id, §11.2), `need` (`sign_in` | `more_access`: a
missing scope is the same card with other words; not `kind`, which every part carries as its own), `subject` (a readiness subject, §7.3), `label` ("Sign
in to <server>", "Allow <server> more access"), `scopes` ([str]: for `more_access` the added scopes, else
`[]`), `tool_part_id`, `state` (`pending` | `signed_in` | `skipped` | `expired` | `cancelled`). Saved at
each state change, beside the waiting tool part.

**`access`**: `request_id` (the access request's `acc_` id, §11.2), `folders` (`[{path, access:
read | read_write}]`, as asked), `hosts` ([str], as asked), `why` (the agent's words), `granted`
(`{folders, hosts, lasts: turn | chat}` | null: what the owner allowed, all, part or none), `tool_part_id`,
`state` (`pending` | `granted` | `refused` | `expired` | `cancelled`). Saved at each state change, beside
the waiting tool part.

**`offer`**: `offer_id` (`off_…`), `action` (one of `owner_actions.ACTIONS`: `commit_changes`,
`push_branch`, `save_artifact`, `switch_to_agent`), `sentence` (the gateway's words for the button),
`state` (`offered` | `ran` | `moved`). Offering changes nothing; the click is the owner's own act, run as
the owner (`decided_by: you`) after the gateway re-reads the facts (`409 owner_action_moved` when they moved).

**`notice`**: `notice` (closed: `stopped`, `cut_short`, `interrupted`, `lost_after_steps`, `retrying`,
`agent_switched`, `model_switched` ("Next step on <model>."), `carried_on`, `prompt_blocked`, `continued`,
`output_continued` ("Continued past the output limit once."), `context_full` (the page offers a new chat
from the summary on it), `info`), `text`, `level` (`info` | `warn`), `settings` (a Settings route
id, or ""). Today's `notice`, `system` and non-terminal `error` rows become notices; a turn's terminal
error is `Message.error`.

**`file`**: `name`, `mime`, `size` (int), `ref` (`{kind: attachment | artifact | path, id}`; `path` only
for a path the gateway resolved: a file-scope path, or a file in the outbox a tool delivered), `version`
(int | null: an artifact ref's version, the one the turn made or the owner attached), `direction` (`in`:
the owner uploaded it; `out`: the turn produced it). An output file part sits on the assistant message
after the tool part that made it, one per artifact per turn, saved again in place when a later call of the
turn makes a newer version; a file a tool delivered to the owner becomes a part on the calling chat's
running turn, never on another chat's. A turn of a chat linked to a channel conversation posts its output
`file` parts into that conversation after its reply's send record settles, for the owner's own turn on a
channel that declares `attachments`; each upload is one §6.3 channel delivery, never retried.

**`mention`**: `target` (closed: `file`, `knowledge`, `prompt`, `artifact`, `skill`, `tool`, `app`,
`agent`), `ref` (a path relative to the chat's folder, a knowledge id, a prompt name, an artifact slug, a
skill name, a tool's stable name, an app name, an agent name), `label`, `tokens` (int | null: the cost the
chip shows), `outcome` (`attached` | `reduced` | `refused` | `not_found` | `handed_off`), `why` (str: why it
was reduced, refused or not found; "" when attached or handed off). The gateway resolves each mention before
the turn starts and records its outcome on the message, so a skill the budget refused, or one outside the
agent's list, says so where the owner sees it. An agent mention is a handoff (decision 42): it hands the
message to that agent as a helper of the chat, whose answer is the turn's reply, and resolves `handed_off`.
A message carries at most one agent mention; one that cannot hand off refuses the send (`409
handoff_refused`) and records nothing; a handoff message's skill, tool and app mentions resolve `refused`.

**`plan`**: `entries` (`[{text, state: pending | in_progress | completed, ticked_at: instant | null}]`),
`source` (`native` | `acp`). At most one per assistant message, saved in place as it changes; the strip above
the composer reads the running turn's. It is the agent's own working list (an agent CLI's ACP `plan` update,
the native runtime's `update_plan` tool), not chat plan mode, which is the planning walkthrough a chat binds
to (`dashboard/chat_plan.py`, `chat_plans/<safe key>.json`) and is unchanged.

**`helper`**: `work` (`subagent:<id>`), `agent`, `task` (redacted, short), `mode` (`batch` |
`background` | `cli`: an agent CLI's own helper), `state` (`HelperState`: `queued`,
`waiting_on_owner`, `running`, `waiting_on_limit`, `completed`, `failed`, `stopped`, `declined`,
`timed_out`, `step_limit`, `interrupted`; an owner's stop is `stopped`, never `failed`; a helper an earlier
gateway ran that was not started again is `interrupted`), `wait` (§4.3's wait record | null), `result`
(`{ending, reason, sentence, partial, digest}` | null: its result, written once at its terminal state),
`generation` (int: 1, and one more for each re-dispatch, §5.9), `again_of`, `follows_up`, `child_of` (helper
ids: the helper this one runs again, the finished one it follows up, an agent CLI helper's parent),
`started_at`, `ended_at`, `last_activity` (`{text, at}`). Its live state comes from the subagent registry
(`SubagentInfo`, `subagent.py:439-464`); the part keeps its identity and how it ended, so it survives a
reload.

**`compaction`**: `trigger` (`auto` | `owner` | `rebuild` | `idle`: the
threshold, `/compact`, a restore over its budget, an idle chat), `form` (`summary` | `digest`; not `kind`, the part's own), `model_ref`
("" for a digest), `check` (`passed` | `retried` | `failed` | `skipped`), `summarized` (int, turns),
`head_through` and `through` (Coord), `kept` ([Coord]: the user messages inside the span kept word for
word), `text` (the summary message as the model received it, fenced), `account` (the resume account blocks
carried beside it), `sections` (five lists, empty for a digest), `focus` (≤ 500 characters, "" for none),
`restored` (`{files, skills, running: [work reference]}`), `pruned` (int), `before_chars` and
`after_chars` (int; they replace `reduced`). `Coord = {message_id, step, steer}`: a turn's user message
(`step` 0), a step of its shown attempt (the record's `step`), or the `steer`-th steer (from 1) the turn
took after that step. It sits on the attempt whose step crossed the threshold (by the record's
`after_step`), on the `/compact` turn's, on the restored turn's for `rebuild`, and on the chat's last
assistant message for `idle` (a `part_saved` after it settled, §3.6). The latest compaction part with a
`through`, in record order, is the chat's compaction; `turns.dialogue` reads it in place of a background
summary (§3.12).

**`review`** (on the reply to a `dispatch: review` message, whose `agent` is `reviewer`):
`review_id` (`rv_…`), `scope` (`{kind, label}`), `state` (`running` | `done` | `stopped` | `failed`),
`verdict` (§17.7's, null while running), `summary` (the reviewer's, masked), `counts` (`{p0, p1, p2, p3,
pre_existing, dismissed, refused}`), `findings` (`[{id, priority, title, file, start, end, state}]`, at
most 25; the review record keeps every `Finding`, §17.7), `coverage` (`{in_scope, inspected,
every_file}`), `model_ref`, `same_model` (bool: the chat's own model reviewed it). The model's history reads
it through `review_part.model_text(part)`, the titles through the outside-text door.

**`ways`** (the one part of the reply to a `dispatch: ways` message, settled at once): `group`
(`tw_…`), `state` (`starting` | `running` | `ready` | `kept` | `discarded` | `failed`), `ways` (`[{n,
session, title, model_ref, phase, outcome}]`), `kept` (int | null). Saved again in place as the ways move
(§3.6).

**`call`** (the one part of a `dispatch: call` message): `state` (`connecting` | `live` | `ending`
| `ended`), `model_ref` (the realtime model), `provider_label`, `local` (bool), `voice`, `started_at`,
`live_at`, `ended_at`, `duration_ms`, `ended_by` (`owner` | `idle` | `limit` | `provider` | `connection` |
`gateway` | `chat` | ""), `outcome` (`complete` | `failed` | `interrupted` | null), `reason`, `transcript`
(`[{who: owner | assistant, text, at, cut}]`: final utterances, redacted, at most 100,000 characters in
all), `transcript_dropped` (int), `requests` (`[{message_id, text, asked_at, state: running | needs_owner
| answered | failed | outlived}]`, at most 40), `started` (`[{work, label}]`: work its requests started
that still ran when it ended), `usage`. Saved whole when it is made, goes live, changes its `requests` and
ends, never per utterance; the audio is never kept. A request made in the call is an ordinary user message
with `input_origin: call`, `facts.call` and `facts.heard`.

#### 3.3.1 Tool families

Status: planned

One closed vocabulary, `Family`, classifies a call for every surface that says it in words: the folded
turn's sentence ("edited 3 files, ran 8 commands", joining #3820) and the approval's sentence (§11.4).

| Family | Calls | Past-tense phrase |
|---|---|---|
| `read` | reading files, listing folders, reading a workbook (`sheet_inspect`, `sheet_read`), reading a background command's output (`job_read`) | "read N files" |
| `edit` | writing or editing files, changing a workbook (`sheet_update`, `sheet_create`) | "edited N files" |
| `command` | shell commands, and writing to or stopping a background command (`job_input`, `job_stop`) | "ran N commands" |
| `search` | knowledge, memory and web search, and reading another chat (`chat_read`) | "searched N times" |
| `fetch` | fetching a page or a URL | "fetched N pages" |
| `browse` | driving a browser, and a browse submission's ask | "browsed N pages" |
| `delegate` | starting, steering, following up and estimating helpers, or asking other agents (`subagent_run`, `subagent_explore`, `subagent_steer`, `subagent_continue`, `subagent_estimate`) | "started N helpers" |
| `send` | sending a message, a notification or a reply | "sent N messages" |
| `other` | everything else, every MCP and app tool | "used N tools" |

The classifier lives in core (`src/personalclaw/tool_families.py`, `family_of(tool, tool_kind, declared,
tool_input)`), reads the tool's name, kind and effective reading (never a guess that widens anything),
classifies a facade call as its inner call, and is the one source the frontend's renderers and the brief
read. Core's own tools are named in a frozen mapping, and a rail fails on a core tool that registers with
no row. It is built with the approval text, or with the typed turn if that lands first, to this table.

### 3.4 A tool part's states

Status: planned

| From | To | When | Set by |
|---|---|---|---|
| (none) | `pending` | the runtime announces the call | the runner |
| `pending` | `running` | it starts: approved, or it needs no approval | the runner |
| `pending` | `refused` | a gate refused it before it ran (an inspect-only check's gate refuses every call that is not replay safe, `refused_by: inspect_only`, §5.5), or its approval ended rejected, expired or cancelled | the runner |
| `pending` | `cancelled` | the turn ended before the call started: any call of the built-in runtime, and an agent CLI's `effect: read` call; a stop's drop (`why: stopped`) and a steer's skip (`why: steered`) | the runner, or the start-up pass |
| `pending` | `unknown` | an agent CLI's `effect: write` call ended while still pending, by a stop, a restart, a crash or the time limit: the CLI may have started it before it reported it | the runner, or the start-up pass |
| `running` | `completed` | the tool returned success | the runner |
| `running` | `failed` | the tool returned a failure other than `ERR_OUTCOME_UNKNOWN` | the runner |
| `running` | `unknown` | the tool returned `ERR_OUTCOME_UNKNOWN`; or an `effect: write` call ended while running by a stop, a restart, a crash or the turn's time limit | the runner, or the start-up pass |
| `running` | `cancelled` | an `effect: read` call ended while running by a stop, a restart, a crash or the time limit | the runner, or the start-up pass |

`completed`, `failed`, `refused`, `cancelled` and `unknown` are terminal. A part changes state only
through `turns.advance(part, to, **facts)`, which raises `IllegalTransition` on any edge not in this table
(the approval, question, sign-in and helper parts move through the same function by their own tables).
The `running` event of an `effect: write` call is on disk before the tool starts.

### 3.5 The definitions every theme uses

Status: planned

| Term | Definition |
|---|---|
| A call that **may have run** | a tool part whose state is `running`, `completed`, `failed` or `unknown`; this is today's "made, and not refused" (`_steps_made`) |
| A **completed side-effecting call** | a tool part in `completed` whose `effect` is `write` |
| A **failed side-effecting call** | a tool part in `failed` whose `effect` is `write`: it may have done part of its work, so it is named, not retried blind |
| A call whose **outcome is unknown** | a tool part in `unknown` |
| A turn that **left work behind** | an assistant message whose turn ended `interrupted`, `error`, cut short or at a named limit (§3.9) while at least one of its calls may have run, or whose reply is still cut at the output cap after its one continuation |
| A **replay-safe** call | a call with `effect: read` and no outside effect, so safe to repeat after a restart: the `read` family (files, folders, workbooks: `sheet_inspect` and `sheet_read` are replay safe by it, and `sheet_update` is not), a shell command the read-only screen passes (no network command passes it), and core's reads and searches of the owner's own stores (`tool_families.REPLAY_SAFE_TOOLS`: `memory_recall`, `memory_list`, `knowledge_search`, `chat_search`, `chat_read`, `tool_result_get`, `subagent_status`, `subagent_list`); never `fetch`, `browse`, web search, `send`, `delegate` or an MCP or app tool |
| A call's **repeat class** | for a call that may have run: `none` when it is replay safe; `idempotent_key` when it carries a key and completed, or its writer recorded `key_honored`; `unknown` otherwise (§6.7) |
| A unit's **side-effect class** | the worst repeat class of the calls in a unit of work (`none` when nothing may have run); a unit of agent work (a helper, a handoff, an automation's agent) is never `idempotent_key` (§6.7) |
| A **check** after a change | a `command` call after a turn's last `edit` call whose program and words are in `code_checks.CHECK_PROGRAMS` (tests, builds, type checks, linters: `pytest`, `npm test`, `go test`, `cargo test`, `make test`, `vitest`, `jest`, `tsc`, `mypy`, `go vet`, `ruff`, `eslint`, `npm run build`, `cargo build`, `go build`), its outcome by exit code; `code_checks.ran_after_last_change(parts)` reads them. Whichever of the ways and a turn's checks lands first creates the table, and the other reads it; it says what ran, never "verified" |

They are written once, as `turns.may_have_run`, `turns.completed_side_effect`,
`turns.failed_side_effect`, `turns.outcome_unknown`, `turns.left_work_behind`, `turns.replay_safe(part)`
(with `tool_families.replay_safe_call(tool, tool_kind, declared, tool_input)` before a call exists),
`turns.repeat_class(part)` and `turns.side_effect_class(parts)`; every surface that says what survived a
turn reads `turns.ending_summary(message)`. The continuation's check, the in-turn recovery, the work queue's
pass (§5.9) and the restart notice (§5.8) read the same three.

### 3.6 Storage: the event log (decision 1)

Status: planned

`sessions/<safe_key>.jsonl` becomes an append-only event log. Each line is
`{"v": 1, "eid": "e_…", "seq": <int>, "at": <instant>, "type": <type>, …}`; `seq` increases by one per
line of the file, written by the chat's one writer under the chat's lock.

| `type` | Fields | Fold |
|---|---|---|
| `chat_started` | `tab_id` | the first line of a chat created after the conversion |
| `message_added` | `message` (a Message without `parts`) | adds the message |
| `part_saved` | `message_id`, `part` (the whole part) | the last line for a part id wins; it may follow its message's `message_settled` (an idle compaction's part on the chat's last assistant message, a `ways` part as the ways move) |
| `message_settled` | `message_id`, `settled_at`, `outcome`, `error`, `usage`, `model_ref`, `facts` | settles the message |
| `reply_shown` | `parent_id`, `message_id` | moves `shown` among a parent's attempts |
| `messages_rewound` | `from_message_id`, `edit_message_id` | an edit-and-replay: the messages from `from_message_id` on are kept, folded behind the edit |
| `turns_undone` | `from_message_id`, `by` | an undo (`chat_undo.py`): the fold drops the messages from that user message on; the lines stay |
| `chat_cleared` | `by` | a runtime's clear: the fold starts empty after it; the lines before it stay, as "nothing shortens the owner's record" says |

Rules:

- A part is written at a durable boundary only: a state change, a decision, a text segment's flush, the
  message's settle, and the gateway's last save (today's `final`, `chat_persistence.py:803-805`), never
  per streamed chunk. Text that streams after a tool group starts a new `text` part.
- Every append is flushed; it is fsynced after `message_settled`, after the `part_saved` that moves an
  `effect: write` tool part to `running`, after `turns_undone`, `chat_cleared`, `messages_rewound` and
  `reply_shown`, and at the last save, so a crash can lose streamed text and nothing that may have changed
  something.
- Text is redacted on write for every role but a user's and a system row's, as today.
- **Not the run ledger** (decision 1). The ledger (`src/personalclaw/ledger/`) is a run's learning
  record, keyed by run, redacted with 64 KiB spills, read by the learning flywheel; the transcript is the
  owner's verbatim record of a chat, read whole by the chat and its readers. Making the chat a ledger
  producer would pour every chat into the refiner's corpus and change what the owner's record keeps. The
  log takes the ledger's stamping rules (a per-file `seq`, a unique event id, idempotent replay) without
  its writer.
- Durability: the `sessions` entry is already `jsonl_append` with `append_dedup`
  (`inventory.py:552-559`); `eid` is the dedup key. Chats still do not sync between homes (an append-only
  folder, `inventory.py:2409-2414`).

### 3.7 The chat record (decision 2)

Status: planned

The record the transcript's first line holds today moves to `sessions/<safe_key>.chat.json`, written
atomically by one writer, `chat_record.write(key, record)`, and changed field by field through
`chat_record.update(key, **fields)` under the chat's lock (the one write path for a chat-level field
another design adds: chat grants, groups, a delegated chat's client); it is read back by one loader,
`chat_record.read(key) -> ChatRecord | None` (absent: `None`; unreadable: the chat opens read-only and
nothing writes it). A metadata line at the head of an append-only file would force a whole-file rewrite on
every title, pin or grant change, which is what §3.6 removes; the sidecar has precedent beside it
(`<safe_key>.summary.json`, `docs/architecture/chat-sessions.md:344-348`).

Fields: every key the line holds today (`chat_persistence.py:914-1033`): `created_at`,
`last_consolidated`, `closed`, `memory_mode`, `title`, `agent`, `model`, `reasoning_effort`, `mode`,
`workspace_dir`, `project_id`, `acp_provider`, `acp_provider_agent`, `task_mode`, `pinned`, `color_index`,
`color_theme`, `natural_voice`, `tags`, `lifecycle`, `last_activity_at`, `never_archive`, `forked_from`
(now `{session, message_id}`: the chat and the message it was forked at), `side`, `tab_id`, `app`,
`created_by_app`, `imported_from`, `message_count`; with `folder_id` renamed `group_id` (§13.4); plus
`v: 1`, and:

| Field | Type | Meaning |
|---|---|---|
| `chat_grants` | [ChatGrant] | the chat's grants (§9.3) |
| `queue` | [QueueItem] | the messages waiting to run, each with every key a queue item carries today (`id`, `content`, its source, `channel`, `files`, `own_words`, `asked_for_by`, and a Retry's `retry` and `hint`) plus `message_id`, `accepted_at`, `body_digest`, `sent_at` (when its sender sent it) and `held` (`""` \| `late` \| `stopping`: a channel message taken in and held, because it arrived long after it was sent or while the gateway was stopping; a held item is never drained on its own, and runs only on the owner's word); written on every append, promote, cancel, eviction and drain, so the queue survives a restart. Nothing drains it on its own at a start: a chat whose cut turn is continued (§5) drains it after the continuation; any other waits for the owner's Send now or Remove |
| `withdrawn` | `[{message_id, body_digest, receipt}]` | the newest 50 queue items removed unsent (a cancel, a soft Stop's clear, an eviction), so a repeated send of one runs nothing (§3.9) |
| `kind` | `chat` \| `worker` | `worker` for an unattended run's conversation (below) |
| `work` | WorkRef \| "" | a worker conversation's work reference; "" for a chat |
| `parent` | str | the chat or app that started a worker conversation; "" for a chat |
| `created_by_client` | `{client_id, label_at_start}` \| null | the External access client that started a delegated chat (below), written once at its start |
| `limits` | `{max_steps, max_dollars, max_tokens, max_seconds}` \| null | the limits a caller set when it created the session (§3.9), read by every turn of it |
| `scheduled_continue` | `{message_id, at, kind: continue \| retry}` \| null | a timed Continue or Retry waiting to run (§5.5), a job of the one scheduler (§17.15) |
| `kept_agent` | str | the kept agent (`ka_…`) whose thread this chat is; "" for an ordinary chat. Written only by the kept-agents store; the routes that change a chat's agent or title refuse a thread (`409 kept_thread_fixed`), and the general chat list leaves threads out |
| `home` | bool | the owner's Home: their one conversation across their channels |
| `matched_by` | `{binding, tier, label}` \| null | the binding a channel arrival matched to reach this chat |
| `works_in` | `folder` \| `worktree` \| `sandbox` | where the chat works (§13.2); `sandbox` joins in the change that makes a chat run in one |
| `worktree` | Worktree \| null | the chat's own worktree (`wt_…`) and its record |
| `ways` | [WaysGroup] | the newest five groups of ways tried from this chat |
| `way_of` | `{group, parent, n}` \| null | on a way's own chat: its group, the chat it was tried from, its number |
| `sandbox` | `{tier, network, ports}` \| null | the sandbox tier the chat works in, its network and its published ports (the tier's traits are §17.8's) |
| `fence_outside` | bool | the owner runs this agent CLI chat outside the shell fence |
| `continues` | `{session, part_id, title, summary}` \| null | the chat and compaction part this chat was started from |
| `chat_holds` | [Rule] | the chat's own ask rules (§9.10, `scope: chat`, effect `ask`) |
| `access_grants` | `[{path, access, lasts, granted_at, request_id}]` | the folders an access request granted (§13.2's `access` origin); a turn's grant ends with the turn |
| `on_this_machine` | bool | "Keep this chat on this machine": its turns run only on a model served here |

`message_count` counts the fold's user and shown assistant messages. A design that adds a chat-level field
adds it here, in the writer and in the loader, in one change.

**Worker conversations.** The turns of a subagent (an automation's agent, a run-prompt or invoke-agent
action, a chat's helper) and of a webhook turn are kept in the same event log and record, keyed by their
session key (`subagent:<id>`, `hook:<…>`), with `kind: worker`. They are read-only and owner-only, kept 30
days and at most 2,000, deleted with the chat that started them, and a Temporary or Incognito chat's
helper keeps none. Loop, campaign and planner workers are chats already and are not recorded twice. They
are listed by agent and parent, newest first (`worker_conversations.list(*, agent="", parent="", since="",
limit=50)`; the listing route takes `agent=`); a helper a handoff started records `handoff`, the asking
message's id. A follow-up to a finished helper starts a new helper with a conversation of its
own (`follows_up` names the old one); nothing appends to a finished helper's conversation.

**Delegated chats.** A chat an External access client allowed to start chats begins is keyed
`inbound:<client_id>:dlg_<ULID>` (`ids.new_id("dlg")`), the `inbound` session kind (unattended,
`session_keys.py`), and carries `created_by_client`. Its source is the client's, so its words change none
of the owner's memory; every approval, question and sign-in it raises is the owner's to answer, and the
recovery journal never continues it (§5.4).

### 3.8 Live frames

Status: planned

| Frame | Data | Sent when |
|---|---|---|
| `turn_message` | `session`, `message` (without `parts`), `seq` | a message is added, settled, or its shown attempt changes |
| `turn_part` | `session`, `message_id`, `part` (whole), `seq` | a part is added or changes |
| `turn_delta` | `session`, `message_id`, `part_id`, `part_kind` (`text` \| `reasoning`), `delta`, `seq` | a streamed chunk |

`seq` comes from the one counter the detail route's `stream_seq` already resumes from
(`DashboardWebSocketState.next_stream_seq`, `ws_state.py:135`; `chat_handlers.py:985, :1044`): a client
applies a frame only when its `seq` is above the snapshot's. In-process readers (the SSE mode of
`POST /api/chat`, the chat-completions door, the CLI) read the same three frames through one
subscription per chat.

- **Retired by these three:** `chat_chunk`, `chat_thinking`, `chat_segment`, `tool_call`, `tool_result`,
  `chat_message`, `chat_user_message`, `chat_variant_switch`, `token_usage` (`chat_runner.py:4955`) and
  `chat_done` (`:5730`); the turn-fact kinds of `activity_event` (`context`, `context_without_memory`,
  `learned`, `stats`, `skills`, `prompt`) become fact writes carried by `turn_message`.
- **Retired by §4's `work_phase`:** `chat_status` (`chat_runner.py:3413`), `activity_event {kind:
  status}` (`:3414-3415`), `turn_steerable` (`running_turn.py:126`), and `heartbeat` (`:3497`, which
  nothing reads).
- **Retired with output file parts:** `file_ready`, which nothing reads; a file a turn made or a tool
  delivered is a `file` part (§3.3).
- **Kept:** the registries' own frames, which feed the waiting list and the companion apps: `approval`
  and `approval_resolved` (`ws_state.py:88`), `question_card` and `question_resolved`
  (`owner_questions.py:44-45`); the signals to re-read the chat, `session_clear`, `chat_undone` and
  `chat_rewound`; and the frames that are not the turn: `queue_promoted` and the queue frames,
  `context_usage`, `chat_followups`, `chat_organize`, `chat_plan_step`, `routing_suggestion`,
  `subagent_status`, `sessions_restarting`, `session_title`, `session_binding`, `voice_chunk`, and
  `activity_event`'s other kinds (`hook`, `permission`, `headroom`, `session`). The `subagent_*` frames
  carry the helper's `state` and `reason` in place of `done` and `error`; `job_changed`
  and `wake_word` are new (§1.6).
- **One table.** These lists are `frame_kinds.py`'s rows (§1.6): `turn_message` and `turn_part` are
  `stored`, `turn_delta` and `work_phase` are `live_only`, and every kept frame above is `not_turn`. A
  design that adds a frame adds its row in the same change.

### 3.9 Sends, receipts, the turn document and Stop

Status: planned

**Request** (`POST /api/chat`): unchanged fields (`message`, `session`, `agent`, `queue_mode`, `meta`,
`input_origin`, `color_theme`, `?ws=1`), plus:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `client_message_id` | `msg_<ULID>` | no; the dashboard and the CLI always send it | becomes the user message's id; absent, the gateway mints one; `meta.client_ts` is removed |

In `meta`, `mentions: [{target, ref}]` (at most 20; an unknown target answers `400 invalid_mention`)
carries the picks of the capability index, each recorded as a `mention` part, and `attachments: [str]`
carries the owner's uploads (the paths the upload route answered), each recorded as a `file` part with
`direction: in`. They replace `meta.files`, `meta.knowledge` and `meta.artifacts`, in one change:
the one that builds the capability index. Three more `meta` keys come from the owner's own
surfaces only (an app token's send carrying one answers `403 forbidden`), each quote and error passing the
outside-text door: `comments: [Comment]` (at most 50, §17.7; `400 invalid_comment` names the first bad
one) and `fix: [{review, finding}]` (at most 25 findings of this chat's reviews; `404 finding_not_found`),
from which the gateway composes the user message's text and `facts.comments` or `facts.fix`;
and `widget_error {slug, title, error}`, a widget's failure sent for the agent to fix.

Delivery stays `queue_mode` (`steer`, `followup`, `collect`, `interrupt`, with the mid-turn policy as the
default, `chat_handlers.api_chat`); the composer's queue-or-steer choice sends it (decision 19). A steer
carries `expected_message_id`, the user message that started the running turn as the page knows it (the
receipt's `turn_message_id`, a row's `meta.message_id`, a `chat_user_message` frame's `message_id`;
`run_state.turn.message_id` once §4 serves it). With it, `take_in` answers, recording nothing: no turn
running, `409 no_running_turn` (the page offers Send); another turn running, `409 turn_not_current` with
`error_extra {expected, actual}` (the page offers Queue); a turn that takes no steer, `409
turn_not_steerable {kind}`: `compact` (a `/compact` its runtime runs) or `command` (a slash word its
runtime runs itself), with a review and a turn whose result schema the steer does not carry joining as
they are built. Without it (scripts), today's rule holds.

**Every door takes a message in one way.** `chat_intake.take_in(state, session, body, *, door) -> Receipt
| Refusal` records every message any door puts into a chat (the dashboard, a channel's intake, a scheduled
notice's injection, a reply to an agent's Inbox item, plan mode's next turn, edit-and-resend, a helper's
report, a goal loop's cycle, a review and a ways start, a background command's report, a request made in a
voice call (`call`, with `queue_mode: followup` for every request), the chat-completions door, a delegated
chat, the journal's continuation and a re-sent queue item), decides the disposition with today's rules,
and checks for a duplicate and records in one synchronous step, so two identical sends racing record one
message. A send whose agent mention cannot hand off answers `409 handoff_refused` and records nothing
(§3.3).

**Receipt** (WS mode: the 200 body; SSE mode: the first event, named `receipt`, of a message that starts a
turn, and the 200 body of one that does not):

| Field | Type | Meaning |
|---|---|---|
| `session` | str | |
| `message_id` | str | the user message's id |
| `accepted_at` | instant | |
| `disposition` | `started` \| `queued` \| `steered` \| `replaced` \| `held` | closed; `held` for a channel message taken in and held, which does not run (§3.7's `held`) |
| `turn_message_id` | str | the user message whose turn this message runs in: itself, or the running turn's for a steer |
| `duplicate` | bool | true when the id was already in this chat with the same body digest |
| `routing_suggestion` | object | optional, as today (`chat_handlers.py:398-431`) |

**Duplicates** (decision 6): the id is unique in the chat for its life, so a repeat is found in the chat
itself (its user messages, its steers, its queue, its merged messages and its withdrawn queue items), with
no store of its own. The chat's rows, steers and queue are read once, when it is first sent to, and every
take-in after that is kept beside the chat while the gateway runs, which is what keeps a withdrawn queue
item taken; a message still queued when the gateway restarts is lost with the queue (§3.7 keeps it), and a
repeat of it after the restart runs as new. Same id, same body digest (`canonical_digest({message, agent,
queue_mode, meta})`, §2.4, over the text as sent, before the dictation note, `queue_mode` as sent, and the
client's meta as the door keeps it: less `client_ts`, `own_words`, `ran_prompt` and `steered`, with
`pastes` as validated): `200` with the original receipt and `duplicate: true`, and nothing runs. Same id,
other digest: `409 message_id_conflict`. A malformed id: `400 invalid_message_id`.

**The turn document** (`GET /api/chat/sessions/{session}/turns/{message_id}`; `404 turn_not_found`; a
steer's or a merged message's id answers the turn it ran in):

| Field | Type | Meaning |
|---|---|---|
| `session`, `message_id` | str | the turn's user message |
| `state` | `queued` \| `running` \| `settled` | |
| `outcome` | as §3.3 \| null | |
| `reply` | Message \| null | the shown attempt, with its parts |
| `attempts` | int | |
| `usage` | Usage \| null | |
| `error` | as `Message.error` \| null | |
| `run_state` | §4 \| null | while running |

The document's reply carries each result's first 8,000 characters; `GET
/api/chat/sessions/{session}/parts/{part_id}` answers one part whole (`404 part_not_found`), with the
detail route's authorization.

**Stop** (`POST /api/chat/sessions/{session}/stop {message_id, with_started?}`): stops that turn only; `409
turn_not_current {expected, actual}` when it is not running, and nothing is stopped or cleared. Without
`message_id` it stops whatever runs, as today; the dashboard always sends one. `with_started` also stops
what the turn started, walked by `caused_by` (its helpers, theirs, the workflow runs it started): the
registry row "Stop this turn and what it started". The answer carries `withdrawn: [message_id]`, the queued
messages a soft Stop removed.

**Limits.** `POST /api/chat/sessions` takes `limits {max_steps, max_dollars, max_tokens, max_seconds}`
(`400 invalid_limits`), kept in the chat record (§3.7). Each ends a turn `error` with its code:
`step_limit_reached`, `spend_limit_reached`, `token_limit_reached`, `time_limit_reached`. The native loop's
100-step stop is the step limit no caller set. Spend binds a run key of the turn's own
(`chat:<session>|<message_id>`), and the turn's helpers draw on it as nested budgets do. The limit is
called steps wherever a person reads it; stored fields named `max_turns` keep their names. **Turn limits
are two changes on one rule.** The first names every limit, its ending and its code, and lets a caller
set one; the second, which depends on it, gives a turn no caller limited its `max_seconds` by origin
(`turn_deadline.limit_for(message)`): the owner's own message and their Continue get
`session.turn_time_limit_secs` (default 7200, a backstop); a turn nobody waits on (a helper's or a
command's report, an automation's injected note, someone else's channel message, the journal's
continuation) keeps 600 s; a goal-loop cycle keeps its loop's bound. The clock pauses while the owner is
asked. An agent CLI's two-hour prompt deadline goes: it becomes this backstop for attended turns and the
helper ceiling for helpers.

**A typed answer.** A turn may carry `result_schema` (JSON Schema, checked when it arrives; `400
result_schema_invalid`). The native runtime then offers `submit_result`, which must be the turn's last
call, with three tries; after the third the turn ends `error`, code `schema_unmet`. Agent CLIs get
`submit_result` among PersonalClaw's tools for that turn.

**`RunResult`** (`run_result.py`) is one envelope for every headless door that runs a chat turn, built from
the turn document: `format_version` (1), `session`, `message_id`, `outcome`, `ending`, `steps`,
`duration_ms`, `usage`, `usage_with_helpers`, `refused` (`[{tool_call_id, tool, refused_by,
refused_rule}]`), `errors` (`[{code, message, retryable}]`), `text` (the shown reply's text parts joined),
`structured` (a schema run's validated object, or null) and `helpers` (`[{work, agent, state, ended_at}]`).
`ending` is closed: `answered`, `stopped`, `needs_approval` (the door returned while the turn waits on an
ask), `connection_lost`, `refused_to_start`, `gateway_interrupted`, `error`, `step_limit`, `spend_limit`,
`token_limit`, `time_limit`, `schema_unmet`; each joins with its producer, so the both-ways rail holds from
the first change. Its doors: `personalclaw run` (`--format json` prints it, `streaming-json`'s last line is
it); the chat-completions door (`answered` is `finish_reason: "stop"`, `step_limit` and `token_limit`
`"length"`, `needs_approval` `409 needs_approval`, any other ending `502` with the turn's error code); and
the `chat_turn` tool, the read an outside client makes of a turn. A door that runs a helper or a
workflow run adopts `ending` once its run's result is typed. A rail fails on a chat-turn door that builds
its own result. `personalclaw run` pairs every tool result with its call by `tool_call_id`, reading each
tool part's `state` (which ends the "denied" heuristic, `cli_run.py:443-449`), and exits 0 for
`answered`, 1 for any other ending, 2 for a usage error and 3 when no gateway of this home could be reached
or started; a signal's exit is that signal's.

### 3.10 The converter

Status: planned

One idempotent backfill (§1.5) converts every chat at the first start of the release that ships the typed
record:

1. Before converting, it takes a snapshot of the home through the snapshot mechanism; a snapshot that
   fails converts nothing, and the chat routes answer `503 chats_not_converted` until a start converts.
2. A file whose first line is `chat_started` is already converted and is left alone.
3. Each file is converted in isolation into `<name>.jsonl.new` and `<name>.chat.json`, then verified by
   folding the new log and comparing, message by message and part by part, with the old reading; only
   then is it renamed over the old file.
4. A file that does not verify is moved to `sessions/.unconverted/`, the Doctor names it, and nothing else
   reads it.
5. `sessions/archive/` batches (`history.py:13`, lines earlier versions trimmed out) convert the same way, as
   logs with no record.

A turn-dispatch row's `meta.message_id` (written from the receipts change on) is its message id; any other
message and every part gets `ids.stable_id(prefix, row.ts, seed)`, so an id a turn document served before
the conversion is the id the converted record keeps.

| Old line | Becomes |
|---|---|
| the `_type: metadata` line | the chat record (§3.7), `message_count` recomputed |
| `user`, `inject`, `subagent`, `nudge` | a user message with that `dispatch` (`user` → `message`, `nudge` → `goal`) |
| any row of a chat whose record names `imported_from` (a conversation imported before this release) | its messages carry `facts.from_outside {what: "A conversation brought over from <tool>", source: "import:<source>", source_type: "import", source_id: <its session there>}`, as an import writes today, so every history hands them to a model through the outside-text door and memory learns nothing from them |
| a row's `meta.from_outside` | `facts.from_outside` |
| `user` with the `auto-go` class (the gateway's turn on its helpers' failures, written before helper reports had a row of their own) | a user message with `dispatch: message`, as its saved row reads once the class is gone; today's history hands it to a model as the owner's |
| a user row's `meta.files` | an entry under an attachment folder: a `file` part (`direction: in`, `ref.kind: attachment`); any other entry, a workspace file the owner picked: a `mention` part (`target: file`, `outcome: attached`) |
| a user row's `meta.knowledge`, `meta.artifacts` | `mention` parts (`target` `knowledge` or `artifact`, `label` the id, `tokens: null`, `outcome: attached`) |
| a `user` row with `meta.steered` (a steer the running turn took); a merged row's `meta.merged` (`meta.queued_from` on one written before it); a row's `meta.receipt`; an `inject` row's `cls.cronLabel`; a `subagent` row's `meta.asked_for_by`; `meta.original`, `meta.ui_label`, `meta.file_changes` | a steer message, with `steered_into` and `after_step` (how many of the answer's text parts came before it, since a row records no step); the facts of §3.3 of the same meaning |
| `assistant` (with `variants`, `variant_idx`) | assistant attempts of one parent, `shown` from `variant_idx` |
| `tool` with `meta.tool_call_id` | a tool part: `done` and `ok` not false → `completed`; `ok: false` → `failed`; not done in a turn that ended → `unknown` when its risk is not known to be safe, else `cancelled`; risk not recorded → `unchecked`, `effect: write` (fail closed). It has no `step`, and its `result.text` is the 8,000 characters the row kept, `received_length: null` |
| a `learned` stamp's items | `facts.learned` items with `state: kept` |
| a helper row, and the report of a helper a restart stopped | a `helper` part in today's state words, a restart's stop read `interrupted` |
| `tool` without an id (a line about a step) | the named call's `notes`, or a `notice` |
| `permission` | an `approval` part, its `state` from `cls.resolved` |
| `file` (a file a tool delivered) | a `file` part on that turn's assistant message (`direction: out`, `ref: {kind: path, id: <the outbox path>}`) |
| `error` | one in the chat runner's words for a turn the gateway ended (`TURN_INTERRUPTED_NOTICES`): an `interrupted` notice of an `interrupted` turn; one in its words for a reply cut short (`TURN_CUT_SHORT_NOTICE`): a `cut_short` notice of a `stopped` turn; of the others, the turn's last one is `Message.error` (`turn_failed`), and earlier ones are `warn` notices |
| `notice`, `system` | notices (`stopped`, `prompt_blocked`, or `info`) |
| `queued` | dropped: a transient placeholder |
| `rewound` tails | the kept messages behind a `messages_rewound` event |

The converter keeps each chat's `<safe_key>.summary.json`; a backfill deletes every one after the
converter has run, since the chat's compaction record stands for them (the change that makes one
compaction depends on the typed record).
One shape reads differently as text (§3.12): an answer of several rows with an earlier version
switched back in. A chat that holds one has its summary recomputed, and the conversion's report names
it as that shape, since the summary's digest was taken over the rows' text.

### 3.11 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| A reload shows what the live page showed | a backend-driven rail replaces `liveAndReloadAgree.test.tsx`: a scripted turn's frames, folded onto the snapshot taken before it, equal the detail route's messages after it |
| A tool part moves only along §3.4's edges | `test_a_tool_part_moves_only_along_its_edges` (every from→to pair), and an AST rail: no assignment to a part's `state` outside `turns.py` |
| The frontend's types are the backend's | `turns.schema.json` generated from `turns.py`; the web types generated from it; a rail diffs both |
| No `cls` remains | the record and the log reject unknown keys; a rail fails on `"cls"` under `dashboard/` |
| A save appends | no write path opens a transcript for rewrite; a test asserts that saving one turn of a long chat writes only that turn's events |
| A repeated send runs nothing, after a restart too | `test_a_repeated_send_answers_its_receipt_and_runs_nothing` |
| Every door takes a message in one way | `test_every_door_takes_messages_in_one_way` (a census of appends, queue writes and steers outside `chat_intake.py`) |
| A stop that names an ended turn stops nothing | `test_a_stop_for_an_ended_turn_stops_nothing` |
| Reasoning never reaches the store | `test_reasoning_is_live_only` |
| Every turn frame names its conversation | `tests/test_apps_see_only_their_own_conversations.py` extended to the three frames |
| The converter loses nothing and runs once | a corpus test over fixture transcripts holding every old role, run twice; the owner runs it over a copy of a real home before the release |
| The record holds what the model received | `test_the_record_holds_what_the_model_received`: a scripted native turn with three steps, a projected result, a steer and a denied call, every message of the runtime's own history recovered from the fold, step by step |
| Every frame kind sent has its row | the census of `broadcast_ws` calls against `frame_kinds.py`, both ways (§1.6) |

### 3.12 What it must not do

Status: planned

- It does not change what a text reader is given. An agent CLI started fresh and every text reader read
  the fold as text (`turns.dialogue`), entry for entry what today's history gives (a steer the runtime took
  is now in it, because the runtime that answered had it), with the chat's latest compaction standing for
  its span. One shape differs: an earlier version of an answer of several rows switched back in, which
  today's history reads in place of the newest version's first row, is read as the attempt it is. A native runtime that starts fresh (a restart, a model or agent switch, a fork, a rebuild) is
  restored from the typed record, its tool calls and results included, within a token budget, every call
  paired with exactly one result (decision 33). A call with no result is restored as "outcome unknown, verify first"
  (`personalclaw.sdk.model.OUTCOME_UNKNOWN_IN_HISTORY` for a model app with its own wire).
- An assistant message carrying `handed_off` enters the model's history as that helper's report as its
  chat is handed it (`subagent_report.for_its_chat`): fenced as data, with the helper as its source; every
  rebuild keeps the rule.
- It does not keep reasoning, and does not make chats a run-ledger producer.
- It writes no event per streamed chunk; deltas are live only.
- It does not make chats sync between homes, and does not change memory modes: a Temporary chat writes no
  file while it runs, and leaves none.
- A receipt is not a delivery guarantee on a channel.

---

## 4. One run state per chat

### 4.1 What it is

Status: planned

One value per chat, computed by the gateway, sent as that chat's #3833 phase frame and served on the chat
list and the detail routes. Every surface reads it; none derives "running" from anything else.

### 4.2 Today

Status: planned

- The list row derives `running`, `stopping`, `pending_approval`, `pending_approval_info` (by scanning back
  for the last unresolved permission row), `waiting_for_input` and `stop_state`
  (`dashboard/state.py:845-899`); the detail serves `running`, `stopping`, `steerable`,
  `last_turn_outcome`, `stream_seq` (`chat_handlers.py:1023-1044`) and `pending_questions` (`:1111`).
- A native turn says one free-text status, "Thinking…", twice: a `chat_status` frame and an
  `activity_event {kind: status}` (`chat_runner.py:3413-3415`); `turn_steerable` says when a steer is
  taken (`running_turn.py:126`); a `heartbeat` frame goes out only when another event arrives, and nothing
  reads it (`chat_runner.py:3496-3498`).
- The page reconstructs stalls (`resolveStalledStream`, `web/src/pages/chat/streamStall.ts:50-64`) and
  lanes (`web/src/lib/attentionLanes.ts:66`).
- A stop is `idle`, `soft_pending` or `killing` (`state.py:556-579`).
- The phase list planned in #3833: `queued`, `thinking`, `searching`, `tool`, `planning`, `delegating`,
  `streaming`, `listening`, `approval`, `input`, `done`, `error`, sent as `{work, kind, phase, line,
  since}`.

### 4.3 The run state (decision 7)

Status: planned

#3833's event is one frame, `work_phase`, for every kind of work. A chat's run state is its `work_phase`
payload, with these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `work` | WorkRef | yes | `chat:<session>` |
| `kind` | `chat` | yes | #3833's kind |
| `session` | str | yes | the frame's conversation field (§1.6) |
| `phase` | Phase (§4.4) | yes | |
| `line` | str | yes | the gateway's one line for what it is doing now; "" when idle |
| `since` | instant | yes | when this phase began |
| `turn` | `{message_id, started_at}` \| null | yes | the running turn, null when idle |
| `stopping` | bool | yes | a stop was asked and the turn has not ended (`_stop_state != "idle"`) |
| `steerable` | bool | yes | the running turn takes a steer (`session._takes_steers`) |
| `waiting` | [str] | yes | ids of #3888 items this chat's own turn raised |
| `helpers_waiting` | [str] | yes | ids of #3888 items its helpers raised (the helpers' roll-up, §12): a helper in `waiting_on_owner` counts, and so do the waiting items of the chat's ways |
| `wait` | Wait \| null | yes | in `approval` and `input`: the typed wait, below |
| `scheduled_continue` | `{message_id, at, kind}` \| null | yes | while a timed Continue or Retry waits (§5.5) |
| `call` | `{message_id, state, since}` \| null | yes | while a voice call in this chat is `connecting`, `live` or `ending`, so every surface knows a call holds the microphone without reading parts |
| `retry` | `{attempt, of, next_at, reason}` \| null | yes | set in `retrying`; `reason` is the provider-failure record's (§17.16) |
| `queue` | `{model, behind}` \| null | yes | set in `waiting_model`: how many requests are ahead on that model |
| `last_step_at` | instant \| null | yes | the latest step's time, so a surface can say "No new activity for 1m" |
| `last_outcome` | as `Message.outcome` \| null | yes | how the latest settled turn ended (today's `last_turn_outcome`) |
| `queued_messages` | int | yes | the chat's queue length |
| `seq` | int | yes | from `next_stream_seq`; a client keeps the highest it saw |

**A wait is a typed record** (`run_state.Wait`; a helper's wait and a goal cycle's take it
too): `reason` (`owner_answer` an approval, `input` a question, `permission` a sign-in or more access,
`children` the helpers or background commands the work started, `dependency` a throttled provider),
`resume_condition {kind: answered | children_done | at_time | signal, ref}` (a `children_done` ref may name
a background command, `job:job_…`, for a goal cycle waiting on one), `deadline_at` and `evidence_source`
(`approval_registry`, `question_registry`, `sign_in_ask`, `subagent_registry`, `dependency_waits`,
`jobs`). Its constructor refuses any other source, `model_text` by name, so a model's words never make a
wait; #3888 and #3889 read it instead of inferring. **A waiting helper gives back its slot:** a helper whose
wait is `owner_answer`, `input`, `permission` or `dependency` releases its concurrency slot and keeps its
runtime; it resumes through admission, where one slot is held back for children and resumes, so waiting
parents cannot starve their own helpers; at most as many helpers as the slot limit wait this way at once.

### 4.4 Phases

Status: planned

| Phase | Defined by | Meaning | The line |
|---|---|---|---|
| `idle` | this document, chats only | nothing queued or running | "" |
| `queued` | #3833 | accepted, not started | "Waiting to start" |
| `thinking` | #3833 | the model works, nothing streams yet | the runtime's activity line, else "Thinking" |
| `searching` | #3833 | a knowledge or web search | "Searching <where>" |
| `tool` | #3833 | a tool runs | the tool part's title |
| `planning` | #3833 | planning | "Planning" |
| `delegating` | #3833 | starting helpers | "Starting N helpers" |
| `streaming` | #3833 | the answer streams | "" |
| `listening` | #3833 | push-to-talk captures for this chat | "Listening" |
| `approval` | #3833 | waits on an approval or a sign-in | "Needs your approval" / "Needs you to sign in" |
| `input` | #3833 | waits on a question's answer | "Waiting for your answer" |
| `retrying` | this document | a provider retry or failover is pending | "Retrying (2 of 3) · next try 10:42" |
| `waiting_model` | this document | queued behind other requests on a local model | "Waiting for <model> · 2 ahead" |
| `done` | #3833 | the turn ended `complete`; transient | "" |
| `error` | #3833 | the turn ended `error`; transient | the error's sentence |

`idle` is the chat's state when it has no live work, not a work phase: #3833's list and its other kinds
are unchanged. A stop does not change the phase; `stopping` is a flag and the line says "Stopping". A
`tool` phase's line gains "· <elapsed>" for a call that has run a while, and "· waiting for input" for a
command that waits on its input.

**Kind `subagent`** maps a helper's `HelperState` (§3.3) to #3833's phases: `queued` → `queued`,
`waiting_on_owner` → `approval` (`input` for a question), `running` → the runner's phase,
`waiting_on_limit` → `retrying`, a terminal state → `done` or `error`. Background commands are live work of
kind `job` on #3889's list (§0.5).

| From | To | When |
|---|---|---|
| `idle` | `queued` | a message is accepted and the turn has not started |
| `idle`, `queued` | `thinking` | the turn starts |
| `thinking` | `searching`, `tool`, `planning`, `delegating`, `streaming` | the runtime reports it |
| any working phase | another working phase | the runtime reports it |
| any working phase | `approval`, `input` | a decision is raised for this chat's turn |
| `approval`, `input` | the phase before it | the decision is answered or ends |
| `thinking`, `streaming` | `retrying` | the chain schedules a retry or a failover |
| `retrying` | `thinking` | the retry starts |
| `queued`, `thinking` | `waiting_model` | the local model's queue holds the request |
| `waiting_model` | `thinking` | the model takes it |
| any working phase | `done` → `idle` | the turn ends `complete` |
| any working phase | `error` → `idle` | the turn ends `error` |
| any working phase | `idle` | the turn ends `stopped` or `interrupted` |

### 4.5 Reads and writes

Status: planned

- `work_phase` is sent on every phase change and, while a turn runs, again every 15 s with the phase
  unchanged and `last_step_at` current, which is the liveness signal a surface needs (no separate
  heartbeat).
- `work_phase` replaces `chat_status`, `activity_event {kind: status}`, `turn_steerable` and `heartbeat`
  (§3.8).
- `GET /api/chat/sessions` rows and `GET /api/chat/sessions/{session}` gain `run_state` (the last
  `work_phase` payload) and lose `running`, `stopping`, `stop_state`, `pending_approval`,
  `pending_approval_info`, `waiting_for_input`, `steerable`, `last_turn_outcome` and `pending_questions`
  (the parts and `waiting` carry them). `stream_seq` stays on the detail. Home's "your turn" moves to
  §12's `attention`.
- The phase module (#3833's) is the one place phases, tones and line templates are defined; the classifier
  that produces a chat's phase lives with the chat runner.

### 4.6 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| Every way a turn ends sends the final frame | a census over the runner's exits, anchored on `terminal_outcome_for_turn` (`chat_runner.py:278-308`) |
| The routes serve exactly what the frame carried | `test_the_list_and_detail_serve_the_last_phase_frame` |
| Every phase has a tone, a line template and a mapping on every surface | #3833's mapping test, extended to `idle`, `retrying` and `waiting_model` |
| No surface derives "running" another way | the chat session wire types no longer have the old flags; a web rail fails on a read of them |
| `retrying` and `waiting_model` come only from measured events | scripted-fault tests (§17.3) drive `retrying`; the local-model queue's own counter drives `waiting_model` |

### 4.7 What it must not do

Status: planned

It is not the waiting list (#3888) or the running list (#3889), which carry it; not attention (unread,
your turn, §12); not history; never a timer a client invents; never per tab.

---

## 5. The recovery journal: resume, not redo

### 5.1 What it is

Status: planned

Two halves. **Continue**: a turn that left work behind (§3.5) gets Continue as its primary action, and
neither Continue nor Retry deletes a call that may have run. **The journal**: a record of each chat turn
while it runs and of each turn the gateway cut off, from which the next start continues eligible chats
with one inspect-first turn.

### 5.2 Today

Status: planned

- Retry deletes every row after the turn's first message, completed calls included
  (`del session.messages[u_idx + 1 :]`, `dashboard/chat_regenerate.py:117`), and reruns a failed turn with
  no hint (`hint = ""`, `:131-135`). The narrow fix that landed before this release makes a Retry
  over a turn whose finished calls may have changed something ask first: `409 retry_repeats_steps`, whose
  detail names those calls and whose yes sends back the question's `confirm` digest; it keeps the
  deletion.
- An interrupted turn says "Send your message again to retry" (`chat_runner.py:262-272`).
- `end_running_turns` cancels every running turn when the gateway stops (`dashboard/state.py:1376-1406`);
  subagents left behind are killed and reported at the next start (`subagent_orphans.reconcile_orphans`,
  `subagent_orphans.py:69`).
- The staged update waits on `_work_in_flight` (`gateway.py:4982-4991`), which counts subagents and
  resident sessions, not running turns or waiting asks (a defect queued as a fix ahead of this release; the
  journal fixes it if it has not landed).

### 5.3 The journal record

Status: planned

`turn_recovery.json` (§1.4): `{"v": 1, "records": [TurnRecovery]}`, one writer, `turn_recovery.py`,
under its lock. A record is written when a chat turn starts (every chat but a Temporary one, which has no
record, before its prompt goes out), so the next start finds a crash's unsettled turn without reading every
transcript: two small atomic writes per turn.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | `rec_<ULID>` | yes | |
| `session` | str | yes | |
| `work` | WorkRef | yes | `chat:<session>` |
| `message_id` | str | yes | the user message whose turn runs, or was cut |
| `agent` | str | yes | |
| `model_ref` | str | yes | as asked |
| `origin` | `dashboard` \| `channel` \| `inbound` \| `app` \| `worker` \| `unattended` | yes | whose chat it is, which decides eligibility; derived once, when the record is made: `unattended` when `session_keys.is_unattended(key)` or the session's own unattended flag says so, `worker` for a loop's, campaign's or planner's session (its own engine resumes it), `inbound` for an `inbound:` key, `app` for a chat an app started (`created_by_app`), `channel` when the turn's `source` names a `source_channel`, otherwise `dashboard` |
| `cut_at` | instant \| null | yes | when the turn was cut; null while it runs |
| `cut_by` | `restart` \| `shutdown` \| `crash` \| null | yes | `restart_request.stopping_for()`; `crash` when the start-up pass finds the record still `running` |
| `state` | §5.4 | yes | |
| `skip_reason` | §5.4 \| null | yes | |
| `claimed_at`, `settled_at` | instant \| null | yes | |
| `continuation_id` | str | no | the `continue` user message's id |
| `mode` | `full` \| `inspect_only` | no | the continuation the pass gave it (§5.5) |
| `posture_digest` | str | yes | `canonical_digest` of what only the owner's act changes and what decides the turn's calls: the agent, agent CLI, model and effort, the task mode, the chat's grant ids, its folder, its project's defaults and the operator ceiling's digest. Trust and YOLO are left out: a restart ends them, and a continuation under a stricter posture asks where the cut turn would not have. Computed from the chat's own fields until §8.4's posture read lands, then over its rows |

It holds no prompt, no reply and no tool output: it names the turn, and the transcript holds the turn.

### 5.4 States

Status: planned

| From | To | When | Writer |
|---|---|---|---|
| (none) | `running` | a chat turn starts | the runner |
| `running` | (removed) | the turn settles any way but `interrupted` | the runner |
| `running` | `cut_off` | the turn ends `interrupted`, or is still running when `end_running_turns` gives up | the runner; `end_running_turns` |
| `running` | `cut_off` (`cut_by: crash`) | the start finds it: its unsettled assistant message is settled `interrupted` with an `interrupted` notice ("PersonalClaw stopped unexpectedly before this reply finished."), its open parts advanced by §3.4's cut rules | the start-up pass |
| `cut_off` | `skipped` | the start-up pass finds it ineligible | the pass |
| `cut_off` | `claimed` | the pass admits it; written to disk before anything runs | the pass |
| `claimed` | `continuing` | the continuation turn starts | the pass, the runner |
| `continuing` | `settled` | the continuation turn ends, however it ends | the runner |
| `claimed`, `continuing` | `skipped` (`restarted_again`) | the gateway stops again first: a turn is never continued twice | the pass |

Records are pruned seven days after `settled` or `skipped`, and a Temporary chat's at once.
`skip_reason` is closed and the first failing reason is recorded: `disabled`
(`session.resume_after_restart` is off), `temporary`, `archived`, `deleted`, `worker`, `channel`,
`inbound`, `app`, `unattended`, `changed_since` (a newer user message exists), `waiting_on_owner` (an
approval, question or sign-in was pending), `posture_changed` (the chat's posture digest is not the
record's), `handoff` (the reply carries `handed_off`: the helper's own row decides what runs again, §5.9,
and the pass settles the reply as any), `review` (a review is started again by the owner, at its cost,
never continued), `outcome_unknown` (a call of the turn is `unknown` and its runtime cannot be held to
replay-safe calls: an agent CLI whose provider does not gate every call), `restarted_again` (the record was
`claimed` or `continuing`, or the cut turn is itself the journal's continuation). Each skip writes
`chat.turn_recovery_skipped` and leaves the turn's ending with its Continue for the owner.

Eligible, by default: a chat the owner started
in the dashboard, with nothing waiting, not Temporary, archived or deleted, with no newer message and its
posture unchanged; one with an `unknown` call is continued `inspect_only` (§5.5). A
helper's row follows the work queue's rule (§5.9): the journal never dispatches a helper, and a
continuation names each helper row's restart decision. Channel chats, inbound clients' sessions (delegated
chats included), app conversations, loop, campaign and planner workers and unattended work are never
continued by the journal in this release. Admission: oldest first, at most two continuations at once, started at
least two seconds apart, each shown as "Continuing after the restart"; the cut turn's settle is flushed
before the continuation's message is added. The pass runs once the dashboard can deliver, after the work
queue's pass (§5.9), and not in a recovery start (§16.3), which holds it until the owner starts
everything.

### 5.5 Continue

Status: planned

`POST /api/chat/sessions/{session}/continue {message_id, allow_repeats: false, at?}`:

- `message_id` is the turn it expects to continue: `409 turn_superseded {expected, actual}` when a newer
  user message exists that is not a continuation of this turn (a continuation of the same turn never
  supersedes it); `409 session_busy` while a turn runs, except an inspect-only check of the same turn,
  which it ends first; `403 not_allowed` for `allow_repeats` or `at` from an app;
- answers `409 nothing_to_continue` when the turn settled `complete`, or when no call may have run and
  nothing was written (plain Retry is then the action);
- with `at`, schedules it instead: one gateway-held run at that time, on the owner's click, kept as the
  chat record's `scheduled_continue` and shown in the composer and the running list with Cancel (`DELETE
  …/continue/scheduled`), ended by any new message, never re-armed, a job of the one scheduler (§17.15);
  `409 continue_already_scheduled` for a second; `chat.continue_scheduled {continues, at}`;
- starts a new turn whose user message has `dispatch: "continue"`, `facts.continues` naming the cut turn,
  and one text part: the continuation, a fixed template, naming each completed and each
  failed side-effecting call (tool, target, a short result) and each `unknown` call, fenced as data, and
  telling the agent to read the current state before acting and not to repeat a side effect already done;
- with `allow_repeats: true` (the owner's alone, never an app's: the loop breaker's stop stays
  deterministic, and only the owner continues past it), raises the loop breaker's read-repeat limit for that one turn, and writes
  `chat.loop_breaker_extended`.

The continuation's user message carries the cut turn's `source`, so it runs as asked by whoever asked the
cut turn (`turn_source.asked_by`), never as the owner's by default, and `facts.mode`. The journal's
automatic continuation is the same call with `allow_repeats: false`, shown in the chat as "Continuing after
the restart"; the chat's queue drains after it. **A continuation consents to nothing**: it answers no
approval, question or sign-in (the cut ones were settled `cancelled`); it is never the owner's presence;
it adds none of the owner's words; a helper it starts asks first, as any start does; and a chat grant answers its
calls only as it would have the cut turn's.

**The inspect-only check.** When a restart cut a turn while a call's outcome was
unknown and its repeat is not known to be safe, the journal's continuation is `mode: inspect_only`. Its
message holds `chat_continue.CHECK` ("A restart cut the turn below off while a step was running, and
whether it happened is not known: <steps>. Find out whether it happened, using only reads of this
machine's files and folders, commands that only read, and searches of your own stores. Do not try it again
or change anything. Then say what happened, what did not, and what is left to do."), the steps through the
outside-text door. The runtime offers only replay-safe tools (§3.5), and its gate refuses any other call
with `refused_by: inspect_only` ("Not run: this turn only checks what happened."). It runs on the native
runtime, and on an agent CLI only when its provider gates every call, in its asking mode; otherwise the
turn is skipped `outcome_unknown`. The check shows as "Checking what happened after the restart" with
**Continue with full tools** and **Stop** from its first moment, while it runs and after: Continue with
full tools is `POST …/continue {message_id: <the cut turn>}`, which stops a running check by its own
message id, waits for it, and starts the owner's full continuation, its text naming the unknown step, the check's
findings above it. Stop ends the check `stopped`; the cut turn keeps Continue and Retry. An owner who
already knows what happened never waits for the check.

**A handoff's Continue.** A Continue of a turn whose reply carries `handed_off` starts a
handoff to the same agent (`dispatch: continue`, `facts.continues`, the agent mention), whose helper is the
cut helper's follow-up when that can take one, else a new helper with the continuation template and the cut
helper's kept report; its start is decided `you`.

A turn that left work behind says what survived ("3 steps finished; their results are above") with
Continue first and Retry second. **Retry never deletes a part that may have run**: it adds an attempt
(§3.3) and keeps the earlier one folded. Retry still asks first when the turn's finished calls may have
changed something, with the narrow fix's question and `retry_repeats_steps` (§5.2), which now offers
Continue as its first answer: repeating is the hazard, not only deleting.

### 5.6 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| No call that may have run is deleted by Retry or Continue | `test_retry_keeps_every_call_that_may_have_run` |
| A record is claimed on disk before its turn runs, and no turn is continued twice | `test_a_cut_turn_is_continued_once_across_two_restarts` |
| A crash's cut turn is found from the journal alone | a test kills the gateway mid-turn; the next start settles the turn and records it `cut_off` with `cut_by: crash` |
| The journal holds no prompt or output | a schema test over its closed keys |
| An ineligible turn is never continued, and says why | one test row per `skip_reason` |
| Another home's journal never arrives | the entry's `machine_local` and `merged_in=False`; `tests/test_durability_inventory.py` |
| Every restart and shutdown path ends turns through `end_running_turns` | a census over `restart_request`'s callers |
| A continuation runs as asked by the cut turn's asker | `test_a_continuation_keeps_its_asker`: a channel-asked turn continued by hand changes none of the owner's memory |
| Retry asks before repeating a change, with or without Continue | the narrow fix's tests, kept green through the converter |
| A continuation consents to nothing: the restart that cut it gave no yes | `test_a_continuation_consents_to_nothing` |
| An inspect-only check calls only replay-safe tools | `test_an_inspect_only_check_calls_only_replay_safe_tools` |

### 5.7 What it must not do

Status: planned

It never repeats a write blind. What a restart runs again on its own is a unit that never started, or
whose class is `none` or `idempotent_key`, with backoff and no grant; an `unknown` unit becomes an
outcome-unknown review, and a cut dashboard turn with an unknown write gets one inspect-only check. It
never resumes a worker's session, a turn waiting on the owner, or a channel, inbound or app chat, never
replays a model's text, and is never an automatic Retry. The work queue's rows follow that rule; the
journal and the rows never decide the same unit; the work queue's pass runs first. A voice call has no
journal record and no row: the start-up pass ends it `interrupted`, and only the owner's Call again dials
again. It is not the workflow resume cache (`workflows/journal.py`) or a loop's resume.

### 5.8 A restart drains first, and one notice says what it did

Status: planned

- **The drain.** For a restart PersonalClaw asks for (the owner's Restart, an update's apply and the staged
  auto-update, all through `updates._graceful_reexec`) the gateway drains before it stops; a stop from the
  service manager keeps today's path.
  1. It stops admitting turns and fires: a send answers `503 gateway_restarting`, which the composer's
     outbox keeps and sends when the socket returns; approvals and questions stay answerable.
  2. It waits up to `gateway.restart_drain_secs` (default 120) for running turns, helpers and in-flight
     workflow steps; a turn waiting on the owner does not hold it. **Restart now** (`{now: true}`) skips
     the wait.
  3. It flushes before it cancels: each running chat's text so far is written first, and a flush that
     fails refuses the restart with its reason ("A chat could not be saved: <reason>. Nothing was
     restarted."). Then `end_running_turns`.

  `DashboardState.active_work_snapshot` counts running chat turns, waiting asks, running helpers and
  in-flight workflow steps, and the staged update's `_work_in_flight` reads the same count. Each drain
  writes `gateway.restart_drained {waited_secs, finished, cut}`.
- **What runs again on its own** (decision 35): a chat's queue is sent after the
  restart, after the chat's continuation ends, or in a chat with no cut turn through the pass's admission
  (oldest first, two at a time, two seconds apart; a start that fails tries again after 2, 4, 8, 16 and 32
  s, then waits). It waits for the owner instead, with Send now and Remove, when the chat's cut turn was skipped
  or is being checked, when the chat is archived, when `session.resume_after_restart` is off, or when the
  item was accepted more than 24 hours before the start. A timed Continue whose time passed while
  PersonalClaw was down runs once at the start. Units of work are §5.9's rows. A re-run carries no grant:
  Trust and YOLO ended with the restart, and every start and call is decided as a new one's would be.
- **One restart notice** (decision 37). `restart_notice.py` writes one notification per start, kind
  `system/restart_notice` ("What a restart did with running work"; a warning when something waits for the
  owner, else info), its id the start's `boot_id`, so another notice of the same start links it
  (`refs.restartNotice`). Its sections, each line linking its chat or work: "Continued", "Checking", "Sent
  again", "Started again", "Waiting for you" (each skip with its reason, each review, each held queue),
  "Helpers the restart stopped" and "Ended". Every pass adds its lines through `restart_notice.note(section,
  line, link)`: the journal, the work queue (helpers and background commands), automation runs and the
  channel send path's re-dispatches. The trigger review's own notice keeps only missed slots, and no
  pass writes a restart notice of its own: the orphan notice goes. Nothing is written when it is empty.
- **The preview.** `turn_recovery.restart_preview() -> {continue, check, send_again, review, chats:
  [{session, title, mode}], …}` says what a restart now would do, with other work's counts added through
  `restart_preview.register(name, fn)` (the work queue's `work`: `{started_again: {kind: n}, for_review:
  {kind: n}, ended: {kind: n}}`). The System widget's Restart, the Update confirm and the drain dialog read
  it and say it in these words: "helpers", "automation runs", "background commands" ("Restart when 2
  running turns finish (at most 2 min)").
- **A recovery start** (§16.3) runs neither the journal's pass nor any re-dispatch of the work queue: a cut
  turn or a row must not set the crash off again. Both wait for the next normal start, or the owner's Start
  everything.

### 5.9 The work queue: every accepted unit of work is a durable row

Status: planned

- **One queue** (decision 36). `work_queue.db` (`work_queue.py`, table `work_rows`; the only code that opens
  it) holds every accepted unit of work that is not a chat's own turn: a helper spawn (`kind: helper`), an
  automation run (`automation_run`) and a background command (`job`). The journal (§5.3) holds chat turns. A
  row is committed and fsynced before its caller answers (`await work_queue.accept(row)`); a write that
  fails refuses the work, `ERR_WORK_NOT_RECORDED`.
- **A row**: `id` (`wq_<ULID>`), `work` (a work reference, unique among unsettled rows), `kind`, `state`
  (the kind's own: a helper's `HelperState`; an automation run's `accepted`, `started`, `done`; a job's
  `running`, `ended`), `parent` (`chat:<key>`, `workflow:<run id>`, a trigger run's work reference, or ""),
  `trigger_id`, `project_id`, `side_effect_class` (declared at accept), `params` (what a re-dispatch needs,
  at most 64 KiB, never a grant, dropped when the row settles), `params_digest`, `start_key`, `generation`,
  `boot_id`, `pid` and `pid_started`, `wait` (§4.3), `accepted_at`, `started_at`, `ended_at`, `settled_at`,
  `outcome`, `result_digest` (a helper's result digest) and `restart` (`{boot_id, action: redispatched |
  held | interrupted | ended, why}`). It moves only through `work_queue.advance(work, to, **facts)`.
- **The start-up order.** Once the dashboard can deliver: first the work queue's pass
  (`work_recovery.recover(boot_id)`), which settles every unsettled row an earlier gateway owned (it kills a
  recorded process still running, applies the common conditions, hands each row to its kind's handler,
  registered with `work_recovery.register(kind, handler)`, writes each decision to disk and schedules the
  re-dispatches); then the journal's pass (§5.4), whose continuations name each helper row's decision; then
  the restart notice (§5.8). The journal and the rows never decide the same unit.
- **What runs again** (decision 35): by class (§3.5, §6.7). A row that never started, and a `none` or
  `idempotent_key` row, is dispatched again with backoff (generation g no sooner than 30 s × 2^(g−1) after
  the start; at most two in any two seconds, oldest accepted first); an `unknown` row becomes an
  outcome-unknown review, never a check run. A helper is agent work, so its `idempotent_key` reads `unknown`;
  one held to the research or text class is `none`. A background command never runs again: it ends. The
  common conditions, else the row is handled as `unknown` with the reason: incident mode is off; the start
  is no recovery start; the row's `params` were kept; and for a helper, it was accepted within 24 hours
  (`waited_too_long`), its `generation` is below 3 (`restarted_too_often`), its parent is there
  (`parent_gone`), and a trigger's agent's automation is enabled at the revision the row recorded
  (`automation_changed`). A re-dispatch carries no grant: no approval mode, earlier yes, write scope or app
  permission is read from `params`.
- **A helper a restart stopped** is `interrupted` (`restart`). Its result reaches its parent chat through
  the manager's batch delivery, which starts no turn in a chat the owner is not in (it waits for that chat's
  next turn), keeps a private chat's rules (landed: a batch's run, and every run a Temporary or Incognito
  chat started, is that chat's own and no other chat's agent reads it, `workflows/chat_runs`; a Temporary
  chat's runs end with it and an Incognito chat's are deleted with it, `workflows/private_runs`), and drops
  a deleted parent's delivery while the notice line stays.
  Its row offers **Run it again** (owner only): a new helper with the same params and `again_of`, whose
  start asks as any start does (`work.run_again`). A helper's report after its re-dispatch arrives as any
  helper's (`dispatch: subagent`), never as a continuation.
- **The exceptions.**
  - A handoff's helper never goes to the batch delivery that starts a turn of its parent's
    agent: its ending, in the gateway that ran it or after a re-dispatch or an interruption, goes to the
    handoff runner, which writes the reply and starts no turn. Its capability class is the chat's task
    mode's, its start key `handoff:<message id>`, and its row's `params` keep `handoff`, so a re-dispatch
    and Run it again stay handoffs.
  - A workflow's agent step (`parent: workflow:<run id>`) is a row its engine resumes: the pass settles it
    `interrupted` and lists it nowhere, and its workflow run resumes the step its own way and reports it.
  - An automation run's row is written at dispatch with the run's id (§2.2). Its action declares its class
    (`ActionProvider.side_effects`: `none`, `idempotent_key` or `unknown`, unstated reading `unknown`; named
    feature `action-side-effects`) and reads `ActionContext.idempotency_key`. At a start, a `none`,
    `idempotent_key` or never-started run is dispatched again through the fire path as a new run with
    `redispatch_of` and the original key; an `unknown` one becomes the trigger review's `unknown` card,
    whose `interrupted` kind goes.
- **One helper per start key.** `spawn(..., start_key="")`: a key matching a row accepted in the last 24
  hours answers that row's helper and starts nothing; the same key with other params answers `409
  helper_start_key_conflict` (`ERR_HELPER_START_KEY_CONFLICT`). `subagent_run` and `subagent_explore` take
  a key (§6.7), so the key core binds for their call is the start key; a trigger's agent passes its run id;
  a handoff passes `handoff:<message id>`. A model never sees or sets it.
- **The backfill and the restore.** At the release's first start, once: each `subagents/<id>/` folder with
  a `state.json` and no row becomes a helper row `interrupted` (`restart`), so the first notice names it;
  each spawn waiting to start (the landed fix's `queued` and `asking` records in the helper folders)
  becomes a `queued` row, whose start is decided anew; then the waiting-spawn records and their module go,
  and a helper's folder keeps only `result.txt`. A restore settles every unsettled row and dispatches none
  (`work_recovery.settle_restored(home)`): helpers `interrupted` (`restored`), jobs `ended`, automation runs
  as a cut run is closed.
- **Bounds.** At most 200 queued helpers in memory, the rest filling the window as it drains. Settled rows
  go seven days after settling, at most 20,000, pruned at the start and every 24 hours.

---

## 6. `outcome_unknown`

### 6.1 What it is

Status: planned

One rule for a write whose result could not be confirmed: it says it may have happened, nothing retries it
on its own, and whoever acts next reads the current state first. A failure counts as "nothing happened"
only when it proves the write never left: a refusal before sending, a connection that never opened, a
refusal before the request body was sent. Any failure after the body was sent and before a confirming
answer is `unknown`. A definite answer from the other side after it received the request (it accepted
it, it saved a draft, it refused it) is never `unknown`. `personalclaw/write_outcome.py` holds the rule
for every writer: `not_sent(exc) -> bool`, true only for a failure that proves the request never left, and
`unknown_error(what, check) -> AgentError`.

### 6.2 Today

Status: planned

`AgentError` has no such code (`errors.py:41-202`). An Inbox send keeps the text as the row's draft and
offers Send again on any failure, answering a flat `502` on an exception and a flat `409` on a falsy result
(`dashboard/handlers_inbox.py:962-994`). `send_reply` returns a truthy value (`inbox_providers/base.py:88-97`).
The mail apps report every SMTP failure alike (the mail-inbox app's
`mail_inbox_runtime.smtp_client.SmtplibSender.send`, and the email-channel app's
`email_runtime.smtp_client.SmtplibSender.send`). The MCP client answers a timed-out call as a
plain failure and can still send a queued call after its caller was told it failed; the agent's channel
send answers one flat failure for any delivery exception; a browse submit whose page cannot be re-read is
recorded as a failed form. A fix queued ahead of this release makes each say what is known in
today's codes and words; this section is the rule it feeds.

### 6.3 Where it appears

Status: planned

| Surface | Form |
|---|---|
| A tool call | `AgentError(code="ERR_OUTCOME_UNKNOWN", what=…, why=…, fix="Read <where> before trying again; never retry it blind.")` from `write_outcome.unknown_error(what, check)`; the tool part moves to `unknown` (§3.4) |
| A turn cut while a write ran | the tool part moves to `unknown`; the journal continues the turn as an inspect-only check, or skips it where a check cannot be held (§5.4, §5.5) |
| A route that wrote for the owner | `json_error("outcome_unknown", status=502, error_extra={"outcome": "unknown", "check": "<where to look>"})` |
| An Inbox reply | the row's `send_outcome: "unknown"`, and "May have been sent — check your Sent folder before sending again" (§6.4) |
| An automation's action | the action result's `outcome: "unknown"` (with `success: false` and the `ERR_OUTCOME_UNKNOWN` envelope); the run's status `unknown`, which counts as a failure for the failure count, is never parked, never retried, waits in the review as a run that may have done its work, and its notification says it may have happened. Only a provider whose writer classified the write sets it; a failure that proves nothing left stays a failure |
| A browse submission | `BrowseLoopResult.submissions[].outcome`: `sent` (the verification judged it worked), `not_sent` (refused before dispatch: no form, declined, not allowed), `refused_by_site` (the page changed and the verification judged it failed) or `unknown` (dispatched, then the page could not be re-read or judged, did not change, or the submit raised after the click). A run with an `unknown` submission ends with the action outcome `unknown`, is never retried, and says "May have submitted a form on {host}; check there before running this again." |
| A channel delivery | every message PersonalClaw posts on a channel goes through one send path (decision 38): `channel_sends.send(target, text, *, kind, chat, answers, key=None) -> SendRecord`, which posts the reply's pieces (`render_reply`) one by one through `post_piece(target, text, *, key, reply_to="") -> PieceReceipt`. A piece's `outcome` is `sent`, `not_sent` (only when the platform's answer proves nothing was posted: a refusal, a rate limit, the conversation gone) or `unknown` (any raise or lost answer after the request left); `find_sent(key)` answers True, False or None (cannot tell) and never raises, an offer, never proof. A file (`upload_attachment`) that raised `sdk.channel.DeliveryRefused` sent nothing; any other raise after it began is `unknown`. The agent's channel send (`POST /api/send-message`) answers an unknown send `502 outcome_unknown` (check: the channel) and tries nothing else; a message for the owner's own direct message is still passed to their next channel, then to the Inbox, with a reason saying the first may have it, since the owner is its only reader |
| An agent CLI's prompt | its stdin writer (`write(data, *, bound_secs=5)`, one lock per transport): no progress for 5 s raises `AcpWriteStalled` and marks the process dead. A prompt whose write stalled ends the turn `error`, code `prompt_delivery_unknown`, never resent; an approval answer whose write stalled says "Your answer did not reach <agent>." and the turn stops |
| An MCP call | the MCP call result, below |
| An app's own tool | `sdk.tool.outcome_unknown(what, check) -> ToolResult` (the named core feature `tool-outcome-unknown`), which its tool part reads as `unknown` |

The writers that classify: channel sends (each first-party channel app raises `DeliveryRefused(reason)`
only when nothing was posted: the service refused the first part of the message, the channel or person
does not exist, or a credential was missing before connecting; a message sent in parts whose later part
failed is not a refusal), Inbox replies (§6.4), an app route a tool called (a non-`GET` route whose
request raised after connecting answers `ERR_OUTCOME_UNKNOWN`, check "the app's own page"; a connect
failure is a failure), an MCP tool call not believed read-only, an app's own tool, and a browse submission.

**The MCP call result.** `McpServerConn.call_tool` answers one value. The MCP client owns the type (decision
27), which carries what the server sent; this section owns its outcome rule.

```
@dataclass(frozen=True)
class McpCallResult:                       # personalclaw.sdk.mcp
    outcome: Literal["ok", "failed", "not_sent", "unknown"]
    text: str                              # what the model reads
    blocks: tuple[ResultBlock, ...] = ()   # §3.3's tool-result blocks, kept as references
    structured: dict | None = None         # structuredContent, capped
    failure: str = ""                      # "" | refused | not_connected | sign_in | step_up | timeout | lost | error
```

| `failure` | `outcome` | When |
|---|---|---|
| `""` | `ok` | the server answered success |
| `error` | `failed` | the server answered with an error (`isError`, a JSON-RPC error) |
| `step_up` | `failed` | the server refused for a missing scope (403 `insufficient_scope`): its readiness reads `needs_access` |
| `refused`, `not_connected`, `sign_in` | `not_sent` | the egress guard refused it, nothing is connected, or a sign-in is needed (the request never left), or the server refused it at authentication before acting on it (nothing ran) |
| `timeout` | `not_sent` | the call's deadline passed before the worker marked it sent: the worker drops it unsent |
| `timeout`, `lost` | `unknown` | the deadline passed, the connection closed, or a transport error came after the worker marked it sent |

The connection's worker marks a call sent immediately before it hands it to the session, and never sends
one whose caller already gave up. The MCP tools app answers `unknown` as `ERR_OUTCOME_UNKNOWN` ("Read
{server}'s current state with its read tools before trying again"), unless the tool is believed read-only
under the owner's seal (§17.1), whose unknown is a failure; `not_sent` for a sign-in or `failed` for a
step-up answers the server's readiness (§7.6). `personalclaw.sdk.mcp` exports `McpCallResult` and
`ResultBlock`.

### 6.4 The reply contract (decision 20)

Status: planned

```
@dataclass(frozen=True)
class ReplyFileLimits:
    max_files: int = REPLY_MAX_FILES              # at most core's 10
    max_file_bytes: int = REPLY_MAX_FILE_BYTES    # at most core's 25 MiB
    max_total_bytes: int = REPLY_MAX_TOTAL_BYTES  # at most core's 25 MiB

@dataclass(frozen=True)
class ReplyPlan:
    action: Literal["send", "draft", "none"]
    sentence: str                          # what Send will do, shown above it; at most 200 characters
    files: ReplyFileLimits | None = None   # None: a reply here carries no files
    looks_up_sent: bool = False            # find_sent can look for a reply

@dataclass(frozen=True)
class ReplyResult:
    outcome: Literal["sent", "drafted", "not_sent", "unknown"]
    reason: str = ""      # a sentence the owner can act on; required unless sent
    reply_ref: str = ""   # the source's id for the reply or the draft, minted before sending where it can be
    where: str = ""       # drafted: where the draft is ("Drafts in you@example.org"); required then

class MessageSourceProvider(ABC):          # inbox_providers/base.py; personalclaw.sdk.inbox
    @abstractmethod
    def reply_plan(self, channel_id: str) -> ReplyPlan: ...                 # settings only, no network
    @abstractmethod
    async def send_reply(self, channel_id: str, text: str, thread_ts: str | None = None, *,
                         files: Sequence[Attachment] = ()) -> ReplyResult: ...
    async def find_sent(self, reply_ref: str) -> bool | None:               # None: cannot tell
        return None

@runtime_checkable
class OwnerReplyDelivery(Protocol):        # channel_delivery.py; personalclaw.sdk.channel
    def reply_plan(self, channel: str) -> ReplyPlan: ...
    async def deliver_reply(self, channel: str, text: str, thread_ts: str = "", *,
                            files: Sequence[Attachment] = ()) -> ReplyResult: ...
    async def find_sent(self, reply_ref: str) -> bool | None: ...
```

- `ReplyResult` refuses an outcome outside the four, a missing `reason` and a `drafted` with no `where`.
  A draft reaches no one, so its doubts are said in `where` and never make it `unknown`.
- A source classifies from proof by §6.1's rule, never sends twice on its own (no retry inside
  `send_reply`, and none in a client library under it), mints `reply_ref` before sending where the
  platform lets it choose an id (a mail's Message-ID), drafts when its plan says draft and says where,
  answers `not_sent` without a send for files its plan does not take, and never raises for a transport
  failure. `find_sent` answers True, False or None and never raises; it is an offer, never proof.
- A channel that speaks as the owner (`ChannelCapabilities.speaks_as_owner`) answers someone new held in
  the Inbox through `OwnerReplyDelivery`; a delivery that is not one answers `reply_not_sent`. Every other
  channel's delivery is untouched.
- `reply-outcomes` and `reply-files` name these contracts (§1.8). A clean SDK break: every first-party
  Inbox source and owner-speaking channel answers `ReplyResult` in the same release; the Inbox source
  conformance kit (`personalclaw.sdk.inbox.assert_inbox_source_contract`) fails a source that answers
  anything else, and the channel kit gains the owner-speaking clause. Core reads an answer through one
  module, `inbox_replies.py`, the only caller of `send_reply` and `deliver_reply`: a `ReplyResult` as it
  is; anything else, or a raise, is `unknown`.
- The Inbox row gains `send_outcome` (`""` | `sending` | `drafted` | `not_sent` | `unknown`; `sending` is
  written before the source is called), `send_reason`, `send_attempted_at`, `reply_ref`, `reply_where` and
  `reply_files`. A start-up pass turns a row left `sending` into `unknown` ("PersonalClaw stopped while
  this reply was being sent, so whether it went out is not known."). A second Send while one is in flight
  is refused.
- `POST /api/inbox/send` answers: sent → `200 {ok: true, sent: true, outcome: "sent", reply_ref}`;
  drafted → `200 {ok: true, sent: false, outcome: "drafted", where, reply_ref}`; not sent → `409
  reply_not_sent`; may have been sent → `502 outcome_unknown`; a source that is not connected → `503
  inbox_source_not_connected`. Sending again after `unknown` asks first (`confirmation_required`).
- Files are core's `attachments.Attachment` (name, type, bytes), resolved by core at send time from
  references the owner chose (an artifact version, an upload, a path re-resolved through the file scope),
  symlinks refused, at most 10 files and 25 MiB in all, lowered by the plan's limits; a source never
  receives a path. Every send's files pass the secret check, which asks the owner on a finding
  (`confirmation_required`), and no unattended path sends a reply: only the owner's Send does.

### 6.5 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| Nothing retries an unknown write on its own | no retry path catches `ERR_OUTCOME_UNKNOWN` (`test_no_retry_path_retries_an_unknown_write`); the Inbox never re-sends an `unknown` row without the owner's Send |
| `not_sent` only for a failure that proves nothing left | the conformance kit: refused before connecting → `not_sent`; the connection dropped after the body → `unknown` |
| A call whose caller gave up is never sent | `test_a_call_whose_caller_gave_up_is_never_sent` (a recording fake server records one call) |
| A channel send that raised after it began is unknown | `test_a_channel_send_that_raises_is_outcome_unknown` |
| A lost submit answer is unknown | `test_a_lost_submit_answer_is_unknown` |
| An `unknown` call makes the journal's automatic continuation an inspect-only check, or skips it where a check cannot be held | §5.6's per-reason table; `test_an_inspect_only_check_calls_only_replay_safe_tools` |
| A restart sends again only by class, and a replayed unit sends the same keys | `test_a_restart_sends_again_only_by_class`, `test_a_replayed_unit_sends_the_same_keys` |
| No unit of agent work reads `idempotent_key` in a restart decision | `test_agent_work_is_never_idempotent_key`, `test_a_helper_is_classed_by_its_parts` |
| An unkeyed cut piece of a channel send waits for the owner; one claimed message owes one reply | `tests/test_an_unkeyed_cut_piece_waits_for_the_owner.py`, `tests/test_one_claim_owes_one_reply.py` |
| The agent is told to read before trying again | the registry meaning and the `fix` line, asserted per writer |
| Only the owner sends a reply | a census of `send_reply` and `deliver_reply` callers: `inbox_replies.py` alone |

### 6.6 What it must not do

Status: planned

It never reconciles on its own; `find_sent` is an offer, never proof; it is never used for a read, whose
failure is just a failure. A key never reaches a model's arguments, and a re-run never carries a grant.

### 6.7 Idempotency keys (decision 35)

Status: planned

This release includes an idempotency-key contract for tools, so a restart or a re-run can repeat a keyed
write safely.

- **Declared.** `ToolDefinition.takes_key: bool` (for an app's tool through the SDK, named core feature
  `idempotency-keys`): the tool does its effect at most once per key and answers a repeated key with the
  first answer.
- **Minted and bound by core.** For a call to a tool that takes a key, core derives `idempotency_key =
  canonical_digest({unit, tool, input, n})`: `unit` the work the call belongs to (a chat turn's message and
  attempt, which a continuation shares with the turn it continues and a Retry does not, since "Run it
  again" means again; a worker conversation's work reference; a durable row's id), `tool` its stable name,
  `input` its canonical arguments, `n` its count among identical calls in the current turn. The key is
  bound around the invocation (a context variable the tool reads with `sdk.tool.idempotency_key()`; the
  tools PersonalClaw serves its agent CLIs read the same), never put in the model's arguments (an argument
  of that name is dropped before the call runs), and kept on the tool part with `key_honored`. A tool reads
  the bound key and derives one of its own only when none is bound, never from `tool_call_id`, which a
  re-run does not keep.
- **The class rule** (§3.5): `turns.repeat_class(part)` and `turns.side_effect_class(parts)`. **A unit of
  agent work** (a helper, a handoff's helper, an automation's agent) is never `idempotent_key`: run again,
  its model does not make the same calls with the same input, so a key cannot be counted on to match, and a
  unit whose parts read `idempotent_key` is handled as `unknown` (decision 35). The work queue's pass
  (§5.9), the continuation's text ("repeating it is safe: it carries the same key") and the restart notice
  read it.
- **The first declarers.**
  - **The agent's channel send** (decision 38). `send_message` (the agent's `notify` tool) takes a key.
    `POST /api/send-message` keeps the call's key on its send's record (`channel_sends`' `call_key`) and
    answers a kept key `{ok: true, sent: true, duplicate: true}` without posting. Each piece of every send
    carries its own key, `<send id>.<n>`, stored before the piece is first handed over, and a replay sends the
    same key. A channel whose service drops a repeat of a key within a window declares
    `ChannelCapabilities.send_keys: SendKeys {window_s}` (under `channel-facts`; there is no other
    declaration): a piece whose outcome is unknown is sent again with its key inside the window, and the send
    is `key_honored`. A started channel send is safe to repeat only when keyed this way; any other is
    `unknown`, and every pass that runs work again reads this one rule. The landed delivery check
    (`received.py`) stays the inbound half: one claimed message owes at most one reply. At a start, before a
    channel's receiver takes a new message, the channel send pass sends `pending` pieces again with backoff, sends
    `sending` pieces again with their key where the window holds, looks them up where it can say, holds the
    rest for the owner, writes `channel.reply_resent` for each and lists each on the restart notice (§5.8).
  - **Memory writes with stable ids**: the memory tools take a key and read the bound one.
  - **A helper's start**: `subagent_run` and `subagent_explore` take a key, and the bound key is the
    helper's start key (§5.9); a handoff's is `handoff:<message id>`.
  - **An automation's action** reads `ActionContext.idempotency_key`, and a re-dispatched run passes the
    original key (§5.9).

---

## 7. The readiness vocabulary

### 7.1 What it is

Status: planned

One function answers "can this capability be used now, and if not, what does the owner do":
`readiness.capability_readiness(subject) -> Readiness`. Every surface that says a capability is not
ready (a Tools card, a Store card, a Settings provider card, the composer, a tool call's failure, the
Doctor) reads it; none composes its own sentence. `readiness.py` holds no store, starts no probe and
imports nothing from `dashboard/`, so agents and the Doctor read it without the dashboard loaded.

### 7.2 States (decision 8)

Status: planned

| State | Meaning | Default action |
|---|---|---|
| `ready` | can be used now | `none` |
| `checking` | being measured or started; no answer yet | `none` (the surface shows progress) |
| `switched_off` | the owner turned it off | `switch_on` |
| `waiting_for_allow` | its definition waits for the owner's Allow | `allow` |
| `needs_setup` | the owner must configure something first (a key, a URL, a choice) | `open_settings` |
| `needs_sign_in` | an account must be signed in, or signed in again | `sign_in` |
| `needs_access` | it needs more access than the owner gave it (a permission, a folder, a host, a scope) | `allow_access` |
| `needs_prerequisite` | something outside the capability must be installed: a program, a server, or an engine PersonalClaw installs | `install` when PersonalClaw can install it, else `open_details` (the prerequisite's `how`) |
| `not_installed` | named, but not installed | `install` |
| `exhausted` | a limit it is held to is used up until a known time (a spend cap, a quota) | `wait_until` |
| `failed` | it failed and can be tried again | `retry` |
| `stopped` | it failed repeatedly and PersonalClaw stopped trying | `retry` |
| `unavailable` | measured: it cannot run on this machine | `open_details` |
| `withdrawn` | its source withdrew it | `uninstall` |
| `unknown` | the check itself failed or never finished | `check_again` |

Fifteen, not seven: `needs_engine` folds into `needs_prerequisite` (an engine is a prerequisite
PersonalClaw can install, so the action says which), and the other added states name facts the code
already records (§7.4) that a shorter list would have merged into a wrong word (`checking` and `unknown`
are not `unavailable`; `switched_off` is the owner's choice, not a failure). A rail fails on any producer
reading outside these fifteen. Their words are one table in core (`readiness.STATE_WORDS`, generated into
the dashboard).

### 7.3 Shape

Status: planned

```
@dataclass(frozen=True)
class Action:
    kind: Literal["none", "check_again", "switch_on", "allow", "open_settings", "sign_in",
                  "allow_access", "install", "wait_until", "retry", "open_details", "uninstall"]
    label: str        # the button's words: "Sign in", "Allow", "Install <the prerequisite's name>"
    where: str        # a dashboard route, "#/tools/mcp/<server>"; "" for none
    until: str = ""   # wait_until only: the instant it becomes usable again

@dataclass(frozen=True)
class Readiness:
    subject: str      # the grammar below
    label: str        # the subject's name as the owner reads it: "notes", "Mail Inbox", "Mail Inbox · Inbox"
    state: str        # §7.2
    reason: str       # one sentence for the owner, redacted; "" for ready
    action: Action
    since: str        # instant this state was entered
```

**Subjects.** The kinds are closed, each with exactly one producer, registered through
`readiness.register_producer(kind, producer)`:

| Kind | Grammar | Names |
|---|---|---|
| `mcp` | `mcp:<server>`, `mcp:<server>/<tool>` | the `mcp.json` key (an app's is `<app>:<server>`); the tool as its server lists it |
| `app` | `app:<name>`, `app:<name>/<capability>` | an installed app; a #3843 capability id |
| `channel` | `channel:<app>` | a channel app |
| `provider` | `provider:<name>` | a provider record as `GET /api/providers` names it |
| `model` | `model:<entry>:<model>` | a model ref as `ModelProvider.served_ref` stamps it |
| `skill` | `skill:<name>` (a library skill); `skill:<folder_key>:<name>` (a skill a folder carries, §15.3; a library name never holds `:`) | |
| `agent` | `agent:<name>` | a configured agent |

`skill` and `agent` join the closed set in the change that registers their producers, so the rail holds
every kind both ways. `parse_subject(text)` refuses any other kind, an empty name, a name over 200
characters and a control character. A subject that names nothing installed or configured answers `404
readiness_subject_unknown` on the route and `not_installed` in a tool's answer.

**Facts and precedence.** A producer answers `(label, facts)` for a name, each fact `{state, reason,
action, label, where, until, measured_at}`, read only from records that already exist; no fact means
`ready`. With several facts, `capability_readiness` answers the first state in one fixed order, with that
fact's reason and action, so the owner reads the first thing they must do: `withdrawn`, `switched_off`,
`waiting_for_allow`, `not_installed`, `unavailable`, `needs_prerequisite`, `needs_setup`,
`needs_sign_in`, `needs_access`, `stopped`, `failed`, `exhausted`, `unknown`, `checking`, `ready`.
`readiness.touch(subject)` recomputes a subject and sends `readiness_changed` when its state, reason or
action changed; `not_ready_error(r)` builds the one agent error (§7.6).

### 7.4 Producers

Status: planned

| Today | File | Maps to |
|---|---|---|
| provider availability `checking`, `available`, `unavailable`, `unknown` | `providers/availability.py:63-66` | `checking`, `ready`, `unavailable`, `unknown` |
| an MCP server switched off (`disabled`); waiting for the owner's Allow (`waiting`) | `mcp_discovery.py:296, :556, :856-1098` | `switched_off`; `waiting_for_allow` |
| an MCP server `unknown`, `probing`, still starting (`mcp_status.still_starting`; `mcp_client.py:504`) | as above | `checking` |
| an MCP server `ok` | as above | `ready` |
| an MCP server connected but not served (`unserved`): its provider switched off, or failed to load | `as_agents_see_it`, which says which | `switched_off`; `failed` |
| an MCP sign-in `required` or `signed_out` | `mcp_oauth.py:1001-1003, :1006`; `SignInRequired`, `:87` | `needs_sign_in` |
| an MCP server that asked for a scope its sign-in lacks (a recorded step-up) | the connection's auth flow | `needs_access` |
| an MCP start failure, by its closed cause (`mcp_status.CAUSES`): `missing_value`, `foreign_value`, `bad_name`, `no_command`, `needs_token` | the probe | `needs_setup` |
| the cause `command_not_found` | the probe | `needs_prerequisite` |
| the cause `egress_refused` | the probe | `needs_access` |
| the causes `timed_out`, `exited`, `closed`, `transport`, `catalog_refused` | the probe | `failed` |
| an MCP server stopped after three failed starts | `mcp_status.py:42, :59` | `stopped` |
| an MCP tool: its server's readiness, then switched off by the owner (`disabledTools`), then left out by the catalog's ceilings; a tool its server marks for its view only | the server row | as its server; `switched_off`; `failed`; `not_installed` to an agent |
| an app's sidecar engine not yet installed | `local_models/sidecar.py` | `needs_prerequisite`, action `install` |
| a capability's status `off`, `needs setup`, `starting`, `running`, `failed`, `unavailable` (planned, #3843) | — | `switched_off`, `needs_setup`, `checking`, `ready`, `failed`, `unavailable` |
| an app account's sign-in, and a feature that needs more access than its consent gave (planned, #3862) | — | `needs_sign_in`; `needs_access` |
| an app on the withdrawal list (planned, #3851) | — | `withdrawn` |
| an app's Check (its `cli.doctor` run out of process after an install, an enable, a settings save or the owner's Check again): a failing line with `needs` (`setup`, `sign_in`, `access`, `prerequisite`, the named core feature `app-check-needs`); a failing line without; a Check that did not finish | the app Check board | `needs_setup`, `needs_sign_in`, `needs_access`, `needs_prerequisite`; `failed` ("Check again"); `unknown` |
| a provider that failed to load, a backend that exited, a backend stopped after a crash loop (#3863) | the provider registry; the backend supervisor | `failed`; `stopped` |
| a spend cap reached for today | `guardrails/budgets.py:596` (`check_day`) | `exhausted`, `until` the next day's start in the owner's time zone, on the `model:` subject of a priced model |
| a provider's own quota reported used up | that provider app's fact, recorded by the allowance work (PersonalClaw/PersonalClawApps#173), read through the `model:` producer | `exhausted` |
| a library skill archived by the curator | `skills/readiness.py` | `switched_off` |
| a folder's skill offered and never allowed, or changed since the owner allowed it; a folder the owner declined; a folder skill its scan refused | `skills/readiness.py` (§15.3) | `waiting_for_allow`; `switched_off`; `unavailable` |
| a native agent whose pinned model cannot run | `providers/provider_bridge.agent_model_problem` | `needs_setup` |
| an agent CLI runtime not found; one that needs its sign-in; one whose last Test timed out or failed; its runtime's app switched off | `agents/readiness.py` | `needs_prerequisite`; `needs_sign_in`; `failed`; `switched_off` |
| a skill's declared requirement, measured: a `program` or every `any_program` missing; a `secret` not set; an `app` not installed; an `os` it does not run on (`tool` and `fallback_for` are decided per agent where surfacing runs, never a readiness fact) | `skills/readiness.py` | `needs_prerequisite`; `needs_setup`; `not_installed`; `unavailable` |
| an MCP server the operator ceiling's `servers` scope refuses (§8.5) | the ceiling read model | `unavailable`, cause `ceiling_refused`, action `open_settings` (`#/settings/security`) |
| a provider the ceiling's `providers` scope refuses | the ceiling read model | `unavailable` |
| a provider in a cooldown the provider-failure record holds (§17.16) | the record | `exhausted`, `until` the cooldown ends, on the `model:` subject |

An app's declared prerequisites (`requires: [{name, why, how}]`, `manifest.Prerequisite`) are a
declaration, never a fact: they stay information on the card ("Needs an image server"), and
`needs_prerequisite` comes only from a measurement (the engine check, or a Check line marked
`prerequisite`). An agent CLI runtime nobody has tested adds no fact: readiness never starts an agent CLI
to find out.

### 7.5 Reads and writes

Status: planned

- `GET /api/readiness?subject=<subject>` → `Readiness`; `GET /api/readiness?subjects=<a>,<b>` (at most
  100) → `{items: [Readiness], unknown: [subject], next_cursor: null}`. Neither, both, or more than 100:
  `400 bad_request`. A read never probes.
- Owner and app tokens both read it. An app token reads the subjects its app owns (`app:<name>`,
  `app:<name>/*`, `channel:<name>`, `mcp:<name>:*`) and the MCP servers its manifest declares it uses
  (`dependencies.marketplace.mcp`, entries a server id or `{id, managedBy}`, `apps/manifest.py:766`); any other subject answers exactly as an unknown one does (404, or listed in `unknown`), so the
  route is no oracle for what else is installed.
- The existing rows embed it: `GET /api/mcp` server rows and `GET /api/mcp/servers/{name}`, `GET
  /api/apps` rows (and each capability's, once #3843 lists them), `GET /api/providers` rows, and the
  skills and agents lists carry `readiness`. Their current status fields stay where other code reads them,
  and each is produced from the same reading in the same request.
- `readiness_changed {subject, readiness}` is sent when a subject's state, reason or action changes,
  coalesced to one frame per subject per 250 ms; a complete value. It carries no conversation, so an app
  socket hears it only for the subjects its app may read (`ws_state.py:96-114`'s gate gains the subject
  rule), and every consumer re-reads on reconnect.

### 7.6 What an agent is told

Status: planned

A tool call that fails because a capability is not ready answers
`AgentError(code="ERR_CAPABILITY_NOT_READY", what=<the subject's label> + " is not ready", why=reason,
fix=<the action in words, naming where the owner takes it>)`, built only by `readiness.not_ready_error`
(an AST rail), and the tool part carries the `Readiness` (§3.3; `personalclaw.sdk.tool.ToolResult` gains
`readiness`). The agent relays the action; it never claims the capability is ready and never works around
it. A call whose subject reads `checking` or `unknown` keeps its own failure.

`tool_search` (the native runtime's, and the compact tool list's facade, `compact-tools`) answers its hits,
then at most five lines naming the not-ready MCP servers and apps whose name or label shares a word with
the query ("Not ready now: notes (MCP server) needs you to sign in; the owner signs in on the Tools page.")
(decision 28). The model's tool list carries no per-tool readiness: a not-ready server's tools are not
listed and a switched-off tool is not offered, so a per-tool field there would only ever read ready. A call
made through the facade's `tool_run` is decided, gated and audited as its inner call (§3.3 `via`).

**`tool_run` has one definition**, shared by the compact tool list and the native bridge (whichever lands
first writes it, and the other reads it): `tool_run {name: str, arguments:
object}`, beside `tool_search`, `tool_schema(name)` and, where tools are grouped, `reset_tools`. The
runtime unwraps it before any gate, so the reservations, the deny list, the pre-tool hooks, the task mode,
the run's grants, the approval and the loop breaker all see the inner call; its `arguments` are checked
against the inner tool's own schema, so a bridged call is refused for a missing argument exactly as a
direct one. A meta tool as the target answers "tool_run runs a tool; it cannot run tool_search,
tool_schema, tool_run or reset_tools."; a name the run does not have or does not show answers the unknown
tool's words. The catalog note says "To use a tool, call tool_schema("name") to see its inputs, then call
it with tool_run(name, arguments)." A call the model makes by a name the request did not declare is written
to the runtime's history as the same call through `tool_run`, so a history names only declared tools
(§17.12).

### 7.7 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| Every producer maps to exactly one state, and every state has a producer | `test_readiness_maps_every_producer`, both directions (§1.9), with every MCP cause and the two producers that register later (`skill`, `agent`) |
| Every state has a default action and every action a label, a sentence and a place | the same test |
| No surface composes a readiness sentence of its own | a web rail fails on a status-to-sentence map outside the readiness component; a core rail fails on a not-ready `AgentError` built without the function |
| A declaration is never a measurement | `test_a_declared_prerequisite_is_not_a_measurement` |
| A read starts no probe and no agent CLI | `test_readiness_runs_no_probe_on_a_read`; `test_readiness_starts_no_agent_cli` |
| An app hears and reads only what it may | `test_readiness_frame_reaches_only_an_apps_own_subjects`; the route's app-scope test |
| Readiness never widens anything | it is read-only; nothing reads it to decide a gate |

### 7.8 What it must not do

Status: planned

It is not health or quality (no `degraded`); it never runs a probe on a request (it reads the boards that
already measure); it never decides an approval; `reason` never carries a secret or a command line.

---

## 8. Effective permissions and the operator-ceiling read model

### 8.1 What it is

Status: planned

Two read models over the rules that already decide, and one function that both the real gates and the
read models call, so "what would happen" and "what happens" cannot disagree:

- **Posture:** for a chat (or a project, or an agent with no chat), each kind of action, whether it would
  run, ask or be refused, which setting decided, and where that setting is changed.
- **The ceiling read model:** what the operator's governance file bounds, from where, as which digest.

### 8.2 Who decided: two existing vocabularies

Status: planned

Two closed vocabularies already say who decided a call; every surface of this release uses them as they are, and
each becomes a closed set in code so the both-ways rail (`tests/test_decision_words_both_ways.py`) can read
it (decision 22):

- **`decided_by`**, who or what let a call run or answered its ask: `approval_grants.DECIDED_BY_WORDS`,
  the grant words of `approval_grants.py:43-130` (`you`, `nobody`, `declared_read`, `work_asks`, `trust`,
  `yolo`, `trust_reads`, `agent_floor`, `parent_trust`, `approval_mode`, `loop_mode` (landed: a loop's
  own grant to its sessions), `trigger_grant`, `heartbeat_task` (landed: a heartbeat task's calls on the
  owner's Allow of it), `setting`, `hook_setting`, `source`, `cli`, `app_grant`, `remembered`,
  `approved_before_resume`, `batch_allowed`, `gate_policy`, `acp_mode`, `session_policy`, `auto_execute`,
  `result_injection`, `eval_safe_tools`), plus `no_approval_needed` (a native tool whose definition asks
  nobody and that is not a declared read; today's audit rows already write it,
  `llm/events.unasked_reason`, `llm/events.py:185`), the mini apps' `miniapp_writes`, and this
  release's `chat_pattern` (§9), `trust_edits` (Trust edits here, a write inside the chat's folder),
  `standing_rule` (a standing allow rule, §9.10; `hook_pattern` becomes it with the fold of the old pattern
  keys, decision 41, and every reader of `hook_pattern` follows in that change) and `allowed_for_run` (an
  automation run's one-run allowance, §9.10). Their sentences stay in that module (`setting_sentence`,
  `:338-359`), and `approval_grants.grant_phrase(word)` gives each one phrase for the posture view ("Normal
  mode", "Trust reads", "the tool declares it only reads", "Always in this chat", …). `CHAT_PATTERN` and
  `TRUST_EDITS` join `OWNERS_OWN`: they stand only for the owner's own work.
- **`refused_by`** with **`refused_rule`**, which gate refused a call before anyone could be asked:
  `llm/events.REFUSED_BY_WORDS` (today a comment at `TOOL_META_REFUSED_BY`, `llm/events.py:120-128`:
  `agent_tools`, `deny_list`, `task_mode`, `tool_grants`, `hook`, `loop_breaker`, `unknown_tool`,
  `withdrawn`, `run_bounds`, `dry_run`), plus `no_one_to_ask` (an ask in work nobody can answer: an
  unattended run, or a gateway with no approval surface, whose audit row keeps today's
  `unattended_no_one_to_ask` and `no_approval_surface`), `inspect_only` (an inspect-only check's gate
  refuses a call that is not replay safe, §5.5), `no_fence` and `fence_not_applied` (§17.8),
  or a tool's own control (`:130-139`: `shell_denylist`, `sensitive_path`, `own_store`, `owner_only`,
  `scheduler`, `file_scope`, `approval_drifted` (a sealed command whose program changed, §9.3), and the
  memory refusals), with the rule in words. A call the landed hooks step refused is audited `decided_by:
  hook` (`pre_tool_hooks.DECIDED_BY_HOOK`), its outcome `hook_blocked` or `hook_error`.
- **`asks_because`**, why a call asks (§8.3), is the third closed set, `approval_decision.ASKS_BECAUSE`.

The operator ceiling refuses standing grants, not calls: a call whose grant it refused asks the owner
(`approval.grant_refused`, `refusal_sentence`, `:265-284`), so it appears as `ask`, with the ceiling named
in the posture row's sentence. A new refusing gate adds its word to `REFUSED_BY_WORDS`; a new grant adds
its word to `DECIDED_BY_WORDS`.

### 8.3 One function (decision 9)

Status: planned

```
@dataclass(frozen=True)
class Declares:              # what the call's tool declares (its definition, or an agent CLI's frame)
    risk: str                # the RiskLevel value, "" when it declares none
    asks_first: bool         # ToolDefinition.requires_approval; True for an agent CLI's call
    builds: bool = False; proposes: bool = False; tells_owner: bool = False; work_asks: bool = False
    annotations: Mapping[str, Any] = MappingProxyType({})
    label: Literal["none", "believed", "untrusted", "unreviewed"] = "none"   # a read-only label, §17.1
    asks_person: bool = False                # only a person may answer it (`asks-person`, §1.8)
    result_origin: frozenset[str] = frozenset()   # untrusted, private: what its result will carry

@dataclass(frozen=True)
class Call:
    tool: str; tool_kind: str; input: object; declared: Declares
    session: str; agent: str; source: str   # dashboard | channel | subagent | room | eval | trigger

@dataclass(frozen=True)
class Context:
    runtime: Literal["native", "agent_cli"]
    holder: Literal["chat", "channel_chat", "none"]   # none: work no chat holds
    unattended: bool; app_conversation: bool; surface: bool   # surface: someone can be asked
    task_mode: str; dry_run: bool
    agent_tools: AgentToolList | None        # native: the agent's tool list
    offered: frozenset[str] | None           # native: the turn's catalog; None when not known
    withdrawn: frozenset[str]
    tool_grants: Callable[..., str] | None   # the host's grants for this run
    cwd: str; roots: tuple[str, ...]; scratch: str
    hooks_config: HooksConfig
    hooks: Literal["dry", "pending", "passed", "blocked", "asked"]; hook_reason: str; hook_name: str
    tool_refusal: ToolRefusal | None         # the tool's own pre-run control, when the gate ran it
    trust: bool; trust_reads: bool; trust_from_floor: str; trust_edits: bool
    yolo: bool; yolo_expires_at: str
    policy: str; policy_by: str              # work no chat holds: its policy and the grant word that set it
    chat_grants: tuple[ChatGrant, ...]
    narrow_grants: tuple[str, ...]           # grants standing where Trust stands (the mini apps' miniapp_writes)
    asked_by: Mapping[str, str]              # the turn's asker (memory_writes.asker()); {} for the owner's own work
    rules: tuple[Rule, ...]                  # standing rules in scope (§9.10): home, the agent's, the chat's
                                             # holds, and an automation run's book and one-run allowances
    withheld: frozenset[str]                 # grant words an unreadable restriction withholds (§1.2)
    task_ref: str                            # the goal loop or loop run the call belongs to; ""
    now: str                                 # the instant `until` grants and YOLO are read at
    placement: Placement | None              # where a shell call runs (§17.8)
    facts: TurnFacts                         # read_untrusted, read_private, input_flagged, call_request

@dataclass(frozen=True)
class Decision:
    verdict: Literal["run", "ask", "refuse"]
    decided_by: str = ""        # run: the grant word (§8.2); ask and refuse: ""
    refused_by: str = ""        # refuse: the gate's word (§8.2)
    refused_rule: str = ""      # refuse: the rule, in words
    expires_at: str = ""        # a time-boxed grant's end (YOLO)
    level: str = ""             # LEVEL_AUTO or LEVEL_HOOK, as approval_grants.stands_for_call takes it
    grant_id: str = ""          # the chat grant that answered (§9)
    asks_because: str = ""      # ask: a closed word (below)
    answered_alone: bool = False   # ask: no standing answer may be offered
    hooked: bool = False        # a dry read: a pre-tool hook runs first and may refuse
    awaits: Literal["", "hooks"] = ""   # the gate: run the pre-tool hooks, then decide again
    ceiling_refused: tuple[str, ...] = ()   # grants the ceiling refused on the way
    placement_required: Literal["", "bounded", "fenced"] = ""   # bound into the call (§17.8)
    reach_cleared_by: str = ""  # the host grant that cleared an off-list reach (step 11)

def decide(call: Call, context: Context) -> Decision: ...   # approval_decision.py
def audit_fields(decision: Decision) -> dict[str, str]: ...   # the audit row's metadata, today's words
```

- `decide` is pure: it reads what `Context` hands it, the config readers it names, and the ceiling through
  `approval_grants.stands_for_work(word, asked_by=…, audit=False)`; it writes nothing, asks nothing and
  runs no hook. It is the one copy of the order, the first step that answers deciding ("native": an input
  only the built-in runtime supplies):

  | # | Step | Answer |
  |---|---|---|
  | 1 | the agent's tool list (native) | refuse `agent_tools` |
  | 2 | observe mode, not a declared read | refuse `dry_run` |
  | 3 | the deny-list: the screen, and every `deny` rule in scope (§9.10) | refuse `deny_list` or the screen's control, with the rule, its `why` and its `instead` |
  | 4 | the task mode | refuse `task_mode` |
  | 5 | the host's tool grants | refuse `tool_grants` |
  | 6 | an unknown, invalid or withdrawn tool | refuse `unknown_tool` or `withdrawn` |
  | 7 | the tool's own pre-run control | refuse with its word (`shell_denylist`, `file_scope`, `owner_only`, `approval_drifted`, …) |
  | 8 | the run's bounds (`run_bounds`) | unattended: refuse `run_bounds` (a delete of a protected folder included, naming the folder), but for a host an automation run's own one-run allowance admits; attended and off the allowed hosts: noted for step 11 |
  | 9 | the operator's blocking hooks | `pending` with a matching hook: `awaits: "hooks"`; `blocked`: refuse `hook`; `asked`: ask alone, `hook_ask`; `dry`: go on, `hooked` |
  | 10 | asks every time | an `ask` rule in scope, or `asks_person`: ask alone (`owner_rule`, `asks_person`). For a call that does not declare it only reads: a persistence path (§17.8), a call request (a turn whose root message has `input_origin: call`, a helper it started and a continuation of it, voice), a flagged input, and a delete of the owner's home folder, the filesystem root or the folder the call runs in, or of a path the command reader cannot name (landed: `run_bounds.put_to_a_person`, read on the command through `protected_folders`): ask alone, by its word |
  | 11 | a reach only a host answer may clear | an off-list host (step 8), or a tainted egress: a `host` chat grant covering every such host clears the reach (`reach_cleared_by`) and the order goes on; otherwise ask with `off_list_host` or `tainted_egress`, alone unless a host answer may be offered (§9.11) |
  | 12 | a standing allow rule (§9.10) | run `standing_rule`, or `allowed_for_run` for an automation run's one-run allowance, at `LEVEL_HOOK` |
  | 13 | what the call declares | run `declared_read`, `work_asks` or `no_approval_needed` |
  | 14 | Trust reads, effective risk `safe` | run `trust_reads` |
  | 15 | a narrow grant (the mini apps' `miniapp_writes`) | run its word |
  | 16 | Trust edits here, a write inside the chat's folder | run `trust_edits` |
  | 17 | Trust, YOLO, the agent's floor, or the policy of work no chat holds | run its word; never in an app's conversation |
  | 18 | a chat grant (§9) | run `chat_pattern` with `grant_id`; never for `destructive`; for `unchecked` only a `fenced` grant while the call's run is fenced (§9.4) |
  | 19 | nobody to ask (`unattended`, or no surface) | refuse `no_one_to_ask` |
  | 20 | the owner | ask, with `asks_because` |

  Every refusal comes before every grant. Each grant step (11, 12, 14-18) asks the ceiling at its level: a
  grant the asker rule holds back asks `someone_else` (the card names the asker); a grant the ceiling
  refuses joins `ceiling_refused` and the order goes on, so the ceiling turns a would-be run into an ask,
  never into a refusal. A grant word in `withheld` does not stand (`restriction_unreadable`). **No fence**:
  while a shell call's `placement.fence` is `none`, steps 12-18 do not stand for it, but
  for Trust reads of a read (`asks_because: no_fence`; unattended work is refused `no_fence` unless
  `security.shell_without_sandbox` is `allow`), and `placement_required` binds a standing answer to the
  fence it assumed (§17.8); a call the owner chose to run outside the fence asks `outside_fence`. A batch
  sibling's refusal is the consequence of an answer, not a rule, so it stays in the gate. A chore's call is
  refused before any gate and is never decided (landed).
- It replaces every copy of the order in the same change: the chat runner's permission path
  (`chat_runner.py:4063-4440`), the channel turns' copy (`chat_trust.chat_grant`, `chat_trust.py:94-159`),
  the native runtime's own order, the subagent relay's, the room member gate's and the evaluation
  runner's. An agent CLI's call meets the pre-tool hooks before any pattern, Trust or YOLO approval, as
  the built-in runtime's does.
- **Hooks.** A pre-tool hook can block and may have side effects, so `decide` never runs one. The landed
  fix asks the operator's blocking hooks on every path at step 9 through one module, `pre_tool_hooks`: a
  chat's gate, a helper's, the background helper's (a heartbeat task, a helper's report, a room member's
  turn), an evaluation's, a channel's own turn (`personalclaw.sdk.channel.ask_pre_tool_hooks`, named
  feature `pre-tool-hooks`), and the native runtime's own step, whose permission request then carries
  `AgentEvent.hooks_asked` so no host asks them twice; a hook that blocks or fails to run refuses the call
  (`HooksSaid`). The gate calls `decide` with `hooks: "pending"`; on `awaits: "hooks"` it asks them once
  through that module and calls `decide` again with `passed`, `blocked` or `asked` (a hook's typed answer
  `ask`, approvals', with `hook_name`). `decide` adds no firing of its own. A dry read passes `dry` and
  reports `hooked` ("A hook you set may decide"); `hook_setting` stays the word of the subagent hook
  settings it names today.
- **A handoff's helper**. The `Context` of a call by a helper a handoff started carries its
  parent chat's `trust` and `yolo` (`decided_by: parent_trust`, `yolo`) and its `task_mode`, and nothing
  else that stands for that chat's agent: `trust_from_floor` is "", `policy` is "" (no Settings mode and no
  `hook_setting`), `chat_grants` is empty. Every deny and ask rule in scope binds it as anywhere; of the
  allow rules, the owner's home rules stand as they do in the chat, and an automation's never. Its spawn is
  decided `you`: the owner asked for it by their mention.
- The real gates call it and then do what it says: raise the approval, run the hooks that have effects,
  write the audit row from `audit_fields` (today's words, so the Audit page and its census read them
  unchanged), and for each word in `ceiling_refused` write the `approval.grant_refused` row. The posture
  view calls it with `audit=False` semantics: no row is written for a dry read (§1.7). A decision that
  raises is an ask, logged.

`asks_because` is closed (`approval_decision.ASKS_BECAUSE`), each with one sentence composed in
`approval_decision.ask_sentence`: `no_grant` (nothing set answers this call), `may_destroy` (only Trust
or YOLO answer a call that may delete something or could not be checked), `off_list_host` (it reaches a
host off the allowed hosts), `tainted_egress` (the turn read private data and outside text, and this call
sends data to a host), `ceiling` (the operator ceiling says every call that needs approval asks a person),
`someone_else` (the asker rule's line), `owner_rule` (the owner's rule asks every time), `hook_ask` (a hook the owner
set asks), `persistence_path` (it writes a place the owner's tools run later), `asks_person` (the server says only a
person may answer this tool), `flagged_input` (the turn started from words the screen flagged),
`grant_drifted` (a sealed grant's program changed), `restriction_unreadable` (a restriction could not be
read, so the grants it qualifies answer nothing), `consult` (a question to a model elsewhere), `call_request`
("You asked for this in a voice call, so anything that changes something asks you here."), `no_fence` and `outside_fence` (§17.8's sentences), `unreviewed_tool` (a trusted server
changed this tool since the owner trusted its labels), `untrusted_labels` (the server's read-only labels
are not trusted), `answered_alone` (only the owner answers this kind of question), `app_conversation`
(this conversation belongs to an app, so the owner's switches do not approve its calls), `protected_folder`
(the landed sentence that names the folder: "This would delete your home folder"). A design that adds a
step adds its word and sentence in the same change.

### 8.4 Posture

Status: planned

`GET /api/chat/sessions/{session}/posture` (`404 session_not_found`; a chat on disk only is read from its
record, with no Trust, which lives in memory), and `GET /api/posture?project=<id>&agent=<name>` for a new
chat with that agent (default: the default agent) in Normal mode or the project's default permission mode
(§13.3), with a second block for work no chat holds (`404 agent_not_found`, `404 project_not_found`). Both
owner only; no app token reads a posture route.

```json
{"session": "chat-12-1759561200", "computed_at": "…", "runtime": "native", "mode": "normal", "ungated": "",
 "rows": [{"action": "edit_files_here", "verdict": "ask", "decided_by": "", "asks_because": "no_grant",
           "hooked": false, "sentence": "Asks before changing files in ~/code/app",
           "setting": "Normal mode", "where": "", "expires_at": "",
           "exceptions": [{"kind": "chat_grant", "id": "cg_…", "label": "edit_file in src/"}]}],
 "tools": {"offered": 41, "servers": [{"subject": "mcp:files", "readiness": {…}}],
           "missing": [{"tool": "bash", "why": "this agent's tool list leaves it out"}],
           "asks_until_reviewed": [{"server": "files", "tools": ["move"], "where": "#/tools/mcp/files"}]},
 "ceiling": {"state": "bounded", "digest": "…", "notice": ""}}
```

Rows, a closed list: `read_files`, `edit_files_here` (the working folder), `edit_files_elsewhere`,
`run_read_only_command`, `run_other_command`, `fetch_allowed_host`, `fetch_other_host`, `send_on_channel`,
`start_helpers`, `spend_past_today`. Each row's verdict is `decide` on a representative call of that kind
(with `hooks: "dry"`), except `start_helpers`, which reads the spawn's own grant through the one function
the spawn path also calls, and `spend_past_today`, which reads `SpendMeter.check_day`
(`guardrails/budgets.py:596`) and `GET /api/usage/budget` (`dashboard/handlers/usage.py:228`). A row whose
answer is a tool's own control reads that control's pure check. `setting` is the deciding grant's phrase
(§8.2), or for an ask the chat's mode, naming where a mode came from when a project's default or the
agent's floor set it (`project_defaults.effective`, §13.3: "Trust reads, from project Billing API");
`where` deep-links the setting that decided; `exceptions` (`{kind, id, label}`) lists what covers the
row's kind beside the deciding setting: `chat_grant` (with `lasts`, `until_at`), `chat_hold`, `rule` (a
standing rule, §9.10), `yolo_expiry`, `access` (an access request's folder or host), and `others_turns` in a
chat others can speak in ("In a turn someone else asks for, these ask you"). The two command rows carry
`placement`, the fenced shell's `Placement.as_fact()` (`{runs_in, network, sees, memory, fenced, fence,
unavailable_reason}`, produced by `fence.placement`, §17.8) for the owner's next turn, with its sentence;
an agent CLI reads "{CLI} runs its commands itself; PersonalClaw does not fence them"; a chat in its own
worktree names it ("in its own worktree"). In plan and ask modes `start_helpers` reads "Starts read-only
helpers; each start asks you". `runtime: agent_cli` adds `ungated`, the sentence that says such a CLI asks
PersonalClaw only about the calls its own settings say to ask about, and for an Unattended loop whose CLI
the owner let approve its own calls (landed), "{CLI} approves its own calls in this loop, as you chose";
the rows then say what PersonalClaw answers when the CLI asks. `tools.missing` names each tool a
row's representative call uses that the chat does not offer, with why; `asks_until_reviewed` lists, per
trusted server, the tools whose labels are not believed until reviewed. The new-chat form adds
`unchatted`: the same rows for work no chat holds (a trigger's agent, a helper started outside a chat).

### 8.5 The ceiling read model

Status: planned

```
def describe() -> CeilingView: ...   # guardrails/ceiling.py

@dataclass(frozen=True)
class CeilingView:
    state: Literal["open", "bounded", "pending", "error"]  # pending: the file changed since boot
    source: str              # the path read at boot, or "" when open
    digest: str              # the 16-character digest it booted with (ceiling.py:650)
    loaded_at: str           # when the gateway first read it
    file_digest_now: str     # the file's 16-character digest now; "" when absent
    file_problem: dict | None   # {code, what, fix}: the file on disk would refuse the next boot
    scopes: tuple[ScopeView, ...]   # one per CEILING_SCOPES row (ceiling.py:242), in table order
    clamps_since_boot: int

@dataclass(frozen=True)
class ScopeView:
    name: str; archetype: str   # ordinal_control | scoped_ruleset | capability_gate | scoped_map
    label: str; notice: str     # ScopeSpec's new required fields (ceiling.py:220)
    bound_words: str            # "every call that needs approval asks a person"; "" when not mentioned
    governs: bool               # the ceiling names this scope
```

- `ScopeSpec` gains `label` and `notice`, both required: `validate_scope_table` (`:287`) refuses a row
  with either empty, raising `GovernanceBootError(ERR_GOVERNANCE_SCOPE_UNDESCRIBED)`. `ceiling.notice_for(scope)`
  formats a scope's notice from the cached ceiling, and today's refusal sentences
  (`approval_grants.refusal_sentence`, `net/guard._ceiling_sets_egress`) become calls to it, moved
  verbatim (security copy is moved, not reworded).
- The ceiling stays read once at boot and cached (`load_ceiling`, `:629`; `active_ceiling`, `:668`;
  `ensure_governance_boot`, `:687`); `describe` reads the cache and the file's current bytes (at most 256
  KiB), never reloads and never writes. `pending` with `file_problem` says, before a restart, that the
  restart would refuse to boot.
- `GET /api/governance` → `CeilingView`, owner only, never app-reachable; no write route exists or is
  added.
- `personalclaw governance show` prints it (from a gateway that answers for this home, else the file as the
  next start would read it); `personalclaw governance check [PATH]` validates a file without a gateway
  (`parse_ceiling`, `:556`), prints each scope's bound, exits 0 when valid and 2 otherwise, and writes one
  `guardrails.governance_check` row from its own process, valid or not (a digest of the resolved path, the
  outcome, the code; never a value from the file).
- A boot refused by the file is §16.3's record, carrying its registered `ERR_GOVERNANCE_*` code.
- **Three more scopes**, scoped rulesets on new `SafetyProfile` fields that default to empty
  (no bound): `servers` (`server_allowlist`, `server_denylist`, matched on a server's id, its command's real
  path or its remote host), `sources` (`source_allowlist`, `source_denylist`, a Store or app source URL
  prefix; the first-party catalog the release ships is admitted unless denied) and `providers`
  (`provider_allowlist`, `provider_denylist`, a name glob over `provider:model`). `ceiling.admits(scope,
  subject) -> str` answers "" or the refusal sentence, read where the owner's own allow is checked today:
  `servers` before a server starts (readiness `unavailable`, cause `ceiling_refused`, §7.4); `sources` in
  the Store's install and update preview and the install (`409 app_source_above_ceiling`); `providers` in
  provider resolution, so a refused model is never resolved and its turn fails with the sentence.
- **One ceiling for every door.** A channel's `yolo on` is refused above the operator ceiling, as the
  dashboard's is.

### 8.6 `posture_changed`

Status: planned

`posture_changed {session}` (`""` for a change that touches every chat: a config edit of the approval
mode, the hooks or the security section, a ceiling re-read, an agent's approval mode, a read-only trust
write, a server's tool list changing, a project's defaults) is sent whenever an input to `decide` changes:
a mode change, a card answer that grants, a chat grant given or removed, YOLO's expiry, a task-mode,
working-folder, agent or model change. A posture view re-reads; the frame carries nothing else.

### 8.7 Owner presence

Status: planned

A credential-minting write and a change that loosens how the owner signs in need proof the owner is
present now. **This was built ahead of this release**: one function,
`dashboard/owner_presence.require_owner_presence(request, action, identity=None)`, accepting a sign-in
within `PRESENCE_WINDOW_SECS` (ten minutes), the local machine secret, the session the gateway handed the
process that started it (the desktop's own windows), or authentication switched off; refusing with
`401 fresh_sign_in_required` (no `X-Auth-Required`, so no client reads it as signed out) and a security
log row; asking for the current password and second-factor code to change an existing password;
never asking for containment (signing a device out, revoking a token, turning incident mode on); and a
census rail (`tests/test_owner_presence_census.py`). This release adopts it as built: every route it
adds that mints a lasting credential or loosens sign-in calls it, and the census holds them. Two other
routes join the guarded set: the whole-home export (`POST /api/durability/export`, "Downloading a copy of
everything PersonalClaw keeps", which it does not guard today), and the uninstall
(`POST /api/uninstall`, a new action `ACTION_UNINSTALL`, "Removing PersonalClaw from this computer"), which mints nothing but cannot be undone, so it needs the same proof that the owner is present;
the census's minting table is unchanged by it. A third joins it: letting an External access client start
chats (`POST /api/external-access/clients/{client_id}/chats {allowed: true}`, a new action
`ACTION_INBOUND_CHATS`, "Letting an outside agent start chats"), which widens a credential;
withdrawing never asks. An agent never runs an uninstall: the host-effect guard
refuses `personalclaw uninstall --execute` in every session.

### 8.8 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| The posture answer equals what the gate does | `tests/test_posture_equals_the_gate.py`: each posture row's representative call through the real gate and through `decide`, per runtime, task mode and trust state |
| No gate decides outside `decide` | an AST rail: `stands_for_call` and `declared_answer` called only from `approval_decision.py`, with a planted call |
| The three word sets fail both ways (`DECIDED_BY_WORDS`, `REFUSED_BY_WORDS`, `ASKS_BECAUSE`) | `tests/test_decision_words_both_ways.py` |
| No grant stands for a call `decide` would refuse, and every path asks the blocking hooks at one step | `tests/rails/test_one_reader_of_the_grant_order.py`; the landed `tests/test_a_blocking_hook_holds_on_every_path.py` |
| A dry read writes no audit row and runs no hook | a test counts audit rows and hook invocations across a posture read |
| The ceiling's view matches what bounds | each scope's `bound_words` is derived from the composed value `resolve` (`ceiling.py:762`) uses |
| Every scope has a label and a notice | `validate_scope_table` (`ceiling.py:287`) refuses a row without them |

### 8.9 What it must not do

Status: planned

Posture never grants, never prompts and never caches a verdict across a change; it is not a simulator
of hooks with effects; the ceiling view never reloads the file or reveals a path an app may read.

---

## 9. Chat-scoped grants

### 9.1 What it is

Status: planned

"Always in this chat" for one tool, one command shape or one folder: an answer on the approval card that
allows the same kind of call again in this chat only, without trusting the whole chat.

### 9.2 Today

Status: planned

Trust is chat-wide and held in memory (`dashboard/state.py:341-400`); the card's answers are
`approved`, `rejected`, `trust`, `trust_agent`, `trust_reads`, `yolo` (`approval_state.py:67-73`). The
agent CLI's "allow always" option is never chosen (`select_allow_option_id`, `acp/dialect.py:333`, whose
only caller passes no preference, `acp/session.py:595`).

### 9.3 Shape

Status: planned

| Field | Type | Meaning |
|---|---|---|
| `id` | `cg_<ULID>` | |
| `kind` | `tool` \| `command` \| `path` \| `host` | `tool`: any call of one tool; `command`: one shell command shape; `path`: one tool's writes to one file or under one folder; `host`: one host, treated as allowed for this chat's calls (§9.11) |
| `tool` | str | the tool's stable name; "" for `host` |
| `pattern` | str | `command`: the program's word, then fixed words, `*` (exactly one word) and, only last, `**` (zero or more further words); `path`: `<folder>/` or `<file>` relative to `root`, with no wildcard (`src/`, `src/app/main.py`); `host`: a bare host; `tool`: "" |
| `label` | str | the grant in words, composed when given ("`pytest **` in this chat", "edit_file in src/") |
| `root` | str | for a `path` grant, the chat's working folder when it was given; "" otherwise |
| `lasts` | `task` \| `chat` \| `until` | how long it answers (decision 41: the field is `lasts`, since `ApprovalAnswer.ends` already names the card's own end) |
| `task_ref` | str | for `task`: the goal loop or loop run it answers (a work reference, §2.2) |
| `until_at` | instant \| "" | for `until`: 1, 4 or 8 hours after it was given |
| `seal` | `{programs: [{word, real, digest}], script: {path, digest} \| null}` \| null | for `command`: the program it saw (below) |
| `fenced` | bool | for `command`: it answers only while the call's run is fenced (§9.4) |
| `via` | `card` \| `channel` \| `cli` \| `access` | where it was given: the card or the dock, a channel prompt, a run's own grants (§9.5), an access request (§11.2) |
| `granted_at` | instant | |
| `granted_by` | `owner` | always the owner (§2.5); an agent cannot make one |
| `request_id` | str | the approval it answered |

Kept in the chat record's `chat_grants` (§3.7), so they survive a restart with their chat (decision
11); chat-wide Trust stays in memory, as today. At most 50 per chat: the 51st answers `409
chat_grants_full`. They end with the chat (delete, Temporary end); a fork carries them only when the
owner asks (`carry_grants: true` on the fork route), each copied with a new id and a
`approval.chat_grant_given` row naming the fork. The record's reader refuses a grant entry with an
unknown key or an unparsable pattern and grants nothing for it, and the chat's posture says so.

| From | To | When |
|---|---|---|
| (none) | given | the owner answers `chat_pattern` or `host_chat` on a card, a channel prompt or an access request, or the owner's CLI makes one for its run (§9.5) |
| given | revoked | the owner removes it (§9.5) |
| given | lapsed | its task ends, or `until_at` passes; `decide` checks `lasts` at every call |
| given | ended | its chat is deleted or a Temporary chat ends; nothing is kept |

**The seal.** At every approval of a native shell call, asked or granted, the shell tool records the program
it saw: each program word's real path in the call's environment, the SHA-256 of each that lives in a folder
the owner can write, and the digest of the one local script or interpreter file the command runs. Just
before the run it resolves again, and a difference refuses the call `approval_drifted`
(`ERR_APPROVAL_DRIFTED`; `approval.drifted`). A `command` grant keeps its seal and answers only while it
holds; otherwise the call asks `grant_drifted`. Code that is not one identifiable file (a package script,
`python -c`) is `unchecked`. An agent CLI's asks are sealed at answer time only.

### 9.4 What a grant may cover

Status: planned

- It is checked as `CHAT_PATTERN = "chat_pattern"` at `LEVEL_AUTO` through `decide` (§8.3), after every
  refusal, the ceiling and Trust, so it never outranks a refusal; the chat's grants are tried in the order
  they were given, and a grant answers only what it covers, only in its own chat, never a helper's, an
  automation's or another chat's call.
- It never covers a call whose effective risk is `destructive` (`task_modes.MAY_DESTROY`). It covers an
  `unchecked` call only as a `fenced` grant while that call's run is fenced (`placement.fenced`, §17.8):
  on an unfenced run it is not consulted and the call asks `no_grant` (the `fenced` kind ships once
  the fence works on both supported operating systems and the measured table of real commands passes on
  both, and a machine whose fence is Landlock only, `outside_only`, is never fenced). It covers a reach to a
  host off the allowed hosts only as a `host` grant naming that host (§9.11). The card does not offer one
  for an ask answered alone, an app's conversation, unattended work or a call request's write (a voice
  call's request: no chat grant, a fenced one included, answers it, and the card offers none).
- The kind offered follows the call: a shell call is offered `command` (the suggestion is the program's
  word, up to two following words that look like subcommands, then `**` when more words follow: `pytest
  tests/x.py -k y` gives `pytest **`, never a wildcard where the word chooses the code to run); a file
  write (a native write tool, or an agent CLI edit with a declared path inside the working folder) is
  offered only `path`, inside the working folder, never `tool`, since a whole-tool grant for a write tool
  would reach every folder the chat may write (a multi-file edit is offered one grant per file, all or
  none, beside the folder); a host reach `host`; any other call `tool`. A `path` grant covers the grant's
  tool writing a path that resolves (symlinks followed) to its file or inside its folder under `root`,
  while the chat's working folder is still `root`. The card shows at most three ends: "Just this once"
  (preselected), "In this chat", and "For this task" in a goal loop or loop run, else "For 1 hour" (4 and
  8 in its menu); nothing is focused or sent on arrival.
- A `command` pattern is read by `shell_syntax.lex` and `simple_commands` (`shell_syntax.py:154, :328`, pure,
  in `command_patterns.py`): exactly one simple command, no redirect, no command substitution, no word the
  lexer cannot read, no wildcard program, `**` only last, a word either `*`, `**` or plain text, at most 200
  characters and 12 words, and no wildcard where the word chooses the code to run
  (`command_patterns.WRAPPERS`: right after a shell; after `env`, `sudo`, `doas`, `nohup`, `timeout`, `xargs`,
  `exec`, `command`, `nice`; after a package runner such as `npx` or `uvx`; after an interpreter and its `-c`,
  `-e`, `-m` or `-`), so `python -m pytest **` is accepted and `python **` is not. A command matches when it
  is one simple command with no redirect but descriptor duplication, no opaque or globbing word, the program
  and each fixed word equal, each `*` one word, `**` the rest, and nothing left over: anchored and linear, no
  regular expression.
- A pattern outside these rules, or one that does not cover the call being answered, is refused with
  `400 chat_grant_pattern_refused`, whose message says which rule; nothing is approved, the call keeps
  asking, and `approval.chat_grant_refused` is written.

### 9.5 Reads and writes

Status: planned

- The card's answer `{action: "chat_pattern", request_id, kind, pattern, lasts, hours?, note?}` on the
  approve route, with the pattern the brief proposed or the owner's edit of it (§11.4), checks the pending
  entry as today (`403 approval_owner_only`, `409 approval_owner_ended`), checks the ceiling (`409
  approval_grant_refused` as today), validates the pattern and that it covers the call, approves this call,
  adds the grant with its seal, persists the record and answers `{ok: true, grant: ChatGrant}`.
  `SESSION_APPROVAL_ACTIONS` gains `chat_pattern`, `chat_hold` (§9.10), `stopped` (§11.2) and the host
  answers `host_once`, `host_chat`, `host_always` and `host_block` (§9.11).
- **A run's own grants.** `POST /api/chat/sessions/{session}/grants {kind, tool, pattern}` is open only to
  the owner's CLI token, only for a run's own `inbound:cli:` chat while it holds no turn (`403
  cli_grant_refused` otherwise): `personalclaw run --grant SPEC` (at most 20) makes each before posting,
  with the same validator, `via: cli`, task mode `agent` and no Trust. They end with the run's turn, as
  `--allow`'s Trust does; `--grant` with `--allow` is refused.
- `GET /api/chat/sessions/{session}/grants` → `{items: [ChatGrant]}`;
  `DELETE /api/chat/sessions/{session}/grants/{grant_id}` → `204`, or `404 chat_grant_not_found`. Both
  owner only.
- Each create and delete writes `approval.chat_grant_given` or `approval.chat_grant_revoked`, and sends
  `posture_changed {session}`.

### 9.6 Agent CLIs

Status: planned

PersonalClaw never chooses an agent CLI's own "allow always" option: the CLI's "always" is the CLI's scope
(often the whole tool, sometimes saved to the project's own settings), wider than a command shape and not
ended with the chat. The chat grant answers each later ask from the CLI itself, so the CLI's repeated asks
cost one round trip and no card. `select_allow_option_id`'s `prefer_always` parameter, which nothing
passes, is deleted.

### 9.7 Channels and apps

Status: planned

`ApprovalAnswer` (`channel_delivery.py:61-90`) gains `pattern` (in `as_dict` only when set). The answer
"Always in this chat: {label}" (key `chat_pattern`, always `lasts: chat`) is offered after Allow once, when
the call qualifies (§9.4) and the channel app declares `chat-patterns` (§1.8); a channel prompt without the
feature offers today's answers only. The channel cannot edit the pattern: it answers with the key, and core
takes the pattern it offered, kept on the pending approval. Until the last channel that runs its own
conversation moves onto the door, core also keeps the offers of a turn the channel runs by `request_id` (at
most 256, each dropped when its approval window ends); that store goes with the move, and `chat-patterns`
stays for the prompts core sends. A `chat_pattern` answer with no request id, or for a request with
no offer, takes nothing. A text-only channel's `/approve <n>` and `/deny <n>` answer through the waiting
item's own route, Allow once only; a channel never offers or answers a host card (§9.11). Deny with a note and
Deny and stop go to channels that declare `deny-notes` (§11.2). To tell the two chat answers apart, the Trust
answer's label becomes "Allow everything in this chat", and the card's scope today labelled "This chat"
becomes "Everything in this chat"; their promise sentences are unchanged. An app never creates a grant.

### 9.8 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| A grant never covers a refused, destructive or off-list call | `test_a_chat_grant_never_covers_a_refused_or_destructive_call` (a table over the deny-list, task modes, the ceiling and each risk) |
| A pattern matches only what it says | property tests in `tests/test_command_patterns.py`: no prefix match, no match across `;`, `&&`, `||`, `|`, a background `&`, a newline, a substitution, a redirect, an opaque or globbing word |
| A grant survives a restart and dies with its chat | `test_a_chat_grant_lives_and_ends_with_its_chat` |
| Only the owner creates one | the route takes no app token; the answer comes only from the card's owner surfaces and a channel's offer core made |
| An unfenced run is never answered by a fenced grant | `test_an_unfenced_run_is_never_answered_by_a_fenced_grant` (in the change that adds `fenced`), iterating `fence.UNFENCED_WAYS` so every way a run can be unfenced is covered by construction; the fence ships the producer half (`test_unfenced_ways_both_ways`); the fenced-grant task depends on the fence's measured-table task |
| A command grant answers only while its seal holds | `test_a_drifted_program_asks_again` |
| A host is allowed or blocked from now on only through the one writer, by the owner's answer | `test_host_answers_have_one_write_path` |

### 9.9 What it must not do

Status: planned

A chat grant is not a global or per-agent allow (those are §9.10's rules), never a path outside the
working folder, never a regex the owner types, and never carried to another chat, a helper, a handoff's
helper or an automation (a fork carries it only when the owner asks). An automation's standing grant is a
`Rule` with `scope: automation` (§9.10), never a chat grant.

### 9.10 Standing rules (decision 41)

Status: planned

One typed rule for every standing allow, ask and deny (`permission_rules.py`):

```
Rule {id: rule_<ULID>, effect: deny | ask | allow, tool: str, spec: Spec | None,
      scope: {kind: home | agent | chat | automation, ref}, source: owner | folded | cli,
      label, why (≤200), instead (≤200), examples: {match: [str], no_match: [str]} (≤10 each),
      granted_at, run: str = ""}
Spec = CommandSpec | PathSpec | HostSpec | ServerToolSpec
```

- **Specs.** `tool` is a stable tool name, "" for any, or a prefix ending in `*` (`edit_*`, `mcp/files/*`).
  `CommandSpec` is §9.4's pattern. `PathSpec` is anchored at its scope's root (the working folder for a
  chat, `~/` for home and agent): `dir/` covers what is inside it, a file names that file, no other
  wildcard, symlinks resolved. `HostSpec` is a bare host covering its subdomains (the egress list's one
  dialect). `ServerToolSpec` is `mcp/<server>/<tool>`, no glob in the server part.
- **Matching and order.** A spec matches the call's input as the tool receives it, never its title. `deny`
  and `ask` apply when any simple command of a compound command matches; `allow` needs every simple command
  to match. Deny, then ask, then allow (§8.3 steps 3, 10 and 12), across every scope in force, under the
  ceiling. An `allow` rule, like a chat grant, never covers a `destructive` call, and an `unchecked` one only
  while the call's run is fenced. A rule whose tool exists nowhere, whose spec does not parse, or whose
  examples disagree with it is refused at save (`400 rule_refused`, naming it); examples run again at each
  load, and a home rule failing there is dropped and listed in Settings, never applied half-read.
- **Where rules live.**

  | Scope | Store | Effects |
  |---|---|---|
  | home | `security.rules.limits` (deny, ask; at most 200; `direction: restricts`) and `security.rules.allows` (allow; at most 100; `direction: grants`) in `config.json` (§1.2) | all three |
  | agent | the agent definition's `rules.limits` | deny, ask: no per-agent allow list |
  | chat | the chat record's `chat_holds` (ask) and `chat_grants` (allow, §9.3) | ask, allow |
  | automation | the `automation_rules` book (below) | allow |

- **The fold of the old keys** (§1.5). At load, `hooks.auto_deny_tools` becomes `deny` rules in
  `security.rules.limits` (`source: folded`, each glob kept as a title-glob spec only the screen matches; no
  new rule takes one). `hooks.auto_approve_tools` is not carried over: its patterns move into
  `security.rules.dropped`, and Settings › Security shows them with "Make each again as a rule if you still
  want it" and Dismiss (which writes `[]`), so a config holding them still boots and nothing they allowed
  runs silently. Their readers go in the same change, and the grant word `hook_pattern` becomes
  `standing_rule` (§8.2).
- **The owner's holds.** A `deny` rule is "Never", refusing in every mode and in the automations their agent
  runs; an `ask` rule is "Ask every time", which asks under Trust and YOLO. `why` and `instead` reach the
  agent's refusal and the audit row. The card offers "Always ask for `{suggest}` in this chat", a chat
  hold that ends with the chat; when the owner's own message holds a phrase from a closed table ("never deploy"),
  the reply offers one click to make it a hold, and no model infers one.
- **An automation's standing grant**. Offered only once the owner has allowed the
  same call shape for the same automation twice: a `Rule` with `scope: {kind: automation, ref: <trigger
  id>}`, the same specs and matcher, kept with its allow count in the `automation_rules` book under
  `grants/` (§17.1), sealed to the automation's behaviour digest, so any revision drops it. Step 12 reads it
  for that automation's runs only, at `LEVEL_HOOK` under the ceiling, never past a refusal, never
  `destructive`; it is listed with Revoke. A chat grant never leaves its chat.
- **A one-run allowance**. A `Rule` scoped to an automation may carry `run`, a run id: it
  rides one dispatch (`ActionContext.run_allows`, handed to the agent the run starts, and to `decide` as
  `Context.rules`), is never stored, and answers only that run's calls at step 12, `allowed_for_run`. An
  unattended run's one-run allowance of a host is admitted at step 8 as a host its own consent covers.
- **Routes**, owner only, each sending `posture_changed` and writing `security.rule_added` or
  `security.rule_removed`: `GET /api/security/rules`; `POST /api/security/rules {effect, tool, spec, scope,
  why, instead, examples}` → `201` (`400 rule_refused`, `409 rules_full`); `DELETE
  /api/security/rules/{id}` → `204` (`404 rule_not_found`). Settings › Security › Rules lists every rule
  (scope, source, label, why, Remove); the agent form gains "Never" and "Ask every time" lists, which packs
  may ship and the pack preview shows.

### 9.11 Host answers (decision 39)

Status: planned

- **One set of four answers**, in `SESSION_APPROVAL_ACTIONS`:
  - `host_once`, "Allow once": this command's run only (the ask at the connection).
  - `host_chat`, "Allow <host> in this chat": a `host` chat grant, `lasts: chat` (§9.3), which treats the
    host as allowed for this chat's later calls (it clears the reach at step 11, and the call is then
    decided as one to a listed host) and clears a tainted egress to it; this call is approved.
  - `host_always`, "Allow <host> from now on": approves this call and adds the host to the allowed hosts
    through the one writer. Its promise: "Every chat's commands and the programs apps start reach {host}
    without asking from now on, and fetches reach it even on a private address. Remove it in Settings ›
    Security."
  - `host_block`, "Block <host> from now on": refuses this call and adds the host to Denied hosts through the
    same writer.
- **The floors**. Offered only to the owner answering: their own attended turn, the
  dock, or their own answer on an Inbox row; never on a channel; never above an egress ceiling they set; never
  an allow for a host on Denied hosts; each only where `egress_hosts.offerable(host, list)` allows it;
  written only through the owner-only writer. A call request's card offers only `host_block`.
  Unattended work, an app's conversation and a turn someone else asked for are refused, naming the host.
- **Where it is asked.** Before a run: `decide`'s step 11, for a host the call names (an MCP call's host is
  its server's URL host, `mcp_discovery.server_host`; a stdio server has none). At the connection: in an
  attended owner's turn, a fenced command's connection to a host the guard refuses as not
  listed is held instead, one ask per host (at most three pending per command; a fourth is refused at once),
  answered alone with the four answers and Not now. The connection resumes when the owner answers within 120 s
  and the client is still waiting; otherwise the answer stands for later connections, and the call's result
  says to run it again.
- **What a fenced command in a chat the owner watches reaches without a card** (decision 40). Asking is not
  limiting, but a card for every registry a build touches would make the default fence a nuisance, against
  the rule that these choices not limit the owner. The per-command proxy admits the Allowed hosts, the
  chat's `host` grants and the package registries the `registry` egress tier already lists
  (`net.policy.REGISTRY_HOSTS`: pypi.org, files.pythonhosted.org, registry.npmjs.org, crates.io, ghcr.io,
  github.com and the rest), so the owner's toolchain installs and fetches with no card, and asks once per
  host per command for any other. A bound secret's hosts still narrow the set; `security.shell_network:
  open_when_attended` opens the network in chats the owner watches.
- **The card's order.** The answers read narrowest first, with Allow once chosen. For a host the owner allowed
  from a card twice in the last 30 days, "Allow <host> from now on" leads and is chosen, its line saying why
  ("You allowed this host twice this month"). Nothing is focused or answered on arrival.
- **The one writer**. `POST /api/security/egress/hosts {host, list: allow | deny, origin:
  {kind: chat | inbox | settings, ref, label}, confirm?}`, owner only, is called by the card's answer
  (`kind: chat`), by an automation's declined-call answer on its Inbox row (`kind: inbox`) and by Settings
  (`kind: settings`). It adds the host to `security.egress.allow_hosts` or `deny_hosts` through
  `config.transactions`, behind the `security.egress` control's consent and its sentence, and records the
  origin in `egress_host_origins.json` (`{v: 1, hosts: {<host>: {list, kind, ref, label, at}}, recent:
  {<host>: [instant]}}`, one writer under its lock; `recent` keeps each host's last five allows for one chat
  or one run within 30 days, which the card's order reads). `DELETE /api/security/egress/hosts/{host}`
  removes the entry and its origin. The `security.egress` PATCH keeps `allow_private` and refuses a change
  to either list, naming this route. Refused, naming the rule: `400 egress_host_refused` (not a bare DNS
  name or an address the guard may allow, `*`, a metadata or link-local address), `409 egress_host_denied`,
  `409 egress_host_above_ceiling`. Each write logs `security.egress_host_allowed`,
  `security.egress_host_blocked` or `security.egress_host_removed`, with the host and the origin.
- **Settings › Security › Network egress** lists each entry with its origin ("Allowed from a chat · 4 Oct
  · Plan the trip", linking to it; "Blocked from an Inbox row"; "Added in Settings"; "Where it came from was
  not recorded") and Remove: removing an allow asks nothing, removing a block asks the control's question.
- **A run's egress policy** admits its chat's `host` grants, in an attended owner's turn only. A folder
  request (§11.2's `access_request`) may ask for hosts too: granted hosts become `host` chat grants (`via:
  access`) under the same floors.

---

## 10. Revisions

### 10.1 One convention, one small module (decision 12)

Status: planned

Each definition keeps its own storage; every revision record follows one shape and one module computes its
digest, its author and its diff: `personalclaw/revisions.py`, pure (no store, no lock, no clock read; each
kind's behaviour function is imported lazily inside its branch).

```
@dataclass(frozen=True)
class RevisionRecord:
    n: int            # 1, 2, 3 … per definition, assigned under the definition's lock, never reused
    digest: str       # digest(kind, definition): canonical_digest(behaviour) (§2.4)
    at: str           # instant
    by: str           # Author (§2.5)
    ref: str          # the author's ref
    note: str = ""    # why, when the door knows: "accepted the refiner's proposal"

def behaviour(kind: str, definition: Any) -> dict[str, Any]: ...
def digest(kind: str, definition: Any) -> str: ...
def diff(kind: str, before: Any, after: Any, *, max_lines: int = 400) -> RevisionDiff: ...  # changed paths, a capped unified diff
def next_number(records: Sequence[RevisionRecord]) -> int: ...
```

`revisions.behaviour(kind, definition)` returns the fields a revision covers, by a closed set of kinds:

| Kind | Behaviour |
|---|---|
| `trigger` | the row less `RUNTIME_FIELDS` (`triggers/store.py:473`), **with** its step's posture keys kept (unlike `what_it_is`, `:523-537`, which drops the loosened ones: a loosening changes what runs and is the change most worth auditing), and less `spec.health_state` (the state an adaptive clock's own action writes on every check): one function, `triggers.store.revision_behaviour(row)`, beside `what_it_is`. The trigger's `project_id` is part of it (§13.5) |
| `workflow` | exactly the fields the workflow-version fix's `versions.digest` covers (its runnable spec less `version`, `created_at`, `updated_at`, `source`, `provenance` and `metadata.a2a_published`), so an Allow recorded before this release still matches |
| `skill` | the skill's files as `{relative path: SHA-256 of bytes}`, the lock file excluded, and the curator's aging line (`status:` in `SKILL.md`'s frontmatter) left out: archiving is runtime state |
| `pack` | `{version, component_locks}` of the install record |

A kind outside the set raises `ValueError`. The diff masks values as the row is masked, so it never shows
more than its reader could already see; a missing side answers no diff, never an empty one.

**How a kind adopts it.** A store that keeps revisions of a definition assigns `n` with `next_number`
under the lock its writes already take and writes the record in the same change as the definition, never
in a second step another writer could interleave with; writes a record exactly when the digest differs
from the newest recorded one; takes `by` and `ref` from its door through `require_author`; serves records
with `author_words`; restores by writing a new revision by `owner` whose behaviour is the restored one,
through the same consent and stale-write rules as an edit, running nothing by itself; trims oldest first
and never reuses a number; and runs the conformance kit (§10.6).

### 10.2 Where each kind keeps them

Status: planned

| Kind | Storage | Writer | Kept |
|---|---|---|---|
| Trigger | `trigger-revisions/<trigger_id>.jsonl`, one `RevisionRecord` per line plus `behaviour` | the trigger store's write funnel, inside its lock, before the file write, for every row whose digest differs from its log's newest, with the door's author; a post-write hook records a sync's or a restore's write of `triggers.json` as `brought_in`, and a hand edit is caught at the store's next write, `by: ""` | the newest 50, by revision number (`bounded_log`) |
| Workflow | `workflows/versions/<name>/vNNN.json` (`workflows/versions.py`) | `service._write_definition` | as today; the record's `saved_by` comes from the workflow-version fix, its `ref` from the door that saved it (`VersionRecord` gains `ref`), and its digest is `revisions.digest` |
| Skill | each skill tier PersonalClaw writes keeps `<tier>/.history/<q(name)>/` (the name percent-encoded into one segment): `<n>.json` per revision (`{v: 1, record: RevisionRecord, name, files: {path: sha256}, restorable}`) and `blobs/<sha256>` per file body, deduplicated; a file over 2 MiB is kept by digest only (`restorable: false`) | the one skill writer (`skills/history.py`, under the library lock), on every save, edit, install, update and shipped sync; a delete records the files as a final revision when they differ from the latest and writes `<tier>/.history/<q(name)>/deleted.json` (`{v: 1, at, by, ref, last: n}`) before it removes the folder (`skills.deleted`) | the last 20, within 8 MiB of blobs per skill (never the newest two dropped); a deleted skill's history 90 days after its delete, then the trim at gateway start removes it; a blob no kept revision names is removed in the same trim; machine-local (§1.4) |
| Pack | the install record (`packs/installed.py:44-89`) gains `revisions: [RevisionRecord]`; its current revision is its `version` with its `component_locks` | the pack install and update paths (`packs/update.py`), on install and on every accepted update | last 20 |
| Mini app | its versions table (#3950) | the mini-app service | its own; it conforms to the record (`n` = `version`, `digest` = its projection seal, `at` = `promoted_at`, `by` = `owner`) |

Trigger revision 1 is written for every existing trigger by an idempotent backfill (§1.5) with `by: ""`,
`ref: "backfill"`. A skill's history starts lazily, at its first write (revision 1 by `""`, ref
`history-start`), with no backfill. A trigger's revision log is removed exactly when its run history is,
and a new automation never takes an id whose log exists.

### 10.3 Reads

Status: planned

**No row or route serves a revision number as `revision`** (decision 26): that name is the stale-write
digest every editable row carries and a whole-form save sends back in `If-Match`
(`stale_write.revision_of`, `src/personalclaw/stale_write.py:75`; `428 revision_required`,
`409 stale_write`). A definition's numbered revision is served as `current_revision` (the newest record
whose digest is the definition's) and, where a grant records one, `allowed_revision`; a history record
carries its number as `n`. A run record is no editable row, so `ScheduleRun.revision` keeps its name.

- Triggers: rows (`GET /api/triggers`, `GET /api/triggers/{id}`) gain `current_revision` (`{n, at, by,
  by_words, ref_label, ref_link}` | null), `allowed_revision` (int, 0 when none is recorded or it is no
  longer kept) and `changed_since_allowed` (`[{n, by_words}]`). `GET /api/triggers/{id}/revisions` →
  `{items: [RevisionView], next_cursor: null}`, newest first; `GET /api/triggers/{id}/revisions/{n}` → the
  record with its masked `behaviour` and the diffs from `n-1` and from the allowed revision (`404
  revision_not_found`); `POST /api/triggers/{id}/revisions/{n}/restore` restores as the owner.
- Skills: `GET /api/skills/{name}/history` (`{items, next_cursor: null}`, newest first),
  `GET /api/skills/{name}/history/{n}` (`404 revision_not_found`), and `POST
  /api/skills/{name}/history/{n}/restore` (a restore is a new revision by the owner), each answering the
  skill's `current_revision`. `GET /api/skills/deleted` → `{items: [{name, tier, deleted_at, by_words,
  current_revision}], next_cursor: null}`; a deleted skill comes back by the restore route on its `last`
  revision, which also removes `deleted.json`, and a skill made or installed under a deleted name continues
  its history.
- A schedule run record (`ScheduleRun`, `schedule_history.py:60-165`) gains `revision: int` (0 means
  "revision not recorded") and `workflow_version: int` (the workflow version a Run-workflow action ran),
  set from the dispatched object's digest when the run is recorded, so an edit while the run was in flight
  cannot mislabel it and "what did Tuesday's run run" is one read.

### 10.4 Workflows

Status: planned

A workflow keeps its word "version" in its own surfaces; its version is a revision by this convention.
**The workflow-version fix built the workflow half ahead of this release** (landed): each version records
who saved it (`saved_by`, set by the door, from `workflows/versions.SAVERS`: `owner`, `publish`, `agent`,
`refiner`, `import`, `app`, `shipped`, `brought_in`), the trigger's grant records the version it allowed
(`{name, version, digest}`, `workflows/automation_version.Allowed`), a fire runs that version unless the
owner saved a newer one, "Use vN" asks first (`POST /api/triggers/{id}/workflow-version`), the pin
and its repin route are removed, and `GET /api/workflows/{name}/automations` lists who runs it. This release
adopts that as built; `revisions.py` becomes the one home of the author vocabulary and
`workflows/versions` imports it, `VersionRecord` gains `ref`, `versions.digest` becomes a call to
`revisions.digest("workflow", …)` byte for byte, and the two author-word tables (`grants._SAVED_BY_WORDS`
and the web's) are replaced by the served `by_words`.

### 10.5 The rule a revision serves

Status: planned

The owner's yes covers the definition as it stood. A newer revision made by the owner at their own door is
followed (their save is their yes); a change any other author makes never widens what runs without their yes
(decision 25). By kind:

- **Triggers.** A trigger's grant records the digest of the behaviour it was given for
  (`capabilities["revision"] = {"digest"}`, runtime, so it never changes the behaviour digest), and the
  allowed revision is found by that digest in the log. A change any other author makes to what an allowed
  automation's action runs, or that adds what needs a grant (as `triggers/grants.narrow`, `grants.py:157-190`,
  compares it), is **held aside**, not stored as the automation: the automation as allowed keeps running,
  or stays off if it was off, and the change waits in `trigger_held_changes.json` (§1.4; one held change
  per automation, a newer one from a non-owner door replacing it) for the owner's Allow, which asks the
  consent question and applies it as a revision by its own author, or their Discard. A held change whose
  base the owner has since edited is out of date and cannot be allowed (`409 held_change_out_of_date`). A
  change that touches nothing the grant covers (a name, a cadence, a gate, a delivery route) is applied as
  a revision by its author, and the row says "Changed by <author> since you allowed it". A posture
  loosening by any door but the owner's is refused outright, as today. A sync's edit keeps the take-in
  rule (taken in, the grant narrowed, its runs refused until the owner allows it), because a home that kept its
  old definition would be handed the same edit at every sync; its revision says `brought_in`. For an action
  that starts work in a project, the trigger's project is part of what the grant covers (§13.5).
- **Workflows.** The allowed version keeps running; a newer version by another author is not followed
  until the owner's "Use vN" (§10.4).
- **Skills and packs.** An upstream update is offered and reviewed, never applied unasked, and an edited
  copy is replaced only with a consent computed over it (§15.2); a folder's skill runs only on the owner's
  yes to its exact bytes (§15.3).

### 10.6 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| A revision is written exactly when behaviour changes | per kind: a runtime-field change writes none; a behaviour change writes one |
| Every run names the revision it ran | `ScheduleRun.revision` set on every fire path, including an edit in flight and a revert; workflow runs' `spec_version` (exists) |
| Only the owner's own change is followed | a table test per author |
| A non-owner change to what an allowed automation runs never runs before the owner's Allow, and what they allowed keeps running | `test_an_agents_change_to_what_an_allowed_automation_runs_waits` (one case per author; the next fire runs the allowed action) |
| One digest rule and one author vocabulary | `revisions.digest` is the one digest of a definition's behaviour; a tree rail fails on another, on a second author-word table and on `SAVERS` |
| Conformance | one kit every revisioned kind runs (`testing/revision_conformance.py`, internal): first, unchanged, runtime, change, author, append-only, restore, trim, concurrent; a planted store that rewrites a record or numbers outside its lock fails it |

### 10.7 What it must not do

Status: planned

No revisions for runtime state; no sync of trigger revisions, held changes or workflow versions between
homes (each home's yes is its own, as the workflow history already is); no restore that runs anything by
itself; no revisions in this release for lifecycle hooks, callbacks or automations a provider app serves.

---

## 11. The decision dock and the approval text

### 11.1 What it is

Status: planned

While anything waits on the owner in a chat, the oldest waiting decision for that chat docks in the
composer stage, above the input, with the transcript keeping a one-line marker. Every approval says the
action in words, with rows lifted from the call and, for a file change, the change itself, on every
surface, from one server-side text.

### 11.2 The waiting list item

Status: planned

The dock reads #3888's list. Its item, as this release relies on it:

| Field | Type | Meaning |
|---|---|---|
| `id` | str | the source's own id (an approval's `request_id`, a question's `question_id`, a sign-in ask's `sia_` id) |
| `kind` | `approval` \| `question` \| `loop_question` \| `workflow_gate` \| `server_question` \| `browser_grant` \| `plan_review` \| `bridge_confirmation` \| `sign_in` \| `secret_request` \| `access_request` \| `fill_request` | #3888's eight sources, plus this release's `sign_in`, `secret_request`, `access_request` and `fill_request`. A browse run's submit ask is a `browser_grant` item whose brief is of family `browse`: no new kind. A held memory change is a learning proposal, never an item here (§17.9) |
| `answered_alone` | bool | an ask no standing grant may answer: an owner-allow ask (a batch start, a definition save; `workflows/owner_allow.ask` raises it through the approval registry with `answered_alone=True`), a call off the allowed hosts (except by a host answer, §9.11), a view's change, every sign-in, secret request, access request and fill request, and every ask §8.3's step 10 makes |
| `work` | WorkRef | the work it belongs to (§2.2) |
| `session` | str | the chat it is shown in: the asking chat, or the chat that started the asking helper. An agent's read of the Inbox shows an item with a `session` only to that chat's work, and a Temporary or Incognito chat's to nobody else; the owner's own Inbox lists every item |
| `arrived_at` | instant | |
| `brief` | ApprovalBrief \| QuestionBrief \| SignInBrief \| SecretBrief \| AccessBrief \| FillBrief | exactly what the card shows (§11.4); a sign-in's brief is `{need: sign_in \| more_access, title, body, subject, scopes}` (`need`, as the sign-in part names it, §3.3) ("notes needs you to sign in before the agent can use search_notes."); a secret request's `{kind, title, body, name, label, why, project}`; an access request's the exact real paths, their access, the hosts, the agent's `why` and a lifetime ("This turn" or "This chat"); a fill request's the page's host and each field's label, never a value |
| `answer` | `{method, path}` | the source's own answer route, unchanged; a sign-in's is `POST /api/sign-in-asks/{id}/answer {answer: "sign_in" \| "skip"}` (owner only: `sign_in` starts the subject's sign-in and answers `200 {state: "pending", authorization_url, redirect_uri}`, `skip` answers `200 {state: "skipped"}`; `404 sign_in_ask_not_found`, `409 sign_in_ask_ended`) |
| `answers` | [ApprovalAnswer] | what it takes (a sign-in: `[{key: sign_in, label: "Sign in"}, {key: skip, label: "Skip"}]`; a secret request: Save and Skip) |
| `may_interrupt` | bool | decided once by the gateway from mute, quiet hours and the notification rules |

A sign-in ask is raised only for a call in an attended dashboard chat, or a helper whose parent is one;
unattended work, channel and inbound turns, app conversations, loops, automations and workflow steps fail
at once with `ERR_CAPABILITY_NOT_READY`. One ask per server per turn: every other call of that turn that
needs the same sign-in waits on it; it lives at most 600 seconds and ends `cancelled` with its turn. The
call it holds was never sent, so it runs once signed in, and its wait counts against neither the call's
deadline nor the turn's bound.

The three kinds this release adds beside the sign-in, each answered through its own route (owner only):

- **`secret_request`** (`srq_<ULID>`): a missing secret asked for where the work stopped. `POST
  /api/secret-requests/{id}/answer {answer: saved | skip}` never carries a value: the owner saves the secret
  in Settings › Secrets, and `saved` answers once it is there (`404 secret_request_not_found`, `409
  secret_request_ended`, `409 secret_request_not_saved`); `secret.request_raised`, `secret.request_ended`.
- **`access_request`** (`acc_<ULID>`): a native tool for the owner's own turns in attended chats
  asks for at most five folders and five hosts. `POST /api/access-requests/{id}/answer {folders: [{path,
  access}], hosts: [host], lasts}` or `{deny: true}` (`404 access_request_not_found`, `409
  access_request_ended`, `400 access_path_refused` for a path the request did not hold). A granted folder
  joins the chat's place with origin `access` (§13.2), kept in the chat record's `access_grants`; granted
  hosts become `host` chat grants (`via: access`, §9.11). Refused before any card
  (`ERR_ACCESS_REQUEST_REFUSED`) for `/`, `~`, the home folder, PersonalClaw's own home, an owner-only
  place, a credential location or a folder past the ceiling's `paths`.
- **`fill_request`** (`fill_<ULID>`): a field only the owner fills (a credential or a payment
  field a browse step reached). `POST /api/fill-requests/{id}/answer {values: {ref: value}}` hands the values
  to the browse driver in memory for at most 600 s; they never enter the transcript, a model, a log, memory
  or a park record (`404 fill_request_not_found`, `409 fill_request_ended`, `409 fill_host_refused` for a
  credential on a host other than its profile's).

**What the owner answers with.** `ApprovalAnswer` gains `pattern` (§9.7), and behind `deny-notes` (§1.8)
`note` (at most 500 characters, masked, owner surfaces only, on Deny and on Allow; the agent is told it as
the owner's instruction for the next step, and `approval.note_given` records only that one was given) and the
answer `stopped` ("Deny and stop": it rejects the call and cancels the turn in one write). An automation's
declined call, noted on its Inbox row with `run`, `rule`, `host`, `changes_before` and `offers`, is
answered through `POST /api/triggers/{id}/declined-calls/{item_id} {answer, confirm?}` with `once`, the host
answers (`host_chat` is "for one run" there; §9.11) or `standing` (§9.10), never on a channel; its host
answers write through the one host writer with `origin.kind: inbox`.

If #3888 has not shipped when the dock is built, the dock's change builds this list and its change event
first (§0.5).

### 11.3 The dock

Status: planned

- A chat's dock holds the items whose `session` is the chat, oldest first, shown "1 of 3"; the transcript
  marks each waiting call "Waiting for your answer below" and, once answered, shows the outcome line it
  shows today.
- #3822's Enter and Esc bind to the docked card; focus moves to it only on an explicit key; typing a steer
  while it waits stays possible (a compact card, the input below).
- A helper's ask docks in the chat that started the helper, named as the helper's.
- An answer goes through `answer`, with its existing guards: no answer can be made through the dock that
  the source's own route would refuse.

### 11.4 The approval text (decision 10)

Status: planned

The brief composed in core (`approval_brief.compose_approval_brief`, `approval_brief.py:403`; one shape,
`_brief`, `:372-402`: `tool`, `input`, `purpose`, `risk`, `answers`, `blastRadius`, `blastRadiusLine`,
`summary`, `reach`) gains three fields, composed from the call's facts by its family (§3.3.1) and the
decision (§8.3):

| Field | Type | Meaning |
|---|---|---|
| `action.sentence` | str | the question in words: "Run a command in ~/code/app?", "Change 2 files?", "Fetch a page from example.org?", "Submit a form on example.org?" |
| `action.rows` | `[{label, value}]` | the facts a person checks: command, working folder, file, host, query, recipient, agent, a form's fields; masked as `input` is; a value over 120 characters cut at a word |
| `action.change` | `{files: [{path, kind: create \| edit \| delete}], diff: str, diff_kind: unified \| cells, truncated: bool}` \| null | for a file change: the files, and the change as text capped at 400 lines: a unified diff (`diff_kind: unified`) for a text file, or the cell list `documents/sheet_edits.describe` gives (`diff_kind: cells`: "Pipeline!H2: (empty) → =G2*K2") for a workbook, where a workbook artifact's entry reads `{path: "<artifact name> (artifact)", kind: edit}` and the rows add "Drops" when the call drops what the copy cannot carry |
| `why_asked` | `{sentence, where}` | the decision's `asks_because` in words (§8.3), and where the setting that would answer it is changed |
| `chat_pattern` | `{kind, pattern, label, lasts}` \| absent | present exactly when "Always in this chat" is offered (§9.4): the suggested grant, which the `chat_pattern` answer's `pattern` repeats |
| `chat_hold` | `{suggest, label}` \| absent | present when "Always ask for `{suggest}` in this chat" is offered (§9.10) |
| `hosts` | `[{host, answers: [host_once \| host_chat \| host_always \| host_block], chosen}]` \| absent | the host answers the card may offer, in the card's order (§9.11); a call request's holds only `host_block` |

- The brief is composed on the server so the dashboard card, the dock, the toast, the phone and every
  channel print the same words; the frontend's renderer registry draws them and composes none (one server
  derivation is what `approval_brief` already promises for the blast radius, `:31-40`). It rides the
  approval entry, the `approval` frame and the approval part, so a reload, the queue, the phone and every
  channel read one object.
- An agent CLI's edit that sends no diff shows the file list with `diff: ""`; a path
  `security.is_sensitive_path` names gets no diff and a row saying its contents are not shown.
- A workbook's cells come from `documents/sheet_edits`: `describe` for `sheet_update`,
  `describe_replacement` for a `sheet_create` that replaces a workbook, and its sheets and ranges for a new
  one; past 400 lines `truncated: true`.
- A shell call's `action.rows` gain a row per `{{secret:NAME}}` it carries ("GITHUB_TOKEN: for
  api.github.com", or "not bound to a host"), "Runs" (the sealed program, §9.3), "Waits for
  input" for a command that will wait on its input, and for `background: true` "Runs in the background · up
  to 2 h · reports when it exits". The sentences cover a browse checkout ("Pay {total}
  to {merchant}?") and a question to a stronger model ("Ask {model} this?"). The shell fence's refusal
  card and its host card compose their sentences and rows here.
- The risk and consent copy is not reworded; the new lines are generated from the call's facts
  (`AGENTS.md`, security copy).
- `ApprovalAnswer` (`channel_delivery.py:61-90`: `key`, `label`, `ends`, `word`, `promise`) gains
  `pattern` for `chat_pattern` (§9). The additions are additive to the SDK's brief.

### 11.5 Invariants

Status: planned

| Invariant | Rail |
|---|---|
| Every surface prints the same action text | the brief is the only source: a web rail fails on a card that composes a sentence from `input`; `tests/test_approval_brief.py` extends to `action`, `why_asked` and `chat_pattern` |
| A docked answer is the source's own answer | one test per kind answers through the dock and through the source's route, with the same guards |
| The diff is capped and masked | a test with an oversized edit and a secret in its body |
| A sign-in ask never waits in unattended work, and the call runs after the sign-in | `test_sign_in_ask_never_raised_for_unattended_work`; `test_sign_in_ask_resolves_on_sign_in` |

### 11.6 What it must not do

Status: planned

The dock never auto-focuses, never answers, never reorders by importance; the text never decides
anything; no new kind of question beyond the sign-in, the secret request, the access request and the fill
request.

---

## 12. Seen

### 12.1 What it is

Status: planned

A per-chat `seen_through` instant kept on the server, so the desktop, the browser and the phone agree; a
chat is unread when its last settled reply is newer.

### 12.2 Store and shape (decision 14)

Status: planned

`chat_seen.json` (§1.4): `{"v": 1, "chats": {<session>: {"seen_through": <instant>, "updated_at":
<instant>}}}`, one writer, `chat_seen.py`. Not a chat-record field (a record write per glance would
churn the record and its readers) and not a transcript event (it is not part of the conversation).

### 12.3 Reads and writes

Status: planned

- `POST /api/chat/sessions/{session}/seen {through}`: monotonic (an older `through` changes nothing);
  `POST /api/chat/seen {through}` advances every chat (⇧Esc, #3822).
- The page advances it while the chat is open, its page visible and the window focused, so a chat left
  open in a background tab does not read as seen.
- `chat_seen {session, seen_through}` is sent on each advance.
- Derived, never stored: `unread` = the last `message_settled` assistant message's `settled_at` >
  `seen_through`, where a turn ended by the owner's own Stop or edit never counts; `needs_you` = the chat
  has a #3888 item, its own or a helper's (`run_state.helpers_waiting`, §4.3); `your_turn` = not running,
  not needing you, and the last message is a settled assistant reply.
- `GET /api/chat/sessions` rows carry `attention: {unread, needs_you, your_turn}`.
- **A long answer that lands while the owner is away**: `chat/answered`, a notification kind (owner
  the chat runner, `immediate`, info, neither attention nor decision), raised when a turn that ran 60 s or
  longer settles while no surface has its thread in view (no device advanced its `seen_through` in the
  last 10 s). It carries the ending in words (`turns.ending_summary`) and the reply's first line, masked, at
  most 140 characters ("A private chat finished" for a private chat); the rules matrix routes it, the bell by
  default, so #3897's rule stands, and the buddy (#3829) shows it as its ready state.
- **Since you looked**: `GET /api/chat/sessions/{session}/since?through=<instant>` → `{answers,
  files_changed, waiting, helpers_finished, since_at}` (owner only), read from records only.

### 12.4 Invariants and non-goals

Status: planned

| Invariant | Rail |
|---|---|
| Seen never clears "needs you" | a test: advancing seen over a waiting chat leaves `needs_you` |
| Every surface reads the same attention | Home's lanes, the sidebar pips and the tray derive from `attention` only |

Not per device, not per message, no read receipts to anyone, and never synced between homes.

---

## 13. The project as the place work happens

### 13.1 Today

Status: planned

A project binds a folder, a brief and an instructions template (`tasks/models.py:1001-1062`); "where work
happens" is read from a chat's `workspace_dir`, a project's folder (`memory_locality.project_folder`,
`memory_locality.py:100`; `work_folder`, `:127`), the memory partition (`partition_for`, `:183`) and
project trust (`guardrails/project_trust.project_decision`, `project_trust.py:117`); the chat list's
"folders" are sidebar groups (`dashboard/chat_folders.py`; `/api/chat/folders`, `dashboard/routes.py:848-851`),
and so are the artifact library's (`artifacts/folders.py`, `artifacts/folders.json`). A trigger made in a
project's chat records no project (`triggers/tools.py:834`), and "a trigger's fire, a hook and a tile run
in no project" (`action_providers/base.py:44-49`); a run-workflow action carries its own `project_id` in
its action config. Today's model and folder choices already have an agent tier (an agent's model pin, an
agent's `default_dir`).

### 13.2 The place

Status: planned

`place.place_of(session_key) -> Place`:

| Field | Type | Meaning |
|---|---|---|
| `project_id` | str | "" when the session is in no project, or when its project no longer exists |
| `folder` | str | the working folder, resolved; "" for none |
| `works_in` | `folder` \| `worktree` \| `sandbox` | where the chat works: its folder, its own worktree of that folder's repository, or a sandbox tier (`sandbox` joins in the change that makes a chat run in one) |
| `source_folder` | str | the folder whose memory, trust and folder skills the session uses: `folder`, except for a chat in its own worktree, whose source folder is the folder it was cut from |
| `extra_folders` | `[{path, origin: project \| access, access: read \| read_write, lasts: turn \| chat \| null}]` | a chat's only: the project's extra folders (`lasts: null`) and the folders an access request granted (`origin: access`, present only for the owner's own attended turns), read by `file_scope` and the posture read; `run_bounds` is never widened by one |
| `partition` | str | the memory partition `partition_for(source_folder)` gives: keyed by the folder's repository when the repository's own files confirm the folder belongs to it, else by the folder itself |
| `agent_partition` | str | the memory partition of the session's own agent when it is a kept agent, "" otherwise |
| `trusted` | bool | the project trust decision for `source_folder` |
| `origin` | `{folder: thread \| project \| agent \| none}` | where the folder came from, derived from `source_folder` and never stored: the project's bound folder → `project`; the agent's `default_dir` → `agent`; empty or the gateway's workspace → `none`; otherwise `thread` |

It keeps `memory_locality`'s ordering rule exactly (a chat's own folder first, then its project's, then
its agent's), and memory, project trust, the context builder, artifacts' and automations' project
stamping, folder skills and the posture view read it instead of their own lookups. Other work follows
`memory_locality.work_folder`'s chain: a subagent the place of the session it works for, a workflow step
its run's project, an automation's run its trigger's project (§13.5); `app:` keys have no place.
`place.calling_place()` answers the place of the session whose tool call is running: in the gateway, from
the turn's own session binding; in an agent CLI's tool process, through `GET /api/chat/sessions/place`
(the session in the `X-Session-Key` header, never a path or query value; `{project_id, project_name,
folder, works_in, source_folder, extra_folders, partition, agent_partition, trusted, origin}`), which
replaces `GET /api/chat/sessions/bound-project` in the routes, the tool process's allowlist and the route
permissions. An absent or unknown caller answers the empty place, never a default project.

- **A worktree chat** keeps its folder's memory, trust and folder skills: `partition`, `trusted` and
  `origin.folder` come from `source_folder`, and memory's readers read it (`memory_locality.chat_folder`);
  worktrees of one repository share its partition. Posture rows say "in its own worktree" (§8.4).
- **Memory partitions by repository**: one idempotent backfill (§1.5) moves a folder's partition
  under its repository's key where the repository's own files confirm it. An agent partition sits beside
  `partition` behind the same resolver: recall reads the place's partition, then the agent's, then the
  global one while the agent reads the owner's memory; a kept agent's write that would land in the global
  partition lands in its own, with what is about the owner copied to the global one (§17.10).
- **One kind of added folder.** A folder the owner grants from an access request and one they add from the
  shell fence's refusal card are written by the same function into the chat record's
  `access_grants`, so they are one kind, `origin: access`.

### 13.3 Project defaults

Status: planned

`Project` gains `defaults` (optional, every key optional), written only by the owner's route (an agent's
project tools make a project with none):

| Key | Type | Meaning |
|---|---|---|
| `agent` | str | |
| `model` | str | a model ref |
| `effort` | str | as the composer's effort control |
| `permission_mode` | `normal` \| `trust_reads` | decision 15: a project can make new chats start in Normal or Trust reads only (`400 project_default_refused` for anything else); Trust and YOLO stay a per-chat act |
| `extra_folders` | [str] | each validated as the working folder is (`chat_handlers.py:2255-2285`) |
| `starters` | `[{title, prompt}]` | the empty thread's starters |
| `worktree_copy` | [str] | the ignored files a new worktree of the project's repository copies; owner written, not a loosening |

`project_defaults.effective(session) -> {key: {value, origin: thread | project | agent | global}}` is the
one precedence function, one order for every key: the chat's own value, then its project's, then its
agent's, then the global one (the project is the narrower scope: a project's model is chosen for this line
of work, an agent's pin for the agent anywhere). `extra_folders` is additive: its value is
`[{path, origin}]`. It is read by the gateway when it builds a turn, by the composer to say "Model: from
project Billing API", by the posture view (§8.4) and by the model-substitution notice, which names the
project when its model could not serve. **One set of words for where a value came from**:
`project_defaults.origin_words(origin, *, project_name, agent_name)` composes them ("Your pick for this
chat", "From project {name}", "From agent {name}", "From Settings"), served beside each `effective` key, and
every surface reads them: the composer's origin notes draw the served words and compose none, and the
posture view's `setting` joins them to the mode ("Trust reads, from project Billing API").

The one write path is `PUT /api/project-defaults/{project_id}` (the whole `defaults` object, under
`If-Match`; `400 project_default_refused`, `404 project_not_found`, `409 stale_write`), read back by `GET
/api/project-defaults/{project_id}`; both owner only, and no app token reaches them. A save that loosens
what new chats may do (a `permission_mode` of `trust_reads` where there was none or `normal`, an added
extra folder) asks first (`400 confirmation_required`). Each save writes `projects.defaults_changed` (the
project, the keys changed, whether it loosened) and sends `posture_changed {session: ""}` (§8.6).

**Who writes a project** (landed). `/api/projects` is a security route family, declared
route by route: an app makes a project under a name only (a body naming the brief, the instructions or the
folder is refused before the handler runs, `AppMay.owner_only_fields`); changing, deleting and importing a
project, the default project, a Work-board claim and its release, and regenerating the context files are
the owner's; the reads stay with the manifest's allowlist; and a project's own folder is no file root of an
app's. `PUT /api/projects/{id}` takes no `defaults`. Nothing in this release adds a door of its
own.

### 13.4 Groups

Status: planned

The chat list's organisation and the artifact library's are renamed **groups** (decision
46), so "folder" always means a directory: `/api/chat/groups` (the four routes of
`dashboard/routes.py:848-851`), `groups.json`, the chat record's `group_id`, `PATCH
/api/chat/sessions/{session}/group {group_id}`; `/api/artifacts/groups*`, `artifacts/groups.json`, an
artifact's `group_id`; the operations `chat.group_*`; and the words ("New group", "Remove from group"). One
idempotent backfill (§1.5) writes each `groups.json` from its `folders.json`, verifies it and then removes
the old file, renames `folder_id` to `group_id` in each chat record and artifact, and renames the
organiser's keys. The `folders` inventory entry is renamed `groups`; the artifact file stays inside the
`artifacts` entry. #3815's Library follows the same words.

### 13.5 Automations belong to their project

Status: planned

A trigger gains `project_id`, part of what the trigger is (not a runtime field, so it is compared between
homes, carried by a sync and covered by a revision, §10.1). It is set from the creating session's place
(`place.calling_place()`), never from a tool argument: no tool schema declares it, and an argument of that
name has no effect. The owner's dashboard dialog sets the project the owner picks; `personalclaw cron add`
takes `--project`. Both dispatches set `ActionContext.project_id` from the trigger when its project exists,
so the work's secrets fill from that project first and its memory is the project's; a trigger whose
project is gone is refused at dispatch through `triggers/cannot_run.py` (`PROJECT_GONE`: "The project it
belongs to no longer exists…"), and its run reads `refused`. The rule `action_providers/base.py:44-49`
states is changed in the same commit: a lifecycle hook and a tile run in no project.

A run-workflow action's own `project_id` is moved onto its trigger by one idempotent backfill (when the
trigger has none; the grant block untouched, so what runs and where are unchanged) and refused in an action
config afterwards: two sources for one run's project would disagree.

**A trigger's project is part of what its grant covers** for an action that starts work in a project
(run-prompt, invoke-agent, run-workflow) (decision 32): `triggers/grants.narrow` compares the trigger's
`project_id` as it compares the action's config, so a change of project the owner did not make (in
practice a sync's, since no tool sets it) is treated as an action change by §10.5's rule for its door: a
sync's is taken in with the grant narrowed and its runs refused until the owner allows it, and any other door's
is held aside. The owner moves an automation through `PUT
/api/triggers/{id}/project {project_id}` (`""` for none; `404 project_not_found`; owner only), which asks
first when its grant covers project-reading work ("It will run in Billing API and use that project's
secrets. Move it?") and, on their yes, gives the grant for the moved automation, so their own move never
switches it off; it writes `triggers.project_changed`. The grant question for an automation in a project
says so ("It runs in project Billing API and can use that project's secrets."). This rule is
built in the change that moves an automation's project, and the project chip on the automation list in
the change that says what each row needs.

### 13.6 Invariants and non-goals

Status: planned

| Invariant | Rail |
|---|---|
| One resolver of place | a rail fails on a new reader of `workspace_dir`, `project_folder` or `chat_folder` outside `place.py` and `memory_locality.py` |
| One precedence | `test_project_defaults_precedence` over every key and origin |
| A trigger's project comes from its session | a test passes a `project_id` argument and asserts it is ignored |
| A worktree chat reads its source folder's memory and trust | `test_a_worktree_chat_keeps_its_folders_memory` |
| A project change by another author drops the grant | a table test: a synced copy with another project narrows the grant; the owner's move keeps it after their yes |
| "Folder" names a directory | a rail: no `folder_id`, `/folders` route or `*.folder_*` operation for a grouping in `src/` |

No cross-project reads, no project-level Trust or YOLO, no nested projects. An access folder never widens
an unattended run, and nothing but the owner's answer adds one.

---

## 14. Evidence records, verdicts and worlds

### 14.1 The evidence record

Status: planned

Each journey test, pytest journey, manual walkthrough or contribution review writes one `record.json`:

| Field | Type | Meaning |
|---|---|---|
| `v` | 1 | |
| `id` | `ev_<ULID>` | minted by the record writer (§2.1) |
| `name` | str | the journey's or walkthrough's name |
| `source` | `journey` \| `pytest` \| `manual` \| `review` | who wrote it |
| `spec` | str | `web/e2e/<file> › <title>`, a pytest node id, or "" |
| `sha` | str | the commit the tree was at; for a review, the reviewed head |
| `dirty` | bool | whether the tree had changes |
| `seed` | str | the fixture seed, "" for none |
| `script_digest` | str | SHA-256 of the scripted model's script, "" for none |
| `world` | str | the world it ran in (§14.2), "" for none |
| `gateway_version` | VersionIdentity (§17.5) | of the checkout |
| `started_at`, `ended_at` | instant | |
| `outcome` | `passed` \| `failed` \| `incomplete` | computed by the writer from the steps and claims, never taken from the caller: `passed` only when every step is `ok` and every claim observed and ok; a skipped step, a step not reached or a claim not observed makes it `incomplete` |
| `reason` | str | why it is not `passed` (the first failing step or claim); "" when it is |
| `steps` | `[{name, state: ok \| failed \| not_reached, at, reason, screenshots: [str]}]` | a step after a failure is `not_reached` ("after a failed step"); a skip is `not_reached` ("skipped: <why>"); screenshot paths are relative to the record's folder |
| `claims` | `[{statement, observed, ok}]` | one assertion line each; `observed` "" means not observed |
| `artifacts` | `[{kind, path}]` | `kind`: `gateway_log`, `audit_log`, `witness`, `egress_log`, `egress_verdict`, `home_archive`, `trace`, `junit` |

One writer, `harness/evidence.py`, validates closed keys and types, mints `id`, fills `sha`, `dirty` and
`gateway_version`, computes `outcome` and `reason`, and passes every string through
`security.redact_for_display` (`security.py:1255`) before it writes the record atomically. The browser
layer, the test suite's journeys and a manual walkthrough (`python -m harness evidence add`) write through
it, so one schema, one validator and one mask serve every language. The store is
`$PERSONALCLAW_EVIDENCE_DIR` when set (CI), else `$(git rev-parse --git-common-dir)/personalclaw-evidence/`,
shared by every worktree so a record outlives the worktree that made it.

### 14.2 Worlds

Status: planned

**The blank slate**. `personalclaw gateway --blank-slate [--keep-blank-slate]` makes
`<tmp>/personalclaw-blank-slate/<blank_ULID>/` holding `home/`, `workspace/` and `blank_slate.json`
(`{v: 1, id, pid, started_at, keep}`, `0600`), then, before anything resolves a home (ahead of the seed and
the log sinks), sets `PERSONALCLAW_HOME`, `PERSONALCLAW_WORKSPACE`,
`PERSONALCLAW_CREDENTIAL_BACKEND=dotenv` (a blank slate writes no keychain item) and
`PERSONALCLAW_BLANK_SLATE=<root>`; a `PERSONALCLAW_HOME` already set is ignored, and the start says so.

- It binds loopback only, on port `auto`. An explicit `--port` is refused (exit 2) unless the start is
  also `--test-mode`, which the harness starts its worlds with, so a browser suite can be configured
  before its gateway starts (decision 30); port 10000, the installed PersonalClaw's port, is refused
  always. Authentication stays on.
- It composes with `--seed` (into the empty `home/`) and `--test-mode` (`cli.py:43-80`).
- Service install, every update apply (the owner's, the CLI's and the staged one) and the uninstall refuse
  with `409 blank_slate_refused` or its sentence.
- It makes no unprompted update check: `self_update.hold_unprompted_checks(reason)` latches the process
  for a blank slate, a seeded start and a test-mode start alike (a fixture is not an install, and a test
  run must not depend on or reveal itself to the network), and Check now still works. `personalclaw
  gateway --no-update-checks` (public: "Make no update check on its own; Check now still works") holds the
  latch, and `--seed`, `--test-mode` and `--blank-slate` imply it.
- **A restart serves the same root.** A restart's new image gets the command line less the seed options
  (the landed one-gateway fix: a restart never replays the seed), so once the root exists `cli.main`
  replaces `--blank-slate` and `--keep-blank-slate` with `--blank-slate-root=<root>` (no help text) and
  puts `--no-update-checks` in place of the seed options it drops. A start given it accepts the root only
  when it sits directly under the blank-slate folder, is not a link, and holds a valid marker naming this
  pid (a restart keeps its pid); it then sets the same four variables, and refuses (exit 2) otherwise.
- The READY line gains `"blank_slate": {"id", "root"}`; `/api/status` gains `blank_slate: {id,
  started_at, keep, labelled}` (null otherwise).
- **The label.** A blank slate a person starts says so everywhere: the dashboard shows a banner on every
  page beside the incident banner (`role="status"`, not dismissable: "Blank slate — a throwaway
  PersonalClaw. Nothing here touches your assistant, and it is deleted when it stops."), `document.title`
  starts "Blank slate · ", and the desktop's `--blank-slate` titles every window "PersonalClaw — Blank
  slate". Under `--test-mode` (`labelled: false`) it draws no label in the page or the title: no person
  drives it, and the visual baselines, the docs images and the website's frames show the product as it
  ships. Its refusals and its root handling are the same either way.
- On a clean stop that is not a restart it removes its root unless `keep` (then it prints the path); the
  next `--blank-slate` start sweeps roots whose marker names a pid that is not alive, only directories
  under the blank-slate folder holding a valid marker, never through a link. The desktop's `--blank-slate`
  keeps its own profile and single-instance lock in a temporary folder of its own, so it runs beside the
  installed app.
- It depends on the per-home keychain namespace (landed).

**Worlds**. `python -m harness world up|down|keep|list|show|env|url|help [--json]` holds a
gateway in a known state, found by name, with a receipt. Two kinds, decided by the name (`^[a-z0-9][a-z0-9-]{0,39}$`):

| Kind | Names | Home | At `down` |
|---|---|---|---|
| `scratch` | any but `dev`, `dev-*` | a blank slate started `--test-mode`, with a scripted model (§17.3) and egress denied | everything is removed |
| `dev` | `dev`, `dev-*` | a named persistent home (`--home DIR` on the first `up`, through `named_home.scratch_home`, then remembered), on a free port it remembers; it binds loopback with the bypass for this machine's own clients, or every interface under `--lan`, as `make serve` and `make serve-lan` do (landed) | the home is kept |

- A world's folder is `$(git rev-parse --git-dir)/personalclaw-worlds/<name>/` (`0700`): under the
  checkout's own git directory, since names are per checkout and a world runs its checkout's code, which it
  checks before it starts. Its receipt, `receipt.json` (`0600`), holds `{v, name, kind, home, workspace,
  url, port, pid, started_at, seed, script, script_digest, witness, egress: {denied, proxy_pid, log}, lan,
  foreground, keep_into, checkout}` and never a credential; a sign-in token is minted when asked for
  (`world url`).
- **The deny proxy, and two witnesses.** A scratch world's refusing proxy is the deny proxy, never "the
  egress guard", which is the product's host policy. The world's verdict reads the deny proxy's log and the
  guard's own `egress_fetch` rows (the door `shell` included, a `failed` row read as refused) and fails when
  either cannot be read: the product's guarded client and the update check ignore proxy settings, so a proxy
  alone would report "nothing left" unseen. The shell fence's per-command proxy chains to the environment's
  proxy, so the deny proxy sees a proxied attempt.
- **Statuses.** World commands exit 0, 1 refused, 2 a usage error, 3 the home already served (passed
  through from the gateway's own status, §16.3) and 4 the egress proof failed; a foreground world exits
  with its gateway's status.
- **A world outlives its gateway's restart.** A restart execs a new image in the same process, so the
  world's pid, receipt and slate root stay; the witness's lines name the image that wrote them
  (`image_at`, §17.3), so a journey reads what the model was offered after a restart.
- No world names the default home: not a flag, not an environment variable. Port 10000 is refused for
  every world.
- A world that keeps a bundle at exit (`--keep-into`, `world keep`) starts its blank slate with
  `--keep-blank-slate`, writes the bundle (its receipt, the gateway's log, the audit trail, the witness,
  the egress log and verdict, the script and a home archive without the `security` component) once the
  gateway has stopped, then removes the root.
- The client is `harness/named_home.scratch_gateway` (`harness/named_home.py:189`, landed): it finds a
  gateway from its home's runtime record through `gateway_base.live_gateway` (`gateway_base.py:162`) and
  signs in through the home's local secret, never a port literal. No second client module is made.
- `world help --json` is the discovery contract (the kinds, the seeds, the scripts with their prompt
  selectors, and example commands a test parses through the real argument parser).
- A per-spec world joins #3939 as a worker-scoped fixture yielding `{baseURL, home, witness}`.

### 14.3 The verdict vocabulary (decision 16)

Status: planned

`passed`, `failed`, `incomplete` for a record; `ok`, `failed`, `not_reached` for a step. One home:
`tools/ci/verdict.py` (`RECORD_OUTCOMES`, `STEP_STATES`, `record_outcome(steps, claims) -> (outcome,
reason)`), the module #3909's typed catalogue writes its per-job verdict JSON with; the harness imports it.
If #3909 has not shipped, the evidence change creates the module there and #3909 adopts it. A CI job's verdict reads
the same words: `incomplete` when a test skipped for a reason none of the job's declared skips matches.

### 14.4 Invariants and non-goals

Status: planned

| Invariant | Rail |
|---|---|
| A green run with a skip is not `passed` | the record's outcome rule, tested with a skipped step (`tests/test_evidence_outcome_rule.py`) |
| Nothing written carries a secret | a planted token in a step's text is masked in the record |
| No tool outside the test suite reaches the real home or the default port | `tests/test_dev_tools_home_rail_no_install_by_default.py`, widened to the `Makefile`, `web/vite.config.ts`, `web/playwright.config.ts` and `web/e2e/`, with a planted violation per form (a Make assignment, a JavaScript port fallback, a `localhost:10000` in a spec) |
| A blank slate touches nothing of the install | `test_a_blank_slate_touches_nothing_of_the_install` under the real-home audit hook; a web test renders the label on every route of a labelled slate and none on a test-mode one |
| No tool opens a home's `gateway.lock` | `test_worlds_never_open_the_home_lock` |

Evidence is not telemetry: it never passes through an exporter, and goes nowhere but a CI run's own
artifacts and the checkout's git directory.

---

## 15. Provenance of installed capabilities

### 15.1 One record

Status: planned

Every installed skill, pack and app records where it came from with the same fields:

| Field | Type | Meaning |
|---|---|---|
| `source.kind` | `folder` \| `git` \| `link` \| `catalog` \| `marketplace` \| `pack` \| `app` | where its bytes came from; `pack` and `app` for a capability installed as part of another, which updates through it |
| `source.pointer` | str | by kind, below |
| `source.detached` | bool | the owner stopped offering updates; nothing is checked, and the record is kept |
| `upstream.revision` | str | the source's commit, version, release or etag at install or last update; "" unknown |
| `upstream.fetched_at` | instant \| "" | |
| `digest` | str | the installed bytes' digest, by kind, below |
| `installed_by` | Author (§2.5) | |

| Kind | `pointer` | Updates offered |
|---|---|---|
| `folder` | an absolute path | packs: yes; a skill brought over from another tool's setup: yes, through the import's Check for changes, its machine-local digests kept in `onboarding/import_sync.json` |
| `git` | `<repository URL>[#<subfolder>]` | apps (#3850) |
| `link` | an https URL; "" for a pasted document | yes, except "" |
| `catalog` | `<catalog>/<id>`; `bundled:<pack>` for a shipped pack | yes |
| `marketplace` | `<marketplace>/<id>`; `native/<name>` for a shipped skill | yes, when the marketplace reads revisions |
| `pack` | the pack's name | through the pack |
| `app` | the app's name | through the app |

Digests: a skill's is the landed `skills/marketplace.files_digest` over its files' per-file SHA-256 map,
without the install record (§2.4: the SHA-256 of the sorted `<path>\0<sha256>` lines, kept as built because
the recorded digests of earlier shipped versions depend on it); a pack's is `canonical_digest({ref:
computedHash})` over its `component_locks` (`packs/installed.py:89`); an app's is its bundle digest
(`apps/disclosure.bundle_digest`, `apps/disclosure.py:132`).

A skill's install record (`.pclaw-lock.json`, landed as `skills/marketplace.InstallRecord`, written only by
`write_install_record`, whose door states its own `source` and author) becomes v1 (`{v: 1, id, trust_tier,
verdict, sha256, installed_at, source, upstream, installed_by}`): it keeps the per-file `sha256` map and
derives `digest` from it (`InstallRecord.digest`), never storing it, since a stored digest could drift from
the map. `InstalledPack` (`packs/installed.py:44-89`) gains `source`, `upstream`, `digest`, `installed_by` and
`revisions`; an app's origin and pinned commit come from #3849 and #3850, with no second store. A Skills row
carries `also_in: [{tier, path, differs}]`, the same name in other tiers: a copy identical to the one served
stays silent, and one that differs is named. One idempotent backfill (§1.5) converts the lock's
free-text `source` and gives a pack row its source; `upstream` and `installed_by` read unknown until the next
install or update. `personalclaw/provenance.py` is the one reader (`of_skill`, `of_pack`, `of_app`,
`installed`), and the Skills, Packs and Apps rows carry `provenance`.

### 15.2 The update offer

Status: planned

`SkillsMarketplace.revision(skill_id) -> str | None` (optional per marketplace; None means "cannot say", never
"update available") reads the upstream revision only on a Store discovery read and on the owner's Check for
updates, within the discovery budget (at most 50 skills a pass within 8 s, each answer cached an hour). An
update is offered when the cached revision differs from `upstream.revision`, the source is not detached and
the owner has not declined that revision. **One offer for every source**: a `GET /api/skills`
row carries `update: {kind: shipped | source, from, to, checked_at} | null` in place of the landed
`bundled_update` (`shipped` when the release ships a version over the owner's edited copy); declines are kept by
version (the shipped digest for a shipped skill, the source's revision otherwise) in the landed
`entity_settings/skill_updates.json`, which gains `notified`; one pair of routes serves both kinds, `POST
/api/skills/{name}/update/preview` and `POST /api/skills/{name}/update {consent}`, with `POST …/update/decline
{version}` for Keep mine and `POST /api/skills/{name}/source {detached}`; the landed `POST
/api/skills/bundled/update` and `…/keep` go, their owner-only rows moving to these routes. Accepting it is two
calls: a preview fetches, stages, scans at the source's tier, shows the diff and answers a `consent` digest of
what it read; the apply fetches again and installs only bytes that still match the consent, replacing the
folder whole and re-locking. A copy the owner edited since it was installed is replaced only with a consent
computed over it, which only a preview of the edited copy yields (the pack rule that a drifted copy is skipped
holds for packs, `packs/update.py:1-28`). An accepted update is a revision by `import` (§2.5, §10). Packs
import and update from a recorded `folder` or `link` source by the same pattern.

### 15.3 Folder skills

Status: planned

Skills a repository carries (the neutral layout `.agents/skills/<name>/SKILL.md`, and the layouts enabled
apps declare through `folder-skill-layouts`, read from the folder up to its repository root) are offered
for that folder only, sealed to their bytes in `GrantBook("folder_skills")` (§17.1) under
`<folder_key>/<name>` (`folder_key` = the first 16 hex of the SHA-256 of the folder's real path) with the
skill's content digest; a skill whose bytes changed since the owner's yes asks again and is not served,
and the library's copy of that name is not served in its place. Their tier sits between an agent's own
skills and the home's; only sessions whose place (§13.2) is at or below the folder see them; inbound
clients never do. A decline is a preference, kept in entity settings, not in the grant book. The switch is
`skills.folder_skills` (on). A chat in its own worktree (§13.2) reads its folder skills from the worktree
and seals them under its source folder's `folder_key`, so the owner's yes carries and a changed byte still
asks. A folder's instruction files are sealed the same way in the `folder_instructions` book
(§17.1).

### 15.4 Invariants and non-goals

Status: planned

| Invariant | Rail |
|---|---|
| Every installed capability names its source and digest | the provenance census reads each kind's record through each door (`tests/test_provenance_census.py`) |
| An update never lands unscanned, over bytes other than those reviewed, or over the owner's edit unasked | the skill update tests, and the pack update tests extended to sources |
| A folder skill runs only on the owner's yes to its bytes, only in its folder | a test changes one byte and expects an ask; a session outside the folder never sees it |
| Every write to a skill names its author and is a revision | an AST rail over every call of the skill writer; the revisions conformance kit for skills |

No auto-update from a source, and no upstream polling outside the Store's discovery budget. Shipped skills
change with the release by the drift rule: an unedited copy is replaced, and a copy the owner edited is
kept, the release's bytes offered as an update (`skills/shipped.sync` replaces today's modification-time
sync).

---

## 16. Incidents, release metadata and boot refusals

### 16.1 The incident record

Status: planned

Today `incident.json` holds `{active, reason, started_at}` and Resume erases it
(`guardrails/incident.py:117-125`); `GET /api/incident` returns the current state only
(`dashboard/handlers/core.py:1206-1240`); the Settings switch always records "Activated from Settings"
(`web/src/pages/settings/GuardrailsPanel.tsx:310-311`); a hook fire during an incident is dropped
(`triggers/history.py:175-185`).

`incidents.jsonl` (§1.4) holds two line types, both with `v: 1`:

| Line | Fields |
|---|---|
| `{type: "incident"}`, written at Resume | `id` (`inc_<ULID>`), `started_at`, `ended_at`, `reason` (required), `activated_by` (`owner` \| `cli` \| `app`), `app` (the app's name when an app activated it, else ""), `resumed_by` (`owner` \| `cli`), `suppressed` |
| `{type: "note"}`, written when the owner adds "What happened?" | `id` (`note_<ULID>`), `incident` (`inc_…`), `note` (1-4,000 characters, stored as data and never handed to an agent), `at` |

`suppressed` is `{runs_skipped, events_dropped, hooks_dropped, loop_cycles_skipped, spawns_refused}`, each
`{count, refs: [WorkRef]}` (the first 50 refs): a scheduled trigger's fire refused (`trigger:<run id>`), an
event or idle trigger's fire refused, a script hook's action skipped, a loop cycle skipped (`loop:<id>`), a
subagent spawn refused (`subagent:<id>`). File and page watches, held workflow runs and loops, and the
maintenance passes are not counted: they catch up or carry on, so nothing is lost. While an incident is
active, `incident.json` keeps today's shape plus `activated_by`, `app` and `suppressed`, so the CLI in
another process can write Resume's line; each seam's existing skip calls `incident.note_suppressed(counter,
ref)` under the file's lock, only while the file still names the same `started_at` and is active, best
effort, never raising and never activating anything. An incident record's `note` is its newest note line.

- A reason is required on every door: `POST /api/incident {reason}` (1-200 characters after trimming)
  answers `400 incident_reason_required` without one; `personalclaw incident on` requires `--reason`; the
  Settings switch asks for it in a dialog. An app keeps `POST /api/incident`, its stop.
- Resume (`POST /api/incident/resume {confirm: true}`, `personalclaw incident off`) appends the incident
  line, then switches off, and answers `{active: false, incident: <the line>}`. A line that cannot be
  written does not stop Resume: the owner's decision to resume is honoured, the answer carries `recorded:
  false` with the reason, the failure is logged, and the Doctor's incident-history probe fails until a
  line is written.
- `GET /api/incident` gains `suppressed`, `activated_by` and `app` while active. `GET /api/incidents` →
  `{items, next_cursor}`, newest first, 20 a page, each with its newest note; `POST
  /api/incidents/{id}/note {note}` → `201` with the note line, `404 incident_not_found`, and writes
  `incident.reviewed`. `/api/incidents` is owner only. Settings › Guardrails lists the past incidents, and
  Resume's summary shows the counts with each dropped hook or event fire offered a Run now.

| From | To | When | Writes |
|---|---|---|---|
| off | active | the switch, `personalclaw incident on --reason`, or an app's stop, each with a reason | `incident.json`, with the reason and who activated it |
| active | active | a seam skips work | the counts, in `incident.json` |
| active | off | Resume | one incident line; Resume's summary shows the counts |
| off | off | the owner adds the note | a note line, and `incident.reviewed` |

### 16.2 Release metadata

Status: planned

- **Withdrawn.** A release body whose first line matches `^> Withdrawn on (\d{4}-\d{2}-\d{2})\. Use
  (v\d+\.\d+\.\d+)\.$` is withdrawn; the first line only, so a note that quotes the marker is no
  withdrawal. One anchored regex, in one module, `personalclaw/release_meta.py` (pure, standard library
  only), shared by the updater and the release scripts (`withdrawn`, `with_withdrawn`, `known_issues`,
  `with_known_issue`). The release view (`self_update.py:732-739`: `tag`, `name`, `body`, `prerelease`)
  gains `published_at`, `withdrawn: {on, use} | null`, `known_issues` (the body's `## Known issues`
  section) and `assets: [{name, size, url}]`; the releases cache gains `"view": 2`, and a cache of another
  view is discarded and the list fetched again. `select_target` (`self_update.py:750-809`) skips a
  withdrawn release on every channel; a pin to one still resolves and says it is withdrawn, and the owner's
  Update onto it asks first. The status gains `running_withdrawn`, `pin_withdrawn`, `known_issues` and
  `soak: {hours, ready_at} | null`.
- **Soak.** `updates.soak_hours` (§1.2) makes an unattended staged apply wait until the release is that
  old (`published_at` plus the hours); an unknown publish time is not soaked (fail closed); the owner's own
  Update ignores it.
- **Run state.** `update_run.json` (`self_update.py:387-413`), one writer (`run_state.py`, under its
  lock), epoch seconds: `version`, `recorded_at` (as today), `previous` (the version that ran before
  `version`; the value `updates.last_version` held), `boot_id` and `started_at` (the latest start, §16.3),
  `stopped_at` (when the latest start ended by a stop, a restart it asked for, or a refusal), `healthy_at`
  (after health is green and no crash artifact is written for two minutes), `failed_starts` (starts of
  `version` that ended before `healthy_at`, not by a stop, a restart or a refusal), `previous_healthy:
  {version, healthy_at} | null` (the last release version, other than `version`, that became healthy),
  `applied: {from, to, by: staged | owner, at} | null`, `returned: {from, to, at, noticed} | null`,
  `skipped` (releases the staged apply never applies again, at most 20), `unclean_ends` ([float]: when a
  start found the one before it ended uncleanly; within five minutes, at most 3), `recovery` (`{by: crashes
  | owner, at, held: [str]} | null`: the latest start is a recovery start) and `stalled` (`{at, secs,
  boot_id, noticed} | null`: the stalled-loop restart). It takes the landed rule that a file that cannot be
  read is never written over: read through `record_files.read`, `begin_start` then writes nothing and
  answers empty facts, the Doctor names it and its kept copy, and surfaces read it through
  `record_files.read_or_none`. `run_state.begin_start` replaces the start-up version recorder. At a
  start of a version other than `version`, the old one becomes `previous`, and becomes `previous_healthy`
  when it had `healthy_at` and is a release version, so Roll back is offered from a new version's first
  second; Roll back offers `previous_healthy`. `updates.last_version` leaves `config.json` (decision 17):
  machine state that is not a knob gets its own file (§1.3); a backfill moves an existing value into
  `previous`.
- **Automatic return** (staged checkouts only): two failed starts of a release the staged apply brought
  return to `previous_healthy`, once per release and never while a boot refusal stands, record the release
  as skipped, write `update.rolled_back` and raise an Inbox item once the healthy release serves.
- **A recovery start.** Two unclean ends within five minutes (the automatic return goes first), or
  `personalclaw gateway --recovery` (a process latch, never persisted, implying `--safe-surfaces`), start
  the gateway without its side services (`recovery_start.held(group)`): every channel's receiver, the
  automations (schedules, watches, the heartbeat, the Inbox's polling), each app's backend and workers,
  and resumed work (loop cycles, the workflow runs a restart suspended, the journal's pass and the work
  queue's re-dispatch, §5.8). Nothing is loosened: every setting, gate and hook keeps applying. `/api/status`
  gains `recovery`, every page shows its banner, and `POST /api/recovery/start {group: channels |
  automations | app | everything, name?}` starts what it held (owner only; `409 recovery_not_active`, `404
  recovery_service_unknown`; `gateway.recovery_service_started`); the start itself writes
  `gateway.recovery_start`. A recovery start never marks a version healthy. One that also ends uncleanly
  makes the next start the refusal `ERR_BOOT_KEEPS_CRASHING` (§16.3).
- **A stalled event loop** restarts the gateway: a daemon thread (`loop_stall.py`, off under
  `--test-mode`) asks the loop to answer every 30 s; three misses in a row restart it through the one
  in-place restart path, with `stalled` set and `gateway.loop_stalled` written. Its wake stays a thread, not
  a scheduler job (§17.15), because it watches the loop the scheduler runs on.
- **Signed sums.** Once the trusted key lands (#3848), a release attaches `SHA256SUMS.sig`, by a key that
  may sign releases only (`trusted_keys/`, marked `signs: releases`; neither key verifies the other's
  kind). A wheel update then installs only a wheel the signed sums name, downloaded and checked first;
  a missing or bad signature, a wheel the sums do not name, or another hash refuses with `502
  update_unverified`, and a release published before signing asks the owner. The updater runs only the
  `uv` the owner allowed (the `installers` book, §17.1; `update.installer_allowed`).
- **Desktop installers.** A desktop update downloads the release's installer into
  `<home>/desktop-installers/<tag>/` only when the owner asks, opens it only after its SHA-256 matches the
  release's published checksum file (checked by the gateway and again by the shell), writes
  `update.installer_opened`, and keeps the running release's and `previous_healthy`'s installers as the way
  back.

| An applied release | Becomes | When |
|---|---|---|
| applied | healthy | health green and no crash artifact for two minutes: `healthy_at` set |
| applied | failing | a start fails before `healthy_at`: `failed_starts` + 1, and the next start, `status` and §16.3's record name the way back |
| failing | returned | the second failed start of a staged checkout's release: back to `previous_healthy`, the release skipped |
| healthy | withdrawn | its release body gains the marker: `running_withdrawn`, shown in Settings › Updates, on Home and by the Doctor |

### 16.3 The boot-refusal record

Status: planned

Today a refused boot prints WHAT, WHY and FIX to stderr and exits 1 (`cli_server.py:1169-1189`); `status`
advises a restart (`:1060-1093`); the desktop shows the last stderr line (`desktop/gatewayLost.js:50-70`);
a refused start under the macOS service is restarted for ever; a second start on a served home overwrites
its runtime record and local secret.

**One refusal type.** `boot_refusal.BootRefused(error: AgentError, *, inputs=())` is every refused start that
writes the record; `GovernanceBootError` subclasses it. Its codes are registered in `errors.ERROR_CODES`, each
in the change that adds its raise site: the nine `ERR_GOVERNANCE_*`, `ERR_GOVERNANCE_SCOPE_UNDESCRIBED`,
`ERR_BOOT_PORT_IN_USE` (with the claim held, never this home's gateway), `ERR_BOOT_HOME_NOT_CLAIMED` and
`ERR_BOOT_KEEPS_CRASHING`, held by a both-directions rail (a refusal raised with an unregistered code, a
registered boot code no raise site uses). A refused start prints the WHAT, WHY and FIX block and exits `78`
(`EXIT_REFUSED`, distinct from a crash's 1), except under a service (`PERSONALCLAW_SERVICE` set in the unit's
environment) from its third refusal in a row (`attempts >= 3`), when it exits 0, the only status that stops
both service managers retrying; the two retries before it give a cause that clears by itself its chance.

**The home claim** (landed). A start claims its home first, in `cli.main` before the boot
(`gateway_base.claim_home`: an exclusive, non-blocking lock on `<home>/gateway.lock`, held for the
process's life and released by the operating system on any exit), before it seeds, binds, publishes or
writes anything.

- **A served home** (decision 29). While another gateway serves the home, the start is
  `gateway_base.HomeAlreadyServed`, never a `BootRefused`: it prints its one sentence naming the running
  gateway, has no agent code, writes nothing (no record and no start mark: the home is served, and the
  record only ever says why PersonalClaw is not running), and exits 3 (`gateway_base.HOME_SERVED_EXIT`), as
  built, documented in the CLI reference's exit table and tested. 3 is already distinct from a crash (1), a
  usage error (2) and a recorded refusal (78), so the desktop, a world and `personalclaw run` act on it
  without reading words; 78 says the configuration is wrong, and nothing about a served home is. The
  desktop's first-start dialog reads no record after 3 and shows the sentence the gateway printed.
- **Under a service it waits**. `cli._claim_the_home()` prints the sentence and "This service
  starts PersonalClaw when that gateway stops.", then `gateway_base.wait_for_home()` takes the same lock
  blocking (asleep in the kernel, no poll, nothing written; SIGTERM ends it at once), says "The gateway that
  served <home> stopped; starting." and goes on. So it spends no restart: exiting 3 under launchd would
  start an interpreter every ten seconds, and under systemd would spend the unit's three starts in five
  minutes, which the recovery start counts on. The desktop's backend and a person's start are no services:
  they exit 3. `personalclaw stop`, with a service installed, stops the service (a waiting one included),
  then a gateway outside it; `restart` stops a gateway outside the service first; `status` says "waiting:
  another gateway (pid 4711) serves this home".
- **A restart hands its claim to its new image.** `restart_request.start`, the one in-place restart path,
  clears the claim descriptor's close-on-exec flag before the exec and names it in the new image's
  environment, `PERSONALCLAW_HOME_CLAIM=<fd>:<device>:<inode>`, set afresh each time; `claim_home` in the new
  image takes the variable out before anything starts a child, checks that the descriptor is a regular file
  whose device and inode are `<home>/gateway.lock`'s (read without following a link), takes the lock on it
  without blocking and keeps it; any mismatch closes it and claims afresh. So a waiting service never wins
  a restart.
- **A home whose claim cannot be taken** (`gateway_base.HomeNotClaimed`: a link at `gateway.lock`, something
  there that is not a file, a disk that cannot lock files) is a `BootRefused` with `ERR_BOOT_HOME_NOT_CLAIMED`:
  a fail-closed refusal with a fix the owner must make, recorded best effort with `boot_id` "" (a start
  refused at the claim makes no start mark) and exiting 78 like every refusal whose cause stays until
  someone acts. Until that change lands it exits 1, as the CLI reference says.
- A `--seed-replace` of a served home is refused the same way, before any file is touched, and a seed
  empties the home but keeps its lock. Nothing opens a home's lock to check it (§14.4), but the uninstall
  executor's hold.

`boot_refusal.json`, written atomically by `boot_refusal.record(exc)` from the one handler in
`cli_server._gateway`:

| Field | Type | Meaning |
|---|---|---|
| `v` | 1 | |
| `code` | str | the refusal's registered agent code |
| `what`, `why`, `fix` | str | as printed, masked |
| `at` | instant | |
| `attempts` | int | refusals in a row: the previous record's + 1 when one stands, else 1 |
| `inputs` | [str] | the names of the files and settings the refused step read; never values |
| `boot_id` | str | the refused start's start mark: `run_state.begin_start`'s `boot_<ULID>` (§16.2); "" for a refusal at the claim, which makes no start mark |
| `way_back` | `{version, command}` \| null | the last version that ran well and the command that returns to it, when the running version has failed starts since it was applied |

It is ignored by the inventory and never restored (decision 18), cleared by the first boot that serves
(after the dashboard binds, before the READY line), and read first by `status` ("PersonalClaw refused to
start at 09:14 (3 attempts)." then what, why and "Fix: <fix>"), `doctor` (a process-tier probe, which runs
while the gateway is down) and the desktop's first-start dialog; `status`'s hint becomes the record's
`fix`. It reads as no record when absent or unreadable, and when a start began after it (the run state's
`started_at` later than its `at`), since a start refused at the claim makes no start mark. A refusal also
writes the run state's `stopped_at`, so it never counts as a failed start.

**A loop it cannot break.** A recovery start (§16.2) that also ends uncleanly makes the next start a
refusal, `ERR_BOOT_KEEPS_CRASHING` ("PersonalClaw stopped unexpectedly 3 times in 5 minutes, the last time
with its side services held back" / "Something PersonalClaw starts every time fails; the newest crash file
names where" / "Run personalclaw report and attach it to a bug report", plus the way back when one is
named), recorded like every refusal, so the readers say it and a service stops at its third. `--recovery`,
typed by the owner, still starts; five quiet minutes empty the count.

| From | To | When |
|---|---|---|
| (none) | refused | a start is refused: the record is written, `attempts` 1 |
| refused | refused | the next start is refused too: `attempts` + 1, the newest `code` and `boot_id` |
| refused | (none) | a start serves: the record is removed |
| any | unchanged | a start finds the home served: it exits 3 and writes nothing |

### 16.4 What these must not do

Status: planned

The incident record is never sent anywhere and never replays dropped work on its own; the withdrawal
marker never deletes a tag or an asset and needs no server of its own; the automatic return never runs on
a release the owner installed by hand, nor more than once per release; the boot-refusal record never
holds a value, only names, and never stops a start by itself.

---

## 17. Other shared concepts

### 17.1 The owner's sealed yes

Status: planned

- `folder_skills` (§15.3): a folder's skill, sealed to its content digest.
- `folder_instructions`: a folder's instruction file, keyed `<folder_key>/<relative path>`,
  sealed to the file's SHA-256, with the allowed copy kept by its digest under `grants/folder_instructions/`
  so a change shows its diff; a changed file asks again.
- `automation_rules` (§9.10): an automation's standing allows and their allow counts, sealed
  to the automation's behaviour digest, so any revision drops them.
- `installers` (§16.2): the owner's yes to the `uv` that installs updates, keyed by its absolute
  real path, sealed to its SHA-256; the updater runs only that path, never the word `uv`.
- `secret_hosts.json` (beside the books under `grants/`, read and written only by
  `secret_hosts.py`): the hosts each secret is for (`{version: 1, bindings: {"<scope>:<name>": {hosts,
  set_at}}}`, at most ten bare hosts); an unreadable file binds every secret to nothing, so a command
  carrying a filled secret is refused at the proxy.
- `mcp_app_views`: a connected server's view and its pins. A view's yes, keyed
  `<server>|<resource URI>`, is sealed to its reach, `canonical_digest({definition:
  mcp_grants.definition(server), resource_uri, connect, resources})` (the declared domains), not to the
  view's bytes: a change to the server's definition, the URI or the domains asks again, a change of bytes
  with the same reach does not, because a sealed view that reaches nothing can do nothing a byte change
  makes worse. A pin's yes, keyed `pin:<pin_id>`, is sealed to its launch, `canonical_digest({definition,
  tool, resource_uri, arguments: canonical_digest(arguments), effect})`; any change stops the pin until it
  is pinned again. A view's change to anything needs a click in it and asks the owner every time, alone.

The per-tool seal of a server's read-only labels was built ahead of this release with records of its own under
`grants/` (`mcp_read_only.json`, `mcp_tool_descriptions.json`, one reader,
`mcp_read_only_trust.believes`, landed); this release adopts it as built, its records keep the sealed
text of each tool so a Review shows what changed, and its seal stays exact. A book never holds a secret's
value or a token, and no agent-reachable path writes one.

### 17.2 Doctor rows

Status: planned

A Doctor row is one shape for the gateway Doctor and the CLI, produced by one function,
`resilience/doctor.row(probe, result) -> DoctorRow`:

| Field | Type | Meaning |
|---|---|---|
| `check` | str | the probe's stable id |
| `group` | str | the probe's capability, the heading it prints under |
| `label` | str | the probe's title |
| `tier` | int 0-3 | `resilience/doctor.Tier` |
| `state` | `ok` \| `warn` \| `fail` | `ok`; `warn` when a capability-tier probe failed; `fail` when a core-tier probe failed |
| `detail` | str | one redacted line |
| `fix_id` | str \| null | a registered, confirm-gated fix |
| `remedy` | str | the sentence for a failure no fix repairs |
| `evidence` | object | structured specifics, redacted |

The rows come from `ProbeResult` (`resilience/doctor.py:73-100`); the gateway Doctor's old row fields
`id`, `capability`, `title` and `ok` are deleted, and its report keeps its grouping
(`capabilities: {group: {state, tier, probes: [DoctorRow]}}`), `core_ok` and `restart_suggested`. The
CLI's checks become probes run without a gateway. `personalclaw doctor --json` prints one document,
`{"v": 1, "ok": bool, "generated_at": instant, "version": VersionIdentity, "rows": [DoctorRow]}` (`ok` is
no row `fail`; `version` is §17.5's identity), schema `src/personalclaw/doctor_rows.schema.json`, and
exits 0 with no `fail`, 1 with one, 2 when the doctor itself could not run; the printed form is rendered
from the same rows. Every new check a design adds is a probe returning `ProbeResult`, never a new row
shape. A probe never fixes anything unasked: a fix is a registered, confirm-gated `fix_id`
(`personalclaw doctor --fix <id>`), and the doctor itself writes nothing.

### 17.3 Scripted faults

Status: planned

The scripted provider (`llm/scripted.py`, schema v1) gains optional keys and stays schema v1:

| Level | Key | Type | Bound | Meaning |
|---|---|---|---|---|
| script | `about` | str | ≤ 200 chars | what the script is for |
| script | `witness_needles` | [str] | ≤ 20, each 1-100 chars | strings whose presence in what the model received the witness records |
| turn | `chunk_delay_ms` | int | 0-5000 | a pause before every thinking and text chunk after the first |
| turn | `fault` | object with exactly one key | below | |
| turn | `when_prompt` | str | 1-200 chars | the turn serves a call whose prompt (the last user message as the provider receives it) contains this text |
| turn | `times` | int | ≥ 1, with `when_prompt` only | how many calls the turn serves; default unlimited |

The first turn with a `when_prompt` the prompt contains and `times` not used up serves the call; otherwise
the next turn without `when_prompt` by the counter, under `on_exhausted` as today; a call no turn serves
raises `ScriptedScriptError`. Every model call consumes a turn, a retry included.

| `fault` | Emits, then raises |
|---|---|
| `{"before_output": "rate_limited" \| "out_of_credit" \| "access_denied" \| "auth_failed" \| "unknown_model" \| "unrecognized"}` | nothing, then `ScriptedProviderFault` (a plain `RuntimeError` read by its words, as a provider's status error is) with the matching status words |
| `{"transport": "connection_refused" \| "connection_reset" \| "timeout"}` | nothing, then the builtin `ConnectionRefusedError`, `ConnectionResetError` or `TimeoutError` |
| `{"cut_after_chunks": N}` | thinking, then N text chunks, then `ConnectionResetError`: visible text, so no retry and no failover |
| `{"stall_ms": N}` (0-120000) | nothing for N ms, then the turn as scripted (a first-token budget may fire) |
| `{"clean_eof": true}` | its thinking and chunks, never a call, then the stream ends with no terminal event, and the model-call guard raises the landed `AnswerCutOff` |

A `before_output` or `transport` turn declares nothing else; `cut_after_chunks` needs more than N chunks;
`clean_eof` refuses `tool_calls`, `stop_reason` and `usage` (every real adapter now emits a call only after
its answer's terminal event, so a fault that emitted calls first would drive a path no provider reaches);
any other mix is a load error. A stop cancels a stall or a
pause at once. Builtin exceptions keep the module free of any HTTP client.

**One transport reading.** `llm_helpers.transport_failure(exc) -> "connect_timeout" | "timeout" |
"refused" | "lost" | None` is the one reading of a transport failure by type; the chat's sentence and the
native runtime's failure mode both read it, so a timeout from any adapter is a timeout. A contract test
compares each fault kind with the core HTTP model adapter's exception for the same condition: the same
sentence, failure clause, failure mode and failover eligibility.

**The witness.** `PERSONALCLAW_SCRIPTED_WITNESS=<path>` (opt-in; a world sets it) makes the provider
append one line per model call when the call ends (`0600`, append-only): `{v: 1, n, turn, at, via: stream
| complete, model, effort, roles, tools (the names offered, sorted), prompt_digest (§2.4's compact
digest of the messages as received), prompt_head (the last user message's first 200 characters, masked),
needles: {needle: bool}, fault, cancelled, image_at}`, `image_at` being the instant the provider's process
image started, the same on every line one image writes, so a reader picks out what the model was offered
after a restart (a restart execs a new image in the same process). It holds no credential and never the
whole prompt;
`llm.scripted.read_witness(path)` is its one parser. A path it cannot open refuses construction. Faults
exist only in the scripted provider; no real provider gains a fault switch.

### 17.4 Usage and cost attribution

Status: planned

`Usage`, a message's usage, is the per-turn subset of `usage_ledger.TurnUsage` (`usage_ledger.py:42-80`):
`provider`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`,
`audio_input_tokens`, `audio_output_tokens`, `cost_usd`, `priced`, `local`, `duration_ms`.

- **Prices.** `routing/rates.ModelRate` gains `audio_in_per_mtok` and `audio_out_per_mtok` (optional, USD
  per million audio tokens; a call with audio tokens in a bucket its rate names no price for is unpriced,
  never priced at a text rate) and `above: {prompt_tokens (≥ 1,000), in_per_mtok, out_per_mtok,
  cache_read_per_mtok, cache_write_per_mtok} | null`, read from every tier of the price table (the shipped
  table, the owner's overrides, an app's declaration): `price_call` prices the whole call at `above` when
  its prompt reaches `prompt_tokens`, and `CallPrice` gains `tier: base | above`.
- **What a row names.** Usage rows gain `skills` (the skills the call's turn used, names only, at most 8),
  `tool_source` (`mcp:<server>` or the app whose tool the call served), `root` (the turn at the top of its
  lineage, §3.3's `facts.root`) and `loop` (on a goal cycle's turn and its judge's). The rollup groups by skill, tool source, automation and loop beside today's keys, and
  the span tree (`GET …/turns/{message_id}/trace`; `sdk.traces.span_tree`, named feature
  `turn-spans`, app permission `turnSpans`, platform event `turn.finished`: ids, names, times, counts, cost
  and `priced`, never content) reads the rows by `root`. A trace exporter is a first-party app, off by
  default, sending only those fields to a collector the owner names.

An automation's run record (`ScheduleRun`) gains `usage`, the folded ledger aggregate, not the per-turn
`Usage`: `usage_ledger.totals`' shape (`input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`, `cost_usd`, `turns`, `priced`, `units`; `usage_ledger.py:505`)
plus `local` (every row ran on this machine), written once when the run's work ends, `null` for a run
refused before it ran. Usage rows (`TurnUsage`) and `Attribution` (`source`, `session_key`, `agent`) gain
`automation` (the trigger id) and `automation_run` (its run id, §2.2), beside #3865's app column, in one
rollup (`rollup`, `:458`, `group_by="automation"`; `totals(automation=, automation_run=)`), carried to the
agents, helpers and workflow steps a run starts. `ScheduleRun` also gains `retried_after_acknowledgement`
(its agent only acknowledged the task, so it was asked once to finish it) and `redispatch_of` (the run a
restart dispatched again, §5.9). No second ledger and no estimate shown as a charge: an unpriced turn says
so (`priced: false`), a local model is a known $0, and a run with no recorded calls shows no cost.

### 17.5 Version identity

Status: planned

`{version, commit, dirty, kind}`, where `kind` is `self_update.InstallKind` (`git`, `pip`, `container`,
`desktop`; `self_update.py:81-110`) and `version` comes from the release tag through the build-time
version tool (a checkout reads `0.2.2.dev47+g1a2b3c4`; a checkout reads its own repository at run time,
because an editable install's metadata goes stale). One function, `version_identity()`, computed once per
process, serves `personalclaw --version`, `personalclaw.__version__` (lazily, through a module
`__getattr__`, so importing the package runs no git), `/api/status` (`version_identity`), `status`'s
heading, `doctor --json`'s `version`, the support report, evidence records, and crash artifacts, whose
`version` becomes the identity object (a clean break its readers follow). It builds on the landed reading
of every version one way (`versions.py`, `same_version`). It never reaches the network.

### 17.6 UI conventions every design follows

Status: planned

- Every new action is a command-registry row (#3819), never a key handler of its own.
- Times, sizes, counts and money are printed by one format module.
- A reversible action is undone, not confirmed; an irreversible one confirms with what it cannot undo.
- A value that may be stale says "As of 10:42".

### 17.7 Review findings and review comments

Status: planned

- **`Finding`** (`personalclaw.sdk.review.Finding`, frozen), the one shape every reviewer PersonalClaw runs
  and every app that reviews code records: `id` (`fnd_<ULID>`, minted by
  `personalclaw.sdk.review.new_finding_id()`), `title` (1-120 characters, masked), `body` (0-1,200,
  masked: why it is a defect and what would fix it), `priority` (`p0` | `p1` | `p2` | `p3`, each with fixed
  words), `confidence` (`high` | `medium` | `low`: in words, never a number a model cannot calibrate),
  `file` (repository-relative, POSIX separators), `lines` (`{side: new | old, start, end}` | null: 1-based,
  at most ten lines, `old` only for lines the change deleted, null for the whole file), `rule` (`{file,
  line, text}` | null: the repository rule that supports it, a line of its review or agent instructions
  read by the checker, at most 300 characters), `pre_existing` (bool, computed, never claimed), `source`
  (`static` | `model` | `helper`), `subject` (`{head, base, tree}`) and `state` (`open` | `dismissed` |
  `reported_again` | `changed_not_reported` | `not_reported` | `gone`).
- **`Verdict`**, computed, never reported: `blocking` when any open finding is P0, `needs_changes` when any is
  P1 or P2, `looks_good` otherwise ("Blocking", "Needs changes", "Looks good"). A reviewer's `summary` (at
  most 600 characters, masked) rides beside it and decides nothing.
- **`Comment`**, the owner's own comment on a diff, a plan or a page: `target` (`{kind: diff, file, side,
  start, end, base}` | `{kind: plan, part_id, entry}` | `{kind: page, preview, pin}`), `body` (1-4,000, the owner's
  words) and `quote` (0-2,000, the anchored text, filled by the gateway from the anchor, never by the page).
  If #3821 ships its own comment shape first, the comments change makes it this one in the same
  change. Comments and findings reach a chat as §3.9's `comments` and `fix`, every quote through the
  outside-text door.
- **The kit**: `verdict_of(findings)`, `check_finding(finding) -> str` (the shape's own rules) and
  `assert_finding_contract(findings)`, a conformance kit an app's tests run, under the named feature
  `review-findings`.
- **The review record**: `reviews/<safe chat key>/<rv_id>.json` (`{v: 1, id, session, message_id, scope,
  focus, subject, state, verdict, summary, findings: [Finding], coverage, refused, helper, model_ref, usage,
  started_at, ended_at, error}`; §1.4), at most 50 per chat, removed with its chat; the `review` part (§3.3)
  is its compact view. The Code Review app records its findings in this shape, with an idempotent backfill
  of its log.

### 17.8 Placement and sandbox traits

Status: planned

- **`Placement`** (`fence.placement(session_key, *, runtime, call=None)`, computed for the call being
  decided and again by the wrap at launch): `runs_in` (`host` | `os_sandbox` | `provider:<name>`), `backend`
  (`sandbox-exec` | `namespace` | `landlock` | `none` | "" inside a provider tier), `fence` (`full` |
  `outside_only` | `none`), `fenced` (`fence == full` and nothing missing), `writes` ([str], the writable
  set's roots), `network` (`all` | `proxy` | `none`), `memory` (`writable` | `read_only` | `hidden`),
  `hides_secrets` (bool), `why_not` (a word of `UNFENCED_WAYS`, "" when nothing is missing) and `sentence`
  (one line for the owner). `Placement.as_fact()` is the posture's placement fact (§8.4).
- **`fence.UNFENCED_WAYS`**, closed, each with its sentence: `no_backend`, `sandbox_off`, `outside_only`,
  `desktop_linux`, `root_refused`, `too_many_repositories`, `held_change_kept`, `gateway_open_locally`,
  `outside_once`, `cli_not_declared`, `cli_self_sandboxing`, `cli_shared_process`, `chat_outside`,
  `tier_declares_less`; a both-ways rail holds the set (`test_unfenced_ways_both_ways`). Only `full` is
  fenced. A machine whose fence is Landlock only (`outside_only`) bounds writes, which is enough for Trust,
  YOLO and unattended runs to stand on, and never enough for a fenced grant.
- **A call runs as placed, or not at all.** `decide` returns `placement_required`: `fenced` when a fenced
  chat grant answered, `bounded` when any other standing answer answered a shell call, "" when the owner
  answered it, when the call is a read the screen vouched for, or when
  `security.shell_without_sandbox` is `allow` and the call is unattended. The gate binds it into the call,
  and `fence.run_command` compares it with the placement it actually applied and, when that is weaker,
  refuses before anything starts: "The sandbox could not hold this command the way the setting that allowed
  it requires, so it was not run." (`refused_by: fence_not_applied`, `ERR_FENCE_NOT_APPLIED`).
- **`SandboxTraits`** (`personalclaw.sdk.sandbox`, named feature `sandbox-traits`, from
  `SandboxProvider.traits()`): `mounts` (`given` | `home` | `all`), `fence` (for the host's files), `network`
  (`proxy` | `off` | `all`), `paths` (`same` | `translated`), `serves_files`, `persistent`, `display` and
  `ports` (`loopback` | `all_interfaces`). A tier that declares none reads `{all, none, all, same, false,
  false, false, all_interfaces}`; an AST rail fails on a core comparison against a tier's name. **The
  container tier's published ports bind `127.0.0.1` only** (`SandboxSpec.expose_ports`), in the
  change that adds the traits; the core `docker` tier then declares `{given, full, proxy, same, false,
  false, false, loopback}`. A chat's sandbox tier (§13.2) depends on that change and adds no port step of its own.
- **A worktree chat's writable set**: the worktree, the extra roots and, of the
  repository's git folder, only `objects/`, `refs/`, `logs/`, `packed-refs` and this worktree's
  administration folder, never `config`, `config.worktree`, `commondir`, `gitdir` or `hooks/`. A fenced
  command commits on its branch and reaches neither the owner's folder nor a setting; unlike a folder chat,
  whose git folder sits inside its workspace, the fence can tell these apart here.
- **The persistence table**: `persistence_paths.ROWS`, one closed table beside `owner_only` of
  the paths that plant code the owner's tools run later (hook managers' files, `.envrc`, package-manager rc
  files, editor task and launch files, `.mcp.json`, `.agents/`, and each folder an app declares through the
  named feature `persistence-paths`), each with the words of what it plants. A write to one asks alone
  (§8.3 step 10, `persistence_path`), and the fence's held set reads the same table. Whichever of the
  fence's workspace profile and the persistence-path ask lands first creates it, and the other reads
  it.
- **Agent CLIs and the agent's computer.** An agent CLI's app declares where its CLI writes
  (`FenceDeclaration`, named feature `fenced-agent-cli`); the agent's computer is a `ComputerProvider`
  (named feature `agent-computer`), and its managed browser keys an unattended browse run's profile by
  `(ActionContext.holder, site)`: a kept agent's run gets a profile of its own per site, and a run with no
  holder keeps the per-site profile.

### 17.9 Held memory changes and the owner's door

Status: planned

- **A held memory change** is a learning proposal of kind `memory_hold`, its `payload` a `MemoryHold`: `op`
  (`semantic` | `document` | `forget`), `key`, `kind` (`lesson` | `fact` | `preference` | `slot` |
  `persona` | `document`), `text` (what would be kept or removed, masked, as the card shows it), `write`
  (the arguments the write was given, or a document's new text), `base` (`{digest, text}` | null),
  `retiring` (`[{key, digest}]`: each lesson the write would retire, as it was), `writer` (§17.10),
  `partition`, `why` (`review`: `learning.memory_review` is `ask`; `untrusted`: the hold for a turn
  that read untrusted text) and `chat` (`{key, title}` | null). The turn's `facts.learned` item for it has
  `state: waiting` and the proposal's id as `ref` (§3.3).
- **Keep** (the owner's accept): the installer re-reads `base` and every `retiring` row and refuses with `409
  memory_hold_out_of_date` on any change, the proposal staying pending; otherwise it applies the write inside
  `memory_writes.at_the_owners_door()`, notes the recorded writer and `owner`/`kept`, and writes
  `memory.hold_kept` (the kind and the writer, never the text). **Don't keep** writes nothing; the learning
  queue's decision memory keeps its fingerprint.
- **The owner's door.** `memory_writes.at_the_owners_door()` is a closed list, held by a census rail: the
  lessons, facts, facets, slots, preferences and projects routes when the request is the owner's own, the
  CLI's `memory` and `learn` commands, the vault's two-way edit, the decision journal's resolve, the
  composer's `/remember`, and the installers that apply the owner's Keep, Replace and Yes. Inside it nothing is held.
- **Not a waiting item.** A held change is never an item of the waiting list (§11.2): it blocks no work, and
  nothing it holds expires on a timer. The Inbox groups its rows by chat. At most 300 wait; past that the
  oldest is dropped unkept, and the count is said.
- **The learning proposal record** gains `payload` (an object each kind defines) and two kinds, each with
  its Inbox label and installer: `memory_hold`; and `follow_up`, an inferred follow-up (its Yes
  creates an ordinary one-time reminder through the triggers' tools as the owner's, `by:
  owner`, `ref: dashboard` (§2.5), and nothing is sent before that Yes; No dismisses it). The heartbeat's
  unasked delivery of inferred follow-ups goes. A learned lesson that would replace one the owner taught is the
  landed `lesson_conflict` question (§17.10), no kind of its own.

### 17.10 Lessons, the write's answer, and who wrote a memory

Status: planned

- **The tier.** A lesson carries `applies: always | on_topic` (authored; unstated reads `always`): "Every
  reply" or "When it comes up". Standing lessons ride a reserved share of the turn's ambient budget, at most
  20 a memory (`409 lesson_standing_full`); an on-topic lesson reaches a turn only when the turn's own words
  share a word with it. `PATCH /api/lessons/{key} {applies}` switches it (owner only; `404
  lesson_not_found`).
- **`LessonWrite`**, the answer of every lesson write (re-exported by the SDK's `MemoryService.write_lesson`):
  `outcome` (`stored` | `sighting` | `refused` | `held`), `key`, `applies`, `code`, `reason`, `retired`
  (`[{key, rule, event_id}]`, each undone by its history event) and `kept` (`[{key, rule}]`: lessons it
  would have retired and kept, because the owner taught them). A `held` write waits for the owner's word: `code`
  `review` (§17.9), `someone_else` (a turn someone else asked for), or `owner_lesson` (the landed rule kept it
  out because it would replace a lesson the owner taught; `key` is the question's id). `lesson_refusal` goes,
  its callers (the landed question's Accept among them) reading the answer instead.
- **Only the owner retires their own** (landed). A lesson the owner taught is replaced, retired or
  rewritten by them alone (`vector_memory.only_a_person_replaces`, asked by conflict resolution, every
  retirement, the lesson dedup and consolidation's formation). When a learned lesson would replace one of
  theirs, the store keeps theirs and nothing of the learned one, and the owner is asked which to keep:
  `learning.lesson_conflicts.ask` files one `lesson_batch` proposal tagged `lesson_conflict`, on the Learning
  page and in the Inbox, naming both; Accept keeps the learned lesson as theirs in their lesson's place
  (`install_accepted`, undoable in Memory › Audit), and Reject keeps theirs, never asked again. This release
  builds on that question rather than adding a kind: its Accept re-reads both lessons and changes nothing when
  either changed since, and the contradiction judge, which never counts against the owner's lessons, retires only
  an on-topic lesson that is not theirs and asks the same way about a standing one.
- **Who wrote a memory record.** `memory_sources.WRITERS`, closed: `session:<key>` (the work of a session,
  filed under the chat at the top of the chain it works for), `app:<name>`, `owner`, `maintenance`,
  `import`, `agent:<ka_id>` (a kept agent's own work), and `several` and `unknown` (backfill
  only); `how`, closed: `wrote`, `confirmed`, `edited`, `kept`, `copied` ("learned by a kept agent and
  copied to your memory"). Every `memory.db` keeps `memory_sources (record_kind, record_ref, writer, how,
  first_at, last_at, times)`, written only by `memory_sources.py`; `source_session` is removed from
  `memory.db`. The landed fix that forgets what memory drew from a deleted chat alone reads it.
- **A kept agent's memory** is its own partition (`agent_partition`, §13.2); `/remember` in a kept agent's
  thread writes to that partition; the learned route (`GET /api/memory/learned`) lists agent partitions,
  and its item's `memory` gains `agent`.
- **Keys** (§6.7): each memory tool takes a key and reads the bound one.

### 17.11 Model facts: a vendor that may train on what is sent

Status: planned

- `llm/model_facts.trains_on_inputs(ref) -> TrainingFact {value: true | false | null, source: this_machine
  | app | ""}`, the one reader, which never raises: `false` when the model runs on this machine; otherwise
  what the entry's type declares (`BrandedProviderSpec.trains_on_inputs: {model pattern: bool}`, matched by
  the price table's matcher, or `ProviderRegistry.register_type(..., trains_on_inputs=)`, under the named
  feature `model-training-facts`); otherwise `null`, never read as `false`.
- The model routes (`GET /api/models/available`, `GET /api/models/chat`, each binding of `GET
  /api/models/active`) carry `trains_on_inputs`; Settings › Models and the model picker show "May train on
  what you send" on a `true` model, and the detail line says which of the four readings holds.
- **Never chosen on its own.** `model_facts.may_choose_unbound(ref)` is false for a `true` model, so the
  router ranks it after every other and the walk for a use case bound to nothing passes over it; a model the owner
  bound is their choice.
- **Refused in a private chat.** A turn of an Incognito or Temporary chat whose model reads `true` ends
  before anything is sent, with `Message.error.code: private_chat_model_trains`; `POST
  /api/chat/sessions/{session}/model` refuses it for such a chat with `409 private_chat_model_trains`.

### 17.12 The stable prompt prefix and the cache hint

Status: planned

- **Rewrite points.** A native runtime may rewrite the front of its prompt only at its first turn (or its
  first after a restore), a compaction applied, a model change (a fallback, a return after one, a rebind, a
  change applied at a turn's next step), a surface change (an install, a removal, a switch-off, a group
  activation, what the run is shown) and a change of the window, each advancing the cache generation.
  Between them its tool block and stable note are byte-identical, and nothing enters its history that a
  later turn removes or rewrites.
- **Per-turn context rides the tail.** What is true for this turn only (where the file tools reach now, how
  the owner's automations stand now, a group change's announcement, the turn's lessons and rules) is laid on
  every request of the turn as one volatile system message at the tail, or rides that turn's own message,
  never earlier history. Standing lessons render in a fixed order, so their bytes stay the same from turn to
  turn.
- **A history names only declared tools**: a call by a name the request did not declare is written as the
  same call through `tool_run` (§7.6).
- **`PromptCacheHint {generation, span: head | tail, pacing: person | machine}`** (`llm/prompt_cache.py`,
  exported by `personalclaw.sdk.model`), at most two on a request; pacing is read from the runtime's
  posture, never from text, and an adapter maps it to a provider's cache tier.

### 17.13 A skill's visibility and who may start it

Status: planned

- Front matter gains `invocation: owner | agent | both` (default `both`; the interop keys that mean "only
  the owner" map to `owner`). The Skills page overrides it per skill in the stored words `offered |
  name_only | owner | off` (Offered; Name only: an agent may start it by name, and it is never surfaced by a
  match; Owner only; Off), kept in `entity_settings/skill_invocation.json` (`{v: 1, skills: {<name>:
  offered | name_only | owner | off}}`, one writer, `skills/invocation.py`).
- The loader reads it wherever skills reach a turn: surfacing and the skill index leave out owner-only and
  switched-off skills, and name-only ones from matching (a name-only skill is indexed by its name only);
  `skill_invoke` refuses an owner-only skill ("This skill is started by its owner."); automations and loops
  reach only `agent` and `both` skills; a skill installed from a learning proposal starts owner-only until
  the owner's review says otherwise.
- The owner starts one from "/": the command registry (§17.14) lists the owner-startable skills after the
  commands.

### 17.14 The chat-command registry

Status: planned

- **One registry** (`chat_commands.py`) for every surface. A row: `name` (`^[a-z][a-z0-9-]{0,31}$`),
  `summary` (≤ 80 characters), `args`, `who` (`owner` | `paired` | `anyone`, decided from the door's verdict:
  the channel's paired owner, which the landed fix keeps to the owner its pairing named; a paired or allowed
  sender; anyone the door admitted), `while_running` (`run` | `refuse`), `surfaces` ({`channel`,
  `dashboard`}), `where` (`any` | `dm` | `group`), `widens_trust` (owner only, timed, audited), `audited`
  and `ui` (a dashboard-only row's composer action). `register(row, handler)`, and a name registered twice
  raises at start; a channel app registers rows for its own channel through
  `personalclaw.sdk.channel.register_command` (named feature `channel-commands`). `handler(ctx:
  CommandContext) -> CommandAnswer`, `CommandContext {surface, channel, address, chat, sender, who, args,
  running}`, `CommandAnswer {text, outcome: done | refused, turn_message_id}`.
- **Parsing.** Only the first token counts: the channel's `command_prefix`, a name, an optional
  `@<handle>`, then the arguments; a first token that is not a prefix and a name is text; in a group chat a
  command counts only when addressed to the agent. An unknown name runs nothing, and a command never runs
  late.
- **Core's rows**: `help`, `whoami`, `status`, `stop`, `title`, `run`, `continue`, `new`, `compact`,
  `model`, `agent`, `approve` and `deny` (Allow once and Deny, through the waiting item's own route; never a
  host card), `yolo` (30 minutes, refused above the operator ceiling), `listen`, `track`, `dashboard`,
  `automations` and `helpers`; the dashboard-only rows `optimize`, `prompts`, `effort`, `project`, `tools`,
  `undo` and `rewind-to-turn`. A row reaches only the chat of the address it came on: no row opens, joins
  or messages another chat (that is later work, #3837).
- **Routes**: `GET /api/chat/commands?surface=dashboard|channel&channel=<name>` → `{items: [{name, summary,
  args, who, while_running, where, ui}], next_cursor: null}` (`400 bad_request`); `POST
  /api/chat/sessions/{session}/commands {name, args}` → `{outcome: "done", text, session, turn_message_id}`
  (`404 session_not_found`, `404 command_not_found`, `403 command_refused`, `409 command_busy`).
- **Native menus**: `ChannelDelivery.register_commands(specs)` maps the rows offered on a channel to its
  platform's command menu; a press reaches the door as `/<name> <args>` with `mentioned`.
- `channel.command_ran` and `channel.command_refused` (`{channel, row, who, outcome}`, never arguments) for
  the audited rows and every refusal of an owner row.

### 17.15 One scheduler for every timed wake (decision 44)

Status: planned

- `scheduler.Scheduler`, one per gateway, started first and stopped last: `schedule(job, *, run,
  at_wall=None, after_elapsed=None, mode: replace | earliest, label)`, `cancel`, `jobs`, `begin_close` and
  `stop`. A job is due at the earlier of its wall deadline and its elapsed deadline, so a sleep and a
  backward clock correction are both noticed within a minute; late wakes coalesce into one run, told
  `coalesced` and `late_secs`; callbacks never run on the scheduler's task, and a job whose previous run is
  still going is skipped, counted.
- **Every timed wake is a job.** One change moves onto it the trigger clock (its tick still drives
  the idle poll and a goal loop's idle tick), the reaper, the task-due sweep, the file and web watches, the
  heartbeat and the three watchdogs; the live tiles and the away check are jobs from the start. Every timed
  wake another change adds is a job from its first commit: a goal loop's cadence, a timed Continue (§5.5),
  the worker conversations' daily prune, keep-awake, dependency waits, a helper's liveness check, the work
  queue's daily prune and re-dispatch backoff, a background command's end, the helper reaper's sweep, the
  config watcher, the healthy mark and a channel's timed sends.
  `tests/test_every_timed_wake_is_a_scheduler_job.py` lists every sleeping loop in `src/` and fails on one
  outside a closed allowlist, each entry with its reason (a per-request poll that ends with its request, a
  browse step's settle, a connection's own keep-alive, the stalled-loop watchdog's thread, §16.2, and a loop
  that exists today and that another change of this release moves, until that change lands); the list may only
  shrink. Apps keep their timers in their own processes.
- `GET /api/system/schedule` (owner only) lists the jobs (`{job, label, next_at, last_at, last_ms,
  late_by_ms, running, skipped}`), and the Doctor's "Timed work" probe warns on a job late by more than twice
  its period.

### 17.16 One provider-failure record

Status: planned

- `sdk.model.ProviderFailure` (named feature `provider-failures`), raised by a model app from its own vendor
  knowledge: `reason` (closed: `auth`, `permission`, `billing`, `rate_limit`, `overloaded`,
  `context_overflow`, `model_not_found`, `bad_request`, `content_filter`, `server_error`, `timeout`,
  `connection`, `cut_off`, `unknown`), `retry_after` (seconds, only when the provider said), the hints
  `retry_same`, `move_on`, `shrink_context` and `wait_for_owner`, and `sentence`, `provider`, `model_ref` and
  `status`. `provider_failure.of(exc)` is the one reading: core's own model clients and the landed
  `AnswerCutOff` (read as `cut_off`) map their typed exceptions, and words survive only in
  `provider_failure.from_text`.
- Every consumer reads it: the in-turn recovery, the chain walk, workflows' failure class (one table from
  `reason`), autopause, the learning detectors, the chat's words (`sentence` first), `run_state.retry.reason`
  (§4.3), `Message.error.reason` and `retry_at` (§3.3), and the breaker, which cools by reason: a rate limit
  or an overload per provider entry and model until its `retry_at`, which the record gives; `auth` and
  `billing` hold the entry with words. A cooldown reads `exhausted` on the `model:` subject until it ends
  (§7.4). Dependency waits read the record's `retry_at` and keep no second schedule.
- A conformance kit (`personalclaw.sdk.model.assert_provider_failures`) replays each first-party model app's
  recorded vendor errors.

