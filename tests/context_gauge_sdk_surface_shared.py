"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_context_gauge_sdk_surface.py``

Its checks:
``tests/rails/test_context_gauge_sdk_surface_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

#: The context-window / gauge contract, and who consumes each symbol. A symbol may only
#: leave this list when its named consumers no longer import it.
_CONTRACT = {
    "declared_context_window": "the bundled ollama-models app, and every app that pops the "
    "operator's per-binding context_window option",
    "model_context_window": "the first-party bedrock-models app (separate repo) — a BUDGET "
    "resolution, which must always answer",
    "ContextGauge": "the bundled ollama-models app — turns a provider token report into a "
    "monotone, in-range, honest reading, and holds the per-binding reference that catches a "
    "silently truncated one",
    "prompt_text_chars": "the bundled ollama-models app — the size of the prompt that was "
    "SENT, which is what makes the reading monotone",
}
