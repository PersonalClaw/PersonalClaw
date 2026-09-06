# App bounty wants-list

The canonical list of apps the project wants a contributor to build. **This file is
the input to the bounty board**, not the board itself: ECOSYSTEM-TOOLING's `ET-7`
files one `bounty`-labelled GitHub issue per row below, and CHANNEL-EXPANSION's
`CE-9` files the three channel rows (whose issue prose is already drafted in
[community-bounty-drafts.md](community-bounty-drafts.md), pending owner approval of
the shared risk-policy paragraph).

One list, two consumers, so the two plans cannot drift into two different ideas of
what is wanted.

## How a row earns its place

A row is admitted on **one** of exactly two grounds, and the row says which:

1. **Measured gap.** Its `provider.type` is a real member of core's `PROVIDER_TYPES`
   (`src/personalclaw/apps/manifest.py`) with **no app implementing it** — measured
   across the 54 apps in `PersonalClawApps` plus the four `ET-6` exemplar org repos —
   or with only a single teaching exemplar and no production app. A type core already
   covers with its own builtins (`sandbox` has `docker`/`lima`, `notification` has web
   push) is NOT a gap and does not get a row.
2. **Named by a plan.** CHANNEL-EXPANSION task T7.3 names the three community-tier
   channels by name, under that plan's official-APIs-only risk policy.

Nothing enters because it sounds nice. A row whose type is not in `PROVIDER_TYPES`
would send a contributor to build an app core cannot install, so
`tests/test_bounty_wants_list.py` reds if one appears.

## The three links every bounty issue carries

Every issue filed from this list carries these three pointers, because "where do I
start" is the whole difference between a bounty that gets picked up and one that
rots:

- **Scaffold** — `personalclaw app new <name> --type <type>` emits a working
  six-file skeleton (manifest, provider stub against the real SDK ABC, passing
  tests, CLI, README, MIT LICENSE). The type table is *derived* from
  `PROVIDER_TYPES` + the provider registry at runtime
  ([src/personalclaw/cli_app_new.py](../../src/personalclaw/cli_app_new.py)), so
  every type in this list already scaffolds today — there is no registration step
  for a contributor to wait on.
- **Guide** — the app-creation guide
  ([PersonalClawApps/docs/app-creation-guide.md](https://github.com/PersonalClaw/PersonalClawApps/blob/main/docs/app-creation-guide.md),
  quickstart at the top), plus
  [docs/guides/build-a-channel-app.md](../guides/build-a-channel-app.md) for the
  channel rows — every `ChannelTransportProvider` member and all 18
  `ChannelDelivery` methods mapped to must/should/may.
- **Conformance** — channel rows have an executable contract:
  `assert_channel_contract`
  ([src/personalclaw/testing/channel_conformance.py](../../src/personalclaw/testing/channel_conformance.py)).
  For every other type the bar is the generated `test_provider.py` plus apps-repo
  CI, which the scaffold output passes as generated. Say which bar applies in the
  issue; do not imply a kit that does not exist.

A fork-and-go starting point exists for four types — the `ET-6` exemplars, each a
small real app in its own public repo:
[channel-null](https://github.com/PersonalClaw/channel-null),
[inbox-github-notifications](https://github.com/PersonalClaw/inbox-github-notifications),
[watched-source-github](https://github.com/PersonalClaw/watched-source-github),
[action-home-assistant](https://github.com/PersonalClaw/action-home-assistant).

## The list

| # | Wanted app | Type | Family | Ground | Why |
|---|---|---|---|---|---|
| 1 | WhatsApp channel | `channel` | channel | named (T7.3) | Most-asked bridge. No official bot API for personal accounts, so it is community-tier by the plan's risk policy — the drafted issue says so plainly. |
| 2 | Signal channel | `channel` | channel | named (T7.3) | Practical route is a `signal-cli`/libsignal linked device; the bridge holds device key material and sees plaintext at its boundary, which the risk paragraph states. |
| 3 | Matrix channel | `channel` | channel | named (T7.3) | First-class client-server API and official SDKs, so no unofficial-client exposure — non-E2EE rooms first, E2EE as a stretch goal. |
| 4 | Calendar-backed on-duty gate | `duty_gate` | provider | measured gap | Zero apps. Core registers exactly one builtin gate, `manual`, and `DutyGateTypeHandler`'s own docstring names "a future calendar app" as the canonical supplier. Until one exists an automation cannot hold its fire against a real calendar. |
| 5 | External knowledge backend | `knowledge` | provider | measured gap | Zero apps. `KnowledgeTypeHandler` exists so an external app's provider is registered and surfaced as `kind:external` ("that is the point", per its docstring) and nothing has taken it up. |
| 6 | External memory backend | `memory` | provider | measured gap | Zero apps. `native` (SQLite + FAISS) is the only registered memory provider, so someone who wants their memory in a store they already run has no option. |
| 7 | Trigger sources for the four first-party channels | `trigger_source` | source | measured gap | The seam is live (`WF2AUT-8`: namespaced `app:<name>:<event>` bus sources) and **not one of `slack-channel`, `telegram-channel`, `discord-channel`, `email-channel` declares one** — so no automation can react to an arriving message. This is CHANNEL-EXPANSION T7.6's forward obligation, now due. |
| 8 | Inbox sources for Telegram and Discord | `inbox` | source | measured gap | `CE-7` left this open in the apps repo deliberately and the conformance kit warns about it on every run of those two suites: both are channel-only, so their messages never reach the generic inbox seam. |
| 9 | A watched source that is not a repo watcher | `trigger_source` | source | measured gap | Only one `trigger_source` app exists (`watched-source-github`, an `ET-6` teaching exemplar). A calendar or feed-shaped source proves the seam beyond one repo watcher. |

Nine rows, which clears `ET-7`'s "≥6 bounties live" bar with all three families
(channels, providers, sources) represented.

## What is still owner-gated

Filing these is not purely mechanical — two owner decisions sit in front of the
board, and neither is an agent's to make:

- **The risk-policy paragraph** (CHANNEL-EXPANSION owner task 5) gates rows 1-3.
  The drafted paragraph in [community-bounty-drafts.md](community-bounty-drafts.md)
  is marked **PENDING OWNER APPROVAL**. It is consent-adjacent security copy; it goes
  public verbatim or as amended by the owner, never paraphrased.
- **The reward model** (ECOSYSTEM-TOOLING owner task 3): recognition-only vs. small
  monetary bounties, with recognition-only recommended at this stage. The issues
  should not promise a reward the project has not decided to offer.

The `bounty` and `community-tier` labels do not exist in this repository yet; create
them as part of posting.
