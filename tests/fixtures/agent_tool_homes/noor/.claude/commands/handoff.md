---
description: On-call handoff note for Monday 10:00
allowed-tools: Bash(git log:*), mcp__sentry__search_issues
---

Write my on-call handoff for the incoming primary.

1. Open Sentry issues for carrier-webhooks and eta-engine with more than 100 events in the last
   7 days, grouped by whether they are new this week.
2. Anything I mitigated but did not fix, with the link to the PR or the flag I flipped.
3. Upcoming risk: carrier API changes, deploy freezes, peak days.

Keep it to one screen. Put the thing most likely to page them first.
$ARGUMENTS
