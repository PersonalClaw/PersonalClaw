# PersonalClaw: a personal agent you can audit

**State:** draft, pending owner sign-off (DISCOVERABILITY-LAUNCH owner task 6). Not published.
Repo-relative links below must be rewritten to canonical `personalclaw.dev` URLs at publication
time; that rewrite and the site plumbing belong to the website atoms, not to this draft.

**Why this file is here and not at `src/content/blog/launch.md`.** DL-6 names that path, but it is
an Astro content path and no such collection exists in either repo: this repo has no `src/content/`
at all, and `personalclaw.dev/src/content.config.ts` defines a single `docs` collection (Starlight
`docsLoader`) with no `blog`. Publishing this post therefore needs a website-repo change — define a
`blog` collection, add its route and listing — which cannot land from the core repo. The prose lives
here, in the repo whose code every claim below is verified against; moving it under a website
collection is a website atom.

**How to read this post.** Every claim below names the file that proves it. If a claim has no
artifact next to it, it should not be here — and a few claims that started in the outline were
cut for exactly that reason. The list of what got cut is at the end, because it is the most
informative part.

---

## The pitch, in one paragraph

PersonalClaw is a self-hosted personal agent. It runs on your machine, talks to whichever model
provider you point it at, and keeps your sessions, memory, knowledge and config in a directory you
own. There is no account, no hosted control plane, and nothing to sign up for. It is
[MIT-licensed](../../LICENSE), needs Python 3.12 or newer, and is version 0.1.3
([`pyproject.toml`](../../pyproject.toml)).

That paragraph is easy to write and most projects write it. The rest of this post is the part
that is hard to fake: the mechanisms that make it true, and the places where it is not true yet.

## Receipt 1: the core does not know your provider's name

The design constraint is that vendor-specific logic lives in removable app bundles, and the core
stays provider-agnostic. The boundary and its deliberate in-core exceptions are written down in
[`docs/architecture/provider-boundary.md`](../architecture/provider-boundary.md) — including a
case study of moving one integration out of core, which is more useful than the rule itself.

The receipt is not the document. The receipt is that an app cannot reach past the SDK even by
accident: [`tests/test_apps_import_boundary.py`](../../tests/test_apps_import_boundary.py) is an
import lint asserting that installed apps only import `personalclaw.sdk.*`, with a per-file view
so a failure names the offending app file. Apps extend the system through 19 declared provider
types — `action`, `agent`, `channel`, `duty_gate`, `inbox`, `knowledge`, `memory`, `model`,
`notification`, `prompt`, `sandbox`, `search`, `skills`, `sync`, `task`, `tool`, `trigger`,
`trigger_source`, `workflow` — enumerated from `PROVIDER_TYPES` in
[`src/personalclaw/apps/manifest.py`](../../src/personalclaw/apps/manifest.py).

## Receipt 2: where the network is allowed to go

"No phone-home" is a sentence. A census is a control.

[`docs/architecture/network-egress-hosts.txt`](../architecture/network-egress-hosts.txt) lists
every routable hostname that appears as a literal in shipped code, and
[`tests/test_network_egress_hosts.py`](../../tests/test_network_egress_hosts.py) reds if a new
routable host literal shows up in core Python or the SPA and is not listed — and also reds on a
listed host that no longer appears, so the file cannot rot into decoration. Adding a destination
becomes a deliberate, reviewable act with a written justification attached.

The census file is candid about its own ceiling, and quoting it is better than paraphrasing it:
it is a census of destinations, not of payloads. It cannot tell you what was sent to an approved
host, and a new module reaching an already-listed host does not red the sweep. Static scanning
can answer "is there somewhere new it could go", which is the question a phone-home is; it cannot
answer "what data left".

### The runtime chokepoint, and the exact shape of its default

The census is a build-time control. The runtime one is a single function:
[`src/personalclaw/net/guard.py`](../../src/personalclaw/net/guard.py)`::evaluate`, which every
core fetch surface routes through, deciding against a declared
[`EgressPolicy`](../../src/personalclaw/net/policy.py). Its order is worth reading because the
order *is* the control: scheme allow-list, then the operator's `deny_hosts` (a deny always wins),
then the exclusive allow-list — both **before** DNS resolution, since a DNS query is itself an
egress signal — then resolve, classify each returned IP, block the private/loopback/link-local
ranges, and pin the resolved IP so the connection dials what was actually validated. An
unresolvable host fails closed.

