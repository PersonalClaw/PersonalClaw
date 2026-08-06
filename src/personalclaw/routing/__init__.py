"""Model routing — telemetry-driven, per-query model selection (MODEL-ROUTING-TELEMETRY).

The routing layer answers "which bound model fits THIS query" without spending an LLM call to
decide. It starts with a pure query classifier (:mod:`personalclaw.routing.classifier`) that
labels a request into a small fixed vocabulary; later slices fold per-class telemetry and reorder
the candidate refs. Nothing here resolves or calls a model — it only classifies and scores.
"""
