# PersonalClaw listing submissions (drafts)

**State:** draft, internal, not submitted. These are the four listing entries DISCOVERABILITY-LAUNCH
atom `DL-8` (task T4.2) requires: PRs to awesome-self-hosted and awesome-ai-agents drafted per each
list's contribution rules, plus selfh.st and AlternativeTo entries. **All four are gated on the P0
launch gate** in [`launch-checklist-draft.md`](launch-checklist-draft.md) and are **submitted by the
owner** — every target needs a human account, and awesome-self-hosted explicitly bans
guideline-violating machine-generated PRs (below). Treat this as finished copy the owner adapts to
the live target at submission time, not as something to auto-submit.

**Why this file is in core `docs/launch/`.** Same reason as
[`launch-checklist-draft.md`](launch-checklist-draft.md) and
[`launch-post-draft.md`](launch-post-draft.md): every entry's facts — the repo, the license, the
feature set, the version — are core-repo surfaces verified against this tree, so the drafts live
next to the checklist that gates them. Actual submission is external and owner-driven.

**How to read this.** One section per target: the real contribution path (with its rules), then the
draft entry, then any eligibility gate or owner decision that must clear before it is sent.

---

## Canonical description (reuse across targets for consistency)

Use this wherever a target allows a full description (selfh.st, AlternativeTo, the launch post). The
awesome-self-hosted entry uses a shorter, rules-compliant variant (below).

> PersonalClaw is a self-hosted, provider-agnostic personal AI agent. It runs on your own machine
> behind one gateway process and one web dashboard, with agentic chat and tool-approval controls,
> autonomous goal loops, layered long-term memory, a document knowledge base, reusable skills, and
> cron/webhook automation. Every model provider and integration is a removable app, so nothing ties
> you to one vendor, and all state lives in a directory you own. MIT-licensed, Python 3.12+, pre-1.0.

