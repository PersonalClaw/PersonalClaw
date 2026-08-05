# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Fixed

- **"Run now" did nothing for almost every automation, while reporting success.**
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

