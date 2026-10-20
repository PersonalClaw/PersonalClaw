---
title: Ordered product tenets decide trade-offs
date: 2026-10-06
status: accepted
supersedes:
superseded_by:
---

## Decision

Six product tenets decide trade-offs, in this order:

1. The owner's yes before any action, failing closed when unsure.
2. Words that are true.
3. The owner's data stays on the owner's machine.
4. A working agent within minutes, with no account.
5. Works with the tools the owner already uses.
6. A feature is not done until the owner can find and use it.

When two pull against each other the earlier one wins, and the change that chose says which tenet
decided, in its PR description. The tenets are written out in
[`docs/vision.md`](../vision.md#product-tenets), above the architecture tenets.

First stated for the v0.5.0 release
([#3998](https://github.com/PersonalClaw/PersonalClaw/issues/3998)).

## Why

Most hard product calls trade one of these goods against another: a quicker first run against
asking the owner first, or a familiar tool against keeping the data on the machine. A fixed order
makes the same conflict come out the same way every time, whoever decides it. Naming the tenet in
the PR leaves a record that a later change can check its own call against.

## What it rules out

- Settling a conflict between two tenets by taste, or one way in one change and the other way in
  the next.
- Reordering or rewording the tenets inside a change that only wants a different answer. A new
  order is a new decision that supersedes this one.
