# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added

- **Notification rules can now deliver as real OS notifications.** The **Desktop** delivery target in
  Settings → Notifications was accepted and stored from the day the rules matrix shipped, but nothing
  acted on it — it was dimmed and labelled as needing the desktop app, and ticking it changed nothing
  even when the desktop app was running. It works now: a rule set to **Notify** with Desktop ticked
  raises a native notification whenever the desktop app is connected, and clicking it brings
  PersonalClaw forward on the surface the note came from (an inbox alert opens Inbox, a loop's progress
  opens Loops, a skill proposal opens Skills).
  It is deliberately per-kind and never global. A kind set to **Badge** or **Digest** raises nothing
  even with Desktop ticked — those modes mean "do not interrupt me", and a banner is an interruption.
  And the dashboard stays the record: a native notification is an addition, not a replacement, so
  closing the desktop app loses nothing. The rule falls back to the dashboard bell it would have used
  anyway, and the stored note says why.
  macOS never reports whether notifications are authorized, so PersonalClaw does not claim a state it
  cannot read — if you have turned them off in System Settings, ticking Desktop will do nothing, and
  that is the OS's answer rather than a broken toggle.

- **A reviewer's findings now get triaged by you before anything touches your code.** When a workflow
  review stage reports problems in the `Finding` shape it has always been asked for
  (`severity / location / problem / why / recommended_fix`), those findings are recorded against the
  run and shown in a **Review** panel on the run page — each one accept or reject, one at a time. Only
  what you accept is sent back to the worker that wrote the diff, as a follow-up instruction it picks
  up on its next iteration. Nothing is written on your behalf: reject everything and nothing is sent,
  decide nothing and nothing is sent.
  **Every finding is checked against your actual diff before you see it.** A comment pointing at a
  line that isn't in the diff, at a file the run never touched, at a filename that matches two files,
  or at a line whose contents have since changed is shown as *unverifiable* with the reason in plain
  words — and cannot be accepted at all. Applying a real critique at the wrong line is the worst thing
  this feature could do, so a finding whose anchor no longer holds is never quietly relocated. The
  check re-runs when you open the panel and again when you submit, so an accept that went stale while
  you were reading is refused rather than dispatched.
  **The findings you reject are recorded against the reviewer.** They land in the same calibration
  record a human override of a judge does, which is what makes "this gate only ever cries wolf"
  something the system can notice instead of something you have to remember.
  **`auto_fixable` is a property, not a permission.** A finding the reviewer marked as a mechanical
  edit is labelled as one, and it still has to be accepted before it can be applied — and only up to
  `Minor`. A `Critical` fix worth making is worth watching land.

- **Your watched sources now write you a morning digest, without you scheduling anything.** At 07:00
  PersonalClaw reads whatever your watched web sources collected since the last digest, writes one
  short note into your library summarising it, and sends you one notification pointing at it. One
  note and one notification per morning, however many items came in — a digest that posted per source
  would be the flood it exists to prevent. Everything was already there except the alarm clock; this
  is the alarm clock.
  **A quiet morning is silent.** No new items means no note, no notification, and no model call — so
  this costs nothing on an install with no sources, and it does not train you to ignore it by saying
  "nothing happened" every day.
  **It respects your notification settings, because it does not have its own.** The digest is
  delivered through the same gate as everything else, so muting all notifications, raising the
  minimum severity, or being inside quiet hours suppresses it. The note is still written either
  way — your library is not a notification.
  **It cannot post the same digest twice.** The window it read is only marked as read once the note
  is safely written, so a restart or a retry re-reads at most one morning rather than skipping one,
  and a second run over an already-summarised window does nothing at all.
  **Scraped pages still cannot give it instructions.** Every title and body goes into the summary
  prompt wrapped as untrusted data, and the run's only powers are writing that one note and sending
  that one notification — no tools, no shell. It shows up as `system:source-digest` under
  Automations if you want to run it by hand, retime it, or turn it off.

- **App cards now tell you whether an app is tested, styled like the rest of PersonalClaw, and
  accessible — and for our own apps, a card that claims it and isn't fails the build.** An app can
  declare a `quality` block in its manifest (`tested`, `designSystem`, `a11y`) and the Store shows it
  as badges. For a first-party app that is not a nice sentiment: CI runs the app's own tests, lints its
  frontend against the same design-token rule the dashboard is held to, and checks its accessibility
  scan against the version actually shipping — so declaring a bar it doesn't meet turns the build red
  rather than making the card look nicer. A stale scan from the previous release doesn't count.
  **An app that says nothing gets no badges.** Not a row of red marks — nothing. Saying "we haven't
  audited this yet" and saying nothing at all are different, and both are different from passing, so
  the card shows all three differently. A third-party app's badges are its author's word, which is
  why the tooltip says *declares*, not *verified*.

- **"Is this template edit actually better?" is now a question you can answer, not settle by
  taste.** When PersonalClaw proposes a change to one of your workflow templates, it now also
  registers a formal A/B study for that change — the old template and the new one, run side by
  side over your own past runs, judged blind. The study's design is fixed and hashed *before* the
  first run, so nobody (including PersonalClaw) can move the goalposts after seeing the numbers;
  editing the scoring rubric mid-study voids the study instead of quietly changing the answer.
  **Nothing spends your money without you asking.** Registering a study is free and automatic.
  Running one costs real model calls, so it is always something you invoke —
  `personalclaw study --list` shows what is registered, and `personalclaw study --run <id>
  --dry-run` tells you exactly how many calls it would take *before* it takes them.
  **A study that cannot measure anything says so.** Too few past runs to be meaningful is
  reported as low power rather than dressed up as a result, and no past runs at all is a refusal
  with an exit code — not a green tick over an empty list. Two candidate versions that turn out
  to be identical are refused outright, because that would report a confident "no difference"
  about a comparison that never happened.

- **A resumed session no longer redoes yesterday's finished work.** Picking a long task back up used
  to hand the agent whatever prose survived and leave it to *infer* how far it had got — and it
  inferred wrong, cheerfully re-running a step that had already completed. A resumed or compacted
  session now carries a short, explicit record of what actually happened, **read out of the logs the
  run already wrote** — which steps completed, which failed, which files were touched, which choices
  were already settled. Nothing in it is written by a model, so it cannot invent a completion: if a
  fact was not recorded, it is not in there.
  **A failed step stays failed.** A step that was tried and did not work is reported as failed, and a
  tool call whose result was never recorded is reported as unfinished — never quietly rounded up to
  "done". Forgetting that something finished costs one repeated step; believing something finished
  when it did not silently skips work you asked for, which is worse.
  **It is a record, not a to-do list**, so the agent reads it as background rather than as
  instructions, and it is small enough to survive compaction without crowding out what you just
  asked. Compaction now carries it through instead of folding it into the summary.
  **And if the record disagrees with your files, the resume stops.** If it says a file exists and it
  is gone — the branch changed under you, the tree was reverted — the turn refuses and names the
  file, instead of carrying on from a picture that is already wrong.
- **A big skill can no longer take the conversation.** A matched skill's body used to be pasted
  into the prompt before anything measured it, so a long skill simply took the window and the
  conversation got whatever was left — and "why did my skill not take effect?" had no answer
  anywhere. Skill bodies now compete for the prompt on the same budget as every other injected
  block, on declared priority rather than on which one was pasted first.
  **Each skill declares what it may spend.** A new optional `context_tier` in a skill's
  frontmatter — `light` (1,000 tokens), `standard` (3,000, the default) or `heavy` (8,000) — is
  that skill's own ceiling, and all the skills in one turn additionally share a 16,000-token
  aggregate. An omitted or misspelled tier is treated as `standard`, so a typo never quietly
  shrinks a skill you rely on.
  **Over its ceiling, a skill loads in a REDUCED form — never cut off mid-sentence.** What goes
  into the prompt is the skill's own one-line description and its declared `resources:` entry
  points, complete, plus the call that loads the whole thing on demand. A body sliced at a byte
  boundary is worse than a shorter complete one: nothing in the text tells the reader it is half,
  and half a procedure fails at step four. A skill that declares *neither* a description nor
  resources has nothing to reduce to, so it is refused and named rather than summarized by
  guesswork.
  **And you are told, in the turn, which skill was reduced or refused and why** — with the
  numbers: this body is 42,458 tokens, its tier allows 3,000, call `skill_invoke` for the rest.
  Three outcomes, no fourth: admitted, reduced, refused.
- **A stale browser tab now says it is stale instead of going blank.** The gateway has always
  published an API version; nothing ever checked it, so a page left open across an upgrade — or a
  cached bundle a service worker held onto — kept calling the new server with the old client's
  assumptions and broke somewhere deep, on whichever field had quietly changed shape. The dashboard
  now tells the gateway which version it was built for, the gateway compares it in one place, and a
  version it cannot speak is refused with a sentence that names both versions and which side is out
  of date: "this client was built for API version 1; this gateway speaks 2-3. Upgrade the client —
  reload the page to fetch the current build." Every accepted request also comes back stamped with
  the version it was treated as, so `curl -sD-` can answer "which contract am I on?" without
  guessing. Nothing you rely on to *recover* is gated — the page itself, its assets, the login door,
  the health probe and the manifest that publishes the version all answer as before — and a caller
  that declares no version at all (a script, a `curl`) is treated as the oldest version still
  supported rather than assumed current, so it keeps working today and is told plainly, rather than
  breaking silently, on the day support for it ends.
- **The app stops showing you an old number and then quietly changing it.** Every screen used to
  read its data through a hand-rolled cache — 124 files reached for the same helper, and a few
  surfaces kept private caches of their own beside it. A cached page painted instantly, which is
  good, but it painted with total confidence whether the value had landed forty milliseconds or
  forty minutes ago, and then swapped it for a different one a moment later. That repaint reads as a
  bug even when both numbers were once true. There is now one data layer, and a cached first paint
  is either **fresh** or **says "Updating…"** while it is being re-read. Measured on the old build:
  the Settings → Inbox card painted "30 day retention" after a reload when the server already said
  7, with nothing on screen to indicate it, and then became 7.
  **A change is reflected everywhere, immediately, without a reload.** Accept a proposal on one
  screen and the badge counting it on another updates itself; delete something in one list and a
  picker elsewhere stops offering it. Each write now declares which data it affects, so nothing has
  to be refreshed by hand and no surface is left describing something that has already changed.
  **A screen that could not load its data says so, instead of saying you have nothing.** "We
  couldn't load your items" with a Retry is a different message from "you have no items yet", and
  they are no longer interchangeable.
  **And opening a page asks the server once.** Several cards reading the same thing used to make the
  same request several times over; they now share one.
- **Your knowledge library can live as plain markdown files you own, and you can edit them.** A
  knowledge item used to be reachable only through PersonalClaw's database. Turn
  `knowledge.vault_mode` on and every item is also written out as a human-readable markdown file
  under your home — YAML front-matter carrying its identity and its relations, wikilinks you can
  follow, readable in Obsidian or `grep` or any text editor, with or without PersonalClaw running.
  **`two_way` means an edit you make in a text editor is read back, not overwritten** — which is the
  whole difference between an export and ownership. It reuses the memory vault's projector rather
  than adding a second one, so both vaults are the same artifact in two directories.
  **Nothing is silently resolved.** A page that changed in your editor *and* in the app since the
  last sync is not merged, not overwritten, and not quietly filed toward the database: nothing is
  written on either side, your text is left exactly as you typed it, `sync_conflict:` appears in the
  page's front-matter, and the Doctor reports it as a page waiting on you.
  **Deletion means deletion, in both directions.** Delete a page in your file manager and it stays
  deleted — it is not re-created on the next sync, and your item is not deleted either (a missing
  file is an ambiguous signal, not an instruction). Delete an item in the app and its file goes with
  it, leaving no stale page behind.
  **Off by default, and it cannot run away with your files.** The projection is opt-in, an
  unreadable config resolves to off, it runs in bounded batches on the existing maintenance cadence
  rather than a loop of its own, and an item too large to project is refused and reported rather
  than truncated.
- **A loop now tells you what it cost.** The loop cockpit carries a spend pill beside the elapsed
  time, reading the same per-turn ledger the Usage panel reads. It covers the loop's worker *and*
  every parallel task worker it fanned out into, so a loop that split into five workers reports one
  figure rather than a fifth of the truth.
  **What the number does not include is written next to it, not buried.** Planning is a separate
  session doing separate work, so planning spend is shown as its own amount (`~$1.25 + ~$0.4000
  planning`) instead of being folded into the run total or quietly dropped. If any model involved had
  no price row, the tooltip says the figure is a floor and the real total is higher — and a loop with
  nothing recorded says exactly that, rather than showing a confident `$0.00` that cannot tell
  free-and-local apart from not-yet-known.

- **A turn that will not fit says so before it runs, and says what to do about it.** PersonalClaw
  used to find out a prompt was too big by sending it and reading the provider's error back — after
  you had already waited. The context is now measured against the bound model's real window *before*
  the call, and exactly one of three things happens: it fits; it fits after compression, and you are
  told which block was compressed and from what size to what; or it cannot fit, and the turn is
  refused with the specific oversized block named — that tool result, that retrieved document, not a
  generic "context overflow" — plus a fix: shorten that block, run `/compact`, or switch to a model
  with a larger window.
  **Room to answer is part of the budget.** A prompt that fills the window exactly leaves nothing for
  the reply and fails the same way one that is too long does, so the bound is the window minus the
  reply reserve — the same number the model is handed as its output limit, not a second guess at it.
  **You are warned while there is still room to act.** A long session now reports its headroom as it
  tightens, instead of only once it is gone.
  **An unmeasurable window hides nothing and blocks nothing.** If neither the model catalog nor the
  window table names your model, the turn proceeds and the pressure reads as unmeasured: "we could
  not measure this" and "there is plenty of room" are different answers, and a hardcoded default
  standing in for either is a guess dressed as a fact. In the same spirit, the one silent drop that
  was already there — the session-context cap quietly shortening long history and telling only a
  server log — now tells you.
- **Ask your library a question about structure and get a traversal, not a guess.** "What links to
  this note", "what does it depend on", "what is under this tag", "what changed since Friday",
  "which of my claims contradict each other" are graph questions, and answering them by semantic
  similarity got them right only by coincidence. The agent now has a `knowledge_structural` tool
  that walks the relations your library already holds — typed links, citations, the tag tree, edit
  times, recorded contradictions — and every answer **carries the chain that reached it**, so you
  can see *why* an item came back instead of taking it on faith. Structural and semantic retrieval
  compose rather than competing: restrict to a tag subtree first, then rank what is inside it by
  meaning. And when there is genuinely nothing there, it says which fact was missing — no such
  item, no such relation, no such tag — instead of quietly handing back the closest thing it
  could find.
- **Independent lookups in one turn now run at the same time.** When the agent asked for several
  things it could have looked up simultaneously — a few greps, a couple of file reads, a repo map —
  it did them strictly one after another, and you waited for the sum. A representative
  eight-lookup turn over a 1,200-file repo took **2,219 ms on average and swung between 1.4 and
  3.6 seconds**; it now takes **942 ms and never exceeds 993 ms**. That is 2.35× faster on the
  average, and the *worst* new sample beats the *best* old one — the old spread was the serial
  accumulation showing through. Calls that could interfere still take turns: a write to a file
  waits for the reads of that file, a call needing your approval runs by itself, and anything the
  system cannot classify runs alone rather than optimistically. Results come back in the order
  they were requested, so nothing you see is reordered.
- **Long lists stay fast however long they get.** Sessions, inbox, knowledge, workflow runs and the
  diagnostics log now render only the rows on screen. On a real store of 5,000 chat sessions the
  session list was rendering all 5,000 rows and 175,688 DOM nodes: typing in its search box took
  **137 ms per keystroke**, scrolling ran at **5 fps**, and reaching the last row took **12
  seconds**. It now renders 18 rows and 1,337 nodes no matter how much you have — **13 ms** per
  keystroke, smooth scrolling, **1.3 s** to the last row, and those numbers no longer grow with
  your library. Below 64 rows nothing changes at all, so short lists behave exactly as before.
  Keyboard navigation still reaches rows that aren't rendered yet (End really does go to the last
  one), a row you had selected is still selected when you scroll back to it, screen readers still
  announce the true total rather than the visible slice, and a link straight to a row still scrolls
  to it — that last one was quietly broken in the inbox for any row deeper than about 400. One
  honest trade: browser find (Ctrl+F) only searches what's on screen, so each list now says so and
  points at its own search field, which searches everything.
- **Pair a phone or a second browser with your gateway over your home network.** Settings → Devices
  already handed you a pairing code and a link; opening that link on the other device now lands on a
  screen that actually redeems it, instead of a "token required" wall telling you to run a command in
  a terminal the joining device does not have. The code arrives pre-filled from the link, you can
  give the device a name, and it signs in. Paired devices are listed with their name, what kind of
  device they are, when they were last seen and how they got in; revoking one locks it out
  immediately, on this gateway and on disk, so it stays out across a restart.
  **The redeem screen is reachable without a session, and that is all it is.** Gating the one page a
  joining device must open behind the session it exists to create would be circular, so it is exempt
  from the token check for the same reason the sign-in page is. The page is a fixed document with no
  secret on it — the code is read out of the link by the browser and never written into the page by
  the server — and every grant still happens at the pairing endpoint, behind its origin check, its
  per-address lockout and its single-use short-lived code. A browser that is already signed in is
  sent home rather than offered the form: redeeming a code there would quietly turn your own laptop
  into a "device" and strand the session it replaced.
  **Pairing across the network needs the gateway to know its own address.** The link points at
  whatever address you are reading the dashboard on, and the gateway only accepts a pairing request
  from an address it recognises. If you reach the dashboard over your LAN, set the dashboard URL to
  that address; until you do, a device that opens the link is told so in words rather than being
  refused without explanation.
- **Ask for plainer prose without editing a prompt.** A new **Natural voice** control in the chat
  composer asks for writing that reads like a person wrote it: answer in the first sentence, no
  "Great question" opener, no summary paragraph repeating what you just read, no "let me know if
  you'd like me to" when nothing was asked, and the shortest accurate word instead of "leverage" or
  "delve". It names the patterns to avoid, because "sound natural" measurably does nothing.
  The same switch lives on an agent definition, so a preference travels with that agent into every
  chat that uses it — and a single conversation can override that agent for itself, without editing
  the agent. **It changes only how the reply reads.** Every fact, number, path, unit and caveat
  stays; a refusal stays a refusal, stated as fully and directly as before, because plainer prose is
  not softer prose. The state shows on the pill (including when an agent is what turned it on), so
  when the writing changes you can see what changed it.
- **Stop actually stops.** Pressing stop mid-turn used to acknowledge the request and then let the
  work carry on: the model request already in flight was awaited and its answer discarded, tool
  calls already queued for that turn all ran anyway, a shell command kept going, and a spawned
  subagent finished its whole task. Stop now reaches every one of those — the in-flight request is
  cancelled at the provider, remaining queued calls are dropped without executing, a running
  command's **whole process tree** is terminated and reaped (a shell's children were the ones most
  likely to survive and keep holding a lock or a file handle), and spawned subagents are stopped
  with it. A cancelled turn also keeps the tokens it spent instead of dropping them from your usage,
  and the transcript distinguishes **you stopped this** from **this was cancelled**, because those
  are different things to read a week later. Pressing stop when nothing is running remains a no-op
  rather than corrupting the next turn.
- **Know whether a model will actually run on your machine before you download it.** Every model in
  the download lists now carries a fit chip — green, yellow, red — computed from this machine's real
  memory budget: total RAM, minus a reserve held back for the OS and the inference runtime, plus a
  discrete graphics card's own VRAM where there is one. A browse filter hides models this device
  cannot run, and Settings → Models gains both the reserve and that filter's default.
  **Unified memory is counted once.** On an Apple Silicon Mac — or any machine with integrated
  graphics — the GPU's memory *is* system memory. Adding the two together reports a budget larger
  than the machine physically has, promises a fit, and then runs out of memory at load time, after
  you already waited through a multi-gigabyte download. Only a discrete card adds a second pool.
  **An unknown budget hides nothing.** If this machine's memory could not be measured, every model
  stays listed and its chip reads unknown: "we could not measure this" and "nothing fits" are
  different answers, and only one of them should take models off your screen. For the same reason a
  model family quotes its **median** variant rather than its smallest, so the chip cannot promise a
  fit you will not get from the variant you actually pick, and the download panel steps down to the
  largest variant that does fit. A download with nowhere to land is refused with both numbers named
  — what it needs and what is free — but when the filesystem cannot be measured at all, the check is
  skipped with a warning instead of blocking a download that would have been fine.
- **Put back one file, not your whole configuration.** Time Travel could already roll a whole area
  of your state back to an earlier point; now you can pick individual files out of a change and
  restore just those. Rollback and revert keep their distinct meanings per file — rolling back a file
  discards the later edits to it, reverting undoes only that one change and keeps everything after —
  and the confirmation names which files it is about to touch, because a dialog that says "roll back
  to this point" while applying to two files out of forty is describing the wrong blast radius.
  The preview stays mandatory by construction: a confirmation now has to match the exact file set it
  was shown, so a selection that changed after you previewed is refused rather than quietly applied.
  **Changes made while you were away are labelled as such.** Writes from unattended work now record
  themselves as background, so the panel's "what changed while I slept" filter has something real to
  separate from your own edits.
  **A settings change now actually reaches your history.** Saving a setting wrote the file in a way
  the history recorder never saw, so the configuration area stayed empty and "roll back my settings"
  had nothing to roll back to. Found by driving the real thing rather than trusting the tests.

- **See every device paired with your gateway, and cut one off.** Settings gains a **Devices** page:
  each paired phone, tablet or browser with its name, what kind of device it is, when it was last
  actually seen, how it got in, when it paired and when its session runs out. "Pair a device" gives
  you a one-time code and a link to open on the other device, with the expiry counting down and both
  values copyable. Revoking asks first — naming the device it is about to lock out — and the lockout
  is real: it drops the session in memory *and* on disk, so a revoked device stays out across a
  restart. A revoke that fails says so instead of quietly leaving the device connected.
  **"Last seen" is honest about not knowing.** A device that paired and never came back reads
  **never**, not the time it paired — the distinction matters precisely because you would use this
  column to decide a device is no longer in use. The timestamp is written where a device's request is
  authorised, at most once a minute per device, and it can never delay or block a login: if that
  write fails, you get a stale timestamp, never a locked-out device.
  The pairing screen shows the link and code rather than a scannable QR image, and says so where the
  image would go — the link already contains the code, so a second browser on your network needs
  nothing else.

- **Plan a task before anything runs, from the chat you are already in.** The composer's **Add** menu
  gains **Plan this first**: the chat drafts a plan, hands it to you as editable markdown, and runs
  nothing until you approve it. You can rewrite the plan by hand, comment to have it redrafted, or
  approve it and watch the work follow what you approved. It is manual only — a quick question is
  never interrupted by a planning step — and the no-execute promise is enforced by the same tool gate
  that backs `ask`/`plan` task modes, not by asking the model nicely: while a plan is awaiting review a
  mutating tool is refused, and the task-mode pill declines to drop out of `plan` until you approve or
  cancel. Turning it on mid-task parks the run in flight, keeps the whole transcript, and continues
  from your approved plan. It reuses the planning walkthrough the loops already use, so there is one
  planner in the product rather than two.

- **See everything your agents are doing at a glance.** The dashboard gains an **Agent world** — an
  ambient scene where every running loop, live chat and background subagent is a body in orbit,
  pulled toward the centre as it starts to want you: waiting on you nearest, then waiting for
  approval, then working, with idle furthest out. Loops draw their cycle progress as an arc; a run
  parked on a tool approval reads as *waiting*, not *busy*. State changes glide between orbits
  instead of jumping. Under `prefers-reduced-motion` it is a still picture, not a slow one — nothing
  orbits, nothing pulses, and the layout is identical. The scene always carries the same facts in
  plain text ("1 waiting on you, 2 working"), so nobody has to see the animation, and it falls back
  to a list where a browser blocks canvas. A failed read says the world is *unknown* rather than
  showing a calm, empty sky.
  For anyone building on it: the underlying `AgentActivityFeed` is a documented read contract
  (`docs/architecture/agent-activity-feed.md`) that folds `/api/loops`, chat sessions, subagents and
  approvals into one typed shape, refreshed by existing WebSocket envelopes **as signals only**. Apps
  will be able to contribute their own worlds against it without asking for a single permission.
- **The desktop app has a live menu-bar item, and quitting it no longer risks the gateway.**
  The macOS menu-bar menu now shows pending approvals and running loops and refreshes itself,
  with every row deep-linking into the dashboard (approvals stay clickable at zero, so the row
  is a way in rather than a control that greys out). **Open at Login** is there too — off by
  default, reversible from the same checkbox or from System Settings → General → Login Items,
  registering only this app bundle with no launch agent and no administrator password, and
  turning it on twice cannot leave two entries behind. **Quit now waits for the gateway to
  actually exit** instead of signalling it and disappearing, escalating after a grace period
  and saying so in the log if the gateway still will not stop — the old path could leave an
  orphaned gateway holding the port after a slow shutdown. If the menu-bar item cannot be
  created, closing the window closes it for real rather than hiding it, so a failed tray can
  never leave a running app with no way back to its window.
  *Quick Capture opens the Inbox for now; writing the note itself is not built yet.*
- **Undo a bad edit instead of restoring a backup.** PersonalClaw now keeps a local, continuous
  git history of the state you and the assistant actually edit — configuration and entity settings,
  `skills/`, prompts and prompt snippets, per-project context, and the memory markdown tree. A commit
  is scheduled about ten seconds after a write and tightens toward immediate under sustained editing,
  so history costs nothing and lags nothing; the memory tree is also committed hourly, which bounds
  how much memory history can ever be missing to one hour. Settings → Backups → **Time travel** shows
  the per-root timeline with a "what changed while I slept" filter, and offers two distinct verbs:
  **roll back** (go to a point in time; the changes you set aside stay listed, so you can come
  forward) and **undo just this** (reverse one change and keep later edits, failing loudly and
  changing nothing if a later edit touched the same lines). Both are preview-first and the *server*
  enforces it — a confirming request must echo the head the preview returned, so nothing destructive
  can run against a tree you did not see, and a preview that went stale is refused rather than
  applied. Secrets are excluded from the history structurally (each repository ignores everything and
  re-includes only the declared paths) and are **preserved across a rollback** — your credential
  store and `.env` are never committed and never deleted. This history is local-only: it is never
  synced, exported, or included in a snapshot. New setting `durability.time_travel` (on by default;
  needs `git` installed, and degrades to "no history" without it).

- **Apps can subscribe to platform events they declare.** An app that declares
  `permissions.eventSubscriptions` now receives `session.created`, `knowledge.ingested` and
  `task.completed` through its existing message inbox — no new route, and the install consent screen lists the
  events it will get. Delivery is **deny-by-default and exact-match**: an app that declares nothing receives
  nothing, and `task.*` or `task.completed.extra` match no event. A subscription grants **timing, not content** —
  payloads carry identifiers only, so subscribing never widens what an app can read.

- **Pair a phone or tablet as its own device.** `POST /api/devices/pair/start` mints a short-lived,
  single-use code (shown as a QR by the Devices panel); `pair/complete` redeems it into an ordinary
  owner session — no new token type — and `GET/DELETE /api/devices` lists and revokes them. A revoked
  device is locked out on its next request and stays locked across a gateway restart. Every route
  writes a security-log entry, denials included.
  **Breaking (pre-1.0):** `sessions.json` rows now carry an issuer and an optional device record
  instead of a bare expiry. Rows in the old shape are **discarded on read**, because a row with no
  issuer is a live session the device registry can neither describe nor revoke. The cost is one
  `personalclaw token` re-mint. Run `personalclaw snapshot` before upgrading if you want a way back.

- **Nudge an artifact's look without spending a message on it.** A generated card, chart or
  dashboard can now declare its own tunables, and the **Iterate** rail beside it turns them into
  real controls — a colour swatch, a slider, a switch. Drag one and the artifact restyles as you
  move, with nothing sent anywhere: no model call, no request, no turn. When you like it, **Save as
  a new version** reads what the preview is actually showing, writes those values back into the
  artifact itself and snapshots a version, so a reload comes back the way you left it and the old
  look is still one click away in the version history. Everything else in the artifact is left
  exactly as the agent wrote it.
- **Point at what is wrong instead of describing it.** Switch on **Mark elements**, click the parts
  of a rendered artifact you want changed, and type one short note each. Sending it produces a
  single message that names every element you marked, so the agent fixes all of them in one pass
  rather than guessing which "second heading" you meant. On a loop's output it goes to that loop as
  guidance; anywhere else it opens a chat and asks for the same artifact to be refreshed in place.
