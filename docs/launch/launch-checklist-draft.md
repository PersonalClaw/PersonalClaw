# PersonalClaw launch checklist (P0 gate + posting drafts)

**State:** draft, internal, not published. This is the operational gate and posting plan for the
public launch (DISCOVERABILITY-LAUNCH atom `DL-8`, tasks T4.2+T4.3). It gates the listing
submissions drafted in [`listing-submissions-draft.md`](listing-submissions-draft.md) and the
launch post drafted in [`launch-post-draft.md`](launch-post-draft.md). Nothing here posts anything;
posting is an owner action (owner task 6 in
the source plan).

**Why this file is in core `docs/launch/` and not the site repo.** The `DL-8` deliverable column
names `launch-checklist.md` in the site repo, marked "(internal)". It lives here for the same
reason [`launch-post-draft.md`](launch-post-draft.md) does: every P0 gate item below links to its
**proof**, and every proof is a core-repo surface — a CI workflow, the install path and its guides,
the screenshot set, the README badges. Core is the repo where those links resolve and where the
docs-lint gate verifies they still resolve, so a moved or deleted proof reds CI instead of rotting
into a checklist that lies. The checklist is internal tooling, not a published marketing page, so
no site route is required to make it useful.

**How to read this.** Each gate item states the condition, then links the artifact that proves it.
"Green" means the linked proof is verifiable now. **No owner posting happens until every P0 item is
green.** The posting sequence and the three community post drafts follow the gate.

---

## The P0 launch gate

The plan's §Listings makes the posting **gated on the P0 gate: CI green, the install one-liner
works, real screenshots are live**. Those are the three blocking items; the release-integrity and
front-door items below them are also blocking because a launch reader hits them in the first minute.

### P0-1 — CI is green on `main`, and the badge proves it

- **Condition:** the latest `main` run of the CI workflow is passing, and the README badge points at
  it.
- **Proof:** the CI badge row in [`README.md`](../../README.md) links to
  [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml); the full gate lives in
  [`.github/workflows/full.yml`](../../.github/workflows/full.yml). Locally the same gate is
  `make gates` / `make lint` / `make test`.
