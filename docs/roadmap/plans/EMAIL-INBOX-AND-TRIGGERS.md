# EMAIL-INBOX-AND-TRIGGERS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/EIAT.md`](../atomic/EIAT.md) as 6 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Email Inbox & Triggers — Mail as a First-Class Source, and Any Source as a Trigger

**Status:** DESIGNED — created 2026-07-29 (owner ask: capability gap analysis; owner direction: "this should be achieved via introduction of inbox provider app for an imap/smtplib provider or a gmail provider or something similar … And then updating our triggers to either include inbox events wired or allow inbox events fire a trigger or create a Trigger providing app that directly provides the email based triggers")
**Created:** 2026-07-29
**Wave:** 2 (S1: generalize the event vocabulary beyond memory; S2: the mail inbox provider app; S3: prompt-bound addresses + the trigger UX)
**Depends on:** nothing hard in core. Builds on the shipped `MessageSourceProvider` ABC (`inbox_providers/base.py:20`), the `EventTrigger` store/engine (`event_triggers.py`), the 10 shipped action providers (`action_providers/registry.py`), and the app-bundle provider seam. Coordinates with INBOX-NOTIFICATIONS-UNIFICATION (42 — **it owns the attention-path contracts**; this plan adds a *message source* and a *trigger source*, and must not add notification kinds or rules; if 42 has landed, mail-derived attention items use its typed kinds), CHANNEL-EXPANSION (40 — owns *channel transports* for two-way conversational channels; **mail here is an inbox SOURCE, not a chat transport** — §Boundary), WORKFLOWS-V2-AUTOMATION-SUBSTRATE (7 — owns "triggers fire workflows"; this plan widens what a trigger can fire *on*, never what it fires *into*), AUTONOMY-GUARDRAILS (DONE — mail-fired automation is unattended execution and inherits its budgets/denylist/kill switch).
**Scope:** PersonalClaw has **no email at all** (verified: zero hits for `imap`, `smtplib`, `smtp` across core and all 40 first-party apps) and its data-event triggers are **memory-only** — `EVENT_PATTERNS = (MemoryUpdate, MemoryKeyPattern, ContentMatch)` (`event_triggers.py:35-38`) with `vector_memory` as the **sole emitter** via `emit_memory_event` (`event_triggers.py:264`). So the agent can act on a *clock* (cron/heartbeat/autonudge, all mature) but not on *the world changing*. A known high-value mechanism is the best fit here: **purpose-specific mail addresses each carrying a stored default prompt**, composed with the user's ordinary Gmail/Outlook filters — which turns any email-emitting SaaS into an automation trigger with zero per-vendor integration. The same design also gates on a **pre-approved sender allowlist**, which is simultaneously the right product default and the right prompt-injection control. This plan delivers that in PersonalClaw's architecture: **(S1)** generalize the event-trigger vocabulary so any source can emit, **(S2)** a mail **inbox provider app** (IMAP/SMTP first) so mail lands in the existing inbox, **(S3)** prompt-bound addresses + the trigger configuration UX. **Soul guardrails:** (1) **allowlist-first, fail-closed** — an unknown sender can never trigger anything, ever; this is an *inbound security surface*, so per §2.7 it fails CLOSED (a missing/corrupt allowlist disables triggering rather than permitting it), unlike the user-facing availability surfaces that fail open; (2) **mail bodies are untrusted, always fenced** — every mail body, subject, and attachment-derived text passes `fence_untrusted` before reaching any prompt, exactly as `inbox_service.py:87` already does for external text; a mail-triggered agent turn is the most attacker-reachable surface in the product; (3) **no vendor path in core** — IMAP/SMTP/Gmail logic lives entirely in an app bundle importing core only via `personalclaw.sdk.*`; core gains only the generalized event vocabulary and the typed source contract; (4) **draft-by-default for outbound** — a mail-triggered automation that would *send* mail starts non-sending, per the well-established graduated-trust pattern. Class **B** (new event patterns persisted in `event_triggers.json`, new app-owned state) — so it lands as a **plain clean break under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry).

🔴 **PREMISE AMENDMENT (2026-08-04 audit) — the generalization target MOVED.** S1 aims at
`event_triggers.py`'s `emit_memory_event`, but WORKFLOWS-V2 has since shipped the unified
`src/personalclaw/triggers/` package (37 modules) with a closed `KINDS` vocabulary, a source-bearing
`event` kind, a real bus with spool/drain, and `triggers/disposition.py` explicitly labelling
`personalclaw.event_triggers` a **legacy surface** that now spools into `triggers.dispatch`. So S1
should widen the unified `event` kind's source vocabulary, NOT the legacy module — widening the
legacy one builds against a retiring contract. This is a re-scope, not a blocker. Also: there are now
**16** registered action providers, not the documented 10, which only strengthens the plan's
conclusion that nothing new is needed to ACT on a mail event.

---

## Context (code recon, 2026-07-29 — verified; do not re-derive)

**The inbox seam is real and correctly shaped — one stub ships today:**
- `MessageSourceProvider` ABC (`inbox_providers/base.py:20`): `source_name`, `poll(watched_channels, checkpoints, user_id) -> (list[IncomingMessage], dict[str, str])`, `send_reply(channel_id, text, thread_ts)`, `add_reaction(...)`. The **checkpoint dict returned by `poll`** is the resumption mechanism — a mail provider stores its UID/`Message-ID` cursor there, so this needs no new machinery.
- `IncomingMessage` (`inbox_providers/base.py:7`): `id, channel_id, channel_name, thread_id, text, sender_id, sender_name, timestamp, thread_context, is_dm`. **Mail maps cleanly**: `sender_id` = the From address (the allowlist key), `channel_id` = the receiving address (which is how a prompt-bound address is distinguished), `thread_id` = the `References`/`In-Reply-To` chain, `thread_context` = the quoted history.
- Shipped implementations: `filesystem_source.py` (polls `~/.personalclaw/inbox/incoming/*.json`) and `native_source.py`. **Neither is a real external mailbox** — so the seam is proven but unexercised by a network source.

