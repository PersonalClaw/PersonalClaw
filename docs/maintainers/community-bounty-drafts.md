# Community channel-app bounty drafts (CE-9)

Ready-to-post GitHub issue drafts for the three most-wanted community channel
apps. **Nothing here is posted yet**: the shared risk-policy paragraph below is
**PENDING OWNER APPROVAL** (CHANNEL-EXPANSION owner task 5), and the issues go
live only after that approval, verbatim or as amended. Post each as its own
issue with labels `community-tier` + `bounty`.

These three are the **channel** rows of
[app-bounty-wants-list.md](app-bounty-wants-list.md), the single list ECOSYSTEM-TOOLING's
`ET-7` bounty board files from. That file enumerates what is wanted (channels, providers,
sources) and is machine-checked; this file carries the public issue prose for the three
channels. Keep them in step — `tests/test_bounty_wants_list.py` reds if they disagree.

The other two CE-9 legs are already satisfied in-tree and are cited by every
draft:

- **Scaffold**: `personalclaw app new --type channel` works today — the type
  table is *derived* from `PROVIDER_TYPES` + the provider registry
  (`src/personalclaw/cli_app_new.py`), so `channel` needs no registration step;
  a scaffold run emits the full six-file app with a real
  `ChannelTransportProvider` stub introspected from `personalclaw.sdk.channel`.
- **Trigger sources**: remote events (a message arriving on a bridged channel)
  ride the app-registered `trigger_source` seam WF2AUT-8 shipped — see the
  coordination note in
  `docs/roadmap/plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md` ("app-contributed
  trigger SOURCES are a real seam, not a maybe"). Channel apps ship **no
  bespoke event glue**.

---

## Shared risk-policy paragraph — PENDING OWNER APPROVAL

> **Community-tier risk policy.** This app is community-tier: it is built and
> maintained by contributors, not the core team, and carries no core-team
> maintenance or security-response guarantee. It runs entirely under *your*
> credentials on *your* machine, inside PersonalClaw's standard app permission
> fencing (install-time consent, per-capability grants, untrusted-input fencing
> on everything the bridge receives). Bridging a third-party service has
> service-specific risks you accept by installing it: services without a
> public bot API (e.g. WhatsApp) are reachable only through unofficial client
> libraries, which can violate the service's Terms of Service and has led to
> account suspension for some users; end-to-end-encrypted services (e.g.
> Signal, Matrix with E2EE rooms) require the bridge to hold a device key on
> your machine, which means messages are decrypted at the bridge — that is
> inherent to any bridge, not a defect of this one, but you should understand
> it before connecting a private account. Do not connect an account you cannot
> afford to lose.

---

## Draft 1 — WhatsApp channel app

**Title:** `[bounty] Community channel app: WhatsApp`
**Labels:** `community-tier`, `bounty`

> Wanted: a community-tier WhatsApp channel app so PersonalClaw can send and
> receive messages on WhatsApp like it already does on its built-in channels.
>
> **Start here:**
> - Guide: [docs/guides/build-a-channel-app.md](../guides/build-a-channel-app.md)
>   — every `ChannelTransportProvider` member and all 18 `ChannelDelivery`
>   methods mapped to must/should/may.
> - Scaffold: `personalclaw app new my-whatsapp --type channel` generates a
>   working skeleton with the provider stub, manifest, tests, and CLI.
> - Remote triggers (react when a message arrives): declare a
>   `trigger_source` provider in your manifest — the substrate seam is already
>   live; do not ship your own event loop glue.
>
> **Notes for this service:** WhatsApp has no official bot API for personal
> accounts; implementations typically use unofficial multi-device client
> libraries. Be explicit in your README about which library you bridge
> through and its ToS exposure.
>
> *(risk-policy paragraph goes here once approved)*

## Draft 2 — Signal channel app

**Title:** `[bounty] Community channel app: Signal`
**Labels:** `community-tier`, `bounty`

> Wanted: a community-tier Signal channel app.
>
> **Start here:** same three pointers as the WhatsApp draft (guide, scaffold,
> trigger-source seam).
>
> **Notes for this service:** the practical route is `signal-cli` /
> libsignal-based linking as a secondary device. The bridge necessarily holds
> a linked-device key and sees plaintext at the bridge boundary — say so
> plainly in your README, and keep the key material inside the app's granted
> storage, never in the repo or logs.
>
> *(risk-policy paragraph goes here once approved)*

## Draft 3 — Matrix channel app

**Title:** `[bounty] Community channel app: Matrix`
**Labels:** `community-tier`, `bounty`

> Wanted: a community-tier Matrix channel app.
>
> **Start here:** same three pointers as the WhatsApp draft (guide, scaffold,
> trigger-source seam).
>
> **Notes for this service:** Matrix has a first-class client-server API and
> official SDKs, so no unofficial-client risk — the considerations are E2EE
> fidelity (device verification, key backup; an unverified bridge device
> degrades other participants' trust UX) and homeserver choice. Support
> non-E2EE rooms first; treat E2EE support as a stretch goal with its own
> honest limitations section.
>
> *(risk-policy paragraph goes here once approved)*

---

## Posting checklist (after owner approval)

1. Owner approves (or edits) the risk-policy paragraph above — record the
   approval in CHANNEL-EXPANSION's execution log.
2. Substitute the approved paragraph into each draft at the marked spot.
3. Post the three issues; apply `community-tier` + `bounty` labels (create the
   labels if the repo lacks them — ECOSYSTEM-TOOLING's ET-7 bounty board
   consumes the same `bounty` label).
4. Link the three issues back from the ET-7 bounty board when it lands.
