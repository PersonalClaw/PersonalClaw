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
  permission (an app backend is its own OS process with its own network stack;
  the declaration is surfaced honestly at install consent but is not a
  gateway-enforced boundary — see the threat model's limitations section).

## Apps and third-party bundles

Installable apps go through a separate supply-chain path (quarantine → scan →
consent → install). Vulnerabilities in that pipeline, or in the first-party app
bundles, are reported here as well; the companion policy in the
[PersonalClawApps](https://github.com/PersonalClaw/PersonalClawApps) repository
covers app-bundle-specific scope.