Now the part a launch post is tempted to overstate. **The default posture is deny-by-default about
private ranges, not about destinations.** The default profile `STRICT`, and the `all` egress tier,
reach *every public host*; what they block is the LAN, loopback and metadata-address class of
target. An exclusive "only these hosts" stance exists only under the `listed` and `registry` tiers
(`allow_only=True`), and `policy.py`'s own comment records that before that flag existed the tier
plane "was decorative" — a 22-host registry preset reached the whole public internet exactly like
`STRICT`.

Two things narrow the claim further, and both are in the code rather than in the marketing:

- **`security.egress.allow_hosts` does double duty.** The same match populates
  `operator_allowed` in `guard.py`, which both waives the private-range block (the homelab
  LAN-webhook case) *and* satisfies the exclusive allow-list. Operator hosts are UNIONed onto a
  tier's preset by `egress_policy_for`. So a host you added once for a LAN webhook also widens a
  later `registry`-tier run, and "the registry tier reaches dev registries only" stops being true
  the moment you have any operator allow-list at all.
- **`on_violation: "warn"` audits and allows.** It is a documented operator escape hatch, so the
  chokepoint can be configured down into a logger.

There is also one narrow fail-open window, which the function's own comment names: if the egress
config has never been read successfully, the best-effort `except` returns the base profile and the
operator's `deny_hosts` do not apply. After one successful read the last-known deny list is
remembered at module scope specifically so a later read failure cannot un-deny a host. Denials only
ever grow; allowances are dropped on error. That is the right asymmetry, and it is still a window.

**And one destination is contacted without you asking.** At gateway start, and at most once every
12 hours ([`src/personalclaw/dashboard/handlers/updates.py`](../../src/personalclaw/dashboard/handlers/updates.py)),
PersonalClaw asks GitHub whether a newer release exists. The request carries a
product-identifying `User-Agent` of `personalclaw-update-check`
([`src/personalclaw/self_update.py`](../../src/personalclaw/self_update.py)) and, necessarily,
your IP. There is currently **no setting that suppresses it**: the `auto_update` config field
gates the unattended pull-and-restart, not the check.

So the accurate claim is: no analytics, no crash reporting, no usage telemetry, and one unprompted
release check to GitHub. A post claiming "zero telemetry, and you can turn off even the update
check" would be factually wrong today, so this post does not claim it.

