---
id: no-naive-transcript-cut
type: ai-coding-rule
statement: >
  Transcript / journal truncation must not cut blindly at a message boundary — it must
  walk back over any dangling tool-call/tool-result pair so a tool call is never separated
  from its result (which corrupts the next model turn).
appliesTo:
  - src/personalclaw/context_compaction.py
  - src/personalclaw/context_management.py
scanner: no-naive-transcript-cut
source: >
  A tool_use message whose matching tool_result is truncated away (or vice versa) makes
  the next provider call malformed — most APIs reject an unpaired tool block. Naive
  "keep last N messages" slicing hits this whenever the cut lands mid-pair.
expiry_condition: >
  Retire if the transcript model stores tool-call/result as one atomic unit that can't be
  split by a slice.
---

# Never cut a transcript between a tool call and its result

When trimming a transcript or journal to fit a context window, a naive
"keep the last N messages" slice can land **between** a `tool_use` and its matching
`tool_result`. That leaves a dangling tool call (or orphan result), and the next provider
request is malformed — most model APIs reject an unpaired tool block outright.

## What compliance looks like

Truncation sites must use the shared walk-back helper that, after choosing a cut point,
moves the boundary so no tool-call/tool-result pair is split — the same pairing invariant
the workflow-rewind path adopts. If you're adding a new place that truncates conversation
history, route it through that helper rather than slicing the list directly.

The scanner check `no-naive-transcript-cut` flags a new slice of a
messages/transcript/journal list in the compaction modules that doesn't reference the
walk-back helper.
