"""Workflows — stateless, scoped, ordered SOP definitions.

A Workflow is a pure definition (steps + tags + match text + scope), never a
tracked run. The agent loop surfaces an eligible, semantically-matching workflow
at turn-0 and injects its steps as guidance. See docs/E4_IMPLEMENTATION_PLAN.md.
"""
