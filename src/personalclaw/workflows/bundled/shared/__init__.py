"""Shared prompt blocks — conventions templates reference instead of duplicating (WF2-R15).

One `.md` per block, cited from a template spec as `{{block:<name>}}` and substituted at
definition time by `workflows/blocks.py`. A package (rather than a bare directory) so
`importlib.resources` can locate it identically for an editable install, a wheel and a source
checkout.
"""
