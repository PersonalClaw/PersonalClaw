# Plan: Channel Expansion — Core Channels Beyond the Slack Proof of Concept

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "first wave = few core channels most popular in market beyond the slack proof of concept")
**Created:** 2026-07-18
**Wave:** 1 (S1-3: sender-trust seam + Telegram) + 2 (S4-8: Discord, email, author ramp)
**Depends on:** nothing hard — the seams are proven vendor-blind. EXTERNAL-ACCESS §3's sender-trust research is absorbed here early. Coordinates with INBOX-NOTIFICATIONS-UNIFICATION (channel DM as a rules-selectable delivery target; pairing prompts become inbox items post-plan-42) and PROVIDER-BOUNDARY-COMPLETION (do not add residue while it removes some).
**Scope:** channels are the mobile story, the retention mechanism, and the viral demo. This plan adds the trust substrate + Telegram, Discord, and email first-party apps + the channel-author ramp. **Soul guardrail:** every channel is an app bundle against the existing seams — zero vendor code enters core. First-party channels use **official APIs only** (no reverse-engineered protocols; WhatsApp/Signal/iMessage are community-tier by explicit risk policy). Dependency discipline: prefer wire-protocol implementations over vendor SDKs (httpx + websockets are already core deps; a vendor SDK enters an app only with a task line naming it).

---

## Context (code recon, 2026-07-18)

- **The inbound seam** (`channel_transports/base.py`): `ChannelTransportProvider` ABC — `name`, `display_name`, `connect/disconnect`, `send(OutboundMessage)`, `receive() -> AsyncIterator[ChannelMessage]`, `start_inbound(services)/stop_inbound`, `health()`, `test()`, `capabilities() -> ChannelCapabilities`, `info()`. Dataclasses `OutboundMessage`/`ChannelMessage`/`ChannelCapabilities` exist.
- **The outbound seam** (`channel_delivery.py`): 18-method `ChannelDelivery` protocol incl. `open_dm`, `deliver_text/rich/cron_result/notification/chat_mirror/subagent_reply`, `resolve_user_name/profile`, `channel_info`, `list_reply_channels`, `is_tracked_channel`, `build_thread_link`, `upload_attachment`, streaming (`start_stream/append_stream_task/stop_stream`), `request_approval`.
- **Trust today is app-local:** `apps/slack-channel/slack_runtime/allowlist.py` — `persist_allowed_user`, `persist_tracking_channel`, owner Allow/Deny prompt flows, dashboard-link send. The generic transport has **no trust vocabulary** — the gap this plan's S1 closes.
- **Linking:** `session_map.py` provides the generic thread↔session map (`set/get`, provider+cwd fields, thread index); channel apps call through it.
- **SDK surface:** `sdk/channel.py` already re-exports the transport/delivery/GatewayServices/security/session surfaces apps need — trust joins these exports.

## Design

### S1 — Sender trust as a core seam (`src/personalclaw/channel_trust.py`)

- **Store:** `~/.personalclaw/entity_settings/channel_trust.json` (atomic_write): per provider — `allowed_senders {id: {name, added_at, via: owner|pairing}}`, `tracked_channels {id: {name, added_at}}`, `pairing {code_hash, expires_at, created_at}` (single active code per provider, single-use, TTL 10 min, 8-digit numeric, **hash stored** — sha256), `policies {dm: pairing|owner_only|open, group: tracked_only|off}` (defaults: `dm=pairing`, `group=tracked_only`).
- **API (sdk-exported):** `is_allowed_sender(provider, sender_id)`, `allow_sender/deny_sender`, `is_tracked_channel`, `track/untrack`, `create_pairing_code(provider) -> code` (returned once), `redeem_pairing_code(provider, sender_id, code) -> bool` (constant-time hash compare; consumes on success), `trust_policies(provider)`.
- **Unknown-sender flow (transport-side contract, documented + conformance-tested):** DM from non-allowed sender → if text matches active code: redeem → allow + owner notification "paired: <name>"; else → canned pairing-needed reply (rate-limited: once per sender per 24h, tracked in-store) + owner notification with Allow/Deny meta-actions (existing notification action pattern; becomes inbox `agent_request` after plan 42). **Non-owner content is data:** group/tracked-channel content from non-owner senders passes `fence_untrusted(text, source="channel:<provider>:<sender>")` before entering any session context — helper `fence_channel_content()` exported via sdk so transports can't hand-roll it.
- **Slack migration:** `allowlist.py` persist/query functions become adapters over the seam (its prompt UX unchanged); its JSON store migrates via a loud one-time `migrate_to_core_trust()` (the `migrate_from_core` precedent, reversed). SEL events: `sender_paired`, `sender_denied`, `pairing_code_created`.

