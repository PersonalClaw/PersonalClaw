You are suggesting follow-up messages for a chat assistant.

Given the last exchange below (the user's message and the assistant's reply), propose 2-3 short follow-up messages the user is most likely to want to send NEXT. Each is a single line the user can click to send, phrased from the USER's point of view (e.g. "Show me an example", "What are the trade-offs?", "Now apply that to the other file").

Rules:
- 2-3 items, each under 60 characters.
- Concrete and specific to THIS exchange — never generic ("Tell me more").
- Written as the user's own next message, not as a question to the user.
- If no useful follow-up fits, respond with an empty array: []

Last exchange:
---
{{exchange}}
---

Respond with ONLY a JSON array of strings. No explanation, no markdown fences.
Example: ["Show me a code example", "How do I test this?", "Apply the same fix to config.py"]
