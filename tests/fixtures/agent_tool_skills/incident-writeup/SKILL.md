---
name: incident-writeup
description: Turn my incident notes, Slack excerpts and graphs into a blameless postmortem in Cartwheel's format. Use after an on-call incident once the mitigation is in.
---

# Incident write-up

You must always lead with customer impact: who was affected, for how long, and what they saw.
Engineers read the root cause section; everyone else stops after the first paragraph.

Use `template.md` for the structure. Fill it from what I give you, and mark anything you had to
infer with "(inferred)" so I can check it.

Rules:
- Times in UTC with the local Toronto time in brackets for the first mention.
- Blameless. Describe what the system allowed, not who did what.
- Anonymise carriers and customers in anything marked "external".
- Every action item has an owner and a date, or it is not an action item.
- Keep the root cause to what we know. Put theories under "Open questions".
