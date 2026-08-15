# External Review Scope — Five High-Risk Paths

The [threat model](threat-model.md) claims that PersonalClaw's controls are
enforced at chokepoints rather than requested in a prompt, and cites a module for
every "enforced" row. A claim that cites its own code is auditable; it is not yet
audited. This document scopes that audit: which five paths a reviewer is asked to
attack, how a finding is written down, and what gets published when.

The five paths are not a survey of the codebase. They are the crossings where a
single mistake converts an *outside* input into *owner* authority — the ones where
the blast radius is the whole machine, because the agent that runs there can run
commands on it.

**Scope owner:** the maintainer. **Review format and publication plan below are
binding on the review**; the path list is the reviewer's starting point, not a
fence — a finding outside these five is in scope if it crosses a boundary
[`SECURITY.md`](../../SECURITY.md) claims is held.

## What is out of scope, and why

This is a single-owner, self-hosted 0.x tool. The review inherits
`SECURITY.md`'s scope section verbatim, which means these are **not** findings:

- **Owner-lowered guardrails.** Auto-approve (YOLO), a permissive task mode, or a
  hand-edited config are decisions, not vulnerabilities. "The owner can disable
  the control" is the design.
- **An already-compromised host.** Root on the box, a compromised OS account, or
  physical access sit *below* every boundary here. So does editing the installed
  package before the process starts — see the threat model's
  [baseline denylist integrity](threat-model.md#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner)
  section, which states that limit rather than papering over it.
- **Surfaces already documented as declaration-only.** The app `network`
  permission is disclosure, not containment, and
  [`limitations.md`](limitations.md) says so at length. Demonstrating that an app
  can reach the network confirms the documentation; it does not find a bug.
- **Hardening requests.** "Add control X" is welcome as an issue. It is not a
  finding, and it will not be published as one.

Two of the five paths are also *deliberately* incomplete today, and the review
should treat them as design review rather than exploitation targets where the
control has not landed. Each says so in its own section.

## The five high-risk paths

Every module path below is relative to `src/personalclaw/`. Entry points are
named as `module.py::symbol` so they resolve by grep.

### 1. Webhook authentication — the one unauthenticated-by-default door

**What it is.** Inbound webhooks let an outside system poke the gateway without
an owner session. `dashboard/handlers/hooks.py::_verify_hook_token` is the only
thing standing between an arbitrary HTTP request and a trigger that can start an
agent turn.

**Why it is high-risk.** This is the shortest path from "anyone on the network"
to "the agent runs". A bypass here does not leak data — it *executes*. The
concrete bad outcome: an unauthenticated request fires a trigger, the trigger
starts a turn, and the turn runs tools under the owner's authority. Everything
else in the threat model assumes the request on the other side of it was the
owner's.

**Entry points.** `dashboard/handlers/hooks.py` (`_verify_hook_token` and every
handler that calls it), plus the trigger surfaces it hands off to
(`triggers/`, `trigger_sources/`).

**What a reviewer should try to break.**

- Reach any hook handler that does *not* call `_verify_hook_token`, or calls it
  after a side effect has already happened.
- Defeat the comparison: timing, type confusion (a non-string token), an empty or
  whitespace token, a token supplied in an unexpected place (query string, second
  header, body) that a framework normalises into the accepted one.
- Confirm the fail-closed claim: with **no** token configured, every request must
  be refused. Look for a code path where "unconfigured" degrades to "open" —
  including startup ordering, a config reload, or a first-run state.
- Verify the denial is *audited*. A refused request that leaves no Security Event
  Log entry is a finding in its own right: it makes the door silent.

### 2. App reverse-proxy token model — privilege narrowing that must never widen

**What it is.** An installed app's backend is proxied through
`dashboard/handlers/apps.py::api_app_proxy`, which strips the owner's
cookie/Authorization and injects a fresh short-TTL app-scoped token minted by
`dashboard/token_auth.py::generate_token` with an `app` claim. The permission
middleware then re-adopts that claim via
`dashboard/token_auth.py::validate_token_with_app`. The whole model rests on one
invariant: **an app token only ever narrows reach, never widens it.**

**Why it is high-risk.** An app is third-party code the owner installed. If a
token minted *for* an app can be replayed *as* the owner, the app-boundary
disappears and a benign-looking app owns the machine. The concrete bad outcome:
an app backend obtains the owner's credentials, or an app-scoped token reaches an
`/api` path outside the app's declared permissions — both explicitly in scope in
`SECURITY.md`.

**Entry points.** `dashboard/handlers/apps.py::api_app_proxy`,
`dashboard/token_auth.py` (`generate_token`, `validate_token_with_app`, and the
middleware that consumes them), `dashboard/server.py::_dev_user_middleware`,
`apps/permissions.py`.

**What a reviewer should try to break.**

- Find a request shape where the owner's credential survives the strip — a header
  case variant, a duplicated header, a redirect the proxy follows, a WebSocket
  upgrade, a streamed body, a trailer.
- Escalate an `app`-claimed token: forge or mutate the claim, drop the claim so
  the token validates as an owner token, replay one app's token against another
  app's routes, or find an `/api` handler that authenticates but never consults
  the app claim.
- Attack the TTL: is `MAX_SESSION_TTL_SECS` actually the ceiling on every mint
  path? Does a refresh path extend an app token beyond it?
- Attack `none` auth mode specifically. The threat model claims the permission
  middleware holds even there. Try to make `_dev_user_middleware` adopt a *wider*
  identity than the presented token.
- Try the proxy as an SSRF primitive: can the app-name or path segment steer the
  proxied request at something other than that app's backend?

### 3. Supply-chain scanner bypasses — scanned bytes must be installed bytes

**What it is.** Installable content is staged in quarantine, scanned there, and
moved into place only on a passing verdict
(`apps/app_manager.py::install`; `supply_chain.py` with `SkillScanner`,
`Verdict`, `TrustTier`). The `dangerous` verdict is terminal and
non-overridable. The design claim is that there is no time-of-check /
time-of-use gap: the scanned tree *is* the installed tree.

**Why it is high-risk.** This gate is the only thing that vets code the owner is
about to import into the gateway process. An app's provider code runs
**in-process**; a bypass here is arbitrary code execution with the gateway's
authority, and it is *persistent*, surviving restarts. The concrete bad outcome:
content the scanner rated `dangerous` gets installed anyway, or content mutates
between scan and install.

**Entry points.** `apps/app_manager.py::install`, `supply_chain.py`,
`skills/`, `apps/catalog.py`, `packs/`.

**What a reviewer should try to break.**

- Win the TOCTOU race the design claims not to have: a symlink, a hardlink, an
  archive entry that escapes the quarantine root (`../`, absolute paths, a
  symlink whose target is created later), a second writer to the staging dir.
- Make the scanner miss: obfuscation, dynamic import, a payload in a data file
  the scanner does not read, a file type it skips, an encoding it mis-decodes, a
  size threshold above which it gives up.
- Make `dangerous` non-terminal: an error path that turns a terminal verdict into
  a warning, an exception in the scanner that fails **open**, a `TrustTier` that
  relaxes strictness further than intended.
- Attack the update path, not just install. `POST /api/apps/{name}/update` is a
  second way bytes arrive; verify it goes through the same gate.
- Check that a rejected install leaves nothing behind — no partial tree, no
  registered app, no importable module.

### 4. Egress guard layering — one chokepoint, or several bypasses

**What it is.** All outbound HTTP is supposed to funnel through one seam:
`net/client.py`, guarded by `net/guard.py`, with policy layered by
`net/policy.py::egress_policy_for` (composed from
`egress_policy_for_tier` / `egress_policy_for_profile`).

**Why it is high-risk.** This is the exfiltration boundary. An agent that has
read the owner's files, memory, and credentials needs exactly one unguarded
outbound call to publish them. The concrete bad outcome: a request that reaches
an arbitrary host without a policy decision — or with the *wrong* layer's policy,
which is worse, because the audit log will show a decision that was never
enforced.

**Why layering specifically.** Three functions compose the effective policy.
Composition order is where this class of control fails: a "narrow" tier policy
that is merged *under* a broad base, a profile that widens rather than
intersects, or a default that applies when no tier matched.

**Entry points.** `net/client.py`, `net/guard.py`, `net/policy.py`, and
critically the modules that *should* use them — search for direct `httpx` /
`requests` / `urllib` / `socket` use outside `net/`, in
`providers/`, `search_providers/`, `channel_transports/`, `sync_transports/`,
`browse/`, `local_models/`, `uploads/`.

**What a reviewer should try to break.**

- Find one outbound call that does not go through `net/client.py`. A single
  direct `httpx.get` in a provider is the whole finding.
- Make the composed policy wider than any of its inputs: pick a tier/profile pair
  where `egress_policy_for` returns something broader than the base. Prove it by
  a request that the base alone would have denied.
- DNS and redirect handling: does the guard decide on the *hostname the owner
  named*, or on the address actually connected to? Try a redirect to an internal
  address, a hostname resolving to loopback or link-local, an IPv6-mapped form.
- Exfiltrate via a *permitted* host: a URL path or query on an allowed domain
  that carries the payload. The threat model's redaction
  (`security.py::redact_exfiltration_urls`) is the intended mitigation — test it.
- Confirm the guard fails **closed**. An exception inside policy evaluation must
  not result in a request being sent.

### 5. Inbound MCP and remote-access surfaces — a boundary still being built

**What it is.** The read-only inbound tool surface: `inbound/mcp_http.py`
mounts `/mcp`, gated by `inbound/auth.py` (`peer_allowed`, `verify_bearer`) and
`inbound/caps.py` (`check_rate`, `acquire_slot`, `clamp_items`, `clamp_text`),
audited via `inbound/audit.py`, exposing `inbound/tools.py`.

**Why it is high-risk.** It is a second front door, and unlike webhooks it hands
the caller *tools*. It is also the newest of the five, and the threat model's
ASI07 row is still `in progress` — broad external access is owned by plans not
yet landed. Reviewing it now is cheaper than reviewing it after it calcifies.
The concrete bad outcome: a non-owner caller drives tools on the owner's machine,
or read-only turns out to be read-mostly.

**Deliberate limitation to review, not exploit.** Full external access and
inbound fencing are not claimed as enforced today. Findings here are most
valuable as *design* findings against the surface as mounted — do not report the
absence of an unlanded control as a vulnerability.

**Entry points.** `inbound/mcp_http.py::mount` and its request handler,
`inbound/auth.py`, `inbound/caps.py`, `inbound/tools.py`,
`inbound/audit.py`, and the mount site in `dashboard/server.py`.

**What a reviewer should try to break.**

- Forge the peer. `peer_allowed` is documented as gating on the transport peer,
  never a forgeable header — test `X-Forwarded-For`, `Forwarded`, a proxy in
  front, an IPv6 form, a unix-socket path.
- Get past `verify_bearer`: an unconfigured-token state that opens instead of
  refusing, a forbidden placeholder value that is nonetheless accepted, a timing
  signal.
- Prove a tool in `inbound/tools.py` is not actually read-only — any side effect,
  any state write, any path that reaches an action provider.
- Defeat the caps: exhaust slots to deny the owner service, evade `check_rate`
  with a client key you control, pass a payload that `clamp_items` /
  `clamp_text` mis-measures.
- Confirm the mount fails **closed**. The mount site swallows exceptions so an
  inbound fault cannot block startup — verify that a failed mount leaves no
  half-registered, unguarded route.

## Review format

Both formats below are credible; the maintainer picks one (see
[Approval and schedule](#approval-and-schedule)). The finding format,
evidence bar, and dispute rule are **identical for both** — that is the point of
writing them down before the review starts.

### Format A — commissioned review

An independent security practitioner is engaged for a time-boxed review of the
five paths. Deliverable: one report, findings in the format below, plus a
statement of what they *did not* get to. The reviewer's name or firm appears only
with their written consent.

### Format B — structured public self-audit

The maintainer works the five paths adversarially and publishes the result under
the same format, with one extra obligation: **each path gets an explicit
"attempted and failed to break" list**, not just the findings. A self-audit's
credibility comes entirely from what it admits it could not do, so the honest
negatives are the deliverable. It must also name a reason for each path where the
attempt was shallow.

A self-audit does not claim independence and must not be presented as one. The
published report says which format produced it, in the first paragraph.

### How a finding is reported

One finding per issue, in this shape:

| Field | Content |
|---|---|
| **ID** | `RS-<n>`, assigned in report order |
| **Title** | The crossing, in one line: what reaches what |
| **Path** | Which of the five (or "outside scope list") |
| **Affected code** | `module.py::symbol` for every module on the path — resolvable by grep, not prose |
| **Severity** | One of the four levels below |
| **Impact** | The concrete bad outcome, as a sentence a user would care about |
| **Reproduction** | Ordered steps from a clean install to the outcome |
| **Evidence** | See the evidence bar below |
| **Version** | Release tag or commit SHA observed |
| **Suggested direction** | Optional. Non-binding — the fix is the maintainer's design call |

**Severity is graded by the boundary crossed, not by a numeric score.** The
threat model defends five boundaries; severity says which one moved.

- **Critical** — a non-owner input reaches code execution or the owner's
  credentials. Paths 1, 2 and 3 are the ones that can produce this.
- **High** — a boundary the threat model claims `enforced` is crossed, without
  reaching execution: an app token widening, a policy layer bypassed, a
  `dangerous` verdict evaded without code running.
- **Medium** — a control is enforced but its *evidence* fails: a denial that is
  not audited, an SEL chain that verifies when it should not, a redaction that
  leaks. The control held; the record lied.
- **Low** — a documented claim is wrong in a way that misleads a user, with no
  boundary crossed. Includes a `threat-model.md` citation that no longer
  resolves.

A finding whose severity depends on auto-approve being enabled, or on a
compromised host, is **not a finding** — see the out-of-scope section. Say so and
move on; do not downgrade it to Low.

### The evidence bar

A finding is accepted when someone other than its author can reproduce it. Two
things are required, and one is preferred:

1. **Required — a mechanical reproduction.** A failing test, a script, or an exact
   request. Prose alone ("the token check looks weak") is a question, not a
   finding, and is answered as a question.
2. **Required — the observed outcome, not the inferred one.** Show the response,
   the log line, the file that appeared, the process that ran. "This would allow"
   is inference; the bar is "this did".
3. **Preferred — a test that fails before the fix and passes after.** This is how
   the fix lands anyway, so contributing it shortens the loop. A finding shipped
   with its regression test is the strongest form.

**Never send a working exploit against a live third-party target**, and never
include real credentials, real personal data, or the contents of a real
`~/.personalclaw` in a report. Reproduce against a throwaway home directory.

### Disputed findings

Disagreement is expected, and a scoping doc that pretends otherwise is useless.
Three kinds of dispute, three rules:

- **"Is this in scope?"** resolves against `SECURITY.md`'s scope section, which is
  the contract. Not against this document, and not against the maintainer's
  preference. If `SECURITY.md` is genuinely ambiguous, the maintainer amends
  `SECURITY.md` — in a separate commit, stating which reading it adopts — and the
  finding is judged against the amended text.
- **"Is this control actually enforced?"** resolves by executing code. The project
  treats code as the as-built authority, so a test is the tiebreaker: whoever
  writes the test that runs settles it. A reviewer's claim with a failing test
  beats a maintainer's reading of the source; a maintainer's passing test beats a
  reviewer's reading.
- **"How severe is it?"** resolves toward the reviewer's rating unless the
  maintainer can name the rail that bounds the impact, in code. Absent that rail,
  the higher severity stands. This is deliberately asymmetric: the maintainer has
  more context and therefore more burden.

**Unresolved disputes get published as disputes.** The report carries the
reviewer's claim and the maintainer's rebuttal side by side, both signed, with no
editorial resolution. Withdrawing a finding requires the reviewer's agreement;
the maintainer cannot delete a finding they disagree with, only answer it. A
published disagreement is more informative than a quiet omission, and a reviewer
who can be edited out has no reason to review.

## Publication plan

### What gets published

- **The full report**, findings and all, including Low severity and including
  disputes.
- **Fix status per finding**: fixed (with the commit), fix planned (with a target
  release), or accepted-as-limitation (with the reason, promoted into
  [`limitations.md`](limitations.md) so it is not only in a report nobody
  re-reads).
- **The negatives.** What the reviewer attacked and could not break, per path.
  Without this the report is a bug list, not a review, and a reader cannot tell a
  thorough review from a shallow one.
- **The coverage gaps.** What was in scope and not reached, and why. A review that
  claims full coverage of five paths is less believable than one that names the
  two it ran out of time on.
- **Which format produced it** (commissioned or self-audit), in the first
  paragraph, without euphemism.

### What is withheld, and for how long

- **An unpatched Critical or High finding is withheld entirely** until a fix is
  released, then published with the fix. Nothing partial: no redacted teaser and
  no "a fix is coming for an issue we won't describe", which tells an attacker
  where to look without telling a user what to do.
- **Exploit code for a Critical finding is withheld permanently** if the impact
  survives the fix for un-upgraded users — which, for a self-hosted tool with no
  auto-update guarantee, it does. The *reproduction* is published so a reader can
  verify the fix; a weaponised form is not.
- **Anything that identifies a real deployment** — hostnames, paths containing a
  username, tokens, the contents of a real home directory — is redacted, always.
  This is not a delay; it never publishes.
- **A reviewer's identity** is published only with their written consent, and
  omitted otherwise without comment.

Nothing else is withheld. In particular, Medium and Low findings publish on the
normal schedule even if unfixed, and an accepted limitation publishes immediately
— a gap the project has decided to live with is exactly the thing a user needs to
know before installing.

### When, relative to a release

The review report publishes **with the release that fixes its highest-severity
finding**, in that release's notes and in the repository:

1. Review completes; findings triaged to severity.
2. Critical and High findings are fixed on a private branch, each with the
   regression test from its evidence.
3. That fix ships as a release. `SECURITY.md`'s stated windows apply to review
   findings exactly as they apply to a reported vulnerability: acknowledgement
   within 7 days, a fix or a remediation plan within 30. Pre-1.0, only the latest
   minor gets the fix; there are no 0.x backports.
4. On that release, the report publishes in full, and
   [`threat-model.md`](threat-model.md) is updated in the same change — an
   `enforced` row that a reviewer broke does not get to stay `enforced` while a
   report elsewhere says otherwise.
5. If there is no Critical or High finding, the report publishes immediately and
   does not wait for a release.

If a finding's fix slips past 30 days, the *slip* is published on schedule — the
finding stays withheld, but a dated note says a High-severity finding is
outstanding and names the release it is targeting. A silent slip would make the
30-day statement in `SECURITY.md` decorative.

### Where it lands

- **The report**: `docs/security/reviews/<YYYY-MM-DD>-<format>.md` —
  e.g. `docs/security/reviews/2026-09-01-self-audit.md`. Reports are immutable
  once published; a correction is a new dated note appended to the same file,
  never an edit to a finding.
- **The index**: a `## Reviews` section in this file, listing date, format,
  finding counts by severity, and the release the fixes shipped in.
- **The cross-links**: `SECURITY.md` gains a link to the latest report;
  `threat-model.md`'s affected rows gain the finding ID that tested them, so a
  reader of a control row can see whether anyone has actually attacked it.
- **Accepted limitations** move into `limitations.md`, in that document's existing
  voice, with the finding ID.
- **Release notes** carry the finding IDs fixed, so a user deciding whether to
  upgrade can see it is a security release.

## Approval and schedule

Two things in this document are the maintainer's to decide, and neither can be
filled in by anyone else:

| Decision | Status |
|---|---|
| **Format** — commissioned review (funded) or structured public self-audit | ⬜ undecided |
| **Scope approval** — this path list and these five sections | ⬜ pending |
| **Date** — review executed, or scheduled with a date | ⬜ unscheduled |

Until all three are filled in, this document is a *proposal*: the paths, the
format, the evidence bar, and the publication plan are written and reviewable,
but no review is commissioned or scheduled. The corresponding roadmap atom
(`SH-9`, in [`SECURITY-HARDENING`](../roadmap/plans/SECURITY-HARDENING.md)) lists
scope approval as an owner task for exactly this reason.

The honest framing for a reader in the meantime: **PersonalClaw has not been
externally reviewed.** Its threat model cites its code, its limitations are
written down, and this document says how a review would run — none of which is
the same as having had one.