### S2-3 — Telegram (`apps/telegram-channel`) — first: best official bot API

- **No SDK:** raw Bot API over `httpx` (core dep). Inbound = long-poll `getUpdates` loop in `start_inbound` (offset-tracked, 50s timeout, backoff on failure); webhook mode deferred to EXTERNAL-ACCESS. Outbound `SendMessage`/`editMessageText`; **streaming = throttled edits** (≥1.1s between edits, final flush on `stop_stream`); `sendDocument`/`sendPhoto` for `upload_attachment`; MarkdownV2 with a proper escaper (its own module + table-driven tests — Telegram escaping is the classic footgun); `request_approval` = inline keyboard (Approve/Deny callback_query → the same approval answer path Slack uses); `build_thread_link` = `https://t.me/...` deep link. DMs pair via the trust seam; groups require `tracked_channels` (bot privacy mode documented). Capabilities: streaming=edit-based, rich=limited, threads=reply-chains (+forum topics where enabled).
- App layout mirrors slack-channel: `transport.py`, `delivery.py`, `format.py` (escaper), `api.py` (thin Bot API client), `settings.py` (ProviderSettings: token via credential store key `TELEGRAM_BOT_TOKEN`), `cli_setup.py`/`cli_doctor.py` (plan 32 seams), `test_*.py` with a fake Bot API (httpx MockTransport).

### S4-5 — Discord (`apps/discord-channel`)

- Needs the Gateway WS for events: minimal client over `websockets` (core dep) — identify (intents: guilds, guild_messages, direct_messages, message_content), heartbeat/ack, resume on reconnect, dispatch MESSAGE_CREATE/INTERACTION_CREATE; REST over httpx for sends/edits/uploads. Approvals = message components (buttons). Streaming = throttled edits (rate-limit-aware, respect 429 buckets). Trust: DMs pair; servers/channels tracked-only. The community's own Discord server (OSS-OPERATIONS) runs this app as production dogfood.

### S6 — Email (`apps/email-channel`)

- Inbound: IMAP poll (stdlib `imaplib` in a thread executor, 60s cadence, UID-tracked; IDLE optional later), sender trust = address allowlist (pairing code = a reply containing the code); HTML→text via core `html2text` path. Outbound: SMTP (stdlib `smtplib`, thread executor; app-password auth documented; OAuth2 deferred with a DISCOVERY note). Threading via `Message-ID`/`In-Reply-To`/`References` → session_map keys. No streaming (capabilities say so); digest delivery target for plan 42's rules. Credential keys `EMAIL_IMAP_*`/`EMAIL_SMTP_*` via the credential store.

### S7-8 — The author ramp

- **Conformance kit:** `tests/channel_conformance.py` in core (exported for app use): given a provider instance + fake backend, asserts the contract — connect/send/receive echo shapes, capabilities dict completeness, health/test shapes, trust-seam integration (unknown-sender flow fires the canned reply + notification), fencing applied to non-owner content, streaming throttle honored. Slack/Telegram/Discord/email all pass it.
- **Guide:** `docs/guides/build-a-channel-app.md` extracted from the Telegram implementation (the cleanest reference): transport lifecycle, delivery-contract table with "must/should/may" per method, trust integration, linking, conformance-kit usage, packaging/manifest. Feeds ECOSYSTEM-TOOLING's `channel` scaffold template + bounty board (WhatsApp/Signal/Matrix as labeled community bounties with the risk-policy note).

## Contracts & Interfaces (this plan OWNS the trust seam; delivery/transport seams are existing, [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md) §3.5)

### C1 — `src/personalclaw/channel_trust.py` (new; exported via `sdk/channel.py`, §2.8 → Tier-S)

