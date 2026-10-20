---
title: Goal loops are the one way of doing autonomous work, and replace Auto-nudge
date: 2026-10-06
status: accepted
supersedes:
superseded_by:
---

## Decision

Goal loops are the one way the agent works on its own. Any chat can become goal work on its own
context: an objective, budgets, a judge or the owner's verify command, Pause, Resume and Stop, a
cadence, and one wrap-up turn. The work keeps its own stop, and shows that stop with its reason.
Auto-nudge's modal, route and stop tool go.

First stated for the v0.5.0 release
([#3998](https://github.com/PersonalClaw/PersonalClaw/issues/3998)).

## Why

Auto-nudge keeps a chat going by sending it the same message each time it goes quiet, up to a
number of cycles, and it stops when the agent writes a stop file. It has no objective to judge the
work against, no budget, and no stop that says why. A goal loop has all three. Two ways of working
unattended would mean two sets of limits for the owner to trust and two kinds of stop to learn.
One way means one set of guardrails, one record of every cycle and one place to steer.

## What it rules out

- Auto-nudge, with its modal, its route and its stop tool.
- A second way of doing autonomous work beside goal loops.
- A chat that keeps working on its own without an objective, budgets and a stop.
