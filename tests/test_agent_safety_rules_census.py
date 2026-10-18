"""Every agent core starts is handed the platform's safety rules, and none is started around them.

The rules (the ``safety-rules`` snippet, worded once) reach an agent's model in one of three ways,
and this census reads every place core starts an agent, failing for one it cannot account for:

* **Assembled** — the turn's first message is built by ``ContextBuilder.build_message``, directly or
  through the context engine, and that assembly layers the rules on whatever prompt resolved
  (``prompt_providers.runtime.with_safety_rules``).
* **Layered** — the path composes its own message, and the function that composes it layers them.
* **Calls nothing** — every call the turn asks for is refused, or its runtime has no tools, so
  nothing it is handed can make it act, and its own prompt frames what it answers.

Where an agent is started: each session core acquires (``….get_or_create(…)``, as the session
census reads them), each runtime it builds from the provider factory itself
(``….create_provider_factory()``), and each direct resolution through the bridge
(``resolve_provider_for_use_case``). A new one fails here until it is named with its way, and each
way is checked against the code: an assembled site calls the assembler, a layered one the layer,
and one that calls nothing refuses its calls. Every detector is shown to find what it looks for
before its answer is trusted. What the paths' models are actually handed is read in
``test_every_agent_is_handed_the_safety_rules.py``.

The same sites are read for the agent's own instructions, which the rules follow. A site that NAMES
the agent it starts (an ``agent=`` its session is acquired or its runtime built with) hands that
agent's instructions to its model, read by their one reader
(``agents.instructions.agent_instructions``): its assembly is told the agent, or handed its
instructions read, or the message it composes itself reads them, or it is named in
:data:`NOT_ITS_OWN_WORK` with why its turn is not that agent's own work. A site that names no
agent starts the default one, whose instructions the assembly reads itself. What each path's model
is handed is read in ``test_a_named_agents_instructions_reach_its_model.py``.

What it cannot see: an agent started by a name other than these three.
"""

from __future__ import annotations

import ast

from agent_safety_rules_census_shared import (
    _calls,
    _names,
    _starts_a_named_agent,
    _tells_the_assembly,
)
from session_acquisitions import _functions

# ── the census ───────────────────────────────────────────────────────────────────────────────


# ── each way, checked against the code ───────────────────────────────────────────────────────


def test_the_detectors_find_what_they_look_for():
    """Positive controls: each check answers yes for a function that has the thing, and no for
    one that lacks it, so a zero above is the tree's and not a broken reader's."""
    tree = ast.parse(
        "def a(c):\n    return c.build_message('x', True)\n"
        "def b(p):\n    return p.stream('x')\n"
        "def d(r):\n    return r(lambda k: f(k))\n"
    )
    funcs = _functions(tree)
    assert "build_message" in _names(funcs["a"]) and "build_message" not in _names(funcs["b"])
    assert _calls(tree, "f") == ["d.<lambda>"]
    assert _calls(tree, "build_message") == ["a"]


# ── the agent's own instructions ─────────────────────────────────────────────────────────────


def test_the_instruction_detectors_find_what_they_look_for():
    """Positive controls: a site that names its agent and tells its assembly, one that names it
    and does not, one that names none, and a runtime built where the factory is made."""
    funcs = _functions(
        ast.parse(
            "async def told(s, c, a):\n"
            "    p, n, r = await s.get_or_create('k', agent=a)\n"
            "    return await asyncio.to_thread(partial(c.build_message, agent=a), 'x', n)\n"
            "async def untold(s, c, a):\n"
            "    p, n, r = await s.get_or_create('k', agent=a)\n"
            "    return await asyncio.to_thread(c.build_message, 'x', n, 'k')\n"
            "async def nameless(s, c):\n"
            "    p, n, r = await s.get_or_create('k', agent=None)\n"
            "    def later():\n"
            "        return c.build_message('x', n, agent='other')\n"
            "    return later\n"
            "def factory(cfg, a):\n"
            "    return cfg.create_provider_factory()('k', agent=a)\n"
        )
    )
    assert _starts_a_named_agent(funcs["told"]) and _starts_a_named_agent(funcs["untold"])
    assert not _starts_a_named_agent(funcs["nameless"]) and _starts_a_named_agent(funcs["factory"])
    assert _tells_the_assembly(funcs["told"]) and not _tells_the_assembly(funcs["untold"])
    assert not _tells_the_assembly(funcs["nameless"]), "a nested function is its own site"


# ── one wording ──────────────────────────────────────────────────────────────────────────────
