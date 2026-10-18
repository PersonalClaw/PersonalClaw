"""The source-tree checks split from
``tests/test_routing_mute_reachable.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import source_index
from routing_mute_reachable_shared import (
    _ROUTE_PREFIX,
    API_TS,
    REPO,
    ROUTE_TABLE,
    _api_ts_clients,
    _registered_routes,
)


@lru_cache(maxsize=1)
def _product_sources() -> tuple[tuple[Path, str], ...]:
    """Every non-test frontend source + its text. A test-only caller is not a product surface.

    🪤 READ ONCE. The first draft re-walked and re-read ~900 files in each of four tests and the
    file took 4m50s under `-n auto`; a rail slow enough to be skipped guards nothing.

    🪤 THE SUFFIX MUST BE EXACT. `rglob("*.ts*")` also matches `ChatPanel.tsx.bak` — and a mutation
    drive that backed a file up in place made this rail read the UNMUTATED copy and stay green on a
    mutant that removed the iteration entirely. A scanner that can see a stale twin of the file it
    is auditing is worse than no scanner.
    """
    return tuple(
        (p, source_index.text(p))
        for p in source_index.web_files()
        if p.suffix in {".ts", ".tsx"}
        and not re.search(r"\.(test|spec|doc)\.tsx?$", p.name)
        and p.name != "api.ts"
    )


class TestEveryRoutingEndpointHasAProductCaller:
    def test_the_derivation_is_not_vacuous(self):
        routes = _registered_routes()
        clients = _api_ts_clients()
        assert ROUTE_TABLE.exists() and API_TS.exists()
        # The three shipped routes: status, dismiss, unmute. A floor, so the rail fires when the
        # scanner breaks (renamed helper, reformatted registration) rather than reading as clean.
        assert len(routes) >= 3, f"the route scanner found {routes} — it broke, not the tree"
        assert _ROUTE_PREFIX + "unmute" in routes, "the clearing route must still be registered"
        assert _ROUTE_PREFIX + "status" in routes
        assert len(_product_sources()) > 300, "the frontend walk must find the tree"
        assert len(clients) >= 3, f"api.ts clients found: {clients}"

    def test_every_client_has_at_least_one_non_test_caller(self):
        """The whole defect, in one assertion.

        ``routingUnmute`` and ``routingStatus`` were defined in ``api.ts`` and called by NOTHING —
        a repo-wide grep returned only the two definition lines. An endpoint with no caller has
        never been exercised by the product, so "it exists" is not the same as "it works".
        """
        sources = _product_sources()
        callers: dict[str, list[str]] = {}
        for path, name in _api_ts_clients().items():
            hits = [
                str(p.relative_to(REPO))
                for p, text in sources
                if re.search(rf"\bapi\.{re.escape(name)}\s*\(", text)
            ]
            callers[f"{name}  ({path})"] = hits
        inert = sorted(k for k, v in callers.items() if not v)
        assert (
            inert == []
        ), "a routing endpoint the product cannot reach — the shape of issue 414:\n" + "\n".join(
            inert
        )

    def test_the_whole_muted_list_is_iterated_somewhere_not_just_membership_tested(self):
        """A per-agent membership test cannot reach an orphan key.

        ``AgentDetail`` asks "is THIS agent muted", which is right for its surface and structurally
        blind to a mute whose name has no agent page (a phantom, a deleted agent, a reserved
        built-in). Some product surface has to iterate the list itself.

        🪤 SCOPE, HONESTLY: this is a source-level check that a full-list iteration EXISTS. Whether
        it is actually reached is a render question and belongs to the surface's own drive —
        `web/src/pages/settings/routingMuteReachable.test.tsx` mounts `ChatPanel` and asserts the
        rows, the phantom key among them. Falsified together: gating the field to `{false && …}`
        leaves this green (the component is still in the file) and reds five of those.
        """
        # 🪤 ANCHORED ON `muted`, not "a `muted` somewhere near a `.map(`". The first form allowed
        # 400 characters of slack, so a mutant that changed the iteration to `[].map(…)` stayed
        # green — the local `const muted = …` a few lines up satisfied the loose window.
        iterates_muted = re.compile(r"\bmuted\b\s*(?:\|\|\s*\[\]|\?\?\s*\[\])?\s*\)?\s*\.map\(")
        iterating = [
            str(p.relative_to(REPO))
            for p, text in _product_sources()
            if "routingStatus" in text and iterates_muted.search(text)
        ]
        assert iterating, (
            "no surface iterates routing_status().muted, so a mute for a name with no agent "
            "detail page stays unreachable — measured live for zz414-phantom-agent"
        )

    def test_every_comparison_against_a_muted_name_canonicalises(self):
        """The store's identity is case-insensitive; a raw comparison silently misses a mute.

        `muted == ["personalclaw"]` with `is_suppressed("PersonalClaw") is True`, and
        `["personalclaw"].includes("PersonalClaw")` is false — so the detail page reported the
        shipped default agent as "Active" while the router was refusing to suggest it.
        """
        offenders: list[str] = []
        for p, text in _product_sources():
            for m in re.finditer(
                r"\bmuted\s*\|\|\s*\[\]\)?\s*\)?\s*\.(includes|some|indexOf)\(", text
            ):
                window = text[m.start() : m.start() + 220]
                if "canonicalAgentKey" not in window:
                    offenders.append(f"{p.relative_to(REPO)}: {window.splitlines()[0].strip()}")
        assert offenders == [], (
            "compare a muted name through lib/agents' canonicalAgentKey — the raw form was the "
            "bug:\n" + "\n".join(offenders)
        )
