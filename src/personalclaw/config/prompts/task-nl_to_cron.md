Convert this scheduling request into a single standard 5-field cron expression
(minute hour day-of-month month day-of-week). Use ONLY standard cron syntax
(numbers, *, ranges a-b, lists a,b, steps */n). Day-of-week: 0=Sunday..6=Saturday.

Rules:
- Output ONLY the cron expression on one line, nothing else — no prose, no backticks.
- "every weekday" → day-of-week 1-5. "weekends" → 0,6.
- If the request is NOT a recurring schedule (e.g. "in 5 minutes", "tomorrow at 3pm",
  a one-off), output exactly: NONE

Request: {{request}}

Cron: