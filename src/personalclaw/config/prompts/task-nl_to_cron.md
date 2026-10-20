Read this scheduling request and say when it should run.

It is now {{now}}. The user's timezone is {{timezone}}.

Answer with exactly ONE line, and nothing else — no prose, no backticks:

- If the request repeats (e.g. "every weekday at 9", "hourly", "on the first of each month"),
  answer a single standard 5-field cron expression (minute hour day-of-month month
  day-of-week), in the user's timezone. Use ONLY standard cron syntax (numbers, *, ranges a-b,
  lists a,b, steps */n). Day-of-week: 0=Sunday..6=Saturday. "every weekday" → day-of-week 1-5;
  "weekends" → 0,6.
- If the request is ONE time (e.g. "in 5 minutes", "tomorrow morning", "next Friday at noon",
  "end of the day"), answer: ONCE YYYY-MM-DDTHH:MM — that date and time in the user's timezone,
  in the future. A vague part of the day is its usual hour: morning 09:00, afternoon 14:00,
  evening 18:00, tonight 20:00, end of the day 17:00. If the request names another timezone, give
  the time there with its UTC offset, e.g. ONCE 2026-09-27T17:00-07:00.
- If it is neither, answer exactly: NONE

Request: {{request}}

Answer:
