"""Platform-legibility runtime surfaces (Platform-Legibility §6-§7).

Two user-facing features that read the self-description machinery S1-S5 built and
turn it into help the human can see:

* :mod:`personalclaw.legibility.discover` — the "Discover" dashboard section and the
  dedicated Discover hub: a hand-authored, curated tour of the system's user-facing
  areas (Chat, loops, Tasks, Knowledge, Memory, Automation, Skills, Apps…), each a
  deep link into the page that owns it. Tips leave the feed only by being dismissed
  or by auto-hiding once the user has engaged that area. Propose-don't-write — it
  never enables anything on the user's behalf.
* :mod:`personalclaw.legibility.context_router` — PClaw as a routed-context provider
  for external agents (§7): marker-fenced adapter blocks rendered into opted-in
  projects' CLAUDE.md / AGENTS.md / .cursorrules.
"""
