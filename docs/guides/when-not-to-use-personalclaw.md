# When PersonalClaw is not the right tool (yet)

Most projects tell you what they do. This page tells you when to close the tab.

Every item below is a situation where PersonalClaw, **as the code stands today**, will
not serve you — not a feature we are coy about, and not a roadmap promise dressed as a
caveat. Each one names the file or the documented decision that makes it true, so you
can check rather than trust. Where a limit is a deliberate design boundary rather than
unfinished work, it says so, because those two deserve very different decisions from
you: one is "wait", the other is "use something else".

If none of these describe you, start at [Getting started](getting-started.md).

---

## 1. You need your data to survive upgrades

**Walk away if:** PersonalClaw would hold anything you cannot afford to lose or
recreate.

There is no migration machinery. PersonalClaw is pre-1.0 and follows a clean-break
doctrine: when a design is replaced the old path is deleted in the same change, and
state under `~/.personalclaw` — sessions, memory, knowledge, config, app state — may
need to be recreated after a 0.x update. That is stated in the README's pre-1.0 banner
and in [CONTRIBUTING.md → Breaking changes](../../CONTRIBUTING.md#breaking-changes),
where the lifecycle model is described as *a mental model, not shipped machinery*.

This is also not nearly over. Migration-backed change discipline is scheduled
deliberately late — it binds once the architecture stops moving — so breaking 0.x
updates are the norm, not the exception, for the foreseeable future.

`personalclaw snapshot` writes a portable archive and `personalclaw restore` reads it
back, which is a real backup path. It is not a migration: restoring an old snapshot into
a newer PersonalClaw is subject to the same clean breaks.

**Come back when:** the README's pre-1.0 banner is gone. It is removed on a judgment
about architectural stability, not on a date.

## 2. You have no model of your own

**Walk away if:** you need real work done and have no model to connect, meaning no local
inference server and no provider account.

No model weights ship inside PersonalClaw's packages: not the wheel, not the container
image, not the desktop build. A fresh install can download one small model, and that
model is a floor rather than a way to get work done. Concretely:

- The `bundled-chat` app (`src/personalclaw/apps/native/bundled-chat/`) offers a one-time
  138 MiB download of `SmolLM2-135M-Instruct` (Apache-2.0) in onboarding and on the chat
  screen. After it, the model runs on the CPU with no key and no network, so a new install
  can chat. It has 135 million parameters and no tools, and it doesn't see your memory,
  skills or knowledge. [Security limitations §5](../security/limitations.md#5-the-bundled-default-model-is-a-floor-not-an-assistant)
  records what it did with real requests, and
  [bundled-model-signoff.txt](../../src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt) records the model,
  its licence and its digest.
- Core contains `llm/anthropic.py` and `llm/openai.py`, but these are *wire-protocol
  clients only* — neither registers itself as a provider at import. Registration is
  owned by the `anthropic-models` and `openai-models` app bundles, which live in the
  separate [PersonalClawApps](https://github.com/PersonalClaw/PersonalClawApps) repo.
  This split is the provider boundary and is documented as such in
  [provider-boundary.md](../architecture/provider-boundary.md).
- The Store's default source is that repo's git URL
  (`_DEFAULT_GIT_SOURCES` in `src/personalclaw/apps/catalog.py`), so installing a
  hosted-model provider needs a reachable `github.com`.
- The wheel bundles two model providers, both seeded into your home at first boot and
  neither one uninstallable: `bundled-chat` (above) and `ollama-models`. The second is a
  client for [Ollama](https://ollama.com), defaulting to `http://localhost:11434`, and
  needs Ollama actually running with a model pulled.

So an offline install can chat, after one download or after you copy
`models/bundled-chat/` from a home that has it, but only at that small model's level.
Offline real work still needs your own Ollama with a pulled model. An air-gapped machine
with no Ollama and no copied weight cannot complete setup into a working chat, and nothing
in the product will pretend otherwise.

This is a design boundary, not a gap: the provider-agnostic core is the reason no vendor
is baked in, and the default model is small on purpose. Its sign-off caps the download at
150 MiB, so it gets a new install talking rather than replacing a model you choose.

## 3. More than one person needs an account

**Walk away if:** you want per-person logins, a shared workspace, per-member
permissions, or SSO.

PersonalClaw is single-user by construction. There is no user-account concept in the
codebase to attach a second person to. The gateway's auth is a local token (or none,
when it is bound to loopback only); SSO/SAML/OIDC is **not shipped by design**, because
a single-user self-hosted system has no directory to federate with — see
[Security model](../architecture/security.md) and [Remote access](remote-access.md).

Nor can you build a team out of several installs. From
[Companion apps → No hub, ever](companion-apps.md#no-hub-ever), quoted as a
permanent decision rather than a status:

> **No hub in core, ever. No gateway-to-gateway anything.** Gateways never discover,
> sync with, or proxy for each other; no shared identity, no cross-gateway search, no
> aggregated inbox in core or in the shells.

Several gateways can appear in one *client's* endpoint list, but they stay N independent
machines and only the client knows they are related. If you need a shared assistant for
a team, family, or company, this is the wrong architecture and will stay the wrong
architecture — that is a permanent boundary, not a backlog item.

## 4. You want a real app on your phone

**Walk away if:** you expected something to install from the App Store or Play Store.

There is no native mobile app and none is in progress. Two shells exist: the Electron
desktop app and "the phone", which is the served dashboard **installed as a PWA** —
[Companion apps → Bringing up a new platform](companion-apps.md#bringing-up-a-new-platform).
That works, and it is genuinely usable on a phone on your own network, but it is a web
app in a browser shell: no push notifications from a closed app, no background
execution, no store listing.

The phone also has to reach the gateway. On your own network that is a LAN address; from
outside it is a tunnel plus password and 2FA ([Remote access](remote-access.md)). If you
wanted a cloud account that "just works" from anywhere, there isn't one — by design,
since there is no hosted service.

## 5. You are on Windows and will not use WSL2 or a container

**Walk away if:** you need a native Windows install.

Windows native is **not supported**, and the reason is recorded rather than deferred
vaguely: a [native-Windows audit](../research/windows-native-audit.md) ruled the backend
port *no-go* because it would silently weaken file-permission and sandbox guarantees the
rest of the system depends on. There is also no Windows desktop shell, and none is
planned until both that audit flips and Windows code-signing secrets exist
([The desktop app → Windows](desktop.md#windows--deferred-2026-09-05)).

What does work on Windows is **WSL2** or **Docker Desktop**, both documented walkthroughs
in [Platforms](platforms.md#windows-via-wsl2). Honest nuance: the WSL2 path is a checklist
that has been executed; the Docker Desktop checklist is written but **not yet executed
verbatim** (see the proof column in [Platforms](platforms.md)). If "install a Windows
program" is a hard requirement, stop here.

## 6. You want a signed, auto-updating desktop app

**Walk away if:** an unsigned build or a manual build step is unacceptable.

The macOS Electron shell is explicitly experimental. It is **not built, signed, or
released by CI, and has no auto-update channel** — the README's tech-stack note says
exactly that. The `.dmg` is not attached to releases because Apple Developer signing and
notarization credentials do not exist in CI; until they do you build it yourself with
`make desktop-dist`, and macOS Gatekeeper charges you one approval step per installed
version ([The desktop app → macOS](desktop.md)).

The gateway itself updates fine (`personalclaw update`, plus **Settings → Updates**).
It is the desktop *shell* that has no update channel. If you only need a window, the
dashboard in an ordinary browser tab is the supported path and always current.

## 7. You want it to live in Telegram, Discord, or your email

**Walk away if:** a specific chat app *is* the product for you.

Core registers exactly one channel transport by default: the **Web UI**. That is the
whole of `register_default_transports()` in
`src/personalclaw/channel_transports/__init__.py`. Slack is not in core either — its
lifecycle is owned by the extension system, so it arrives as an app you install. Telegram
and Discord are named in that module's own docstring as *future*; they do not exist
today, and neither does an email channel.

So the inbox that watches channels and drafts replies is real, but on a fresh install the
only channel it has to watch is the dashboard. Bringing a new one is a documented,
supported job — [Build a channel app](build-a-channel-app.md) ships the transport and
delivery obligations plus a conformance kit — but it is work you would be doing, not a
setting you would be toggling.

## 8. You want batteries-included web search or internet-wide RAG

**Walk away if:** you expected search to work out of the box.

**No search provider ships bundled.** `web_search` and `web_fetch` are real tools, and
the research flows are built on them, but they are served by a search-provider app you
bind — with none bound, nothing is bound. This is a provider seam you fill.

Retrieval over *your own* documents is the part that works without any of that: keyword
search (SQLite FTS5) is always available. The semantic half is not free either — vector
retrieval needs an embedding provider bound, and with none bound embeddings are off and
retrieval stays keyword-only. Media extraction (image OCR/vision, audio/video
transcription) is model-gated the same way: the file still ingests, extraction just
skips.

## 9. You intend to run apps you do not trust

**Walk away if:** you were reading "permission-gated app platform" as a sandbox.

It is not one, and the project says so at length in
[Security limitations](../security/limitations.md). The five named
non-enforcements matter most here:

- An app's own code runs as you. Its backend, its provider module, and the MCP server
  commands and setup hooks in its manifest can read and change any file in your
  PersonalClaw home, including `config.json` and the key that signs your session tokens.
  The permissions bound the app's token, not its code. Only a backend that names a
  sandbox tier is confined.
- The app `network` permission is **declaration-only, unenforced by design**. An app's
  provider code is imported **in-process** by the gateway, so its outbound calls *are*
  the gateway's. The consent surface labels this advisory rather than implying
  containment.
- An app's declared Python dependencies pip-install into one directory every app shares
  (`<home>/app-python`), which the gateway loads into its **own process** after its own
  packages. An app can add a package but not replace one the gateway uses; once loaded,
  its code is importable by everything in that process.
- An app's frontend bundle runs in the dashboard's **own page**, not a separate origin.
- ACP agents under auto-approve rely on system-prompt framing, not rails.

The real control is the supply-chain scanner — quarantine → scan → consent → install,
with a `dangerous` terminal verdict — plus a closed set of owner-only capabilities that no
app token can reach (the terminal, computer-use, the credential store, the audit log,
your password and second factor, your security settings, the MCP servers the gateway
launches, and defining the automations that run as you). That is meaningful, and it is
also *vetting what you choose to install*, not confinement afterwards. Treat installing
an app as running a program as yourself, because that is what it is.

## 10. You want a hosted service, or to run one install for other people

**Walk away if:** you do not want to operate a process.

There is no hosted PersonalClaw and no plan for one. You run the gateway: a local
process, a container, or a systemd/launchd service, on a machine you keep. Nobody
operates it for you, nobody backs it up for you, and support is a GitHub issue rather
than an SLA (see [SECURITY.md](../../SECURITY.md) for what response to expect on a
security report specifically).

Running one install *on behalf of* several people is the same wrong shape as §3: there
are no accounts to separate them, so everyone sharing an install shares one memory, one
knowledge base, one credential store, and one chat history.

## 11. You need a spend cap you can trust out of the box

**Walk away if:** you cannot accept an autonomous agent that will spend your provider
credit without a ceiling until you set one, or you needed a *hard* limit rather than an
estimate.

This one is easy to miss, because the feature exists. Three ceilings are real, wired, and
have controls under **Settings → Guardrails**: `guardrails.budgets.max_tokens_per_run`,
`max_tokens_per_day` and `max_dollars_per_day` (`src/personalclaw/config/safety.py`,
`BudgetConfig`). A spent token ceiling pauses unattended runs into needs-input rather than
overspending quietly, and a dollar ceiling refuses each call that costs money once there is no
room for it, while calls to a model that costs nothing keep running. A loop a ceiling refuses is
paused, not failed: it says which ceiling and what was spent, raises one Inbox item, and tries
nothing again until you resume it. The day counter is
persisted to `~/.personalclaw/spend.json` so it survives a restart, and its day is the day in
your timezone (`timezone` in config.json, set by `personalclaw setup`, else this machine's), the
one your schedules run in and Settings → Usage counts too. That is a genuine control.
Five things about it are worth knowing *before* you point a goal loop at something and go to
bed:

- **All three default to zero, and zero means unlimited.** The dataclass says so in as
  many words — *"Zero means UNLIMITED for that dimension — the conservative default so an
  existing user's unattended work is never suddenly capped on upgrade."* So a **fresh
  install has no cap at all**. Nothing will prompt you for one; you have to go and set it.
- **They bind unattended work only — not the chat window.** Enforcement lives in
  `ModelCallGuard`, and that module states its own scope: the wrap is *"gated on the
  non-interactive chat-text use case"*, which *"excludes, by construction, both the
  interactive `NativeAgentRuntime` … and its inner model — the interactive chat stream a
  human is watching is explicitly out of scope"*
  (`src/personalclaw/guardrails/model_call.py`). That is a defensible line for a stream you
  are sitting in front of, but it means `max_dollars_per_day` is **not** a whole-install
  cap. Goal loops, cron fires and subagents are metered; typing into chat is not.
- **The dollar ceiling is an estimate, not a bill.** A call costs what its provider reports,
  else what it used at its model's rate, in the unit the model is billed in: tokens for a text
  model, an image (by its size and quality) for an image model, a second of video, a minute of
  audio for speech-to-text, characters for text-to-speech. The rate is one you set in
  **Settings → Usage → Model prices**, a known $0 for a model this machine runs itself (a model
  server whose app runs the models it serves where it is, such as Ollama, at an address on this
  machine, for each model it runs itself and not one it answers from Ollama's cloud, whose tag
  says `cloud` or whose host the server names; the bundled offline model; and a speech, embedding
  or image engine whose models are
  downloaded into PersonalClaw's home, or that runs them through a model server at an address on
  this machine), the rate its provider app declares, or the shipped price list (`price_call` and
  `price_units` in `src/personalclaw/routing/rates.py`, which price every dollar the caps and the
  Usage page count). The shipped list is each model maker's published list price on the day it
  was recorded, and Model prices says whose it is and when; the service that runs a model (a
  cloud platform's inference profile, a reseller) may bill differently, and a price you set comes
  first. A Bedrock inference profile (`global.`, `us.`, `eu.`, `apac.` …) is priced as its base
  model. An OpenAI-compatible endpoint on this machine is not taken as free, because a proxy there
  can answer for a paid cloud API: it is priced by its model's id, or has no price. A call none of
  those prices is refused while a dollar ceiling is set, and the refusal says how to lift it: give
  the model a price ($0 if it really costs nothing), or set the ceiling to 0. Raising the ceiling
  does not help, since no ceiling can count a call it has no price for. With no dollar ceiling set
  such a call runs, and the meter counts it as one its totals could not count. PersonalClaw never
  sees your provider invoice, so an estimated ceiling cannot be an authoritative one.
- **Images, video, transcription and speech are metered too.** Each goes through one metering
  seam (`src/personalclaw/guardrails/media_call.py`): one an automation, a loop, a subagent or an
  import makes is weighed against the dollar ceilings before it runs and charged after, one you
  make yourself is not capped (as your chat is not), and every one is counted in Usage by its
  images, seconds, minutes or characters. A transcription whose length cannot be read before it
  runs cannot be weighed, so under a dollar ceiling it is refused unless its model costs nothing.
  Embedding calls are not metered.
- **It holds calls that run at the same time, as far as it can estimate them.** Before a call
  that costs money starts, it sets aside what it may cost: its prompt and an answer as long as
  the longest a call to that model has given today (4,096 tokens before the first), at its
  price, or the most a call to that model has cost today if that is more. It starts only when
  that fits beside what is spent and what the calls already running have set aside; otherwise it
  waits for them, and is refused when it cannot fit even alone. Until a call to a model has
  finished today, a second call to that model waits for it, so a fan-out learns the price of the
  first before it spends on the rest. A call can still cost more than it set aside (an answer
  longer than any before it): it is charged what it cost, and nothing that costs money starts
  once the ceiling is reached. A call whose provider reports no usage is counted at what it set
  aside.

What you *do* get for free is visibility rather than control: every model turn is recorded
to a per-turn cost/token ledger (`src/personalclaw/usage_ledger.py`) and rolled up under
**Settings → Usage**. It is deliberately *"observation only, never enforcement"*, and it is
honest about what it cannot price — a turn nothing prices records `priced = False` and
renders as **unpriced**, never as `$0.00`, and any rollup containing one reports itself
incomplete. So you can always answer "what did that cost me", and a model served on this
machine costs nothing either way. A model your Ollama answers from Ollama's cloud is not one: it
is unpriced until you give it a price ($0 if your plan makes it free to you).

Whatever provider and model you use, you can give it your own price under **Settings → Usage →
Model prices**, and that includes overriding a price PersonalClaw knows: whoever runs a model may
bill differently from its maker. The section lists every model your uses are bound to, and every
model spent on in the last 30 days, with the price its calls are counted at and where that comes
from: your price and the day you set it, the price its provider app declares, or PersonalClaw's
table, which names whose list price it is and the day it was recorded. A model nothing prices
says so. Under a price of yours it shows the default your price stands in front of, and **Reset
to default** brings that back. A price of $0 set for an instance's every model (`my-proxy:*`)
declares that instance free. A price is set for a `provider:model` ref, a pattern of them
(`anthropic:claude-*`), or a model id alone, which prices that model whoever serves it, this
machine included, in the unit the model is billed in (see above). A price counts from the next
call.

Your prices are part of your configuration, `config.json` in your PersonalClaw home, under
`model_prices`:

```json
{"model_prices": {"overrides": {
  "my-gpu:qwen3:8b": {"in_per_mtok": 0.2, "out_per_mtok": 0.4, "recorded": "2026-10-01"},
  "claude-sonnet-*": {"in_per_mtok": 3, "out_per_mtok": 15, "cache_read_per_mtok": 0.3}}}}
```

Prices set in an earlier release were kept in `model_rates.json`; the gateway carries them into
`config.json` the first time it starts (a price already there wins) and removes the file. If
`config.json` cannot be read, no price you set is in effect, the section says why, and it refuses
to save over the file until it is fixed.

**What to do instead, if you stay:** set the ceilings before you leave anything running,
and set a hard spend limit **at your provider** as the real backstop — that is the only cap
that can actually stop a charge. The zero defaults and the interactive gap are today's
state; the estimate being an estimate is permanent, because the authoritative number lives
in an account PersonalClaw has no access to.

---

## What this page is not

It is not the security threat model — that is
[threat-model.md](../security/threat-model.md), which maps each control to the OWASP
Agentic Top-10 with code citations. It is not a roadmap: nothing here is a commitment to
change, and two of the items (§3's no-hub decision and §10's no hosted service) are
permanent boundaries we expect to still be true at 1.0.

If something on this page has gone stale against the code, that is a bug worth filing —
the code is the authority, and a wrong limitations page is worse than none.