- **A pinned dashboard tile can now keep its own numbers up to date, for free.** Bind a tile to a
  skeleton (an artifact whose body carries `{{...}}` slots) plus the data sources that fill them,
  give it a refresh interval, and the tile re-renders itself on that cadence — same layout, new
  data, and **no model call**, because the refresh is pure substitution rather than an agent
  rewriting your panel. The tile header tells you the truth about it: how long ago it last
  refreshed, one dot per data source (hover for that source's own error), and a link to the exact
  ledger row with the cost — which reads `0 tokens` and means it. If a source fails, the tile keeps
  showing the last good content and turns its dot red instead of going blank. Tiles fire nothing
  while incident mode is on, and a tile's sources are limited to read-only ones.

- **Sync through storage you don't trust, and it still can't read your data.** Turn on
  `durability.sync_encrypt` and everything PersonalClaw sends to a shared bucket or folder is
  encrypted on this machine first — your tasks, memory and knowledge arrive as bytes the storage
  provider cannot open. What stays readable is only the routing: which machine wrote which batch,
  so a second machine can still work out what to fetch without holding your passphrase. Every
  machine that knows the passphrase reads every other's data; anyone who doesn't gets nothing, and
  a single altered byte is refused rather than quietly accepted. Backups → sync status now tells you
  plainly whether your data *is* encrypted instead of echoing the setting back at you. The default
  does the sensible thing per destination — on for cloud storage and shared folders, off for a
  private git repo, where a readable history is the reason you chose it — and you can override it
  either way. The passphrase lives in the credential store, never in a config file, so it stays out
  of exports and out of the app's own history. Two honest caveats: if encryption is switched on and
  no passphrase is stored, sync **stops** rather than sending your data in the clear; and a
  forgotten passphrase means the remote copies are unreadable — the data on this machine is
  untouched, so you start a fresh sync location rather than losing anything.
- **Mistyping your sync passphrase is now a mistake you can take back.** Get it wrong and the
  batches from your other machines are held, not thrown away: fix the passphrase and the very next
  sync merges everything it was holding. Previously a single sync with the wrong passphrase would
  have skipped those batches permanently, and correcting it afterwards would not have brought them
  back.
- **Saved the same article twice? Knowledge will now tell you, and fold the two together.** Open a
  knowledge item and the **More details** panel lists anything that looks like the same document
  again, each with the reason it was matched and how long ago it arrived, so you can judge the claim
  rather than take it on trust — and **Open** lets you read the other copy before you decide.
  **Merge into this item** keeps the one you are looking at and moves everything off the other one:
  its collections, tags, entity mentions and highlights all end up on the copy you kept, so merging
  never quietly undoes curation you did on the wrong copy. It asks first, and the question names
  which copy survives, which one gets deleted, and that it cannot be undone. If the check itself
  fails, it says so and offers to retry, instead of shrugging and showing you a clean library.
- **Hold a thought, press a key, keep your hands where they are.** In the desktop app a global
  shortcut (**⌘⇧Space** by default, and configurable in Settings → Speech & Transcription) starts
  recording your microphone from wherever you are, even with another app in front. Press it again
  and what you said is transcribed and dropped into the composer **at your cursor** — your existing
  draft is kept, not replaced. While it is listening you can see it in three places at once: macOS's
  own orange microphone dot, `● Listening` in the PersonalClaw menu-bar item, and a chip beside the
  composer. The middle one matters because the shortcut is global: if the window were hidden behind
  something, an in-window indicator would be an indicator you cannot see. Ending the recording
  releases the microphone — the app does not sit on an open mic between captures — and a capture you
  forget about stops itself after two minutes. Pick a shortcut another app already owns and Settings
  says so and keeps the one you had, rather than failing quietly until the next launch. Two honest
  limits: the shortcut **toggles** rather than working while physically held, because reading key
  releases system-wide would mean asking for a far broader permission than dictation deserves; and
  PersonalClaw records **the microphone only** — never system audio, since on macOS the only route to
  that is the screen-recording permission, and asking to record your screen in order to record sound
  is not a trade worth making quietly.
- **The design-system tool can list what it has, instead of making the assistant guess.** Asking
  the assistant to build a page used to mean it searched the UI kit by keyword — and a search needs
  a word you already know. `ui_list` just names everything: every component with its one-line
  description, or every design token. Behind it, a bundled app can now carry its own provider code
  instead of pointing at something inside PersonalClaw, so capabilities like this one grow in the
  app that owns them.
- **PersonalClaw can now notice what a project is and suggest a pack for it — and it only ever
  suggests.** Point a project at a Terraform directory and **Settings → Packs** offers the new
  **Infra Ops** pack, with the reason attached: which file patterns and which content signals
  matched, out of how many the rule declared, the example files it found, and the full list of what
  installing would put on your machine. Nothing is read by a model — it is file-shape matching, and
  it runs only when you create a project or press **Suggest packs**, never on a timer. Say "not for
  this project" once and it is remembered for that project forever. The whole thing is off if you
  turn off **Project fingerprinting**. Same panel now has the **pack store** (install any pack that
  ships with your build) and, for packs you already have, **Check for update** — which shows you
  what it would replace *and what it would leave alone* before you apply it. A pack update never
  overwrites a file you have edited: your version is kept and the skip is named on screen, with the
  reason, so a respected edit never looks like a silent one.
- **Branch a conversation from any message, and see where a branch came from.** Hovering any
  past message — your question or the assistant's answer — reveals a **Branch from here**
  button that copies the conversation up to that point into a new chat, leaving this one
  exactly as it was. Branch the same answer twice to take it two directions, or branch a
  branch; each one is a real, separate chat with its own context. The new chat carries a
  **Branched from** link back to the one it came from, which stays there after a reload and
  follows the original if you rename it. Nothing is overwritten, so there is no confirmation
  to click through.
- **A bad edit is no longer permanent.** Before the agent's first write to a file in a turn,
  PersonalClaw now saves that file's current bytes. When a turn wrecks something, `/rewind-to-turn N`
  first *shows* you what it would change — the exact files, with diffs — and only writes after you
  confirm. It restores the files and leaves the conversation alone, so the transcript still shows what
  happened (`/undo` remains the one that rolls back the chat). Credential files like `.env` are never
  copied at all, which also means a rewind will not restore one — the preview says so rather than
  quietly skipping it. Tune the store under **Settings → Chat → File checkpoints**.

- **Your phone can find this machine on its own now, if you ask it to.** Getting a companion
  device onto your gateway used to start with reading an IP address off one screen and typing it
  into another. Turn on **Settings → Companion apps → LAN discovery** and the gateway announces
  itself on your local network by name instead — the same mechanism that makes printers and
  speakers show up — so `personalclaw discover` on the other device prints it and the URL to use.
  It is **off until you turn it on**, because announcing a service on a network is a choice and
  the answer is obviously different on your own Wi-Fi than in a café. Turning it off does not cost
  you access, only typing: the address and the pairing code both work exactly as before, and
  nothing about pairing depends on discovery being on. What gets announced is four things — the
  name you chose, the port, "this will want you to pair", and a version number — and the panel
  shows you that record verbatim, because the honest way to answer "what did I just publish about
  myself" is to let you read it. There is no token in it, no session, and nothing you have said to
  the assistant. Discovery only ever says *where* this machine is; what decides *whether* a device
  gets in is still the token or the pairing code, unchanged. If your gateway is only listening on
  itself, it announces nothing at all and the panel tells you why rather than pretending the
  switch did something — a service at `127.0.0.1` means a different thing on every device that
  hears about it, so advertising one would be a lie. There is a new guide,
  [Companion apps](docs/guides/companion-apps.md), including the parts that do not work yet.
- **Two ready-made setups you can install in one go — Personal CFO and Health OS.** Each one
  arrives as a whole working thing rather than a pile of parts: the skills, the agent, a
  runnable template, a prompt, and one scheduled automation. Nothing about them starts running
  on its own. The automation lands **switched off**, waiting in Automations until you turn it
  on. The outside service each pack would like — a finance connector, a health-records
  connector — asks you whether to set it up, swap in one of your own, or skip it; skipping is a
  real answer, and anything that depended on it says plainly that it is unavailable instead of
  failing later for no visible reason. And each pack ships a short "finish setup" interview you
  run when you are ready, which asks where your statements or your journal live and remembers
  the answer; until you have answered, the pack tells you exactly which question is still open.
- **A pack can bring a team, and only the people you actually hired show up.** A pack's roster
  lists each agent with a tier. Installing it puts every persona on your machine, but one click
  deploys only the `always` tier — the rest stay parked, ready to bring in later, and nothing
  quietly turns them on. If a pack's roster points at an agent it forgot to include, the install
  stops before writing anything and names the exact missing reference, rather than installing a
  team with a hole in it.
- **Paste a prompt card and turn it into something you can actually use.** Those long "life OS"
  prompts people share can now be pasted in and converted into a real prompt, a multi-step
  template, or an agent — whichever it actually is. The pasted text is treated as data, never as
  instructions, so a card that tries to talk to the assistant gets described rather than obeyed.
  Nothing is saved until you look at the result and accept it; you see exactly what would be
  written first, and rejecting it is remembered so it does not come back.
- **Share a setup as one link.** A pack can be handed over as a single JSON file instead of a
  zip, small enough to paste. Importing one goes through exactly the same checks a pack file
  does, and every piece inside carries its own fingerprint — change one byte anywhere and the
  whole import is refused before anything is written.
- **Take your data out — all of it, or just the part you asked for.** Settings → Import/Export now
  has three buttons instead of one: everything, just your knowledge (your documents, with their
  filenames), or just what the assistant remembers about you. Credentials never travel, and neither
  do rebuildable caches like search indexes — a stale index restored next to newer data is worse than
  no index. Archives made from now on carry a checksum for every file inside them, so importing one
  tells you whether it arrived intact *before* it writes anything; older archives still import, and
  say plainly that there was nothing to verify them against. Settings → Backups gained an archive
  browser: what is in each snapshot broken down by area, whether the monthly restore rehearsal passed
  on it, and a preview-then-restore that shows you the plan first.
- **You can now edit the memory registers the assistant reads every session, and see who your
  memories are about.** Memory → Studio grew two new kinds beside Facts and Episodes: **Slots**,
  the small always-injected registers (persona, preferences, pending items, glossary, self notes,
  self model), and **Entities**, the people, projects and tools your memories name. Editing a slot
  shows its live budget, and if a line will not fit nothing is written — you are told exactly which
  of your own lines to drop instead. Removing one retires it rather than deleting it, so a later
  reflection pass cannot quietly put it back. Selecting a fact now shows which entities it links
  to, with the reason for each link, which is how you answer "why is this in my context?". Names
  that keep coming up but are not entities yet queue up for a yes/no instead of being created
  behind your back. The graph canvas can switch from records to the entity map, coloured by
  neighbourhood and filterable by link type, provenance and confidence — and **Export as HTML** in
  Memory → Health saves it as one file you can mail or archive, which opens years later with no
  server and no scripts in it. Settings gained the vault folder, a topology-orientation switch, the
  claim-attribution switch, and a budget for what slots may cost each turn.

- **See what a run actually built, in a browser.** When a code run leaves a dev server running in
  its workspace, the run's **Workspace** panel now shows a **Preview** block with an **Open
  Preview** link straight to `localhost:<port>`. Ports are matched to the run by which process is
  working inside its workspace, so one run never offers you another run's server, and the list is
  checked each time you open the panel — a server you have since stopped simply is not there. If
  nothing is listening the panel says so, and if the machine has no way to look it says *that*
  instead, because "your server isn't running" and "nothing checked" are different problems. Local
  only: this is your machine, with no tunnel and nothing shared.

### Fixed

- **Knowledge search's keyword fallback never returned anything, in any install.** When the smarter
  search is unavailable — no embedding model configured, or it fails — searching your knowledge is
  meant to fall back to plain keyword matching. That fallback matched two database columns that can
  never be equal, so it found nothing, ever, and the answer was reported as a successful search with
  no results rather than as a search that could not run.
  **Fixing that alone would not have been enough**, which is worth saying because the obvious fix
  looks complete. The search text was handed to the database as an *expression*, so anything but a
  single bare word was a syntax error that the same code quietly turned back into "no results". A
  hyphen was enough: searching `cold-start` failed. Both halves are fixed, and the query now goes
  through the same quoting the rest of the knowledge store already used — so the two agree instead of
  disagreeing on every input.
  **And the last-resort search took `%` and `_` as wildcards.** Searching `a_b` matched `axb`, which
  is a result you cannot explain, on the fallback that runs when everything cleverer has failed.
  Those characters are searched for literally now.

- **A loop interrupted by a restart could stay stuck "running" forever, with nothing working on it.**
  Bringing loops back after a crash or a restart was a one-shot step during startup, and if anything
  at all went wrong in it the failure was written to the log and startup carried on. There was no
  second attempt: for the rest of that session every loop the step should have picked up sat there
  saying *running*, which reads as "still working" when in fact nothing was. **Loops are now brought
  back by the supervisor that watches them, on its first pass**, so an attempt that fails is simply
  retried on the next one a few seconds later — the same way workflow runs have always been recovered.
  A loop with a workspace that went missing while you were away is still parked with a question rather
  than restarted against a folder that is no longer there.
  **Two related fixes came with it.** Starting up no longer waits for that recovery, so a restart with
  several half-planned loops is not held behind however long it takes to resume them — the dashboard
  comes up immediately and the loops catch up on their own. And a healthy loop resting between cycles
  is no longer mistaken for a dead one: it used to be possible to restart work that was perfectly
  fine, which also silently reset the approval window you had granted it.

- **Turning a tool off on the Tools page now turns it off everywhere.** It hid the tool from the agent,
  which is what it said in the code and what it did. But PersonalClaw has a second way to run a tool,
  and that one went straight to the provider without ever reading your preferences, so a tool showing a
  "disabled" chip still ran when something invoked it directly.
  **The path that mattered is scheduled work.** A cron script reaches its tools through exactly that
  route, on purpose, so it gets the same set the agent has. It was getting the set *before* your
  preferences were applied. The realistic version of this bug is a scheduled job quietly using a tool
  you turned off weeks ago. It is refused now, with a message saying where to re-enable it.
  **The primitives cannot be locked away by this.** `bash`, file reads and the platform provider are
  never disableable, and the refusal reuses the same exemption the agent's side already had rather than
  restating it — so a stray preference row cannot leave a scheduled script unable to run anything.

- **The skill-proposal queue could fill up and never empty.** When PersonalClaw notices you doing
  something repeatedly it writes up a skill and asks you to approve it. On a long-running install those
  requests had become 89% of everything waiting for your attention, and **87% of them could not be
  approved at all** — the button returned an error, permanently, and the request stayed in the list.
  **Approving one is what broke the next.** A skill is written once and never twice, so the first
  approval for a topic claimed the name and every later suggestion about that same topic was refused
  forever. But a twenty-first suggestion about your loop workflow *is* a refinement of the skill you
  already have, whatever the request is labelled — so it is applied as one now, as a small overlay
  that leaves the original untouched and can be undone by deleting one file. Requests already stuck in
  your queue become approvable again with nothing to run.
  **PersonalClaw also stops mislabelling them.** A suggestion about a skill you already have is filed
  as a refinement in the first place, so the list says "Refine a skill" instead of "New skill
  proposed", which is the truth and is what you need to decide.
  **The approved skills were invisible.** They live in a sub-folder, and the Skills page only ever
  looked one level deep — so skills that were being loaded into every conversation did not appear
  anywhere you could read or delete them. They do now.
  **And the request that announced it never cleared.** Answering a request wrote to a copy of the
  attention list rather than the live one, so the row stayed open forever, still asking for a decision
  you had already made.
  **"Dismiss all" now means all.** It skipped anything you had opened, because opening a row marks it
  as read — so browsing your queue quietly put those rows beyond the reach of the only bulk control.
- **Editing a task can no longer corrupt it.** Saving an edit wrote whatever you sent, exactly as
  sent, with no check that it was the right sort of value — because the checks all ran when a task was
  first built and an edit never re-ran them. What that let through, each measured on a real install:
  a task given a non-numeric position vanished from every screen while its file stayed on disk,
  holding its name and unreachable through the app; a task given a single label as plain text instead
  of a list took **the whole Tasks page** down into an error screen, for every task, and stayed broken
  after the offending task was deleted; a task given a number where its notes belong made the task
  **search box fail for every search**, including searches that had nothing to do with it; and a task
  given a single completion criterion as plain text had it split into one criterion per letter, none
  of which can ever be ticked, so the task could never be marked done.
  **One place now decides what each field is**, and both creating and editing go through it — they
  used to disagree, so the same value could be accepted one way and rejected the other. A value that
  can't be used is refused with a message naming the field, instead of being stored and discovered
  later.
  **Tasks already damaged by this repair themselves.** Reading a task no longer gives up when one
  field is unusable: that field falls back to its default and the rest of the task loads, so it
  becomes visible, editable and deletable again with nothing to run and no data lost beyond the
  value that was already meaningless. The task-search box starts working again on its own.

- **Scheduled automations now actually run.** Every trigger on a clock was deciding it was due,
  writing that down, and advancing its next run time — and then doing nothing. No action ever ran. The
  dashboard showed the trigger enabled with a next-run time ticking forward the whole time, which is
  the worst possible version of this: a promise kept on screen and nowhere else. Measured on a real
  install, every run in the history had been started by hand or replayed; not one had come from a
  schedule.
  **Why nothing was visible.** A fire was being handed to a mailbox belonging to a chat session that
  nothing ever opens, so it was delivered to nobody and quietly discarded. A fire does not need a chat
  session — it needs somewhere to go — so one with no mailbox now runs directly.
  **A trigger no longer jams itself for an hour.** Each fire takes out a marker saying "this one is
  busy", released when the run finishes. A fire that never ran never released it, so the trigger sat
  showing as running for a full hour, skipped every slot in between, and refused the Run button with
  "already running". Anything that ends without running now hands the marker back — including the
  deliberate case where a trigger is skipped because you happen to be mid-conversation with it.
  **Everything downstream was alive and unreachable.** Run history, the automatic pausing of a
  repeatedly failing automation, and the notification when one fails were all already built and all
  waiting on a fire that never arrived. They start working with no further change.
  **And a trigger attached to a conversation was being delivered to one place and collected from
  another.** Two pieces of code worked out where a fire should go and only one of them accounted for
  the conversation it was bound to.

- **Four places where sending the wrong kind of value did something worse than refuse it.** Each one
  already had the right answer written a few lines away, in a sibling that handles the same field
  correctly — so each of these now does what its neighbour does.
  **Editing an agent could silently switch off its tools.** Sending a single tool name where a list
  belongs *deleted the whole field* and reported success — on the built-in agent, whose file is the
  live configuration PersonalClaw reads at startup. Its two sibling routes have always ignored a
  wrong-typed list instead of acting on it. Sending an empty list still clears it, which is how you
  say "none" on purpose.
  **A malformed message crashed the chat request instead of refusing it.** Sending a number or a list
  where the message text belongs returned a bare server error with nothing to read. It's a clear
  refusal now, naming the field.
  **Reordering a chat folder crashed on a value the identical tag route quietly ignored.** Two
  controls for the same thing, disagreeing about the same input.
  **And one out-of-range number could make your whole artifacts library unreadable.** A value too
  large to be a number serialises as something that isn't valid data, and the browser rejects the
  entire response rather than the one row — so every artifact vanished from the page until the
  offending record was found. Out-of-range values are recorded as text now, so they stay visible
  without breaking anything around them.
  **Two more things while in there:** deleting an agent through this route now consults the real list
  of protected built-in agents rather than three hard-coded filenames (it covered one of five), and
  edits to the live agent configuration are written atomically and recorded in the audit log — this
  was the one path that did neither.

- **Switching an automation off now survives a restart.** Turning one off worked, and stayed off on
  disk. Then the next start-up read the old scheduling file, which still said the automation was on,
  and put it back — enabled *and* armed, so it went straight back to counting down to its next run. A
  stop button that a restart undoes is not a stop button. Whether an automation is switched on is a
  record of something a person did, so it now travels with the rest of that history instead of being
  re-derived from a file that predates the decision.
  **The subtlety that made this two lines rather than one:** the code that carries this history
  forward skipped any value it considered empty, and in Python `False` counts as empty. So simply
  listing "switched on/off" alongside the rest would have carried *on* and quietly dropped *off* — the
  one value that needed carrying. A yes-or-no answer is never missing; `False` is an answer.

- **Asking for an automation to be created switched off now creates it switched off.** The request was
  accepted, the field was dropped, and you got a live automation already scheduled for its next run.
  It is honoured now, it is not armed while off, and asking for it in a way that isn't a plain
  yes-or-no is refused rather than guessed at — because the text `"false"` counts as *true* in the
  language this is written in, so guessing would have turned the request into its opposite. When
  PersonalClaw creates one for you and tells you about it, that message also stops claiming a
  switched-off automation is "active now".

- **Restarting PersonalClaw quietly moved a chat onto a different agent.** If you had pointed a chat
  at an external coding CLI, or put it in Ask or Plan mode, a restart threw both away and the next
  message you sent ran on the built-in agent instead — with a different set of tools and a different
  idea of what it was allowed to touch, and nothing on screen to say so. A chat you had deliberately
  put in Plan mode came back in Agent mode, free to make changes. The binding was in fact being
  written to disk when you picked it; the very next turn then overwrote the file without it.
  **A chat now keeps the agent and the mode you gave it across a restart.** And if the agent you
  chose genuinely cannot be brought back, the chat says which one it could not restore and that the
  built-in agent has different tools and different limits — once, in the turn's activity, rather than
  looking like an ordinary reply. Being moved onto another agent without being told is the part that
  actually costs you something.

- **Restarting PersonalClaw mid-conversation lost what the agent had actually done.** A chat running
  on an external coding CLI came back knowing only what was written in the transcript. Everything the
  agent had learned from *doing* the work — what a command printed, what a file turned out to
  contain — was gone, so it would ask again, or guess. **A restart now picks the conversation back up
  inside the CLI itself**, with the agent's own memory of the session intact: it can still tell you
  the output of a command it ran before the restart. Three separate things had to be fixed for this,
  and none of them was the one it looked like: the stored conversation id was being thrown away at
  every start (checked against a file nothing has ever written), the request to reopen the
  conversation was gated on the same missing file, and a chat pointed at an external CLI was, on this
  one path, quietly answered by the built-in model instead — which is why a restart used to feel like
  talking to a different agent that had read your notes.
  **And when a CLI genuinely cannot pick a conversation back up**, the chat now says *"Session
  restored from history"* rather than *"Session resumed"*. It is a smaller promise and it is the
  truthful one: the conversation is rebuilt from what was written down, so what was said survives and
  what was merely *done* does not. A line claiming a full resume it did not get is worse than no line.

- **The assistant learned nothing from turns run through an external coding CLI.** PersonalClaw keeps
  a quiet record of which tools actually work for which kind of job, and leans on it later. That
  record was only ever written by the built-in agent. Run the same work through an external CLI
  (Claude Code, Codex, kiro and friends) and **nothing was written at all** — a turn with six tool
  calls in it, three of them failing, left no trace. All the learning that comes from watching your
  own tools succeed and fail was silently switched off for anyone using an external agent, which is
  most people. Those turns are now recorded on exactly the same footing as the built-in agent's.
  **A failing tool is now reported as failing.** Underneath, the reason for the silence was that the
  "this call failed" mark was being dropped in translation, one step after it was written. Two other
  things were quietly relying on it, and both start working again: a failed tool call in the
  transcript is now coloured as failed rather than looking like a success, and the brake that stops
  the agent hammering the same broken command over and over can finally see the failures it counts.
  Before this, an external agent could fail the identical call six times in a row and get no warning
  and no stop.
- **A read-only command is no longer called "destructive", and read-only tools no longer wait on
  you.** Approval cards, the tool audit log and the "approval needed" inbox notification all print
  how risky a tool call is. For sessions running through an external coding CLI, that number was
  arriving from the wrong place. Those agents announce a tool in two steps — first "I am about to run
  a shell command", then a moment later the command itself — and the risk was being decided at step
  one, when the only thing known was "a shell command". With nothing to read, it printed the worst
  case: a plain `pwd; ls` was audited as **destructive**. In the same sessions the reverse also
  happened — the approval request for a file read arrived without the "this is a read" label the
  agent had already sent one message earlier, so **Read**, **Search** and **Fetch** never qualified
  as safe and kept raising a card even with "auto-approve read-only tools" turned on.
  **The label the agent sent is now carried across to the approval request** it belongs to, and a
  command the assistant supplies inline is read there too. So a read is labelled a read and
  auto-approves when you asked for that, and a read-only command is labelled safe.
  **A command nobody could read is now labelled "caution", not "destructive"** — a card still comes
  up, so nothing runs behind your back, but the audit trail no longer asserts something nobody
  measured. **It is still blocked in Ask and Plan mode**: honest labelling is not permission.
- **The approval card now tells you which tool it is asking about.** On sessions running through an
  external coding CLI, approval cards and the tool audit trail could read just `unknown` — you were
  being asked to approve something the card could not name, and the audit log recorded
  `unknown ｜ approved` afterwards. The name was never missing; those agents announce a tool in two
  messages and some of them put the human-readable name only in the first one, which the approval
  request did not carry over. It does now, alongside the risk label. A card still reads `unknown` in
  the one case where it is true — when the agent never named the tool in either message — because a
  name that was guessed would be worse than one that is missing.
- **Ask mode no longer refuses a read-only `ls`.** Ask and Plan mode allow inspection and block
  changes, but they decide from the command text — and when that text was attached to the approval
  request rather than to the earlier announcement, it was being dropped, leaving the gate to guess
  from the tool's display name. A name like "Running: ls -la" reads as an action, so a plain
  directory listing was refused in a mode that exists to let you look around. The command is now
  read wherever the agent put it.
- **The session line said "Session created" on every single turn, and named the wrong runtime.** The
  activity line on the Loop and Code cockpits — `Session created · <agent> · <model> · via <runtime>`
  — got both halves wrong. It announced a *creation* on turn forty of the same conversation, so the
  one signal that would have told you an agent had lost its history and started over was the same
  sentence you saw when nothing had happened at all. And the runtime it named was the little
  protocol adapter PersonalClaw launches (`acp:claude-agent-acp`), not the CLI you actually chose
  (`acp:claude-code`) — or, when the adapter had to be fetched on demand, the fetching tool itself:
  `via acp:npx`, which names nothing you have ever heard of.
  **The line now says what happened.** A session that was genuinely started says **created**; one
  restored from a saved conversation says **resumed**; a turn served by the session already running
  says **continued** — a state the line previously had no word for and so reported as a creation.
  **And it names the runtime you picked**, on every turn, however that runtime was launched. Which
  matters beyond the label: the same misread name was also the key PersonalClaw used to look up a
  backend's known permission-gating limitations and to stamp its audit records, so under an
  on-demand adapter fetch a *documented* limitation could read as an unexplained gap.
- **The assistant learned "never more" as a permanent rule.** Ask it to answer in one sentence "from
  now on, never more" and it wrote itself a standing rule reading `Never: never more`, showed you
  `Learned: never more`, and kept the row forever. The word "never" was enough on its own: whatever
  followed it up to the next full stop became the prohibition, so an intensifier ("never more"), an
  idiom ("never mind the tests"), and a proverb ("better late than never") all read as hard rules.
  This was the loosest thing the assistant learned and also the most permanent one, because a "never"
  rule is stored as a lesson — the place standing always/never rules live — while an ordinary style
  preference is stored as a preference that fades unless you repeat it.
  **A "never" is now only learned when it prohibits an action** — the words after it have to name
  something to not do. Real rules are unaffected: "never force-push to main", "don't ever delete my
  notes", "never commit secrets to the repo" are all still learned, and a genuine rule stated after a
  false one in the same message is now picked up where before the fragment won and stopped the search.
  Quantity phrasings like "never more than one sentence" are deliberately not treated as
  prohibitions; say it as a preference ("keep responses short") and it is learned as one.
- **A reasoning-effort setting that the coding CLI cannot honor is now refused instead of
  silently stored.** Some external coding CLIs report that they have no reasoning-effort
  control at all, and the composer already hides the pill for those. The API did not agree:
  it accepted an effort anyway, saved it onto the session and read it back afterwards, so the
  setting looked applied when nothing would ever act on it. Both places that set it now check
  what the runtime actually declared and refuse anything outside it, naming the runtime and
  the options it does offer. The opposite case is fixed too — a CLI offering its own value
  (say `xhigh`) had it rejected, because the check compared against a fixed
  low/medium/high/max list instead of the CLI's own. Clearing the setting is always allowed.
- **A reasoning-effort setting no longer quietly lapses partway through long-running work.**
  Some external coding CLIs treat a session as finished after one turn, so for long jobs the app
  opens a fresh session per cycle. It re-applied the agent, the model and the permission mode on each
  one but not the reasoning effort — so from the second cycle onward the work continued at the CLI's
  default effort while everything on screen still said otherwise. It is re-applied now. The same
  path also skipped draining the tool-server startup messages before beginning the turn, which is now
  done too.
- **A tool blocked by Ask or Plan mode no longer ends the whole conversation.** When one of these
  modes refused a tool on an external coding CLI, the turn could stop dead — the assistant's reply
  became "*Conversation interrupted*", the model never learned its tool had been refused, and you
  never got the short "(Ask mode — only read-only tools run)" explanation. The cause was the message
  sent back: refusing a tool reported *the whole turn was cancelled* rather than *this one tool was
  declined*, so the CLI reasonably stopped. A refusal now says exactly that, using the CLI's own
  "reject" choice, so the assistant can acknowledge it and carry on — which is already how it behaves
  when you press Reject on an approval card yourself.

- **The context gauge said "0%" on turns that were nearly full.** The little ring on the model pill,
  and the "Turn complete" line under a finished turn, both printed a context percentage on every
  turn — including turns where the agent behind the chat had never reported one. The number they
  printed was zero, so a session carrying a large amount of context read as completely empty. A
  driven session showed `context 0%` on fourteen consecutive turns while the window was filling up.
  A gauge that states a number it was never given is worse than no gauge: it invites you to keep
  going right up to the point where the conversation gets truncated.
  **An unmeasured context now shows nothing at all** — no ring, no percentage — rather than zero.
  **A genuinely empty context still shows 0%**, because that is a real answer and hiding it would be
  the same mistake pointed the other way. This also fixes two decisions that were reading the fake
  zero: the background session was recycled as "no readings ever arrived" when it had in fact
  measured an empty window, and the automatic-compaction check no longer treats an unknown gauge as
  a low one.
- **Typing `/compact` at a coding-CLI agent killed the whole turn.** Any message starting with a
  slash was sent as a *command* to whichever agent was bound, without ever asking whether that agent
  understands commands. None of the three CLIs we drive does, so the answer came back "Method not
  found" and the turn died on an error card — you got no reply at all, and `/compact` compacted
  nothing. A slash command now goes out as a command only to an agent that says it can run one;
  otherwise your message is answered as an ordinary question and an inline line tells you the
  command was not run natively, so you are never handed a plain answer while believing a command
  executed. If a command does fail as unknown *after* the agent has already started replying, the
  turn stops and says why rather than silently starting a second one — re-asking would duplicate the
  reply you can already see and bill the work twice. Any other command failure still surfaces as the
  failure it is.
- **The sign-in page said "Sign-in failed" no matter what went wrong.** It read the error out of the
  wrong place in the response, so a wrong password, a rate-limited address, a password-sign-in that
  is switched off and an already-used device code all produced the same unhelpful sentence — and the
  field for a two-factor code could never appear when the server asked for one, because the branch
  that reveals it never ran. Each failure now says what it is.
- **The audit log's "Failed" filter hid most failures.** Settings → Audit log offers outcome filter
  pills, and they were defined in the dashboard as two literal words — `denied` and `failed` — while
  the code that writes the log uses sixty-two different outcome words. So "Failed" matched only the
  four places that happen to write `failed`, and missed every `failure` and every `error`; "Denied"
  missed `rejected`, `blocked` and `refused`. On a tamper-evident record of what your agent did, a
  filter that quietly leaves matching entries out is the worst possible failure — you read the empty
  list as "nothing went wrong". Found by driving the panel: a real terminal-session delete that had
  recorded `error` was invisible behind the Failed pill. The pills now cover whole families of
  outcomes, and the families are defined next to the log itself rather than in the dashboard, so a
  new outcome word reaches the filter without anyone remembering to update the UI. Each pill names
  the outcomes it covers on hover.

- **Setting a chat's working directory with a mistyped field no longer silently unbinds it.**
  `POST /api/chat/sessions/{id}/workspace-dir` read a missing `workspace_dir` key as "clear it", so a
  body like `{"dir": "/some/path"}` answered `{"ok": true, "workspace_dir": ""}` and left the session
  with no working directory at all — while the caller had every reason to think it had just set one.
  For a chat bound to an external agent CLI that binding decides where the CLI actually runs, so the
  agent would go on to read and write in whatever directory the host resolved instead. The request is
  now refused with a 400 that names the one deliberate way to unset it (an explicit empty string,
  which still works). Found by driving a real `acp:kiro-cli` session, not by reading the code.
- **Settings → Prompts named four of its forty-four rows.** The panel lists every bindable runtime
  context — the prompt that serves chat, the one that writes a conversation title, the one that turns
  "every Tuesday at 9" into a cron expression, each loop judge and planning brief. Four of them had a
  human name and a description; the rest showed their internal key (`nl_to_cron`,
  `history_compression`, `cycle_judge_skeptic`) with no explanation of what binding it, and a screen
  reader announced the picker as "Prompt for nl_to_cron". They also arrived as one undifferentiated
  list of forty-four. Every context now names and describes itself, and the rows are grouped — agent
  system prompts, internal task prompts, loop and orchestration prompts, evaluation prompts — using
  the grouping the bundled-prompt catalog had already declared for this exact purpose and never sent
  to the dashboard. The description comes from the prompt catalog itself rather than a table in the
  dashboard, so a context added later (including one contributed by an installed app, which is where
  four of these rows come from) arrives already described instead of appearing as a bare key.

- **A scheduler tick wrote its history into the wrong PersonalClaw home.** Two writers on the tick
  path — the suppressed-fire ledger row and the hourly rate meter that reads it back — built their
  run store from the *active* home instead of the home the tick was actually running under. Anything
  that drove a tick against an isolated home (an ad-hoc script, a dev drive, a diagnostic) therefore
  appended its rows to the real `~/.personalclaw/cron-history/`, and then measured its rate caps
  against that same foreign history — so a cap could be satisfied, or exhausted, by fires belonging
  to a different install. Both now resolve through one funnel rooted at the tick's own home, matching
  the rule already documented for trigger claims: a record describing one store must not live in
  another. No behaviour change for the gateway, the CLI or the dashboard, where the two roots were
  always the same directory. If you have driven ticks outside the normal gateway, your real
  `cron-history/` may hold stray rows for job ids you do not recognise; they are inert bookkeeping
  and safe to delete once you have checked them.
- **An agent CLI could run in your home directory instead of the folder you gave the chat.** Picking
  an agent for a chat used to run its CLI in a hardcoded `~/.personalclaw/workspace` rather than the
  working directory bound to that session — so files landed outside the folder you chose, and a
  gateway or test started with `PERSONALCLAW_HOME` pointing elsewhere still wrote into the real home.
  The session's working directory now reaches the process, agent discovery uses the configured
  workspace, and an agent that declares no default directory of its own no longer relocates a chat
  you had already pointed somewhere. An agent that *does* declare one still opens there, unchanged.
- **A "Pre tool use" hook that blocks nothing now says so.** Only a lifecycle hook an agent's
  trigger list references can reject a tool; an unreferenced one still runs, but its exit code is
  read by nobody. Both looked identical on the Triggers page — and the unreferenced one was the more
  convincing of the two, because it fired on every tool call and its run count climbed. Measured: a
  hook exiting 2 to deny a file write fired three times while all three writes landed. A blocking
  hook now carries **Not enforcing** or **Enforcing** wherever it is listed, the run count on an
  unarmed one is labelled advisory, and the reply to creating one says which state it starts in.
  Hooks on the other lifecycle events are unchanged: they have nothing to arm, so they are not
  badged. Nothing about when a hook blocks changed — only whether you can tell.
- **A chat with no folder set could run its agent CLI wherever the app itself happened to be
  started.** If no workspace directory resolved — none configured, or the configured one turned out
  to be missing or a credential folder that is never usable as a workspace — a chat you had not
  pointed anywhere fell back to the app's own current directory, which on a service install is
  whatever the system launcher chose. Files the agent wrote landed somewhere you had no way to find.
  Such a session is now refused before the CLI starts, with a message naming what to set and where.
  A chat that already has a working directory, and one whose workspace resolves normally, are
  unaffected.
- **Tinted "chip" buttons had unreadable labels in six of the twelve colour schemes.** The label
  colour on a tonal button (the primary-tinted CTA — "Open Chat" on the dashboard is one) was a fixed
  coral, but the tint underneath it is painted from whichever scheme you picked. So the label's
  contrast depended on a scheme it knew nothing about: in dark mode Mono, Amber, Phosphor, Jade,
  Honey and Forest all fell below WCAG AA at rest, worst 4.07:1 against a 4.5 floor, and once the
  hover tint is counted the default coral scheme missed it too. The label now takes the active
  scheme's own accent shade instead of a frozen value, so it follows the scheme — including a custom
  primary you pick yourself. **Visible change:** tonal button labels are a shade lighter in dark mode
  and a shade deeper in light mode, in every scheme including the default. A new rail composites the
  translucent pair over all 12 schemes × 2 modes × 3 surfaces × {rest, hover} and fails the build on
  any combination below AA, which is what was missing — the old guard only checked the opaque
  filled-button pair.

- **"Unattended runs need a verified adapter" only covered one kind of unattended run.** The switch
  promised to refuse background work onto an external agent CLI whose ACP adapter has no verified
  provenance, and it did — for a subagent. A cron fire, a loop-cycle worker, the background session,
  an inbox or side sweep, a channel delivery and a trigger dispatch all went through unchecked,
  because each one had to volunteer that it was unattended and only one of them did. Whether a spawn
  is unattended is now decided from the session itself, using the same rule the safety profiles
  already use, so all of them are covered without opting in. Interactive chat is still never gated.
  If you have this switch on and an unverified adapter, background runs that used to launch will now
  be refused by name — that is the switch doing what it said.

- **Settings → Agents could show you a stale runner reading as if it were current.** A runner row
  printed "healthy, v2.1.4, 58 ms" with no indication of when that was measured, so a check from last
  week looked exactly like one from a minute ago. Rows whose measurement is older than the new
  **Runner health check interval** (Settings → Agent defaults, default one hour) now say **check
  overdue** next to the reading instead of quietly presenting it as the present state. A runner that
  was never probed still says so — it is not reported as overdue.
- **A provider that rides a CLI subscription could look signed in and still fail.** For a model
  provider whose vendor bills by subscription — no API key to paste, just an agent CLI you already
  signed into — one of the two ways such an app gets built never looked for that sign-in, so the
  provider came up holding a placeholder secret and every request failed as unauthorized with your
  login sitting right there. Both paths now resolve the credential the same way, from the same code.
  **Test connection** can see the sign-in too: a signed-in provider is genuinely probed, and a
  signed-out one is told to sign in — where it used to print the truncated "No API key configured
  (set it or )", naming an environment variable a subscription app deliberately does not have.
  Finally, restoring a conversation no longer quietly swaps your pinned model: a provider app that
  serves a model family is now recognized as serving it instead of only the providers built into
  PersonalClaw.
- **"Possible duplicates" only looked at your 25 newest items.** The panel that tells you a second
  copy of a knowledge item exists compared it against a handful of the most recently added items of
  the same kind — so the case it exists for, an old copy and a new copy of the same document with a
  grown library in between, was the case it could not see. It said nothing, which looks exactly like
  having no duplicates. It now checks every item in the library: titles are compared first (cheap),
  and the expensive content comparison runs only on what a title match survives — on a
  2,000-item library the whole check takes about 15 ms and finds the duplicate, where the old
  25-item version took 6 ms and found nothing. Candidates are also listed strongest match first, and each one now says
  how similar it actually is ("Same title · content similarity 0.99") instead of repeating one fixed
  phrase for every match, weak or near-identical.
- **An export could carry the same database twice, and one copy was the unsafe one.** Databases
  that live inside your workspace (the knowledge and lexicon stores) were written into a portable
  export twice: once as a proper checkpointed backup, and again as a plain file copy taken while the
  store was open. The plain copy landed second, so unzipping overwrote the good copy with it, and the
  `-wal`/`-shm` working files travelled alongside. Worst case, when a database was too damaged to
  back up safely, the export said it was skipping that store and then shipped the raw copy anyway,
  listing it in the archive's manifest as if it had been verified. Each database now leaves through
  the safe backup path or not at all, and an archive's manifest again matches what is inside it. A
  database you put in your own workspace yourself still travels as before.

- **Your ready-task list was in no particular order.** The list behind **Tasks → Ready** (and the
  same list an agent reads when it asks what to work on next) came back in whatever order the store
  happened to return — most-recently-updated first — so a task due today sat below one due next
  month, and a task blocking three others sat below one blocking none. A due date you had set was
  read by nothing at all. Ready work is now ranked the way you would expect: priority first, then
  how many other tasks it unblocks, with an extra bump once it is overdue, and ties broken
  consistently so the order is stable between refreshes. Nothing about how tasks are stored
  changed, so no existing task moves except in where it appears in the list.

- **Forking a conversation could cut it earlier than the message you clicked.** In a chat with
  tool calls or multi-part answers, the fork was measured by the position of the bubble on
  screen rather than the message in the transcript, and those drift apart — so you got a
  plausible-looking copy that quietly stopped short, sometimes before the answer you were
  forking from. It now cuts exactly where you clicked.

- **A "replace everything" restore could run while the app was running.** Over the web API it was
  supposed to refuse and, on any port other than the default, it silently didn't — it checked whether
  something was listening on the port it had been *configured* with rather than the one actually in
  use. It now refuses outright, because the request being served is proof the app is up. Your previous
  state was never lost either way: a replace moves what it displaces aside before writing.

- **Buttons inside a generated widget now work everywhere you can see the widget.** A widget the
  agent builds can carry real controls, and clicking one used to only do something while you were
  looking at it inside a conversation — the same button in an artifact's preview or on a dashboard
  tile quietly did nothing. Now it opens a chat and your click arrives as the first thing you said,
  form fields and all; pressed inside a conversation it still answers in that conversation rather
  than starting a new one. A widget still cannot press its own buttons — only a real click counts —
  and it cannot pad your message either: anything past 16 KB is cut with a visible …truncated so you
  can see it was shortened.
- **Home, the Inbox and Discover now arrive in sequence instead of all at once.** Their sections
  fade and rise in one after another, about 44ms apart, so a page reads as composed rather than as
  eight widgets landing in the same frame. It costs you nothing: everything is on screen and
  clickable from the first frame — the movement only decorates the arrival, it never holds content
  back. How much cascade there is follows the Expressiveness slider in Settings › Design (calmer is
  tighter, not dead), and if your system asks for reduced motion there is no cascade at all — not a
  faster one. It also plays once, when you arrive at a page, so a refresh, a new inbox item or a
  filter change never makes the page flicker through it again.
- **A guided tour of the app, and you can take it again whenever you like.** The last screen of
  setup now offers a quick walk through the five places that matter: the sidebar, chat, the Inbox,
  where anything risky waits for your permission, and Settings. It happens on the real app — each
  stop dims the page and puts a ring around the actual thing it is talking about, then takes you to
  the next one — so you are being shown your own dashboard, not a slideshow of screenshots. Escape
  ends it at any point and so does clicking anywhere outside the card, and what you are left with is
  the app, fully working, back on the page you started from. Nothing about it is recorded: there is
  no progress saved, no "you have seen this" flag, and no step reports anything anywhere, which is
  also why it can simply be replayed instead of resumed. To take it again, open Discover — there is
  a "Replay the tour" card at the top that cannot be dismissed away, so it is still there after you
  have dismissed every tip on the page or turned tips off entirely. If your system asks for reduced
  motion the spotlight stops pulsing and the card stops sliding; nothing is slowed down, it is just
  still.
- **Empty pages now explain themselves and give you something to press.** Every one of the seven
  main surfaces — Loops, Workflows, Knowledge, Memory, Skills, Tasks and Triggers — says what the
  thing it holds actually is when you have none of it, and offers the one obvious next step
  instead of a blank column. Workflows was the worst of them: two empty screens that told you to
  go find a control somewhere else. Now "No workflow runs yet" hands you a button that takes you
  to the definitions, and an empty definitions list starts you from a template. The Memory panel
  had no real empty states at all — just a few grey sentences — and now matches the rest of the
  app, with a way to add your first fact right there. A page you have merely filtered down to
  nothing keeps the explanation but drops the create button, because offering to make your first
  task when you already have ninety is just noise.
- **The audit log can now answer "what did my agent actually do?" — and tell you which record was
  tampered with.** Settings → Audit log used to hand you the last 200 events and filter them in
  your browser, so anything older was simply unreachable. It now pages properly: you get the most
  recent events, a "Load older events" button walks back through the whole log, and the filters
  (operation, caller, downstream service, and a from/to date range) run on the server, so they
  search everything rather than only what happened to be on screen. Paging is stable while your
  agent keeps working — new events landing mid-scroll can no longer make a page repeat rows or,
  worse, quietly skip them. Each row now carries its own tamper check: if a record was altered on
  disk after it was written, that row is called out on its own and counted in a banner at the top,
  and "Verify" tells you how many of how many events still check out. Nothing here leaks
  credentials — a token that appeared in a command shows as `[REDACTED: credential]` while the
  rest of the command stays readable, so an entry is still useful for working out what happened.
  Finally, "Export" downloads exactly what you are looking at as a `.jsonl` file, one event per
  line, safe to attach to a bug report and readable by anything that speaks JSON. Reading this log
  is yours alone: an installed app can never fetch it, even one that asks for it.
- **Artifacts are now findable from knowledge search.** Anything written into a text artifact —
  markdown, HTML, plain text, JSON, CSV — becomes searchable in Knowledge, so an answer you or the
  agent wrote into an artifact last month turns up in the one place you look for what you know.
  Artifacts stay in the Artifacts library and are **never listed as knowledge items**: they only
  appear as search results, labelled "Artifact" with a link straight back to the real thing, so your
  library's counts and lists do not double. Editing an artifact refreshes what search finds, renaming
  it refreshes the title, and deleting it removes it from search entirely — no leftovers. Indexing is
  **local**: a mirrored artifact never reaches a model, and any credential in an artifact's body is
  stripped before it is indexed. If you already have artifacts, they are indexed once the first time
  this runs and never re-indexed on later restarts. Widgets, React and SVG artifacts are left out on
  purpose — their bodies are code, and indexing them would bury your notes under variable names. The
  whole thing is one switch in **Settings → Sources → Artifacts** (on by default) and takes effect
  without a restart.
- **First run now picks up where you left it, and you can walk out of it at any point.** Reload
  the page halfway through setup and you come back to the step you were on rather than the
  beginning — the apps you installed stay installed, and a card you already ran still counts as
  your first success. You do have to type your name again, because your name is only saved at the
  end and inventing one for you would be worse. Every step also has a way out now: one link under
  the stepper leaves setup and drops you straight into a working dashboard, and if you leave
  before telling us your name it says which name it will use so nothing is renamed behind your
  back. Everything you skipped is still in Settings, and skipping never takes a feature away.
  The last screen finishes the job: it recaps what actually happened, then hands you the three
  things worth knowing on day one — where work comes back to you (with a link straight to the
  Inbox instead of the dashboard), the live dial that decides how much the interface moves, and a
  switch that opens the whole sidebar from the start if a short one is not for you. Those are the
  real Settings controls, not previews, so moving them here sticks. And `personalclaw setup` now
  says where the guided setup lives — one line, and only when you are somewhere a browser can
  actually open it.
- **Moving between pages now crossfades instead of cutting.** Clicking a nav item — or going
  back and forward in your browser — fades the old page into the new one on the same curve the
  rest of the app moves on, so navigating feels continuous rather than like a hard cut. It is
  purely cosmetic and deliberately cannot get in the way: the address bar and the page itself
  change immediately whether the animation runs, fails, or is not supported by your browser at
  all, so nothing is ever waiting on a fade. If your system asks for reduced motion there is no
  fade at all, just the instant swap. Opening a detail panel, switching a tab, typing in a search
  box and following a redirect all stay instant on purpose — those are refinements, not
  navigation, and fading the page under them would fight what you were doing.
- **The two shipped personalities now arrive with their own motion and their own tone.** Picking
  **Claw Arcade** in Settings → Design also sets the backdrop to sparkle dots on an offset lattice
  and turns the motion language up to its bold end; **Retro Terminal** does the opposite on the same
  dials — square dots on a straight grid, and springs flattened to no overshoot at all. If you have
  sound cues switched on, a finished turn in Claw Arcade is an arcade coin and an approval in Retro
  Terminal is a terminal bell; the panel tells you which moments an identity re-voices, so a
  different tone is never a mystery. Everything here stays **yours to change**: each dial lands in
  your own Appearance settings, so moving a slider afterwards sticks, and switching back to
  PersonalClaw puts every dial back to its default rather than leaving one pinned. Sound is still
  off until you ask for it, still silent in a background tab and under Reduce Motion, and the
  sparkle backdrop is a single still frame — never a moving one — when your system asks for reduced
  motion.
- **You can show the assistant your screen for one message — off until you turn it on.** Settings
  → Chat has a new **Share screen in chat** switch. It ships **off**, and while it is off there is
  no control in the composer *and* the server refuses a frame outright, so nothing can start
  sharing your screen by asking. Switch it on and the composer grows a **Share screen** button: your
  browser's own dialog picks the screen or window, your browser's own indicator stays lit the whole
  time, and PersonalClaw adds a pulsing **Sharing screen** chip in the chat header so you can also
  see which conversation is being shown frames. One frame is captured at the moment you send a
  message — not a stream — it is held in memory for that single turn, never written to disk, and
  dropped as soon as it is used; sending a second message replaces it rather than piling up. If the
  model you are talking to cannot read images, a vision model describes the frame instead and the
  turn says so, so you always know whether it saw pixels or a description. Stopping the share (the
  chip, your browser's stop button, or closing the tab) clears everything immediately. If you want
  to keep a frame, "+" → **Pin shared frame** saves it as an ordinary attachment — the only way one
  ever reaches your disk, and it is refused in a temporary or incognito chat.
- **You can snip a region of your screen into a message, on any platform.** "+" → **Capture
  screen area** now works everywhere, not just on a Mac. This is a different thing from **Share
  screen** above: sharing shows the assistant a live screen for one turn and keeps nothing, while a
  snip produces an ordinary PNG attachment you can see, remove and send — the same chip a
  dragged-in file gets, read the same way. On macOS it still uses the system snipping tool, which is
  the better crosshair; everywhere else your browser asks which screen or window to capture, exactly
  **one frame** is taken, and the capture is **stopped before you crop** — so nothing keeps
  watching your screen while you decide, and your browser's own indicator goes out immediately. Then
  you crop it in the app: drag a region, or use the arrow keys (hold Alt to resize) with the whole
  capture selected to begin with, so there is nothing here you can only do with a mouse. It tells you
  the exact pixel size you are about to attach. Escape cancels and leaves nothing behind — no
  attachment, no file on disk. Where a browser cannot capture a screen at all (iOS Safari) the menu
  item is simply not there, rather than there and broken. One fix rides along: a capture taken with
  the macOS tool is now actually **read** on send. Its chip has always been visible, but what was in
  it never reached the assistant — it does now, the same as any other attachment.
- **Optional sound cues, off until you turn them on.** Settings → Design → Personality has a new
  switch that gives you a brief tone when a turn finishes, when a tool approval needs you, and when
  something fails — nothing else. It ships **off**, and even switched on it stays quiet while the tab
  is in the background or while your system asks for reduced motion. The tones are generated in the
  browser, so no audio file is downloaded and none ships in the app.
- **First run now ends with three things you can actually do, not a tour.** A new **Try one** step
  saves a note and asks your own library a question, sets a real 9:00 AM reminder and fires it once
  so you see what it will say, and starts a real one-cycle loop — each showing what actually
  happened rather than a preview, each finished in about a second, and none of them spending a
  single token. Skip any or all of them. If a call fails, the card shows the exact error the server
  gave and offers a one-click jump to the Settings panel that owns it.
- **A knowledge item now has a reading mode, and a passage you highlight in it stays
  highlighted.** Open anything with a text body from Knowledge and press **Reading mode**: the
  article gets the column, real editorial type instead of metadata-sized text, and a ring showing
  how far through it you are alongside a rough reading time. Select a passage and press **Highlight
  selection** (or the pill that appears at the selection) to keep it, with an optional note about
  why it matters. Your highlights are marked in the text when you come back, and they are also
  listed under **More details**, so they are on the item whether or not you are reading it. If you
  later edit the body out from under a highlight it stops being marked but is never thrown away,
  and merging two copies of an item keeps the highlights from both.
- **You can now see which local models are eating your RAM, and free one.** Settings → Models
  grows an **On this machine** section — a memory bar for the whole machine and a row per model
  that is actually loaded right now, each with an **Unload** button — and the dashboard gets the
  same band. The useful part is the attribution: a model stays in memory after you bind a
  different one, and those rows are marked *not bound* and sorted to the top, because they are the
  ones worth reclaiming. Unloading is safe and repeatable; the model loads again the next time
  something needs it, and the bar moves so you can see the memory actually came back.
- **A model provider can now run in its own process, so a crash in a native library can no longer
  take the gateway down with it.** An app opts in with `"execution": "sidecar"` in its manifest
  and gets its own Python environment for its heavy dependencies — no more waiting for a gateway
  restart after installing them. If the child process dies mid-request you get a clear typed
  error instead of a hung page, the next request starts a fresh one, and search keeps working
  without restarting anything. In-process stays the default for every existing provider; nothing
  changes unless an app asks for it. Installing a sidecar's environment is a resumable background
  job: if it is interrupted, re-running picks up from the step that failed rather than starting
  over, and it tells you the one thing to do about a failure rather than only what broke.
- **A new install now opens on a short sidebar that grows as you use the app.** Instead of
  eighteen destinations on day one, a fresh setup shows five — Home, Chat, Inbox, Store and
  Settings — plus an **Everything +13** row at the bottom of the list. Nothing is locked away:
  open any other surface from a link, from search (⌘K) or from Discover and it renders exactly as
  before *and* joins your sidebar for good, so the rail ends up matching what you actually use.
  One click on **Everything** shows all of them permanently, and the same row (or
  **Settings → Design → Navigation → Show every surface**) puts it back — turning it back off
  keeps every surface you had already opened. **If you are upgrading, nothing changes:** an
  existing install keeps its full sidebar, because the short rail only ever starts for a setup
  that ran the first-run flow. The preference is per browser, like your theme and sidebar width.
- **You can now open your memory in Obsidian and edit it there.** Settings → Memory → **Memory
  vault** replaces the old on/off toggle with three choices. **Mirror** writes memory out as a
  browsable markdown vault — a page per fact, per episode and per person/project/tool it knows
  about, wired together with `[[wikilinks]]` so Obsidian's graph view works — and regenerates it
  from the store, so hand edits are overwritten. **Two-way** reads your edits back: change a fact
  page above its `personalclaw:generated` marker, hit **Sync now** (or just finish a session), and
  your version replaces what was stored — your edit wins even over a fact you originally typed in
  the dashboard, and it shows up in the memory event log so you can undo it.
  Nothing is merged on a guess. Each page carries a hash of its own body, so the sync knows exactly
  which pages you touched; if it cannot read your edit with confidence — the heading is gone, the
  page is empty, or it is an episode, which stays read-only because evidence should not be
  rewritten — it **leaves your text exactly as you wrote it**, marks the page `sync_conflict`, and
  lists it under Settings → Memory → **Health** alongside broken links, pages it does not recognise
  and edits still waiting to be read back. Person/project pages keep a compiled summary on top and
  an append-only timeline below that no sync ever reorders.
  Drop a document in the vault's `raw/` folder and the next sync files it under **Knowledge**, never
  into memory. `personalclaw snapshot` now includes the vault, so an edit you have not synced yet is
  backed up with everything else.

- **An empty Triggers page now offers four working starters instead of a blank form.** Morning
  briefing, weekly digest, nightly check and a standup reminder each show their cadence in your
  own locale's clock, and picking one opens the ordinary create form already filled in — review it,
  change anything, save. The cards are fully keyboard-operable, and the blank "New trigger" path is
  exactly where it was.
- **PersonalClaw can now watch things for you, and new entries land in your library on their
  own.** Knowledge → **Sources** is a new page where you point it at three kinds of thing: a
  **web page** (a changelog, blog index, category or newsroom page), a **feed** (RSS, Atom, JSON
  Feed or a CSV export, with ready-made recipes for Hacker News and GitHub), or a **folder on this
  machine** (new and edited files are indexed; a deleted file's item is archived, never destroyed).
  Each source polls on a cadence you pick and every new entry becomes an ordinary knowledge item —
  searchable, in the graph, pickable with `@` in the composer — with no extra wiring.
  For a web page, paste the URL and press **Preview** first: it runs the detection once and shows
  you the items it *would* save and which detector found them, so you can tune the stack and only
  then save. **No model is involved in any of this** — detection, parsing and de-duplication are
  plain deterministic code, so a source costs zero tokens to watch. If you want that guarantee to
  extend all the way through ingestion, set a source to **Raw**: its items get indexed and embedded
  locally and never reach a model at all, and the source is labelled **no AI** wherever you see it.
  The same story arriving from two sources becomes ONE item carrying both attributions, and polling
  the same feed twice adds nothing.
  When a source stops working the page tells you which fix to try, because the two common failures
  need **opposite** answers: a page that rendered fine but yielded nothing is usually the wrong URL
  (auto-detection reads pages that LIST entries, not homepages or single posts) and you get a field
  to point it somewhere better; a page that builds itself with JavaScript needs the render tier and
  you get a one-press button to allow it. Each row also shows when it last ran, how many entries
  arrived, whether it had to climb to the expensive fetch tier, and when it will check again — and
  you can pause any source without losing what it has already collected.
  Every fetch goes through the same guarded egress path the rest of PersonalClaw uses (host
  classification, private-address denial, per-redirect re-checks, byte caps and a per-poll request
  budget), a watched folder is refused if it points at a sensitive location, and scraped content is
  sanitised at extraction and fenced at every model boundary.
- **App installs now check who published the bundle, not just what's in it.** A publisher can sign
  an app bundle, and the Store verifies that signature on the staged copy **before** the security
  scan and before anything reaches your app tree — so a bundle whose contents don't match its
  signature never gets as far as running its install hook. The install-consent surface now says
  which it is: "Signed by PersonalClaw", "Unsigned — community tier", or a refusal that names the
  file that doesn't match. The signature covers **every file** in the bundle, not just its manifest,
  so swapping a script after signing is caught. Unsigned apps install exactly as they always have,
  at community tier — signing earns extra trust, it is not a new wall. An *invalid* signature is
  refused outright and cannot be clicked through, because a tampered artifact isn't a risk you're in
  a position to accept. Maintainers sign with `scripts/sign_app.py`; the scheme, the rejected
  alternatives and the workflow are in `docs/security/signing.md`.
- **You can install the phone companion to your home screen.** PersonalClaw now ships a web-app
  manifest and a service worker, so the approvals companion opens from a home-screen icon as its own
  full-screen app instead of a browser tab, and its shell still loads when the connection drops. What
  it will never do is show you stale data: API responses are never written to, or served from, the
  offline cache — so an approval you see on the phone is one that is genuinely still waiting, not a
  copy of one that already timed out. Installing requires reaching your gateway over `localhost` or an
  https tunnel, because browsers only run service workers in a secure context; over a plain-http LAN
  address the dashboard works exactly as before and says why installing is unavailable.
- **Memory now decides what to do with a new fact instead of just piling it on.** When a session is
  consolidated, PersonalClaw first collects the facts it extracted, then looks up what it already
  knows that might collide with each one, and only then decides per fact: add it, update the
  existing entry, retire the entry it contradicts, or do nothing because it is already known.
  **Retiring is never a delete** — the old entry stays readable with a pointer to what replaced it,
  and the change is in the memory event log, so a wrong call is recoverable. When it genuinely
  cannot tell which of two contradictory facts is true, it **keeps both and tells you**: the pair
  shows up as an undecided contradiction in the memory Health checks rather than being averaged
  away into one confident-sounding answer. Adjudication costs one extra cheap model call, and only
  when something actually collided; if that call fails, every fact is still saved exactly as before.
- **Memory can record whose claim something is (opt-in).** Turn on "Attribute Claims to Who Said
  Them" and a fact that is only true because somebody said it is stored as a claim with its holder —
  you, the assistant, a named person, or an outside source — and injected into context that way
  ("Alex believes…, weight 0.40") instead of as established fact. Second-hand claims are capped
  lower than first-hand ones, and a lower-authority claim can never retire something you stated
  yourself. Off by default; with it off, every memory is stored unattributed exactly as before.
- **An optional topology block orients a new session in your memory graph.** "Topology Orientation
  Block" groups your linked entities into neighbourhoods and shows the biggest few (with their
  leading entities) at the start of a new session, so the assistant knows which areas exist before
  it searches. Costs a few hundred characters of context and stays off by default; the grouping is
  fully deterministic, so the same graph always produces the same neighbourhoods.
- **Papers now ingest as papers.** Drop a PDF into the Knowledge Library, or save an arXiv link, a
  DOI or any `.pdf` URL, and PersonalClaw reads its shape rather than just its words: it detects the
  sections (abstract, introduction, method, results, discussion, conclusion, references) and stores
  three purpose-cut views beside the full text — a *brief* for "what is this claiming", a *body* for
  "how did they do it and what happened", with the bibliography stripped out of both, and a *meta*
  cut of the front matter. The bibliography itself is parsed into references keyed by arXiv id, DOI,
  title or author-and-year, so a paper arrives with its citations already listed. All of it is plain
  deterministic parsing: the same file always yields the same sections, and no model is involved at
  any point. Saving an arXiv or PDF link used to run the HTML page-scraper over PDF bytes and store
  the resulting noise; it now fetches the document itself. Originals are cached by content hash, so
  re-saving the same paper, or regenerating an item, costs no network at all.
- **An app can now teach PersonalClaw to watch a source it has never heard of — by shipping a
  parser, not a client.** A connector pack is an ordinary app that declares which URL to fetch
  (a template, plus which of your saved credentials to send as a header) and a small script that
  turns the response into knowledge items. PersonalClaw does the fetching itself, through the same
  guarded network path everything else uses, and hands the downloaded body to the pack's script on
  standard input. **The script never gets a network connection of its own** — it cannot open a
  socket, start a process, or call into system libraries, and if it tries, the whole batch is
  thrown away rather than partly imported. The same is true of a script that misbehaves by
  accident: garbage output, a run that stops halfway, or an item missing the fields needed to
  recognise it again produces zero items and a specific reason, never a half-imported feed that
  looks like a source that went quiet. Packs are bounded by a time limit and size caps, and a
  pack must ask for network permission in its manifest, so what you are agreeing to is visible
  at install.
- **Watching a site you have a link to now starts with "we already know this one".** The
  Sources create screen opens with a box for the URL you already have: paste it and PersonalClaw
  checks a bundled directory of site recipes before you touch a single setting. It covers GitHub
  releases and trending, Hacker News, PyPI project releases, a subreddit, a Substack newsletter,
  and generic changelog/release-notes pages. Pick a match and the form arrives filled in from the
  URL you pasted — a GitHub repository link becomes its releases feed, and the screen shows you
  that it will be watching the feed rather than the page you typed. If nothing covers your URL it
  says so plainly and you carry on choosing a kind yourself, exactly as before.

- **A voice is now a thing you own, not a dropdown value.** Voice profiles hold a name, the
  engine that renders them, a reference clip, a pinned seed and a spoken-consent record, and you
  can bind a different one per surface — one voice in the web dashboard, another for a Slack
  channel, another for a specific agent — with an explicit "speak as this one" request always
  winning over any binding. When you hear a generation you like you can lock it: PersonalClaw
  copies that clip and pins its seed, so the voice stops being a lottery until you unlock it
  again. Reference and consent clips upload resumably, so a long clip that stalls picks up where
  it left off instead of starting over, and a half-finished upload is never treated as a usable
  clip. Two guarantees are structural rather than promised: consent for a cloned voice is
  re-derived from the recording on disk every single time it is read — editing the saved flag by
  hand proves nothing — and revoking consent immediately stops that voice's audio from being
  served back out, with every consent record/verify/revoke written to the security audit log
  (ids and verdicts only, never the recording or what you said). If you create no profiles,
  nothing changes: speech resolves exactly as it did before.

- **Proposals are now one thing you approve in one place — and an app can raise one.** Anything that
  suggests a change (a skill the system wants to extract, a workflow it wants to run, an action, or
  an installed app's own suggestion) files the same kind of proposal, and the Inbox's new Proposals
  view shows what approving would actually DO before you click. Approving runs it through the
  machinery that already exists for that kind of work — no separate approval path per producer — and
  **if the apply fails, the proposal stays in your inbox with the error on it** rather than quietly
  disappearing as if it had worked. Batch approve is deliberately narrow: it lights up only when
  every selected proposal comes from the same source and is the same kind, so "approve all" can never
  sweep together four unrelated changes you reviewed as one. Proposals whose payload is marked
  editable can be edited before approving, and what you edited is exactly what runs. An app must
  declare each proposal kind it may raise in its manifest (`permissions.proposals`), which you see at
  install time; an undeclared kind is refused, and one app can never propose an action that calls
  into another app. Every app-raised proposal is recorded in the security event log.
- **An HTML artifact can now be opened as a real page, not just previewed in a card.** Deploy a
  widget/HTML artifact from its detail view and it is served at a stable in-app URL
  (`/artifacts/serve/<slug>/`) you can open in a pane beside the artifact or in a new tab, so a
  generated dashboard or tool can actually be clicked through instead of only read. The library
  toolbar lists everything currently serving with its URL, and "Tear down" un-publishes it while
  keeping the artifact and its history. This is local-only on purpose: the page sits behind the same
  session auth as the rest of the dashboard — there is no public link — and it is fenced by a strict
  content-security policy that leaves it unable to call PersonalClaw's own API, so an artifact
  written by a model cannot use your session to act on your instance. Deleting an artifact
  automatically stops serving it, and a request that tries to climb out of the artifact's own folder
  is refused rather than answered.
- **You can point PersonalClaw at an outside skill catalog and browse it in the Skills store.** Add a
  catalog under `packs.skill_catalogs` — a JSON index endpoint, or a repo laid out as
  `skills/<slug>/SKILL.md` — and it shows up as one more source alongside the bundled skills, with
  per-source match counts on the source filter so a large catalog tells you how much of it matched,
  not just how many rows fit on screen. A catalog is treated as untrusted third-party content
  whatever you think of its author: it installs through exactly the same guarded path as every other
  marketplace (staged to quarantine, scanned at community trust, then committed with a lock file you
  can re-verify later), so a malicious skill is refused before anything reaches your skills tree.
  Catalog fetches go through the same network guard as every other outbound connector — a catalog URL
  can't be talked into reaching a private address — and browsing a big index costs one fetch and no
  model tokens; only the skill you choose to install is ever downloaded. One unreachable catalog is
  skipped with a log line instead of emptying your store.
- **When two machines edit the same thing while offline, nothing is overwritten — you get asked.**
  Multi-machine sync now tells a real conflict apart from an ordinary catch-up: if both machines
  changed the same record since they last agreed on it, the change is not applied. Your local copy
  stays exactly as it is, both versions are kept, and the divergence lands in a conflict review queue
  as a needs-review item — memory conflicts on the memory review surface, knowledge conflicts on the
  knowledge one. A background model pass drafts a suggested merge with a short rationale for you to
  look at, and that draft is only ever a suggestion: PersonalClaw never applies it for you. With no
  model configured (or when the model is unavailable) the conflict still appears — just without a
  suggestion. A one-sided change, where only one machine moved, keeps merging automatically as before.
- **Memory now has slots: a handful of small, always-there notes about you, instead of facts the
  assistant has to go looking for.** Six of them exist by default — persona, preferences, pending
  items, self-notes, glossary (per workspace) and self-model — and whatever is in them is put in
  front of the assistant at the start of every session, so standing context like "call me by my
  first name" or "in this project, 'run' means the deploy script" stops depending on whether a
  search happened to surface it. Each slot has a size limit and the whole block has a hard ceiling,
  because something injected on every turn costs you context forever. When a slot is full, the write
  is **refused and you are told what would have to go** — with the specific lines named — rather than
  quietly trimmed or quietly dropped: losing something you asked to be remembered without saying so
  is the one outcome worth failing loudly to avoid. Slots start out empty and nothing is written
  until you put something in one. Anything the assistant adds to a slot on its own is only ever
  appended, and a line **you** delete is never brought back, no matter how many times it re-observes
  it. (An editor for slots arrives with the memory dashboard work; today they are reachable through
  the memory API.)

- **PersonalClaw can now try a local model first for background work, and fall back to a cloud
  model when it can't.** Bind two models to a background use case — say a local one and a cloud one
  under Reasoning — turn routing on, and PersonalClaw will reach for the local model first, moving
  on to the next model you bound if it's unavailable or too slow. It only ever reorders the models
  you already chose: it never adds one, never removes one, and a model that can't be reached still
  reports a clear error rather than being quietly swapped for something else. Settings → Models →
  Routing gains a policy table showing exactly which model each kind of request tries first and why,
  with three ways to overrule it: a mode (off / prefer local / learn from results), a pin (always
  local, always cloud, or one exact model), and manual reordering. Off by default; changes to the
  table are recorded to the security event log, since routing decides which models see your prompts.
- **"Check this work" — verification that actually runs, instead of a second opinion from the same
  voice.** A new bundled `check-work` skill answers "did that actually work?" by reconstructing what
  the session CLAIMED, deriving 2-4 executable checks from those specific claims (does the file
  exist, does it contain the symbol the claim named, does the command re-run clean), running them
  with real tool calls, and reporting pass/fail with the observed line quoted as evidence. A check it
  cannot execute is reported **unverifiable** with the reason — never assumed passing — and a session
  from which no check can be derived is told so rather than handed a generic checklist. After a turn
  that did real multi-step work and said it was done, chat now offers a **Check this work** chip: it
  only offers, and the checks run when you click it, so verification never spends your tokens or your
  latency without you asking (**Settings → Chat → Offer 'Check this work'**, on by default).
  Unattended SDLC loops can run the same derivation after a stage's gate passes
  (`loops.check_work_stages`, off by default) — which catches the case a gate command can't see: the
  command passed, but the stage claimed a file it never wrote.
- **Ask for a few versions and get the best one, with the others one click away.** Say "give me 3
  versions and pick the best" and PersonalClaw drafts the candidates **in parallel** — each at a
  different temperature, so they are genuinely different answers rather than the same one three
  times — then judges them against criteria you confirm and leads with the winner. The runners-up sit
  in a collapsible list with their scores, and "use #2" switches to that candidate verbatim, no
  re-drafting. Because N candidates cost N model calls, it always confirms the count (capped at 5)
  and what "best" means before spending anything, and every call is metered and logged like any other
  model call. If one candidate fails you still get the rest, judged; if the judge is unavailable you
  get one answer clearly labeled as unranked; if everything fails it says so instead of inventing an
  answer. Each run also records an anonymous line — how many candidates, how far apart their scores
  were, which one won — so it can eventually tell you when sampling is actually worth the extra cost
  and when it isn't.
- **Apps can now share data with each other, read-only, only when both sides agree.** An app that
  wants to expose its stored data declares `storageShared: true`; an app that wants to read another's
  data names it in `storageRead`. A read is granted only when BOTH are declared — neither app can
  reach into the other one-sidedly — and the reader gets a strictly read-only view (writing to shared
  data fails; to send another app data, apps still go through the app-messaging broker). Both
  declarations are shown on the install-consent screen so you see, before installing, which apps an
  app shares with or reads, and each active grant is recorded to the security event log.
- **Evaluation scenarios are now yours to keep, version and extend.** The four bundled scenarios
  install into `~/.personalclaw/evals/scenarios/` on first use instead of hiding inside the
  installation, so `personalclaw eval` runs — and the offline eval substrate scores — the same
  library you can edit and add your own scenarios to. An upgrade refreshes a bundled scenario only
  when it ships a newer version than the copy in your home, so your edits survive updates. Each
  scenario also names the seeded fixture home it runs over, so a run starts from a known clean state
  rather than from whatever your home happens to contain — and nothing an eval run does can touch
  your real home. Every recorded eval result now carries a pin (which scenario, which models, which
  prompts, which config), and a result that can't be attributed is refused rather than filed
  misleadingly: "did the score move, or did something underneath it move?" is now answerable.
- **Voice input can now run hands-free, and spoken replies stop talking over you.** Beside the
  push-to-talk mic there is a hands-free toggle: keep talking and your dictation accumulates in the
  composer, and nothing is sent until you say a confirmation phrase ("go ahead", "do it") — say
  "cancel" and the draft is thrown away, so a half-finished thought can't become an executed
  instruction. While a reply plays aloud the microphone is released and whatever it captured is
  discarded, and any transcription that repeats three consecutive words the assistant just said is
  dropped as echo, with the dashboard saying so rather than looking deaf. Spoken text is cleaned
  first — code blocks, URLs, file paths and CLI flags are no longer read out letter by letter, while
  the transcript keeps the full text — and a dictated turn tells the model it came from speech so it
  self-corrects misheard words. All six knobs live in **Settings → Speech & Transcription →
  Hands-free voice**.
- **Approval memory: teach the assistant what it may do without asking again.** A new
  `triage_rules` tool lists, adds, and revokes standing approve/deny rules for the coming
  proactive digest, each shown with how often it has fired and where it came from. A **deny rule
  always beats an approve rule**, however narrowly the approve was written, so blocking a class of
  action is the safe move. New **Proactive** settings section — the digest schedule, its
  classifier gate, and the auto-execution cap — with triage and auto-execution both OFF by
  default. Nothing runs or acts until you turn them on.
- **Attention notifications can now ask for a second opinion before interrupting you.** Turn on
  verification for a proposal or agent-request rule and, before its notification fires, a cheap
  background check judges whether the claim holds. Only a clear refutation withholds it — every
  uncertain, unchecked, or model-unavailable case still delivers, so a real request is never
  silently dropped. Withheld items appear under a new **Filtered** view with a one-click **Restore**
  that delivers the notification you held back. (Off by default; opt in per rule.)
- **A Companion apps settings section — turn on LAN discovery so phone/desktop clients can find this gateway.** Settings → Companion apps adds a **LAN discovery** toggle (off by default — announcing a service on your network is an opt-in) and an **instance name** field (the friendly label a client shows; empty falls back to the machine hostname). This is the configuration foundation for the companion-app clients; the discovery advertiser itself arrives next.
- **You can now replay a finished workflow run and see exactly where an edit would change it.**
  `personalclaw workflow replay <run_id>` re-drives the run's decision path against its OWN recorded
  responses — it calls no model and spends nothing — and compares the result to the path the run
  actually took, reporting the first step that moved. Replaying an unchanged run reproduces its
  trajectory exactly; edit a step's prompt and replay names that step as the first to diverge, which
  is the question a mid-run edit really asks: *what did my change actually affect, and from where?*
  Divergence is a normal answer, not an error — a template edit is supposed to diverge, and the verb
  tells you where rather than failing.
- **A run's introspection now shows how its branches and judges actually decided, across the
  template's history.** A workflow that routes on a condition, or gates on a judge, journaled each
  decision one run at a time and never added them up — so a branch that has taken the same case
  every single time, or a case no run has ever reached, was invisible. The cockpit's introspection
  panel now has an **Edges** section: for each branch it shows the case distribution, and for each
  judge the verdict distribution. It flags two things a legible plan should not have — a **case
  never taken** (declared but dead), and a **selector doing no work** (a branch that always routes
  one way, or a judge that always returns the same verdict). Both are held to the same sample bar as
  the "never said no" gate badge: over a handful of runs a case simply hasn't been sampled yet, and
  a warning there would just teach you to ignore the panel — so nothing fires until there is a real
  history behind it.
- **A workflow template can now learn from its own runs — and you stay in control of every change.**
  Open a template and you'll find three new things. A **Versions** tab shows its history as an
  append-only list: each accepted change is a new version, you can see what changed between two of
  them as a typed diff, and **rolling back is one click** — it just re-points to an older version,
  nothing is rewritten, and a run always records the exact version it executed. A **Run Ledger** tab
  shows the template's recent runs at a glance. A **maturity badge** says how proven the template is
  (a gate that has never rejected a bad run is not yet "proven", and the badge is honest about that).
  And a **Refine now** button runs a propose-only refiner over the template's failure history: it
  reads what went wrong, and if the evidence supports it, files ONE reviewable proposal to improve
  the template. It can only propose — it can never edit a template, install a skill, or change what
  makes a template fire. You accept a proposal to apply it (which creates the next version), or reject
  it. Nothing changes until you say so.
- **Accepting a skill refinement no longer rewrites the skill.** A refinement now applies as a small
  sidecar file layered onto the skill when it loads, so the original is never touched — which means
  reverting a refinement is deleting one file, and a marketplace skill's integrity lock stays intact.

- **Runs now record what LANDED, not just what they did.** A workflow that made a measurable
  decision could already journal the bet it was making and have it graded once the horizon passed.
  That was the only thing in the system able to do it. Now any producer can open the same kind of
  question, and two more already do: **publishing an artifact** asks whether anyone ever consumed it
  (a week's horizon), and **stopping to ask you a question** asks whether interrupting you was worth
  it — graded from your own answer, so an approval reads as the interruption landing, a rejection as
  a bet that lost, and a gate nobody ever answered as an interruption that went nowhere. Two
  restraints kept from the original: a question whose ground truth cannot be read closes as
  *inconclusive* rather than being invented, and that weaker evidence ages out about four times
  faster than a real measurement; and only a decision's outcome files anything for you to review —
  the rest are recorded in the run's own ledger, because "this artifact's outcome is inconclusive"
  is not something you can act on.

- **You can now ask which runs of a template went a different way — and get warned when they start
  going a worse way.** Every run now carries a *trajectory signature*: a fingerprint of the exact path
  it took — which steps ran, in what order, how each resolved, which branches it skipped. Two runs of
  a template that took the same path share the same signature, so "show me the runs that did something
  different" is finally a question with an answer, per run on its introspection view and per template
  at `GET /api/workflows/{name}/trajectory`. And when a template's recent runs shift onto a path that
  fails more often than the one they used to take, that shift is surfaced as a risk — not on the third
  run of a brand-new template (that is just a template that has barely run), but once there is enough
  history for the shift to be real. It costs nothing to compute: it is read straight from the ledger
  each run already writes, with no new tracking of any kind.
- **Work that nobody reads now says so.** Every watchdog until now measured whether a scheduled run
  was still *working* — findings, wall-time, errors, stagnation. None asked whether anyone ever
  opened what it produced, so a template quietly writing a deliverable on a cadence into a document
  nobody reads looked perfectly healthy. Now, when the last three deliverables of one work unit each
  sat a full week without being opened, pinned or edited, it appears in your review queue as a
  proposal to **pause or retire** it, naming the runs and the documents involved.
  It **never stops anything by itself.** "Nobody has looked yet" and "nobody will ever look" are
  different facts and only you can tell them apart, so this reports and waits — accepting the
  proposal does not change a schedule either. Three further restraints so it stays worth reading:
  one open, one pin or one edit anywhere in those three cycles and it stays silent; a document that
  was deleted counts as *unknown*, never as unread; and if you reject the finding once, it is not
  raised again.
- **A second starter home, for looking around before you commit anything.**
  `personalclaw gateway --seed demo-home` fills a scratch home with a system that has clearly been
  used for a couple of weeks: two projects that each carry a real brief, three lists, and ten tasks
  spread across in-progress, done, blocked and cancelled — one of them blocked on another task by
  name, with exit criteria part-ticked and notes explaining why the hard one is still open — plus
  written-up memory covering preferences, current project context and two days of history. It also
  arrives past the first-run setup, so it opens on the dashboard rather than the wizard. Use it to
  take screenshots, try a surface, or see what a populated PersonalClaw looks like without touching
  your own data: `--seed` **refuses your real home outright**, so point `PERSONALCLAW_HOME` at a
  throwaway directory and delete it when you are done. Two things it deliberately leaves empty —
  knowledge items and loops — because those live in databases rather than files and a starter home
  is only a file copy; create one of each by hand if a screenshot needs them.

### Changed
- **What finishes a loop is now written down in one place instead of decided in five.** Every loop
  kind — general, goal, code, design, research — used to carry its own Python answer to "is this
  loop done?", so there was no way to read the rule without reading five modules, and two of the
  five turned out to answer nothing at all. Each kind now *declares* its convergence: which
  mechanism decides done-ness (a command it runs, a judge it commissions, its own per-cycle
  orchestration, or never), whether reaching the cycle budget counts as a clean finish, and whether
  the stall detector applies. One supervisor reads that declaration for every kind. Behaviour is
  unchanged — same commands, same judges, same skeptic pass, same calibration canary, same
  budget and stall handling — but the rule is now inspectable, and a new loop kind gets convergence
  by declaring a row rather than by shipping code the engine has to trust.
- **PersonalClaw no longer writes anything into your coding CLI's own config, and an ACP agent app can
  no longer ask it to.** A CLI-side config seeder existed for a coding CLI that ignored the tool list
  we hand it when a session opens. Driven end to end against all three shipped CLIs, no such CLI
  exists — each one honours the list passed over the protocol, verified by watching PersonalClaw's own
  tool server actually start underneath it while none of the CLIs' config files mentioned us at all.
  The seeder never ran either: nothing ever supplied the setting that switched it on. It is now
  deleted rather than left lying around, so there is one way the tools reach a session instead of two,
  and enabling or disabling an agent app touches nothing of yours outside PersonalClaw's own home.
  **SDK note for app authors:** `register_acp_cli_entry` no longer accepts `agent_config_dir`. A
  bundle still passing it fails loudly at import instead of quietly seeding nothing — which is the
  point of removing it rather than ignoring it. Nothing else about the call changes, and no CLI needs
  the argument, so a bundle that never declared it is unaffected.
- **The agent can no longer edit a file it has not read.** An edit computed against a stale or
  imagined version of a file used to succeed silently and revert whatever someone else had just
  changed. Now a write to an existing file is admitted only if that file's *current* content was
  actually shown to the agent first, and a write that cannot prove it is refused with the exact read
  to do instead — so the agent fixes itself in one step rather than clobbering your work. The check is
  on what was really observed, not on whether a read happened: a read of a *different* file does not
  count, and neither does one whose output was cut off before the part being edited, because reading
  the first page of a long file does not tell you what is on the fifth. **Creating a new file needs no
  read** (there is nothing to lose), but overwriting an existing one is held to the same bar as an
  edit, and it must have been seen in full — an overwrite replaces everything, including the part you
  never saw. If a file changed on disk after the agent read it, the write is refused too, and the
  refusal says so; `bash` is the one exception, since a shell command names no target the check could
  hold it to.
- **Rewinding a conversation no longer throws the old ending away — and that history is now
  stored inside your chats.** Editing a message from earlier in a conversation used to delete
  everything after it. It still replays from that point, but the turns that came off are kept on
  the message you edited: a divider at the rewind point tells you how many are held, you can
  expand it to read them, and **restore** rebuilds "everything up to the edit, plus the old
  ending" as a *new* session, leaving the chat you are in untouched. Five rewinds' worth are kept
  per message; a sixth pushes out the oldest. **This adds a field to saved chat messages.** A chat
  written before this update loads exactly as it did and simply has nothing held at any turn — but
  a chat written *after* it, opened by an older PersonalClaw, will not show the retained endings,
  and rewinding there goes back to discarding them. As with any 0.x state-shape change, run
  `personalclaw snapshot` before updating if you want a restore point.
- **Finding something in a long conversation now works properly with a keyboard and a screen
  reader.** `Esc` closes the find bar from every one of its controls rather than only from the
  text field, `↑`/`↓` cycle matches instead of moving the caret, and closing the bar puts your
  focus back where it was instead of dropping you at the top of the page. A screen reader now
  hears the position in words — "Match 3 of 17", or "No matches" — rather than the bare digits
  on screen, and it is told when follow-up suggestions appear, which was a change you could
  previously only see. On a phone-width screen the find bar now spans the column, so on a narrow
  phone it no longer hangs off the edge of the screen.
- **The Optimize button now knows who said what, and leaves an already-good prompt alone.** The
  recent conversation it sends along is labelled by speaker and ordered oldest-to-newest, and each
  turn gets twice the room it used to — so asking it to "add a test for that file from earlier"
  resolves to the actual file, instead of guessing from an unattributed blob. Twice the room means
  the ten turns no longer collide with the size limit; when a limit is hit, whole turns are dropped
  from the oldest end rather than a turn being cut in half and arriving attributed to nobody. And a
  prompt that is already specific now comes back untouched: the optimizer is told to say so in one
  word instead of paraphrasing your prompt back at you, which used to be how a good prompt got
  quietly reworded. Reverting still restores exactly what you typed — and now puts the cursor back
  in the composer, since you reverted in order to keep typing.
- **The assistant now needs to see a habit work three times, not twice, before it offers to make it
  a standing principle.** Its self-model — the one part that learns from what quietly *works*
  instead of from corrections — proposes a behavioural principle only after three reinforcements.
  Twice is the count a coincidence reaches, and a principle is always-on: it changes how every
  later answer is shaped, which is a change you would struggle to trace back. Working theories,
  which announce themselves as guesses, keep the lower bar. As before, nothing is installed on its
  own — it is still a proposal you accept or reject.
- **A step that reads another step's output no longer needs a hand-written ordering — and steps in
  different branches of a workflow can now feed each other.** The engine now derives "run after"
  directly from "reads the output of": if a step binds `{{nodes.other.output}}`, the scheduler holds
  it until `other` has finished, wherever the two sit in the workflow. Two things follow. A step that
  reads a sibling running alongside it is simply held rather than refused, so you no longer hand-write
  an ordering the engine could see for itself. And a shape that used to be impossible now works: a
  workflow can fan out into parallel branches and have a later step pull results from *across* those
  branches — a diamond spanning two containers — instead of being told the branches cannot reference
  each other. A hand-written `needs` still has one job, expressing an ordering that is not about data
  (a lock, "publish only after the announcement went out"); it is now checked against what the data
  already implies — the engine warns when a `needs` merely restates a binding, and refuses one the
  workflow's structure could never honour. No bundled template changed how it schedules.
- **Autonomous loops now learn the way workflows do.** The self-improvement loop — the part of the
  system that reviews finished work and proposes better ways to do it next time — could only ever see
  *workflow* runs. The long-running autonomous loops (goal, code, design, research) kept their cycle
  findings and done-ness verdicts in a separate place it never looked, so a loop that ran for weeks
  contributed nothing to what the system learned. Loops now record their cycles, assessments, stalls
  and reaps to the same shared log workflows use, and a finished loop mines its own history for
  proposals — so three loops that keep taking the same successful path can now surface as "this looks
  like a procedure worth naming." **This moves where a loop's findings and verdicts are stored.** A
  loop that finished *before* this update keeps its files, but its old findings/verdicts won't appear
  in the cockpit or feed learning; loops going forward are unaffected. As with any 0.x state-shape
  change, run `personalclaw snapshot` before updating if you want a restore point.
- **A fan-out step that shares a limited resource now waits its turn instead of racing.** A workflow
  that spreads work across parallel branches can mark a step as needing a lease on a named resource;
  the engine admits only one holder at a time and the rest wait, and the lease survives a gateway
  restart so a crash mid-fan-out does not double-claim. Steps can also declare a bake-in delay before
  a result is trusted, or roll back a step whose measured quality regressed. Workflows that declare
  none of these behave exactly as before.

- **A workflow that reads another step's output now refuses to save unless that step is guaranteed to
  run first.** The engine kept two separate pictures of how steps relate: what must run before what,
  and what reads whose output. Nothing checked they agreed, so a template could pull
  `{{nodes.some_step.output}}` from a step running *alongside* it — and instead of a clear complaint,
  the run failed partway through with "binding failed: check the referenced node id and field exist",
  pointing at a step id that was perfectly correct. That is now a typed error at save time
  (`WF_UNORDERED_DEP`) naming the reader, the step it reads, and why the ordering is missing. **No
  bundled template was affected** — all 19 were checked first, and every one already ordered its
  steps correctly. Nothing about how workflows run has changed.

- **A workflow step that declares what its output will contain is now checked against the steps that
  read it.** A step can declare an output contract — "this must be JSON, and it must contain these
  keys" — and the engine has always enforced it on the *producing* step. Nothing ever compared it to
  the steps reading that output, so a template could bind `{{nodes.classify.output.summary}}` from a
  step whose own contract promises `findings`, save perfectly clean, and then die partway through the
  run on an unresolvable reference. That is now a typed error at save time
  (`WF_UNSATISFIABLE_OUTPUT_REF`) naming the reader, the step it reads, the key it wanted and the
  keys the producer actually guarantees. In a workflow that already uses contracts, a step read at a
  sub-path but declaring none raises an advisory warning instead, listing the readers that would
  benefit. **No bundled template was affected** — all 19 were censused first: none declares an output
  contract today, so nothing shipped changes and nothing new appears in validation output. No new
  contract vocabulary was added, and nothing about how workflows run has changed.


- **A loop that keeps working but stops getting anywhere now stalls, even when it insists it is
  making progress.** The supervisor's stall detector used to read one number the worker itself
  writes — `new_findings_count` — and treated a missing number as "progressing". So a worker that
  reported *any* nonzero count, or simply stopped reporting one, could spin for its entire cycle
  budget on your money without anything noticing. The supervisor now also watches two things the
  worker cannot write for itself: whether the cycle report is **byte-identical** to the previous
  cycles', and whether every cycle **checked the same sources or touched the same files**. Either
  one stalls the loop to *"needs direction"* with the reason on the card, so you can steer it
  instead of paying for another ten identical cycles. The self-reported count is still used — it is
  the cheapest and clearest signal when it is honest — it just can no longer overrule what the
  supervisor can see, and its silence no longer counts as progress.
  A monitor loop still never stalls (a quiet cycle is the point), and a loop kind that records
  nothing to compare is judged only on content, so nothing stalls for lack of data. The number of
  no-progress cycles it takes is now a setting, `loops.stagnation_window` (default 5, minimum 2),
  read live — no restart needed.

- **A loop's judge no longer runs on the same model as the worker it grades.** Autonomous goal
  loops never let the worker certify its own work — a separate judge, in its own session with its
  own prompt and no write tools, decides whether a cycle is done. But that judge was resolving the
  same model binding as the worker, so the two shared a blind spot on exactly the question the judge
  exists to answer. The judge now resolves its own axis, `loops.judge_use_case`, which defaults to
  **Reasoning** — so if you have pinned a stronger model to Reasoning in Settings → Models, that is
  now the model that decides done-ness, and the loop's work still runs on the Loops binding. Set
  `loops.judge_use_case` to `loops` in `config.json` to put both back on one binding.
  A judge whose model is unavailable behaves exactly as before: the cycle is **deferred**, never
  reported complete, and the warning in the log now names the binding to go check.

- **The approval prompt now tells you what a tool call can touch, and how far your answer
  reaches.** When the agent asks permission to run a tool, the card is a four-part brief instead
  of a tool name and four buttons: **what** (the tool and its arguments), **why** (the one-line
  purpose, when the agent gave one), **what it can touch** — chips like "Runs a command", "Writes
  files", "Uses the network", "Reads only" — and **how far the answer reaches**, a
  "Remember this choice" picker (*Just this once* · *This chat* · *This agent*) that spells out, in
  plain text, exactly what gets remembered before you answer. Then one **Allow** and one **Deny**.
  The out-of-context approval toast (an approval raised in a chat you are not looking at) carries
  the same one-line summary, so "another chat session needs approval to run bash (runs a command)"
  is legible without opening it.
  Two deliberate restraints: the chips are **claims, not an audit** — only facets the system can
  actually establish are shown, and when it can establish nothing it says nothing rather than
  painting four reassuring negatives; and the brief **never advocates**. Nothing recommends
  approving, no answer is preselected or focused, and neither verb is styled as "the" action —
  the only thing chosen for you is the narrowest scope, which remembers nothing.

### Security

- **A password inside a URL was invisible to every place PersonalClaw redacts secrets.** The
  redaction knows what a secret *looks like* — the key formats the big providers use, and lines of the
  form `api_key = …`. A credential carried by *position* instead, in the `user:password@` part of a
  URL, matched none of those. So a git remote like `https://you:yourtoken@github.com/you/repo.git`
  went through the diagnostics log, the security audit log, agent output and confirmation previews
  completely intact.
  **It looked covered, and that is worth saying.** One test claimed to check exactly this — and
  planted a GitHub token as the password, which the existing rules already recognised on shape alone.
  It passed for the wrong reason. Substitute an ordinary password and it fails. Both cases are checked
  now.
  **The host is deliberately kept.** Only the credential is replaced, so "the clone of
  github.com/acme/repo failed" is still readable in your logs. Removing a secret should not cost you
  the ability to see what went wrong.
  **The security audit log gets this first**, because it is the one place that cannot be cleaned up
  afterwards: it is a tamper-evident chain, so rewriting an old entry would break it. A secret written
  there is there for good.
  **And a source URL carrying a password is now refused outright** when you add an app source, rather
  than accepted and then redacted downstream. The message says why. Ordinary remotes are unaffected —
  including `git@github.com:owner/repo.git` and `ssh://git@host/repo`, where the username before the
  `@` is a username and not a secret.

- **Adding an app source no longer accepts anything you type.** `not-a-git-url` was stored silently
  and then appeared in the Store as its own source heading with no apps under it and nothing saying it
  was broken. It is refused now, with a message naming the forms that work. (A *valid* source that
  turns out to be unreachable still shows as an empty group; that part is unchanged.)

- **Three more ways a path could leave the folders PersonalClaw is allowed to touch.** All three are
  the same mistake as the record-id one above, reached differently, and all three were reproduced
  before being fixed.
  **"Reveal in Finder" checked less than every other file operation.** It was the one file endpoint
  that never asked whether a path was inside a folder the dashboard surfaces, so `/etc/hosts` and
  another install's home both worked — and it is the endpoint that hands the path to your operating
  system to open with whatever it thinks the file is. The checks it *did* have could not catch that: a
  path needs no `..` and no credential-looking name to simply be somewhere else.
  **A one-line file could redirect the git panel at any repository on the machine.** Git lets a `.git`
  entry be a file that points somewhere else — that is how worktrees work — and PersonalClaw was
  checking where the pointer *sat* rather than where it *pointed*. A pointer written inside a folder
  it was allowed to browse therefore aimed the whole git panel anywhere, and a whole-commit diff would
  hand back `.env` contents that reading the file directly refuses. The check ran on every request; it
  was measuring the wrong path. Worktrees of repositories you can already browse are unaffected.
  **Installing a skill checked every file in it and not the folder they go into.** A skill whose name
  was a path escaped the skills folder entirely. A safe file path underneath an unsafe folder is not a
  safe path. The same hole existed in the quarantine folder used to scan a skill *before* installing
  it — so it escaped ahead of the security scan, which could not object to a write it had not been
  asked about yet. That one was found while fixing the reported one.

- **A record id can no longer address a file outside its own store.** Projects, task lists, tasks,
  task comments, learning proposals, skill proposals and attribution records were each stored as a
  file named by putting an id into a directory — and in Python that expression is not a join: hand it
  an absolute path and the directory is discarded entirely. So a URL could name any file on the disk.
  Measured, not theorised: deleting a "project" removed an arbitrary **directory and everything under
  it**, and reading or deleting a "task" reached any `.json` file on the machine.
  **The refusal lives in the store, not at the door.** The tools, the workflow actions and the CLI all
  reach these stores without passing through an HTTP handler, so a check on the way in would have left
  three ways around it. One resolver now owns the question for every store, which is also what makes
  the next store inherit the answer instead of having to remember it.
  **A refused id says so, instead of looking like a missing record.** These stores answer a read they
  cannot complete with "not found", which is how this survived being looked for — a rejected path and
  a file that isn't there were indistinguishable. A malformed id is now a `400` naming the parameter
  it came from, and the refusal is deliberately built so those "not found" fallbacks cannot swallow it.
  **What changes for you:** an id containing a `/`, a `\`, a `..`, or more than 200 characters is
  refused. No id PersonalClaw has ever generated looks like that, so ordinary use is unaffected.

- **An argument a tool call carries can no longer lower that call's risk.** `command` is an ordinary
  argument name, and the approval gate read it out of *any* tool's arguments to answer "what shell
  command is this call going to run?". So a destructive tool that happened to carry one was judged by a
  string that was never going to be executed: deleting a workflow definition, with `command: "ls"`
  alongside it, resolved to **safe** and was auto-approved with no prompt under "trust reads".
  **The same string also unlocked Ask and Plan mode**, whose entire promise is that nothing is
  executed. That half was not in the report; it turned up because a test asserting "this tool is
  denied in Ask mode" is a different assertion from "this tool needs approval", and both were wrong.
  **One question, asked in one place.** "Is this call a shell invocation?" now has a single answer that
  every gate consults, recognising all three ways a shell call actually arrives — the agent's declared
  kind, the tool's own name, and the `Running: <command>` title an agent sends when the command rides
  inline. Anything else is a tool whose arguments are data.
  **Your read-only `bash` still doesn't prompt.** That is what "trust reads" is for, and the shell path
  is unchanged: `ls` is still safe, `rm -rf` is still destructive, and both still reach the same gate.
  What changed is that a tool which runs no shell can no longer borrow the answer.
  **One thing got stricter on the way:** a shell call whose command text never reached PersonalClaw
  used to be treated as a read, because "bash" carries no dangerous-sounding word in its name. It is
  now treated as a command nobody has read, which means it is shown to you rather than assumed safe.

- **Ways *in* now share one gate instead of each inventing their own.** The read-only MCP endpoint
  used to be the only inbound surface, and it carried its own answer to "am I allowed to serve
  this?". Four more surfaces are on the way, so that answer moved into one place they all pass
  through — and it got considerably stricter on the way.
  **Four switches stack, and any one of them says no.** A master switch that takes every inbound
  surface down at once; a per-surface switch; a per-client switch, so you can cut off one
  integration without turning off the surface it uses; and the guardrails incident flag, which
  suspends every inbound request while unattended work is paused. All four are re-read on every
  request, so turning one off takes effect on the next call — never on the next restart.
  **Every one of them fails closed.** A missing, corrupt, or unreadable switch reads as *off*, an
  unreadable incident file reads as *active*, and a client registry that will not parse
  authenticates nobody. This is the opposite of how a *guard* flag behaves, and deliberately so: a
  guard that cannot read its flag must keep protecting, while a door that cannot read its lock must
  stay shut.
  **A caller is now a client, not just a token.** Each integration gets its own record with a label
  and its own bindings — which surfaces it may reach, which agent, which tools, what scope — and
  those bindings are **pins, not defaults**: a request that asks for something it is not bound to is
  refused and logged, never quietly given the bound value instead. Only a hash of each token is
  stored, so revoking a client is deleting its record. Each client has its own rate ceiling, and one
  that keeps hitting it is disabled automatically with a notification, rather than being throttled
  forever in silence.
  **And everything that comes back out is fenced.** Content returned to an external caller is
  wrapped as data with its origin attached — down to which client asked — so a model reading it
  cannot mistake it for instructions. Every request, allowed or refused, is written to an audit
  trail, and the security-relevant ones also reach the security event log.

- **Inbound settings and tokens moved, and old ones stop working.** The config section is now
  `external_access` rather than `inbound`, so `personalclaw config set inbound.mcp.enabled true`
  becomes `personalclaw config set external_access.mcp.enabled true` — plus the new master switch,
  `external_access.enabled`, which must also be on. Surface tokens now live in the credential store
  (your keychain, or `.env` at `0600`) instead of a bespoke `.inbound_<surface>_token` file, so an
  existing MCP token needs re-minting with `personalclaw inbound token create mcp`. A token is also
  now refused if it equals *another* surface's token — five surfaces sharing one bearer would
  collapse five separately revocable credentials into one. `external_access.public_url` and
  `allow_remote` are not editable from the dashboard at all: the endpoint refuses them rather than
  ignoring them, because a security boundary that moves on one request is not a boundary. Client
  records join snapshots and exports; the request audit trail deliberately does not.

- **A `.env` reached a file checkpoint through a symlink.** File checkpoints — the backups behind
  `/rewind-to-turn` — never copy credential-shaped files like `.env`. That rule was applied to the
  name the agent wrote to, so a file called something harmless that was really a link to your `.env`
  was copied anyway, secret and all, into a store that lives under your home and travels in
  snapshots. The check now looks at what will actually be read, not just what it is called, which
  also closes the same trick pointed at `~/.aws/`. Nothing else changes: an excluded file is still
  reported in the rewind preview as "not captured" rather than silently skipped.

- **A rewind now refuses to write outside the workspace it belongs to.** Restoring files is the one
  place PersonalClaw writes a previously recorded path back to disk, so it no longer takes that path
  on trust: a destination that resolves outside the session's own workspace — through a `..`
  component or a symlink planted out of the tree — is refused and reported, and a rewind that
  refused anything is reported as incomplete rather than as a success. Files inside the workspace
  restore exactly as before.

- **An app can no longer change the version of a library PersonalClaw itself depends on.** Apps are
  allowed to bring their own Python packages, and they are installed into the same environment
  PersonalClaw runs from — so until now an app could declare, say, an older `numpy` than the one
  running underneath you, and installing it would quietly swap that library out from under a live
  gateway. Now the install is refused before anything is downloaded, and the message names the
  package, the version you are actually running, and the version the app asked for, so you can see
  the exact disagreement. Nothing you already have installed is affected: an app may still bring any
  library PersonalClaw does not itself depend on, which covers every one of the provider apps that
  ship with it — the AI provider SDKs are optional extras, not part of the core set. If the check
  cannot prove a request is safe (a version specifier it cannot read, or a package whose installed
  version it cannot determine) it refuses rather than guessing. What is still true, and now written
  down under Security limitations, is that an app can *add* packages to that shared environment, so
  install apps you trust.
- **Credentials can now live in your OS keychain, and Doctor tells you where they actually are.**
  Set `PERSONALCLAW_CREDENTIAL_BACKEND=keychain` and new secrets go to the macOS Keychain, Linux
  Secret Service or Windows Credential Locker (`pip install 'personalclaw[keychain]'`) instead of
  `~/.personalclaw/.env`. Reading is unchanged everywhere — nothing you use has to know which store
  answered. On a headless box with no secret service the request **falls back to `.env` at mode
  0600**, never to a plaintext file somewhere else and never to looser permissions, and Doctor says
  so rather than claiming a keychain you don't have. Doctor reports the store that is actually
  holding your secrets, not the one you asked for.
- **Unattended automations now run read-only by default, and you're asked before one runs scripts
  in a project folder.** Three changes narrow what work runs while no one is watching. A background
  *research* spawn — the kind a cron or an agent fires with no human present — now starts in a
  read-only capability class: it can read, search and fetch, but a write or execute tool (including
  a shell) is refused at the tool-approval layer unless the automation was created with an explicit
  write grant (`capability: mutating` on the trigger). A read-only research run that quietly gains
  write is exactly the escalation this closes. Second, when a finished background run hands its
  result back into an unattended session for follow-up, that turn no longer blanket-auto-approves
  whatever tool it reaches for — it resolves through the one safety-profile path the rest of
  unattended work already uses, so the security hooks screen it; an interactive chat, where you are
  present, is unchanged. Third, the first time an automation wants to run scripts in a project
  folder it stays in **Preview** — read-only, no script execution — and asks you to **Trust** the
  folder; the decision persists (`project_trust.json`, keyed by the resolved directory), so it asks
  once, and only Trust lets it write or run project scripts there. Manage it at
  `POST /api/guardrails/project-trust`. **Honest limitation:** these bound what an *unattended* run
  may do by default; a run you explicitly grant `mutating`, or a folder you Trust, has the access
  you gave it — the point is that it is a decision you made, not a default it inherited.
- **The built-in command denylist now repairs itself.** The 112 always-on patterns that refuse
  credential exfiltration, destructive commands and self-tampering used to live only as a list
  inside a Python module. Anything running in the same process — a stray monkeypatch, an agent that
  talked a tool into editing module state, a future refactor — could shorten that list, and every
  command screened afterwards would quietly obey the shorter version. The patterns now ship as a
  packaged data file with a sha256 over their exact contents and order, and every read re-checks the
  live list against it: a shortened, edited or reordered denylist is restored before the next command
  is screened, and the repair is written to your security event log as
  `baseline_denylist_reasserted` (with which patterns came back). A refused attempt to shrink the set
  is logged as `baseline_denylist_tamper_attempt`. Your own additions in
  `security.denied_commands` work exactly as before — they are appended, deduped against the
  built-ins, and can only ever make the list longer. `personalclaw doctor` gained a
  **Baseline command denylist integrity** row that re-verifies the packaged file and reports a
  divergence without ever adopting it, so a tampered file cannot shrink what is enforced.
  **Honest limitation:** this is protection against drift and against an agent tampering at runtime,
  not against you. Anyone who can edit the installed package before the process starts can change
  the baseline — it is your machine.
- **Settings → Security now shows which denylist is actually protecting you.** The panel listed 112
  patterns without saying where they came from, so there was no way to tell a healthy instance from a
  drifted one. It now shows the baseline's version, the sha256 recorded at release, how many patterns
  are enforced, and whether the packaged file still matches — and if it no longer matches, the quiet
  status line becomes an alert that says so while confirming the verified patterns are still being
  enforced. The baseline is read-only on this surface by design: there is no control that can edit,
  reorder or remove one of its patterns. Your own list is now summarised as **"N user additions"**
  counted from what actually takes effect, so an entry that merely repeats a built-in is reported as
  adding nothing instead of inflating the number. The panel states the same honest limitation the
  release notes do — the check proves the patterns match what shipped, and anyone who can edit the
  installed package before PersonalClaw starts owns the baseline; the full statement lives in
  `docs/security/threat-model.md`. A failed read of either the posture counts or the denylist now
  renders an error with the server's message and a Retry, replacing a silent empty list that was
  indistinguishable from "nothing is blocked".
- **Installed apps' backends no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for an app backend that read a variable it never declared.** An app with a backend runs
  it as a subprocess, and that subprocess used to start from a full copy of the gateway's own
  environment — which, because PersonalClaw deliberately puts your `.env` credentials there so
  "trusted children" can see them, meant every installed app's backend could read every credential
  you had configured, plus (measured on a real gateway) about **130** other variables it had no
  declared need for, including your SSH agent socket, AWS settings and your git identity. An app
  backend is the least-trusted long-running process in the system — third-party code, scanned but
  not trusted at install — so it now gets the same *minimal* environment hooks and cron scripts got
  in the previous release: `PATH`, `SHELL`, `PWD`, `TERM`, `PYTHONPATH`, your locale and `TZ`,
  `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle settings, and
  `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT`. The four variables the app contract promises are
  unchanged: `PORT`, `PERSONALCLAW_APP_NAME`, `PERSONALCLAW_APP_SECRET`, and
  `PERSONALCLAW_APP_DATA_DIR` for an app that declares the `storage` permission. Everything else is
  withheld. **All 44 first-party apps were booted against this change and are unaffected** — the two
  that ship a backend (Growth, Minutes) read only `PORT` and `PERSONALCLAW_APP_DATA_DIR`, and the
  model/search apps read their API-key fallbacks in the gateway process itself, not in a backend, so
  a key exported in your shell still works for them exactly as before. **If a third-party app's
  backend stops working for want of an environment variable, you have two ways to fix it:** move the
  value into PersonalClaw's credential store and configure it on the app's instance (the supported
  route — every first-party model app treats the environment variable as a *fallback* to a
  configured credential), or, if the app genuinely needs the ambient variable, name it in
  `sandbox.env_passthrough` (`personalclaw config set sandbox.env_passthrough '["MY_VAR"]'`). Note
  that `sandbox.env_passthrough` is global — a name declared there is visible to your hooks and cron
  scripts too, not just to app backends — and that credential-shaped names (`AWS_SECRET*`,
  `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`, `GIT_ASKPASS`) stay refused even if you declare
  them. To see what a backend is missing, run the gateway with `--verbose`: each backend launch logs
  the names it withheld.
- **A scheduled Python script can no longer exhaust PersonalClaw's file descriptors.** A
  `run-script` cron ran inside the OS sandbox with a minimal environment, but with no cap on what
  it could *consume* — so a script that leaked open files could starve the gateway of descriptors,
  something an agent `bash` command has been unable to do for a while. Scheduled scripts now run
  under the same resource ceiling as every other agent-driven child: the `sandbox.nofile` limit
  (default 4096) applies to the script and everything it starts. A script that hits it gets an
  ordinary `OSError`/`EMFILE`, which surfaces in the job's run history. If you have a legitimately
  descriptor-hungry script, raise the limit with `personalclaw config set sandbox.nofile 16384`.
- **The Store now tells you which other apps an app may message.** An app can declare that it may
  send messages to other installed apps, and PersonalClaw really does enforce that list: a brokered
  message is the only way one app can reach another, and a target the app did not declare is refused
  and written to the security log. But the Store never showed you the list. Installing an app that
  declared it could message your mail and notes apps looked identical to installing one that could
  message nothing — the permission was enforced behind your back rather than consented to. Both the
  install-consent panel and the installed-app panel now name each target among the permissions the
  gateway enforces. A wildcard target is spelled out rather than shown as-is, because it grants more
  than it looks like: an app declaring `mail-*` reads as "any app whose name starts with mail-",
  which covers apps you have not installed yet, and `*` reads as "any installed app". An app that
  declared no target is stated too — "App messaging: none — it declared no target, and the gateway
  broker is the only way one app can reach another, so it can message no other app" — so silence is
  never left to your imagination. Nothing about what an app can do has changed; the enforcement was
  already there and is unchanged. No first-party app declares this permission today, so nothing in
  your Library will start showing a messaging row.
- **The Store no longer implies PersonalClaw confines an app's network access.** An app's manifest can
  declare a `network` permission, and the Store used to list it as a bullet under "Permissions"
  alongside storage, scheduled jobs and background agents — all of which the gateway really does
  enforce. This one it does not, and cannot: an app's code runs inside PersonalClaw's own process (or,
  for the handful of apps that ship a backend, as an ordinary OS process of its own), so there is no
  point at which the platform can intercept the app's outbound traffic. The misleading half was the
  quiet one: an app that declared `network: false` showed **no** network row at all, which read as a
  guarantee that it had been blocked — including for the only two first-party apps that ship a
  backend, Growth and Minutes, both of which declare exactly that. The app detail and install-consent
  panels now show the network claim *outside* the list of permissions the gateway enforces, marked
  advisory, and show it whether or not the app declares one: "Network access: declared / not declared
  — advisory only. PersonalClaw does not confine an app's outbound traffic: this app's code can reach
  the network either way. The declaration is disclosure, not containment." Nothing about what an app
  can do has changed — only what the interface promises. The controls that really do bound an installed
  app are unchanged: its `api` permission bounds what it may ask the gateway for, and the supply-chain
  scanner still gates what you can install in the first place.
  Platform note: on Linux the same mechanism also carries the optional `sandbox.max_pids` and
  `sandbox.max_rss_mb` bounds (both off by default) plus the OOM-killer preference that protects
  the gateway; on macOS only the descriptor limit is enforced.
- **Your hooks and cron scripts no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for any hook, cron script or bash action that read an inherited environment variable.**
  A hook command, a `run-script` cron script and a bash action used to start from a copy of the
  gateway's own environment with a few names filtered out — measured on a real gateway, that was
  **121** variables, and PersonalClaw deliberately puts your `.env` credentials in there so
  "trusted children" can see them. A one-line hook (`printenv`) could read them. Those children now
  get a *minimal* environment built from a fixed list instead: `PATH`, `SHELL`, `PWD`, `TERM`,
  `PYTHONPATH`, your locale and `TZ`, `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle
  settings, and `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT` — plus, for a hook, the
  `PERSONALCLAW_HOOK_EVENT`/`_CONTEXT` variables and the trigger's `$variables` exactly as before.
  Everything else is withheld. **If a script of yours needs one more variable, name it in
  `sandbox.env_passthrough`** (`personalclaw config set sandbox.env_passthrough '["SLACK_BOT_TOKEN"]'`,
  or the config API) — for example a Slack token for a notifier script, or a language runtime's
  variable. Credential-shaped names (`AWS_SECRET*`, `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`,
  `GIT_ASKPASS`) stay refused even if you declare them. To see what a script is missing, run the
  gateway with `--verbose`: every spawn logs the names it withheld. Unchanged: cron scripts still
  receive PersonalClaw's internal secret and port through the temp file they always did, so scripts
  that call back into the API keep working.
- **Scheduled, file-watch, webhook and chained automations now honour the action denylist — they
  never did.** ⚠️ **This changes behaviour for automations that already exist.** The denylist that
  refuses an automated action touching a credential path or running a destructive/exfiltrating
  command was enforced when a *script hook* or a *memory-event trigger* fired an action, but not on
  the busiest path of all: the one every clock, file-watch, webhook and chained trigger dispatches
  through. That seam had the incident kill switch and the autonomy ladder, so the omission was easy
  to miss — the denylist was lost when the old scheduled-job dispatcher was retired and its
  replacement was never re-wired. From this release all three dispatch paths enforce it, which also
  means an action contributed by an installed **app** inherits the denylist wherever it is fired.
  A refused fire does not run, is recorded in the automation's run history as a **skipped gate**
  (not a failure, so it will not count toward auto-pausing your automation) naming the rule that
  matched, is written to the security event log, and — for a `needs_human` rule — raises a
  notification. What this can affect, measured before shipping: only `bash` (its `command`),
  `run-script` (its script name) and `run-prompt` (its `cwd`) carry a field the denylist inspects;
  the other shipped action types are unaffected. If one of your scheduled commands stops running,
  the likely built-in patterns are `rm -rf ~…` / `rm -rf /…` and `git … push` — the same patterns
  the assistant's own shell tool has always refused, which is the point: this is not a new policy,
  it is an existing one that one seam was skipping. Nothing new is configured by default
  (`security.autonomy_denylist` stays empty); to allow a command that is being refused, adjust the
  command rather than the guardrail.
- **A governance ceiling an operator writes once now bounds every unattended run — and the safety
  profile it bounds is finally read at all.** PersonalClaw already described each run's posture in a
  `SafetyProfile` (approval, tool grants, egress tier, path denies, budget, secret-scan mode), but
  nothing consulted the tier/grants/denies, and every automated dispatch seam — clock, file, webhook
  and chained triggers, memory-event triggers, and script hooks fired without a parent session —
  asked for its posture with an *empty* session identity, which classified as "a human is watching"
  and resolved the **interactive** posture. Automated work has been running under the interactive
  profile. Now: those seams identify themselves as unattended, so they resolve the `headless`
  posture, and an optional operator file at `$PERSONALCLAW_HOME/governance/ceiling.json` (or an
  absolute path in `PERSONALCLAW_CEILING_FILE`) sets a hard bound that a run can only make
  *stricter* — never looser. Six governed scopes (`approval`, `scan`, `egress`, `paths`, `tools`,
  `budget`); for example `{"version": 1, "scopes": {"approval": {"value": "ask"}, "paths":
  {"mode": "closed", "allow": ["~/workspace/**"]}}}` means nothing on this machine auto-approves and
  automated actions may only touch your workspace. **No file means no change**: absent a ceiling,
  behaviour is exactly what it was. A malformed one is a hard stop with a WHAT/WHY/FIX message
  rather than a silent start, because "governance could not be established" is not a degraded mode.
  The file is deliberately not editable through the app (it is absent from the config PATCH
  allowlist and its directory is on the built-in sensitive-path denylist, so the agent's own write
  paths refuse it), it is read once at start-up so an edit cannot widen a running gateway, and every
  clamp is logged and written to the security event log. What it cannot do is stop a process running
  as you from editing the file and restarting — for a real trust root, point
  `PERSONALCLAW_CEILING_FILE` at a root-owned `0444` file outside your home. See
  `docs/architecture/security.md`.
  Two effects of the seam correction you may notice even with no ceiling file, both by design: an
  automation whose action type is allowed to run fully on its own now runs with the **undo handle and
  the passive "this happened" notification** kept (the "runs on its own, silently" rung is reserved
  for work a human is watching, since nobody is there to notice otherwise) — it still runs, it is
  just no longer invisible; and outbound secret scanning for automated runs follows your configured
  mode (redact by default) instead of warn-only.
- **An egress "allow-list" now actually restricts.** `allow_hosts` only ever *waived* the
  private-address block, so the `registry` and `listed` egress tiers reached every public host
  exactly like the default — a limit in name only. Policies can now be exclusive (only listed hosts
  are reachable, checked before DNS resolution), which is what lets a run's egress tier — or a
  ceiling of `{"egress": {"value": "listed"}}` — genuinely confine outbound traffic. The agent's web
  fetch and watched-source polls both honour it, and a tier of `off` refuses the request with a
  visible reason instead of making it.
- **A watched-source poll now honours your denied hosts on the headless-browser tier too.** The
  JavaScript-rendering tier passed a hardcoded policy, so `Security → Network` deny-hosts applied to
  the plain fetch and were ignored on the render path. Both tiers now use the same resolved policy.
- **An auto-approval grant for a spawned subagent can be refused by the ceiling.** The trust toggle,
  `--approval yolo`, an `approval_mode: auto` caller and the config default could each widen a
  subagent to auto-approve tool calls; none of them consulted an operator bound. With a ceiling of
  `{"approval": {"value": "ask"}}` the grant is refused and the refusal is audited. Without a
  ceiling file, the toggles behave exactly as before.
- **Path rules are matched correctly.** The action denylist compared paths as strings without
  anchoring them, so a relative path like `../../etc/passwd` could slip past a deny of `/etc/**`,
  and `**` was treated as a single-level `*`, so `~/.ssh/**` missed `~/.ssh/sub/key`. There is now
  one matcher: the queried path is expanded and absolutized, patterns are never rewritten, and `**`
  crosses directories.

- **App backends now authenticate inbound requests, closing a direct-to-port bypass.** An app's
  backend subprocess binds on loopback (`127.0.0.1:<port>`), which is a network boundary, not an
  authorization one — before this change any local process that found the port could talk to the
  backend directly, bypassing the gateway proxy and therefore session auth and the app-permission
  middleware. Every request the proxy forwards now carries an HMAC signature
  (`X-PersonalClaw-Proxy: <ts>:<hmac>` over `<ts>:<METHOD>:<path?query>:<sha256(body)>`, ±60s
  replay window, constant-time compare) keyed by a per-app 256-bit secret minted 0600 at
  `apps_dir()/<app>/.app_secret`. The backend verifies it **fail-closed** via the new
  `personalclaw.sdk.security.require_proxy_signature()` middleware — no/stale/bad signature ⇒ 401
  before any route runs; a backend that cannot obtain a verifiable secret does not start. `/health`
  is exempt so the watchdog can probe it. Both first-party backends adopt the middleware. This
  proves a request came from the gateway proxy; it does not encrypt loopback traffic or defend
  against a process that can read the root-only secret file (see
  `docs/architecture/app-platform.md`).

### Fixed

- **Generated documents no longer show up in your library as broken images, and a generated PDF
  finally previews.** A Word document, spreadsheet, deck, PDF or video that PersonalClaw made for
  you had its card drawn as a picture — so the grid showed the browser's torn-page glyph for a
  file that had been created perfectly. Those cards now show the icon for what they actually are,
  in that format's own colour, while an image artifact still shows its real thumbnail. Opening a
  generated PDF used to show nothing at all: the viewer knew how to display a PDF sitting in your
  files but not one the agent had just produced, and quietly came up empty. It now displays either
  one, and if a PDF genuinely cannot be found it says so instead of offering to open a file that
  is not there. An artifact of a kind the app does not recognise now reads "Unknown kind" rather
  than borrowing the name of a real one — that impersonation is why every generated document was
  labelled "Widget" for four releases with nothing anywhere reporting a problem.
- **The Loops page no longer tells you that you have no loops when it simply could not load
  them.** If the request failed, the page said "No loops yet" and invited you to start your
  first — the most confident possible way to say the opposite of what happened, to someone whose
  loops were fine and merely unreachable. It now says "Couldn't load your loops", shows the
  server's own reason, and gives you a Retry that puts the list back. The Memory audit log had a
  smaller version of the same problem: one sentence, "No matching events", served both a log that
  recorded nothing and a filter that matched nothing. Those are different facts and now read
  differently.
- **A security-audit write that fails is no longer swallowed.** When the subagent reaper
  force-kills a subagent that blew its deadline, it writes one security-event row — the only
  record that the kill happened. That write sat inside a catch-all `except`, so on a home that
  could not be written (read-only, full, permissions) the kill went ahead and the audit row was
  lost with nothing raised: an unauditable kill that looked identical to an audited one. The
  failure now surfaces (logged per-agent by the reaper sweep, which continues with the other
  agents) rather than being absorbed. Every other audit write on this path already behaved this
  way; this one was the exception.
- **Developer-facing: the test suite no longer leaks SQLite handles, and the self-dev harness works
  in a git worktree.** A full run printed ~1,600 `unclosed database` resource warnings from 95 test
  files — every sqlite-backed store was built by a fixture and never closed — and closing them
  exposed a real isolation bug: the process-wide knowledge store was memoized across tests, so
  tests were searching an earlier test's database. Separately, the harness resolved its interpreter
  as the cwd-relative `.venv/bin/python`, which does not exist in a worktree, so
  `python -m harness validate` could not collect the suite there and three of its tests failed in
  every worktree.

- **A lesson saved for one project no longer becomes a rule for every project.** The
  `memory_remember` tool offers `scope: "workspace"` and the workspace-identity prompt block
  promises such a lesson is "only visible in this working directory" — but `POST /api/lessons`
  read neither the scope nor the workspace name, and neither `write_lesson` layer had a scope
  parameter, so the lesson was stored **global** and the caller was told `ok`. A workspace lesson
  is now stored against the working directory it was taught in (`realpath`, matched exactly), and
  is injected only into sessions running in that directory. Global lessons are unchanged: every
  lesson saved before this release is global and still applies everywhere, and no migration runs.
  The endpoint now **refuses instead of downgrading** — `scope="workspace"` without a workspace, a
  workspace that is not an absolute path, or an unrecognized scope each return 400 rather than
  quietly writing a global lesson. Your lesson list still shows every lesson (and now says which
  workspace each belongs to), so a workspace lesson stays visible to manage and delete;
  `GET /api/lessons?workspace=<absolute path>` asks for one directory's view.

- **`until_dry` workflow loops now end when the work reports no progress, instead of always running
  to their iteration cap.** A loop can declare which field of its iteration output counts as
  progress (`progress_field`), and two shipped templates do — `goal-pursuit-open-ended`
  (`new_findings_count`) and `general-project` (`meaningful_progress`) — but the engine never read
  it. Dryness was measured over the *whole* output of the last node in the iteration, which in both
  templates is a judge stage returning a populated JSON object; so a cycle that honestly reported
  `new_findings_count: 0` still counted as progress, the "two clean cycles in a row" streak never
  completed, and the run paid for a model call per iteration up to `max_iterations` (12 and 6) to
  learn nothing. A declared field now decides, wherever in the iteration it was emitted: zero,
  false, blank, empty or null means the cycle surfaced nothing new; anything else is progress. Loops
  that declare no field — the majority, including `audit-sweep` and `deep-research` — keep the
  previous whole-output rule unchanged. A declared field an iteration did not emit also falls back
  to that rule rather than counting as dryness: ending a run because the body forgot a key would
  silently truncate real work, which is worse than paying for one more iteration. `streak` is
  unchanged and still means N *consecutive* dry iterations.
- **Run history no longer says "ran" for automations that did not run.** The run feed translates
  each store's own status word into a typed outcome, and four of the statuses actually being written
  had no entry in that translation — so they landed on a fallback nobody had chosen for them. A
  lifecycle hook that only *launched* background work, and a hook the incident kill switch stopped
  **before** it reached its action, both showed as "ran"; a payload the injection screen blocked, and
  every fire suppressed by quiet hours, a budget cap, an overlap or a triage decision, all showed as
  a red "failed". Each now shows what it was: `deferred` ("outcome not yet known"), a neutral grey
  suppression that folds into the archived half of the feed with its reason, and the shield-marked
  `blocked`. Suppressed and screened rows are also marked as ledger entries rather than openable
  runs, since neither ever reached a runner. This was a reporting fix only — the trigger
  autopause counter reads the stored rows directly and was never affected, so no automation's
  pause/resume behaviour changes. A status the build cannot classify is now logged by name and never
  reported as a success.

### Changed

- **Knowledge search now finds the passage, not just the document — and tells you which passage.**
  Semantic search used to compare your query against one vector per item, built from the item's title
  and summary, so an answer buried on page 12 of a long document was effectively invisible to it: the
  document either matched as a whole or not at all. Search now compares your query against the
  individual passages the library indexes for each document, and scores each document by its single
  best-matching passage. Two things change for you. Long documents become findable by what is *inside*
  them, which is where most of a real library's value sits. And a result that matched semantically now
  carries a citation — the section heading and line range of the passage that actually matched — where
  before, if none of your literal query words appeared in the text, the result could only name the
  document and point at its top. A document with no indexed passages yet (anything added before this
  release, until it is re-indexed) still matches the way it always did, so nothing becomes unfindable
  in the meantime. Ranking, the relevance-cliff cutoff and the similarity threshold are unchanged;
  only the evidence feeding them got better. Indexing many passages per document also means many more
  vectors to compare, so this release pairs the change with the index described next.

- **Semantic search on a large library got about twenty times faster.** Comparing your query against
  every indexed passage was done one vector at a time in Python, which is fine for a few hundred notes
  and slow for a real library — on a 300-document library with five passages each, measured at roughly
  40 ms of vector work per query, growing in a straight line from there (a 5,000-document library would
  have spent over half a second on every search). PersonalClaw now keeps a vector index inside the same
  knowledge database file and asks it for the nearest passages instead of reading them all: the same
  measurement drops to about 2 ms, and the results are **identical** — same documents, same order, same
  citations — because the index only narrows down which passages to score, and the scoring, the
  similarity threshold, the ranking and the relevance-cliff cutoff are the ones you already had. If
  your Python's SQLite cannot load extensions, search keeps working exactly as before at the old speed;
  it says so once in the log, and `personalclaw doctor` shows a line explaining why search is slower
  rather than leaving you to guess it is broken. Libraries indexed before this release are brought into
  the index automatically on the first search after upgrading.

- **The library you already have becomes searchable by content, without you doing anything.** The two
  changes above only help documents PersonalClaw has broken into passages, and it only started doing
  that on the way in — so everything you added *before* it kept matching the old way, on its title and
  summary alone. That was the gap: on a library of six long documents, asking "how far behind can a
  replica get before it stops serving reads" returned one result, the document whose *title* was the
  closest match and which did not contain the answer anywhere, with no citation, while the document
  that actually answered it was not returned at all. PersonalClaw now indexes the passages of your
  existing documents in the background, starting the next time it launches, and after that the same
  question returns the right document first and cites the section that answers it — the heading and the
  exact lines. Three things about how it does that. It works in small batches, so a library of any size
  costs the same in memory; it can be interrupted at any point — quit, crash, power cut — and picks up
  exactly where it stopped, without redoing or skipping a document; and searching *while* it is partway
  through is safe: documents it has not reached yet keep matching the way they always did, so nothing
  becomes unfindable in the meantime. Running it a second time does nothing, by design. It reads your
  documents and adds passages; it never rewrites anything you can see, and it leaves the whole-document
  index alone. Expect the database to grow — measured on a 300-document library, indexing 2,100
  passages took it from 1.5 MB to 11 MB and took about nine seconds. If no embedding model is available
  yet, it says so in the log and waits for the next launch.
- **Anthropic models now reuse the stable head of a conversation instead of re-reading it every
  turn.** Anthropic bills prompt content it has already seen at a fraction of the normal input
  price, but only when the request marks where the reusable part ends — and PersonalClaw never sent
  that mark, so every turn paid full price for the same assembled context, memory and skills. It is
  sent now, on the last piece of stable content in each request, which means from the second turn of
  a conversation onward that whole prefix is billed at the reduced rate and comes back faster.
  Nothing about *what* the model is told changes: the same words in the same order, just flagged as
  reusable. Only Anthropic-family models are affected — providers that cache on their own
  (OpenAI-compatible endpoints) already benefited from the prompt reordering in the previous
  release, and a provider with no cache support sends byte-for-byte the request it sent before.
  Anthropic reports how much of each request it served from cache; showing that back to you as a
  per-turn saving is still to come.
- **The Retro Terminal and Claw Arcade personalities now skin the error surfaces too.** Picking one
  of the two shipped personalities in Settings → Design changed the palette, the wordmark and the tab
  title, but a failed page and the incident banner still looked exactly like the default identity —
  the two moments where an identity is most noticeable stayed generic. Retro Terminal now draws them
  as a hard-edged mono-type terminal frame and Claw Arcade as a dashed cabinet panel. This is a
  **skin and nothing else**: the wording, the Retry and Resume buttons, and the fact that the incident
  banner announces itself to a screen reader are byte-for-byte the same under every personality, and
  each treatment's colours are checked against WCAG AA in both light and dark before it can ship
  (worst measured pairing 4.98:1). If you use a standard colour scheme — which is everyone who has
  not deliberately picked a personality — **nothing changes**: both surfaces render exactly the markup
  they did before, asserted against the previous release's output rather than promised.
- **The Retro Terminal personality now lays a CRT raster over the whole shell.** Picking it in
  Settings → Design recoloured the app and renamed the wordmark, but the shell itself still looked
  like an ordinary web app — the one thing a terminal identity is supposed to feel like was missing.
  It now draws a fine lattice of scanlines across the app with a single soft band travelling slowly
  down it. The raster takes its ink from the active colour scheme rather than a fixed green, so it
  belongs to whatever palette the personality names. **If you have Reduce Motion turned on, the band
  is not drawn at all** and you get the still raster — and turning the system setting on or off takes
  effect immediately, without reloading. The overlay cannot be clicked and is invisible to screen
  readers, and it deliberately sits *under* dialogs, toasts and the update overlay, so anything asking
  you to make a decision stays crisp. On any standard colour scheme — everyone who has not
  deliberately picked a personality — **nothing renders and nothing downloads**: the overlay ships as
  its own small chunk that is only fetched when a personality that uses it is active.

- **A goal loop's judge verdict now shows you what the supervisor checked for itself.** When a loop
  declares something checkable — a verify command, or a named deliverable file — PersonalClaw does
  not take the worker's word that it worked: it runs the command and reads the file itself, and
  weighs that over the worker's account. It has done so for a while, but only the *judge* saw the
  result; you got a one-line reason and no way to tell whether it rested on an independent
  observation or on the worker's own narration. A cycle's verdict panel now lists what was
  independently observed — the command it ran and what that command returned, and each deliverable
  it read — so "done" comes with its receipts. A cycle with nothing checkable to observe says
  nothing new, which is the honest answer for a goal that named no anchor. Under the hood this is
  one verdict record instead of three: the loop's private vocabulary was folded into the workflow
  judge contract, so a loop cycle and a workflow gate now describe "was this good enough?" the same
  way, including the marginal-value and regression signals that drive when a loop decides it has
  stopped making progress. Verdicts stored by an earlier version still display correctly.
- **"Reduce motion" now actually stops the springs — and the Bounciness slider reaches everything it
  claimed to.** If your operating system is set to reduce motion, PersonalClaw relied on a framework
  setting that neutralises movement *across the screen* but leaves the underlying spring running, so
  anything animating opacity or a blur still bounced its way in. Every spring in the app now collapses
  to an instant swap under that setting, in one place, so a new animation cannot escape it. Separately,
  menus and popovers were reading your **Bounciness** value once when the app loaded and then ignoring
  it: moving the slider changed nothing for them until a full reload. They now read it every time they
  open. The app's spring presets are also down to one named set of four — *snappy*, *smooth*, *fluid*
  and *playful* — so motion is consistent between surfaces that used to pick from two overlapping
  lists; a few entrances (dialogs, the update overlay, the composer) are slightly quicker and bouncier
  as a result, and Settings → Design → Motion tunes all of them. **Three new sliders in that same
  group** control drag and swipe feel: how far a dragged card stretches past its edge, and the flick
  speed *or* distance at which a swipe dismisses it (a toast can now be flicked away quickly or hauled
  away slowly — previously only one worked, at values you could not change).

- **Housekeeping now runs when your machine actually needs it, instead of on a fixed clock — and
  one system does it, not two.** PersonalClaw's minute-by-minute heartbeat used to carry its own
  maintenance schedule: rebuild the memory search index every 15 minutes whether or not anything
  had changed, and once a day trim old daily-history files, trim the security event log, and age
  the skill library. The self-healing Maintenance engine on the Doctor page (Settings → Doctor) was
  doing the same kind of work from measured evidence, so the two overlapped. Those four passes are
  now jobs the engine owns and schedules from what it can measure — how many memory files disagree
  with the search index, how many history files are past your retention setting, how many
  security-log entries are past theirs, how many skills are due for aging — so each one runs when
  there is genuinely something to do and is recorded in the Doctor's run history with what it did.
  Two consequences worth knowing. **If you turn the engine off** (`resilience.remediation.enabled`),
  the heartbeat picks all four back up on its original schedule, so you are never left with no
  housekeeping at all. **And the memory search index is now reconciled properly:** deleting old
  history used to leave its text in the search index until the next rebuild, so a search could quote
  a file that no longer existed; the prune now removes both together. Skill *tamper detection* is
  also finally scheduled — a skill whose files changed after installation used to be noticed only if
  you happened to open the Skills page, and is now checked on every maintenance pass and shown on
  the Doctor page (it is reported, not silently "fixed": re-recording a changed skill's fingerprint
  would hide the very change you need to see).
- **A workflow judge now has to show its work, and a PASS it cannot justify is refused.** A `judge`
  gate used to be asked for exactly one word — `PASS`, `RETRY`, `ESCALATE` or `REJECT` — which
  cannot carry a score, a citation or a reason. Every rule the judge contract states ("a PASS
  without cited proof is invalid", "any rubric criterion below its target fails the stage") was
  therefore written down and applied to nothing: on `goal-pursuit-open-ended` the template asked
  for a full verdict object and the engine appended the one-word demand right after it, so the gate
  shipped two contradictory instructions. Judges are now asked for the verdict object and the
  engine validates it: a PASS carrying neither `proof` nor `evidence_refs` is refused, the rubric
  criteria your template declares in `runtime_hints.judge` are compared against their
  `target_score` for the first time, the overall score is recomputed from the dimension scores
  (the model's own number is kept beside it so any drift is visible), the judge's evidence has
  retry/iteration markers stripped before it reads it ("attempt 4 of 5" tells a judge how much
  patience is left), and a gate that opted into `self_judge` now parks for a human instead of
  approving its own work. The six templates whose judge STAGE already produced this object have it
  validated and carried forward — into the review handoff, or into the next cycle's worker prompt
  as the critique to start from — instead of discarded.

  **What you may notice.** Judge answers cost more than they did (an object instead of a word), and
  a judge that replies with prose instead of JSON now fails its gate with a named protocol error
  rather than a guess. Enforcement was deliberately scoped so it cannot break a working template: a
  run that declares no rubric has nothing to fall short of, the prompt spells out the exact score
  keys the check will look for, and a restated key still counts. This is a behaviour change to
  every judge gate and judge stage — `personalclaw snapshot` before upgrading if you have judged
  runs in flight. Templates you wrote yourself that hand-write a "reply with one word" judge prompt
  should drop that line; the engine supplies the shape.

- **A workflow plan now tells you which of its stops survive an unattended run.** The autonomy
  offer routinely recommends `per_stage` while still offering `unattended`, and the plan preview
  listed the confirmations for the *recommended* mode only — so choosing "run it unattended" meant
  giving up an unnamed set of stops. The preview gained an `unattended_interrupts` block naming, per
  confirmation, whether unattended still stops for it and which interrupt fires (`irreversible`, or
  `uninferable` for a credential or payment detail nobody can guess). A credential ask now reports
  as *uninferable* rather than *irreversible* — the same stop, but it tells you to supply a value
  instead of sending you looking for a blast radius. Advisory only: what an unattended run may
  actually do is unchanged, and still decided by the engine's gate policy.
- **A `foreach` with `on_item_error: collect` now has defined behaviour, and it collects.**
  `collect` was accepted by the validator and advertised in the workflow capabilities catalog, but
  no code branched on it: it fell through to the generic container outcome, so it neither halted
  early like `halt` nor tolerated failures like `skip` — a mixture nothing had specified. It now
  means one thing: **run every item to completion, then fail the run if any item failed**, with the
  per-item failures written to the run ledger as one `items_collected` record (item index, label,
  node, failure class and cause). `skip` (the default) and `halt` are unchanged. **If you have a
  spec using `collect`,** its verdict is what it was before — the run still fails when an item
  fails — but the behaviour is now guaranteed rather than incidental, and you can read which items
  broke out of the ledger instead of reconstructing it from per-node events. Use `skip` if you want
  a fan-out's failures tolerated and the run to complete.

- **Security docs now describe what the sandbox actually does — credential-hiding, not
  confinement.** `docs/architecture/security.md` gains an explicit "what the sandbox does and does
  not do" section (no network/process/write confinement beyond `~/.ssh`); the "bounded by
  guardrails" claim is scoped to *unattended* work (interactive chat is never gated); and the
  desktop shell is described as an experimental macOS-only build, not a shipped platform — matching
  what CI actually releases. Documentation only; every change narrows a claim rather than widening
  one.

- **The desktop app can now tell the dashboard what it is actually allowed to do.** The macOS shell
  gained a typed capability bridge — `window.pclawDesktop.capabilities` — covering the microphone,
  screen recording, native notifications, the menu-bar item, a global hotkey and open-at-login. Each
  one can be probed for its real OS permission state and, where macOS allows an app to ask, requested;
  the request raises exactly one system dialog, and a capability already denied routes you to System
  Settings instead of silently doing nothing. Two capabilities are honest about their limits rather
  than guessing: macOS gives an app no way to prompt for Screen Recording and no way to read whether
  notifications are authorized, so those are labelled as such and offer no button that would do
  nothing. On boot the shell registers this manifest with your gateway over loopback, so
  **Settings → Security → Desktop capabilities** shows the truth — and in an ordinary browser tab it
  says "Desktop app not connected" instead of listing native permissions a tab could never grant.
  Apps can reach a capability only by declaring it (`"permissions": {"desktop": ["audio_capture"]}`);
  the gateway mediates every such call, refuses an undeclared one, records the refusal in the security
  event log, and the Store names the capabilities an app asked for before you install it.

- **First run now sets you up with a working model instead of pointing at Settings.** The second step
  of the welcome flow used to be a readiness check: if no model provider was configured it told you
  chat could not run, offered a link to Settings, and left you to find your own way. It is now an
  **Essential apps** step that does the work in place. Four groups are listed from the app catalog —
  a **model provider** (required; nothing else works without one), plus optional **web search**,
  **speech** (transcription/voice) and a **messaging channel** — and for the model you can go from
  nothing to a bound, tested model without leaving the flow: install the provider app, fill in its
  own settings fields, run its real connection **Test**, then pick which model chat should use. If
  the Test fails you see the provider's actual error and can correct it right there; skipping every
  optional group still gets you to a working dashboard. **Nothing installs by itself.** Each app has
  a **Review** step that shows exactly what installing grants it — the same permission disclosure,
  scheduled-job list and scanner warning the Store shows — and installs only when you click that
  card's own Install button. Where you are in the flow, and which of the four you set up, is now
  remembered on the server.
- **Prompt caching is now a switch you can find, in Settings → Models.** Providers that support it
  can be asked to cache the stable front of your prompt — the assembled context that does not change
  from turn to turn — so a long conversation stops re-paying for the same tokens on every turn.
  That was already happening; there was no way to see it or stop it. There is now a **Prompt
  caching** switch beside your model bindings, on by default. It is on by default because caching is
  transparent: the model is shown exactly the same tokens either way, and a provider without cache
  support is unaffected. Turn it off when you are debugging a provider and want caching ruled out —
  nothing else changes when you do. In particular, **what the model is shown and in what order is
  identical either way**: the ordering of the served prompt is a correctness property, not part of
  the caching feature, so the switch does not quietly serve you a different prompt.

- **You can approve what PersonalClaw is waiting on from your phone.** A run that stops to ask
  permission used to stay stopped until you were back at a desk, because the only place the question
  appeared was the desktop dashboard. There is now a phone route at **`#/companion`** — open it on
  your phone (over your tailnet or however you reach your gateway) and it shows every pending tool
  approval with the whole decision on screen: the tool, its **full arguments** (not a truncated
  preview — you should never approve something you cannot read), why it wants to run, which session
  or automation asked, and how long it has been blocked. Allow and Deny are thumb-sized, and the
  answer lands on the same gateway the dashboard talks to, so a run held up by a permission prompt
  proceeds the moment you tap. Two things it deliberately will *not* do: if it cannot reach your
  gateway it says so and offers a retry, rather than showing an empty list that would read as
  "nothing needs you"; and if an answer fails to send, the card comes back instead of quietly
  disappearing. The rest of the companion — running loops, inbox, notifications — is named on the
  page as not built yet rather than shown as empty, and push notifications for approvals are still
  to come. This first release has no app-store app and no offline install; it is a page you open in
  your phone's browser.
- **PersonalClaw can now earn autonomy one action at a time, and lose it instantly.** The safety
  floor used to be binary — an unattended action was read-only, or it was a permission you granted
  when you created the automation — so a reply draft you had approved unchanged forty times still
  asked every time. There is now a per-action-type ladder: **draft only → one tap → run with undo →
  autonomous**, where each action type declares the rung it starts at and a ceiling it can never
  pass (anything that leaves your machine stops below "autonomous" unless its declaration says
  otherwise). The track record behind a promotion is **recomputed every time from what already
  happened** — the approvals in your security event log and the 👎 you have given that action's
  output — and never stored as a score, so it cannot outlive the evidence. Two rules make it safe:
  **PersonalClaw never promotes itself** (clearing the bar files a suggestion; only your click
  grants a rung), and **one rejection demotes immediately** and starts a cooldown before the
  suggestion can come back. `personalclaw incident on` holds every action at "one tap" or below
  until you resume, whatever it had earned. Thresholds live under Settings (`guardrails.autonomy`:
  approvals required, days they must span, rejections tolerated, cooldown, evidence window), the
  grants and demotions are saved to `autonomy_rungs.json` and travel with `personalclaw snapshot`,
  and an unreadable or unrecognized entry grants nothing. This release ships the mechanism; the
  action types that use it, and the ladder panel that shows it, follow.
- **The autonomy ladder now actually decides whether an automated action runs.** Every built-in
  action declares its rung, and an app can declare one for its own action with an `autonomy:
  {floor, ceiling}` block on its provider — which the lifecycle-hook, data-event and
  clock/file/webhook trigger paths all honour. An action held at **draft only** files a proposal
  saying what it would have done; held at **one tap** it files a request for you to decide; at
  **run with undo** it runs and tells you quietly, keeping a handle on what it created; at
  **autonomous** it just runs. Held actions leave a real row in your inbox and a typed entry in the
  automation's history, never a silent stop. Two things an app cannot do: it cannot claim its action
  stays on your machine (PersonalClaw decides that from the network permission the app already
  declares), and it cannot claim the top rung for an action that reaches the network — that request
  is lowered to "run with undo", and both your log and the security event log say so rather than
  quietly overruling the app. Actions that carry no declaration behave exactly as before: the
  denylist, the kill switch and the permission you granted when you created the automation are
  unchanged, and nothing you already run stops running.
- **You can now see, grant and take back what each automation may do on its own.** The autonomy
  ladder had no face: it decided quietly, and there was no way to find out why an action was allowed
  to run unattended, no way to accept a rung it had earned, and — worst — no way to actually undo an
  action that ran at "run with undo", however loudly the notification offered one. Settings →
  Guardrails now lists every governed action with the rung it runs at, a plain sentence saying WHERE
  that permission came from (declared that way · you promoted it on this date, with the record you
  were shown · granted, but held down right now by incident mode), the track record behind its next
  rung, and its demotion history. The same chip rides every row on the Triggers page, so you can
  scan a list of automations and see which ones act on their own. **Promotion is still only ever
  your click** — when an action clears the bar, PersonalClaw files a proposal in your inbox and
  waits; nothing in the system can promote anything, and a request asking for a rung above an
  action's declared ceiling, or during a cooldown, is refused with the reason. Two buttons take
  autonomy back: **Hand back** returns an action to the rung it was declared with, and **Undo** on
  an automatic action's notification (or in the panel) really reverses it — the task an automation
  filed is deleted — **and** stops that action from doing it by itself again. The undo asks the
  provider that created the thing to take it back, since only it knows what "undo" means for its own
  effect, and it works from PersonalClaw's own record of what ran: if that record is gone, the thing
  was already deleted, or nothing installed can reverse it, the undo refuses and says which, and
  crucially leaves the action's earned rung alone — a broken undo request can never be a way to
  quietly degrade what your automations are allowed to do.
- **You can share a chat as a read-only artifact — inside your own instance, never on the
  internet.** Right-click a chat in Chat History → **Share as read-only artifact** and the
  conversation is saved into your artifacts library as a Markdown record, then opened for you. The
  body is the *same* credential-redacted transcript the Export action produces (redaction covers
  your own messages too, which the chat log itself does not), and the artifact is frozen: it can be
  read, downloaded, or deleted, but never edited — a record you can point at, not a document that
  can drift from what happened. It is created only when you ask, on an authenticated request: there
  is no public link, no share token, and nothing shares a chat automatically. Incognito and
  temporary chats refuse to be shared, since an artifact is durable and those chats promise not to
  be. Export is unchanged.
- **A Routing & Efficiency panel in Settings shows which model is efficient for which kind of
  work.** Settings → Routing & Efficiency lets you pick a use case (chat / code & tools / reasoning)
  and a request kind (short chat, code, summarize, extract structured, long reasoning — both
  round-trip the URL) and shows, per model, the real success rate, p50/p95 latency, and cost per
  call for that kind of request, with the ones on the efficiency **frontier** (not beaten on all of
  quality, speed, and cost) flagged and floated to the top. Observation only — it visualizes
  already-recorded telemetry and does not change how requests are routed. A bucket with no data yet
  shows a friendly "fills in as models handle this kind of request" note; a local/zero-cost model
  reads "free," never a misleading "$0.00."
- **A Usage panel in Settings shows what you're spending.** Settings → Usage renders Today / 7-day /
  30-day cost + token totals (the period control round-trips the URL), a by-model and a by-source
  table with each row's share, a cache-savings line, and — when you've set a daily budget in
  Guardrails — a read-only "spent $X of your $Y cap" (automations only; interactive chat is
  uncapped). A period that includes a model with no price row shows a "partial — N unpriced models"
  marker instead of a misleadingly complete figure. Observation only — nothing here caps or throttles
  a turn. This completes cost observability: per-turn, per-conversation, and per-account.
- **The chat header shows what the whole conversation has cost.** A cost chip — e.g.
  `$0.19 · 46k tokens` — appears in the session header once a chat has recorded usage, reading the
  per-turn ledger scoped to that session. A conversation whose models are all priced shows a real
  dollar figure; one that used a model with no price row shows `unpriced` rather than a misleadingly
  precise total.
- **The "Turn complete" line now shows what the turn cost.** When a turn finishes, its telemetry
  line (in the collapsible per-turn details) reports real USD plus in/out token counts — e.g.
  `$0.0123 · 1,200 in / 340 out tokens`. A model with no price row shows `unpriced` rather than a
  misleading `$0.00`, and a cache fragment appears only when the provider actually reported cached
  tokens. Cost is provider-reported when available, otherwise derived from the pricing table.
- **`personalclaw doctor` now reports your SQLite driver and its capabilities.** The Dependencies
  section shows the resolved driver (`pysqlite3` or the stdlib `sqlite3`), its version, and whether
  FTS5 and JSON1 are compiled in — with a fix hint (`pip install pysqlite3-binary`) when FTS5 is
  missing, since the knowledge and memory search paths need it. Under the hood the driver is now
  selected in one place (`sqlite_compat`) instead of seven, so every subsystem shares one honest
  answer.
- **Memory-backed answers cite their sources, and say so when memory is empty.** When a reply
  draws on episodic memory recalled for the turn, it can cite a fact inline as `[Memory N]`, and the
  chat renders each such token as a chip that deep-links to that episode in Settings → Memory. The
  system prompt also instructs the model to answer only from the recalled memory — to say it doesn't
  have something in memory rather than present an un-recalled fact as remembered. A citation resolves
  through a per-message manifest keyed by the memory's record id (never the model's echoed text), so
  a mis-cited or hallucinated `[Memory N]` degrades to plain text instead of a wrong link.
- **A muted agent can be un-muted from its detail page.** When the auto-router stops suggesting an
  agent because you dismissed its chip enough times, the agent's Advanced → Routing status now shows
  that it's muted and offers an Unmute control to make it eligible for suggestions again — previously
  the mute was invisible and irreversible from the UI.
- **Local models now carry a capability matrix and a runtime/license contract from a declarative
  catalog.** A local-model provider can describe its models in a `catalog.json` — per-model feature
  flags (word/segment timestamps, speaker labels, hotword budget, languages), runtime and
  runtime-contract tags, SPDX license, and context/output budgets — which flow to
  `GET /api/models/available` and render as chips in Settings → Models. A non-commercial license
  shows a warning chip at bind time; a deprecated model shows a chip but stays bindable; and a
  download whose weights are incomplete (under 60% of the declared size) is flagged `truncated` with
  a Repair action that re-downloads it. Config-only pipeline repos (no local weights) are never
  mis-flagged.
- **Mid-run steering now takes effect, and the judge leaves a paper trail.** A workflow's decision
  layers are wired into the run loop: an instruction you queue while a loop is running is consumed
  at the next iteration boundary and re-ranks the plan (rather than sitting unread until the run
  ends); every judge-gate verdict is recorded to the Run Ledger with its evidence chain, and a
  human overriding a judge records the divergence — so the flywheel can tell a human-steered
  outcome from an autonomous one. A judge gate that has never rejected across enough runs is now
  flagged as a "nodding loop" and blocked from becoming its kind's default. The loop breaker keeps
  a single authority (no duplicate trip path).

### Fixed

- **An automation fired from a background write could be dropped without a trace when the retry
  it was owed was skipped.** Some fires are recorded on disk instead of running immediately — a
  memory write from the CLI, for example, has no event loop to run an action on, so the fire is
  parked and picked up on the next scheduler tick. If picking it up failed, the fire was marked
  handled anyway and deleted: a warning in the log, and an automation that never ran. It is now
  retried. A failure that happened *before* the automation could have started anything is held and
  tried again on later ticks, up to five times, and the attempt count survives a restart, so a
  temporary problem no longer costs you the fire. A failure that happened *after* the automation
  was handed off is never retried, because running it twice is worse than not knowing whether it
  finished. A fire that can never work — a malformed record, or five failed attempts — is dropped
  once, loudly, with the reason, instead of stalling every automation behind it. Two identical
  fires parked within five minutes of each other now run once, which is the same rule already
  applied to a webhook a sender retried or a file saved twice.
- **A workflow set to `on_overlap: queue` started a second run alongside the first instead of
  queueing it.** The policy did the opposite of its name: with a run already in flight, `queue`
  matched no branch in the code that applies the setting and fell through to "start now", so a
  trigger that fired every minute against a slow workflow stacked runs without bound — the exact
  thing the default (`skip`) exists to prevent. `queue` now means what it says: the start is saved
  as an unstarted run and begins when the run in flight ends, whether that happens while the
  gateway is up or after a restart. The queue holds one pending start (the run after next would do
  the same work with staler inputs), and a start refused by that limit says so — in the trigger's
  recorded outcome and in the log — rather than reporting itself as queued. An unstarted run you
  created yourself is never picked up by this: only starts the overlap policy queued are, and the
  distinction is recorded on the run. Trigger history shows a queued fire as deferred with its own
  reason, so it is no longer indistinguishable from one that ran.
- **The Inbox's Mentions and Email filters could never match anything.** The dashboard has
  filtered and counted items by kind for a while, and the inbox stored a kind per row — but the
  message-source seam every provider builds on had no field for it, so every message a source
  polled arrived as a plain "message" no matter what it really was. Mail landed
  indistinguishable from a chat message, and two of the kind chips were unreachable by
  construction. A source can now state what a message is (`IncomingMessage.kind` — `message`,
  `mention` or `email`) and that value is what gets stored, filtered and counted. Nothing infers
  a kind from the text of a message: a mention is something the source knows from its payload,
  not a guess about whose name appears in a sentence. A source that declares a kind the dashboard
  cannot render keeps its message — the row still arrives as a plain message — and the mistake is
  logged with the source that made it rather than silently accepted.
- **`personalclaw update` was a dead end unless you had installed from git.** If you installed the
  documented way — `pipx install personalclaw`, `pip install personalclaw`, `uv tool install
  personalclaw` — `personalclaw update` printed "❌ PERSONALCLAW_PROJECT_DIR not set — cannot locate
  source tree" and exited 1, because the CLI still ran the old git-only pipeline while the
  install-kind-aware updater the dashboard uses lived elsewhere. The command now behaves per install
  kind: a **wheel install** upgrades itself in place (`-U personalclaw==<latest>`, no source tree, no
  Node) and tells you to `personalclaw restart`; a **git checkout** keeps the fetch + reset + rebuild
  pipeline and honours Developer update mode (ride release tags by default, every commit when it's
  on); a **container** prints the two `docker compose pull` / `up -d` commands rather than pretending
  it can patch an image; a **desktop** install defers to the app's own updater. An install kind it
  does not recognize says what it detected and refuses to guess, instead of falling back to
  `git reset --hard` on a tree it may not own.
- **`personalclaw update` could run `git reset --hard` without anyone agreeing to it.** With
  uncommitted tracked changes, the confirmation prompt used to read whatever was on stdin — so from
  cron, a pipe, or `< /dev/null` it could take a piped "y" and discard your work, or crash with an
  `EOFError` traceback. Without a terminal it now refuses the destructive reset, names the files at
  risk and the remedy (`git stash` or commit), and exits non-zero. Declining at a real prompt still
  exits 0 — that's a choice, not a failure. Untracked files are no longer listed as at risk, because
  a reset does not touch them.
- **A detached-HEAD update fetched a branch that does not exist.** When git reported no branch (a
  checkout parked on a release tag), the updater fell back to a hardcoded branch name this project
  has never used, so the fetch failed with a confusing git error. It now resolves the branch
  honestly: the branch you are on, else the remote's own `HEAD` (read locally, so it still works
  offline), else what the remote reports, else `main`.
- **Deleting a knowledge item mid-enrichment crashed its background pipeline with a noisy
  error.** If you deleted an item within the ~30 seconds its tags/insights were still being
  extracted, the enrichment pipeline hit a `FOREIGN KEY constraint failed` error, logged a full
  traceback, and then tried to record a "failed" status on the row you had just deleted. Deletion
  itself always worked, but the orphaned pipeline made noise it shouldn't. A delete during
  enrichment now aborts the pipeline quietly — the item is gone, so there is nothing left to
  enrich. Genuine mid-pipeline failures (where the item still exists) still record as failed.
- **Renaming the built-in Personal or Repeatable project quietly broke your projects.** The API
  let you rename a default project even though the UI hides the button — and because the system
  re-creates any missing default by name, the rename left you with two Personal (or two
  Repeatable) projects: the original, now holding your task lists but no longer the one new work
  routes to, and a fresh empty duplicate. Worse, neither could be cleaned up through the app.
  Renaming a default is now refused with a clear message (other edits like its brief or workspace
  still work), and the delete guard now protects a project by its current name rather than a
  sticky internal flag, so any stray duplicate left behind by the old bug can finally be deleted.
- **"Run now" did nothing for almost every automation, while reporting success.** Clicking Run now
  (or dry-run's live counterpart) on a schedule or automation reported that it had run, but no
  action executed and nothing appeared in the run history — the Run button just sat on "Running…"
  waiting for a run that had never started. Automations still fired on their own schedule, so
  nothing looked broken until you tried to test one by hand. Manual runs now execute the
  automation's action, and a run that cannot start says so instead of claiming success.
- **A manual "Run now" left no trace and the "Running…" pill never cleared.** Even after Run now
  began actually executing the action, the run was recorded nowhere — the run history gained no
  row, the "last run" time never advanced, and so the animated "Running…" indicator waited forever
  for a completion it could not see. A manual run is now written to the automation's run history
  (tagged as a manual run) and advances its last-run time, so the history updates and the pill
  clears. A manual run that fails is recorded as a failed run rather than swallowed. Testing an
  automation by hand still never counts against its own fire limit.
- **Knowledge ingest reported steps as finished that never ran.** The per-item ingest view marked
  entity extraction, intent matching, and embedding as done on every item regardless of what
  actually happened — so with no embedding model bound, an item that stored zero vectors still
  showed "Embed ✓". Each step now reports its real outcome: done when it did the work, skipped
  when there was nothing to do (no model bound, no intents defined), failed when it errored.
- **Accepting a "refine an existing skill" proposal always failed with an error.** When the
  system proposed refining a skill you already have (rather than creating a brand-new one),
  clicking Accept returned "could not write skill … (invalid, oversized, or exists)" and the
  proposal stayed stuck in the queue forever — the only way out was to reject it and lose the
  improvement. Accept now updates the named skill in place, appending the refinement under a
  dated heading so the original skill and any earlier refinements are preserved. New-skill
  proposals are unaffected, and a refine whose target skill was since deleted quietly falls
  back to creating a new one instead of erroring.
- **Importing a memory file that wasn't a JSON object failed with an unhelpful server error.**
  Handing `POST /api/memory/import` (or `personalclaw memory import`) a JSON list, string, number,
  `null`, or `true` crashed the import instead of telling you the file was the wrong shape. Both
  now say so plainly, and a valid export imports as before.
- **A request that named no task mode relaxed every chat to full execution.** `POST
  /api/chat/task-mode` read a missing `mode` as `agent`, and a missing `session` as "all
  sessions", so one under-specified request could take every chat you had set to Ask or
  Plan and hand it full tool access. An absent or non-string `mode` is now refused the
  same way an invalid one always was, and the response names the sessions it changed so a
  caller can tell a one-chat change from a fleet-wide one.
- **A project could be pointed at your credential directories.** Binding a project's workspace
  accepted `~/.ssh`, `~/.aws` or an OS system tree with no complaint — the same paths the
  terminal refuses to open in — and a chat started under that project inherited the path as the
  working directory of an unsandboxed agent. Binding one is now refused, on exactly the check
  the terminal already used. Editing a project also stops accepting field names it never wrote:
  an unrecognised field now comes back as an error naming it, instead of a silent no-op or a
  blank "server error".
- **The dashboard could be tricked into handing over your secrets by changing the case of a
  filename.** The file panel refuses to open credential files — the session signing key, the
  local secret, the telemetry salt, `.env`, and anything ending in `.key`/`.pem`/`.secret`. On
  macOS and Windows, where the filesystem ignores upper/lowercase, that block was case-sensitive
  and could be walked straight past: asking for `.LOCAL_SECRET` instead of `.local_secret`
  returned the real bytes. The same hole let the write endpoint clobber a protected file under a
  different-case name. The block is now case-insensitive and also compares file identity, so no
  spelling — including hard links or Windows short names — reaches a protected file, in either
  the read or write direction.

- **One app could borrow another app's permission to run an agent, and read agent runs that
  weren't its own.** The permission check on the app agent-run endpoints read the app name out of
  the URL instead of asking who was actually calling, so an app allowed to reach `/api/apps/*`
  could name any agent-permitted app in the path and get an auto-approving background agent run.
  Separately, polling a run's status never checked who owned the run: any app holding the `agent`
  permission could read the task text and result of runs started by the dashboard, a schedule, or
  a different app. Both endpoints now gate on the calling app's verified identity, and a run is
  only readable by the app that started it. No app in the Store declares the `agent` permission
  today, so nothing shipped was exposed — this closes the hole before the first app needs it.
- **Discover's "see goal loops" tip opened a blank new-loop form instead of your loops.** The
  button pointed at an address the app no longer serves, so it redirected to the composer and
  asked "What do you want to accomplish?" — the opposite of showing you what already exists. It
  now opens the loop list.
- **Changing your embedding model silently stopped the assistant remembering anything.** If the
  embedding model changed after the memory index was built, every attempt to save a new memory
  failed — and nothing said so. Recall and the nightly consolidation pass broke the same way for
  the same reason. Memories are now kept whenever this happens, with a warning naming both sizes
  and telling you to re-embed, so the worst case is that older memories are temporarily missing
  from semantic search instead of new ones being lost entirely. If you have hit this, your
  memories are still there: re-embed to bring them back into search.
- **A task comment could be signed as anyone, and never taken back.** The comment endpoint took
  the author straight from the request, so any client reaching your gateway could post a comment
  under your name — and with no delete, a forged one was permanent short of hand-editing files.
  The author is now derived on the server from your configured username, sending an `author` is
  rejected outright rather than quietly ignored, and comments can be deleted from the task panel.
  A malformed comment body now answers 400 instead of failing with a server error.

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

- **Sign in from outside your home network.** Reaching your own dashboard while away used to
  mean being at the machine — the only way in was a token link you had to mint locally. You can
  now set a password (`personalclaw auth set-password`) and turn on a sign-in page, so a browser
  anywhere can log in for a session. **It is off by default and it is additive:** the token link
  and the loopback paths keep working exactly as before, and they stay the way back in if you
  ever forget the password — a login you misconfigure cannot lock you out of your own box.
  Optional 2FA (`personalclaw auth totp setup`) adds a time-based code. Failed attempts are rate
  limited with a lockout, and every attempt is recorded in the audit log.
- **Pair a phone without typing your password into it.** `personalclaw auth enroll` prints a
  short code you enter once on the other device. It works exactly once, expires in five minutes,
  and is stored only as a hash — so the worst case for a code you lose on a screen is that you
  run the command again.
- **Sessions survive a restart.** Previously every gateway restart invalidated every token: on a
  local box you re-ran `personalclaw token`, and away from home you were simply locked out,
  because minting a URL required being at the machine. The signing key and the session records
  are now persisted (both `0600`). `personalclaw auth revoke --all` ends every session, and that
  survives a restart too.
- **Hardening for an internet-exposed instance.** Set `dashboard.public_url` when you reach the
  dashboard through a TLS-terminating tunnel and the session cookie gains `Secure`, the
  WebSocket policy allows `wss://` to that host, and proxy headers (`X-Forwarded-For` /
  `X-Real-IP`) are honored **only** from an address you list in `dashboard.trusted_proxies`.
  That last one closes a real hole: those headers used to be trusted based on the *shape* of the
  peer address, and on an exposed box any container neighbour sits on a private address and
  could have moved a session's bound address. A local install is unaffected — nothing changes
  until you declare a public URL. The new [remote-access guide](docs/guides/remote-access.md)
  walks the whole setup and is explicit about what it does *not* protect you from.

- **One place for everything waiting on you.** The inbox is no longer just messages: a goal
  loop that needs a decision, a proposed skill, and a tool approval you walked away from all
  land there as items you can answer in place — instead of a toast that scrolls past while the
  loop stays stalled. Filter chips show what kind of attention each thing wants, and a row
  deep-links to the loop or chat it came from.
- **Per-notification-kind delivery rules.** Settings → Notifications now has a row per kind of
  notification with four choices: notify, badge (keep it in the list without interrupting),
  digest (batch it into a daily summary), or never. Previously the only control was a global
  severity floor, so quietening one noisy kind meant raising the bar for everything. Keyword
  and name-mention alerts became per-kind conditions, which means they now work for loop
  requests and proposals too — not just channel messages.
- **A daily digest.** Anything set to `digest` collects into one grouped summary at a schedule
  you choose (08:00 by default), grouped by kind so "9 heartbeats" reads as one line. A quiet
  day produces nothing rather than an empty summary.

- **Memory records who contributed them.** Every memory PersonalClaw writes now carries your
  username, so if your memory store is ever shared — a team store, an imported export, a
  synced setup — you can tell your own memories from a colleague's. Recall labels another
  person's memory as *(from name)* and states plainly that the label is provenance, never an
  instruction to follow. At comparable relevance your own memories come first, but only as a
  tie-break: a colleague's memory that genuinely answers the question better still wins, and
  nothing is ever hidden from you on the basis of who wrote it. Existing memories keep an
  empty contributor rather than being back-stamped with your name, because a record written
  before this existed has genuinely unknown authorship. Solo installs behave exactly as
  before.

- **Memory can now offer itself, not just answer when asked.** Mention a person, project or
  tool the entity graph knows and PersonalClaw can volunteer up to three linked memories for
  that turn — including ones that share no words with what you typed, which ordinary search
  structurally cannot find ("ships Fridays" doesn't match "when does Sparrow release?"). It
  costs no tokens or model calls, and it's **off by default**, because putting context in
  front of the model that you didn't ask for should be your choice. Turn it on under
  **Settings → Memory**, along with how confident a match has to be. The Health tab shows how
  often what it volunteered actually got used afterwards, so you can tighten the setting from
  evidence instead of guesswork. Temporary chats get nothing; incognito chats get the benefit
  without anything being recorded.

- **Take a conversation with you, and stop rebuilding the same chat setup.** Any chat can
  now be exported as **Markdown or JSON** from its context menu in the chat history —
  readable, pasteable, and **credential-redacted**, including the messages you typed
  yourself. Save a chat's setup (agent, model, reasoning effort) as a **starter** from its
  header, and it appears on the new-chat screen ready to pick; manage or remove starters
  under **Settings → Chat**. A starter captures the setup only, never the conversation.

- **Hand an artifact to the agent, or point at one mid-conversation.** An artifact — a
  widget, a document, a chart — can now be opened straight into a chat set up to *change*
  it: the agent starts with the current version in front of it and a prompt that names the
  artifact, so a revision lands as a new version of that artifact instead of a
  near-duplicate beside it. And in any chat you can now reference artifacts from the
  composer's **+** menu, which grounds the reply in whatever those artifacts say *right
  now*. Each reference is recorded on the artifact's own timeline, so you can see where it
  got used and jump back to that conversation.
- **Shelves for your knowledge library — including ones that fill themselves.** Saved
  documents, notes and links were one flat list. You can now group them onto
  **collections**: a *manual* shelf holds whatever you put on it, and a **smart** shelf
  holds whatever matches a search you name once — so "everything about the borrow
  checker" stays current on its own as you save more, with nothing to re-run and no
  backfill step.

  Items can sit on several shelves at once, and a shelf is a *view*, not a container:
  deleting one leaves every document in your library untouched (the confirmation says
  so). Each shelf has its own URL, so you can link straight to one. Alongside it, items
  gain a **reading state** — unread → reading → read, because "reading" is the state a
  reading list exists to represent — and a **favorite** star. Marking something read
  deliberately does *not* count as editing it, so working through a backlog won't
  reshuffle a library sorted by recency.
- **Clean up a long chat list in one action, and let old chats retire themselves.**
  Conversations pile up, and until now the only tools were one-at-a-time. You can now
  select many chats at once and **archive**, restore, tag, re-file, or exempt them in a
  single action — and chats you haven't touched in a while (30 days by default) move to
  **Archived** on their own.

  Archiving is not deleting, and that's the point: an archived chat keeps its full
  transcript, **stays searchable**, and is one click from coming back. That's what makes
  it safe to do automatically. Anything you want kept in the list forever can be pinned
  "never archive", and opening or replying to an archived chat brings it back by itself.
  Set the window in Settings, or set it to 0 to switch auto-archive off entirely.

  Two deliberate limits: bulk **delete** is not offered beside archive — irreversible
  actions shouldn't sit one mis-click from reversible ones — and chats with no recorded
  activity yet (everything from before this shipped) are never auto-archived, so
  upgrading can't sweep away your history.
- **On a shared task board, your assistant only works on *your* tasks.** If tasks come
  from somewhere other people also write to, task rows now show who a task belongs to,
  and you can switch between "Mine" and "Everyone" — the filter appears only when
  someone else's work is actually there, so nothing changes for a solo setup.

  The part that matters most is invisible: ready-task counts, the "what should I do
  next" picker, and the agent's own work selection all count and choose **only your
  tasks**. Someone else's task can never quietly become something your assistant picks
  up. Dependencies are still honored across everyone, so a task of yours blocked by a
  colleague's unfinished work is correctly *not* ready rather than falsely startable.
- **Decks and PDFs too — and anything already saved can become a document.**
  **deck_create** turns a markdown outline into a real PowerPoint deck: each `##` is a
  slide, the lines under it become bullets, and `<!-- notes: ... -->` becomes that slide's
  speaker notes. PDF generation works on every install rather than depending on whatever
  converter happens to be on the machine.

  And the library now works in both directions: point `document_create` at a saved
  knowledge item or a note you already have, and it comes back out as a Word document you
  can send. Same generator, so an exported document is no different from a freshly
  written one.

- **It can make you a Word document or a spreadsheet you can actually send.**
  PersonalClaw could read .docx, .xlsx, .pptx and .pdf but could not produce a single
  one — everything it generated stayed inside the app. Ask for a document now and you get
  a real file: **document_create** turns markdown into a Word document (headings, bullets,
  numbered lists, tables, code, page breaks) and **sheet_create** builds a spreadsheet
  with a bold header row and frozen panes.

  Numbers stay numbers, so the result can be summed and charted — a spreadsheet full of
  text-formatted numbers is the main way generated ones turn out useless. Both land in
  your Artifacts library with version history, so re-generating one updates it in place
  instead of leaving a near-duplicate beside it, and each has a download button plus a
  text preview.

  Verified by opening the output in real applications, not just our own reader: macOS
  identifies the files as genuine Office documents and renders them correctly.

  Two things this fixed along the way. **Tables in Word documents you upload were being
  silently dropped** — the reader only looked at paragraphs, so often the densest
  information in a document was invisible to search and to the agent. And **generated
  videos were being stored as images**, which made them unplayable; video is now a real
  artifact type with a working player.

- **Tags are a real taxonomy now — nest them, rename them, merge them.** Tags were a
  flat list of strings stapled to each item, so a typo meant editing every item that
  carried it and there was no way to express that "tokio" is a kind of "rust". The new
  **Tags** view on the Knowledge page shows every tag with how many items actually use
  it, and lets you rename, nest one under another, merge two together, or delete one —
  from a right-click.

  Renaming is instant and applies everywhere at once. Merging moves every item onto the
  surviving tag and takes nested tags with it rather than orphaning them. Deleting a tag
  removes it from your items but never deletes the items, and its nested tags become
  top-level rather than disappearing with it. Unused tags are kept on purpose: a tag you
  built is part of your taxonomy even when nothing carries it this week.

  Two things this quietly fixed. **Tags with non-Latin characters were unsearchable** —
  a tag like 日本語 was stored in an escaped form the search index couldn't match, so it
  simply never came up; it does now. And the tags you write by hand are now marked as
  yours, so automatic enrichment can refresh the tags it generated without ever
  overwriting one you chose.

- **Your reading state and favorites are now visible, and filterable.** Marking a saved
  item as reading, read, or a favorite already worked — but nothing showed it anywhere,
  so favoriting was effectively write-only: you could star something and then had no way
  to find it again. Items now carry a **star** for favorites and a **reading** badge, a
  read item's title dims, and chips let you filter to Reading, Unread, Read or Favorites
  (each appearing only when there's something to show, with a count).

  Favorites finally have their own mark instead of borrowing the pin icon — pinning
  floats an item to the top of the list, favoriting is a personal bookmark, and they read
  as different things now. The reader gained the same two controls beside Pin and
  Archive. Unread items stay unbadged on purpose: it's the default state, and marking
  every new item would just be noise.

- **Curate a whole shelf of saved items in one action.** Working through a knowledge
  library one item at a time is what makes nobody do it. Select many items and **mark
  read or unread, favorite, add to a shelf, or archive** them together. Each action
  reports what actually happened — "38 shelved · 2 already there" — because a selection
  can go stale between the click and the request, and a partial success is not a failure.

  Marking a backlog read deliberately does *not* count as editing those items, so
  catching up won't reshuffle a library sorted by recency. Bulk **delete** is not
  offered beside these: everything here is reversible, and an irreversible action
  shouldn't sit one mis-click away from a safe one.
- **See what changed between two versions of an artifact.** An artifact keeps every
  version, but the only way to tell what actually moved between two of them was to open
  each in turn and compare by eye. **Compare versions** now shows a real side-by-side
  diff — for images, the two versions themselves, before and after. It opens on the two
  most recent versions, since "what changed in the last pass?" is usually the question,
  and you can pick any pair or swap which side is which.

  Whitespace-only changes are shown rather than hidden: an agent re-rendering a widget
  often re-indents it, and reporting "nothing changed" would be a lie.
- **Backups now happen on their own, and they get checked.** PersonalClaw takes a
  full snapshot nightly and exports whatever changed every hour, so how much you can
  lose is bounded by an hour rather than by when you last remembered to run
  `personalclaw snapshot`.

  Retention keeps a *spread* instead of a window — two weeks of daily snapshots, then
  weekly, then monthly — so a year of history costs about 30 files and you can still
  reach back to January. Settings → **Backups** is where all of this lives: when each
  job last ran, a button to run one now, how long each tier is kept, and the snapshot
  list marking exactly which files the current settings would remove — before anything
  is removed. Restoring stays a command-line action, and the screen says so, because a
  restore has to replace live state while the gateway is stopped.

  Once a month it also runs a **restore drill**: the newest snapshot is unpacked into
  a temporary directory and every database inside it is integrity-checked, then you
  get a pass/fail notification. A backup nobody has restored is a hope, not a backup —
  and a failure is reported as a warning, so it isn't hidden by quiet hours.
  `durability.auto_backup` turns the schedule off if you'd rather do it by hand.

  Fixed while building it: the incremental export had been unable to notice memory
  changes at all. Databases run in WAL mode, so a saved change lands in a companion
  file and the main database's timestamp never moves — the export saw "nothing
  changed" through an entire session of work.
- **Find any chat by what was said in it.** Chat search now runs against a real
  full-text index of your transcripts instead of scanning the 500 most recent files,
  so a conversation from months ago is as findable as yesterday's — and each result
  shows the matching passage with your terms highlighted, so you can tell at a glance
  which chat is the one you meant.

  On a 120-chat history a content search returns in about 30 milliseconds. The index
  keeps itself current as you chat and repairs itself on a schedule, so there is
  nothing to maintain. **Incognito and temporary chats are never indexed** — and a
  chat you switch to incognito after the fact disappears from search immediately.
  If the index is ever unavailable, search quietly falls back to the previous
  behavior rather than failing.

  Fixed along the way: a chat marked incognito *after* some of it was written could
  still appear in content search, because only its saved mode was checked and that
  still read "persistent". Both search paths now honor the live setting.
- **The agent navigates your code by symbol instead of grepping blind.** Asking
  "where is this function defined and what calls it?" used to cost a grep, a couple
  of file reads, and often another grep — every round-trip spending tokens on
  navigation instead of the actual work. A new `code_map` tool answers it in one
  call from a tree-sitter index of your workspace, and `code_map_overview` gives the
  shape of an unfamiliar codebase (the most-referenced modules and their public
  surface) in one read.

  Indexing Python, TypeScript, JavaScript, Rust and Go: on a 1,500-file repository
  the first pass takes about four seconds and later passes are effectively
  instant, since only changed files are re-read. The same index makes SDLC planning
  passes start from a map of the codebase rather than exploring for it, and ranks
  the chat composer's `@` file picker so widely-used modules surface above
  same-named leaves.

  It is strictly an accelerator: with no index, no parsers, or an unparseable file,
  everything falls back to the grep-and-read behavior it had before, and the tool
  says so plainly rather than guessing.
- **Memory now knows what it's *about*.** Every memory is linked to the people,
  projects and tools it names, so asking "what do I know about Ana?" follows those
  links instead of hoping a similarity search surfaces everything. A memory about a
  standup, a stored fact about a repo, and a lesson from last month finally connect
  when they concern the same thing.

  Linking happens the moment a memory is written and **costs nothing** — no tokens,
  no model call, just exact-name matching against the people and projects you've
  named. Links are typed, so the graph distinguishes a memory that's *about* you
  from one that merely mentions a project.

  Unknown names are **proposed, never invented**: a name that shows up across three
  separate memories appears in Memory → Health for one-click accept, because a junk
  entity quietly degrades every future search. Everything is reversible through the
  existing memory undo. Find it under **Settings → Memory → Health**, where
  *Rebuild links* seeds entities from what you've already stored and links your
  whole history in one pass. `memory.graph_enabled` turns it off — existing links
  are kept, so turning it back on needs no rebuild.

  > **Note (0.x clean break):** this adds tables to `memory.db` (schema v7). Old
  > stores upgrade in place on first run with no data loss and nothing to migrate;
  > consider `personalclaw snapshot` beforehand, per the pre-1.0 banner.
- **Your IDE can now actually ask your assistant things.** The MCP endpoint ships with
  six read-only tools: recall what the assistant remembers, search your saved documents,
  list or read a task, search past conversations by what was said, and check what the
  instance can currently do. Every answer is wrapped as data rather than instructions,
  conversation results are credential-redacted, and temporary or incognito chats are
  never searchable. There is no path from any of these to a write — the tool list is
  short and hand-written precisely so that stays true.

  **Security fix found while building it:** credential redaction did not recognize LLM
  provider API keys. Anthropic, OpenAI, GitHub, and Google keys pasted into a chat were
  invisible to the redactor, so they could survive into anything that strips secrets on
  the way out — session-search results and conversation titles included. All of those
  key shapes are now redacted everywhere redaction applies.
- **Point your IDE at your assistant: a read-only MCP endpoint.** PersonalClaw can
  now answer questions from a local MCP client (your IDE, an MCP inspector) over
  `POST /mcp` — JSON-RPC 2.0, with `initialize`, `tools/list` and `tools/call`.
  It is **off until you deliberately turn it on**, and turning it on takes two
  separate steps that are both re-checked on every request:

  ```bash
  personalclaw inbound token create mcp        # printed once — copy it now
  personalclaw config set inbound.mcp.enabled true
  ```

  Because both are checked per request, `inbound.mcp.enabled false` is an immediate
  kill switch — no restart. The surface answers **loopback callers only** unless you
  explicitly declare a public URL and opt into remote access in the config file;
  neither of those knobs is editable from the dashboard, so widening your network
  exposure can't be one mis-click in a browser. Requests are capped on every
  dimension an outside caller controls (body size, rate, concurrency, result size),
  refused requests come back with a real JSON-RPC error, and every request — allowed
  or refused — is recorded in `<home>/inbound_audit.jsonl`, with refusals also
  landing in the security event log. This release ships the surface with **no tools
  yet**: a client can connect and see an empty table. The five curated read-only
  tools (memory, knowledge, tasks, sessions, status) follow next, and by
  construction they can only ever read — there is no path from an inbound request to
  a write.
- **Tool groups: the agent loads the tools it needs, not all of them.** Every tool
  provider is now an activatable **group** (`schedule`, `artifacts`, `memory`, one
  per MCP server or app, …), and a session can run with only the groups it needs —
  so unused capabilities cost one line of catalog instead of every schema. Measured
  on the 69-tool built-in surface: a background session's tool block drops **56%
  (~6,900 tokens per turn)**. The agent manages this itself via a new `reset_tools`
  tool that takes the *final* set of groups it wants.
  Capability is never reduced, only context: **every tool stays callable by name
  even while its group is inactive**, each inactive group advertises itself in one
  line, and `tool_search` still searches everything — naming the activation step
  when it finds a tool in an inactive group. Off by default (`tools.groups_enabled`),
  and interactive chat keeps every group active even when on, so nothing changes for
  the chat you're watching; background, loop, and subagent runs start focused
  (tune per surface with `tools.group_defaults`). `GET /api/tools` now reports each
  tool's group.
- **The artifacts library: live previews, search, and collections.** The new
  Artifacts page is now a real library: a responsive grid where every card renders
  a **live preview** — widgets/HTML/React/documents/SVG in the same sandboxed,
  theme-injected frame chat widgets use (scaled down, inert, never re-implemented),
  images from their raw bytes, prose as an excerpt. Previews mount lazily as cards
  approach the viewport and at most a dozen live frames exist at once (older ones
  quietly demote to placeholders), so a 200-artifact library scrolls smoothly. The
  toolbar filters by text, kind, source, and **collection** — all URL-backed, so a
  filtered view is shareable — plus Recent/Name/Kind sort. Opening a card gives the
  full-page detail (version rail, events timeline, edit/snapshot/revert) with
  `?v=N` deep-linking a historical snapshot read-only, and a header control to
  assign the artifact to a collection. File-backed cards show a "source changed"
  drift badge.
- **Artifacts get their own page.** Artifacts — the named, versioned outputs agents
  produce — moved out of the Files page into their own top-level **Artifacts** nav
  entry (`#/artifacts`, deep-linkable per slug). Files now shows only your raw file
  roots; old `#/files/<slug>` artifact links (in past chats and event timelines)
  redirect automatically to the artifact's new home, and "Save as artifact" from a
  file jumps you there. The viewer itself is unchanged — render, edit/snapshot,
  version history + revert, and the events timeline all work exactly as before.
- **Artifacts: collections + save-time dedup.** Saved artifacts can now carry a
  **collection** label (a free-form grouping for the coming library), settable at
  save time and reassignable later, and filterable via `GET /api/artifacts?collection=`
  and the `artifact_list` tool. And saving no longer silently mints duplicates: a
  fresh `artifact_save` (or `POST /api/artifacts`) whose name matches an existing
  artifact now **refuses with a hint** — the tool tells the agent to update the
  existing slug or pass `force`, and the REST route returns `409 similar_artifact_exists`
  with the existing slug (bypass with `?force=1`). File-backed saves keep their
  existing source-path dedup. Pre-existing artifacts load unchanged (tolerant read).
- **Agent routing: suggest the right specialist, never route silently.** Give an
  installed agent a **Specialty** and comma-separated **Routing hints** (in the agent
  editor), and when a message in a default-agent chat clearly fits it, a quiet
  "route to `<agent>`?" chip appears above the composer. One click re-targets the
  session (via the existing agent-switch path); the ✕ dismisses it and suppresses
  that agent for a cooldown (three dismissals mute it until you re-enable). It is a
  **proposal** — nothing changes until you click — and classification is
  deterministic-first (keyword-phrase overlap, then embedding cosine when an
  embedding model is bound), with the LLM never in the hot path. Silent auto-routing
  is deliberately out of scope. Route/dismiss also feed the routing pair's accuracy
  into Settings → AI feedback. Tune it in Settings → Chat → Agent routing
  (`agents_routing.*`); zero behavior change until you author routing metadata.
- **Chat craft: seven chat-surface mechanics.** The chat surface gains the pieces
  the sibling platforms proved out. **True rewind** — edit ANY past user message and
  replay from there; the discarded answers are kept in this chat's history (viewable
  under a "rewound from here" divider, restorable as a fork) and the provider context
  rebuilds from the truncated transcript, so the agent never references the undone
  turns. **Queue with manners** — each queued message now has an "Interrupt now" that
  gracefully stops the running turn and runs that message next. **Find in
  conversation** — Cmd/Ctrl+F opens an in-chat find bar (count, next/prev, jump-to-
  match) that highlights every occurrence without ever re-rendering the markdown.
  **Quote toolbar** — selecting transcript text floats a Quote + Copy toolbar; Quote
  inserts an attributed blockquote (who said it) into the composer, now from keyboard
  and touch selections too. **Follow-up chips** — after each reply, 2-3 suggested next
  messages appear via one cheap background call (never blocks the turn; skipped for
  temporary/incognito chats and silent when no model is bound; toggle in Settings →
  Chat). **Smoother streaming** — the reveal snaps to word boundaries so text lands in
  whole words, with a new Settings → Chat "Streaming text reveal" (smooth | immediate)
  control.
- **Background compression keeps long chats fast.** Old, idle conversation history
  is now topic-segmented and compressed in the background on the maintenance
  cadence — the always-on complement to on-demand tool-output projection. A
  transcript untouched for a week (default) is split into topics (by embedding drift
  when an embedding model is bound; a deterministic turn-count fallback otherwise),
  then compressed by attention: the most-recent topic stays verbatim, middle topics
  reduce to their request/response pairs, and the oldest tier is summarized by a
  cheap background model. It only ever touches sessions **at rest** (never a live
  turn), incognito/temporary chats are skipped entirely, every dropped span is
  archived first (fully recoverable) and any tool-result recovery handle is
  preserved, and savings land in the TokenJuice ledger under `bg_topic`. Toggle and
  idle window live in Settings → Chat config (`tools.bg_compress_enabled` /
  `tools.bg_compress_idle_days`); disabling it stops the pass within one tick.
- **Feedback that actually teaches: 👍/👎 on AI judgments.** Inbox classifications,
  drafted replies, digests, and loop findings now carry a quiet thumbs pair. 👍 is
  silent-positive ("Mark accurate" — it only feeds the accuracy denominator); 👎
  optionally takes a one-line "why". Every verdict is attributed to the source that
  produced the judgment — the bound prompt, the loop judge, a workflow's surfacing —
  and per-source rolling accuracy lives in Settings → AI feedback (honest counts,
  shown only after enough verdicts). A source that keeps being wrong **stops
  surfacing** and raises a one-time "retire this rule?" notification with a deep
  link; snooze or clear it after an edit. Everything is deterministic counting —
  no model calls, and feedback never leaves the instance. Apps record feedback on
  their own judgments via `personalclaw.sdk.feedback` / `POST /api/feedback`
  (namespaced server-side, so an app can never impersonate a core source).
- **Investigate anywhere: chat about any entity with its context pre-loaded.** Inbox
  items and loop findings (more surfaces to follow) gain an "Investigate in chat"
  button that opens a fresh chat carrying the entity's full context — composed
  server-side from the owning store, injected as fenced untrusted data on your
  first message (never pasted into your visible text), with the composer pre-filled
  with an editable opening question. The session opens in read-only **Ask** mode —
  investigating never mutates the entity; you escalate the mode yourself. A header
  chip deep-links back to the source. Apps get the same primitive via
  `useInvestigate` in the app SDK.
- **Model use-cases v2: routing sub-categories + fallback chains.** Chat work is
  now routable by kind — `background` (titles, tags, suggestions, digests,
  consolidation), `orchestration` (supervising turns and model-less subagents),
  `loops` (goal-loop workers and judges), alongside the existing `code_tools` and
  `reasoning` — each bindable in Settings → Models under a new **Chat routing**
  group, falling back to your Chat chain when unbound. Bind a cheap or local model
  to `background` and housekeeping chores stop burning your flagship chat model.
- **Every model binding is an ordered fallback chain.** The first model is the
  default; later entries take over when an earlier provider's circuit breaker is
  open or a call fails (background calls advance mid-batch; a failed chain surfaces
  one clear error). The Models panel gains a chain editor with reordering and
  per-entry provider-health dots; the composer's model pick sits above the chain —
  if the picked model fails, the chain takes over.
- **Type-routed tool-output compressors.** Large tool results now project smarter: a
  JSON array of thousands of items becomes a per-field schema (names, types, ranges,
  null counts) plus the first/last item verbatim; a large code file becomes a
  signatures-and-docstrings outline with a line map (`code` is a new content type,
  sniffed conservatively). The full raw always stays one `tool_result_get` away.
- **Projection rules: three layers + line operations.** A builtin rule pack now
  recognises common command output (git, pytest, npm, docker, cargo…) so e.g. a
  `git diff` run through the shell projects as a diff; a repo can ship its own
  `.personalclaw/projection_rules.json` (project layer, beats user rules); and every
  rule may carry declarative line operations — head/tail window, keep/skip filters,
  and a fold-repeats counter — editable in Settings → Tool output.
- **Background prose summarizer.** Long natural-language output on background paths
  can be model-summarized with a guaranteed deterministic fallback (never wired into
  the synchronous tool path).

- **"Investigate in chat" is now on everything worth asking about.** The affordance
  that shipped for inbox items and loop findings now covers eleven more kinds:
  notifications, tasks, schedule runs, triggers, loop cycles, knowledge items, memory
  records and lessons, Doctor findings, crash reports, and audit events. One click opens
  a read-only chat already carrying that entity's context, with the question already
  written for you.
  Failures get the richest context, which is the point: investigating a failed cron or
  loop notification pulls in **the run it's about** — its task, status, latest finding,
  the job's cadence and consecutive-failure count — and asks "why did this fail?".
  A Doctor finding re-runs its probes and brings the offered fix's *dry-run preview*
  (never applying it). A learned lesson brings its provenance and the chain of beliefs
  it replaced, and asks "why do you believe this?". An audit entry brings the other
  entries from the same approval flow, so one decision reads as one story.
- **Tool groups are now visible, and they hide what can't work.** The Tools page shows
  the group partition — every group with its tool count, which ones are always loaded,
  and what each kind of session starts with — plus the switch to turn grouping on or
  off. Each tool provider is labeled with the group it belongs to. And groups whose
  capability isn't configured (subagent tools with no model bound, say) are now hidden
  entirely rather than offered in a state where they'd fail; asking to activate one
  says so plainly instead of quietly doing nothing. New `GET /api/tools/groups` reports
  the partition for anything else that needs it.

- **Personalities: themes that carry an identity, not just a palette.** Settings → Design
  now offers a **personality** — one switch that sets the color scheme, the wordmark, the
  browser tab title, and the interface density together, and can offer the assistant a
  matching name. Two are included as starting points (a mono-green **Retro Terminal** with
  a terse operator voice, and **Claw Arcade**), alongside the default PersonalClaw identity.
  Renaming the assistant is **offered, never assumed**: activating a personality shows a
  toggle naming the exact setting it would change, and declining it switches the look while
  leaving your configuration alone. Switching back restores everything, including the name.
  Every personality's palette goes through the same accessibility contrast checks as the
  built-in schemes, so a personality can't ship an unreadable theme.

- **A username, so your contributions stay attributable.** Settings → Account now takes
  a short handle (Settings suggests one from your name), and PersonalClaw stamps it onto
  things you create — tasks and task comments carry an author. It's a label, not a login:
  nothing signs in with it, and leaving it empty keeps records unattributed exactly as
  before. Renaming it affects future writes only; existing records keep the name they
  were written with, because rewriting them would falsify the very history attribution
  exists to preserve.

- **Backups you can actually read and verify: `personalclaw backup`.** Alongside the
  opaque snapshot tarball, state can now be exported as **deterministic shards** —
  one canonical JSONL file per store plus a SHA-256 manifest. Identical state always
  produces identical bytes, so the export diffs cleanly (adding one task shows up as
  exactly one added line — `git log` over your shards becomes a readable history of
  what the assistant learned) and a future sync never re-uploads data that didn't
  change. `personalclaw backup validate` re-derives every shard's size, row count and
  checksum, re-parses every row, and **exits non-zero** on any problem, so you can run
  it from cron: a backup nobody has verified is a hope, not a backup. `--incremental`
  re-exports only what changed. Secrets are never included — shards are the
  representation that leaves your machine.

### Fixed

- **`personalclaw logout` never actually revoked anything.** It printed
  "All dashboard sessions revoked" and returned success while the gateway refused the request
  (403) and every session kept working — the endpoint was gated behind the very dashboard
  session it was meant to end. Found by running the command against a real gateway rather than
  reading the code.

- **Auto-archive skipped the very chats it existed to tidy — and you couldn't see or
  change the rule.** Chats are archived after a period of inactivity (30 days by default),
  but the sweep only considered conversations currently loaded in memory. Since old chats
  aren't loaded until you open them, a conversation idle for months was exactly the one the
  rule could never reach: it ran hourly and reported that nothing was stale. The sweep now
  covers chats that exist only on disk, archiving them in place without loading them.
  Separately, the threshold had no control anywhere in the app — it now lives under
  **Settings → Chat → Context & lifecycle**, showing how many chats are currently stale so
  you can see what the rule would do before it does it. Archiving remains fully
  reversible; nothing is deleted.

- **The "Steer" button never steered.** Typing while an answer was streaming showed a
  button labelled *"Steer — send into the running turn"*, and clicking it queued the
  message for *after* that turn instead — then displayed a card reading "1 queued · sent
  one at a time as each turn finishes", contradicting the button you had just pressed. The
  message was never lost, but it never did what the label promised. Steering now actually
  reaches the answer being written, and the message appears above the composer marked as
  steered rather than queued.

  Four independent faults each guaranteed the failure, which is why it survived: the
  frontend asked for `followup` regardless of the button; the lookup used a session key the
  session manager never registers, so the steer path could not match a session *at all*;
  the drain lived only after a tool batch, so a plain-prose turn — the most common kind —
  ran past it and discarded the message; and the confirmation event was filtered out as
  status noise, so even a successful steer was invisible. A steer sent to a runtime with
  no delivery path used to be buffered and silently dropped while the API answered
  `{"steered": true}`, growing an unread backlog for the life of the process; it now
  queues visibly instead. Mid-turn handling also gained a **Steer** option in
  Settings → Chat, alongside Queue and Replace — the policy field shipped earlier with no
  control at all.
- **Knowledge and memory could never embed with a config-defined provider.** With an
  embedding model bound to a provider you configured yourself (an Ollama endpoint, say),
  ingested knowledge items sat at "processing" with no embedding **forever** — no error,
  no notification, just nothing. Semantic search and the entity graph had nothing to work
  with, and memory's semantic layer could not embed at all. Chat through the very same
  provider worked, which made it look like embedding was broken rather than unavailable.
  The cause: configured providers are replayed into the model registry during gateway
  startup, but the background embed pass could run before or outside that path and then
  saw an empty registry. It now replays the configured providers itself when a lookup
  misses, so the embed succeeds instead of silently returning nothing — and a provider
  that genuinely is not configured still reports that plainly rather than retrying. (#47)
- **Binding a model can no longer fail silently.** `PUT /api/models/active/{use_case}`
  answered "ok" in two cases where the binding had not actually taken. A request whose
  body never mentioned `models` — an automation or a person reasonably guessing the key
  name — was read as "clear this binding", so it **unset the use-case's model and still
  reported success**. And on a fresh install (a config with no providers configured yet)
  the unknown-provider check was skipped entirely, so a model reference naming a provider
  that does not exist was stored unchallenged as a dead binding. Now a body without
  `models` is a `400` that names the keys it did receive, clearing requires an explicit
  `{"models": []}`, and an unknown provider is rejected whether or not other providers are
  configured. The model *id* is still deliberately not checked against the discovered
  catalog — a real provider that is slow to enumerate its models must not have valid
  references rejected. (#48)
- **Settings and the Store no longer blink to a loading skeleton when you touch
  anything.** Clicking "Check" for updates, flipping a toggle, rotating a key, adding a
  lexicon entry, or finishing an app install tore the whole panel (or the entire apps
  grid) down to a skeleton and rebuilt it — reading as a jarring full-page refresh even
  though the data had barely changed. One shared cause: the stale-while-revalidate cache
  dropped its value the instant a panel asked to reload, so every panel's "no data yet →
  show a skeleton" branch fired on the way to fresh data that was already in flight.
  Reloading now *holds* what is on screen and swaps in the new data when it lands, which
  is what stale-while-revalidate was supposed to mean. Switching to a genuinely different
  resource still clears, so one page's rows can never paint under another's filter. The
  fix is in the shared data hook, so all **88 reload sites across 32 panels** are covered
  at once. (#52)
- **Installing an app and updating PersonalClaw both failed on a `uv` virtualenv.**
  A `uv venv` ships no `pip` module — uv is the installer — but four separate code
  paths hardcoded `python -m pip install`, so each died with `No module named pip`
  on the project's own documented dev setup and on the uv-based end-user install
  path. Installing **any** app that declares `pythonDependencies` failed, and
  Settings → Updates showed "Update failed — pip upgrade failed" forever with no way
  to see why (the real cause was logged but never sent to the UI). Now one resolver
  picks the installer that actually exists — **uv** (targeted at the running
  interpreter, so the packages land where the gateway imports from) or **pip** — and
  says so plainly, naming both remedies, when neither is available. The update panel
  now shows the **real** failure reason instead of a static label, with the
  installer's colour codes stripped and the meaningful line first. The same resolver
  also fixes startup dependency repair and the git-checkout updater's editable
  install, which had the identical assumption. (#46, #51)
- **`personalclaw snapshot` was not backing up everything — and could copy a live
  database unsafely.** Two real problems, both closed. Your **tasks, projects,
  autonomous runs, artifacts, prompts, workflows, agents, installed apps, and
  per-entity settings were in no backup at all** — the snapshot carried a
  hand-written file list that had drifted from what the app actually stores, so a
  "full backup" could silently omit your entire task board. And only two of the
  five databases were copied with SQLite's safe backup API; the rest
  (knowledge, lexicon, autonomous-run records) were copied as plain files while
  the gateway held them open, which can capture a half-written database. In a
  reproduction of that case, a raw copy lost **2000 of 4000 rows**; the fixed path
  captures all of them.
  There is now one declared inventory of every store, which the snapshot and the
  portable export both read — plus a test that fails the build the moment a new
  store or database is added without being declared, so this can't drift again.
- **Snapshots of a non-default home no longer land in your real home.** With
  `PERSONALCLAW_HOME` set, the archive went to `~/.personalclaw/snapshots` anyway,
  mixing two installs' backups in the directory that retention pruning walks.

### Changed

- **Breaking-change policy is now written down, and it distinguishes maintainer from
  contributor.** During 0.x the maintainer keeps making backward-incompatible
  clean-break architectural changes with no migrations — that stays true, and the
  README now says plainly that this is expected to last a while, because
  migration-backed discipline arrives deliberately late, once the architecture stops
  moving. What's new is the other half: **contributors are
  not expected to make breaking changes.** Contributor guidance stays
  lifecycle-shaped — additive by default, no hand-rolled gate or migration
  machinery, and surface a needed break in an issue or PR description instead of
  shipping it, so the maintainer decides whether to take it, reshape it, or schedule
  it. See [CONTRIBUTING.md](CONTRIBUTING.md#breaking-changes); the PR template's change
  class now spells out both paths.

Forward-looking work is tracked in [docs/roadmap/](docs/roadmap/roadmap.md).

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.** PersonalClaw now
  computes a **health score** (100 − measured deficits: knowledge items missing an
  embedding, orphaned stale locks, skills due for aging — each capped, and an *unfixable*
  deficit like "no embedder bound" is excluded rather than held against a score you can't
  improve) and runs a **dependency-ordered remediation plan** to raise it: re-index,
  orphan-prune, skill-age, stopping when the target score is reached, the per-run dollar
  cap is spent, or the plan is exhausted. It runs itself on an **adaptive heartbeat cadence**
  (further apart when healthy, sooner when degraded) and is visible + runnable on demand
  from Settings → Doctor → Maintenance, with a run ledger. Deterministic jobs are free;
  model-touching jobs (future) charge the guardrails spend meter. Every run is idempotent
  (per-job cooldowns) and the whole engine is one toggle — disabling it falls back to the
  legacy per-tick heartbeat maintenance. This is the final slice of the **Platform
  Resilience** program (Doctor · degraded mode · mid-turn · fixes/simulators/crash-capture ·
  this engine).
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what
  crashed.** Three additions to the health surface (all Settings → Doctor):
  **confirm-gated fixes** — a finding that has a repair (a `static/dist` copy shadowing
  the runtime symlink, stale locks/rollback leftovers, model bindings pointing at removed
  providers) shows a **Fix** button with a read-only preview; nothing auto-applies, a
  two-step confirm runs it, and every application is security-audited and touches harness
  mechanics only (never your content). A **per-provider selftest** fires a tiny real
  inference per capability (a one-token chat / short embed) for true ground-truth instead
  of a reachability guess. A **surfacing simulator** dry-runs the skill scorer in explain
  mode — type a query and see, per candidate, the keyword/semantic scores, the thresholds,
  and exactly why each skill was included or excluded (zero model calls). And **structured
  crash capture**: an unhandled failure at a turn/loop/gateway boundary now writes one
  redacted, recoverable artifact under `~/.personalclaw/crashes/` (capped, never uploaded)
  that the Doctor surfaces as a card — a mid-stream death leaves a record instead of a lost
  stack trace.
- **Mid-turn message policy: queue (default) or cancel-and-replace.** A follow-up sent
  while a turn is still generating now follows a *declared* policy. The default,
  **`queue`**, is today's behavior formalized — the message is delivered next turn. Opt
  into **`cancel_and_replace`** (a platform default in Settings, overridable per channel)
  and a rapid follow-up instead cancels the in-flight answer and starts fresh with the new
  message — no stale ghost response, no wasted compute. A per-session debounce coalesces a
  burst of messages into ONE cancel + the last message. The guard is strict: only
  **interactive** turns (the web chat, a channel DM) are ever cancel-and-replaced —
  unattended work (goal loops, cron, subagents, the heartbeat) always queues, so a user
  message can never pull the rug out from under a background job. Built on the existing
  soft-cancel verb and turn-end queue drain (no new dispatch path); a new
  `resilience/active_jobs.py` tracks each turn's origin as the bookkeeping behind the
  decision.
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
  Every model-dependent surface now declares its **LLM-free floor** explicitly, so an
  offline laptop (dead ollama, wiped cache, no API key) degrades by design instead of
  error-walling: search drops from hybrid to keyword (FTS) + graph + recency ranking;
  the inbox keeps raising keyword/name-mention alerts (only auto-classify/draft/digest
  pause); knowledge still captures documents (only entity/insight extraction is skipped,
  marking the item partial); memory keeps its deterministic preference-facet capture;
  speech features turn visibly off rather than erroring; and chat says so plainly rather
  than faking a reply. A compact **degraded chip** appears in the shell (with a popover
  listing each degraded surface, its floor, and any pending-enrichment backlog) whenever a
  surface is running on its floor, and a notification fires on the transition down
  (`warning`) and on recovery (`info`). A lint test asserts every non-interactive
  model-call site maps to a registered contract, so a future surface can't ship without
  declaring its floor. New `GET /api/resilience/degraded`; two guard-class config switches
  (`resilience.doctor_enabled`, `resilience.degraded_indicator` — a missing/unknown value
  keeps the surface visible).
- **A Doctor tab now diagnoses every subsystem from one read-only view.** Settings →
  Doctor runs **tiered health probes** — process → socket → cheap-RPC → per-capability
  — across memory (db + faiss consistency), channels, local models (availability +
  phantom bindings), app backends (+ interrupted-update leftovers), the SPA
  `static/dist` symlink (the stale-SPA bug-class), and model-provider breakers
  (composed from the guardrails audit). The core doctrine is enforced: **a degraded
  capability never marks the gateway down and never suggests a restart** — only a
  core-tier failure does. Every probe is read-only and fail-safe (an exception
  becomes a failed row, never a 500), and secrets are redacted from probe output. New
  endpoints `GET /api/doctor` (all capabilities, cached 30s) and
  `GET /api/doctor/{capability}` (re-run one card); the dashboard System Health strip
  gains a one-line rollup that appears only when something needs attention and links
  to the tab. Confirm-gated auto-fixes and the trust/debug simulators land in later
  Platform-Resilience sessions.
- **First-party apps now appear in the Store on a plain install.** The published
  first-party apps repository (`github.com/PersonalClaw/PersonalClawApps`) ships as
  a default Store source, so a bare `pip install personalclaw` surfaces every
  first-party app — model providers (OpenAI, Anthropic, Bedrock, Ollama, …),
  search, speech, channels — without the dev workspace tree. They appear
  **uninstalled**: nothing runs until you click Install, so the per-app
  install-consent + provider-agnostic-core contracts are unchanged. The source is a
  built-in default (not user-removable); the dev filesystem source and the
  `PERSONALCLAW_FIRST_PARTY_APPS_DIR` override still work for offline/local-clone
  development. The Store's catalog scan is cached (5-minute TTL) and runs off the
  event loop, so the first open clones once in the background.
- **Every non-interactive model call now passes through one guarded seam.**
  Background LLM calls (the `reasoning` axis behind `one_shot_completion`, the
  goal-loop judges, the loop gates, web-extract) are now wrapped in a
  **model-call guard** — the LLM twin of the network egress chokepoint. It adds a
  **per-provider circuit breaker** (opens after N consecutive failures, half-opens
  after a recovery window): during a provider outage an overnight run fails in
  microseconds instead of stacking timeouts. It adds a **hard wall-clock timeout**
  on every call, and an **attempt-level JSONL audit trail**
  (`~/.personalclaw/model_calls.jsonl`, one line per attempt, trimmed to the most
  recent entries) recording provider, model, latency, tokens, and outcome. The
  **interactive chat stream is deliberately untouched** — a human is watching it.
  `one_shot_completion` also gains a typed **`output_type`** option: pass `dict`
  or `list` to require a parseable JSON shape, and a parse miss is retried once
  with a targeted correction note before raising a loud `OutputContractError` —
  replacing the silent `None` degrade that `parse_llm_json` returned at every
  call site (migrated: web-extract, inbox classify). Goal-loop and eval judge
  verdicts gain a bounded **`reasoning`** field written before the verdict, so a
  structured-output constraint no longer suppresses the judge's chain of thought.
  A new graded provider capability descriptor (`structured_output`:
  `none`/`json_mode`/`json_schema`) lets provider apps opt into native
  schema enforcement in a later change; until then every provider gets the
  universal parse-with-retry path. This is a **clean break** (pre-1.0): the new
  audit trail is additive on-disk state under `~/.personalclaw/` — **run
  `personalclaw snapshot` before upgrading** if you want a rollback point.
  (AUTONOMY-GUARDRAILS §2, Session 1.)
- **Unattended spend now has budgets, and outbound prompts are scanned for
  secrets.** A new **Guardrails** settings section (`config.json` → `guardrails`)
  adds daily spend ceilings for unattended work: set a **max tokens/day** or
  **max dollars/day** and when the day's automated spend hits the ceiling, further
  unattended LLM calls are refused (a cron agent fire is skipped with a one-time
  "daily automation budget reached" notification, a subagent spawn is refused) —
  interactive chat is never budget-gated. Spend is metered at the model-call seam
  into `~/.personalclaw/spend.json` (per-day, pruned after 30 days), with dollars
  estimated from the existing per-model price table (provider-reported cost
  preferred). Every outbound prompt bound for a **remote** provider is scanned for
  secrets/PII (AWS keys, private keys, Slack tokens, emails, phone numbers) and
  handled per a configurable **scan mode**: `warn` (log + send), `redact`
  (substitute + send, the default), or `block` (refuse the call); a local provider
  is always `warn` since its content never leaves the machine. The circuit-breaker
  thresholds from Session 1 are now configurable here too (failure threshold,
  recovery seconds). Defaults are **unlimited budget + redact**, so an existing
  install's behavior is unchanged until you set a ceiling. Clean break (pre-1.0):
  additive on-disk state (`spend.json`) — **run `personalclaw snapshot` before
  upgrading** for a rollback point. (AUTONOMY-GUARDRAILS §1.1, §2.2, Session 2.)
- **A kill switch, a path/action denylist, and a live-write guard for unattended
  work.** Three safety-floor controls land. (1) **`personalclaw incident on`** (and
  `POST /api/incident`) suspends every unattended fire — cron, hooks, event
  triggers, subagent spawns — within one poll interval; **interactive chat keeps
  working**, and resuming requires an explicit `personalclaw incident off` (or
  `POST /api/incident/resume {confirm:true}`). Activation/resume are tamper-evidently
  logged. (2) A **path/action denylist** (`security.autonomy_denylist`, rules of
  `{paths, actions, verdict: block|needs_human}`) is enforced at all three
  action-dispatch seams (script hooks, scheduled jobs, memory-event triggers), so
  an app-contributed action provider inherits it without cooperating; it composes
  with the always-on built-in sensitive-path + destructive-command denylists. A
  `needs_human` rule holds the action and raises a needs-input notification instead
  of dropping it. (3) **`PERSONALCLAW_DISABLE_LIVE_WRITES=1`** makes live,
  hard-to-reverse writes (deleting a downloaded model, a non-GET request to a
  non-loopback host) refuse with a loud typed error instead of executing — and it
  is auto-set for the whole test suite, structurally closing the bug class where a
  destructive test once deleted a real bound model. Guard flags parse fail-safe
  (a missing/typo'd value keeps the guard ON), and the outbound scan now defaults
  to `redact` (never the leaky `warn`), enforced by a schema test. Clean break
  (pre-1.0): additive config + an `incident.json` flag file — **run `personalclaw
  snapshot` before upgrading**. (AUTONOMY-GUARDRAILS §1.2–§1.4, §5, Session 3.)
- **A Guardrails settings surface, a provider-health view, and named safety
  profiles.** The safety floor gets its cockpit and its posture layer. A new
  **Settings → Guardrails** panel gathers the incident kill switch (with a
  one-click toggle), the daily spend budgets, the outbound scan mode, and the
  circuit-breaker tuning — and a **provider-health view** derived from the
  model-call audit (per-provider breaker state, pass rate, p50/p90/p99 latency,
  recent failure modes; `GET /api/models/health`, computed from files already on
  disk — no telemetry). A **persistent incident banner** now shows on every page
  while incident mode is active, with inline Resume. Under the hood, **named safety
  profiles** (`interactive` / `coding` / `review-only` / `cleanup` / `incident` /
  `headless`) become the single object that decides approval + tool grants + egress
  tier + budget + scan for a run; unattended runs (cron, subagents, channel, inbox,
  loop workers) resolve to the read-only **`headless`** profile *by construction*
  from their session key, and a curated **package-registry egress tier** lets a
  sandboxed run reach pypi/npm/crates/GitHub/… without opening the whole internet.
  Defaults preserve today's behavior. Clean break (pre-1.0), additive. (AUTONOMY-
  GUARDRAILS §2.5, §3, §4.2, §4.4, Session 4.)
- **The animated dot-wave backdrop is now a choosable background style.** A new
  **Background** control in Settings → Design → Backdrop & motion switches the
  surface behind chat, the new-chat composer, and onboarding between four modes:
  `waves` (the animated breathing dot-wave surface, default), `still` (the same
  dot field frozen — the lattice without the motion), `glow` (only the soft light
  hugging the composer, no dots), and `none` (a plain, empty canvas). The choice
  persists in your appearance settings and applies live with no reload. Motion
  modes still honor `prefers-reduced-motion` (they render one static frame).
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you
  haven't tried, and hands external agents a routed project context.** Three
  legibility surfaces land together. (1) The `ui/` component kit is now
  self-documenting: each primitive ships a `.doc.ts` object (purpose, props,
  best-practice tenet) compiled into `ui-docs.json` at build time, and two agent
  tools — `ui_search(query)` for a budgeted brief and `ui_get(name)` for
  machine-readable props — let an app-building agent find the right primitive
  instead of hand-rolling chrome; a drift test fails the build if a primitive ships
  without its doc. (2) A **Discover** surface guides you through the parts of
  PersonalClaw you haven't tried yet — a hand-authored catalog of user-facing areas
  (Chat, goal loops, automation, Tasks, Projects, Inbox, Knowledge, Memory, Skills,
  Apps), each a one- or two-sentence lesson with a deep link into the page that owns
  it. It is deliberately NOT tool-derived: the tool surface is an implementation
  detail you're never meant to drive by hand. The dashboard shows a rotating
  spotlight of the first few; a dedicated **Discover hub** (`#/discover`, also in the
  command palette) lists every tip grouped by area. A tip leaves the feed two ways,
  both hide-only: an explicit dismiss that persists forever, and an auto-hide once
  you've actually used that area (detected from state that already exists — a chat on
  disk, a knowledge item, a scheduled job…). It only points and hides, never enables
  anything, and the whole surface is behind the `legibility.discover_tips` config
  flag. (3) PersonalClaw
  can act as a **routed-context provider** for external coding agents: per project it
  assembles a tiered manifest — hard rules/brief at the top, scored memories + skills
  + knowledge *pointers* in the middle, and an L0 catalog of what was NOT loaded (with
  the tool to pull each) at the bottom — exposed as the in-process `get_context` MCP
  tool and, opt-in per project (`legibility.context_adapters`, default off), rendered
  into the project's `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside a
  `<!-- PCLAW:START -->` fence that regenerates in place and never touches your own
  content outside the markers. Memory-derived and knowledge-derived content stay under
  distinct headings, and knowledge items render as titled pointers — never inlined
  bodies. A "Refresh context files" action on the project page (re)writes the block.
- **Apps surface their skills and backend routes to the agent (declared, not
  discovered).** An app now declares two legible surfaces in `app.json`, both
  readable without executing app code. `skills[]` names SKILL.md directories the
  app ships and OWNS: on enable they seed into the user skills tree **through the
  supply-chain chokepoint** (quarantine → scan at the app's trust tier →
  `.pclaw-lock.json` provenance) — an app skill never bypasses the gate just
  because it arrived inside an app — and are removed provenance-keyed on disable,
  never touching a user's own or another app's skill. `backend.routes[]` names the
  app's agent-callable HTTP surface (`op`, method, path, summary, param/body
  hints); one generic tool provider turns every enabled app's `agentCallable`
  routes into `app_<name>_<op>` tools (risk keyed off the verb) and drives them
  through the existing loopback reverse proxy, and a `call-app-route` action lets
  hooks/crons fire the same routes — both share one resolver so the callable gate
  can't diverge. The routes also render into `GET /api/manifest`'s `app_surfaces[]`
  (a non-callable route documents the surface with `tool: null`), and a declared
  route whose backend answers 404 raises a one-shot drift notification so a
  dead-declared route is caught the moment it's called. First-party Growth (17
  routes) and Minutes (24 routes) ship their route tables.
- **Offline agent reference + `pclaw-api` skill** — an agent driving PersonalClaw
  from outside a running gateway now reads exact tool/route signatures instead of
  guessing them. The distribution ships a generated markdown reference
  (`personalclaw/reference/`: every registered tool with its input schema +
  examples, the agent-callable HTTP routes, and the provider taxonomy) rendered
  from the same source as the live `GET /api/manifest`, plus a bundled `pclaw-api`
  operator skill (the never-guess-copy-it + verify-after-mutate discipline). Locate
  the files from the installed binary with the new `personalclaw doctor --paths`,
  which prints the resolved reference / config / skills / install directories. A
  drift test byte-compares the checked-in reference against a fresh render, so a
  tool or route added without its metadata reddens the build.
- **Render-smoke gate** (`npm run smoke:render`): the built SPA is now loaded
  in headless Chromium — key routes must mount real content with no uncaught
  errors — before any frontend-affecting push (repository-owned pre-push hook,
  `npm run hooks:install`) and on every PR (CI `web` job). Closes the
  verification hole behind the v0.1.0 blank dashboard, where typecheck, unit
  tests, and the production build all passed without ever rendering the
  artifact in a browser.

### Changed

- **The dashboard's system indicators are now a docked bottom rail.** The System
  strip (uptime, version, CPU/memory/network/disk/load, triggers, subagents, and
  the update action) was the last item in the scrolling column; it's now a
  shell-like rail pinned to the bottom edge, so the live indicators stay visible
  while the rest of the dashboard scrolls. The dashboard header's at-a-glance
  pulse strip sheds two now-redundant indicators: the gateway connectivity pill
  ("Live/Offline") and the gateway-version pill. The app shell's top-right corner
  already carries a live connectivity dot on every page, and its expanded system
  card now shows the gateway version (sourced from `/api/system`) — so the header
  strip is just the live count pills. The rail itself is width-responsive (a CSS
  container query, keyed to the content-width preset + sidebar, not the viewport):
  it sheds the decorative CPU sparkline and the metric word-labels — icon + value
  keep carrying the reading, with the full text on hover — to stay on one line as
  the available width tightens, and the "Details →" action stays anchored to the
  right edge.

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).** The released SPA crashed at first
  render with `TypeError: Cannot read properties of null (reading 'useContext')`
  — a dependency-group bump had split the installed tree across React 18 and
  React-DOM 19 (the classic dual-React invalid-hook failure), so every install
  kind (pip/uv, container, git) served an empty page. The web toolchain is
  reverted to its known-good React-18 set, a root npm `overrides` pins
  `@types/react`/`@types/react-dom` so transitive packages cannot drag React-19
  types back in, and the lockfile is regenerated from a clean install so the
  declared and resolved trees agree.
- **`monaco-editor` was never declared as a dependency** — it is a peer of
  `@monaco-editor/react` and imported directly, but resolved only by lockfile
  accident; a clean reinstall broke the build. Now a direct dependency
  (`^0.55.1`, the version v0.1.0 shipped transitively).

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams** — an app can now hook into `personalclaw setup` and
  `personalclaw doctor` via manifest `cli.setup` / `cli.doctor` (`module:function`),
  and declare its log namespaces via `loggerRoots`. `personalclaw setup --app <name>`
  runs just one app's setup step. Core names no channel vendor in its CLI.
- **CI & release engineering** — GitHub Actions for both repos: `ci.yml`
  (lint/test/web/rails, ≤10-min budget) and `full.yml` (3.12/3.13 × ubuntu/macos
  matrix, audit, coverage) on core; manifest-validate/tests/boundary on the apps repo.
  A tag-triggered `release.yml` builds the wheel (with the prebuilt SPA) + multi-arch
  GHCR images, publishes to PyPI via Trusted Publishing behind an owner-approval gate,
  and attaches an SBOM + build-provenance attestations. `uv.lock` pins the dependency
  graph (CI installs `--locked`); Dependabot watches pip/npm/actions weekly. See the
  [supply-chain posture](README.md#supply-chain).

### Changed

- **Provider-boundary completion (Slack residue retired from core):** the Slack
  channel app now ships its own token/slash-command setup and doctor probe (via the
  new `cli.setup`/`cli.doctor` seams) instead of living hardcoded in core's CLI; app
  logger roots are derived from installed manifests (`constants.APP_LOGGER_ROOTS`
  removed); `slack-sdk` is no longer a core runtime dependency (kept as the `[slack]`
  extra, and the slack-channel app declares it via manifest `pythonDependencies`, which
  the app-install pipeline installs). A residue-sweep test + a machine-checked keeps
  table (`docs/architecture/provider-boundary-keeps.txt`) prevent vendor residue from
  regrowing in core.
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):** a bare
  `pip install personalclaw` no longer pulls the OpenAI or Anthropic SDKs. They now
  ship via (a) the `[openai]` / `[anthropic]` packaging extras for pip/uv users, and
  (b) the branded provider apps' manifest `dependencies.pythonDependencies`, which the
  app-install pipeline installs into the shared venv (plan 32 T2.1). The provider
  adapters import their SDK lazily and now raise a clear `MissingSDKError` naming the
  exact `pip install 'personalclaw[openai]'` remedy (and `personalclaw doctor`) when a
  hosted provider is used without its SDK. This trims the default install; users who
  install a provider app or the matching extra are unaffected (plan 34 T1.4).
- **Self-update is now install-kind aware (git · pip · container · desktop):** the
  in-app updater (Settings → Updates) and the update check no longer assume a git
  checkout. The availability signal is the **latest GitHub release tag** (ETag-cached,
  offline-tolerant) compared against the running version — tags are the release truth
  for every install path. Apply adapts to the install kind: a **git** checkout runs the
  existing pull → reinstall → rebuild → restart pipeline (with a new *Developer update
  mode* toggle, `dashboard.update_dev_mode`, to track every commit instead of only
  tagged releases); a **pip/uv/pipx** install runs `pip install -U personalclaw==<tag>`
  into its own interpreter and gracefully re-execs (no web build — the wheel ships the
  dashboard); a **container** install shows the exact `docker compose … pull && up -d`
  commands (no in-place apply); a **desktop** install delegates to the app shell. The
  Updates panel renders the right affordance per kind, and git installs also surface
  commits-behind as secondary info.

  This is a **clean break** (pre-1.0): the old git-only updater is replaced directly,
  not gated — the migration-backed gate machinery is deferred, so there is no
  `update_kind_aware` gate to flip (owner decision 2026-07-20). Behavior change: a git
  checkout now updates on new *release tags* by default instead of every commit — flip
  *Developer update mode* on to restore per-commit updates. **Run `personalclaw
  snapshot` before updating.** (plan 34 S4.)

### Removed

- **`personalclaw gateway --slack-only`** — the legacy alias for `--headless` is
  removed. Use `--headless`.

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.** The release
  pipeline (and `make build`) build the sdist first, then build the wheel from that
  sdist; the built `web/dist` was not included in the sdist, so the wheel-from-sdist
  shipped without the dashboard and failed `scripts/verify_wheel.py`. A new
  `MANIFEST.in` grafts `web/dist` into the sdist, which also makes the sdist itself
  self-contained (a wheel built from the PyPI sdist serves the dashboard too). Guarded
  by `tests/test_sdist_bundles_spa.py`. (plan 34; caught in the release dry-run.)


Initial public release — the first end-to-end PersonalClaw: a self-hosted, local-first,
provider-agnostic personal AI agent behind one gateway and one web dashboard.

### Added

- **Agentic chat** — multi-session chat with tool use and approval controls, session
  forking/undo, answer variants/regenerate, folders/tags/kanban, side conversations,
  per-session model and reasoning-effort overrides, and temporary/incognito memory modes.
- **Goal loops** — give the agent a target; it classifies, plans, and loops autonomously
  under a deterministic supervisor you can pause, nudge, or stop.
- **Memory** — layered semantic/episodic/procedural memory with active recall, after-turn
  learning from corrections, promotion of repeated facts, and an Obsidian-compatible vault.
- **Knowledge base** — document/media/web ingestion, AI enrichment, entity extraction, a
  knowledge graph, and semantic search wired into chat context.
- **Skills** — SKILL.md procedures with a marketplace, supply-chain scanning on install,
  session-scoped ephemeral skills, and an approval inbox for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, a channel-watching
  inbox with drafted replies, and workflow SOPs surfaced on match.
- **App platform** — a permission-gated, scanner-gated Store: model providers, search,
  speech (STT/TTS), local models, channel connectors, agents, and full backend+UI apps,
  each installed through a quarantine → scan → consent lifecycle with subprocess isolation.
- **Agent runtimes** — the built-in native loop plus external CLI agents over ACP
  (Agent Client Protocol) as pluggable runtimes.
- **Model layer** — per-use-case model bindings (chat, background, embedding, ingestion,
  speech) over 16 provider apps; nothing is hardwired to a vendor.
- **Security** — four auth modes (loopback-forced `none`), command screening (denylist +
  suspicious-pattern watchers), an OS child sandbox, one egress chokepoint with host
  policy, untrusted-content fencing, a non-overridable "dangerous" install verdict, an
  HMAC-chained tamper-evident security event log, and credential-excluding exports.
- **Delivery surfaces** — local gateway, Docker Compose, systemd/launchd service install,
  a desktop shell, and portable snapshot/restore.

### Notes

- Single-user, self-hosted, MIT-licensed. **Zero telemetry** — no usage data leaves your
  machine.
- Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0