One detail belongs here precisely because it looks bad on a grep and turns out to be fine. Your
PersonalClaw home contains a file called `telemetry_salt`, and `GET /api/status` returns an
`owner_id_hash` — an HMAC-SHA256 of your hostname and username
([`src/personalclaw/dashboard/handlers_system.py`](../../src/personalclaw/dashboard/handlers_system.py)).
That is exactly the shape of a pseudonymous analytics identifier. It is not one: it is a field in a
response served to your own dashboard by your own local gateway, there is no code that sends it
anywhere, and the salt is treated as a secret — excluded from sync shards, denied to packs, and
filed under `security` in `snapshot.py`. The name is aspirational for infrastructure that does not
exist; `handlers/core.py` says so in as many words ("Derived, not collected … no telemetry
infrastructure"). Grep it yourself rather than taking the sentence.

## Receipt 3: an audit trail that notices tampering

[`src/personalclaw/sel.py`](../../src/personalclaw/sel.py) is the Security Event Log: an
append-only JSONL record of tool and MCP actions, carrying the timestamp, the caller identity
(session key, agent, source interface), the operation, the resources touched, the outcome, and the
downstream MCP server where one applies. Each entry is signed with HMAC-SHA256 over the previous
entry's hash, so the chain is tamper-evident rather than merely append-only. Retention defaults to
365 days. It is a local file in your PersonalClaw home; nothing ships it anywhere.

## Receipt 4: which gates are gates, and which are only reports

This distinction is where most projects quietly overclaim, so here it is explicitly.

**A real merge gate.** [`.github/workflows/full.yml`](../../.github/workflows/full.yml) runs a
dedicated `security-corpus` job that exercises the adversarial corpus for PersonalClaw's own
app-install supply-chain scanner — five attack classes, the scanned-bytes-equals-installed-bytes
race invariant, and a meta-test that reds when the scanner is weakened. It runs
[`tests/security/`](../../tests/security),
[`tests/test_supply_chain_scanner.py`](../../tests/test_supply_chain_scanner.py) and
[`tests/test_supply_chain_gates.py`](../../tests/test_supply_chain_gates.py). It carries no
`continue-on-error`, so a regression fails the job. The methodology is published in
[`docs/security/scanner-testing.md`](../security/scanner-testing.md), including how the scanner is
tested for weakness rather than only for strength.

**Not a gate.** In the same workflow, the dependency scan — `pip-audit` plus `npm audit` — runs
under `continue-on-error: true` with `|| true`, and the workflow comment says why: visibility, not
a merge gate. A finding there does not block anything. If you read "we run pip-audit" elsewhere as
"vulnerable dependencies cannot merge", that inference does not hold here, and the code says so in
a comment rather than hiding it.

## Receipt 5: fail-closed, and precisely where

The gateway defaults to token auth — `LOCAL_TOKEN` in
[`src/personalclaw/auth/modes.py`](../../src/personalclaw/auth/modes.py); unauthenticated mode is
something you opt into, not something you forget to turn off. The credential store treats an
unreadable or malformed file as "no credential" rather than as a bypass
([`src/personalclaw/auth/credentials.py`](../../src/personalclaw/auth/credentials.py)). An app
backend that cannot obtain a verifiable secret does not start
([`src/personalclaw/apps/backend_runtime.py`](../../src/personalclaw/apps/backend_runtime.py)),
and an app-to-app message on an undeclared pair is a 403 plus a logged denial
([`src/personalclaw/apps/messaging.py`](../../src/personalclaw/apps/messaging.py)).

The most interesting case is the headless one-shot turn, `personalclaw run`, because its own module
docstring refuses the easy story. [`src/personalclaw/cli_run.py`](../../src/personalclaw/cli_run.py)
records that the read-only default is enforced by the session's **task mode**, which is
deny-by-default, runs before the approval gate and is documented as un-bypassable — and that the
approval gate itself is *not* a containment boundary there, because the headless profile's
fall-through is auto-approve. It also records why the obvious-looking field was not used:
`SafetyProfile.tool_grants` has no enforcement point in the tree today
([`src/personalclaw/guardrails/policy.py`](../../src/personalclaw/guardrails/policy.py)), so
relying on it would have shipped a read-only promise that denied nothing.

That is the shape of receipt worth trusting: a comment that tells you which of two plausible
mechanisms is actually holding the line, and names the one that would have been theatre.

## Receipt 6: the limitations page is a feature

[`SECURITY.md`](../../SECURITY.md) carries the reporting policy, and
[`docs/security/threat-model.md`](../security/threat-model.md) plus
[`docs/architecture/security.md`](../architecture/security.md) carry the model. But the page to
read first is [`docs/security/limitations.md`](../security/limitations.md), which enumerates what
PersonalClaw does **not** enforce yet:

1. Agents running under auto-approve rely on system-prompt framing, not on rails.
2. An app's `network` permission is **declaration-only** — it is disclosure at install time, not
   per-app egress isolation.
3. App Python dependencies install into the same virtualenv the gateway runs from.

Each of those is a real constraint on what you should let this software do unattended today. They
are listed, with reasons, rather than deferred to a changelog nobody reads.

## The honest limitations paragraph

PersonalClaw is pre-1.0 and the [README says so in a badge](../../README.md): upcoming 0.x releases
may introduce breaking changes **with no automatic migration** of your sessions, memory, knowledge,
config or app state. That is a deliberate choice, not an oversight — migration-backed change
discipline binds once the architecture stops moving, and gating a half-built architecture is worse
than breaking it honestly now. Take a snapshot before upgrading, and read release notes.

Several subsystems are floors rather than finished features: they hold the contract and pass their
tests without yet covering everything the name suggests. The three non-enforcements above are the
security-relevant instances, and the egress census's destination-not-payload ceiling is another. If
you need per-app network isolation, dependency isolation between apps, or enforced rails around an
auto-approving agent, PersonalClaw does not have those today and this post is not going to imply
otherwise.

The egress chokepoint needs its own sentence, because "all network access goes through one guarded
function" is the kind of claim a reader will reasonably assume from Receipt 2, and it is not what
the code says. `net/guard.py` governs **core's** fetch surfaces. An installed app's backend is its
own OS process with its own network stack, so its outbound traffic never reaches the guard at all —
[`docs/security/threat-model.md`](../security/threat-model.md) lists an app's own network traffic
under what PersonalClaw deliberately does not defend against, and the `network` permission is
disclosure at install consent rather than a boundary. What is enforced for apps is the supply-chain
scan on what you install and the gateway-mediated `api` reach. Alongside that: the default posture
reaches any public host, one operator allow-list entry widens every exclusive tier that later run
uses, `on_violation: "warn"` demotes the guard to an audit log, and operator denials do not apply
until the egress config has been read successfully once. Each of those is defensible in isolation;
together they mean the honest summary is "a real chokepoint with a configurable ceiling, covering
core and not apps", not "nothing leaves without passing the guard".

The threat model is also explicit that a compromised host OS or account, physical access to an
unlocked machine, tampering with the installed package before startup, and the owner's own
auto-approve choices are all outside the model. Those are scope statements, not bugs — but a reader
deciding whether to trust this software unattended should read them as limits, because that is what
they are.

## How the work is checked

The roadmap is executed as small atoms, each with a written execution log in
[`docs/roadmap/plans/`](../roadmap/plans). Those logs are unusual in one respect: they record what
the session tried to **disprove**, not only what it built — along with deviations, adjacent problems
found and deliberately not fixed, and blocks with reasons. Two entries in the
DISCOVERABILITY-LAUNCH log alone exist mainly to record that an atom was *not* buildable and why.
As of commit `5283468b`, 54 of the 70 plan files contain such a falsification note
(`git grep -l -i falsif -- docs/roadmap/plans/ | wc -l`).

Worth stating plainly: that is a project convention visible in the logs, not a rule written into
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) or [`AGENTS.md`](../../AGENTS.md). Those files require
the deviations ledger; the falsification habit is stronger in practice than in policy.

