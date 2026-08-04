# Plan: Email Inbox & Triggers — Mail as a First-Class Source, and Any Source as a Trigger

**Status:** DESIGNED — created 2026-07-29 (owner ask: competitive gap analysis, Genspark + Manus; owner direction: "this should be achieved via introduction of inbox provider app for an imap/smtplib provider or a gmail provider or something similar … And then updating our triggers to either include inbox events wired or allow inbox events fire a trigger or create a Trigger providing app that directly provides the email based triggers")
**Created:** 2026-07-29
**Wave:** 2 (S1: generalize the event vocabulary beyond memory; S2: the mail inbox provider app; S3: prompt-bound addresses + the trigger UX)
**Depends on:** nothing hard in core. Builds on the shipped `MessageSourceProvider` ABC (`inbox_providers/base.py:20`), the `EventTrigger` store/engine (`event_triggers.py`), the 10 shipped action providers (`action_providers/registry.py`), and the app-bundle provider seam. Coordinates with INBOX-NOTIFICATIONS-UNIFICATION (42 — **it owns the attention-path contracts**; this plan adds a *message source* and a *trigger source*, and must not add notification kinds or rules; if 42 has landed, mail-derived attention items use its typed kinds), CHANNEL-EXPANSION (40 — owns *channel transports* for two-way conversational channels; **mail here is an inbox SOURCE, not a chat transport** — §Boundary), WORKFLOWS-V2-AUTOMATION-SUBSTRATE (7 — owns "triggers fire workflows"; this plan widens what a trigger can fire *on*, never what it fires *into*), AUTONOMY-GUARDRAILS (DONE — mail-fired automation is unattended execution and inherits its budgets/denylist/kill switch).
**Scope:** PersonalClaw has **no email at all** (verified: zero hits for `imap`, `smtplib`, `smtp` across core and all 40 first-party apps) and its data-event triggers are **memory-only** — `EVENT_PATTERNS = (MemoryUpdate, MemoryKeyPattern, ContentMatch)` (`event_triggers.py:35-38`) with `vector_memory` as the **sole emitter** via `emit_memory_event` (`event_triggers.py:264`). So the agent can act on a *clock* (cron/heartbeat/autonudge, all mature) but not on *the world changing*. Both competitors converged on the same high-value mechanism here, and Manus's version is the better one: **purpose-specific mail addresses each carrying a stored default prompt**, composed with the user's ordinary Gmail/Outlook filters — which turns any email-emitting SaaS into an automation trigger with zero per-vendor integration. Both also gate on a **pre-approved sender allowlist**, which is simultaneously the right product default and the right prompt-injection control. This plan delivers that in PersonalClaw's architecture: **(S1)** generalize the event-trigger vocabulary so any source can emit, **(S2)** a mail **inbox provider app** (IMAP/SMTP first) so mail lands in the existing inbox, **(S3)** prompt-bound addresses + the trigger configuration UX. **Soul guardrails:** (1) **allowlist-first, fail-closed** — an unknown sender can never trigger anything, ever; this is an *inbound security surface*, so per §2.7 it fails CLOSED (a missing/corrupt allowlist disables triggering rather than permitting it), unlike the user-facing availability surfaces that fail open; (2) **mail bodies are untrusted, always fenced** — every mail body, subject, and attachment-derived text passes `fence_untrusted` before reaching any prompt, exactly as `inbox_service.py:87` already does for external text; a mail-triggered agent turn is the most attacker-reachable surface in the product; (3) **no vendor path in core** — IMAP/SMTP/Gmail logic lives entirely in an app bundle importing core only via `personalclaw.sdk.*`; core gains only the generalized event vocabulary and the typed source contract; (4) **draft-by-default for outbound** — a mail-triggered automation that would *send* mail starts non-sending, per the graduated-trust pattern both competitors independently adopted. Class **B** (new event patterns persisted in `event_triggers.json`, new app-owned state) — pre-LIFECYCLE-DOCTRINE, so it lands as a **plain clean break under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry).

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
- **S3 — prompt-bound addresses + the trigger UX.** The mechanism worth copying from Manus, in our shape: an app-owned table of **purpose-specific receiving addresses each carrying a stored default prompt** (`travel@…` → "build my itinerary, add calendar entries, buffer travel time"). Mail arriving at such an address fires a trigger whose action is `run-prompt`/`invoke-agent` with the stored prompt plus the fenced mail body. The user composes this with their **existing** Gmail/Outlook filters — zero per-SaaS integration work, which is exactly why it generalizes. Plus the Triggers page gains inbox-event configuration (source, address, sender pattern, subject/body matcher) and the honest **draft-by-default** posture for any action that would send mail.
- **What this is NOT:** not a mail client (no folder browsing, no full MUA UI); not a chat transport (§Boundary); not a notification/attention redesign (plan 42 owns that); not a workflow engine change (plan 7 owns what triggers fire into); not a Gmail-specific integration in core.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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

_(empty — no session has run yet)_
