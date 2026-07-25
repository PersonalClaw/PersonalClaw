"""Platform-legibility runtime surfaces (Platform-Legibility §6-§7).

Two user-facing features that read the self-description machinery S1-S5 built and
turn it into help the human can see:

* :mod:`personalclaw.legibility.tool_usage` — a per-tool invocation counter (the
  skill-usage sidecar pattern, one file over), so "capabilities that exist minus
  capabilities you've touched" is computable for the first time.
* :mod:`personalclaw.legibility.power_ups` — the dashboard capability-discovery
  widget's data source: one untouched capability at a time, a deterministic
  two-sentence lesson, a "try it" deep link, and persisted dismissals.
  Propose-don't-write — it never enables anything on the user's behalf.
"""
