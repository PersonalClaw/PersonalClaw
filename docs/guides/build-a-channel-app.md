# Build a channel app

A **channel** is a place a person talks to their assistant that is not the dashboard: a
chat app, a DM, a mailbox. A channel app owns both directions — it receives messages from
the vendor and it renders the assistant's replies back into the vendor's idea of a
message. Everything vendor-specific lives in the app; core only knows the two seams.

This guide is extracted from the first-party **telegram-channel** app in the
[PersonalClawApps](https://github.com/PersonalClaw/PersonalClawApps) repository — the
reference channel, because it is written against the raw vendor HTTP API with no SDK, so
every obligation below is visible in one small tree. Read it along
[the inbox & channels architecture](../architecture/inbox-channels.md) and
[the provider boundary](../architecture/provider-boundary.md).

Two rules frame everything else:

- **Core never names your vendor.** No string, enum member, `if provider == …`, or
  accommodation. If your channel seems to need a core change, that change is a new
  *generic* seam or it does not happen.
- **Your app owns every seam your vendor touches** — not just the channel. See
  [Vendor completeness](#vendor-completeness) before you decide you are done.

---

## The two contracts

| Contract | Kind | Import |
|---|---|---|
| `ChannelTransportProvider` | ABC — your class subclasses it | `from personalclaw.sdk.channel import ChannelTransportProvider` |
| `ChannelDelivery` | `Protocol` — structural, you do not subclass it | `from personalclaw.sdk.channel import ChannelDelivery` |

The transport is the *connection*: identity, lifecycle, health, inbound. Delivery is the
*rendering*: how a reply, a stream, an approval prompt, or a file becomes a message in your
vendor's UI. A channel that only ever pushes notifications can ship a transport alone; a
channel that carries conversations ships both.

Everything a channel app is allowed to import from core comes through
`personalclaw.sdk.channel`. That facade is the boundary the apps-repo import lint enforces
— reaching into `personalclaw.<internal>` will fail the lint, and would freeze a core
internal besides.

---

## Obligation tables

Three levels, and they mean exactly this:

- **MUST** — the platform calls it unconditionally. Omit it and your app is not
  installable, or it lies to a surface the owner reads.
- **SHOULD** — the platform degrades *visibly* without it but still works. If your channel
  genuinely cannot do it, say so in `capabilities()` and document the gap in your README.
  Do not silently omit a method core will call.
- **MAY** — genuinely optional. A channel is not defective for lacking it.

The **Kit** column says whether the conformance kit *asserts* the obligation. The kit is
the authority where the two could disagree: it carries the machine-readable inventory
(`MUST_TRANSPORT_METHODS`, `SHOULD_DELIVERY_METHODS`, `MAY_DELIVERY_METHODS` in
`src/personalclaw/testing/channel_conformance.py`) and this table is written from it.

### `ChannelTransportProvider`

| Member | What core does with it | Obligation | Kit |
|---|---|---|---|
| `name` (property) | the opaque provider key: trust store, SEL, inbox source, settings all key off it. Pick it once; changing it orphans state | **MUST** | yes — clause 1 |
| `display_name` (property) | the label on the Channels page | **MUST** | yes — clause 1 |
| `connect()` | called at boot; returns success as a `bool` | **MUST** | yes — clause 2 |
| `disconnect()` | graceful close at shutdown | **MUST** | yes — clause 2 |
| `send(OutboundMessage)` | the one outbound primitive every surface can rely on; returns `bool`, and never raises for a well-formed message | **MUST** | yes — clause 2 |
| `capabilities()` | machine-readable feature gate — core routes and feature-gates off it, so it must be *honest*, not aspirational | **MUST** | yes — clause 3 |
| `health()` | the Channels page pill. `{state, detail}` with `state` in `ready` / `offline` / `error`; a fourth state renders as an unknown grey pill | **MUST** | yes — clause 5 |
| `test()` | the "Test" button — an active probe. `{ok: bool, detail: str}`, and it MUST agree with `health()`: a green Test on an offline channel is a lie | **MUST** | yes — clause 5 |
| `info()` | static listing; MUST project `name`, `display_name`, `connected`, `capabilities()` without relabelling any of them | **MUST** | yes — clause 1 |
| `connected` (property) | the default `health()`/`test()`/`info()` all derive from it | **SHOULD** — override it, or override `health()` so it stops mattering | **no** — in no kit tuple |
| `start_inbound(services)` | called once by the gateway *after* core services are up, with a `GatewayServices` handle. This is where a push/poll receiver starts | **MUST if `capabilities().inbound` is `True`**, else MAY | partly (clause 4 checks the inbound path exists, via `inbound_via=`) |
| `stop_inbound()` | graceful stop of whatever `start_inbound` started | **MUST if you implement `start_inbound`** | **no** — in no kit tuple |
| `receive()` | the optional pull-based inbound seam: an `AsyncIterator[ChannelMessage]`. The base implementation raises | **MAY** — no shipped channel uses it; they all drive their own loop from `start_inbound` | partly (clause 4 accepts a named handler instead) |

`connected`, `start_inbound`, `stop_inbound` and `receive` appear in **none** of the kit's
three tuples. Their levels above are derived from the ABC's own contract (what core calls,
and what the default does if you skip it) — treat them as doctrine, not as something the
kit will catch for you.

### `ChannelDelivery`

All eighteen `Protocol` methods. Every delivery obligation is conditional on shipping a
delivery object at all; once you do, the kit asserts the MUST and SHOULD rows.

| Method | Purpose | Obligation | Kit |
|---|---|---|---|
| `deliver_text` | a plain reply reaching the user — the floor of a conversational channel | **MUST** | yes — clause "delivery" |
| `deliver_rich` | formatted output (your vendor's markdown / blocks / embeds / HTML) | **SHOULD** | yes — clause "delivery" |
| `upload_attachment` | files out | **SHOULD** | yes — clause "delivery" |
| `request_approval` | the tool-approval prompt and its answer path (buttons, inline keyboard, reply token) | **SHOULD** | yes — clause "delivery" |
| `build_thread_link` | a deep link back to the conversation, for the dashboard and for notifications | **SHOULD** | yes — clause "delivery" |
| `start_stream` | opens a live-updating message; returns the message ts/id, or `""` for "no animation" | **SHOULD when `capabilities().edits` is `True`**; **MUST return `""`** when `edits` is `False` | yes — clause 8 |
| `append_stream_task` | pushes a progress update into the open stream, throttled | **SHOULD when `edits` is `True`** — at most one edit per your declared floor | yes — clause 8 |
| `stop_stream` | closes the stream and **force-flushes** the exact final text past the throttle | **SHOULD when `edits` is `True`** — a throttled-away final update is a stream frozen mid-run | yes — clause 8 |
| `deliver_cron_result` | a scheduled run's output | **MAY** | no — never asserted |
| `deliver_notification` | an owner notification routed to this channel | **MAY** | no — never asserted |
| `deliver_chat_mirror` | mirrors dashboard chat into the channel | **MAY** | no — never asserted |
| `deliver_subagent_reply` | a subagent's reply | **MAY** | no — never asserted |
| `resolve_user_profile` | richer profile lookup | **MAY** | no — never asserted |
| `list_reply_channels` | the pickable reply targets a settings UI offers | **MAY** | no — never asserted |
| `open_dm` | resolve a user id to a DM conversation id | **SHOULD if your vendor has DMs** (owner-directed delivery needs it) | **no** — in no kit tuple |
| `resolve_user_name` | user id → display name, for transcripts and attention items | **SHOULD** | **no** — in no kit tuple |
| `is_tracked_channel` | whether this conversation is opted in — delegate to core's `is_tracked_channel(provider, id)`, never a second store | **SHOULD** | **no** — in no kit tuple |
| `channel_info` | vendor metadata about a conversation | **MAY** | **no** — in no kit tuple |

`open_dm`, `resolve_user_name`, `is_tracked_channel` and `channel_info` are in none of the
three tuples either — same caveat as above: doctrine, not an assertion.

### Declare capabilities honestly

`ChannelCapabilities` is the routing input, so a field you set to `True` is a promise:
`inbound`, `threads`, `attachments`, `reactions`, `edits`, `rich_text`,
`typing_indicator`, `max_text_len` (`0` = unbounded). Defaults are conservative — text-out
only, no inbound — which is the right starting point. Two clauses of the kit exist purely
to catch dishonesty here: declaring `inbound=True` with no inbound path, and declaring
`edits=False` while `start_stream` hands core a ts to animate.

---

## Transport lifecycle — who calls what, when

```
install / enable      →  your manifest's provider `implementation` factory builds the instance
gateway boot          →  connect()                     (returns bool; a False is not a crash)
                      →  start_inbound(services)       (once, AFTER core services are up)
Channels page render  →  info(), capabilities(), health()
"Test" button         →  test()                        (must agree with health())
every inbound message →  your handler → guard_inbound(...) → session
gateway shutdown      →  stop_inbound() → disconnect()
```

Notes that bite:

- `start_inbound` receives a `GatewayServices` handle — sessions, cron, channel history,
  dashboard state, config, owner. Route inbound messages into chat sessions through it;
  do not reach around it into core internals.
- Own your loop. Every shipped channel drives its own receiver from `start_inbound` and
  normalises the vendor payload into `ChannelMessage` in a private handler. Tell the kit
  which handler that is with `inbound_via="_on_message"` — it accepts a named async method
  as proof of the inbound path rather than demanding the `receive()` shape nobody uses.
- Persist your cursor (poll offset, IMAP UID, gateway session) **before** dispatching the
  message, not after. A crash mid-dispatch must not replay the message forever.
- `health()` is passive and cheap (credentials present? socket up?). `test()` is allowed to
  make one round-trip. If they can disagree, you have two truths and the owner will find
  the wrong one.

---

## Trust, pairing and linking

A channel is an open door: anyone who can find your bot can type at it. Core owns that
policy so it cannot drift per channel — your transport's job is to *call* it, on **every**
inbound path, before any text reaches a session.

```python
from personalclaw.sdk.channel import guard_inbound

verdict = guard_inbound(
    state, self.name, sender_id,
    sender_name=sender_name, channel_id=channel_id, is_dm=is_dm, text=raw_text,
)
if not verdict.allowed:
    if verdict.canned_reply:
        await self.delivery.deliver_text(channel_id, verdict.canned_reply)
    return
text_for_session = verdict.fenced_text or raw_text
```

What that one call gets you, and what you must not re-implement:

- **Unknown DM sender.** Under the default `dm="pairing"` policy an unpaired sender is
  **denied**, `verdict.canned_reply` is the shared `CANNED_PAIRING_REPLY` (every channel
  says the same thing — do not write your own), and core raises exactly **one** actionable
  owner attention item carrying Allow/Deny plus the `provider` + `sender_id` the buttons
  need. It is deduped: a second message from the same stranger does not re-alert. The
  canned reply is rate-limited to once per sender per 24h.
- **Pairing.** The owner runs `personalclaw pair <provider>` (see
  [the CLI reference](../reference/cli.md)) and hands over the 8-digit code out of band;
  the sender types it into your channel and you call `redeem_pairing_code`. Single-use,
  TTL-bound. Or the owner just hits **Allow** on the attention item.
- **Groups.** Default `group="tracked_only"`: an untracked group is denied *silently*
  (`reason == "untracked_channel"` — no owner spam), a tracked one is allowed. Use
  core's `track()` / `untrack()` / `is_tracked_channel()`; a channel-local allowlist is a
  second source of truth and will diverge.
- **Fencing.** Non-owner content comes back as `verdict.fenced_text`, already wrapped by
  `fence_channel_content(text, provider, sender)`. **Use it.** Passing the raw text into a
  session instead is a prompt-injection hole, and a hand-rolled fence loses the
  neutralised chat-template-token defences. Never test a fence by substring — the
  attributed form will not match; use `security.is_fenced`. The kit reads your module's
  source for a `fenced_text` reference precisely to catch a refactor that reverts to
  `cm.text`.
- **Owner identity** comes from the credential store (`CRED_OWNER_ID`), not from your
  settings.

Linking the channel to the dashboard: build a session link with the token-auth helpers
(`generate_token`, `LINK_WINDOW_SECS`) over `dashboard_origin()`, and give the owner a way
back the other direction with `build_thread_link`. Treat any token you print as a
credential — it is a bearer token with a long TTL.

---

## Using the conformance kit

One executable contract, four channels. Your suite calls it with a **live provider
instance** (the kit never constructs one, so it cannot disagree with your wiring):

```python
# your-channel/test_provider.py
from personalclaw.sdk.channel import assert_channel_contract

from your_runtime.delivery import YourDelivery
from your_runtime.transport import YourTransport


def test_channel_contract(fake_backend):
    transport = YourTransport(token="test-token")
    delivery = YourDelivery(fake_backend)
    assert_channel_contract(
        transport,
        delivery=delivery,
        fake_backend=fake_backend,          # must expose a recorded `edits` list
        min_edit_interval=delivery.EDIT_MIN_INTERVAL,
        clock=fake_backend.set_now,         # setter advancing the injected monotonic clock
        inbound_via="_on_message",          # your async inbound handler
    )
```

Import it from `personalclaw.sdk.channel` — **not** from `personalclaw.testing.…` and not
from core's `tests/` tree. The facade is the only path the apps-side import lint allows,
and it is the path core's own suite exercises. (The kit lives in the installed package
rather than core's `tests/`, which ships in no wheel; the module docstring records that
decision.)

Run it against an isolated `PERSONALCLAW_HOME` — the kit drives the **real** trust seam,
so it needs to see default policies and must never write your actual store. It raises
`ChannelContractError` (an `AssertionError` subclass) naming the violated clause, and it
imports no pytest, so a plain script or your own harness can call it too. It is re-entrant:
call it once per config if you have several.

| Clause | Fails when |
|---|---|
| identity | blank `name`/`display_name`, or `info()` relabels either, or drops the capability dict |
| capabilities | `to_dict()` is missing a declared field, adds an undeclared key, or ships a wrong type |
| connect/send | `connect()`/`disconnect()` are not awaitable or return the wrong type; `send()` returns a non-bool or raises on a well-formed message |
| receive/inbound | `inbound=True` with no inbound path, a named `inbound_via` that is missing or not async, or `inbound=False` while an inbound handler exists |
| health/test | a state outside `{ready, offline, error}`, a missing `detail`, a non-bool `ok`, or `test()` claiming ok on a non-ready `health()` |
| unknown-sender | an unpaired DM sender is allowed, gets the wrong canned reply, or raises zero or two owner requests |
| fencing | tracked-group content comes back unfenced, the fence replaces rather than wraps the text, or your module never reads `verdict.fenced_text` |
| delivery | `deliver_text` missing, or any SHOULD method absent while you passed a `delivery` |
| streaming | `edits=True` and the trio is incomplete, the throttle fires more than once per your floor, `stop_stream` does not force-flush — or `edits=False` and `start_stream` returned a non-empty ts |
| vendor completeness | **never fails** — warns; see below |

If you cannot supply `min_edit_interval` + `clock`, the streaming clause degrades to
presence-only. The kit refuses to sleep, and refuses to invent a floor it cannot know.

---

## Packaging

A channel app is an ordinary app bundle. Required manifest fields are the usual four —
`name` (kebab-case), `version` (semver), `displayName`, `description` — plus the provider
registrations. The full manifest contract is
[the app-platform architecture doc](../architecture/app-platform.md) and the
app-creation guide in the apps repository; the channel-specific parts are:

```jsonc
{
  "name": "your-channel",
  "version": "0.1.0",
  "displayName": "Your Channel",
  "description": "Talk to your assistant from Your Channel.",
  "provider": {
    "type": "channel",
    "implementation": "your_runtime.transport:create_provider",
    "settingsSchema": { /* Draft-07 + x-meta: what the settings form renders */ }
  },
  "providers": [
    { "type": "inbox", "implementation": "your_runtime.source:create_source" }
  ],
  "permissions": { /* the MINIMUM — the Store shows these as the install-consent surface */ },
  "ui": [ /* your own pages, if the generic settings form does not fit */ ]
}
```

- `provider` (singular) is the canonical registration; `providers[]` carries the rest.
  Both shapes are read everywhere, including by the completeness advisory below.
- **Secrets go in the credential store**, never in `app.json` or the settings schema.
  Prompt for them from your `cli_setup` contribution and probe them from `cli_doctor`.
- Declare the **minimum** permissions. The Store renders them as the consent surface a
  user reads before installing.
- Ship `test_provider.py` (the kit call above), a `README.md` that documents any capability
  you declared `False`, and a `LICENSE`.
- No vendor SDK unless it buys something a thin HTTP client cannot. The reference channel
  is deliberately raw `httpx`: fewer transitive dependencies, no version-pin fights, and
  every retry/backoff rule visible in your own code.

---

## Vendor completeness

A channel is rarely the only seam a vendor touches, and an app that registers only the
channel leaves the rest of the platform blind to it. **One vendor app registers every seam
that vendor touches.** The checklist:

| Seam | Manifest | Why it is not optional |
|---|---|---|
| **Channel transport** | `provider` / `providers[]` `type: "channel"` | conversations in and out |
| **Inbox message source** | `providers[]` `type: "inbox"` (a `MessageSourceProvider`) | messages that arrive while no session is live. Without it, anything sent to your channel outside a conversation is invisible to the owner's Inbox |
| **Trigger source** — *when that seam exists* | (forward obligation) | vendor events driving automations. The workflows automation substrate owns this seam; until it lands, do **not** hand-roll early event glue. Add the provider when the seam ships |
| **Contributed UI** | `ui` pages in your own bundle | anything the generic provider-settings form cannot express |

**Rule 2 — your UI, not core's.** Anything that does not fit a pluggable seam becomes
*that vendor app's own UI surface* (a `ui` page inside the app) — **never** a core
accommodation. If a vendor feature seems to need a core change, the change is a new
*generic* seam or it does not happen.

**Rule 3 — core never names the vendor.** Not in code, not in an enum, not in a comment.
If you find residue while touching a seam, scrub it in the same change.

The reference for the completed pattern is the **slack-channel** app in the apps
repository: one bundle, a channel transport plus an inbox `MessageSourceProvider` built as
an adapter over the transport's *existing* client (not a second client), and its non-seam
surface behind its own `ui` block.

### The kit's completeness advisory

The conformance kit checks this for you. From your live provider it locates the owning
`app.json`, and if the manifest declares a `channel` provider but no `inbox` provider in
either shape, it emits a `UserWarning` naming the missing seam:

```
vendor completeness: the app 'your-channel' registers a channel transport but no inbox
message source — its manifest declares provider types ['channel']. …
```

It is **advisory, never a failure**, deliberately: the doctrine postdates the shipped
channel apps, and giving a control teeth before the population satisfies it is an outage,
not a gate. It also stays silent when no `app.json` is discoverable — a bare unit test or
a core fixture has no bundle, and an advisory that fires on fixtures teaches readers to
ignore it.

If your vendor genuinely has no message-source semantics, say so and the advisory stops:

```python
assert_channel_contract(
    transport,
    no_inbox_source_reason="the vendor exposes no message history to poll",
)
```

Put the real reason there. It is the exemption record, and the next reader of your suite is
the only audience for it.

---

## Ship checklist

- [ ] `capabilities()` is honest — every `True` is backed, every `False` documented in the README.
- [ ] `guard_inbound` is called on **every** inbound path, and `verdict.fenced_text` is what reaches the session.
- [ ] `health()` and `test()` cannot disagree.
- [ ] The inbound cursor is persisted before dispatch.
- [ ] `assert_channel_contract` passes with `delivery=`, `min_edit_interval=`, `clock=` and `inbound_via=` supplied — not just the bare transport.
- [ ] No completeness advisory, or a real `no_inbox_source_reason`.
- [ ] Core imports go through `personalclaw.sdk.*` only; no core file names your vendor.
- [ ] Secrets in the credential store; minimum permissions declared.
- [ ] `README.md` + `LICENSE` + tests ship with the bundle.