**The event-trigger engine is well built and narrowly wired — this is the exact generalization point:**
- `EventTrigger` (`event_triggers.py:47-61`): `id, pattern, action_provider, action_config, key_glob, content_re, enabled, max_fires, fire_count, debounce_secs, last_fired_at`. Persisted in `<config_dir>/event_triggers.json` (`event_triggers.py:115`).
- `matches(trigger, *, event_type, key, value) -> bool` (`event_triggers.py:94`) is a **pure function** — trivially testable and the right place to extend.
- Real hardening already present and reusable as-is: per-trigger **debounce** (default 5s), a **global storm cap** of 30 fires / 60s window (`event_triggers.py:41-42`), `max_fires` self-retire, plus denylist/incident gating.
- **The single emitter** is `emit_memory_event(*, event_type, key, value, now)` (`event_triggers.py:264`) — best-effort, never blocking a write. The engine's entry point is `on_memory_event(...)`. **The generalization is to rename/widen this to a source-agnostic emit and keep the memory path as one caller** — a clean break, not a dual path.

**Action providers are ready — nothing new needed to *do* something with a mail event:**
- 10 registered (`action_providers/registry.py`): `bash`, `run-script`, `notify`, `send-message`, `create-task`, `invoke-agent`, `run-prompt`, `run-workflow`, `call-app-route`, + `webhook` from the `webhook-action` app. `run-prompt` and `invoke-agent` are exactly what a prompt-bound address needs.
- **The denylist is enforced at the 3 dispatch seams, not by provider cooperation** — so an app-contributed provider inherits it unknowingly. Mail-fired actions therefore inherit command screening for free.
- `supports_dry_run` exists on the ABC (`action_providers/base.py:98`) — the draft-by-default guardrail has a home already.

