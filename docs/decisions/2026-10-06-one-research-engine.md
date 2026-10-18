---
title: The research loop is the one research engine
date: 2026-10-06
status: accepted
supersedes:
superseded_by:
---

## Decision

The `research` loop kind is the one research engine, and today's `deep-research` workflow becomes
its fixed-rounds mode. The Research Lab page becomes a view over research loops, with two
actions: fork a campaign, and send its report to the library.

First stated for the v0.5.0 release
([#3998](https://github.com/PersonalClaw/PersonalClaw/issues/3998)).

## Why

Research runs three ways today: the `research` loop kind, the bundled `deep-research` workflow, and
the Research Lab app's own campaigns. Each keeps its own budget, its own record and its own report.
One engine means research is budgeted, judged and recorded in one place, and every surface shows it
the same way.

## What it rules out

- A second research engine, in core or in an app.
- A Research Lab that runs campaigns of its own instead of showing research loops.
