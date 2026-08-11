# CHANNEL-EXPANSION — atomic plans

**Source plan:** [`CHANNEL-EXPANSION`](../plans/CHANNEL-EXPANSION.md)  
**Code:** `CE`  
**Source status:** proposed

CHANNEL-EXPANSION is DESIGNED and fully unstarted (no core trust seam, no channel apps, no kit/guide/CLI). 9 atoms, all todo: 1 core trust seam, 1 Slack migration, 3 channel apps (Telegram/Discord/email), plus a conformance kit, guide, Slack full-pattern, and ramp-coordination atom. Cross-plan edges to INBOX-NOTIFICATIONS-UNIFICATION (agent_request inbox kind), ECOSYSTEM-TOOLING (channel scaffold), and WORKFLOWS-V2-AUTOMATION-SUBSTRATE (trigger source).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CE-1` | ⬜ | Sender-trust core seam: channel_trust.py store+API, fence helper, SDK exports, SEL+owner-notification, pair CLI | — | channel_trust.py unit tests pass (allow/deny/track, pairing create/expire/single-use/wrong-code, policy defaults, corrupt-file->defaults+warn); fence_channel_content covered and apps import-boundary test green; SEL emits sender_paired/sender_denied/pairing_code_created; unknown-sender fixture yields one SEL entry + one actionable owner notification whose Allow action persists the sender (via existing DashboardState.notify mechanism); `personalclaw pair <provider>` prints an 8-digit code once, redeem within TTL works and refuses after; V1 echo-transport walkthrough recorded |
| `CE-2` | ✅ | Migrate the Slack app onto the core trust seam with a one-time loud migrate_to_core_trust() | `CE-1` | slack-channel persist_allowed_user/persist_tracking_channel delegate to core channel_trust; migrate_to_core_trust() moves app-local JSON into the core store once (idempotent, logged, second run no-op per fixture); Slack app tests green |
| `CE-3` | ✅ | Telegram channel app (raw Bot API over httpx): api client, MarkdownV2 escaper, transport, delivery, setup/doctor | `CE-1` | MockTransport tests cover getUpdates/sendMessage/editMessageText/sendDocument/sendPhoto/answerCallbackQuery/getMe incl. 429 retry-after; MarkdownV2 escaper table-driven tests pass full reserved set; long-poll transport maps ChannelMessage with trust hooks (DM pairing, group tracked-only, fencing) and honest capabilities; throttled edit-streaming <=1 edit/1.1s with exact final flush; inline-keyboard request_approval resolves the pending approval; `personalclaw setup` configures Telegram on a fixture; V2 owner-phone walkthrough recorded (owner task 1+2: BotFather bot + phone validation) |
| `CE-4` | ✅ | Discord channel app (Gateway WS + REST over httpx): gateway client, delivery+buttons, transport+trust, setup/doctor | `CE-1` | fake-WS tests cover identify/heartbeat/ack/resume/dispatch (guilds, guild_messages, direct_messages, message_content intents); REST delivery tests incl. 429-bucket backoff and approval-button round-trip; transport passes trust integration (DM pairing, guild channels tracked-only, fencing) with honest capabilities; setup/doctor configure end to end; V4 real-test-server validation recorded (owner task 3: Discord app+bot+server) |
| `CE-5` | ✅ | Email channel app (stdlib IMAP/SMTP in thread executors): poll transport+address-allowlist pairing, SMTP delivery+Message-ID threading, setup/doctor | `CE-1` | fake-IMAP tests: new-mail detection, UID persistence, code-in-reply pairing; fake-SMTP tests: correct In-Reply-To/References headers and thread continuity across three messages via session_map; capabilities declare streaming=false; setup/doctor (IMAP/SMTP hosts + app-password guidance, probe=login+select) configure end to end; V6 real-mailbox validation recorded, digest-target deferral noted if plan-42 S5 absent (owner task 4: dedicated mailbox + app password) |
| `CE-6` | ✅ | Channel conformance kit in core, wired into slack/telegram/discord/email test suites | `CE-1`, `CE-2`, `CE-3`, `CE-4`, `CE-5`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:emit_attention_item(kind=agent_request) for the unknown-sender inbox assertion (uses existing notification path until it lands)` | tests/channel_conformance.py::assert_channel_contract asserts connect/send/receive echo shapes, capabilities() completeness, health/test shapes, unknown-sender flow (canned reply + attention item), fence_channel_content applied to non-owner content, streaming throttle where declared; export-path decision recorded; all four apps pass the kit in apps-repo CI |
| `CE-7` | ⬜ | build-a-channel-app.md guide (from Telegram) + vendor-completeness section, and the kit's inbox-source check | `CE-3`, `CE-6`, `CE-8` | guide maps every ChannelDelivery/ChannelTransport method to a must/should/may obligation and documents transport lifecycle, trust integration, linking, conformance-kit usage, packaging; vendor-completeness section spells out the seam checklist (channel + inbox + trigger-source-when-available + contributed UI) and rule-2 'your UI, not core's' doctrine; conformance kit flags a channel-only app that does not also register an inbox source with a warning; S2-6 app tasks cite the checklist |
| `CE-8` | 🟡 | Bring Slack to the full vendor-completeness pattern: register the inbox MessageSourceProvider, move non-seam UI behind app ui block, scrub core vendor-name residue | `CE-2` | slack-channel app.json registers >=2 providers incl. an inbox MessageSourceProvider over the existing runtime client; Slack messages flow through the generic inbox source seam with no core slack string (inbox_providers docstring + native_source.py comment residue scrubbed); non-seam Slack surface lives behind the app's own ui block; boundary tests green |
| `CE-9` | ⬜ | Ramp coordination: community bounty issues + channel scaffold registration + trigger-source forward note | `CE-7`, `EXT:ECOSYSTEM-TOOLING:channel scaffold template + bounty board`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:app-registered trigger source types` | WhatsApp/Signal/Matrix GitHub issues live and labeled community-tier with the risk-policy paragraph and guide+kit links (after owner approves the risk-policy paragraph); the `channel` template is registered with ECOSYSTEM-TOOLING's scaffold (or a DISCOVERY note filed if the scaffold has not landed); a coordination line into WORKFLOWS-V2-AUTOMATION-SUBSTRATE records the app-registered trigger-source forward obligation with no bespoke early event glue shipped |

## Atom scopes

### `CE-1` — Sender-trust core seam: channel_trust.py store+API, fence helper, SDK exports, SEL+owner-notification, pair CLI

**Status:** todo

Session 1 — Trust seam (core): T1.1, T1.2, T1.3, T1.5 + V1; Design §S1; Contracts C1 (channel_trust API), C2 (channel_trust.json store)

**Done when:** channel_trust.py unit tests pass (allow/deny/track, pairing create/expire/single-use/wrong-code, policy defaults, corrupt-file->defaults+warn); fence_channel_content covered and apps import-boundary test green; SEL emits sender_paired/sender_denied/pairing_code_created; unknown-sender fixture yields one SEL entry + one actionable owner notification whose Allow action persists the sender (via existing DashboardState.notify mechanism); `personalclaw pair <provider>` prints an 8-digit code once, redeem within TTL works and refuses after; V1 echo-transport walkthrough recorded

### `CE-2` — Migrate the Slack app onto the core trust seam with a one-time loud migrate_to_core_trust()

**Status:** done (merged)

Session 1 — T1.4 (apps repo slack-channel); Design §S1 'Slack migration'

**Done when:** slack-channel persist_allowed_user/persist_tracking_channel delegate to core channel_trust; migrate_to_core_trust() moves app-local JSON into the core store once (idempotent, logged, second run no-op per fixture); Slack app tests green

### `CE-3` — Telegram channel app (raw Bot API over httpx): api client, MarkdownV2 escaper, transport, delivery, setup/doctor

**Status:** done (apps#26)

Sessions 2-3 — Telegram: T2.1–T2.5 + V2; Design §S2-3; Contracts C3 (Telegram column)

**Done when:** MockTransport tests cover getUpdates/sendMessage/editMessageText/sendDocument/sendPhoto/answerCallbackQuery/getMe incl. 429 retry-after; MarkdownV2 escaper table-driven tests pass full reserved set; long-poll transport maps ChannelMessage with trust hooks (DM pairing, group tracked-only, fencing) and honest capabilities; throttled edit-streaming <=1 edit/1.1s with exact final flush; inline-keyboard request_approval resolves the pending approval; `personalclaw setup` configures Telegram on a fixture; V2 owner-phone walkthrough recorded (owner task 1+2: BotFather bot + phone validation)

**Shipped** in `PersonalClawApps/telegram-channel/` (apps#26): a `ChannelTransportProvider` (`getUpdates` long-poll, offset persisted and advanced before dispatch) + `ChannelDelivery` over the raw Bot API on `httpx` — no vendor SDK — importing core only via `personalclaw.sdk.*`. `api.py` retries 429 (`retry_after` body → header, capped) and backs off on 5xx; `transport.py` maps `ChannelMessage` and runs trust against the real `channel_trust` seam (DM pairing, group tracked-only, non-owner fencing) with honest capabilities; `delivery.py` renders MarkdownV2, splits, throttles edits to ≤1/1.1s with an exact final flush, and resolves inline-keyboard approvals; `format.py` is the table-tested escaper over the full 18-char reserved set. Gate green: manifest round-trip stable, SDK-only boundary lint clean, `python -m pytest telegram-channel -q` → **108 passed**. Owner V2 real-phone walkthrough (plan "Owner tasks" 1–2) remains a real-world owner step, outside this automated change.

### `CE-4` — Discord channel app (Gateway WS + REST over httpx): gateway client, delivery+buttons, transport+trust, setup/doctor

**Status:** done (apps#29)

Sessions 4-5 — Discord: T4.1–T4.4 + V4; Design §S4-5; Contracts C3 (Discord column)

**Done when:** fake-WS tests cover identify/heartbeat/ack/resume/dispatch (guilds, guild_messages, direct_messages, message_content intents); REST delivery tests incl. 429-bucket backoff and approval-button round-trip; transport passes trust integration (DM pairing, guild channels tracked-only, fencing) with honest capabilities; setup/doctor configure end to end; V4 real-test-server validation recorded (owner task 3: Discord app+bot+server)

**Shipped** in `PersonalClawApps/discord-channel/` (apps#29): a `ChannelTransportProvider` + `ChannelDelivery` over the raw Gateway WS (`websockets`) + REST (`httpx`) — no vendor SDK, so the manifest declares no `pythonDependencies` — importing core only via `personalclaw.sdk.*`. `gateway.py` carries the full lifecycle (HELLO-driven heartbeat + ACK tracking, IDENTIFY with the intents bitfield summed from named bits = 37377, READY→`session_id`/`resume_gateway_url`, RESUME, INVALID_SESSION resumable-vs-not, RECONNECT) and contains the **zombie-connection** trap: a beat unacked when the next is due closes **4000**, never 1000 (1000 makes the session unresumable), and resumes; the sequence advances before dispatch so a raising handler can't replay one event forever. `api.py` implements **per-bucket** rate limiting — bucket state from the response headers, an exhausted bucket waited off *pre-emptively* instead of spending a 429, a global limit tracked separately from a per-route one, buckets keyed on method + concrete path so one busy channel can't stall another. `delivery.py` renders approvals as message **components** (Approve/Deny action row, `custom_id` carrying the request id, the `INTERACTION_CREATE` press always acknowledged inside Discord's 3s window even for a stale id, then the prompt edited to its outcome with `components` stripped so no clickable Approve survives), throttled edit-streaming with an exact final flush, 2000-char splitting. `transport.py` runs the REAL core trust seam (`guard_inbound`, provider `"discord"`) with `is_dm` derived from the **absence of `guild_id`** (Discord's actual signal) and drops self-authored/other-bot messages — `MESSAGE_CREATE` fires for the bot's own sends, a trap Telegram's `getUpdates` doesn't have. Every capability declared `True` has an implementation behind it (reactions, typing_indicator included), and the printed invite asks for exactly the six permission bits the code exercises (274878008384, no ADMINISTRATOR). Gate green: **198 tests** (no network, no wall-clock sleeps — `httpx.MockTransport`, a scripted fake WS, injected clocks; trust against the real seam in an isolated home), manifest round-trip stable, SDK-only boundary lint clean incl. `conftest.py`, `telegram-channel` 108 still green. The zombie check, close code, DM intent bit, `is_dm` derivation, self-message filter and global-429 gate were each **falsified by breaking them**. Owner V4 real-test-server walkthrough (plan "Owner tasks" 3 — Developer-Portal application + the MESSAGE CONTENT privileged intent + a bot invite) remains a real-world owner step, outside this automated change.

### `CE-5` — Email channel app (stdlib IMAP/SMTP in thread executors): poll transport+address-allowlist pairing, SMTP delivery+Message-ID threading, setup/doctor

**Status:** done (apps#30)

Session 6 — Email: T6.1–T6.3 + V6; Design §S6; Contracts C3 (Email column, streaming MUST-NOT)

**Done when:** fake-IMAP tests: new-mail detection, UID persistence, code-in-reply pairing; fake-SMTP tests: correct In-Reply-To/References headers and thread continuity across three messages via session_map; capabilities declare streaming=false; setup/doctor (IMAP/SMTP hosts + app-password guidance, probe=login+select) configure end to end; V6 real-mailbox validation recorded, digest-target deferral noted if plan-42 S5 absent (owner task 4: dedicated mailbox + app password)

**Shipped** in `PersonalClawApps/email-channel/` (apps#30): a two-way conversational email **channel** — `ChannelTransportProvider` (inbound) + `ChannelDelivery` (outbound) over stdlib `imaplib`/`smtplib` **only**, no vendor SDK, no new `pythonDependencies`, core imported exclusively via `personalclaw.sdk.*`. Distinct from the sibling `mail-inbox` app (EIAT-2), a `MessageSourceProvider` with its own allowlist: per plan 43's §Boundary the channel **reuses** the trust seam rather than forking one, so trust is owned by core `channel_trust` (`provider="email"`) and this app keeps no allowlist. Both stdlib APIs block, so every IMAP/SMTP call crosses a thread executor — one blocking `select()` on the loop would stall the whole gateway. The poll loop is **UID-based, never sequence numbers** (which renumber on expunge and would silently skip or reprocess), read-only, and persists `last_uid` + `UIDVALIDITY`, so a restart neither reprocesses nor skips; the `UIDVALIDITY` check runs **before** the search and re-derives the cursor under the new numbering, so a renumbered mailbox *recovers* rather than staying permanently skipped, and the advance lands before dispatch (and on `CancelledError`) so a raising handler can't replay one message forever. Trust is keyed on the `parseaddr` address **only**, with the `local@domain` shape verified rather than assumed (`parseaddr` hands back a bare token like `not-an-address` as the address half), so `From: "bob@allowed.example" <attacker@evil.example>` is denied — the display name is attacker-controlled and feeds UI text alone. Self-authored inbound is dropped: providers copy sent mail into the inbox and a reply-to-self loops forever (the `MESSAGE_CREATE` trap Discord has, absent from Telegram's `getUpdates`). Pairing is a reply containing the 8-digit code; allowed non-owner content enters the session as `guard_inbound`'s `fenced_text`, never raw; an unparseable message, missing `From`, or trust-store read failure denies and continues the loop. Outbound sets `In-Reply-To` plus an accumulating `References` chain keyed through `session_map`, so three messages stay one conversation. Contract C3's Email column is honored — `deliver_text` MUST, `deliver_rich` MAY (HTML alternative), `request_approval` by reply token, `upload_attachment` as a MIME part, `build_thread_link` as a `mid:` anchor — and the streaming trio is MUST-NOT: since `ChannelCapabilities` has no `streaming` field, streaming-falsity is declared as **`edits=False`** (in every other channel a stream *is* a repeatedly-edited message) with `start_stream()` returning `""` and no-op append/stop, both halves pinned by one test. Gate green: **306 tests** (no network, no wall-clock sleeps, no writes outside a tmp home — fake IMAP/SMTP servers and clocks injected, not monkeypatched onto stdlib; trust against the real seam in an isolated home), manifest round-trip stable + `validate()` clean with permissions `{storage, network}`, SDK-only boundary lint clean incl. `conftest.py`/`_fakes.py`, `telegram-channel` 108 and `mail-inbox` 32 still green. The UID advance, self-message filter, `parseaddr`-only trust match, three-message `References` chain, streaming-trio no-op and fenced-text path were each **falsified by breaking them**, which surfaced three real defects (a non-address trust key, the `UIDVALIDITY` check running after the search, and `imap_use_ssl` declared with no `setup` writer). Deferred with reasons: OAuth2/XOAUTH2, IMAP IDLE (`imaplib` has none), and plan-42 S5's digest target (`notification_rules.TARGETS` carries `channel_dm` with no dispatcher wired). Owner V6 real-mailbox walkthrough (plan "Owner tasks" 4 — dedicated mailbox + app password) remains a real-world owner step, outside this automated change.

### `CE-6` — Channel conformance kit in core, wired into slack/telegram/discord/email test suites

**Status:** done

Sessions 7-8 — Ramp: T7.1; Design §S7-8; Contracts C4 (assert_channel_contract)

**Done when:** tests/channel_conformance.py::assert_channel_contract asserts connect/send/receive echo shapes, capabilities() completeness, health/test shapes, unknown-sender flow (canned reply + attention item), fence_channel_content applied to non-owner content, streaming throttle where declared; export-path decision recorded; all four apps pass the kit in apps-repo CI

### `CE-7` — build-a-channel-app.md guide (from Telegram) + vendor-completeness section, and the kit's inbox-source check

**Status:** todo

Sessions 7-8 — Ramp: T7.2 + T7.5; Design §S7-8; Amendment 2026-07-26 (vendor-completeness pattern)

**Done when:** guide maps every ChannelDelivery/ChannelTransport method to a must/should/may obligation and documents transport lifecycle, trust integration, linking, conformance-kit usage, packaging; vendor-completeness section spells out the seam checklist (channel + inbox + trigger-source-when-available + contributed UI) and rule-2 'your UI, not core's' doctrine; conformance kit flags a channel-only app that does not also register an inbox source with a warning; S2-6 app tasks cite the checklist

### `CE-8` — Bring Slack to the full vendor-completeness pattern: register the inbox MessageSourceProvider, move non-seam UI behind app ui block, scrub core vendor-name residue

**Status:** in_progress

Amendment 2026-07-26 — T7.4 (apps slack-channel + core inbox_providers docstrings)

**Done when:** slack-channel app.json registers >=2 providers incl. an inbox MessageSourceProvider over the existing runtime client; Slack messages flow through the generic inbox source seam with no core slack string (inbox_providers docstring + native_source.py comment residue scrubbed); non-seam Slack surface lives behind the app's own ui block; boundary tests green

### `CE-9` — Ramp coordination: community bounty issues + channel scaffold registration + trigger-source forward note

**Status:** todo

Sessions 7-8 — Ramp: T7.3 + Amendment T7.6; Design §S7-8; owner task 5 (risk-policy approval)

**Done when:** WhatsApp/Signal/Matrix GitHub issues live and labeled community-tier with the risk-policy paragraph and guide+kit links (after owner approves the risk-policy paragraph); the `channel` template is registered with ECOSYSTEM-TOOLING's scaffold (or a DISCOVERY note filed if the scaffold has not landed); a coordination line into WORKFLOWS-V2-AUTOMATION-SUBSTRATE records the app-registered trigger-source forward obligation with no bespoke early event glue shipped

