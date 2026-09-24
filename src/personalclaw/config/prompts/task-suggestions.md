You are generating contextual prompt suggestions for a developer assistant dashboard.

Based on the user's current context below, generate 4-6 short, actionable prompt suggestions that the user might want to ask right now. Each suggestion should be a single sentence (under 60 characters) that the user can click to start a conversation.

Consider:
- What they were working on recently (projects, sessions)
- Their preferences and habits
- Time of day and day of week
- Pending tasks or follow-ups implied by recent activity

Current context:
---
{{context}}
---

Respond with ONLY a JSON array of strings. No explanation, no markdown fences.
Example: ["Check CI status for the API service", "Continue refactoring auth module", "Review yesterday's PR feedback"]
