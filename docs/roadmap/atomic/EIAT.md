# EMAIL-INBOX-AND-TRIGGERS — atomic plans

**Source plan:** [`EMAIL-INBOX-AND-TRIGGERS`](../plans/EMAIL-INBOX-AND-TRIGGERS.md)  
**Code:** `EIAT`  
**Source status:** in-progress

EIAT-1 is DONE (2026-08-09): the event vocabulary is source-agnostic. Premise verified live: unified triggers/ package with a source-bearing `event` kind is shipped (S1 target moved there); MessageSourceProvider ABC, 16 action providers, and AUTONOMY-GUARDRAILS are DONE, so nothing cross-plan blocks a start. EIAT-2/3 remain the greenfield mail-inbox app (no imap/smtp anywhere yet); EIAT-4/5 add prompt-bound addresses + the Triggers-page inbox UI. Owner real-world gates (test mailbox for S2 validation, receiving-address strategy, draft-by-default confirmation) gate validation, not implementation.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `EIAT-1` | ✅ | Generalize the event-trigger vocabulary to source-agnostic (core, S1) | `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:extend the shipped unified triggers/ `event` kind source vocabulary (source/pattern spec) rather than the retiring legacy event_triggers.py — satisfied (WF2 shipped), this is the seam S1 builds on` | An inbox trigger created against the existing filesystem inbox source fires a notify action; a memory trigger never fires on an inbox event and vice versa (source-scoped matching, unit-tested per matcher); the emitter is source-agnostic with no memory-specific shim left behind and memory triggers still fire exactly as before (regression); accepted inbox items emit exactly one event with correct raw value + structured meta while a rejected sender emits none (negative proven); inbox trigger routes round-trip, an InboxSender trigger with no sender_glob is rejected with a typed error code, and the offline reference drift test is green; debounce/storm-cap/max_fires behavior unchanged; make lint + targeted pytest + make test pass. |
| `EIAT-2` | ✅ | mail-inbox provider app: IMAP inbound, checkpointing, fail-closed allowlist, MIME extraction (S2) | `EIAT-1` | Polling a real IMAP mailbox surfaces messages as inbox items; a restart neither reprocesses nor skips (UID checkpoint via poll's returned dict, tested) and a duplicate Message-ID is dropped; the sender allowlist is provider-enforced and fail-closed — an unlisted sender and an empty allowlist both surface ZERO messages and zero events (both asserted), startup logs the posture, and SEL mail_sender_rejected fires per rejection; multipart mail extracts text/plain, HTML-only mail is sanitized, a PDF attachment contributes extracted text via the platform's existing document readers; credentials come only from the SDK credential store (never app.json/ProviderSettings); app installs from a local Store source. Validation (V2) requires an owner-provided test mailbox. |
| `EIAT-3` | ✅ | mail-inbox send_reply over SMTP, draft-by-default (S2) | `EIAT-2` | A reply is composed but NOT sent while draft-mode is on; enabling sending delivers and threads correctly via In-Reply-To; the provider honors the platform's live-writes/dry-run posture (supports_dry_run). Owner-gated on the draft-by-default confirmation (Owner task 4). |
| `EIAT-4` | ✅ (apps#34) | Prompt-bound receiving addresses + app settings UI (mail-inbox, S3) | `EIAT-1`, `EIAT-2` | Mail to a bound address fires run-prompt/invoke-agent and runs the stored user-authored default_prompt grounded in fence_untrusted(body, source='mail:<address>'); the fenced markers wrap the body and an in-body fence-break attempt is neutralised (explicit test); addresses (name/address/default_prompt/per-address senders) are manageable from the app's configSchema-generated settings page with secrets masked; README documents the worked Gmail-filter -> bound-address composition example. Validation (V3) needs the owner-decided receiving-address strategy (Owner task 2). |
| `EIAT-5` | ✅ | Triggers page inbox-event configuration UI (web/, S3) | `EIAT-1` | An inbox trigger (source, address glob, sender glob, subject/body matcher) is creatable from the Triggers page with URL-backed state, and draft-by-default is clearly surfaced for send-capable actions; no primitive-adoption ratchet trips (uses Button / ui/forms); a11y (focus-visible, reduced-motion) passes; cd web && npm run typecheck && npm test && npm run build all green. |
| `EIAT-6` | ✅ | Carry a source-declared item kind through to the inbox row (core) | `EIAT-1`, `EIAT-2` | A source declaring `kind="email"` (or `"mention"`) produces a row whose PERSISTED `item_kind` is that kind, selected by `GET /api/inbox?kind=email`, counted in `GET /api/inbox/kinds`, and equal to the value the dashboard's kind filter compares — proven by a round trip driven from the filesystem source's own `inbox/incoming/` directory through poll → ingest → disk → API, not by a dataclass unit test; a kind outside the channel-shaped set (including core's own non-channel attention kinds) is REFUSED with a warning naming the source and the rejected value while the row still arrives as `message` (never silently, never dropped); `SOURCE_DECLARABLE_KINDS` is asserted equal to `ItemKind` minus `NON_CHANNEL_KINDS` so a future enum member cannot skip the decision, and every declarable kind has a frontend chip (parity test against `inboxMeta.ts`); `inert-surface-baseline.json` regenerated on the shrink (`enum:ItemKind.EMAIL` + `enum:ItemKind.MENTION` cleared, 154 → 152); make lint + targeted pytest + make test green. |

## Atom scopes

### `EIAT-1` — Generalize the event-trigger vocabulary to source-agnostic (core, S1)

**Status:** done

Session 1 — Generalize the event vocabulary (core) (T1.1-T1.4, V1); Contracts C1 & C2; Design §S1. Re-scoped per the 2026-08-04 PREMISE AMENDMENT: widen the SHIPPED unified triggers/ `event` kind source vocabulary, not the legacy event_triggers.py.

**Done when:** An inbox trigger created against the existing filesystem inbox source fires a notify action; a memory trigger never fires on an inbox event and vice versa (source-scoped matching, unit-tested per matcher); the emitter is source-agnostic with no memory-specific shim left behind and memory triggers still fire exactly as before (regression); accepted inbox items emit exactly one event with correct raw value + structured meta while a rejected sender emits none (negative proven); inbox trigger routes round-trip, an InboxSender trigger with no sender_glob is rejected with a typed error code, and the offline reference drift test is green; debounce/storm-cap/max_fires behavior unchanged; make lint + targeted pytest + make test pass.

### `EIAT-2` — mail-inbox provider app: IMAP inbound, checkpointing, fail-closed allowlist, MIME extraction (S2)

**Status:** done (2026-08-09, apps#27)

Session 2 — The mail inbox provider app (T2.1, T2.2, T2.3); Contract C3. New PersonalClawApps/mail-inbox/ app.

Shipped as the `mail-inbox` bundle: `MailInboxProvider` (inbox `MessageSourceProvider`) polls IMAP past a persisted UID cursor (restart neither reprocesses nor skips), dedups on Message-ID, enforces a fail-closed sender allowlist (empty ⇒ zero messages, never connects; rejection logs a `mail_sender_rejected` SEL event), and extracts text/plain + sanitized HTML + PDF/DOCX/PPTX attachment text via the core document readers. The IMAP password lives only in the credential store under `MAIL_INBOX_PASSWORD`, never in app config. Stdlib-only (zero pythonDependencies); core reached only via `personalclaw.sdk.*`. Gate green: manifest round-trip + boundary lint + 32 tests. **Live-wiring** an installed inbox app into the gateway (`_init_inbox` hardcodes the filesystem source) and the **real-mailbox V2 validation** remain owner-gated, out of this atom's scope.

**Done when:** Polling a real IMAP mailbox surfaces messages as inbox items; a restart neither reprocesses nor skips (UID checkpoint via poll's returned dict, tested) and a duplicate Message-ID is dropped; the sender allowlist is provider-enforced and fail-closed — an unlisted sender and an empty allowlist both surface ZERO messages and zero events (both asserted), startup logs the posture, and SEL mail_sender_rejected fires per rejection; multipart mail extracts text/plain, HTML-only mail is sanitized, a PDF attachment contributes extracted text via the platform's existing document readers; credentials come only from the SDK credential store (never app.json/ProviderSettings); app installs from a local Store source. Validation (V2) requires an owner-provided test mailbox.

### `EIAT-3` — mail-inbox send_reply over SMTP, draft-by-default (S2)

**Status:** todo

Session 2 — T2.4; Contract C3 (Outbound). Guardrail 4 (draft-by-default outbound).

**Done when:** A reply is composed but NOT sent while draft-mode is on; enabling sending delivers and threads correctly via In-Reply-To; the provider honors the platform's live-writes/dry-run posture (supports_dry_run). Owner-gated on the draft-by-default confirmation (Owner task 4).

### `EIAT-4` — Prompt-bound receiving addresses + app settings UI (mail-inbox, S3)

**Status:** todo

Session 3 — T3.1, T3.3; Contract C4 (BoundAddress via ProviderSettings). App-side prompt-bound addresses and configSchema settings page.

**Done when:** Mail to a bound address fires run-prompt/invoke-agent and runs the stored user-authored default_prompt grounded in fence_untrusted(body, source='mail:<address>'); the fenced markers wrap the body and an in-body fence-break attempt is neutralised (explicit test); addresses (name/address/default_prompt/per-address senders) are manageable from the app's configSchema-generated settings page with secrets masked; README documents the worked Gmail-filter -> bound-address composition example. Validation (V3) needs the owner-decided receiving-address strategy (Owner task 2).

### `EIAT-5` — Triggers page inbox-event configuration UI (web/, S3)

**Status:** done (2026-08-09)

Session 3 — T3.2. Core web/ frontend only (separate repo/PR seam from EIAT-4's apps-repo work).

The Triggers create page grew a third kind, **Data event**, alongside Schedule and Lifecycle. Selecting it exposes a pattern picker (`InboxMessage` / `InboxSender` / `InboxAddress` / `MemoryUpdate` / `MemoryKeyPattern` / `ContentMatch`) in lockstep with `event_triggers.EVENT_PATTERNS`; each pattern renders exactly the ONE matcher field the backend's `matches()` reads (sender/address/key glob or content regex) and a derived source badge (Inbox vs Memory) — the source is never sent, mirroring `PATTERN_SOURCE`. `InboxSender` is gated matcher-required client-side, matching the server's `sender_glob_required` code so the empty submit is blocked before the round-trip. Both the chosen kind and pattern are URL-backed (`?kind` / `?pattern`, replace-mode) so the flow is deep-linkable and back/forward-safe. A send-capable action (`send-message` or any future `send-*`) surfaces a draft-by-default reminder before Create. New `api.createEvent`; new `triggerMeta` helpers (`EVENT_PATTERN_META`, `eventPatternMeta`, `eventSourceIcon`, `actionIsSendCapable`) with unit tests. Built only on primitives (Field / TextInput / Combobox / Button / Segmented) — no primitive-adoption ratchet trip. Gate green: make lint, `npm run typecheck`, 887 web tests, `npm run build`.

**DEVIATION — "subject/body matcher" mapped to the wired content matcher, not a new inbox field.** The done_when named a subject/body matcher for inbox events, but the shipped backend (`event_triggers.EventTrigger` / `matches()`) has NO subject or body field for the inbox source — inbox matching is sender_glob/address_glob only; content matching exists solely on the memory source (`content_re` via `ContentMatch`). Building a subject/body control in the UI would ship an inert input the backend silently ignores, violating the no-inert-control tenet. Resolution: the create form exposes the content matcher where it is actually wired (`ContentMatch` on the memory source) and the two wired inbox matchers (sender/address glob); an inbox subject/body matcher is a backend feature (add a field + `matches()` branch on `event_triggers.py`) that must precede any UI, and is left to EIAT-4's app-side work or a follow-up core atom.

**DEVIATION — draft-by-default surfaced by a provider heuristic, not a core capability flag.** The done_when asked that draft-by-default be "clearly surfaced for send-capable actions." Core has no per-action-provider send-capability/draft flag today (the real draft-by-default posture is owned by the mail-inbox app's `send_reply`, EIAT-3, which is still todo/owner-gated). Resolution: `actionIsSendCapable()` is a documented UI-copy heuristic keyed to the one bundled send provider (`send-message`) plus any future `send-*`, rendering an informational note; it is explicitly NOT a capability contract. When EIAT-3 lands a real posture, this note should read it instead of the name heuristic.

**Done when:** An inbox trigger (source, address glob, sender glob, subject/body matcher) is creatable from the Triggers page with URL-backed state, and draft-by-default is clearly surfaced for send-capable actions; no primitive-adoption ratchet trips (uses Button / ui/forms); a11y (focus-visible, reduced-motion) passes; cd web && npm run typecheck && npm test && npm run build all green.


### `EIAT-6` — Carry a source-declared item kind through to the inbox row (core)

**Status:** done (2026-08-11)

Follow-on to EIAT-1/EIAT-2, core only. The missing half of the source→inbox seam:
`IncomingMessage.kind` plus its validated resolution into `InboxItem.item_kind`.

**Design.** The inbox shipped **live readers for two kinds nothing could write.**
`inboxMeta.ts` declares `{key:'mention'}` and `{key:'email'}` chips, `InboxPage` filters
`(it.item_kind || 'message') === kind`, `handlers_inbox` filters (`?kind=`) and counts
(`/api/inbox/kinds`) on the field, and `inbox.py` persists it — but
`inbox_providers/base.py::IncomingMessage` had **no kind field at all** (id, channel_id,
channel_name, thread_id, text, sender_id, sender_name, timestamp, thread_context, is_dm).
`InboxService._ingest` therefore constructed every polled row with the dataclass default,
so `ItemKind.EMAIL` and `ItemKind.MENTION` were unreachable *by construction* — a mail item
was indistinguishable from a channel message. `inert-surface-baseline.json` had independently
censused both members as inert.

Three decisions:

1. **The kind rides the seam, declared by the source.** `IncomingMessage.kind` (default
   `message`) → `_ingest` → `InboxItem.item_kind`. Only a source knows its kind, and only
   from its own payload: a mail source knows it polled a mailbox; a channel source knows the
   vendor payload listed the operator among a message's at-mention ids.
2. **Core never infers a kind.** No text scan for the user's name. `evaluate_alert` already
   does that kind of matching for *alerting*, and reusing it here would have been the
   difference between a soft signal and a durable, filterable classification — a row
   permanently labelled "Mention" because someone typed the operator's first name.
3. **Unknown kind ⇒ refuse the CLAIM, keep the MESSAGE, log loudly.**
   `_resolve_source_kind` accepts only `SOURCE_DECLARABLE_KINDS` (= `ItemKind` minus
   `NON_CHANNEL_KINDS` = message/mention/email); anything else is filed as `message` with a
   warning naming the source and the value. Trusting the value would let a source mint a kind
   no chip, icon or label exists for (an unfilterable row) or claim one of core's non-channel
   attention kinds (a row with no refs, no deep-link, no reply — a dead row wearing a live
   kind's chip). Dropping the message would mean one typo (`"mail"` for `"email"`) silently
   stops a user's mail arriving, and in the filesystem source's case wedges the poll batch on
   the offending file forever. Mis-filing one row is strictly better than losing it, and the
   mistake is loud on the only side that can fix it — the provider author's logs. It is
   explicitly **not** a silent fallback: an unknown kind always warns.

**Implementation plan (as executed).**

1. `inbox.py`: `SOURCE_DECLARABLE_KINDS` beside `NON_CHANNEL_KINDS` (+ `__all__`) —
   enumerated literally for legibility, ratcheted equal to `ItemKind` minus the non-channel
   set by test, so a new enum member cannot skip the declarable/non-declarable decision.
2. `inbox_providers/base.py`: `kind: str = ItemKind.MESSAGE.value` on `IncomingMessage`, with
   the closed set and the refusal posture documented where a source author reads it.
3. `inbox_service.py`: `_resolve_source_kind(declared, source_name)` + `item_kind=` on the
   `_ingest` row. One validation point for every source, present and future.
4. `inbox_providers/filesystem_source.py`: pass the batch entry's `kind` through — core's
   live writer. The drop-box is the documented seam for a local producer (a mail fetcher, a
   channel bridge) to state what a message is; it never guesses.
5. `tests/test_inbox_item_kind_seam.py`: the round trip (drop a real batch in
   `<home>/inbox/incoming/` → `FilesystemSourceProvider.poll` → `_poll_once` → re-read from
   disk → `api_inbox_list`/`api_inbox_kinds`), the refusal cases with the warning asserted,
   the enum-parity ratchet, and a parity test that every declarable kind has an
   `ITEM_KINDS` chip in `inboxMeta.ts`.
6. `inert-surface-baseline.json` regenerated with its own generator on the legitimate
   shrink: `src/personalclaw/inbox.py` 3 → 1 inert surface, totals 154 → 152 (enum 27 → 25).

**Which kinds got a real core writer.** Both `email` and `mention` are now reachable and
proven end-to-end through core's own filesystem source — but as *declared* kinds, not
core-derived ones: core validates and persists what the producer states. No core source
derives either kind, and none should. The **native** push source was deliberately left
alone: an agent-posted item is not a channel message, but re-labelling it (`system` /
`agent_request`) would move it out of `NON_CHANNEL_KINDS`' complement and kill the
`can_reply` reply-routing that native questions depend on — a change with a frontend half,
and not this atom's. **`ItemStatus.SENT` is untouched**: it belongs to EIAT-3's reply path,
which is owner-gated on the draft-by-default decision.

**Follow-on (apps repo, out of scope here).** `mail-inbox` must pass `kind="email"` when it
builds each `IncomingMessage`; a channel source must pass `kind="mention"` when the vendor
payload lists the operator among the message's at-mention ids, and `message` otherwise.
Nothing else changes — the field defaults to `message`, so an app that does not set it keeps
today's behavior exactly. Valid values are `message` / `mention` / `email`; anything else is
refused (row kept, filed as `message`, warning logged). No SDK export was added on purpose:
`personalclaw.sdk.inbox` would gain a symbol with no in-repo consumer, i.e. a NEW inert
surface, so the closed set is documented on the dataclass instead of exported as a constant.

**Done when:** A source declaring `kind="email"` (or `"mention"`) produces a row whose
PERSISTED `item_kind` is that kind, selected by `GET /api/inbox?kind=email`, counted in
`GET /api/inbox/kinds`, and equal to the value the dashboard's kind filter compares — proven
by a round trip driven from the filesystem source's own `inbox/incoming/` directory through
poll → ingest → disk → API, not by a dataclass unit test; a kind outside the channel-shaped
set (including core's own non-channel attention kinds) is REFUSED with a warning naming the
source and the rejected value while the row still arrives as `message` (never silently, never
dropped); `SOURCE_DECLARABLE_KINDS` is asserted equal to `ItemKind` minus `NON_CHANNEL_KINDS`
so a future enum member cannot skip the decision, and every declarable kind has a frontend
chip (parity test against `inboxMeta.ts`); `inert-surface-baseline.json` regenerated on the
shrink (`enum:ItemKind.EMAIL` + `enum:ItemKind.MENTION` cleared, 154 → 152); make lint +
targeted pytest + make test green.
