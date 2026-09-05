# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Fixed
- **Set default**
- A lifecycle trigger's **Test** button is now a rehearsal, not a fire: it no longer writes into the trigger's real `run_count`/`last run`/`last status` (“Ran 2× · ok” could previously describe a trigger that had never actually fired — both runs were Test clicks), and the action's payload is tagged the way the event-trigger test path already tags it, so a provider can tell a rehearsal from the real thing.
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart.
- **A refused inbox-settings write now rolls back instead of keeping the rejected value on screen: Retention accepted `-5`/`0`/`99999`, and even once the failure notification landed, the optimistic merge stood — the field showed the refused value as saved until a reload silently restored the stored one.**
- The app **Configure** dialog now reads its schema's `required` array.
- Config validation now enforces the constraint keywords a manifest declares, through **one** validator instead of two that had drifted.
- **Manually reclassifying an inbox item now marks the verdict as the user's own ("Set by you") instead of keeping the AI's confidence in the overridden verdict, and the classification feedback thumbs hide once no machine judgment is on display — Mark wrong can no longer file training feedback against a classification the model never produced.**
- One-line previews are plain text: inbox rows strip markdown marks from both the painted preview and the announced name (a digest's ** and ## no longer render literally or get read aloud), and the artifact card's clipped excerpt is aria-hidden — a decorative thumbnail no longer walks 600 chars of raw markdown into the accessible tree.
- **The week grid and the server now agree on one projection window: the grid sends its exact local-week end (7 local calendar days = 167h/169h across a DST transition, not a fixed 168h), so a real fire is no longer dropped on spring-forward weeks and no phantom hour is drawn on fall-back weeks; any out-of-window occurrence is disclosed in the caption instead of vanishing.**
- **The Files page keeps unsaved edits across a rename: it now owns the draft cache FileViewer documents, the cache entry moves with the file (no more "Rename and discard" consent — nothing is discarded), a confirmed close purges the draft so a discarded edit cannot resurrect on reopen, and the Code cockpit's two programmatic close paths (workspace switch, worker delete) purge theirs too.**
- The **Speaking speed** slider (Settings → Speech & Transcription) no longer tells every provider the same story: its Fast/Slow ends and "lower is faster" hint were Piper's `--length-scale` semantics, which are exactly backwards for OpenAI-compatible remote voices (the same raw number is the API's multiplier, where higher is faster) — so dragging toward "Fast" made remote speech slower.
- **The app enable/disable toggle now reads Activate/Deactivate on every surface (card, menus, detail panel) — the detail panel's old "Install" implied a re-download that never happens, since a deactivated app's files stay on disk; "Install" is reserved for real store downloads.**
- **Deleting a single notification now confirms first: the per-row Delete removed the entry from disk on one unconfirmed click with no undo, while Clear all on the same page confirms and every sibling per-row delete in the app gates on the shared confirm dialog.**
- **The Settings → Apps tile now counts what its caption says: it read "0 installed apps" on an instance with 33 installed, because the stat deliberately filters to non-provider apps (providers configure under Settings › Providers) but captioned the filtered count with the unqualified noun.**
- **The agent detail panel now names its trigger bindings instead of printing raw hex ids: bound triggers resolve through the same endpoint the picker built its options from and render as "name · event", matching Skills/Tools (whose stored values are already labels).**
- **The trigger list's lifecycle badge now reads each event's real fire path: globally-fired events (like MemoryWrite) no longer wear "dormant" while actually firing, agent-scoped events with no referencing agent say "no agent references this", "dormant" is reserved for events nothing fires, and the create form discloses agent scoping at the point of choice.**
- **The artifact viewer tells the truth about a version that failed to load — a danger banner with Back to current instead of showing CURRENT content under a "historical vN (read-only)" claim with Revert armed for a nonexistent version — and closing version-compare (or the cockpit's diff) no longer throws Monaco's TextModel-disposed error: both DiffEditor sites detach their models through one shared teardown hook.**
- A project's linked **Artifacts** row now actually lists its loops' deliverables: `/api/projects/{id}/linked` filtered artifacts on `project_id`, which the loop-deliverable convention never wrote — those artifacts carry a `loop:<id>` tag instead, so the row was permanently empty on exactly the artifact class a project is guaranteed to produce.
- **Dismiss all now records the same per-item dismiss engagement signal for every item it sweeps, so the strongest topic-rejection gesture trains inbox ranking instead of being discarded (one store write per sweep).**
- **Generate draft no longer runs on inbox items that can never be replied to: `can_reply` gated only the Send button, so on a read-only item the model ran, a full reply persisted, the row gained a `draft` badge — and Send stayed disabled.**
- **Quiet hours no longer accepts a zero-length window: `08:00 → 08:00` saved with a Saved ✓ while the delivery gate documents start == end as never matching — quiet hours read as enabled and suppressed nothing (the opposite of the all-day quiet a user plausibly meant).**
- Project guard refusals now speak the UI's own vocabulary: deleting or renaming a protected project says "the built-in project 'Personal' cannot be deleted" instead of "the **default** project…" — a singular article that was returned for two different projects and clashed with the Built-in badge those rows already carry.
- **Artifact list responses no longer serve a fabricated `live_dirty`: the flag is computed per read by the detail path while list rows come off persisted metadata that never stores it — so the same artifact reported `False` in the list and `True` in the detail, and every consumer had learned to distrust the list copy.**
- **The Agents page's group headers now count what the group shows: every group filters its rows through the search box, and the no-match empty states already say so, but each header's `count` read the unfiltered catalog — so searching for something absent rendered `Native | 8` directly above "No matching agents".**
- **Creating an artifact with an unrecognized `kind` is now a 400 naming the allowed set instead of a silent success that stored the artifact as `widget` — the sandboxed-*execution* kind, so a typo like `"markdwon"` or a plausible `"md"` had its content treated as executable widget payload rather than prose, and lost the comment layer without explanation (sandboxed kinds are not commentable).**
- **A reaped trigger run reads as a failure everywhere it renders: the reaper writes `health_status: degraded` plus the reap reason while the run-store row still says `success`, and both the detail panel's Last-run badge and the list's schedule-row dot read the two fields through a bare `last_run_status || last_status` chain — so the one record where the fields disagree rendered a green "ok · 1d ago" two lines above the red "Reaped after 1818s" banner.**
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart.
- **Reset everything to defaults**
- **`GET /api/artifacts/{slug}/versions/{n}` now self-describes as the version it carries: `native.get()` read the head metadata and swapped only the content, so v1 and v5 both reported `version: 5` next to different bytes — any caller labeling a version from its own payload mislabeled every historical fetch.**
- Prompt render and preview now accept the same variable-value payload: preview took the map under `values` while render/launch/snippet-render demanded `variables`, and each silently ignored the other's key — so render returned a false `missing required variable` for a variable that **was** supplied (pointing the caller at the template instead of the request), and preview returned `ok: true` with unsubstituted braces.
- **The Week grid's empty state no longer blames cron for an empty week: the hint still said "Only enabled interval schedules are plotted.**
- **Discarding a session skill draft or rejecting a skill proposal that no longer exists now returns 404 — matching the sibling skill-delete in the same module — instead of `200 {ok: false}`.**
- **Routing notes can be cleared again: emptying the field and saving PUT an empty `content` that the backend rejected with 400 `content required` — yet an agent with no note is a supported state everywhere else (a missing metadata file reads as `""`), so a note, once set, could never be removed from the UI. An empty (or whitespace-only) PUT now clears the stored note — the file is removed so the canonical empty representation stays "absent" — and clearing an agent that has no note is an idempotent 200.**
- An existing tool-output projection rule can be edited normally again: the rule editor saved the whole list on **every keystroke** and the follow-up refresh re-seeded the input from server state, so keystrokes typed during the round-trip were silently discarded (measured: one of five survived) — a regex could not be corrected at all except by delete-and-re-add.
- The prompt **Tags** field in the edit form can hold more than one tag again: it was a raw input whose value was re-parsed on every keystroke (`split(',')` → trim → `filter(Boolean)` → `join`), so the comma was eaten the moment it was typed and `red,green` became the single tag `redgreen` — while the placeholder instructed "comma-separated".
- Signing in with a **correct** password from a LAN/tunnel address no longer reports "Wrong username or password." Three layers independently converted a CSRF origin rejection into a credentials error: the origin branch in the auth routes returned `auth_invalid_credentials`, the server-level CSRF middleware answered in plain text (which the login page parsed as `{}`), and the page JS defaulted every unmodelled error to the credentials message.
- The agent edit form's **Name** field no longer silently swallows typing: the lock was enforced by discarding writes inside `onChange`, so the field looked pixel-identical to the editable Description below it — focusable, no disabled/readOnly/aria cue — and threw keystrokes away.
- **The dashboard's Action Center and the HeroPulse "inbox" pill no longer double-count skill proposals: most pending inbox items are proposal *mirrors* (`item_kind: proposal`, `refs.skill_proposal`) of the same proposals the triage queue lists directly with Accept/Reject, so every open proposal rendered twice, "+N more to triage" over-reported ~2×, and the pill badged "31 inbox" for one real message.**
- **`POST /api/artifacts` no longer reports a larger body than it saved: the store persists a text body sliced to `MAX_CONTENT_BYTES` (1 MiB) but `create()` echoed the full un-sliced input in its 201 response, so a >1 MiB save looked successful at full size while a subsequent `GET` returned only the first MiB. `create()` now returns the persisted (capped) body — matching what `update()`/`get()` already return by re-reading from disk — so the create response and the next read agree ([#781](https://github.com/PersonalClaw/PersonalClaw/issues/781)).**
- Setting the backend log level from **Settings → Agent defaults** now takes effect immediately instead of only at the next restart.

- **Artifact history now records exactly the real changes.**
- **Context compaction no longer silently disables itself on providers that report no token usage — the normal local-model configuration, where an OpenAI-compatible endpoint rejects `stream_options` and the usage chunk never arrives.**
- **Task lists now reject a duplicate name within one project, matching how projects reject duplicate project names: `create_task_list` had no uniqueness check, so a project could hold two lists of the same name — including two "General" lists, which made a `project_id`-only task attach to whichever id sorted first, arbitrarily.**

- **The backend `skipped` task status now has a frontend representation: `TaskStatus` and the `STATUSES` vocabulary omitted it, so a skipped task matched no Kanban column (invisible on the board), rendered with the not-started glyph in the list, and sorted among active work.**
- **Saving a session template with `reasoning_effort: "max"` is no longer rejected: the template validator's `_VALID_EFFORTS` allowlist omitted `max`, while the composer offers it, the native runtime accepts it, and the OpenAI adapter maps it to `high` — so the one value that could not be stored as a template default was one every other layer honours.**
- **A tagged local-model id (`family:tag`, the normal Ollama form — `llama3.1:8b`, `qwen2.5:0.5b-instruct-q4_0`) now resolves to its FAMILY's context window instead of the 200k default: `model_context_window` split the id and kept the tail (right for a `Provider:` qualifier, wrong for `family:tag` where the family is the head), so the adaptive memory-injection budget scaled a local model's prompt to a window 1.5–6x larger than it actually has.**
- **A schedule the trigger store kept despite a malformed field (lenient load) now flags "needs attention" on Settings → Triggers instead of listing as a healthy-looking row: the backend already carried the parse errors as `broken`, but the `ScheduleJob` type omitted the field, `scheduleToTrigger` dropped it, and the indicator was gated to `kind === 'store'` — so a schedule that silently could not fire gave the user no signal.**
- **The chat approval wait no longer raises `UnboundLocalError` in its `finally` when the wait is cancelled (gateway shutdown, client disconnect, navigation away, or a CI timeout): `outcome` is now bound before the try, so the cancellation propagates unmasked and the mirrored inbox approval item is resolved instead of left asking for a decision the turn is already tearing down ([#1536](https://github.com/PersonalClaw/PersonalClaw/issues/1536)).**
- **Dashboard failure feedback: Action Center approve/reject/accept/dismiss now report a failed action with the server's own message instead of an empty catch that made a 409 look like a dead button ([#324](https://github.com/PersonalClaw/PersonalClaw/issues/324)); bulk task Complete/Delete reads the per-item outcomes from the 200 body and names refused items instead of reporting success ([#478](https://github.com/PersonalClaw/PersonalClaw/issues/478)); the chat workflow progress card only collapses on a real 404 — transient fetch failures keep the card and offer Try again instead of silently erasing it ([#549](https://github.com/PersonalClaw/PersonalClaw/issues/549)).**
- **Task/project integrity: completing a task now refuses while a BLOCKS prerequisite is still open (the DONE write enforced only the task's own exit criteria, so a kanban drag could complete a task with an unfinished prerequisite, strand a `blocked_reason_kind="auto"` stamp on the done row, and inflate graph completion — [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)); and deleting a project now cascades its tasks instead of orphaning them pointing at dead task-list ids, unreachable from every scoped view ([#457](https://github.com/PersonalClaw/PersonalClaw/issues/457)).**
- **Task/project integrity, create path: `create_task(status="done", …)` now runs the same completion gates as the update path instead of persisting a done row straight from a caller-supplied status — a task born with `status=done` while an exit criterion is unfinished or a BLOCKS prerequisite is non-terminal is refused (400), closing the create-side backdoor to the `can_mark_complete()==False` DONE row that [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)'s gate refuses only on update.**
- **Trigger specs are now validated at the door: `POST /api/triggers` and the chat/agent create tool refuse invalid cron expressions, 6/7-field (seconds-cadence) expressions, malformed skip dates, and unregistered action providers instead of persisting enabled rows that can never fire or dispatch; sub-900s cron cadences and skip dates the schedule never fires on are warned about at creation and reported by the trigger doctor ([#483](https://github.com/PersonalClaw/PersonalClaw/issues/483), [#687](https://github.com/PersonalClaw/PersonalClaw/issues/687), [#612](https://github.com/PersonalClaw/PersonalClaw/issues/612), [#560](https://github.com/PersonalClaw/PersonalClaw/issues/560), [#270](https://github.com/PersonalClaw/PersonalClaw/issues/270), [#779](https://github.com/PersonalClaw/PersonalClaw/issues/779)).**
- **The native agent loop now retries ONE pre-stream inference transient per turn (provider 5xx class, timeout) with the taxonomy's correction note and guard-shaped audit rows — a single provider blip no longer kills the chat turn ([#2287](https://github.com/PersonalClaw/PersonalClaw/issues/2287), [#252](https://github.com/PersonalClaw/PersonalClaw/issues/252)).**
- **Regenerate on a failed chat turn now works as a clean Retry of that turn's own user message instead of returning HTTP 400 (first-turn failure) or silently truncating the failed turn's message and replaying the previous question (later failures).**

- **Chat:**
- **Dashboard:**
- **Security (skills marketplace):**
- **Prompts:**
- **Loops:**
- **Security (egress guard):**

### Added

- **Proposals can now show whether they would have helped on YOUR work.**

- **Automations can now read a web page.**
- **"Open at login" is now a working switch in Settings, and it agrees with the menu bar.**
- **You can put your own note in your inbox.**

- **Notification rules can now deliver as real OS notifications.**

- **Your phone can wake you when a run is blocked on your approval — and the notification carries nothing but two ids.**
- **A reviewer's findings now get triaged by you before anything touches your code.**

- **Your watched sources now write you a morning digest, without you scheduling anything.**

- **App cards now tell you whether an app is tested, styled like the rest of PersonalClaw, and accessible — and for our own apps, a card that claims it and isn't fails the build.**

- **"Is this template edit actually better?" is now a question you can answer, not settle by taste.**

- **A resumed session no longer redoes yesterday's finished work.**
- **A big skill can no longer take the conversation.**
- **A stale browser tab now says it is stale instead of going blank.**
- **The app stops showing you an old number and then quietly changing it.**
- **Your knowledge library can live as plain markdown files you own, and you can edit them.**
- **A loop now tells you what it cost.**

- **A turn that will not fit says so before it runs, and says what to do about it.**
- **Ask your library a question about structure and get a traversal, not a guess.**
- **Independent lookups in one turn now run at the same time.**
- **Long lists stay fast however long they get.**
- **Pair a phone or a second browser with your gateway over your home network.**
- **Ask for plainer prose without editing a prompt.**
- **Stop actually stops.**
- **Know whether a model will actually run on your machine before you download it.**
- **Put back one file, not your whole configuration.**

- **See every device paired with your gateway, and cut one off.**

- **Plan a task before anything runs, from the chat you are already in.**

- **See everything your agents are doing at a glance.**
- **The desktop app has a live menu-bar item, and quitting it no longer risks the gateway.**
- **Undo a bad edit instead of restoring a backup.**

- **Apps can subscribe to platform events they declare.**

- **Pair a phone or tablet as its own device.**

- **Nudge an artifact's look without spending a message on it.**
- **Point at what is wrong instead of describing it.**
- **A pinned dashboard tile can now keep its own numbers up to date, for free.**

- **Sync through storage you don't trust, and it still can't read your data.**
- **Mistyping your sync passphrase is now a mistake you can take back.**
- **Saved the same article twice? Knowledge will now tell you, and fold the two together.**
- **Hold a thought, press a key, keep your hands where they are.**
- **The design-system tool can list what it has, instead of making the assistant guess.**
- **PersonalClaw can now notice what a project is and suggest a pack for it — and it only ever suggests.**
- **Branch a conversation from any message, and see where a branch came from.**
- **A bad edit is no longer permanent.**

- **Your phone can find this machine on its own now, if you ask it to.**
- **Two ready-made setups you can install in one go — Personal CFO and Health OS.**
- **A pack can bring a team, and only the people you actually hired show up.**
- **Paste a prompt card and turn it into something you can actually use.**
- **Share a setup as one link.**
- **Take your data out — all of it, or just the part you asked for.**
- **You can now edit the memory registers the assistant reads every session, and see who your memories are about.**

- **See what a run actually built, in a browser.**

### Fixed

- **Searching the skill store said "No results" when there was nothing to search.**
- **Selecting many conversations at once could file them under a tag or folder that doesn't exist.**

- **Two knowledge shelves could have the same name, with nothing to tell them apart.**
- **The dashboard file explorer's "Uploads" and "PersonalClaw" roots now follow the active home instead of a hardcoded `~/.personalclaw`.**

- **The audit log's "Rotate" control described a key rotation it never performed.**

- **Regenerating a project's agent-instruction files could destroy your own notes, or write them to the wrong place entirely.**

- **Choosing a project while editing a task now moves the task.**
- **A crash no longer explains itself to you as "Server got itself in trouble."**

- **A list-typed action setting is no longer thrown away without a word.**
- **"Speak replies aloud" did nothing: replies were spoken whether it was on or off.**

- **The "Streaming transcription" toggle is gone, because there is nothing behind it.**

- **Editing an artifact and navigating away lost the edit.**

- **`personalclaw snapshot` left your custom themes behind.**
- **When a model's answer is cut off mid-tool-call, it is now told that, instead of being told it forgot something.**

- **Renaming a file you were editing asked permission after the fact, then stranded the tab.**
- **Workflow controls no longer answer confidently about a run that is not there.**
- **A trigger's skip dates could be set once and never changed.**
- **Starring an inbox item now shows you something.**

- **The Doctor's "backfill missing knowledge embeddings" repair could not repair anything, and said it had.**

- **Two settings saved at the same moment could lose one of them.**
- **Renaming an automation replaced what it does.**

- **A too-long file or folder name reported a server error instead of telling you the name was too long.**

- **When the model was unavailable, knowledge enrichment told you it had found nothing.**
- **Anything named like a path was treated as a credential — which broke a bundled template and stripped native libraries out of a workflow's leaves.**
- **Saying "approve it" stopped working for good once a run had answered its first gate.**

- **Creating a knowledge intent could silently delete one you already had.**

- **Merging a knowledge tag into one nested underneath it made the tag disappear from the tree.**

- **Knowledge search's keyword fallback never returned anything, in any install.**

- **A loop interrupted by a restart could stay stuck "running" forever, with nothing working on it.**

- **Turning a tool off on the Tools page now turns it off everywhere.**

- **The skill-proposal queue could fill up and never empty.**
- **Editing a task can no longer corrupt it.**

- **Scheduled automations now actually run.**

- **Four places where sending the wrong kind of value did something worse than refuse it.**

- **Switching an automation off now survives a restart.**

- **Asking for an automation to be created switched off now creates it switched off.**

- **Restarting PersonalClaw quietly moved a chat onto a different agent.**

- **Restarting PersonalClaw mid-conversation lost what the agent had actually done.**

- **The assistant learned nothing from turns run through an external coding CLI.**
- **A read-only command is no longer called "destructive", and read-only tools no longer wait on you.**
- **The approval card now tells you which tool it is asking about.**
- **Ask mode no longer refuses a read-only `ls`.**
- **The session line said "Session created" on every single turn, and named the wrong runtime.**
- **The assistant learned "never more" as a permanent rule.**
- **A reasoning-effort setting that the coding CLI cannot honor is now refused instead of silently stored.**
- **A reasoning-effort setting no longer quietly lapses partway through long-running work.**
- **A tool blocked by Ask or Plan mode no longer ends the whole conversation.**

- **The context gauge said "0%" on turns that were nearly full.**
- **Typing `/compact` at a coding-CLI agent killed the whole turn.**
- **The sign-in page said "Sign-in failed" no matter what went wrong.**
- **The audit log's "Failed" filter hid most failures.**

- **Setting a chat's working directory with a mistyped field no longer silently unbinds it.**
- **Settings → Prompts named four of its forty-four rows.**

- **A scheduler tick wrote its history into the wrong PersonalClaw home.**
- **An agent CLI could run in your home directory instead of the folder you gave the chat.**
- **A "Pre tool use" hook that blocks nothing now says so.**
- **A chat with no folder set could run its agent CLI wherever the app itself happened to be started.**
- **Tinted "chip" buttons had unreadable labels in six of the twelve colour schemes.**

- **"Unattended runs need a verified adapter" only covered one kind of unattended run.**

- **Settings → Agents could show you a stale runner reading as if it were current.**
- **A provider that rides a CLI subscription could look signed in and still fail.**
- **"Possible duplicates" only looked at your 25 newest items.**
- **An export could carry the same database twice, and one copy was the unsafe one.**

- **Your ready-task list was in no particular order.**

- **Forking a conversation could cut it earlier than the message you clicked.**

- **A "replace everything" restore could run while the app was running.**

- **Buttons inside a generated widget now work everywhere you can see the widget.**
- **Home, the Inbox and Discover now arrive in sequence instead of all at once.**
- **A guided tour of the app, and you can take it again whenever you like.**
- **Empty pages now explain themselves and give you something to press.**
- **The audit log can now answer "what did my agent actually do?" — and tell you which record was tampered with.**
- **Artifacts are now findable from knowledge search.**
- **First run now picks up where you left it, and you can walk out of it at any point.**
- **Moving between pages now crossfades instead of cutting.**
- **The two shipped personalities now arrive with their own motion and their own tone.**
- **You can show the assistant your screen for one message — off until you turn it on.**
- **You can snip a region of your screen into a message, on any platform.**
- **Optional sound cues, off until you turn them on.**
- **First run now ends with three things you can actually do, not a tour.**
- **A knowledge item now has a reading mode, and a passage you highlight in it stays highlighted.**
- **You can now see which local models are eating your RAM, and free one.**
- **A model provider can now run in its own process, so a crash in a native library can no longer take the gateway down with it.**
- **A new install now opens on a short sidebar that grows as you use the app.**
- **You can now open your memory in Obsidian and edit it there.**

- **An empty Triggers page now offers four working starters instead of a blank form.**
- **PersonalClaw can now watch things for you, and new entries land in your library on their own.**
- **App installs now check who published the bundle, not just what's in it.**
- **You can install the phone companion to your home screen.**
- **Memory now decides what to do with a new fact instead of just piling it on.**
- **Memory can record whose claim something is (opt-in).**
- **An optional topology block orients a new session in your memory graph.**
- **Papers now ingest as papers.**
- **An app can now teach PersonalClaw to watch a source it has never heard of — by shipping a parser, not a client.**
- **Watching a site you have a link to now starts with "we already know this one".**

- **A voice is now a thing you own, not a dropdown value.**

- **Proposals are now one thing you approve in one place — and an app can raise one.**
- **An HTML artifact can now be opened as a real page, not just previewed in a card.**
- **You can point PersonalClaw at an outside skill catalog and browse it in the Skills store.**
- **When two machines edit the same thing while offline, nothing is overwritten — you get asked.**
- **Memory now has slots: a handful of small, always-there notes about you, instead of facts the assistant has to go looking for.**

- **PersonalClaw can now try a local model first for background work, and fall back to a cloud model when it can't.**
- **"Check this work" — verification that actually runs, instead of a second opinion from the same voice.**
- **Ask for a few versions and get the best one, with the others one click away.**
- **Apps can now share data with each other, read-only, only when both sides agree.**
- **Evaluation scenarios are now yours to keep, version and extend.**
- **Voice input can now run hands-free, and spoken replies stop talking over you.**
- **Approval memory: teach the assistant what it may do without asking again.**
- **Attention notifications can now ask for a second opinion before interrupting you.**
- **A Companion apps settings section — turn on LAN discovery so phone/desktop clients can find this gateway.**
- **You can now replay a finished workflow run and see exactly where an edit would change it.**
- **A run's introspection now shows how its branches and judges actually decided, across the template's history.**
- **A workflow template can now learn from its own runs — and you stay in control of every change.**
- **Accepting a skill refinement no longer rewrites the skill.**

- **Runs now record what LANDED, not just what they did.**

- **You can now ask which runs of a template went a different way — and get warned when they start going a worse way.**
- **Work that nobody reads now says so.**
- **A second starter home, for looking around before you commit anything.**

### Changed
- **PersonalClaw no longer writes anything into your coding CLI's own config, and an ACP agent app can no longer ask it to.**
- **The agent can no longer edit a file it has not read.**
- **Rewinding a conversation no longer throws the old ending away — and that history is now stored inside your chats.**
- **Finding something in a long conversation now works properly with a keyboard and a screen reader.**
- **The Optimize button now knows who said what, and leaves an already-good prompt alone.**
- **The assistant now needs to see a habit work three times, not twice, before it offers to make it a standing principle.**
- **A step that reads another step's output no longer needs a hand-written ordering — and steps in different branches of a workflow can now feed each other.**
- **Autonomous loops now learn the way workflows do.**
- **A fan-out step that shares a limited resource now waits its turn instead of racing.**

- **A workflow that reads another step's output now refuses to save unless that step is guaranteed to run first.**

- **A workflow step that declares what its output will contain is now checked against the steps that read it.**

- **A loop that keeps working but stops getting anywhere now stalls, even when it insists it is making progress.**

- **A loop's judge no longer runs on the same model as the worker it grades.**

- **The approval prompt now tells you what a tool call can touch, and how far your answer reaches.**

### Security

- **A "read-only" background task could write to your memory, open a webhook, and schedule itself.**

- **Uninstalling or disabling an app did not stop it, and uninstalling briefly gave it more access than it had.**

- **PersonalClaw's own keys were protected in the files area and nowhere else.**

- **A password inside a URL was invisible to every place PersonalClaw redacts secrets.**

- **Adding an app source no longer accepts anything you type.**

- **Three more ways a path could leave the folders PersonalClaw is allowed to touch.**

- **A record id can no longer address a file outside its own store.**

- **An argument a tool call carries can no longer lower that call's risk.**

- **Ways *in* now share one gate instead of each inventing their own.**

- **Inbound settings and tokens moved, and old ones stop working.**

- **A `.env` reached a file checkpoint through a symlink.**

- **A rewind now refuses to write outside the workspace it belongs to.**

- **An app can no longer change the version of a library PersonalClaw itself depends on.**
- **Credentials can now live in your OS keychain, and Doctor tells you where they actually are.**
- **Unattended automations now run read-only by default, and you're asked before one runs scripts in a project folder.**
- **The built-in command denylist now repairs itself.**
- **Settings → Security now shows which denylist is actually protecting you.**
- **Installed apps' backends no longer inherit PersonalClaw's environment.**
- **A scheduled Python script can no longer exhaust PersonalClaw's file descriptors.**
- **The Store now tells you which other apps an app may message.**
- **The Store no longer implies PersonalClaw confines an app's network access.**
- **Your hooks and cron scripts no longer inherit PersonalClaw's environment.**
- **Scheduled, file-watch, webhook and chained automations now honour the action denylist — they never did.**
- **A governance ceiling an operator writes once now bounds every unattended run — and the safety profile it bounds is finally read at all.**
- **An egress "allow-list" now actually restricts.**
- **A watched-source poll now honours your denied hosts on the headless-browser tier too.**
- **An auto-approval grant for a spawned subagent can be refused by the ceiling.**
- **Path rules are matched correctly.**

- **App backends now authenticate inbound requests, closing a direct-to-port bypass.**

### Fixed

- **Generated documents no longer show up in your library as broken images, and a generated PDF finally previews.**
- **The Loops page no longer tells you that you have no loops when it simply could not load them.**
- **A security-audit write that fails is no longer swallowed.**

- **A lesson saved for one project no longer becomes a rule for every project.**

- **`until_dry` workflow loops now end when the work reports no progress, instead of always running to their iteration cap.**
- **Run history no longer says "ran" for automations that did not run.**

### Changed

- **Knowledge search now finds the passage, not just the document — and tells you which passage.**

- **Semantic search on a large library got about twenty times faster.**

- **The library you already have becomes searchable by content, without you doing anything.**
- **Anthropic models now reuse the stable head of a conversation instead of re-reading it every turn.**
- **The Retro Terminal and Claw Arcade personalities now skin the error surfaces too.**
- **The Retro Terminal personality now lays a CRT raster over the whole shell.**

- **A goal loop's judge verdict now shows you what the supervisor checked for itself.**
- **"Reduce motion" now actually stops the springs — and the Bounciness slider reaches everything it claimed to.**

- **Housekeeping now runs when your machine actually needs it, instead of on a fixed clock — and one system does it, not two.**
- **A workflow judge now has to show its work, and a PASS it cannot justify is refused.**

- **A workflow plan now tells you which of its stops survive an unattended run.**
- **A `foreach` with `on_item_error: collect` now has defined behaviour, and it collects.**

- **Security docs now describe what the sandbox actually does — credential-hiding, not confinement.**

- **The desktop app can now tell the dashboard what it is actually allowed to do.**

- **First run now sets you up with a working model instead of pointing at Settings.**
- **Prompt caching is now a switch you can find, in Settings → Models.**

- **You can approve what PersonalClaw is waiting on from your phone.**
- **PersonalClaw can now earn autonomy one action at a time, and lose it instantly.**
- **The autonomy ladder now actually decides whether an automated action runs.**
- **You can now see, grant and take back what each automation may do on its own.**
- **You can share a chat as a read-only artifact — inside your own instance, never on the internet.**
- **A Routing & Efficiency panel in Settings shows which model is efficient for which kind of work.**
- **A Usage panel in Settings shows what you're spending.**
- **The chat header shows what the whole conversation has cost.**
- **The "Turn complete" line now shows what the turn cost.**
- **`personalclaw doctor` now reports your SQLite driver and its capabilities.**
- **Memory-backed answers cite their sources, and say so when memory is empty.**
- **A muted agent can be un-muted from its detail page.**
- **Local models now carry a capability matrix and a runtime/license contract from a declarative catalog.**
- **Mid-run steering now takes effect, and the judge leaves a paper trail.**

### Fixed

- **An automation fired from a background write could be dropped without a trace when the retry it was owed was skipped.**
- **A workflow set to `on_overlap: queue` started a second run alongside the first instead of queueing it.**
- **The Inbox's Mentions and Email filters could never match anything.**
- **`personalclaw update` was a dead end unless you had installed from git.**
- **`personalclaw update` could run `git reset --hard` without anyone agreeing to it.**
- **A detached-HEAD update fetched a branch that does not exist.**
- **Deleting a knowledge item mid-enrichment crashed its background pipeline with a noisy error.**
- **Renaming the built-in Personal or Repeatable project quietly broke your projects.**
- **"Run now" did nothing for almost every automation, while reporting success.**
- **A manual "Run now" left no trace and the "Running…" pill never cleared.**
- **Knowledge ingest reported steps as finished that never ran.**
- **Accepting a "refine an existing skill" proposal always failed with an error.**
- **Importing a memory file that wasn't a JSON object failed with an unhelpful server error.**
- **A request that named no task mode relaxed every chat to full execution.**
- **A project could be pointed at your credential directories.**
- **The dashboard could be tricked into handing over your secrets by changing the case of a filename.**

- **One app could borrow another app's permission to run an agent, and read agent runs that weren't its own.**
- **Discover's "see goal loops" tip opened a blank new-loop form instead of your loops.**
- **Changing your embedding model silently stopped the assistant remembering anything.**
- **A task comment could be signed as anyone, and never taken back.**

## [0.1.3] — 2026-07-30

The **attention-and-access** release. Two themes:

**One place for everything waiting on you.** The inbox stops being a message list and becomes
the single attention surface — a goal loop that needs a decision, a proposed skill, and a tool
approval you walked away from all land there as items you can answer in place, instead of a
toast that scrolls past while the work stays stalled. Delivery becomes a choice per kind of
notification (notify / badge / digest / never) rather than one global severity floor, with a
daily digest for the noisy kinds.

**Reach your own assistant from anywhere.** Sessions now survive a restart (they didn't — every
restart logged you out, and away from home that meant locked out), and an optional password
sign-in with 2FA and device pairing lets a browser anywhere get in. It is off by default and
purely additive: the local token link keeps working and remains the way back in, so a login you
misconfigure cannot lock you out of your own box.

Plus: artifacts get a real library, knowledge gets shelves and a proper tag taxonomy, the agent
navigates code by symbol instead of grepping blind, backups run and verify themselves, and
👍/👎 on AI judgments starts actually teaching.

> **Note (0.x clean break):** model bindings in `active_models.json` now carry
> ordered fallback-chain semantics. Old stores read cleanly (a single binding is a
> one-entry chain); consider `personalclaw snapshot` before upgrading, per the
> pre-1.0 banner.
>
> **Note (0.x clean break):** true rewind adds a `rewound` field to persisted chat
> messages (the retained discarded tail). Old sessions read cleanly (missing field =
> today's behavior — no migration); consider `personalclaw snapshot` before upgrading.
>
> **Note (0.x clean break):** knowledge-item tags move from a JSON column into their own
> tables, and the old column is dropped. Opening your library migrates it in place — the
> upgrade is verified against duplicates, blanks, non-ASCII and malformed values, and
> refuses to drop the column if any tag would be lost. Consider `personalclaw snapshot`
> before upgrading, per the pre-1.0 banner.
>
> **Note (0.x clean break):** the unread badge now counts unresolved **inbox** items instead
> of unacknowledged notifications, so **it resets once on upgrade** — any old unacked toasts
> stop contributing to it. Nothing is lost: the notification list keeps its full history and
> becomes a delivery audit. The badge is more honest afterwards (dismissing a toast no longer
> hides work that is still outstanding, and handling something in the inbox actually clears
> it). Your inbox alert keywords move to notification rules automatically. Consider
> `personalclaw snapshot` before upgrading, per the pre-1.0 banner.

### Added

- **Sign in from outside your home network.**
- **Pair a phone without typing your password into it.**
- **Sessions survive a restart.**
- **Hardening for an internet-exposed instance.**

- **One place for everything waiting on you.**
- **Per-notification-kind delivery rules.**
- **A daily digest.**

- **Memory records who contributed them.**

- **Memory can now offer itself, not just answer when asked.**

- **Take a conversation with you, and stop rebuilding the same chat setup.**

- **Hand an artifact to the agent, or point at one mid-conversation.**
- **Shelves for your knowledge library — including ones that fill themselves.**
- **Clean up a long chat list in one action, and let old chats retire themselves.**
- **On a shared task board, your assistant only works on *your* tasks.**
- **Decks and PDFs too — and anything already saved can become a document.**

- **It can make you a Word document or a spreadsheet you can actually send.**

- **Tags are a real taxonomy now — nest them, rename them, merge them.**

- **Your reading state and favorites are now visible, and filterable.**

- **Curate a whole shelf of saved items in one action.**
- **See what changed between two versions of an artifact.**
- **Backups now happen on their own, and they get checked.**
- **Find any chat by what was said in it.**
- **The agent navigates your code by symbol instead of grepping blind.**
- **Memory now knows what it's *about*.**
- **Your IDE can now actually ask your assistant things.**
- **Point your IDE at your assistant: a read-only MCP endpoint.**
- **Tool groups: the agent loads the tools it needs, not all of them.**
- **The artifacts library: live previews, search, and collections.**
- **Artifacts get their own page.**
- **Artifacts: collections + save-time dedup.**
- **Agent routing: suggest the right specialist, never route silently.**
- **Chat craft: seven chat-surface mechanics.**
- **Background compression keeps long chats fast.**
- **Feedback that actually teaches: 👍/👎 on AI judgments.**
- **Investigate anywhere: chat about any entity with its context pre-loaded.**
- **Model use-cases v2: routing sub-categories + fallback chains.**
- **Every model binding is an ordered fallback chain.**
- **Type-routed tool-output compressors.**
- **Projection rules: three layers + line operations.**
- **Background prose summarizer.**

- **"Investigate in chat" is now on everything worth asking about.**
- **Tool groups are now visible, and they hide what can't work.**

- **Personalities: themes that carry an identity, not just a palette.**

- **A username, so your contributions stay attributable.**

- **Backups you can actually read and verify: `personalclaw backup`.**

### Fixed

- **`personalclaw logout` never actually revoked anything.**

- **Auto-archive skipped the very chats it existed to tidy — and you couldn't see or change the rule.**

- **The "Steer" button never steered.**
- **Knowledge and memory could never embed with a config-defined provider.**
- **Binding a model can no longer fail silently.**
- **Settings and the Store no longer blink to a loading skeleton when you touch anything.**
- **Installing an app and updating PersonalClaw both failed on a `uv` virtualenv.**
- **`personalclaw snapshot` was not backing up everything — and could copy a live database unsafely.**
- **Snapshots of a non-default home no longer land in your real home.**

### Changed

- **Breaking-change policy is now written down, and it distinguishes maintainer from contributor.**

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.**
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what crashed.**
- **Mid-turn message policy: queue (default) or cancel-and-replace.**
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
- **A Doctor tab now diagnoses every subsystem from one read-only view.**
- **First-party apps now appear in the Store on a plain install.**
- **Every non-interactive model call now passes through one guarded seam.**
- **Unattended spend now has budgets, and outbound prompts are scanned for secrets.**
- **A kill switch, a path/action denylist, and a live-write guard for unattended work.**
- **A Guardrails settings surface, a provider-health view, and named safety profiles.**
- **The animated dot-wave backdrop is now a choosable background style.**
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you haven't tried, and hands external agents a routed project context.**
- **Apps surface their skills and backend routes to the agent (declared, not discovered).**
- **Offline agent reference + `pclaw-api` skill**

### Changed

- **The dashboard's system indicators are now a docked bottom rail.**

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).**
- **`monaco-editor` was never declared as a dependency**

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams**
- **CI & release engineering**

### Changed

- **Provider-boundary completion (Slack residue retired from core):**
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):**
- **Self-update is now install-kind aware (git · pip · container · desktop):**

### Removed

- **`personalclaw gateway --slack-only`**

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.**

### Added

- **Agentic chat**
- **Goal loops**
- **Memory**
- **Knowledge base**
- **Skills**
- **Automation**
- **App platform**
- **Agent runtimes**
- **Model layer**
- **Security**
- **Delivery surfaces**

### Notes

- **Single-user, self-hosted, MIT-licensed.**
- **Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.**