- **Check:** open the live badge —
  [CI workflow runs](https://github.com/PersonalClaw/PersonalClaw/actions/workflows/ci.yml) — and
  confirm the head-of-`main` run is green. A red or stale badge blocks the launch; this is the
  `EXT:CI-RELEASE:green main + CI badge` dependency the atom declares.

### P0-2 — the install one-liner works on a clean machine, with a captured log

- **Condition:** a first-time user can install and boot the dashboard from the documented one-liner
  on a clean OS, and the run is captured as a log artifact (the `EXT:DISTRIBUTION:install one-liner
  verified working` dependency).
- **Proof:** the Quickstart in [`README.md`](../../README.md) and
  [Getting started](../guides/getting-started.md) document both paths —
  `uv tool install personalclaw && personalclaw setup` and the bootstrap
  [`curl -fsSL https://personalclaw.dev/install | sh`](https://personalclaw.dev/install); release
  artifacts and their smoke are built by
  [`.github/workflows/release.yml`](../../.github/workflows/release.yml); per-platform reality is in
  [Platforms](../guides/platforms.md) and [Containers](../guides/containers.md).
- **Check (produces the log artifact):** on a throwaway VM or container matching a
  [supported platform](../guides/platforms.md), run the one-liner, then `personalclaw setup` and
  `personalclaw gateway`, and confirm the dashboard serves at `http://localhost:10000`. Capture the
  full terminal session; that transcript is the "install log" proof the launch links from. Redo it
  whenever the pinned release or the install script changes.

### P0-3 — the screenshots are real, current, and live

- **Condition:** the screenshot set renders on real (non-personal) data and is referenced from the
  README and the site.
- **Proof:** [`docs/screenshots/`](../screenshots/CAPTURE.md) ships light and dark sets — e.g.
  [dashboard, light](../screenshots/light/01-dashboard.png) and
  [knowledge, dark](../screenshots/dark/03-knowledge.png) — reproducibly via the capture procedure
  in [CAPTURE.md](../screenshots/CAPTURE.md); they are embedded in [`README.md`](../../README.md)
  and the [visual showcase](../../SHOWCASE.md). They are captured on the `demo-home` seed fixture
  (atom `DL-4`, done), never on owner data.
- **Check:** confirm the images embedded in the README and [`SHOWCASE.md`](../../SHOWCASE.md) render
  on personalclaw.dev, and that the demo-home timestamps were refreshed at capture time (CAPTURE.md
  notes they are static and drift as the fixture ages). The 60-90s launch GIF is a **separate** item
  (atom `DL-5`, owner-driven) and is **not** a P0 blocker for the text listings.

### P0-4 — release integrity is real, not asserted

- **Condition:** the published release carries the supply-chain evidence the project claims.
- **Proof:** the "Supply chain" section of [`README.md`](../../README.md) — builds from the committed
  `uv.lock`, PyPI Trusted Publishing (OIDC, no stored tokens) behind a manual owner-approval gate, a
  syft SBOM and build-provenance attestations on the wheel and images — is produced by
  [`.github/workflows/release.yml`](../../.github/workflows/release.yml). The adversarial supply-chain
  scanner corpus that actually gates merges is the `security-corpus` job in
  [`.github/workflows/full.yml`](../../.github/workflows/full.yml), with methodology in
  [scanner-testing.md](../security/scanner-testing.md).
- **Check:** confirm the launched version tag has its SBOM and attestations attached, and that the
  version in [`README.md`](../../README.md) / [`pyproject.toml`](../../pyproject.toml) matches the
  tag being announced.

### P0-5 — the front door reads honestly in 60 seconds

- **Condition:** the README, security surface, and honesty caveats are current, so a launch reader is
  not misled.
- **Proof:** [`README.md`](../../README.md) carries the pre-1.0 breaking-changes banner and the
  accurate privacy statement; [`SECURITY.md`](../../SECURITY.md), the
  [threat model](../security/threat-model.md), the [security model](../architecture/security.md), and
  the [limitations page](../security/limitations.md) are current; the network story is the
  test-enforced [egress host census](../architecture/network-egress-hosts.txt).
- **Check:** the privacy claim must read as it does in the README today — **"no analytics, no crash
  reporting, no usage telemetry, plus one unprompted release check to GitHub"**, with the note that
  there is **no off switch for that check yet**. See "Honesty rails" below; this phrasing is
  load-bearing across all three post drafts.

---

## Posting sequence and discipline

1. **Gate first.** Every P0 item above is green. If any is red, stop.
2. **Listings before threads.** Open the passive listings (see
   [`listing-submissions-draft.md`](listing-submissions-draft.md)) first — they are lower-stakes and
   let you confirm the copy reads well before a high-traffic thread. Note the awesome-self-hosted
   eligibility gate in that file (first release must be ≥4 months old) may defer that one submission.
3. **Owner posts personally, one platform at a time.** Community norms on all three targets favor the
   author posting, not a proxy. Post to one venue, watch it, then decide on the next; do not fan out
   simultaneously. Owner task 6 owns the timing.
4. **Be present for the first hours.** Answer questions plainly, link the proof, and correct any
   misreading of the privacy posture immediately. Do not argue; where a limitation is real, point at
   [limitations.md](../security/limitations.md).
5. **Disclose authorship** in every thread (HN and both subreddits expect it).

## Channel evidence (measured 2026-09-17/18) — read before re-ordering the sequence above

The sequence above is fenced by measurement, not intuition. A seven-peer pass compared each peer's
**fork-count inflection** against every dated channel event within ±30 days of it. Three findings, with
their numbers, so a future editor can re-derive them instead of re-guessing.

> **Read every line below as CO-OCCURRENCE, never causation.** The instrument can place a channel
> event beside an inflection on a calendar; it cannot show the event produced the inflection, and for
> 6 of the 7 peers the co-occurrence is causally empty. Nothing here licenses "channel X grew peer Y."
>
> **These peer names are planning evidence and must not travel into any public draft** — the pre-1.0
> name-scrub ruling in *Honesty rails* below still governs every posted word.

**(a) Directory presence LAGS traction — it does not lead it.** Of the 5 peers listed on
awesome-self-hosted, 4 were added months to years *after* their own fork inflection; only one was
added while it was ramping:

| Peer | Fork inflection | Added to the list | Delta |
|---|---|---|---|
| dify | 2023-05-15 | 2023-12-27 | **+7mo** |
| open-webui | 2023-12-05 | 2025-04-17 | **+16mo** |
| anything-llm | 2023-06-08 | 2025-06-13 | **+2yr** |
| LibreChat | — | later still | later still |
| n8n | 2019-10-08 | 2019-10-18 | +10d (the only near-simultaneous one) |

So a directory row is evidence a project *already* had traction, not a mechanism that supplies it.

**(b) Where a channel event does co-occur, ENGAGEMENT is the variable — and only one peer has any.**
The single engagement-backed co-occurrence in the set is **n8n's 728-point / 196-comment Show HN**
(`21191676`, 2019-10-08), landing exactly on its ramp day. Every other "hit" is a **2–23-point** post,
and for two of them the ramp was **already climbing before** the post went up:

| Peer | Post | Points/comments | Offset from inflection |
|---|---|---|---|
| n8n | `21191676`, 2019-10-08 | **728**/196 | 0d |
| open-webui | `38236857`, 2023-11-12 | 23/1 | −8d |
| langflow | `35168096`, 2023-03-15 | 6/1 | +0–1d |
| dify | `36053688`, 2023-05-24 | 4/1 | +9d — ramp began 05-15, *before* the post |
| Flowise | `35579504`, 2023-04-15 | 2/0 | +2–3d — ramp began 04-12, *before* the post |

A 728-point reception is not an action a 4-star project can take; it is an outcome. Treat the
low-point rows as the realistic prior for our own post.

**(c) The consequence for sequencing.** The directories open to us right now (selfh.st, AlternativeTo,
awesome-ai-agents) buy **presence, not traction** — list them because being findable is worth having,
not because listing is expected to move adoption. And **no awesome-list row may be sequenced before its
own calendar gate**: the awesome-self-hosted submission stays held until **2026-11-23** on its 4-month
age gate (the date is timezone-ambiguous — see
[`listing-submissions-draft.md`](listing-submissions-draft.md) §1 before touching it), and no ordering
in this checklist may place it earlier.

**What this evidence cannot do:** rank channels by contribution. Only one of seven peers has an
engagement-backed co-occurrence, so any ranking would be manufactured. Two peers (anything-llm,
LibreChat) have no channel event near their inflection at all.

## Honesty rails (apply to all drafts below)

- **Not "zero telemetry."** Use "no analytics, no crash reporting, no usage telemetry, plus one
  unprompted release check to GitHub (no off switch yet)." The release check is real and unprompted;
  claiming otherwise is a factual defect. Grounded in [`README.md`](../../README.md) and
  [`launch-post-draft.md`](launch-post-draft.md).
- **No named competitors.** No peer/comparison product names in any public post — the standing
  pre-1.0 name-scrub ruling (see the `DL-7` entries in
  the source plan).
- **Pre-1.0, breaking changes expected.** Say so, matching the README banner: 0.x updates may break
  state with no automatic migration; snapshot before upgrading.
- **The egress chokepoint covers core, not apps.** Do not imply "nothing leaves without passing the
  guard" — an installed app's backend is its own process. See [limitations.md](../security/limitations.md).

---

## Post draft — Show HN

Format per [Show HN guidelines](https://news.ycombinator.com/showhn.html): something people can run,
plainly described, author present in the thread.

**Title:**

```
Show HN: PersonalClaw – a self-hosted personal AI agent you can audit
```

**Body:**

```
I built PersonalClaw, a self-hosted personal AI agent that runs on your own machine: agentic
chat with tool-approval controls, autonomous goal loops under a deterministic supervisor,
layered long-term memory, a document knowledge base, reusable skills, and cron/webhook
automation — all behind one gateway process and one web dashboard. State lives in a directory
you own; there is no account and no hosted control plane.

It is provider-agnostic: every model provider, search, speech, channel, and agent runtime is a
removable app, so you can point it at a hosted API or a local model without touching the core.
MIT-licensed, needs Python 3.12+.

On privacy, the honest version: no analytics, no crash reporting, no usage telemetry — plus one
unprompted release check to GitHub at startup (identifying User-Agent, no usage data), which has
no off switch yet. The network destinations that appear in shipped code are enumerated in a file
and a test reds if a new one shows up.

It is pre-1.0 (v0.1.3). Expect breaking changes with no automatic state migration on 0.x updates
— snapshot before upgrading. The security model, and the things it deliberately does not enforce
yet, are written down rather than glossed over.

Repo: https://github.com/PersonalClaw/PersonalClaw
Site + docs: https://personalclaw.dev

Happy to answer questions.
```

## Post draft — r/selfhosted

Norms for [r/selfhosted](https://www.reddit.com/r/selfhosted/): disclose you're the author, lead with
what it does and how to run it, be candid about the network posture.

**Title:**

```
PersonalClaw: a self-hosted, provider-agnostic personal AI agent (chat, goal loops, memory, knowledge, automation) — MIT, author here
```

**Body:**

```
Author here. PersonalClaw is a self-hosted personal AI agent you run on your own box behind one
gateway + web dashboard. What it does:

- Agentic chat with tool use and approval controls
- Autonomous goal loops (plan → run → cycle) under a supervisor you can pause/nudge/stop
- Layered long-term memory with an optional Obsidian-compatible markdown vault
- A knowledge base (PDF/DOCX/HTML/…) with entity graph and semantic search wired into chat
- Skills + a permission-gated app Store with supply-chain scanning
- Cron/interval/webhook automation and an inbox that watches channels

All state is under one directory on your machine; it degrades to local-only and never needs the
network for core operation. Every vendor (model provider, search, speech, channels) is a
removable app, so nothing ties you to one LLM provider.

Network honesty, since this sub cares: no analytics, no crash reporting, no usage telemetry —
plus one unprompted release check to GitHub at startup (no usage data, but it is a request, and
there is no off switch for it yet; block egress if you need zero outbound). The hostnames that
appear in shipped code are enumerated in a committed file with a test that reds on a new one.

Install (uv brings Python 3.12):

    uv tool install personalclaw && personalclaw setup
    personalclaw gateway
    # dashboard at http://localhost:10000

Docker Compose and other paths are in the README. It is MIT and pre-1.0 (v0.1.3) — 0.x updates
may break stored state with no auto-migration, so snapshot before upgrading. Screenshots (light
and dark) and the full docs are on the site.

Repo: https://github.com/PersonalClaw/PersonalClaw
Site: https://personalclaw.dev
```

## Post draft — r/LocalLLaMA

Norms for [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/): lead with the local-model / no-cloud
angle, disclose authorship, keep it technical.

**Title:**

```
PersonalClaw: a provider-agnostic personal agent that runs against a local model as easily as a hosted one (self-hosted, MIT)
```

**Body:**

```
Author here. PersonalClaw is a self-hosted personal AI agent whose core does not know your
provider's name — model providers are removable apps, so you can bind a local model or a hosted
API and swap either without touching the core. If you run models locally, it means the agent
layer (chat, autonomous goal loops, long-term memory, a knowledge base, skills, automation) sits
on top of whatever you already serve.

It runs behind one gateway + web dashboard, keeps all state in a directory you own, and needs no
account. On outbound traffic: no analytics, no crash reporting, no usage telemetry — the one
unprompted call is a release check to GitHub at startup (no off switch yet), and the network
destinations in shipped code are enumerated with a test that reds on a new one, so pointing it at
a purely local stack is a first-class path.

MIT, Python 3.12+, pre-1.0 (v0.1.3) — expect breaking changes with no auto state migration on 0.x,
snapshot before upgrading. I would especially value feedback from people running local models on
which provider/runtime apps are worth prioritizing.

    uv tool install personalclaw && personalclaw setup && personalclaw gateway

Repo: https://github.com/PersonalClaw/PersonalClaw
Site: https://personalclaw.dev
```

---

## What is deliberately NOT in these drafts, and why

Kept here on purpose so the next editor does not "helpfully" add them back:

- **"Zero telemetry."** Cut in favor of the accurate phrasing above. The GitHub release check is
  real, unprompted, and has no off switch yet.
- **A comparison to any named product.** Omitted — the pre-1.0 name-scrub ruling (`DL-7`).
- **"All network access goes through one guarded chokepoint."** Cut — the guard covers core's fetch
  surfaces, not an installed app's own process. See [limitations.md](../security/limitations.md).
- **A demo GIF as a launch blocker.** The 60-90s capture is atom `DL-5` (owner-driven) and is not a
  P0 gate item for the text listings; the still screenshots are.
- **Any owner name.** The posts are authored in the first person and posted by the owner personally;
  no name is written into the drafts.

## Status

`DL-8` stays `todo` in DL.md / `dag.json`: this file and
[`listing-submissions-draft.md`](listing-submissions-draft.md) are the *drafts* the `done_when`
requires, but the atom also requires the gate to be all-green and the owner to actually submit and
post — none of which an implementation session can complete. Flip the atom only when the submissions
are open and the owner has posted.