**What genuinely does not exist (state plainly; don't imply otherwise):**
- No mail anywhere. No `Message-ID` handling, no MIME parsing beyond `doc_parser`'s attachment-format readers, no sender allowlist concept, no outbound mail.
- No non-memory event source. `fs_watch.py` polls **4 local config paths** on a 3s `(mtime,size)` signature and is unrelated to triggers.
- No `watch_source` concept (grep: zero hits) — WATCHED-SOURCES (15) owns that and is unstarted.

## Boundary: inbox source vs channel transport (read before writing code)

These are two different seams and mail could plausibly go in either. The owner's direction is explicit and this plan follows it: **mail is an inbox SOURCE.**

| | `MessageSourceProvider` (this plan) | `ChannelTransportProvider` (CHANNEL-EXPANSION) |
|---|---|---|
| Shape | poll → items land in the inbox | connect → live two-way conversation |
| Model | triage/act on messages | chat with the agent |
| Reply | `send_reply` on a specific item | full streaming delivery contract (18 methods) |
| Mail fit | **yes** — mail is asynchronous, batched, triage-shaped | forces mail to pretend to be a chat |

A later Mail-as-a-*channel* (converse with the agent by email) remains possible and belongs to CHANNEL-EXPANSION; it would **reuse this plan's allowlist and fencing**, not fork them. This plan does not build it.

## Design

- **S1 — generalize the event vocabulary (core; small, enabling, no mail yet).** Widen the trigger engine from memory-only to source-agnostic: an `EventSource` notion (`memory | inbox | app`), new patterns for inbox events, and `emit_event(*, source, event_type, key, value, meta, now)` replacing the memory-specific emitter — with `emit_memory_event` **deleted** and `vector_memory` updated to call the general one in the same change (clean break, one path). `matches()` gains source scoping plus the two mail-relevant matchers (sender and receiving-address globs) driven by a `meta` dict so a source can carry structured fields without the engine learning any source's schema. Debounce, storm cap, `max_fires`, denylist and incident gating all apply unchanged — that is the payoff of extending this engine rather than writing a second one.
- **S2 — the mail inbox provider app (apps repo).** A `mail-inbox` app implementing `MessageSourceProvider`: IMAP IDLE-or-poll inbound, SMTP for `send_reply`, credentials via the SDK credential store (never in app config), the receiving address as `channel_id`, MIME text extraction reusing the platform's existing readers for attachments, and the **sender allowlist enforced in the provider** so a disallowed message is never even surfaced as an inbox item. It emits inbox events into S1's vocabulary. A Gmail/OAuth variant is a *second app* later — the seam is the point, and IMAP/SMTP works with Gmail today via app passwords.
- **S3 — prompt-bound addresses + the trigger UX.** The mechanism to adopt, in our shape: an app-owned table of **purpose-specific receiving addresses each carrying a stored default prompt** (`travel@…` → "build my itinerary, add calendar entries, buffer travel time"). Mail arriving at such an address fires a trigger whose action is `run-prompt`/`invoke-agent` with the stored prompt plus the fenced mail body. The user composes this with their **existing** Gmail/Outlook filters — zero per-SaaS integration work, which is exactly why it generalizes. Plus the Triggers page gains inbox-event configuration (source, address, sender pattern, subject/body matcher) and the honest **draft-by-default** posture for any action that would send mail.
- **What this is NOT:** not a mail client (no folder browsing, no full MUA UI); not a chat transport (§Boundary); not a notification/attention redesign (plan 42 owns that); not a workflow engine change (plan 7 owns what triggers fire into); not a Gmail-specific integration in core.

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Generalized event vocabulary (`event_triggers.py`; clean break)

```python
# Sources (new)
SOURCE_MEMORY = "memory"; SOURCE_INBOX = "inbox"; SOURCE_APP = "app"
EVENT_SOURCES = (SOURCE_MEMORY, SOURCE_INBOX, SOURCE_APP)

# Patterns — existing three KEPT verbatim (they are persisted values; renaming them
# would break every stored trigger), plus:
INBOX_MESSAGE   = "InboxMessage"     # any inbox message from a watched source
INBOX_SENDER    = "InboxSender"      # sender_glob matches (the allowlist-scoped key)
INBOX_ADDRESS   = "InboxAddress"     # address_glob matches the RECEIVING address
EVENT_PATTERNS = (MEMORY_UPDATE, MEMORY_KEY_PATTERN, CONTENT_MATCH,
                  INBOX_MESSAGE, INBOX_SENDER, INBOX_ADDRESS)

@dataclass
class EventTrigger:
    ...                                  # every existing field unchanged
    source: str = SOURCE_MEMORY          # tolerant read: an old row with no source is memory
    sender_glob: str = ""                # InboxSender
    address_glob: str = ""               # InboxAddress

def matches(trigger, *, source: str, event_type: str, key: str, value: str,
            meta: dict | None = None) -> bool:
    """Pure, as today. Source must match first: a memory trigger can never fire on an
    inbox event. Inbox matchers read `meta` ({'sender','address','subject'}) so the
    engine never learns any source's schema. CONTENT_MATCH stays usable across
    sources (regex over `value`)."""

def emit_event(*, source: str, event_type: str, key: str, value: str | None,
               meta: dict | None = None, now: float) -> None:
    """The ONE emitter every source calls. Best-effort — never blocks or raises into
    the caller's write path (the existing emit_memory_event discipline, preserved)."""
```

**Clean-break requirement:** `emit_memory_event` is **deleted**, and `vector_memory`'s call site is updated to `emit_event(source=SOURCE_MEMORY, ...)` in the same commit. Do not leave a shim — "one path per concern" (§2, and the tenet). Old persisted triggers read tolerantly (absent `source` ⇒ `memory`), which is the class-B tolerant-read clause.

### C2 — Inbox → event bridge (core)

The inbox service already polls sources and creates items. After an item is accepted (post-allowlist), it emits:

```python
emit_event(source=SOURCE_INBOX,
           event_type="message_received",
           key=item.id,                      # {channel}_{ts} — the existing id shape
           value=item.text,                  # raw; fencing happens at PROMPT time, never double-fenced
           meta={"sender": msg.sender_id, "address": msg.channel_id,
                 "subject": ..., "source_name": provider.source_name},
           now=time.time())
```

Two clauses an executor must honor:
- **`value` is raw here.** Fencing happens once, where content reaches a prompt (the established rule — `investigate.py`'s C1 states the same "raw here, fenced at injection" discipline). Double-fencing corrupts the markers.
- **Emission happens only for allowlisted messages.** A rejected sender produces no inbox item and therefore no event — the allowlist is enforced upstream in the provider, not as a trigger condition. This is what makes guardrail 1 structural rather than configurable.

### C3 — Mail provider app (apps repo; `mail-inbox`)

```
mail-inbox/
  app.json          # name, version, displayName, description; provider: inbox;
                    # permissions: MINIMUM — network:true (honest declaration), storage
  provider.py       # MessageSourceProvider impl: poll() / send_reply() / add_reaction() (no-op)
  addresses.py      # prompt-bound address table (S3), stored via ProviderSettings (§2.6)
  test_provider.py  # allowlist, MIME extraction, checkpointing, dedup
  README.md · LICENSE
```

Rules, each load-bearing:
- **Credentials via the SDK credential store only** (`sdk/credentials`, UPPER_SNAKE keys) — never `app.json`, never `ProviderSettings` (which is for non-secret config, §2.6).
- **Checkpointing** uses `poll`'s returned dict: store the IMAP `UIDNEXT`/highest UID per folder so a restart never reprocesses or skips. Dedup on `Message-ID` as a second belt.
- **Sender allowlist is provider-enforced and fail-closed**: no allowlist configured ⇒ **no messages are surfaced at all** (not "all messages"). Startup logs the posture explicitly so a silent empty inbox is diagnosable.
- **MIME text extraction** reuses the platform's existing document readers for attachments rather than reimplementing extraction; body prefers `text/plain`, falls back to sanitized `text/html`.
- **Outbound (`send_reply`) is draft-by-default**: it honors the platform's live-writes/dry-run posture, and a mail-triggered automation that would send must be explicitly enabled by the user (guardrail 4).
- `network: true` is an **honest declaration, not a boundary** — `apps/permissions.py` says so in code. Note it in the app README so nobody assumes enforcement.

### C4 — Prompt-bound addresses (S3; app-owned state)

```python
@dataclass
class BoundAddress:
    address: str          # the receiving address, e.g. "travel@<user-domain>"
    name: str             # "Business Travel"
    default_prompt: str   # stored instruction prepended to the fenced mail body
    enabled: bool = True
    allow_senders: list[str] = field(default_factory=list)  # narrows the global allowlist further
```
Stored via `ProviderSettings` in the app's `data/` (survives updates, §2.6). A message to a bound address fires a trigger with `action_provider="run-prompt"` (or `invoke-agent`) whose config carries `default_prompt`; the runtime composes `default_prompt + fence_untrusted(body, source="mail:<address>")`. **The stored prompt is user-authored** (trusted); **the body is not** (fenced). Never blur those.

### Integration points
- **Calls:** `event_triggers.emit_event` (new), `fence_untrusted` (`security.py`), the inbox service's item-creation path, `ProviderSettings` (§2.6), `save_credential` (§2.5), the action-provider registry, SEL (§2.3 — new event types lowercase snake: `mail_polled`, `mail_sender_rejected`, `inbox_event_fired`).
- **Called by:** the Triggers page (inbox-event configuration); mail-fired `run-prompt`/`invoke-agent` actions.
- **Storage owned:** the widened `event_triggers.json` rows (core, tolerant read); the app's own settings + checkpoint state (app `data/`). **No new core store.**
- **Deliberately NOT touched:** notification kinds/rules (plan 42), channel transports (plan 40), what a trigger fires *into* (plan 7), `fs_watch.py`, WATCHED-SOURCES' polling model (plan 15 — a mail source is not a watched source).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Generalize the event vocabulary (core)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Widen `EventTrigger` + `matches()` per C1: `source`/`sender_glob`/`address_glob` fields, three new inbox patterns, source-scoped matching, `meta`-driven inbox matchers; keep the three existing pattern strings verbatim | `src/personalclaw/event_triggers.py`, `tests/test_event_triggers.py` | a memory trigger never fires on an inbox event and vice versa; each new matcher has a unit test; an old persisted trigger (no `source`) reads as `memory` |
| T1.2 | Replace `emit_memory_event` with `emit_event` (source-agnostic) and update `vector_memory`'s call site **in the same commit**; no shim remains | `src/personalclaw/event_triggers.py`, `src/personalclaw/vector_memory.py`, tests | grep for `emit_memory_event` returns zero; memory triggers still fire exactly as before (regression test); debounce/storm-cap/max_fires behavior unchanged |
| T1.3 | Inbox→event bridge per C2: emit on accepted inbox items only, raw `value`, structured `meta`; SEL `inbox_event_fired` | the inbox service item-creation path, tests | an inbox item emits exactly one event with correct `meta`; a rejected sender emits none (test proves the negative) |
| T1.4 | Triggers API + reference: inbox patterns accepted/validated on the existing trigger routes; §2.2 error envelope for a bad glob/pattern combination; regenerate the offline agent reference (routes/schema changed — there is a drift test) | `dashboard/handlers/` trigger routes, `docs/reference/`, tests | creating an inbox trigger round-trips; an `InboxSender` trigger with no `sender_glob` is rejected with a typed code; drift test green |
| V1 | Validation as a user: create an inbox trigger against the **existing filesystem inbox source** (drop a JSON file into `~/.personalclaw/inbox/incoming/` on an isolated dev home) and watch it fire a `notify` action; confirm memory triggers still work; confirm the storm cap and debounce still bound firing; `make lint` + targeted pytest + `make test` | — | holds — **note this session is fully validatable with zero mail infrastructure** |

### Session 2 — The mail inbox provider app (apps repo)

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Scaffold `mail-inbox` with `app.json` (minimum permissions), `MessageSourceProvider` impl, SDK-credential wiring; IMAP inbound + UID checkpointing via `poll`'s returned dict + `Message-ID` dedup | `PersonalClawApps/mail-inbox/` | polling a real IMAP mailbox surfaces messages as inbox items; a restart neither reprocesses nor skips (checkpoint test); duplicate `Message-ID` is dropped |
| T2.2 | **Fail-closed sender allowlist** in the provider: no allowlist ⇒ zero messages surfaced; startup logs the posture; SEL `mail_sender_rejected` per rejection | `mail-inbox/provider.py`, `test_provider.py` | an unlisted sender produces no inbox item and no event; an empty allowlist surfaces nothing (not everything) — both asserted |
| T2.3 | MIME handling: prefer `text/plain`, sanitized-HTML fallback, attachment text via the platform's existing readers; body/subject carried raw into the event (fencing is downstream) | `mail-inbox/provider.py`, tests | multipart mail extracts correctly; an HTML-only mail is sanitized; a PDF attachment contributes extracted text |
| T2.4 | `send_reply` over SMTP, **draft-by-default**: honors the live-writes/dry-run posture; sending requires explicit enablement | `mail-inbox/provider.py`, tests | a reply is composed but not sent while draft-mode is on; enabling it sends and threads correctly (`In-Reply-To`) |
| V2 | Validation as a user: add the app as a local Store source, install it, configure a real mailbox + allowlist, send yourself mail from an allowed and a disallowed address; confirm only the allowed one appears in the inbox and fires a trigger; confirm the reply threads; push repo edits via `POST /api/apps/mail-inbox/update` (the gateway runs INSTALLED copies) | — | holds |

### Session 3 — Prompt-bound addresses + the trigger UX

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `BoundAddress` table per C4 via `ProviderSettings`; a message to a bound address fires `run-prompt`/`invoke-agent` with `default_prompt` + `fence_untrusted(body, source="mail:<address>")` | `mail-inbox/addresses.py`, provider wiring, tests | mail to a bound address runs the stored prompt grounded in the fenced body; the fenced markers wrap the body; a fence-break attempt in the body is neutralised (explicit test) |
| T3.2 | Triggers page: inbox-event configuration (source, address glob, sender glob, subject/body matcher) using shell primitives + tokens; URL-backed state; draft-by-default clearly surfaced for send-capable actions | `web/src/pages/triggers/`, `web/src/lib/api.ts` | an inbox trigger is creatable from the UI; no primitive-adoption ratchet trips (use `Button`/`ui/forms`); a11y (focus-visible, reduced-motion) passes |
| T3.3 | App settings UI for addresses (name / address / default prompt / per-address senders) via the app's `configSchema`-generated settings page; secrets masked | `mail-inbox/app.json` (configSchema), README documenting the Gmail-filter composition pattern | addresses are manageable from the app's settings page; the README shows the worked Gmail-filter → bound-address example |
| V3 | Validation as a user: create `travel@…` with a real default prompt; set a Gmail filter forwarding booking confirmations to it; send a realistic booking mail; confirm the agent runs the stored prompt against the fenced body and produces the expected result; confirm an unlisted sender to the same address does nothing; full local gate incl. web typecheck/test/build | — | holds |

## Owner tasks (real world)
1. **Provide a test mailbox** (a throwaway IMAP/SMTP account, or Gmail with an app password) — S2's V-task cannot be validated without one. S1 needs nothing.
2. **Decide the receiving-address strategy** for prompt-bound addresses: a catch-all domain you control (cleanest — `travel@yourdomain`), Gmail `+suffix` addressing (zero setup, works today, `you+travel@gmail.com`), or per-purpose mailboxes. The plan works with all three; the app README should document whichever you pick.
3. **Rule on whether a Gmail-OAuth app is wanted** after IMAP/SMTP proves the seam. The plan's position: a *second app*, not a core change — but it is your sequencing call.
4. **Confirm draft-by-default for outbound.** The plan makes sending opt-in; say if you'd rather it default on for replies to allowlisted senders.

## Risks & open questions
- **Prompt injection is the headline risk, and it is now an observed threat, not a theoretical one.** A mail-triggered turn is attacker-reachable by anyone who can get mail to an allowlisted address. Layered mitigation: the fail-closed allowlist (S2), always-fenced bodies (S3 with an explicit fence-break test), no unattended *sending* by default, and inherited denylist/budget/kill-switch from AUTONOMY-GUARDRAILS. Worth recording: the competitive research itself hit two live injection attempts on vendor/affiliate pages — treat mail bodies with at least that suspicion.
- **A compromised allowlisted sender** bypasses the allowlist by definition. Accepted; the remaining controls (fencing, draft-only send, budgets, storm cap) are what bound the damage. Do not present the allowlist as sufficient on its own.
- **Mail volume vs the storm cap.** The existing global cap is 30 fires/60s; a busy mailbox could hit it. That is the correct behavior (a cap that never engages is not a cap), but the UI must say *why* firing stopped rather than appearing broken.
- **IMAP reliability** (idle timeouts, provider throttling, folder quirks) is the app's problem, not core's — which is precisely why it is an app.
- **Open:** whether inbox events should also be able to fire *workflows* directly. Deferred: plan 7 owns "triggers fire workflows," and `run-workflow` already exists as an action provider, so this likely needs no work here — verify at S3 rather than pre-building.

## Execution log

- **2026-08-09 — DONE: EIAT-1** (Generalize the event-trigger vocabulary to source-agnostic, core, S1).
  Widened the shipped `event_triggers.py` vocabulary (the live runtime for the unified `event` kind
  per `KIND_RUNTIMES["event"]`) to be source-agnostic per Contracts C1 & C2 — a clean break, no shim.
  - **C1 (source-agnostic matcher):** `EventTrigger` gained `source` (memory/inbox/app, defaulted from
    the pattern via `PATTERN_SOURCE` so a mismatched pattern/source pair can't defeat isolation),
    `sender_glob`, `address_glob`; `EVENT_PATTERNS` extended with `InboxMessage`/`InboxSender`/
    `InboxAddress`. `matches()` now takes keyword `source`/`meta` and gates on `trigger.source != source`
    **before** any pattern logic — a memory trigger is invisible to an inbox event and vice versa.
    Legacy specs with no `source` key infer memory semantics via `from_dict`.
  - **Clean break:** `emit_memory_event`/`on_memory_event` were DELETED (not aliased) and replaced by
    `emit_event`/`on_event(*, source, event_type, key, value, now, meta=None)`; the two callers
    (`vector_memory`, `triggers/loop` spool re-entry) and the dispatch spool envelope kind
    (`f"{source}.{event_type}"`) were updated same-change.
  - **C2 (inbox→event bridge):** `inbox_service._ingest` emits exactly one `source=inbox`
    `message_received` event per accepted item, carrying the raw value + structured meta
    (sender/sender_name/address/source_name); best-effort so a bridge failure never breaks ingest.
  - **Typed error:** the dashboard create/update handlers reject an `InboxSender` trigger with no
    `sender_glob` (wire code `sender_glob_required`) and derive `source` from the pattern.
  - **Gate:** `make lint` green; targeted pytest green (262 trigger/inbox/memory tests incl. 8 new EIAT-1
    matchers + the engine source-scoping test); `make test` green after satisfying three full-suite-only
    ratchets EIAT-1 legitimately moved (`LEGACY_FIELD_MAP` +3 fields; `EVENT_PATTERNS` scope-pin test
    re-affirmed on the no-chat-turn-source invariant; offline reference unaffected).
  - **Note:** two other full-suite reds (`providers.md` `+sandbox` drift; unclassified
    `sandbox_providers/none.py` spawn site) were PRE-EXISTING EI-1 (#933) debt on `main`, not EIAT-1 —
    fixed in the stack-base commit that this atom stacks on. PR stacked on that health fix.

- **2026-08-09 — DONE: EIAT-2** (mail-inbox provider app: IMAP inbound, checkpointing, fail-closed
  allowlist, MIME extraction, S2). New `PersonalClawApps/mail-inbox/` bundle (apps#27), Contract C3.
  - **T2.1 (IMAP + checkpointing):** `MailInboxProvider.poll` returns `(messages, checkpoints)` where
    the checkpoint dict carries a per-mailbox UID cursor (`mailuid:<user>:<folder>`). The IMAP client
    does `UID SEARCH (last+1):*` and filters strictly `> last_uid`, so `start:*` always returning the
    highest UID can't reprocess the cursor message; an empty FETCH breaks the loop rather than skipping.
    A restart handed the same checkpoint dict refetches nothing; a newer UID is surfaced, older are not.
    Second belt: `Message-ID` dedup persisted to `seen_message_ids.json` (bounded 5000) via `app_data_dir`.
  - **T2.2 (fail-closed allowlist + SEL):** the allowlist is provider-enforced with `fnmatch` globs.
    An **empty allowlist surfaces zero messages and never even connects**; an unlisted sender is rejected
    (zero messages) and logs a `mail_sender_rejected` event via `sel().log_api_access`. A rejected message
    still advances the cursor so a restart doesn't refetch-and-re-log it forever. Posture logged once at
    startup. Credentials come **only** from the SDK credential store (`MAIL_INBOX_PASSWORD`); a password
    wrongly placed in ProviderSettings is ignored (asserted).
  - **T2.3 (MIME):** `extract_body` prefers `text/plain`, sanitizes HTML-only mail (drops
    `<script>`/`<style>`, strips tags), and appends PDF/DOCX/PPTX attachment text via the core document
    readers (`sdk.channel.extract_text`). Charset honored.
  - **Boundary/deps:** core reached only via `personalclaw.sdk.*` (inbox/channel/settings/cli/util);
    **stdlib-only**, zero `pythonDependencies`. The SEL-singleton test reset reaches the class SDK-legally
    via `type(sel())` (the class itself isn't an SDK export; `conftest.py` is linted, only `test_*.py` is
    skipped).
  - **Gate green:** manifest round-trip (`AppManifest.from_dict`), boundary AST lint, `pytest mail-inbox`
    **32 passed** — all against core installed from `main`.
  - **Owner-gated remainder (out of scope):** live-wiring an installed inbox app into the gateway
    (`_init_inbox` hardcodes the filesystem source) and the real-mailbox V2 validation. EIAT-3 (SMTP
    send-reply, draft-by-default) is the next atom in this plan.

- **2026-08-09 — DONE: EIAT-5** (Triggers page inbox-event configuration UI, web/, S3). Core `web/`
  frontend only (separate PR seam from EIAT-4's apps-repo work); backend was already fully wired by
  EIAT-1 with no create UI on the page (only chat tools / the raw API).
  - **T3.2 (create UI):** the Triggers create page (`web/src/pages/triggers/TriggerCreatePage.tsx`)
    gained a third kind — **Data event** — beside Schedule and Lifecycle. It renders a pattern
    Combobox over the six wired `event_triggers.EVENT_PATTERNS` members, and per pattern shows exactly
    the ONE matcher field the backend's `matches()` reads (sender/address/key glob or content regex) —
    no inert extras — plus a derived source badge (Inbox vs Memory; the source is never sent, mirroring
    `PATTERN_SOURCE`). `InboxSender` is client-gated matcher-required to match the server's
    `sender_glob_required` code, so the empty submit is blocked before the round-trip; catch-all
    patterns (`InboxMessage`/`MemoryUpdate`) render a "fires on everything" note instead of a field.
  - **URL-backed state:** both the chosen kind and the pattern live in the hash query (`?kind` /
    `?pattern`, replace-mode) via `useQueryParam`, so the flow is deep-linkable and back/forward-safe.
  - **Draft-by-default:** a send-capable action surfaces an info note before Create (see DEVIATION).
  - **API + helpers:** new `api.createEvent` (posts `trigger_type:'event'` + pattern + the one matcher
    + action; backend derives source); new `triggerMeta` helpers `EVENT_PATTERN_META` (lockstep with
    the Python tuple), `eventPatternMeta`, `eventSourceIcon`, `actionIsSendCapable`, each unit-tested.
  - **Primitives/a11y:** built only on Field / TextInput / Combobox / Button / Segmented — no
    primitive-adoption ratchet trip; focus-visible + reduced-motion inherited from the global rings.
  - **DEVIATION — "subject/body matcher" → the wired content matcher.** The done_when named a
    subject/body matcher for inbox events, but the shipped backend has NO subject/body field for the
    inbox source (inbox matching is sender/address glob only; content matching lives on the memory
    source via `ContentMatch`/`content_re`). Building a subject/body input would ship an inert control
    the backend ignores — a no-inert-control violation. Resolution: expose the content matcher where it
    is actually wired (memory `ContentMatch`) + the two wired inbox globs; an inbox subject/body matcher
    is a backend feature (new field + `matches()` branch on `event_triggers.py`) that must precede any UI.
  - **DEVIATION — draft-by-default via a provider heuristic, not a capability flag.** Core has no
    per-provider send/draft flag today (the real posture is owned by mail-inbox `send_reply`, EIAT-3,
    still todo). `actionIsSendCapable()` is a documented UI-copy heuristic keyed to `send-message` +
    future `send-*`; when EIAT-3 lands a real posture the note should read it instead of the name.
  - **Gate green:** `make lint` (771 files); `npm run typecheck`; `npm test` **887 passed** (incl. the
    new triggerMeta cases); `npm run build`. Ran under lockfile-pinned deps (`npm ci`) — a stale local
    `node_modules` (lucide 0.469 vs pinned 1.30) had reded an unrelated chat-icon test; syncing fixed it.

- **DONE `EIAT-4`** (prompt-bound receiving addresses + settings page) — shipped as **apps#34**; apps-repo
  only, no core change required. `fence_untrusted` was already on `personalclaw.sdk.security`, and
  the mail-inbox app's MIME module (apps repo) had explicitly reserved this work in a comment ("`fence_untrusted` in
  EIAT-4, never here — so text is never double-fenced").
  **The load-bearing finding: an app cannot fire run-prompt/invoke-agent itself, and core says so.**
  core's `src/personalclaw/sdk/__init__.py` records that the ambiguous action providers (run-prompt / invoke-agent / …) "need a
  `personalclaw.sdk.runtime` … intentionally not published yet". So rather than invent an endpoint, the
  app composes into the chain that already exists: `inbox_service._ingest` emits one `message_received`
  event per accepted item → `EventTriggerEngine` matches an `InboxAddress` trigger on `meta.address` →
  `execute_event_action` → `invoke-agent`. core's `src/personalclaw/event_triggers.py` (the `is_fenced(value)` branch) does and passes
  already-fenced text THROUGH instead of re-wrapping, so the app's fence attribution survives intact.
  The user supplies the one `Data event → InboxAddress → invoke-agent ($value)` trigger (EIAT-5 shipped
  that UI); the app cannot create it without reaching past the SDK, and that boundary was respected.
  **Two decisions worth carrying forward.** (1) The schema went into `provider.settingsSchema`, NOT a new
  `setup.configSchema`, because core's `_effective_config_schema` treats the latter as an *override* —
  adding one would have hidden the 7 existing IMAP fields unless duplicated, the two-schemas-one-file
  drift core's own comments warn about. (2) **No secret field is declared, deliberately**: `x-meta.sensitive`
  masking is wire-only, so a password field would still persist the password into `data/config.json`,
  breaking EIAT-2's guardrail. The IMAP password stays credential-store-only and a test asserts zero
  sensitive fields are declared.
  **Would have shipped inert without one line:** `channel_id` now carries the bound address rather than
  the mailbox login — an `InboxAddress` trigger for `travel@…` could never have matched otherwise.
  **Gate (re-verified independently by the driving session):** repo CI is manifest-validate · tests ·
  boundary · dco — 45 manifests valid, the app's own pytest suite **51 passed** (32 pre-existing + 19 new),
  boundary clean (every core import via `personalclaw.sdk.*`), DCO signed with no other trailers.

- **BLOCKED `EIAT-3`** (send_reply over SMTP, draft-by-default) — **owner-gated by its own done_when**,
  which ends "Owner-gated on the draft-by-default confirmation (Owner task 4)". Whether a reply defaults
  to *draft* or to *send* is a product/safety decision about sending mail as the user, not an engineering
  call, so it is not something a tick may pick. Everything else in the atom (SMTP `send_reply`,
  `In-Reply-To` threading, `supports_dry_run` honouring the platform's live-writes posture) is
  implementable the moment that posture is confirmed. Unblock = owner confirms the default.

- **DONE `EIAT-6`** (2026-08-11) — **Carry a source-declared item kind through to the inbox row (core).**
  **The finding: the inbox shipped LIVE READERS for two kinds nothing could ever write.**
  `web/src/pages/inbox/inboxMeta.ts` declares `ITEM_KINDS` including `{key:'mention'}` and
  `{key:'email'}`; `InboxPage` filters `(it.item_kind || 'message') === kind` and `kindMeta()`
  renders the label/icon; `handlers_inbox` filters (`?kind=`) and counts (`GET /api/inbox/kinds`)
  on the same field; `inbox.py` persists it and carries it through `emit_attention_item`. But
  `inbox_providers/base.py::IncomingMessage` had **no kind field at all** (id, channel_id,
  channel_name, thread_id, text, sender_id, sender_name, timestamp, thread_context, is_dm), so
  `InboxService._ingest` constructed every polled row with the dataclass default and
  `ItemKind.EMAIL` / `ItemKind.MENTION` were unreachable **by construction** — `grep -rn "ItemKind\."
  src/personalclaw` outside `inbox.py` produced no writer for either, and
  `inert-surface-baseline.json` had independently censused both as inert. EIAT-2's mail-inbox app
  could poll a real mailbox and still produce rows indistinguishable from a chat message. This is
  the "live reader of an unwritten key" shape: the reader is correct, the round trip is complete,
  and the seam simply cannot express the value.
  **Design.** (1) The kind rides the seam: `IncomingMessage.kind` (default `message`) → `_ingest`
  → `InboxItem.item_kind`. (2) Core never infers one — no text scan for the operator's name.
  `evaluate_alert` already matches names for *alerting*, and reusing that here would confuse a
  soft signal with a durable, filterable classification (a row permanently labelled "Mention"
  because someone typed the user's first name). A mention is what the SOURCE knows from its
  payload's at-mention ids. (3) `SOURCE_DECLARABLE_KINDS` (`= ItemKind` minus `NON_CHANNEL_KINDS`
  = message/mention/email) is the single validation point for every source, present and future.
  **The unknown-kind posture: refuse the CLAIM, keep the MESSAGE, log loudly.** Both rejected
  alternatives were considered explicitly. *Trusting* the declared value would let a source mint a
  kind the dashboard has no chip/icon/label for — an unfilterable, unreachable row — or claim one
  of core's own non-channel attention kinds and render a row with no refs, no deep-link and no
  reply (a dead row wearing a live kind's chip). *Dropping* the message would mean one typo
  (`"mail"` for `"email"`) silently stops a user's mail arriving, and in the filesystem source's
  case wedges the poll batch on the offending file forever, since the file is only renamed into
  `processed/` after a successful parse. So the row is delivered and confined to a kind the UI can
  render, and the mistake is loud on the one side that can fix it — the provider author's logs.
  Explicitly **not** a silent fallback to `message`: an unknown kind always warns, naming the
  source and the value.
  **Which kinds got a real core writer, and which did not.** `filesystem_source` passes each batch
  entry's declared `kind` through — the drop-box is core's documented seam for a local producer (a
  mail fetcher, a channel bridge) to state what a message is. So **both `email` and `mention` are
  reachable and proven end-to-end inside core**, but as *declared* kinds: core validates and
  persists what the producer states, and **no core source derives either one** (there is no IMAP/
  SMTP anywhere in core, and deriving `mention` from text is exactly the heuristic this atom
  refuses). The **native** push source was deliberately left as `message`: an agent-posted item is
  not a channel message, but relabelling it `system`/`agent_request` moves it into
  `NON_CHANNEL_KINDS`, which is what the UI keys its reply affordance off — it would kill the
  `can_reply`/`reply_target` routing native questions depend on. That is a change with a frontend
  half and its own validation, not this atom's.
  **`ItemStatus.SENT` stays open and untouched.** It is the third inert surface on `inbox.py`, and
  it belongs to `EIAT-3`'s reply path (a draft was sent at the source) — which is owner-gated on
  the draft-by-default confirmation (Owner task 4). Giving it a writer here would have pre-decided
  that product question in code.
  **Round trip proven on the real path, not on the dataclass.** `tests/test_inbox_item_kind_seam.py`
  drops a real JSON batch in `<home>/inbox/incoming/`, polls it with `FilesystemSourceProvider`
  through `InboxService._poll_once`, re-reads the row from disk with a *fresh* `InboxStore`, then
  asserts `GET /api/inbox`, `?kind=<kind>` and `/api/inbox/kinds` — including the exact comparison
  `InboxPage` makes. Plus: the refusal cases with the warning asserted (`"mail"`, `"Email"`,
  `"proposal"`, …), a ratchet asserting `SOURCE_DECLARABLE_KINDS == {ItemKind} - NON_CHANNEL_KINDS`
  so a future enum member cannot silently skip the declarable decision, and a parity test that
  every declarable kind has an `ITEM_KINDS` chip in `inboxMeta.ts` (the reader half, with a
  vacuity assertion so a drifted regex fails instead of passing empty).
  **No frontend change was needed** — the `InboxItemKind` union and `ITEM_KINDS` were already
  exhaustive over the enum, and chips are built from what is present in the store, so Mentions and
  Email light up the moment a row lands. **No SDK export was added on purpose**: a constant on
  `personalclaw.sdk.inbox` would have no in-repo consumer and would therefore book a NEW inert
  `sdk_export` surface; the closed set is documented on `IncomingMessage` instead.
  **Baseline.** `inert-surface-baseline.json` regenerated with its own generator
  (`scripts/generate_inert_surface_baseline.py`) in this commit on a legitimate SHRINK only:
  `src/personalclaw/inbox.py` 3 → 1 inert surfaces (`enum:ItemKind.EMAIL` and
  `enum:ItemKind.MENTION` cleared, `enum:ItemStatus.SENT` correctly remaining), totals 154 → 152,
  enum 27 → 25. No counter rose.
  **APPS-REPO FOLLOW-ON (out of scope here, do not edit the apps repo from a core atom).** The
  provider-side writers are one argument each: `mail-inbox` must pass `kind="email"` on every
  `IncomingMessage` it builds (it currently sets none, so its mail still lands as `message`); a
  channel inbox source must pass `kind="mention"` when the **vendor payload** lists the operator
  among that message's at-mention ids, and `message` otherwise — never by scanning the text. Valid
  values are `message` / `mention` / `email`; anything else is refused (row kept, filed as
  `message`, warning logged naming the app's `source_name`). The field defaults to `message`, so an
  app that does nothing keeps today's behavior exactly — this is additive, not a break.
  **Deliberately NOT done:** the inbox→event bridge's `meta` does not carry the kind. No matcher
  reads it (`event_triggers.matches()` gates inbox events on sender/address globs only, per
  EIAT-5's DEVIATION), so adding it would ship an inert field; an "email-only trigger" needs the
  backend matcher first.
  **Gate:** `make lint` clean · targeted `pytest -k "inbox or item_kind or notification or trigger"`
  green · inert-surface + agent-reference + docs-lint + config-roundtrip ratchets green · full suite
  green apart from the known worktree-only `test_harness_validate` trio · real-home rail: unchanged.
- **2026-08-17 — DONE (`EIAT-3`): `mail-inbox` send_reply over SMTP, draft-by-default** (PersonalClawApps#36).
  Draft-by-default is the module's *shape*, not a flag read at the send site: the reply is composed
  unconditionally, then `draft_reason()` — one decision point — returns non-empty for explicit `dry_run`,
  `PERSONALCLAW_DISABLE_LIVE_WRITES`, `send_enabled` false, or incomplete SMTP config. In every draft case
  `_make_sender` is never called, so no socket can open. The default is set in two places that must agree
  (dataclass field and `load()`'s `d.get("send_enabled", False) is True`), so an absent key *and* a
  hand-edited `"yes"` both read False. Threading asserts real header values (`In-Reply-To`, and
  `References` = parent chain + parent id per RFC 5322 §3.6.4, deduped); a prompt-bound address sends
  `From:` the **bound** address so a purpose address cannot leak the account. **No real email was sent** at
  any point — every send goes through an in-memory fake and the transport tests double `smtplib`. Gate:
  82 passed, the exact CI boundary lint clean over 101 files, core `AppManifest` round-trip stable with
  `send` surviving. Five falsifications, all reddening.
- **2026-08-17 — DISCOVERY (`EIAT-3`): three shipped halves have no reader on the core side.**
  (1) `MessageSourceProvider.send_reply` has **zero call sites in core** — only the ABC and
  `filesystem_source`'s stub — so the capability is provider-level and not yet reachable by a user; a core
  caller is a separate atom. (2) **Nothing in core reads `supports_dry_run` off an inbox provider**; it
  exists on `ActionProvider` and is read via `getattr` in `workflows/grounding.py` over
  `ALLOWED_HOOK_PROVIDERS`. Declaration was bound to behaviour by a test rather than shipped as a floating
  claim. (3) `guardrails.writes.live_writes_disabled` is **not an SDK export**, so an app cannot import it;
  `outbound.live_writes_disabled()` reads `PERSONALCLAW_DISABLE_LIVE_WRITES` directly (that env var *is* the
  contract) and mirrors `guardrails.flags.guard_flag` exactly, including any-other-value → guard ON.
  Promoting it to `personalclaw.sdk.*` is worth a core issue. **No new permission was invented:** core's
  `Permissions.from_dict` has no outbound-mail key, so declaring one would drop on round-trip and ship an
  inert consent claim. Contract note: `send_reply` returns `False` for a successful draft because the ABC's
  `bool` means *delivered*; `reply() -> ReplyOutcome` carries the distinction.
