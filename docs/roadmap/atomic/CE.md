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
| `CE-4` | ⬜ | Discord channel app (Gateway WS + REST over httpx): gateway client, delivery+buttons, transport+trust, setup/doctor | `CE-1` | fake-WS tests cover identify/heartbeat/ack/resume/dispatch (guilds, guild_messages, direct_messages, message_content intents); REST delivery tests incl. 429-bucket backoff and approval-button round-trip; transport passes trust integration (DM pairing, guild channels tracked-only, fencing) with honest capabilities; setup/doctor configure end to end; V4 real-test-server validation recorded (owner task 3: Discord app+bot+server) |
| `CE-5` | ⬜ | Email channel app (stdlib IMAP/SMTP in thread executors): poll transport+address-allowlist pairing, SMTP delivery+Message-ID threading, setup/doctor | `CE-1` | fake-IMAP tests: new-mail detection, UID persistence, code-in-reply pairing; fake-SMTP tests: correct In-Reply-To/References headers and thread continuity across three messages via session_map; capabilities declare streaming=false; setup/doctor (IMAP/SMTP hosts + app-password guidance, probe=login+select) configure end to end; V6 real-mailbox validation recorded, digest-target deferral noted if plan-42 S5 absent (owner task 4: dedicated mailbox + app password) |
| `CE-6` | ⬜ | Channel conformance kit in core, wired into slack/telegram/discord/email test suites | `CE-1`, `CE-2`, `CE-3`, `CE-4`, `CE-5`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:emit_attention_item(kind=agent_request) for the unknown-sender inbox assertion (uses existing notification path until it lands)` | tests/channel_conformance.py::assert_channel_contract asserts connect/send/receive echo shapes, capabilities() completeness, health/test shapes, unknown-sender flow (canned reply + attention item), fence_channel_content applied to non-owner content, streaming throttle where declared; export-path decision recorded; all four apps pass the kit in apps-repo CI |
| `CE-7` | ⬜ | build-a-channel-app.md guide (from Telegram) + vendor-completeness section, and the kit's inbox-source check | `CE-3`, `CE-6`, `CE-8` | guide maps every ChannelDelivery/ChannelTransport method to a must/should/may obligation and documents transport lifecycle, trust integration, linking, conformance-kit usage, packaging; vendor-completeness section spells out the seam checklist (channel + inbox + trigger-source-when-available + contributed UI) and rule-2 'your UI, not core's' doctrine; conformance kit flags a channel-only app that does not also register an inbox source with a warning; S2-6 app tasks cite the checklist |
| `CE-8` | ⬜ | Bring Slack to the full vendor-completeness pattern: register the inbox MessageSourceProvider, move non-seam UI behind app ui block, scrub core vendor-name residue | `CE-2` | slack-channel app.json registers >=2 providers incl. an inbox MessageSourceProvider over the existing runtime client; Slack messages flow through the generic inbox source seam with no core slack string (inbox_providers docstring + native_source.py comment residue scrubbed); non-seam Slack surface lives behind the app's own ui block; boundary tests green |
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

**Status:** todo

Sessions 4-5 — Discord: T4.1–T4.4 + V4; Design §S4-5; Contracts C3 (Discord column)

**Done when:** fake-WS tests cover identify/heartbeat/ack/resume/dispatch (guilds, guild_messages, direct_messages, message_content intents); REST delivery tests incl. 429-bucket backoff and approval-button round-trip; transport passes trust integration (DM pairing, guild channels tracked-only, fencing) with honest capabilities; setup/doctor configure end to end; V4 real-test-server validation recorded (owner task 3: Discord app+bot+server)

### `CE-5` — Email channel app (stdlib IMAP/SMTP in thread executors): poll transport+address-allowlist pairing, SMTP delivery+Message-ID threading, setup/doctor

**Status:** todo

Session 6 — Email: T6.1–T6.3 + V6; Design §S6; Contracts C3 (Email column, streaming MUST-NOT)

**Done when:** fake-IMAP tests: new-mail detection, UID persistence, code-in-reply pairing; fake-SMTP tests: correct In-Reply-To/References headers and thread continuity across three messages via session_map; capabilities declare streaming=false; setup/doctor (IMAP/SMTP hosts + app-password guidance, probe=login+select) configure end to end; V6 real-mailbox validation recorded, digest-target deferral noted if plan-42 S5 absent (owner task 4: dedicated mailbox + app password)

### `CE-6` — Channel conformance kit in core, wired into slack/telegram/discord/email test suites

**Status:** todo

Sessions 7-8 — Ramp: T7.1; Design §S7-8; Contracts C4 (assert_channel_contract)

**Done when:** tests/channel_conformance.py::assert_channel_contract asserts connect/send/receive echo shapes, capabilities() completeness, health/test shapes, unknown-sender flow (canned reply + attention item), fence_channel_content applied to non-owner content, streaming throttle where declared; export-path decision recorded; all four apps pass the kit in apps-repo CI

### `CE-7` — build-a-channel-app.md guide (from Telegram) + vendor-completeness section, and the kit's inbox-source check

**Status:** todo

Sessions 7-8 — Ramp: T7.2 + T7.5; Design §S7-8; Amendment 2026-07-26 (vendor-completeness pattern)

**Done when:** guide maps every ChannelDelivery/ChannelTransport method to a must/should/may obligation and documents transport lifecycle, trust integration, linking, conformance-kit usage, packaging; vendor-completeness section spells out the seam checklist (channel + inbox + trigger-source-when-available + contributed UI) and rule-2 'your UI, not core's' doctrine; conformance kit flags a channel-only app that does not also register an inbox source with a warning; S2-6 app tasks cite the checklist

### `CE-8` — Bring Slack to the full vendor-completeness pattern: register the inbox MessageSourceProvider, move non-seam UI behind app ui block, scrub core vendor-name residue

**Status:** todo

Amendment 2026-07-26 — T7.4 (apps slack-channel + core inbox_providers docstrings)

**Done when:** slack-channel app.json registers >=2 providers incl. an inbox MessageSourceProvider over the existing runtime client; Slack messages flow through the generic inbox source seam with no core slack string (inbox_providers docstring + native_source.py comment residue scrubbed); non-seam Slack surface lives behind the app's own ui block; boundary tests green

### `CE-9` — Ramp coordination: community bounty issues + channel scaffold registration + trigger-source forward note

**Status:** todo

Sessions 7-8 — Ramp: T7.3 + Amendment T7.6; Design §S7-8; owner task 5 (risk-policy approval)

**Done when:** WhatsApp/Signal/Matrix GitHub issues live and labeled community-tier with the risk-policy paragraph and guide+kit links (after owner approves the risk-policy paragraph); the `channel` template is registered with ECOSYSTEM-TOOLING's scaffold (or a DISCOVERY note filed if the scaffold has not landed); a coordination line into WORKFLOWS-V2-AUTOMATION-SUBSTRATE records the app-registered trigger-source forward obligation with no bespoke early event glue shipped