## Who should not use this yet

If you want a managed product with migrations, a support channel and a stability guarantee, wait.
If you want a personal agent whose boundaries you can read, whose network destinations are
enumerated and test-enforced, and whose unfinished edges are written down where you can find them
before they surprise you — that is what is being offered, at 0.1.3, honestly labelled.

Start at [`README.md`](../../README.md); the CLI surface is in
[`docs/reference/cli.md`](../reference/cli.md); the intent behind the architecture is in
[`docs/vision.md`](../vision.md).

---

## Claims cut from this draft, and why

Kept in the draft file on purpose: the next person to edit this post needs to know which
attractive sentences are unsupported.

- **"Zero telemetry."** Cut to "no analytics, no crash reporting, no usage telemetry, plus one
  unprompted release check". The GitHub release check is real, is unprompted, carries an
  identifying `User-Agent`, and has no off switch.
- **"You can disable the update check."** Cut entirely. `auto_update` gates the apply, not the
  check.
- **"Vulnerable dependencies cannot merge" / "we gate on pip-audit."** Cut. Those scans are
  report-only by explicit design.
- **"Fail-closed approval in headless runs."** Cut and replaced with the task-mode mechanism.
  Headless approval falls through to auto-approve; claiming approval as the boundary would name
  the wrong control.
- **"Sandboxed apps" / "per-app network policy."** Cut. `docs/security/limitations.md` says the
  `network` permission is declaration-only and app dependencies share the gateway's virtualenv.
- **"Our contributor guide mandates falsification."** Softened to a convention observed in the
  logs, because neither `CONTRIBUTING.md` nor `AGENTS.md` uses the word.
- **A comparison against named competitors.** Omitted. No peer set has been chosen, and naming
  peers is positioning, not implementation.
- **"All network access goes through one guarded chokepoint."** Cut. `net/guard.py::evaluate` is a
  real chokepoint for core's fetch surfaces, but an app backend is a separate OS process with its
  own network stack and never passes through it.
- **"Egress is deny-by-default."** Cut as written. It is deny-by-default about private/loopback/
  link-local ranges; the default profile reaches any public host. Only the `listed` and `registry`
  tiers are an exclusive allow-list.
- **"The registry egress tier reaches package registries only."** Cut. `egress_policy_for` unions
  the operator's `security.egress.allow_hosts` onto the tier preset, and `guard.py` uses one match
  for both the private-range waiver and the exclusive allow-list — so any operator entry widens the
  tier.
