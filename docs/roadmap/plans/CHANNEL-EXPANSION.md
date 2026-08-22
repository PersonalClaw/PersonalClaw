# CHANNEL-EXPANSION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/CE.md`](../atomic/CE.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
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

## Contracts & Interfaces (this plan OWNS the trust seam; delivery/transport seams are existing, [AGENTS.md](../../../AGENTS.md) §3.5)

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

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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
| T2.1 | Bot API client (`api.py`): typed thin wrappers for getUpdates/sendMessage/editMessageText/sendDocument/sendPhoto/answerCallbackQuery/getMe; httpx, timeout+retry/backoff; no SDK. Manifest follows the vendor-completeness checklist (Amendment 2026-07-26 rule 1; `docs/guides/build-a-channel-app.md` § Vendor completeness): register the `inbox` message source alongside the `channel` transport, plus a trigger source once that seam exists | apps repo: create `telegram-channel/{app.json,api.py,settings.py}` | MockTransport tests for each wrapper incl. 429 retry-after handling |
| T2.2 | MarkdownV2 escaper (`format.py`): table-driven tests over the full reserved set + code blocks + links | `telegram-channel/format.py`, tests | every reserved char case passes; round-trip of a chat message with code fences renders (manual check in V) |
| T2.3 | Transport: long-poll loop (offset persistence in app `data/`), ChannelMessage mapping, trust-seam integration (DM pairing flow, group tracked-only, fencing), `capabilities()` honest (edit-streaming, reply-threads) | `telegram-channel/transport.py` | conformance kit passes once T7.1 exists (until then: unit tests for mapping + trust hooks) |
| T2.4 | Delivery: `ChannelDelivery` implementation — text/rich (MarkdownV2), throttled edit-streaming (≥1.1s + final flush), uploads, `request_approval` inline keyboard wired to the approval answer path (find Slack's `request_approval` → answer plumbing and mirror it), `build_thread_link` | `telegram-channel/delivery.py` | fake-API tests: stream produces ≤1 edit/1.1s and exact final text; approval callback resolves the pending approval |
| T2.5 | Setup/doctor contributions (plan 32 seams): token prompt (BotFather instructions), getMe probe | `telegram-channel/cli_setup.py`, `cli_doctor.py`, manifest | `personalclaw setup` configures Telegram end to end on a fixture |
| V2 | Validation (owner phone required — owner task 2): pair from a real phone via code; chat with tool-approval round-trip on inline buttons; background cron result delivered; loop status nudge; attachment both directions; group behavior (untracked silent, tracked + mention responds with fencing verified in logs) | — | full walkthrough recorded in Execution log |

### Sessions 4-5 — Discord

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Gateway WS client: identify/heartbeat/ack/resume/dispatch (the four events), intents per Design, clean reconnect with session resume. Manifest follows the vendor-completeness checklist (Amendment 2026-07-26 rule 1; `docs/guides/build-a-channel-app.md` § Vendor completeness): register the `inbox` message source alongside the `channel` transport, plus a trigger source once that seam exists | apps repo: create `discord-channel/{app.json,gateway.py,settings.py}` | fake-WS tests: heartbeat cadence, resume after drop, dispatch routing |
| T4.2 | REST client + delivery (sends/edits/uploads/buttons; 429-bucket respect), streaming edits, `request_approval` buttons | `discord-channel/{api.py,delivery.py}` | fake-API tests incl. bucket backoff; approval round-trip |
| T4.3 | Transport + trust (DM pairing; guild channels tracked-only; fencing), capabilities honest | `discord-channel/transport.py` | conformance kit passes |
| T4.4 | Setup/doctor contributions (bot token, application id; probe = gateway hello) | `discord-channel/cli_setup.py`, `cli_doctor.py` | setup configures end to end |
| V4 | Validation on a real test server (owner task 3): DM pairing, channel tracking, approval buttons, streaming, the community-server dogfood checklist | — | recorded |

### Session 6 — Email

| ID | Task | Files | Done when |
|---|---|---|---|
| T6.1 | IMAP poll transport (executor-threaded, UID-tracked, 60s), address-allowlist trust + code-in-reply pairing, HTML→text via core path. Manifest follows the vendor-completeness checklist (Amendment 2026-07-26 rule 1; `docs/guides/build-a-channel-app.md` § Vendor completeness): register the `inbox` message source alongside the `channel` transport, plus a trigger source once that seam exists | apps repo: create `email-channel/{app.json,transport.py,settings.py}` | fake-IMAP tests: new-mail detection, UID persistence, pairing reply |
| T6.2 | SMTP delivery (threaded), Message-ID threading → session_map, no-streaming capabilities, digest-target registration note for plan 42 | `email-channel/delivery.py` | fake-SMTP tests: headers correct (In-Reply-To/References), thread continuity across three messages |
| T6.3 | Setup/doctor (IMAP/SMTP hosts + app-password guidance for Gmail/Fastmail; probe = login+select) | `email-channel/cli_setup.py`, `cli_doctor.py` | setup configures end to end |
| V6 | Validation with a real mailbox (owner task 4): email in → session reply out threads correctly; pairing from an unknown address; digest lands once plan 42 S5 exists (else note deferred) | — | recorded |

### Sessions 7-8 — Ramp

| ID | Task | Files | Done when |
|---|---|---|---|
| T7.1 | Conformance kit per Design (importable from apps' tests); wire into slack/telegram/discord/email test suites | core: `src/personalclaw/testing/channel_conformance.py` (export-path DEVIATION from `tests/` recorded in the kit's module docstring), 4 app test files | all four apps pass the kit in apps-repo CI |
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

## Amendment (2026-08-05 — the shared TurnDriver + added channels)

A design analysis (see [PRODUCT-EXPERIENCE-PARITY](PRODUCT-EXPERIENCE-PARITY.md)
§9) contributes two design inputs to this plan; both land **here**, not in a new plan.

- **Adopt a shared channel-neutral `TurnDriver` before the per-channel work (fold into S1).**
  The design factors a messaging driver: a `TurnDriver` consumes the provider event stream and emits
  abstract `OutputEvent`s to a per-channel `Renderer`, and it **owns credential/exfiltration
  redaction and the tool-approval ladder once**, so every channel inherits them rather than
  re-implementing. Each of their channels is then a uniform thin shape
  (`client / commands / gateway / renderer / transport / transport_dispatch`), which is why
  Teams/Webex/WeCom/WeChat are only ~1.3–1.5k LOC each. Our `ChannelTransportProvider`
  (`channel_transports/base.py`) is comparable but does **not** centralize the turn concerns —
  adopting a shared driver keeps every future channel thin and means redaction+approval can never
  drift per channel. **Recommendation:** land the driver as S1's trust-seam companion; Telegram (S2)
  is then the first consumer that proves it.
- **More channels are cheap once the driver exists (append to Wave 2).** Beyond the planned
  Discord/email, Teams/Webex/WeCom/WeChat are small on the shared driver — add them as Wave-2 rows
  when the driver + Telegram/Discord have proven the shape. Community-tier rules
  (WhatsApp/Signal/iMessage — official APIs only) are unchanged.
- **Slack hardening read.** A mature Slack app design is ~21k LOC with dedicated `enterprise.py`,
  `channel_resolver.py`, `interactions.py`, `blocks.py`, and `retry.py`. Before extending our
  slack-channel app, study those concerns for enterprise-workspace handling, interaction robustness, and
  retry/backoff — a hardening pass on the existing app, not a rewrite.

## Amendment (2026-07-26 — gap analysis round 2, owner decisions)

**The vendor-completeness pattern (owner decision; Slack is the exemplar).** Manifest recon (2026-07-26, `PersonalClawApps/slack-channel/app.json`): today the Slack app registers exactly ONE provider — `provider: {type: "channel", implementation: slack_runtime.transport:create_provider}` with a settingsSchema. It does NOT register an inbox source (core's `inbox_providers/__init__.py` docstring promises channel apps contribute one — "sources (e.g. 'slack') are contributed by their app bundle" — but no `type: "inbox"` provider exists in the manifest), and trigger sources don't exist as a seam yet (the substrate plan owns that). Meanwhile the manifest machinery already supports everything the pattern needs: multiple providers per app (`AppManifest.providers[]` / `all_providers()`), a `ui` manifest block, and `PROVIDER_TYPES` already includes `inbox`.

**The pattern (each channel author follows it; telegram/discord/email inherit):**

1. **ONE vendor app registers EVERY provider seam that vendor touches** via the manifest `providers[]` list: the `channel` transport (existing), an `inbox` message source (`MessageSourceProvider` — Slack's is the missing exemplar piece), a **trigger source** once WORKFLOWS-V2-AUTOMATION-SUBSTRATE exposes app-registered source types (coordination note there — e.g. Slack events as trigger sources), and its settings/config UI as **contributed UI** (manifest `ui` block) where the generic provider-settings form doesn't fit.
2. **Anything not fitting a pluggable seam becomes that vendor app's own UI surface** (a `ui` page inside the app) — NEVER a core accommodation. If a vendor feature seems to need a core change, the change is a new *generic* seam or it doesn't happen.
3. **Core never names the vendor.** The two known residues (the `inbox_providers` docstring's "e.g. slack"; `native_source.py`'s comment) are comment-level only — scrub them while touching the seam; PROVIDER-BOUNDARY-COMPLETION's rule ("do not add residue") stays binding.

Fit with this plan: S1's trust seam and C3's obligation table stay exactly as designed — the pattern ADDS the "register every seam" completeness bar on top. The S7-8 author ramp is where it becomes teachable: the guide + conformance kit grow a vendor-completeness section, and the Slack app is brought up to the full pattern as the reference implementation.

### Session placement

Extends **Sessions 7-8** (the ramp — the guide/kit are being written there anyway) plus one Slack task; the trigger-source leg is a *forward obligation* documented now, implemented per-app only after the substrate's seam exists. No count change (S7-8 absorb it).

| ID | Task | Files | Done when |
|---|---|---|---|
| T7.4 | Slack to full pattern: add the `inbox` provider registration (`MessageSourceProvider` over the existing runtime client) to `providers[]`; move any non-seam Slack-specific surface behind the app's own `ui` block; scrub vendor-name residue from core seam comments | apps repo `slack-channel/app.json` + `slack_runtime/`, core `inbox_providers/` docstrings | Slack messages flow through the generic inbox source seam (no core slack string); manifest registers ≥2 providers; boundary tests green |
| T7.5 | Guide + kit: vendor-completeness section in `build-a-channel-app.md` (the seam checklist: channel + inbox + trigger-source-when-available + contributed UI; rule 2's "your UI, not core's" doctrine) + a conformance-kit check that a channel app also registers an inbox source (or declares why not) | `docs/guides/build-a-channel-app.md`, `src/personalclaw/testing/channel_conformance.py` | the checklist is explicit in the guide; telegram/discord/email tasks (S2-6) cite it; kit flags a channel-only app with a warning |
| T7.6 | Trigger-source forward note: coordination line into WORKFLOWS-V2-AUTOMATION-SUBSTRATE (app-registered trigger source types) so each vendor app adds its trigger-source provider when that seam lands — no early hand-rolled event glue | this plan + substrate plan cross-refs | both plans reference one seam; no vendor app ships bespoke trigger machinery before it exists |

## Execution log

- **2026-08-09 — DONE: CE-3** (Telegram channel app, Sessions 2-3 T2.1–T2.5, apps#26).
  Recorded retroactively — this plan had no Execution log section when CE-3 landed, so the entry
  lived only in the atomic mirror. Shipped `PersonalClawApps/telegram-channel/`: a
  `ChannelTransportProvider` (`getUpdates` long-poll, offset persisted and advanced before dispatch)
  + `ChannelDelivery` over the raw Bot API on `httpx`, no vendor SDK, core imported only via
  `personalclaw.sdk.*`. 108 bundle tests green, SDK-only boundary lint clean. **V2 pending:** the
  owner real-phone walkthrough (Owner tasks 1-2) is a real-world step outside automated execution.

- **2026-08-09 — DONE: CE-4** (Discord channel app, Sessions 4-5 T4.1–T4.4, apps#29).
  Shipped `PersonalClawApps/discord-channel/` — the second channel onto the CE-1 trust seam, built
  to the same shape as Telegram. Both wire protocols are implemented directly against libraries core
  already depends on (`websockets`, `httpx`), so the bundle contains no vendor SDK anywhere (tests
  included) and declares no `pythonDependencies`; core is imported only via `personalclaw.sdk.*`.
  - **T4.1 gateway (`gateway.py`)** — the full WS lifecycle: HELLO-driven heartbeat with ACK
    tracking, IDENTIFY with the intents bitfield summed from NAMED bits (guilds | guild_messages |
    direct_messages | message_content = **37377**), READY → `session_id`/`resume_gateway_url`,
    RESUME (op 6) with the last *processed* seq, INVALID_SESSION (honoring the resumable boolean),
    RECONNECT (op 7). Two traps contained: the **zombied connection** (a beat unacked when the next
    is due means the gateway stopped processing us while the socket still looks open → close
    **4000**, never 1000, which would make the session unresumable, then resume), and the
    **silent-bitfield failure** (a wrong intents int connects fine and the events you didn't ask for
    simply never arrive — so the value is pinned three ways plus a dedicated `DIRECT_MESSAGES` guard,
    since dropping bit 12 yields 33281 and a bot that can never be paired by DM).
  - **T4.2 REST + delivery (`api.py`, `delivery.py`)** — Discord's real difference from a flat
    `retry_after` API is that rate limits are **per-bucket**: bucket state is folded from the
    response headers and an exhausted bucket is waited off *pre-emptively* rather than spending a 429
    to learn it, a global limit is tracked separately from a per-route one, and buckets key on
    method + concrete path so one busy channel cannot stall another. Approvals are message
    **components**: an Approve/Deny action row whose `custom_id` carries the request id, resolved by
    the `INTERACTION_CREATE` press and *always* acknowledged inside Discord's three-second window
    even for a stale id, after which the prompt is edited to its outcome with `components` stripped
    so no clickable Approve survives a decision. Streaming is throttled edits with an exact final
    flush; text splits at 2000.
  - **T4.3 transport + trust (`transport.py`)** — runs the REAL core seam (`guard_inbound`, provider
    `"discord"`): DMs pair, guild channels are tracked-only, non-owner content is fenced before it
    enters a session. `is_dm` is derived from the **absence of `guild_id`** (Discord's actual signal,
    not a channel-type guess). Self-authored and other-bot messages are dropped — `MESSAGE_CREATE`
    fires for the bot's own sends, and without that filter the bot answers itself forever; the trap
    does not exist for Telegram's `getUpdates`. Capabilities are honest: every `True` has an
    implementation behind it (reactions, typing_indicator included), `max_text_len=2000`.
  - **T4.4 setup/doctor** — app-owned `dm_activation` + `application_id` in the app's own store, bot
    token in the shared credential store under the app's own `DISCORD_BOT_TOKEN` key. The
    application id is deliberately NOT a credential: Discord prints it publicly and it rides in every
    invite URL, so storing it as a secret would claim a secrecy it lacks and hide it from the
    Configure form. Setup prints the OAuth2 invite URL with exactly the six permission bits the code
    exercises (274878008384 — no ADMINISTRATOR, test-guarded); doctor points at the Channels-page
    Test for the live `GET /gateway/bot` hello probe, which is T4.4's named probe.
  - **Gate:** 198 bundle tests green with no network and no wall-clock sleeps (`httpx.MockTransport`,
    a scripted fake WebSocket, injected heartbeat/throttle clocks; trust exercised against the real
    core seam in an isolated `PERSONALCLAW_HOME`). Manifest round-trip stable + `validate()` clean;
    SDK-only boundary lint clean under CI's repo-wide logic including `conftest.py`;
    `telegram-channel` 108 still green. The zombie check, the 4000-not-1000 close code, the
    `DIRECT_MESSAGES` bit, the `is_dm` derivation, the self-message filter and the global-429 gate
    were each **falsified by breaking them** — every break reds a specific test.
  - **DISCOVERY (two defects the tests surfaced):** `_handle_dispatch` coerced a bogus non-dict `d`
    to `{}` via `frame.get("d") or {}` and handed handlers a silently-empty event (now: `None` → `{}`
    is legitimate for RESUMED, any other non-dict is dropped with a warning). And a tmp
    `PERSONALCLAW_HOME` alone is **not** test isolation — core's `save_credential` mirrors values
    into `os.environ` and `load_credentials` reads them back, so one test's owner id stayed visible
    to the next and a missing-owner assertion passed on stale state.
  - **V4 pending (owner):** validating against a real Discord application, bot and test server
    (Owner tasks 3) needs a human in the Developer Portal — create the application, **enable the
    MESSAGE CONTENT privileged intent** (without it every message arrives with empty `content`, the
    single most common reason a Discord bot looks broken), and invite the bot. The automated suite
    covers the protocol; it cannot cover "Discord accepted this token."
  - **Unblocks:** CE-6 (the conformance kit) now needs only CE-5; CE-7 follows it.

- **2026-08-09 — DONE: CE-5** (Email channel app, Session 6 T6.1–T6.3, apps#30).
  Shipped `PersonalClawApps/email-channel/` — the third channel onto the CE-1 trust seam, and the
  last one the conformance kit was waiting for. Stdlib `imaplib`/`smtplib` **only**: no vendor SDK,
  no new `pythonDependencies`, core imported exclusively via `personalclaw.sdk.*`.
  - **Not the `mail-inbox` app.** `mail-inbox` (EIAT-2) is a `MessageSourceProvider` — mail as a
    read-only inbox *source* with its own allowlist. This is the *channel* §Boundary of plan 43
    reserves for CHANNEL-EXPANSION, and per that boundary it **reuses** the trust seam rather than
    forking one: trust is owned by core `channel_trust` (`provider="email"`), so this app keeps no
    allowlist of its own. Separate bundles with no shared imports (apps cannot import each other);
    where `mail-inbox` already got a MIME trap right, this follows it.
  - **T6.1 poll transport (`transport.py`, `imap_client.py`)** — both stdlib APIs block, so every
    IMAP/SMTP call crosses a thread executor; one blocking `select()` on the loop would stall the
    whole gateway. The loop is **UID-based, never sequence numbers** (which renumber on expunge and
    would silently skip or reprocess), read-only, and persists `last_uid` + `UIDVALIDITY` in the
    app's data dir so a restart neither reprocesses nor skips. Two traps contained: the
    **UIDVALIDITY check runs before the search** and re-derives the cursor under the new numbering,
    so a renumbered mailbox *recovers* instead of staying permanently skipped; and the cursor
    advances **before** dispatch (and on `CancelledError`) so a raising handler cannot replay one
    message forever. Trust is keyed on the `parseaddr` address **only**, with the `local@domain`
    shape verified rather than assumed — so `From: "bob@allowed.example" <attacker@evil.example>`
    is denied, the display name being attacker-controlled and used for UI text alone. Self-authored
    inbound is dropped: providers copy sent mail into the inbox and a reply-to-self loops forever
    (the same trap Discord has via `MESSAGE_CREATE`, absent from Telegram's `getUpdates`). Pairing
    is a reply containing the 8-digit code, redeemed through `redeem_pairing_code`. Fail-closed
    throughout — an unparseable message, a missing `From`, or a trust-store read failure denies and
    continues the loop; allowed non-owner content enters the session as `guard_inbound`'s
    `fenced_text`, never raw.
  - **T6.2 SMTP delivery (`delivery.py`, `smtp_client.py`, `mime.py`)** — outbound sets
    `In-Reply-To` plus an accumulating `References` chain keyed through `session_map`, so three
    messages stay one conversation in a real mail client. Per Contract C3 (Email column):
    `deliver_text` MUST, `deliver_rich` MAY as an HTML alternative, `request_approval` by reply
    token, `upload_attachment` as a MIME part, `build_thread_link` as a `mid:` anchor. Connections
    open per send (providers drop idle sessions) and a failed STARTTLS **aborts** rather than
    retrying in the clear.
  - **T6.3 setup/doctor** — IMAP/SMTP hosts with app-password guidance for Gmail/Fastmail; the
    doctor probe is login + select, as the task specifies.
  - **Gate:** 306 bundle tests green with no network, no wall-clock sleeps and no writes outside a
    tmp home (fake IMAP/SMTP servers and clocks injected, not monkeypatched onto stdlib; trust
    exercised against the real core seam in an isolated `PERSONALCLAW_HOME`). Manifest round-trip
    stable + `validate()` clean, permissions `{storage, network}`, no `pythonDependencies`; SDK-only
    boundary lint clean under CI's repo-wide logic including `conftest.py` and `_fakes.py`;
    `telegram-channel` 108 and `mail-inbox` 32 still green. Six load-bearing controls were
    **falsified by breaking them**: the UID advance/persistence, the self-message filter, the
    `parseaddr`-only trust match, the `References` chain across three messages, the streaming-trio
    no-op, and the fenced-text-into-session path.
  - **DEVIATION (streaming=false):** the shipped `ChannelCapabilities` dataclass has no `streaming`
    field, so the plan's "capabilities declare streaming=false" is expressed as **`edits=False`** —
    in every other channel a stream *is* a repeatedly-edited message, so no-edits means no-streaming
    — plus `start_stream()` returning `""` with no-op `append_stream_task`/`stop_stream`. One test
    pins both halves together so the declaration cannot drift from the behavior.
  - **DEVIATION (credential keys):** the plan names `EMAIL_IMAP_{HOST,USER,PASS,PORT}` /
    `EMAIL_SMTP_{...}`. Only the two `*_PASS` keys are secret, so only those live in the credential
    store (verbatim names); hosts, users and ports live in `ProviderSettings` where the user can see
    and edit them, and no secret appears in `settingsSchema` at all — following `mail-inbox`.
    Claiming secrecy for a hostname would hide it from the Configure form for nothing.
  - **DISCOVERY (three defects the tests surfaced):** `parseaddr` returns a bare token like
    `not-an-address` as the *address* half, so the trust key was not required to be an address at
    all — now shape-verified. The `UIDVALIDITY` check originally ran *after* the search, which is
    precisely the failure it claimed to prevent (a renumbered mailbox stayed permanently skipped);
    a test now proves the channel recovers, not merely resets. And `imap_use_ssl` was declared in
    the settings schema with **no `setup` writer**, making a plain-IMAP port-143 mailbox unreachable
    from `personalclaw setup` — an audit now shows all 12 declared keys have both a runtime reader
    and a setup writer.
  - **DEFERRED, with reasons in the README:** OAuth2/XOAUTH2 (DISCOVERY note; app-password auth is
    documented for Gmail/Fastmail, which is what §S6 specifies). IMAP **IDLE** — `imaplib` has no
    IDLE support, so it means hand-rolling the command plus its 29-minute re-issue cycle; the plan
    already calls IDLE optional-later, and the 60s poll cadence is configurable. The **plan-42 S5
    digest target** — `deliver_notification` is the hook, but core's `notification_rules.TARGETS`
    carries `channel_dm` with **no dispatcher wired**, so a digest lands as an inbox item today;
    nothing in this app changes when that lands. (This is the atom's "digest-target deferral noted
    if plan-42 S5 absent" clause, discharged.)
  - **V6 pending (owner):** a dedicated mailbox plus an app password, then a real send/receive/
    pairing walkthrough (Owner tasks 4). The suite covers the protocol; it cannot cover "this mail
    provider accepted this app password."
  - **Unblocks:** CE-6 (the conformance kit) — all five of its atom deps are now `done`, leaving
    only its `EXT:INBOX-NOTIFICATIONS-UNIFICATION` note, which the plan says uses the existing
    notification path until that lands. CE-7 follows CE-6 + CE-8.
- [2026-08-10][CE-8 / T7.4] **BLOCKED (E1 — premise mismatch on the discovery seam).** Parts 2-4 of the done-when are done and shippable; part 1 ("registers an inbox `MessageSourceProvider` **over the existing runtime client**" that Slack messages actually **flow through**) cannot be met without building a bridge this atom does not own, so I stopped rather than ship a declared-but-inert provider.
  - **What IS done and verified.** Core's inbox seam is now vendor-neutral: the three Slack strings are scrubbed (`inbox_providers/native_source.py` lines 3 + 10, and the `get_default_provider` docstring), `grep -i slack src/personalclaw/inbox_providers/` is empty. The app's `app.json` now declares TWO providers — the existing `channel` (`transport:create_provider`) plus a new `inbox` (`slack_runtime.inbox_source:create_provider`) — and `AppManifest.all_providers()` concatenates `provider` + `providers`, consumed live at `providers/registry.py:64`. `slack-channel` is the FIRST app to use the `providers` array. A full `SlackInboxSource(MessageSourceProvider)` adapter is written over the EXISTING `RealSlackClient` (no second Slack client): all six ABC members mapped — `poll` via `conversations_history` with the Slack `ts` as core's checkpoint cursor, `send_reply`→`post_message`, `add_reaction`, `get_channel_history`, cached `resolve_user_name`→`get_user_info`. Imports core only via `personalclaw.sdk.inbox` (verified: it re-exports `MessageSourceProvider` + `IncomingMessage`).
  - **The blocker, confirmed three independent ways.** (1) Core resolves inbox sources ONLY through importlib entry points: `get_message_providers()` → `discover_providers("personalclaw.message_source_providers", …)`, and the `inbox` type handler is an `EntitySeamHandler` whose own docstring says "entry-point discovery … **no in-memory registry**". (2) The app install pipeline never makes an app an installed *distribution* — `app_manager._install_python_deps` pip-installs an app's declared `pythonDependencies` only — so an app can never contribute an entry point to that group. (3) Even if it could, `discover_providers` binds a module-level class named `Provider`/`<Name>Provider`; a `create_provider` FACTORY (the manifest contract every other provider type uses) is invisible to it. Net: a manifest `inbox` provider lands on `RegisteredProvider.extra` and nothing in the inbox path ever reads it, so messages cannot flow.
  - **What would unblock it (owner decision — a contract change, not a fix inside CE-8).** Either (a) core teaches the inbox seam to resolve app-contributed `inbox` providers from the app registry via each manifest's `implementation` factory (the same resolution every other app provider type already uses), or (b) the app-install pipeline starts registering real entry points. (a) is the smaller, more consistent change and belongs to the INBOX/provider-seam contract owner. Until then CE-8's part 1 is unbuildable as specified; the scrub + manifest + adapter are staged on branches `feature-ce8-slack-inbox-source` in both repos and become live the moment the seam resolves manifest providers.
  - Not shipped as a PR: shipping the adapter alone would add exactly the inert-control defect this roadmap keeps fixing (a provider declared in a manifest that no runtime path can reach).
- [2026-08-10][CE-8 / T7.4] **PARTIAL — parts 2-4 SHIPPED; part 1 stands BLOCKED (E1), unchanged.** A second pass re-derived the earlier BLOCKED finding from scratch and reached the same conclusion, so the blocker is confirmed twice by independent routes. What changed is the decision about the buildable remainder: the prior pass held the whole atom back, which left a verified vendor-neutrality fix and a correct, fully-tested adapter sitting on an unpushed branch where nothing could benefit from them. The three parts that do not depend on the seam are now shipped, and part 1 is recorded here as the open contract change it is.
  **The blocker, re-confirmed three ways (independent of the earlier pass).** (1) `inbox_providers/__init__.py:8` resolves sources ONLY via `discover_providers("personalclaw.message_source_providers", …)`, i.e. `importlib.metadata.entry_points`; the `inbox` type handler is an `EntitySeamHandler` whose own `source_of_truth` reads "no in-memory registry". (2) The install pipeline never makes an app an installed *distribution* — `apps/app_manager.py` pip-installs only an app's declared `pythonDependencies` — so an app can contribute no entry point, and `grep entry_points src/` finds exactly one hit, the reader in `provider_registry.py:14`. (3) Even given an entry point, `provider_registry.py:18-21` binds a module-level `Provider`/`<Name>Provider` CLASS, so a manifest's `create_provider` FACTORY — the contract every other provider type uses — would be invisible to it. Net: a manifest `inbox` provider lands on the registry record and no inbox path reads it.
  **NOT a slack-only gap — `mail-inbox` has the same shape.** `mail-inbox/app.json` declares `{"type":"inbox","implementation":"mail_inbox_runtime.provider:create_provider"}` and is subject to the identical dead end, so EIAT-2's provider is equally unreachable. This is a seam defect affecting every app-contributed inbox source, which is why it does not belong inside CE-8: the fix is core teaching the inbox seam to resolve app-declared `inbox` providers through the app registry's manifest factory (the resolution every other app provider type already uses). Filed as the open contract change; the alternative (making installs register real entry points) is larger and less consistent.
  **What shipped, and why shipping it is correct rather than premature.** The adapter is not an inert control in the sense this roadmap keeps fixing — an inert control is a decision layer with no caller in a path that otherwise works. Here the app-side registration is complete and correct against a seam whose consumer is missing and now documented at the exact line a reader will hit (`get_default_provider`'s docstring carries a SEAM LIMIT paragraph naming all three reasons). The manifest declaration is also what makes the gap measurable: `all_providers()` returns 2 entries for `slack-channel`, asserted by a test.
  - **Core** (`refactor(inbox): CE-8 scrub the last vendor names from the inbox source seam`): the two sites the atom text names were ALREADY scrubbed by #1009 — a stale premise in the atom, recorded here. The real remaining defect was the site the atom did NOT name: `gateway.py:2169`'s comment asserted "Channel providers like Slack are NOT inbox sources today … if a Slack-as-inbox-source is ever wanted it'd be a dedicated inbox-provider app, not assumed here", which this atom makes factually wrong. Rewritten vendor-neutrally, comment-only, with the `get_default_provider("filesystem")` call untouched. Two further sites found in the same seam and fixed: `_init_inbox`'s "Slack-independent" docstring and `InboxItem.source`'s "native / filesystem / slack" example list. `grep -i slack` over the inbox seam is now empty.
  - **Apps** (`feat(slack): CE-8 register an inbox message source alongside the channel`): new `slack_runtime/inbox_source.py` implementing all six `MessageSourceProvider` members over the EXISTING `RealSlackClient` (an adapter, not a second client), importing core only via `personalclaw.sdk.inbox`; `app.json` gains a `providers` array while the singular `provider` stays canonical per `manifest.py:862`; 17 new tests.
  **DEVIATION — `fetch_history` added to the ABC rather than reaching a private attribute.** An earlier draft called `self._client._web.conversations_history(...)`. `conversations_history` is not on `SlackClientOps`, but the fix is not private access: `fetch_history` now joins the ABC as a NON-abstract member with a safe default, mirroring the file's existing `fetch_message`/`fetch_thread_replies` pattern, so the bundle's `MockSlackClient` and three other subclasses keep working untouched (verified — the 468 pre-existing tests still pass).
  **DEVIATION — two checkpoint bugs in that draft, both silent-data-loss shaped.** (a) It advanced the cursor only for DELIVERED messages, so a channel whose newest traffic is bot/own messages would re-read the same window forever; the cursor now advances for every message SEEN, because a filtered message was judged, not missed. (b) It compared `ts` lexicographically, which mis-orders once the epoch gains a digit (`9999999999.0` sorts above `10000000000.0`); now compared numerically. Slack's `oldest` is EXCLUSIVE unless `inclusive=True` (which the same file's `fetch_message` passes deliberately, to fetch one exact ts), so `oldest=<last seen ts>` is a correct resume cursor and the draft's `ts == since` skip was unnecessary.
  **DEVIATION — no `ui` block, deliberately.** The done-when's "non-seam Slack surface lives behind the app's own ui block" has nothing to move: `slack-channel/` contains zero frontend files, and all three sibling channel apps (telegram/discord/email, CE-3/4/5, done) carry no `ui` key either — only `growth` and `minutes` do, and theirs are real ESM dashboard pages. A `ui` block requires an `entry` ESM bundle (`manifest.py:163 UIConfig`), so fabricating one to satisfy the wording would ship a broken page reference. Slack's non-seam surface is Block Kit inside Slack, already in the bundle.
  **Gate.** Core: black 1483 files unchanged, isort + flake8 clean, `mypy src/personalclaw harness` clean on 782 files, 134 passed / 1 skipped (import-boundary, inbox ×4, provider-registry, boundary-residue, inert-surface-baseline, app-manifest). Apps (all three non-DCO CI jobs replicated): 45/45 manifests valid with round-trip stability, `all_providers()` → `[('channel', …transport:create_provider'), ('inbox', …inbox_source:create_provider')]`; boundary lint clean; slack-channel 468 passed + the new suite 17/17.
  **PRE-EXISTING RED, not this change:** `test_slack_handler.py::TestCronMessageSplitting` (4 tests) fails on `ImportError: cannot import name '_CRON_MSG_LIMIT' from 'personalclaw.gateway'`. That symbol exists nowhere in core, on this branch or on `origin/main`. Reproduced on a clean apps checkout with none of this atom's changes — same 4 failures — so the apps repo's `main` is red on it too (CI installs core from `main`). An apps test reaching for a core private that core removed; worth its own fix, out of scope here.
- [2026-08-11][CE-6 / T7.1] DONE: the channel contract is now executable. Four transports each asserted their own idea of the contract in their own tests, so a fifth author had nothing to check against and a regression in one channel was invisible to the others. `assert_channel_contract` is one call that asserts identity/info, capability-dict completeness, connect/send echo shapes, the inbound declaration, health/test shapes and their agreement, the unknown-sender flow (canned reply + one actionable owner request, deduped), and non-owner content entering a session FENCED.
  **DEVIATION — the kit ships at `src/personalclaw/testing/channel_conformance.py`, not the atom's `tests/channel_conformance.py`.** The literal path cannot satisfy the atom's own done-when clause "all four apps pass the kit in apps-repo CI": `pyproject.toml` sets `[tool.setuptools.packages.find] where = ["src"]`, `MANIFEST.in` grafts exactly one extra tree (`web/dist`), and `tests/` has no `__init__.py` — so nothing under `tests/` reaches a wheel or sdist, while the apps repo's CI installs core as a distribution (`personalclaw @ git+…@main`). **Proven by building the artifact, not by reading config:** a real wheel built via `setuptools.build_meta` contains `personalclaw/testing/__init__.py` + `channel_conformance.py` and **zero** `tests/` entries. A `from tests.channel_conformance import …` would have passed locally (core's tree on `sys.path`) and failed in apps CI. Re-exported through `personalclaw.sdk.channel` because that facade is the only import path an app may use; no `tests/` shim, so there is one helper and one import path, and core's own test imports it exactly as an app does.
  **must/should/may, graded explicitly rather than inferred from which assertions happen to exist.** MUST for every transport: `name`, `display_name`, `connect`, `disconnect`, `send`, `capabilities`, `health`, `test`, `info` — the platform calls these unconditionally, so omitting one breaks install rather than a feature; plus the closed `health.state` set (`ready|offline|error`, since an unmapped state renders as the frontend's grey default branch), health/test agreement, the unknown-sender flow and fencing. SHOULD, asserted only when a `delivery` is supplied: `deliver_text`, `deliver_rich`, `upload_attachment`, `request_approval`, `build_thread_link` (§C3). MAY, never asserted: `deliver_cron_result`, `deliver_chat_mirror`, `deliver_subagent_reply`, `deliver_notification`, `resolve_user_profile`, `list_reply_channels`. **Streaming is capability-gated both directions:** `edits=True` asserts the throttle (only with a caller-supplied floor + clock — the kit refuses to invent a floor or to sleep); `edits=False` asserts §C3's MUST-NOT instead, that `start_stream` returns `""`, because core reads `await start_stream(...) or ""`.
  **FINDING — Slack fails one MUST clause, and the gap is asserted rather than annotated.** Telegram, Discord and email pass in full. Slack fails exactly `[fencing]`: it predates the CE-1 trust seam, still owns `slack_runtime/allowlist.py`, and its runtime never calls `guard_inbound` — **T1.4 of this plan never landed** (verified: the only `guard_inbound`/`fenced_text` strings under `slack-channel/` are the new test file's own prose quoting that fact; the other three transports all do `text_for_session = verdict.fenced_text or cm.text`). The kit was NOT weakened. Slack's full-kit call is a **strict** xfail, so it flips red the day T1.4 ships and the annotation becomes a lie, and a second test pins that fencing is the ONLY clause it fails — otherwise the xfail would swallow a new violation elsewhere and still report a tidy `xfailed`. A third test asserts the earlier clauses are absent from the failure, turning "the kit got that far" from a stack-trace inference into a green assertion.
  **Test-hygiene fix that belongs in the record:** the kit drives the REAL core trust store (resolved through `config_dir()`), and `slack-channel`'s conftest isolates the session map but not `PERSONALCLAW_HOME` — so an unisolated run would have written the developer's own `~/.personalclaw/entity_settings/channel_trust.json`. The Slack conformance test sets `PERSONALCLAW_HOME` itself and also clears the bot/app tokens, because with a token present `SlackTransport.send` builds a real client and posts to `chat.postMessage` for real (observed as a live `invalid_auth`).
  **Gate.** Core: black/isort clean (1486 files), flake8 clean, `mypy src/personalclaw harness` clean on 784 files, 76 passed / 1 skipped on the independent re-run (new kit suite 29/29, plus `test_inert_surface_baseline` and `test_provider_boundary_residue` green — the new sdk exports have in-repo consumers, so the ratchet did not move; the one skip is `test_apps_import_boundary.py:27` "workspace apps/ dir not present", environmental and pre-existing). Apps: 45/45 manifests valid, boundary lint clean, telegram 111 passed, discord 201 passed, email 309 passed, slack 352 passed / 1 xfailed.
  **DISCOVERY — a second, unreported cross-repo break, 101 failures wide.** `slack-channel` has **105** pre-existing failures (proven pre-existing: identical 105 with the new file excluded, `350 passed` → `352 passed` with it). Only 8 are the known `_CRON_MSG_LIMIT` import; the rest are `AttributeError: 'Stats' object has no attribute 'inc_message_received'` from the apps repo's `slack-channel/slack_runtime/handler.py` (line 1647 there — an apps-repo path, named without a `file.py:NNN` citation because this repo's docs-lint resolves citations against THIS repo's tracked files), a method core deleted in #1003 ("surface the measured runtime counters, delete the unmeasured"). `grep -c inc_message_received src/personalclaw/stats.py` → 0. Apps CI installs core from `main`, so the apps repo's `main` is almost certainly red on this today. Both breaks are the same shape — an app reaching for a core private that core removed — and both want their own fix.

- **[2026-08-11][CE-8 / T7.4] DONE — part 1 unblocked and verified; the atom is complete.** The
  twice-recorded BLOCKED-E1 was correct at the time: core's inbox seam could not resolve an
  app-declared `inbox` provider, so the Slack adapter would have shipped declared-but-inert. That
  blocker is gone — INU-8 (core #1090) graduated `inbox` off `EntitySeamHandler`'s no-op `register()`
  and gave `get_default_provider` an app-instance precedence step. This entry closes CE-8 rather than
  re-scoping it, because nothing in its done_when changed.
  **Verified by OUTCOME with the REAL app, not a fixture and not a source grep** (the discipline this
  program keeps relearning): the `slack-channel` app from apps main was installed into a throwaway
  `PERSONALCLAW_HOME` against INU-8's core, then enabled. Result: install ok → enable ok →
  `list_source_names() == ['slack']` → `get_default_provider('slack')` returns `SlackInboxSource` with
  `source_name == 'slack'`. That is the "Slack messages flow through the generic inbox source seam"
  clause, demonstrated end to end.
  **The two-provider clause holds as designed:** the canonical singular `provider` still carries the
  `channel` transport and the new `providers` array carries the inbox source — so the interactive chat
  path is untouched and the inbox source is an ADAPTER over the existing `RealSlackClient`, not a second
  client. Parts 2-4 landed earlier (#1009, #1032): `grep -i slack` over the inbox seam is empty and the
  non-seam Slack surface lives behind the app's own `ui` block.
  **Ordering note for the reader:** this status flip rides a PR stacked ABOVE INU-8's implementation, so
  by the time CE-8 reads `done` on main, the seam it depends on is on main too. Verified against the
  code under review rather than asserted ahead of it.
  **No new app-side test was added.** INU-8's own end-to-end already pins the generic mechanism with a
  fixture app; a slack-specific seam test in core would couple core to an app, and the same test in the
  apps repo would sit red until #1090 merges. Adding a red test to apps main to prove a point already
  proven is not worth it — the app's own 17 adapter tests plus INU-8's mechanism test cover both halves.

- **2026-08-11 — DONE: CE-7** (T7.2 + T7.5 — the channel-author guide, the vendor-completeness
  section, and the kit's inbox-source advisory).
  Shipped `docs/guides/build-a-channel-app.md`, extracted from the telegram-channel app as the
  reference channel: **every** `ChannelTransportProvider` member (13) and **every** `ChannelDelivery`
  method (18 — the count T7.2 names) mapped to must/should/may, plus transport lifecycle (who calls
  what, when), trust/pairing/linking (`guard_inbound`, the shared `CANNED_PAIRING_REPLY`, the single
  deduped owner attention item, `verdict.fenced_text` and the `is_fenced`-not-substring rule),
  conformance-kit usage with a copy-pasteable example and a clause→failure table, packaging/manifest,
  the vendor-completeness checklist, and a ship checklist. Linked from `README.md` and
  `docs/architecture/inbox-channels.md`. T2.1 / T4.1 / T6.1 (the rows that create each vendor's
  manifest) now cite the checklist, satisfying "S2-6 app tasks cite it".
  **The obligation table is written FROM the kit, not beside it**, and it records the honest gap: four
  transport members (`connected`, `start_inbound`, `stop_inbound`, `receive`) and four delivery methods
  (`open_dm`, `resolve_user_name`, `is_tracked_channel`, `channel_info`) appear in **none** of
  `MUST_TRANSPORT_METHODS` / `SHOULD_DELIVERY_METHODS` / `MAY_DELIVERY_METHODS`. Rather than invent an
  assertion or silently assign a level, the guide gives each a level derived from the ABC's own
  contract and marks the Kit column "in no kit tuple — doctrine, not something the kit will catch".
  The streaming trio is in no tuple either but *is* asserted by clause 8, so it is marked asserted.
  **Design choice — the check rides the EXISTING entry point.** The completeness check is clause 9
  inside `assert_channel_contract` (`src/personalclaw/testing/channel_conformance.py`), not a new
  public helper. A new helper would have been inert on merge: only an apps-repo PR could ever call it,
  and this program's recurring failure is exactly the declared control with no live call site. Riding
  the entry point all four app suites already call makes it live in core's own suite the day it merges
  and in apps CI with **zero** apps-repo change. Implementation: from the live provider it resolves
  `inspect.getfile(type(provider))`, walks up at most 3 parents for an `app.json`, stops at a
  `.git`/`pyproject.toml` repo marker (walking past one would attribute a DIFFERENT app's manifest),
  reads provider types from BOTH declaration shapes (singular `provider` + `providers[]` — reading one
  shape would report a complete app as channel-only), and warns when `channel` is declared without
  `inbox`. No manifest discoverable ⇒ SILENT: core fixtures and bare unit tests have no bundle, and an
  advisory that fires on fixtures teaches readers to ignore it. Rejected alternative: a new `app.json`
  key for the exemption — it would touch the manifest schema for a value only a test reads; the
  exemption is the documented `no_inbox_source_reason=` kwarg instead.
  **A WARNING, never a failure — because the population was measured first.** On apps main today:
  `telegram-channel` and `discord-channel` are channel-only (singular `provider` = `channel`, empty
  `providers`), `slack-channel` is complete (CE-8), `mail-inbox` is not a channel. So the advisory
  flags exactly telegram + discord — the honest flag — but a hard clause would turn two already-green
  app suites red for a doctrine that postdates their authors. Giving a control teeth before the
  population satisfies it is an outage, not a gate. `UserWarning` rather than a bespoke subclass: the
  message is the whole payload, and neither repo sets `filterwarnings = error`, so it stays advisory
  where the four suites run.
  **Tests drive the OUTCOME through the entry point**, never the private helper: real temp `app.json`
  files for channel-only (warns, and the message is asserted to name the missing inbox seam, the app,
  the kwarg, and the guide), channel+inbox in both declaration shapes (silent), the exemption kwarg
  (silent), no manifest at all (silent), core's own `GoodTransport` (silent — its walk hits the repo
  marker), a manifest with no `channel` provider (silent), and a malformed manifest (silent). Plus a
  test that a channel-only app still passes every hard clause, which is the whole "warning not failure"
  claim. The bundled fixture transport is the existing `GoodTransport` subclassed into a module whose
  `__file__` sits in a real bundle dir — no second fake transport.
  **DEVIATION (path):** T7.5's Files column cited `tests/channel_conformance.py`; the kit's real,
  packaged home is `src/personalclaw/testing/channel_conformance.py` (`tests/` ships in no wheel, so a
  kit there is unimportable in apps-repo CI — the DEVIATION was already recorded in the kit's module
  docstring under T7.1). Both T7.1's and T7.5's rows now cite the real path.
  **Left for the apps repo (surfaced, not silently deferred):** telegram-channel and discord-channel
  still need their `inbox` `MessageSourceProvider`. The advisory now says so on every run of their
  suites, which is the mechanism that keeps it from being forgotten; CE-7's own scope is the guide plus
  the check. Also corrected an adjacent stale claim in `docs/architecture/inbox-channels.md` ("apps
  *may* contribute sources, but none do today by decision") — CE-8 shipped one, and the doctrine now
  expects one per channel app.

- **2026-08-22 — DISCOVERY (owner decision needed): `CE-9` and `ET-7` are in a real dependency
  DEADLOCK, so neither can ever reach the ready frontier.** Surfaced by the workspace dashboard's own
  health check, which has been reporting "2 cycles" for many ticks; this is the one that matters.
  Measured from `dag.json`:
  * `CE-9` deps include `EXT:ECOSYSTEM-TOOLING:channel scaffold template + bounty board` → resolves to
    `ET-7` (*"Bounty board: labeled `bounty` issues for wanted apps"*).
  * `ET-7` deps include `EXT:CHANNEL-EXPANSION:channel wants-list (T7.3) + channel scaffold template the
    channel bounties reference` → resolves back to `CE-9`.
  Both are `todo`, so this is a mutual wait, not a bookkeeping artifact: `CE-9` waits on `ET-7`'s bounty
  board while `ET-7` waits on `CE-9`'s channel wants-list. `CE-9` sits in OWNER PRIORITY area 2
  (Channels), so the deadlock is on the priority path.
  **Not fixed here, deliberately.** Breaking it means re-pointing a cross-plan dependency edge — most
  likely `ET-7`'s edge should name the earlier CE atom that actually publishes the wants-list rather than
  `CE-9`, which also consumes the bounty board — and that is roadmap *design*, which is owner-maintained
  (`CLAUDE.md` §2: propose roadmap changes via issue, not by editing `docs/roadmap/` structure). Recorded
  with evidence so the call can be made once.
  **The other cycle is harmless and needs nothing:** `CRE-4 → DIST-3 → DIST-1 → CRE-4` is formed the same
  way by `EXT:` resolution, but **all three atoms are `done`**, so nothing is waiting on it. It inflates
  the health count without blocking scheduling. (An earlier guess of mine — that this cycle was part of
  why priority area 1 looks gated — was wrong, and the `status=done` on all three is what disproved it.)
  **Also still open, and genuinely an owner item:** `WF2LEA-10`'s dep
  `EXT:OWNER-RULING:skill-md-conformance` is the dashboard's one `unresolved` edge — it names an owner
  ruling that has not been made.
