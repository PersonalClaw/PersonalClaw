"""The repo's chars-per-token estimates, in ONE place — and there are exactly TWO.

Ten modules independently approximated "how many tokens is this text?" — six named
constants and four bare ``// 4`` literals with no constant at all. They were not all the
same number, and that is the whole reason this module has two constants instead of one.

**Why two, not one.** Nine of the ten sites used ``4``; the native loop's compaction
backstop used ``3.0``, deliberately. They are answering different questions:

* A **content budget** asks "how much text fits in N tokens?" and wants the *nominal*
  ratio, because being wrong by 10% either way just makes a budget slightly loose or
  slightly tight. Nine sites.
* A **compaction trigger** asks "am I about to overflow?" and must err toward *yes*. At
  3.0 the estimated token count comes out higher than reality, so the trigger fires
  slightly early — one cheap summarizer call — instead of slightly late, which overflows
  the window and kills the turn. One site.

So collapsing all ten to ``4`` would silently delete that safety margin (and the margin is
load-bearing: the trigger is the *only* thing that can fire for a provider reporting no
usage), while collapsing all ten to ``3.0`` would tighten nine unrelated budgets by 25%
for no reason. Two constants with stated roles is the honest consolidation; one would be a
behaviour change wearing a refactor's clothes.

Neither is a tokenizer, and neither should become one: every consumer here is on a hot
path where a per-model tokenizer load costs more than the error it would remove.
:mod:`personalclaw.learning.surfacing` does prefer a real tokenizer when ``tiktoken``
happens to be importable, and falls back to :data:`NOMINAL_CHARS_PER_TOKEN`.

This module imports nothing from ``personalclaw`` — it is a leaf, so any of the ten
callers can import it without a cycle.
"""

from __future__ import annotations

#: The repo's standard rough ratio for English prose and code, used wherever a token
#: budget is converted to (or from) a character count. An ESTIMATE: consumers that expose
#: it to a user label it as one.
NOMINAL_CHARS_PER_TOKEN = 4

#: The ratio for a compaction TRIGGER, which must over-estimate token usage rather than
#: under-estimate it. Lower than :data:`NOMINAL_CHARS_PER_TOKEN` on purpose — see this
#: module's docstring for why the two cannot be merged. A float, because the arithmetic it
#: feeds is a true division against a window size.
CONSERVATIVE_CHARS_PER_TOKEN = 3.0
