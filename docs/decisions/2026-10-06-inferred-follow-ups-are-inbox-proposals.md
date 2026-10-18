---
title: Inferred follow-ups become Inbox proposals, and nothing is sent before the owner's yes
date: 2026-10-06
status: accepted
supersedes:
superseded_by:
---

## Decision

A follow-up the agent infers, one the owner never asked for, becomes a proposal in the Inbox. Its
Yes sets an ordinary reminder. Nothing is sent before that Yes, and the heartbeat's unasked
delivery goes.

First stated for the v0.5.0 release
([#3998](https://github.com/PersonalClaw/PersonalClaw/issues/3998)).

## Why

Memory consolidation picks out check-ins the owner never asked to be reminded about, and today the
heartbeat sends each one when it falls due. A wrong check-in is an interruption the owner never
agreed to. The first product tenet decides it: the agent may propose a follow-up, and the owner
decides whether it happens.

## What it rules out

- Any message or reminder sent on an inference alone.
- The heartbeat delivering a check-in nobody asked for.
