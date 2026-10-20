---
id: fence-at-ingestion
type: ai-coding-rule
statement: >
  Any new call site that reads external / channel / web text into an LLM prompt must pass
  that text through `fence_untrusted` (or the documented fencing helper) before it reaches
  the model, so untrusted content can't be interpreted as instructions.
appliesTo:
  - src/personalclaw/**/*.py
scanner: fence-at-ingestion
source: >
  Prompt-injection defense: untrusted text (inbound channel messages, fetched web pages,
  ingested documents) folded raw into a prompt lets an attacker's text act as agent
  instructions. Fencing wraps it so the model treats it as data, not commands.
expiry_condition: >
  Retire only if fencing becomes automatic at the context-assembly layer (a single choke
  point wraps all external text), making per-call-site fencing unnecessary.
---

# Fence untrusted text at the ingestion point

External text — inbound channel messages, fetched web content, ingested documents,
tool outputs from untrusted sources — must be fenced before it enters a prompt. The
codebase provides a fencing helper (`fence_untrusted`) whose wording is a **security
control** (copy-sensitive — do not reword it). Fencing marks the boundary so the model
treats the enclosed text as data to reason about, not instructions to follow.

## What compliance looks like

At any new site where you take text that originated outside the user/system trust
boundary and put it into prompt context, wrap it with the fencing helper. If you're
adding a new *source* of external text, fence at the point it's read, not deep in the
prompt builder where the origin is already lost.

The scanner check `fence-at-ingestion` is a **WARNING-level heuristic**: it flags new
prompt-assembly sites that reference channel/web/document text without a nearby fencing
call. A heuristic can't prove a real gap, so it prompts a look rather than failing hard.
