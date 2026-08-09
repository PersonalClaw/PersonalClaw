# EMAIL-INBOX-AND-TRIGGERS — atomic plans

**Source plan:** [`EMAIL-INBOX-AND-TRIGGERS`](../plans/EMAIL-INBOX-AND-TRIGGERS.md)  
**Code:** `EIAT`  
**Source status:** in-progress

EIAT-1 is DONE (2026-08-09): the event vocabulary is source-agnostic. Premise verified live: unified triggers/ package with a source-bearing `event` kind is shipped (S1 target moved there); MessageSourceProvider ABC, 16 action providers, and AUTONOMY-GUARDRAILS are DONE, so nothing cross-plan blocks a start. EIAT-2/3 remain the greenfield mail-inbox app (no imap/smtp anywhere yet); EIAT-4/5 add prompt-bound addresses + the Triggers-page inbox UI. Owner real-world gates (test mailbox for S2 validation, receiving-address strategy, draft-by-default confirmation) gate validation, not implementation.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `EIAT-1` | ✅ | Generalize the event-trigger vocabulary to source-agnostic (core, S1) | `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:extend the shipped unified triggers/ `event` kind source vocabulary (source/pattern spec) rather than the retiring legacy event_triggers.py — satisfied (WF2 shipped), this is the seam S1 builds on` | An inbox trigger created against the existing filesystem inbox source fires a notify action; a memory trigger never fires on an inbox event and vice versa (source-scoped matching, unit-tested per matcher); the emitter is source-agnostic with no memory-specific shim left behind and memory triggers still fire exactly as before (regression); accepted inbox items emit exactly one event with correct raw value + structured meta while a rejected sender emits none (negative proven); inbox trigger routes round-trip, an InboxSender trigger with no sender_glob is rejected with a typed error code, and the offline reference drift test is green; debounce/storm-cap/max_fires behavior unchanged; make lint + targeted pytest + make test pass. |
| `EIAT-2` | ⬜ | mail-inbox provider app: IMAP inbound, checkpointing, fail-closed allowlist, MIME extraction (S2) | `EIAT-1` | Polling a real IMAP mailbox surfaces messages as inbox items; a restart neither reprocesses nor skips (UID checkpoint via poll's returned dict, tested) and a duplicate Message-ID is dropped; the sender allowlist is provider-enforced and fail-closed — an unlisted sender and an empty allowlist both surface ZERO messages and zero events (both asserted), startup logs the posture, and SEL mail_sender_rejected fires per rejection; multipart mail extracts text/plain, HTML-only mail is sanitized, a PDF attachment contributes extracted text via the platform's existing document readers; credentials come only from the SDK credential store (never app.json/ProviderSettings); app installs from a local Store source. Validation (V2) requires an owner-provided test mailbox. |
| `EIAT-3` | ⬜ | mail-inbox send_reply over SMTP, draft-by-default (S2) | `EIAT-2` | A reply is composed but NOT sent while draft-mode is on; enabling sending delivers and threads correctly via In-Reply-To; the provider honors the platform's live-writes/dry-run posture (supports_dry_run). Owner-gated on the draft-by-default confirmation (Owner task 4). |
| `EIAT-4` | ⬜ | Prompt-bound receiving addresses + app settings UI (mail-inbox, S3) | `EIAT-1`, `EIAT-2` | Mail to a bound address fires run-prompt/invoke-agent and runs the stored user-authored default_prompt grounded in fence_untrusted(body, source='mail:<address>'); the fenced markers wrap the body and an in-body fence-break attempt is neutralised (explicit test); addresses (name/address/default_prompt/per-address senders) are manageable from the app's configSchema-generated settings page with secrets masked; README documents the worked Gmail-filter -> bound-address composition example. Validation (V3) needs the owner-decided receiving-address strategy (Owner task 2). |
| `EIAT-5` | ⬜ | Triggers page inbox-event configuration UI (web/, S3) | `EIAT-1` | An inbox trigger (source, address glob, sender glob, subject/body matcher) is creatable from the Triggers page with URL-backed state, and draft-by-default is clearly surfaced for send-capable actions; no primitive-adoption ratchet trips (uses Button / ui/forms); a11y (focus-visible, reduced-motion) passes; cd web && npm run typecheck && npm test && npm run build all green. |

## Atom scopes

### `EIAT-1` — Generalize the event-trigger vocabulary to source-agnostic (core, S1)

**Status:** done

Session 1 — Generalize the event vocabulary (core) (T1.1-T1.4, V1); Contracts C1 & C2; Design §S1. Re-scoped per the 2026-08-04 PREMISE AMENDMENT: widen the SHIPPED unified triggers/ `event` kind source vocabulary, not the legacy event_triggers.py.

**Done when:** An inbox trigger created against the existing filesystem inbox source fires a notify action; a memory trigger never fires on an inbox event and vice versa (source-scoped matching, unit-tested per matcher); the emitter is source-agnostic with no memory-specific shim left behind and memory triggers still fire exactly as before (regression); accepted inbox items emit exactly one event with correct raw value + structured meta while a rejected sender emits none (negative proven); inbox trigger routes round-trip, an InboxSender trigger with no sender_glob is rejected with a typed error code, and the offline reference drift test is green; debounce/storm-cap/max_fires behavior unchanged; make lint + targeted pytest + make test pass.

### `EIAT-2` — mail-inbox provider app: IMAP inbound, checkpointing, fail-closed allowlist, MIME extraction (S2)

**Status:** todo

Session 2 — The mail inbox provider app (T2.1, T2.2, T2.3); Contract C3. New PersonalClawApps/mail-inbox/ app.

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

**Status:** todo

Session 3 — T3.2. Core web/ frontend only (separate repo/PR seam from EIAT-4's apps-repo work).

**Done when:** An inbox trigger (source, address glob, sender glob, subject/body matcher) is creatable from the Triggers page with URL-backed state, and draft-by-default is clearly surfaced for send-capable actions; no primitive-adoption ratchet trips (uses Button / ui/forms); a11y (focus-visible, reduced-motion) passes; cd web && npm run typecheck && npm test && npm run build all green.