```python
def is_allowed_sender(provider: str, sender_id: str) -> bool: ...
def allow_sender(provider: str, sender_id: str, name: str = "", *, via: str = "owner") -> None: ...
def deny_sender(provider: str, sender_id: str) -> None: ...
def is_tracked_channel(provider: str, channel_id: str) -> bool: ...
def track(provider: str, channel_id: str, name: str = "") -> None: ...
def untrack(provider: str, channel_id: str) -> None: ...
def create_pairing_code(provider: str) -> str: ...        # 8-digit, TTL 600s, hash stored, single active per provider
def redeem_pairing_code(provider: str, sender_id: str, code: str) -> bool: ...  # constant-time; consumes on success
def trust_policies(provider: str) -> dict: ...            # {"dm": "...", "group": "..."}
def fence_channel_content(text: str, provider: str, sender_id: str) -> str:
    return fence_untrusted(text, source=f"channel:{provider}:{sender_id}")  # §3.7
```

### C2 — Trust store `~/.personalclaw/entity_settings/channel_trust.json`

```jsonc
{
  "<provider>": {
    "allowed_senders": {"<id>": {"name":"", "added_at":"<ISO>", "via":"owner|pairing"}},
    "tracked_channels": {"<id>": {"name":"", "added_at":"<ISO>"}},
    "pairing": {"code_hash":"<sha256>", "expires_at":"<ISO>", "created_at":"<ISO>"},
    "policies": {"dm":"pairing|owner_only|open", "group":"tracked_only|off"},  // defaults: dm=pairing, group=tracked_only
    "rate": {"<sender_id>":"<ISO last canned-reply>"}   // 24h once-per-sender pairing-needed reply
  }
}
```
Corrupt/missing → defaults + warn (fail-open for the *store*; but an unknown sender is denied by *policy* — that's the fail-closed half). SEL events: `sender_paired`, `sender_denied`, `pairing_code_created`.

### C3 — Per-transport delivery obligation table (the conformance contract; full must/should/may in `docs/guides/build-a-channel-app.md`)

| ChannelDelivery method (§3.5) | Telegram | Discord | Email |
|---|---|---|---|
| `deliver_text` | MUST | MUST | MUST |
| `deliver_rich` | SHOULD (MarkdownV2) | SHOULD (embeds) | MAY (HTML) |
| streaming trio | SHOULD (throttled edit ≥1.1s) | SHOULD (edit, 429-aware) | MUST-NOT (capabilities: streaming=false) |
| `request_approval` | MUST (inline keyboard) | MUST (buttons) | SHOULD (reply-token) |
| `upload_attachment` | SHOULD | SHOULD | SHOULD (MIME parts) |
| `build_thread_link` | MUST (t.me deep link) | MUST | MAY (message-id anchor) |

Each transport declares honest `ChannelCapabilities` (§3.5 dataclass). Credential keys: `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `EMAIL_IMAP_{HOST,USER,PASS,PORT}` / `EMAIL_SMTP_{...}` (credential store, §2.5).

### C4 — Conformance kit `tests/channel_conformance.py`
`assert_channel_contract(provider_instance, fake_backend)` — asserts: connect/send/receive echo shapes; `capabilities()` dict completeness; health/test shapes; unknown-sender flow (canned reply + `emit_attention_item(kind="agent_request")`); `fence_channel_content` applied to non-owner content before it enters session context; streaming throttle honored where declared. Every channel app's test suite calls it.

### Integration points
- **Calls:** `fence_untrusted` (§3.7), `session_map.set/get` (§3.5 linking), `emit_attention_item(kind="agent_request")` (plan 42 — owner Allow/Deny), `sel()`, `atomic_write`/`config_dir`.
- **Called by:** every channel transport (slack migrates onto it in S1 T1.4; Telegram/Discord/email consume it); plan 24 §3 inherits this seam (does NOT rebuild it).
- **Consumed by:** 42 (channel_dm delivery target routes through `ChannelDelivery.deliver_notification` on these transports).
- **Storage owned:** `channel_trust.json`; apps own their `data/config.json` (ProviderSettings, §2.6) + offset/UID state in `data/`.
- **SDK exports added:** the C1 API block + `fence_channel_content` in `sdk/channel.py`.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Trust seam (core)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `channel_trust.py`: store + full API per Design (atomic writes, hash-only codes, constant-time redeem, rate-limit bookkeeping) | create `src/personalclaw/channel_trust.py`, `tests/test_channel_trust.py` | unit tests: allow/deny/track, pairing lifecycle (create/expire/single-use/wrong-code), policy defaults, corrupt-file → defaults+warning |
| T1.2 | `fence_channel_content()` helper wrapping `fence_untrusted` with the channel source format; sdk exports for the whole trust API | `src/personalclaw/channel_trust.py`, `src/personalclaw/sdk/channel.py` | import-boundary test still green; helper covered by test |
| T1.3 | SEL events (`sender_paired/denied`, `pairing_code_created`) + owner notification with Allow/Deny actions on unknown-sender (reuse the existing notification-action mechanism — locate Slack's Allow/Deny prompt wiring and generalize the *notification* half into core, leaving Slack's in-channel prompt UX app-side) | `channel_trust.py`, notification wiring site (locate via `allowlist.py` imports) | unknown-sender fixture produces one SEL entry + one actionable notification; Allow action persists the sender |
| T1.4 | Slack app onto the seam: `persist_allowed_user/tracking_channel` delegate to core trust; one-time loud `migrate_to_core_trust()` moving its JSON into the core store (idempotent, logged) | apps repo: `slack-channel/slack_runtime/allowlist.py`, `settings.py` | slack tests green; migration fixture: app-local entries appear in core store once, second run no-op |
| T1.5 | `personalclaw pair <provider>` CLI (creates + prints a code with TTL note) + `docs/reference/cli.md` entry | `src/personalclaw/cli.py` | code printed once; redeem within TTL works, after TTL refuses |
| V1 | Validation: with the echo transport — unknown sender → canned reply + notification; pair via CLI code; sender now converses; group message from non-owner arrives fenced in session context (inspect stored context) | — | all hold; ledger written |

### Sessions 2-3 — Telegram

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Bot API client (`api.py`): typed thin wrappers for getUpdates/sendMessage/editMessageText/sendDocument/sendPhoto/answerCallbackQuery/getMe; httpx, timeout+retry/backoff; no SDK | apps repo: create `telegram-channel/{app.json,api.py,settings.py}` | MockTransport tests for each wrapper incl. 429 retry-after handling |
| T2.2 | MarkdownV2 escaper (`format.py`): table-driven tests over the full reserved set + code blocks + links | `telegram-channel/format.py`, tests | every reserved char case passes; round-trip of a chat message with code fences renders (manual check in V) |
| T2.3 | Transport: long-poll loop (offset persistence in app `data/`), ChannelMessage mapping, trust-seam integration (DM pairing flow, group tracked-only, fencing), `capabilities()` honest (edit-streaming, reply-threads) | `telegram-channel/transport.py` | conformance kit passes once T7.1 exists (until then: unit tests for mapping + trust hooks) |
| T2.4 | Delivery: `ChannelDelivery` implementation — text/rich (MarkdownV2), throttled edit-streaming (≥1.1s + final flush), uploads, `request_approval` inline keyboard wired to the approval answer path (find Slack's `request_approval` → answer plumbing and mirror it), `build_thread_link` | `telegram-channel/delivery.py` | fake-API tests: stream produces ≤1 edit/1.1s and exact final text; approval callback resolves the pending approval |
| T2.5 | Setup/doctor contributions (plan 32 seams): token prompt (BotFather instructions), getMe probe | `telegram-channel/cli_setup.py`, `cli_doctor.py`, manifest | `personalclaw setup` configures Telegram end to end on a fixture |
| V2 | Validation (owner phone required — owner task 2): pair from a real phone via code; chat with tool-approval round-trip on inline buttons; background cron result delivered; loop status nudge; attachment both directions; group behavior (untracked silent, tracked + mention responds with fencing verified in logs) | — | full walkthrough recorded in Execution log |

### Sessions 4-5 — Discord

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Gateway WS client: identify/heartbeat/ack/resume/dispatch (the four events), intents per Design, clean reconnect with session resume | apps repo: create `discord-channel/{app.json,gateway.py,settings.py}` | fake-WS tests: heartbeat cadence, resume after drop, dispatch routing |
| T4.2 | REST client + delivery (sends/edits/uploads/buttons; 429-bucket respect), streaming edits, `request_approval` buttons | `discord-channel/{api.py,delivery.py}` | fake-API tests incl. bucket backoff; approval round-trip |
| T4.3 | Transport + trust (DM pairing; guild channels tracked-only; fencing), capabilities honest | `discord-channel/transport.py` | conformance kit passes |
| T4.4 | Setup/doctor contributions (bot token, application id; probe = gateway hello) | `discord-channel/cli_setup.py`, `cli_doctor.py` | setup configures end to end |
| V4 | Validation on a real test server (owner task 3): DM pairing, channel tracking, approval buttons, streaming, the community-server dogfood checklist | — | recorded |

### Session 6 — Email

| ID | Task | Files | Done when |
|---|---|---|---|
| T6.1 | IMAP poll transport (executor-threaded, UID-tracked, 60s), address-allowlist trust + code-in-reply pairing, HTML→text via core path | apps repo: create `email-channel/{app.json,transport.py,settings.py}` | fake-IMAP tests: new-mail detection, UID persistence, pairing reply |
| T6.2 | SMTP delivery (threaded), Message-ID threading → session_map, no-streaming capabilities, digest-target registration note for plan 42 | `email-channel/delivery.py` | fake-SMTP tests: headers correct (In-Reply-To/References), thread continuity across three messages |
| T6.3 | Setup/doctor (IMAP/SMTP hosts + app-password guidance for Gmail/Fastmail; probe = login+select) | `email-channel/cli_setup.py`, `cli_doctor.py` | setup configures end to end |
| V6 | Validation with a real mailbox (owner task 4): email in → session reply out threads correctly; pairing from an unknown address; digest lands once plan 42 S5 exists (else note deferred) | — | recorded |

### Sessions 7-8 — Ramp

| ID | Task | Files | Done when |
|---|---|---|---|
| T7.1 | Conformance kit per Design (importable from apps' tests); wire into slack/telegram/discord/email test suites | core: `tests/channel_conformance.py` (+ export path decision recorded), 4 app test files | all four apps pass the kit in apps-repo CI |
| T7.2 | `docs/guides/build-a-channel-app.md` per Design (must/should/may table for all 18 delivery methods + transport lifecycle + trust + conformance usage) | new guide | a reader can map every ABC/protocol method to an obligation level |
| T7.3 | Bounty scaffolding: issues for WhatsApp/Signal/Matrix (community tier, risk-policy paragraph, guide + kit links); `channel` template registered with ECOSYSTEM-TOOLING's scaffold (coordinate — file DISCOVERY if scaffold not landed yet) | GitHub issues, cross-plan note | issues live and labeled |
| V7 | Validation: dry-run the guide as a stranger building a "null channel" against the kit in <2h | — | timed run recorded |

## Owner tasks (real world)

1. **Telegram:** create the bot via **@BotFather** (`/newbot` — pick name/username), copy the token into `personalclaw setup` when prompted; optionally set the bot's privacy mode per the guide. ~5 min.
2. **Telegram validation (V2):** your phone, ~30 min driving the walkthrough.
3. **Discord:** create an application + bot at discord.com/developers (enable *message content* intent), create a private test server, invite the bot with the scopes the setup step prints; later add it to the community server. ~15 min.
4. **Email:** dedicate a mailbox (fresh address recommended over your personal inbox), create an app password (Gmail/Fastmail flow per guide), run setup. ~10 min.
5. **Approve the channel risk-policy paragraph** (official-APIs-only for first-party; community tier for unofficial) before T7.3 publishes it.

## Risks & open questions

- **Discord gateway maintenance** is the highest-complexity piece (WS lifecycle); contained by the minimal-intents client + conformance kit. If it exceeds budget, ship Telegram+email first (owner's "few core channels" is satisfied) and let Discord ride a community bounty with the half-built client as a head start — E6 decision point, flagged early.
- **Telegram MarkdownV2** and **Discord rate buckets** are the two classic correctness traps — both have dedicated table-driven tests by design.
- **Open:** whether pairing prompts should also appear in channel (canned reply) when `dm_policy=owner_only` — default: no reply at all (silent), documented.
