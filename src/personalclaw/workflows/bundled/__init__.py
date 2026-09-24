"""Bundled workflow templates — the batteries-included library (WF2 §6).

One directory per template, each holding a `workflow.json`. Shipped INSIDE the package so a
fresh `pip install personalclaw` has a working library with no network, no API key and no
first-run download — the same reasoning as `skills/bundled/`.

Read-only by contract. A user who wants to change one instantiates it (which copies the spec
into their own `defs/`) and edits that; writing through to the package directory would put a
user's edit somewhere `pip install --upgrade` overwrites without warning.
"""
