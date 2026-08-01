# Discussions welcome post — draft for owner approval

**Status:** awaiting owner sign-off (OSS-OPERATIONS T2.3 / owner task 4). This is a
draft in the maintainer's voice for you to edit and post; it is deliberately not
posted automatically — a community welcome written by an agent and signed by you
would be the wrong start.

## How to post it

1. Open <https://github.com/PersonalClaw/PersonalClaw/discussions/new?category=announcements>
2. **Title:** `Welcome — what this space is for`
3. **Body:** everything below the `## Draft` heading. Every link in it is an absolute
   URL, so it pastes safely — GitHub Discussions renders relative links unreliably,
   which an earlier version of this draft would have tripped over.
4. **Start discussion**, then pin it: the **⋯** menu on the new thread → **Pin discussion**.

Edit freely before posting — this is a guess at your voice, not a script.

**Category status (2026-07-31):** ✅ **App Dev added by the owner.** The GitHub API
cannot create discussion categories (no `createDiscussionCategory` mutation exists —
verified against the GraphQL schema), so that step was necessarily manual.

The defaults GitHub created (Announcements, General, Ideas, Polls, Q&A, Show and tell)
cover the rest of the plan's list. **Roadmap Input is still optional:** `README.md` and
`docs/roadmap/roadmap.md` currently route roadmap proposals to **Ideas**, which works.
Say the word and both links get repointed if a dedicated category is added.

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
[SECURITY.md](https://github.com/PersonalClaw/PersonalClaw/blob/main/SECURITY.md) and jump the queue.

**Two honest warnings**

It is **pre-1.0 and breaks data without migrations** — the README says so in a
banner. Run `personalclaw snapshot` before upgrading. And it is a genuinely powerful
agent on your own machine: read
[the threat model](https://github.com/PersonalClaw/PersonalClaw/blob/main/docs/security/threat-model.md) and
[the limitations](https://github.com/PersonalClaw/PersonalClaw/blob/main/docs/security/limitations.md) before pointing it at anything you care
about, especially before exposing it to the internet.

**Want to help?** The
[good-first-issue](https://github.com/PersonalClaw/PersonalClaw/issues?q=is%3Aopen+label%3Agood-first-issue)
label is the front door. [CONTRIBUTING.md](https://github.com/PersonalClaw/PersonalClaw/blob/main/CONTRIBUTING.md) has the doctrine —
it is opinionated, and reading it first will save you a review cycle.

Glad you're here.
