# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added

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

