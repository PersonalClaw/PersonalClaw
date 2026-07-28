"""Durability — one inventory of everything that matters (DURABILITY-AND-SYNC).

PersonalClaw accumulates state across ~30 stores under the home directory. Before
this package, "what is PersonalClaw's state?" had no single answer: `snapshot.py`
carried a hand-maintained ``CORE_FILES`` allowlist, `portability.py` carried its
own exclude sets, and the two had already drifted apart from reality — nine store
directories (tasks, projects, loop, artifacts, prompts, workflows, agents, apps,
entity settings) were backed up by *neither*.

Session 1 (this slice) ships the inventory and closes that gap:

* :mod:`personalclaw.durability.inventory` — the declarative manifest of every
  state entry (kind, domain, secret/derived flags, merge strategy), plus the
  claims-everything audit that fails the moment an unclaimed path appears under
  the home. Snapshot components and the export exclude-set become *projections*
  of this manifest, so the allowlist-drift bug class dies here.

The doctrine is that the inventory is the only place a store is declared. Any
other module that needs to know "what is state" iterates it.
"""
