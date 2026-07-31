# Discussions welcome post — draft for owner approval

**Status:** awaiting owner sign-off (OSS-OPERATIONS T2.3 / owner task 4). This is a
draft in the maintainer's voice for you to edit and post; it is deliberately not
posted automatically — a community welcome written by an agent and signed by you
would be the wrong start.

**Where to post:** Discussions → Announcements, then pin it.

**Two things to do first (web UI only — the GitHub API cannot create discussion
categories, verified 2026-07-31):**

1. Add an **App Dev** category (format: Discussion) — "Building apps against the
   PersonalClaw SDK: manifests, capabilities, permissions, the Store."
2. Decide on **Roadmap Input**. The README and `roadmap.md` currently point roadmap
   proposals at the default **Ideas** category, which works fine. Add a dedicated
   category only if you want roadmap threads separated from general feature ideas —
   if you do, update the two links in `README.md` and `docs/roadmap/roadmap.md`.

The defaults GitHub created (Announcements, General, Ideas, Polls, Q&A, Show and
tell) already cover the rest of the plan's list.

---

## Draft

**Title:** Welcome — what this space is for

PersonalClaw is a self-hosted personal AI agent: one gateway process and one
dashboard you own, running on your machine, with no telemetry. It is at **v0.1.3**
and moving fast.

I built it for myself and then made it public, which shapes what this space is for.

**Where things go**

- **Q&A** — you are stuck. Setup, providers, local models, something not behaving.
  No question is too small; if the docs failed you, that is a docs bug and I want
  to know.
- **Show and tell** — you built something. An app, a loop, a workflow, an
  unreasonable automation. This is the category I am most curious about.
- **App Dev** — you are building against the SDK. Manifests, capabilities,
  permissions, the Store. First-party apps live in
  [PersonalClawApps](https://github.com/PersonalClaw/PersonalClawApps) and are the
  best reference.
- **Ideas** — feature thinking, including roadmap proposals. The roadmap is
  maintainer-owned so the cross-plan contracts stay coherent, but the intake path is
  written down and real: propose here and I file or amend the plan.
- **Bugs → [Issues](https://github.com/PersonalClaw/PersonalClaw/issues)**, not here,
  so they can be tracked and closed.

**What to expect from me**

One maintainer, so: honest latency rather than promised latency. Issues get triaged;
Q&A gets answered when I can. Security reports go through
[SECURITY.md](../../SECURITY.md) and jump the queue.

**Two honest warnings**

It is **pre-1.0 and breaks data without migrations** — the README says so in a
banner. Run `personalclaw snapshot` before upgrading. And it is a genuinely powerful
agent on your own machine: read
[the threat model](../security/threat-model.md) and
[the limitations](../security/limitations.md) before pointing it at anything you care
about, especially before exposing it to the internet.

**Want to help?** The
[good-first-issue](https://github.com/PersonalClaw/PersonalClaw/issues?q=is%3Aopen+label%3Agood-first-issue)
label is the front door. [CONTRIBUTING.md](../../CONTRIBUTING.md) has the doctrine —
it is opinionated, and reading it first will save you a review cycle.

Glad you're here.
