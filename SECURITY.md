# Security Policy

PersonalClaw runs an autonomous agent on your own machine, so its security model
is defense-in-depth: authentication modes, command screening, an OS sandbox, one
egress chokepoint, app-scoped tokens, supply-chain scanning, untrusted-content
fencing, and a tamper-evident audit log. The architecture is documented in
[`docs/architecture/security.md`](docs/architecture/security.md), and the public
threat model — including the OWASP Agentic Security Top-10 (ASI) mapping and an
honest statement of limitations — lives in
[`docs/security/threat-model.md`](docs/security/threat-model.md).

## Reporting a vulnerability

**Please report security issues privately — do not open a public issue.**

Use GitHub's private vulnerability reporting: go to the
[Security tab](https://github.com/PersonalClaw/PersonalClaw/security) and click
**"Report a vulnerability"**. This opens a private advisory visible only to you
and the maintainer.

Include, where you can:

- the affected component (auth, command screening, sandbox, egress, tokens,
  supply-chain scanner, fencing, or the Security Event Log);
- a description of the impact and a proof-of-concept or reproduction steps;
- the version or commit you observed it on.

### What to expect

PersonalClaw is maintained by a single person, so these are honest expectations,
not contractual SLAs:

- **Acknowledgement within 7 days** of your report.
- **A fix or a remediation plan within 30 days** for confirmed issues.

If a report stalls past these windows, a polite nudge on the advisory thread is
welcome.

## Supported versions

PersonalClaw is pre-1.0. Only the latest released minor version receives security
fixes; there are no backports to older 0.x lines.

| Version | Supported |
|---|---|
| Latest 0.x minor | ✅ |
| Older 0.x | ❌ |

## Scope

### In scope

Security issues that let an attacker cross a trust boundary the product claims to
hold:

- **Remote code execution** or gateway compromise from a non-owner input.
- **Authentication bypass** — reaching an authenticated `/api` surface without a
  valid token, or an app-scoped token reaching paths outside its declared
  permissions.
- **Sandbox / scanner / egress bypass** — executing a command the screening layer
  should deny, installing content the supply-chain scanner rated `dangerous`, or
  making an outbound request that evades the egress chokepoint.
- **Token or credential leakage** — an app backend or exported artifact obtaining
  the owner's credentials, or credentials appearing in logs, exports, or the
  Security Event Log.

### Out of scope

- **Self-inflicted YOLO / auto-approve footguns.** PersonalClaw lets its owner
  lower their own guardrails (e.g. enabling auto-approve); the owner choosing to
  do so is not a vulnerability. See the limitations in the threat model.
- **Issues that require an already-compromised host** (root on the machine, a
  compromised OS account, physical access). PersonalClaw does not defend the
  owner against themselves or against a host that is already owned.
- **Hardening requests** — "you should also add control X." These are valuable and
  welcome, but file them as a normal issue, not a private advisory.
- **Declaration-only surfaces documented as such**, e.g. an app's `network`
  permission. There is no per-app egress chokepoint to enforce it at, for two
  separate reasons: an app's **provider** code is imported *in-process* by the
  gateway (`providers/loader.py`), so its outbound calls simply *are* the
  gateway's; and an app that ships a **backend** gets its own OS process with its
  own network stack. The declaration is surfaced honestly at install consent,
  labelled advisory and shown whether or not the app declares it, but it is not a
  gateway-enforced boundary — see `src/personalclaw/apps/permissions.py` and the
  threat model's limitations section. Treat installing an app as running a program
  as yourself.
- **What an installed app's frontend reaches in the dashboard page.** An app's UI
  bundle is imported into the dashboard's own origin, so it has the host DOM, the
  owner's session and same-origin `/api/*` access; the `api` allowlist binds the app's
  backend and its SDK client, not its page code. Disclosed at install consent and in
  the limitations page (§4), and not separable without a distinct origin for app UI.
  The supply-chain gate on what you install is the control. A *new* way to reach the
  host from app UI is not a finding; a way to install an app past that gate is.

## Governance stability

Some of what a self-hosted tool promises you is not a control in the code — it is who owns
the project and on what terms. Those promises are worth as little as the licence they sit on,
so as of **2026-09-18** they are stated plainly and enforced by CI:

- **MIT only.** PersonalClaw has been MIT since its first commit and has never carried any
  other licence. No dual licensing, no open-core tier, no source-available variant.
- **No CLA.** There is no contributor licence agreement and none is planned. Contributions
  arrive under the DCO sign-off and stay MIT; no contributor assigns copyright, so the
  paperwork a relicensing move would need does not exist here.
- **No telemetry.** No analytics, no crash reporting, no usage pings. The product makes one
  unprompted outbound call — a GitHub release check, documented in the README's Privacy
  section — and `updates.check_enabled=false` reduces it to zero.
- **No relicensing of already-published releases.** Everything already published to PyPI,
  GHCR and the GitHub releases page is MIT permanently. A future licence change could apply
  only to new releases; it could not reach the version you already installed and trusted.

**These are rails, not just prose.** `tests/test_licence_governance.py` fails CI if a licence
identifier anywhere in the tree stops saying MIT, if `LICENSE`'s grant text is rewritten (the
copyright year is excluded, the grant is not), or if a `CLA` /
`CONTRIBUTOR_LICENSE_AGREEMENT` file appears; `tests/test_network_egress_hosts.py` fails it if
a new outbound host is added. Each of those checks is itself proven to fail on an injected
violation, so none of them is a green light that never turns red. The census of every place
this project declares its licence is
[`docs/architecture/licence-identity.txt`](docs/architecture/licence-identity.txt).

Changing any of the four is therefore a governance decision requiring an explicit edit to
committed artifacts and this section — which is the point. It is not a promise that the
licence can never change; it is a guarantee that it cannot change *quietly*, and that a
release you already have cannot change at all.

## Apps and third-party bundles

Installable apps go through a separate supply-chain path (quarantine → scan →
consent → install). Vulnerabilities in that pipeline, or in the first-party app
bundles, are reported here as well; the companion policy in the
[PersonalClawApps](https://github.com/PersonalClaw/PersonalClawApps) repository
covers app-bundle-specific scope.
