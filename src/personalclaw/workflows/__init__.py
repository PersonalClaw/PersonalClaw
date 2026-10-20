"""Workflows — the composable execution platform.

The previous feature (stateless "SOP" checklists with embedding-based auto-surfacing)
was deleted wholesale in a clean break; this namespace is reused with no compatibility
layer. The def-provider registry seam (`defs.py`) exists because the extension
registry's `workflow` type handler must point somewhere real: `PROVIDER_TYPES` and the
runtime handler set have to stay equal or installing/updating any app that declares a
workflow provider is blocked.
"""