Facts, each verifiable in-tree: MIT ([`LICENSE`](../../LICENSE)), Python 3.12+ and version
([`pyproject.toml`](../../pyproject.toml)), the feature set and privacy posture
([`README.md`](../../README.md)). Keep the privacy phrasing consistent with the
[checklist's honesty rails](launch-checklist-draft.md) — never "zero telemetry."

---

## 1. awesome-self-hosted

**List:** [awesome-selfhosted.net](https://awesome-selfhosted.net) — machine-readable data repo
[`awesome-selfhosted/awesome-selfhosted-data`](https://github.com/awesome-selfhosted/awesome-selfhosted-data).

**Contribution path (per their
[CONTRIBUTING.md](https://github.com/awesome-selfhosted/awesome-selfhosted-data/blob/master/CONTRIBUTING.md)):**
add a new `software/personalclaw.yml` (kebab-case filename) based on their `addition.md` template,
remove unused optional fields, commit as `add PersonalClaw`, and open a PR.

**⛔ Eligibility gate — not met yet.** Their guideline: *the project's first release must be more
than 4 months old.* PersonalClaw's first release `v0.1.0` was tagged **2026-07-21**, so it becomes
eligible on **2026-11-21**. Submitting before then will be closed per their template. **Hold this PR
until 2026-11-21**; if you want a placeholder sooner, open an *issue* on the data repo instead (they
tag issues to mature rather than closing them).

**⚠️ Human submission required.** Their CONTRIBUTING states machine/LLM-generated contributions that
do not respect the guidelines are not allowed and result in a ban. The owner must review, adapt, and
submit this as their own work; the YAML below is a starting point, not a paste-and-fire.

**Fit check (qualifies):** PersonalClaw is a self-hostable server application with a web dashboard —
not a cloud-provider-locked service, not a desktop/mobile/CLI app that relies on a separate server,
not a library/SDK, and not a PaaS for arbitrary apps. Describe it as a personal agent/assistant, not
as an "app platform," to avoid the PaaS-exclusion misread.

**Draft `software/personalclaw.yml`:**

```yaml
# Based on awesome-selfhosted-data's addition.md template; optional/unused fields removed.
name: PersonalClaw
website_url: https://personalclaw.dev
source_code_url: https://github.com/PersonalClaw/PersonalClaw
# 10-250 chars, capitalized, ends with '.', does not repeat the name, and avoids the
# banned redundant terms (open-source / free / self-hosted). ~192 chars:
description: Single-user AI agent with agentic chat, autonomous goal loops, long-term memory, a knowledge base, skills, and cron/webhook automation behind one gateway and web dashboard. Provider-agnostic.
licenses:
  - MIT
tags:
  # Choose from the LIVE tags/ directory (tags/<slug>.yml) — single-page mode shows the
  # software under its FIRST tag only, so order deliberately. Candidates below are proposed;
  # confirm each exists before submitting, and do not invent a tag (a new tag needs >=3
  # projects and goes through the tag process).
  - Automation
  - Personal Dashboards
  - Knowledge Management Tools
# depends_3rdparty intentionally omitted (defaults false): core operation is local and needs no
# third-party service — you can bind a local model — so the "relies on a nonfree third-party
# service" flag does not apply.
```

Notes for the submitter: `stars`, `updated_at`, and similar metadata are auto-populated by the
repo's `make update_metadata`, so they are not hand-set. If you decide to name what it is an
alternative to, their format is a `(alternative to $PRODUCT)` suffix on the description — but that
is a peer-naming decision and is **gated by the pre-1.0 name-scrub ruling** (see §5).

---

## 2. awesome-ai-agents

**List:** [`e2b-dev/awesome-ai-agents`](https://github.com/e2b-dev/awesome-ai-agents) — "A list of AI
autonomous agents."

**Contribution path:** the list is a curated `README.md`; contribute by opening a PR that adds an
entry under the **open-source projects** section (PersonalClaw is MIT). Match the exact per-entry
markdown block the README already uses for its entries (heading level, the short description, and the
standard link labels; add a logo asset under `assets/` if the surrounding entries carry one). Do not
invent a format — copy the shape of an existing neighboring entry.

**Draft entry content (adapt to the live per-entry block):**

```markdown
### PersonalClaw

Self-hosted, provider-agnostic personal AI agent: autonomous goal loops under a deterministic
supervisor, layered long-term memory, a document knowledge base, reusable skills, and cron/webhook
automation — all behind one gateway and one web dashboard, with every model provider as a removable
app. MIT, Python 3.12+.

- Website: https://personalclaw.dev
- Code: https://github.com/PersonalClaw/PersonalClaw
```

Fit note: this list is specifically for AI assistants/agents (not SDKs/frameworks) — PersonalClaw's
autonomous goal loops place it squarely in scope. Keep the entry factual; the list skews concise.

---

## 3. selfh.st/apps

**Directory:** [selfh.st/apps](https://selfh.st/apps) (curated by Ethan Sholly).

**Contribution path:** there is no PR — submit via [selfh.st/submit](https://selfh.st/submit/) ("Submit
Content") and share the project details (per their [FAQ](https://selfh.st/apps-about/): "reach out and
share the details of your project"). License, star count, and last-activity are auto-retrieved from
the git APIs, so you supply identity, description, and category/tags only.

**Details to submit:**

```text
Name:        PersonalClaw
Repository:  https://github.com/PersonalClaw/PersonalClaw
Website:     https://personalclaw.dev
Description: Self-hosted, provider-agnostic personal AI agent — agentic chat, autonomous goal
             loops, long-term memory, a knowledge base, skills, and cron/webhook automation
             behind one gateway and web dashboard. MIT, Python 3.12+.
Category:    AI / personal assistant / automation
Tags:        ai, agent, automation, knowledge-base, personal-assistant, privacy, local-first
```

Notes: PersonalClaw is itself a self-hosted project, so it lists as an application in its own right
(the directory's "companion app" criteria are for tools that extend *another* project — not
applicable here). The default sort scores partly on repository age and recent activity, so a brand-new
project ranks low at first; that is expected and not a blocker.

---

## 4. AlternativeTo

**Directory:** [alternativeto.net](https://alternativeto.net).

**Contribution path (per their [FAQ](https://alternativeto.net/faq/)):** sign in, use **"Suggest new
application"** from the user menu (top-right), fill **Platforms, License, Descriptions, Tags**, then
**"Submit the application."**

**Draft entry:**

```text
Name:         PersonalClaw
Platforms:    Self-Hosted, Linux, Mac, Docker
              (Windows via WSL2 / Docker Desktop; native Windows not supported)
License:      Open Source (MIT)
Short desc:   A self-hosted, provider-agnostic personal AI agent you run on your own machine.
Long desc:    (use the canonical description at the top of this file)
Tags:         ai-assistant, self-hosted, automation, knowledge-base, personal-assistant,
              privacy, local, agent, llm
```

Platform facts are grounded in the [Platforms guide](../guides/platforms.md) (Linux and macOS
Apple-silicon first-class; macOS Intel best-effort; Windows via WSL2/Docker; native Windows not
supported).

**⚠️ Owner decision — the "alternative to" association.** AlternativeTo is organized around what an
app is an alternative *to*. Asserting specific peer products is public positioning and is **gated by
the pre-1.0 name-scrub ruling** (see §5). Submit the standalone application entry (which needs no
named alternative) and **defer any "alternative to X" association to the owner** — do not write peer
product names into this draft.

---

## 5. Cross-cutting gates and rulings

- **P0 launch gate.** Do not submit any of the four until every P0 item in
  [`launch-checklist-draft.md`](launch-checklist-draft.md) is green — CI green with the badge, the
  install one-liner verified on a clean machine, and the screenshots live.
- **awesome-self-hosted age gate.** Blocked until 2026-11-21 (§1). The other three have no age gate.
- **Owner submits, as a human.** Every target needs an account; awesome-self-hosted bans
  guideline-violating machine-generated PRs. These drafts are inputs to a human submission.
- **Pre-1.0 name-scrub ruling.** No peer/competitor product names on public surfaces. This is why the
  awesome-self-hosted `(alternative to …)` suffix and the AlternativeTo "alternative to" association
  are deferred to the owner rather than filled in here. See the `DL-7` entries in
  [the source plan](../roadmap/plans/DISCOVERABILITY-LAUNCH.md).
- **Consistency.** Reuse the canonical description so all listings agree, and keep the privacy
  phrasing identical to the launch post and README — never "zero telemetry" (see
  [limitations.md](../security/limitations.md) and the checklist's honesty rails).

## Status

`DL-8` stays `todo` in [`DL.md`](../roadmap/atomic/DL.md) / `dag.json`: these are the drafts the
`done_when` names, but the atom also requires the PRs/entries to actually be open and the gate to be
green before any owner posting — external, owner-only steps. Flip the atom only when the submissions
are live.
