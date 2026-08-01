"""Workflows — the composable execution platform (WORKFLOWS-V2).

The previous feature (stateless "SOP" checklists with embedding-based auto-surfacing)
was deleted wholesale in Phase 1 of the plan's clean-break sequence; this namespace is
reused with no compatibility layer. Until the engine slices land, the only inhabitant
is the def-provider registry seam (`defs.py`) — it exists now because the extension
registry's `workflow` type handler must point somewhere real: `PROVIDER_TYPES` and the
runtime handler set have to stay equal or installing/updating any app that declares a
workflow provider is blocked (issue #47's bug class).
"""
