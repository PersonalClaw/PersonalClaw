"""A routing mute the product can create, the product must be able to undo.

Three dismissals of a routing suggestion mute an agent PERMANENTLY: no expiry, and
``is_suppressed`` returns on the mute before it ever reads ``cooldown_hours``, so no setting
walks it back. The clearing endpoint and the status endpoint both existed and worked — and had
**zero** frontend callers, while Settings › Chat › Agent routing told the user three dismissals
"mute it until you re-enable". A producer with no consumer, plus copy asserting the missing
capability (core issue 414).

Measured on a live gateway on an isolated ``PERSONALCLAW_HOME`` before the fix:

    POST /api/agents/routing/dismiss {"agent":"zz414-probe-agent"} ×3 -> count 3, muted true
    GET  /api/agents/routing/status  -> muted ["personalclaw","zz414-probe-agent",
                                               "zz414-phantom-agent"]

    surface                                   said                              undo?
    ─────────────────────────────────────────────────────────────────────────────────
    agent detail, zz414-probe-agent (real)    "Muted — the auto-router stopped…"  1 button
    agent detail, PersonalClaw (mixed case)   "Active — eligible for auto-…"      0  ← LIED
    agent detail, zz414-phantom-agent         no such page                        0
    agent detail, personalclaw-coder          no Advanced section at all          0
    Settings › Chat › Agent routing           "…until you re-enable"              0

So the per-agent control closed exactly one of five rows. The other four are what this
rail is about, and three of them are unreachable *by construction* from a per-agent surface:

  · ``record_dismiss`` never checks that an agent by that name exists, and an agent can be
    deleted while muted, so the store legitimately holds keys with no detail page to visit;
  · reserved built-ins render no Advanced disclosure, so they have no slot for the control;
  · the store's identity is case-insensitive, so a raw-name comparison misses a real mute.

🪤 THE ROUTE→CLIENT→CALLER CHAIN IS DERIVED, NOT LISTED. The endpoint names come out of
the route table's own registrations and the client names out of ``lib/api.ts``'s own URL strings,
so a NEW routing endpoint (a mute-all, a bulk clear) joins this rail on the commit that
registers it and has to ship a caller. A hand-written list is what let two clients sit inert
across four re-confirmations of the issue.
"""

from __future__ import annotations

import time

import pytest
from routing_mute_reachable_shared import REPO, _api_ts_clients, _registered_routes

from personalclaw.agents import routing

WEB_SRC = REPO / "web" / "src"


class TestEveryRoutingEndpointHasAProductCaller:

    def test_every_registered_route_has_an_api_ts_client(self):
        missing = sorted(_registered_routes() - set(_api_ts_clients()))
        assert missing == [], f"registered with no frontend client: {missing}"


class TestTheStoreIsClearableWhateverItHolds:
    @pytest.fixture(autouse=True)
    def _tmp_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
        )
        yield

    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("zz414-real-agent", "zz414-real-agent"),  # an ordinary user agent
            (
                "zz414-phantom-agent",
                "zz414-phantom-agent",
            ),  # never an agent: dismiss does not check
            ("PersonalClaw", "personalclaw"),  # the shipped default agent's MIXED-CASE name
            (
                "personalclaw-coder",
                "personalclaw-coder",
            ),  # reserved: no Advanced disclosure, no slot
            ("  Spaced-Agent  ", "spaced-agent"),  # what a hand-edited store / API client can hold
        ],
    )
    def test_a_mute_is_clearable_and_unmute_reports_the_key_it_cleared(self, name, key):
        """🪤 THE EXPECTED KEY IS SPELLED OUT, not computed by `canonical_agent`.

        The first draft wrote `key = routing.canonical_agent(name)` and a mutant that deleted the
        `.lower()` from `canonical_agent` left every case GREEN — the assertion moved with the
        implementation. Falsified: the mutant now reds `[PersonalClaw]` and `[  Spaced-Agent  ]`.
        """
        now = time.time()
        for _ in range(3):
            st = routing.record_dismiss(name, now=now)
        assert st["muted"] is True, f"{name} did not reach the mute threshold"
        assert st["agent"] == key, f"dismiss reported {st['agent']!r}, expected {key!r}"
        assert key in routing.routing_status()["muted"]
        assert routing.is_suppressed(
            name, now=now + 10**6, cooldown_hours=0.0
        ), "the mute must outlive any cooldown — otherwise this issue is not what it says"
        # The echoed key is what a UI rendering the store's own list must send back.
        assert routing.unmute(name) == key
        assert routing.routing_status() == {"muted": [], "dismissals": {}}
        assert not routing.is_suppressed(name, now=now, cooldown_hours=24.0)

    def test_every_key_the_status_endpoint_reports_is_a_key_unmute_accepts(self):
        """The list surface sends back what the status endpoint gave it — so round-trip it."""
        now = time.time()
        names = ["Alpha-Agent", "zz414-phantom", "personalclaw-lite", "  spaced-agent  "]
        for n in names:
            for _ in range(3):
                routing.record_dismiss(n, now=now)
        reported = routing.routing_status()["muted"]
        # Spelled out, not derived: `Alpha-Agent` and `  spaced-agent  ` must ALREADY be canonical
        # by the time the endpoint reports them, or the UI renders one name and sends another.
        assert sorted(reported) == [
            "alpha-agent",
            "personalclaw-lite",
            "spaced-agent",
            "zz414-phantom",
        ], f"the status endpoint reported non-canonical keys: {reported}"
        for key in list(reported):
            assert routing.unmute(key) == key, f"{key!r} came from status and unmute changed it"
        assert routing.routing_status()["muted"] == []

    def test_unmute_is_idempotent_on_a_name_that_was_never_muted(self):
        assert routing.unmute("never-muted") == "never-muted"
        assert routing.routing_status() == {"muted": [], "dismissals": {}}

    def test_canonical_agent_is_the_one_owner(self):
        """No store function may re-implement the rule the frontend now mirrors.

        🪤 PARSED, NOT GREPPED. A line scan for ``.lower()`` read this module's own docstring —
        which quotes the five inline calls it replaced — and reported the explanation as the
        defect. The same false-green shape the frontend rails in this repo keep re-learning.
        """
        import ast

        src = (REPO / "src" / "personalclaw" / "agents" / "routing.py").read_text()
        tree = ast.parse(src)
        store_fns = {
            "canonical_agent",
            "_load_store",
            "is_suppressed",
            "record_dismiss",
            "unmute",
            "routing_status",
        }
        strays: list[str] = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name not in store_fns:
                continue
            if fn.name == "canonical_agent":
                continue
            for sub in ast.walk(fn):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "lower"
                ):
                    strays.append(f"{fn.name}:{sub.lineno}")
        assert strays == [], (
            "the store's canonicalisation must have exactly one owner — a second copy is how the "
            "frontend's raw comparison stayed wrong across four re-confirmations of issue 414:\n"
            + "\n".join(strays)
        )
        # Vacuity guard: the walk must actually have visited the store functions.
        visited = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in store_fns
        }
        assert visited == store_fns, f"the store functions moved or were renamed: {visited}"
