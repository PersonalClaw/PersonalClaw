"""The source-tree checks split from
``tests/test_workflow_escalation_is_reported.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import workflow_escalation_is_reported_shared
from rails_repo import REPO

_isolated_home = workflow_escalation_is_reported_shared._isolated_home


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: Resolved from THIS file, the way every other source-reading test here does it — the
#: package's own `__file__` points into site-packages under a non-editable install, where
#: `web/` does not exist.
_REPO_ROOT = REPO


_SRC = _REPO_ROOT / "src" / "personalclaw"


_META = _REPO_ROOT / "web" / "src" / "pages" / "workflows" / "attentionMeta.ts"


async def test_the_reason_vocabulary_is_what_the_frontend_maps():
    """The TS `ESCALATION_REASON` map is keyed on these tokens, and a token with no sentence
    falls through to the raw word. Pinned here so the two `_escalate` call sites cannot add a
    reason the panel has never heard of without something going red.

    Derived from the source rather than restated: `resilience.check_breaker`'s verdicts plus
    `loop/tick.py`'s convergence reasons are the two producers, and both are one edit away
    from growing.

    The controller is a THIRD producer that this scan cannot parse — it spells its tokens as plain
    `reason=` keyword arguments to `_escalate`/`loop_convergence.surface_loop` rather than in either
    producer's shape — so its tokens are listed literally below. That is a gap in the scan, not a
    second vocabulary: a token added there is invisible here, which is exactly how
    `iterations_failed` could have shipped without a sentence. Listed rather than parsed because a
    `reason="…"` regex over the controller also matches the tokens it merely FORWARDS from the other
    two producers, so it would report every breaker verdict as a controller token and prove nothing
    about either.
    """
    resilience_src = (_SRC / "workflows" / "resilience.py").read_text()
    tick_src = (_SRC / "loop" / "tick.py").read_text()
    meta = _META.read_text()

    breaker = set(re.findall(r'BreakerVerdict\(\s*True,\s*"([a-z_]+)"', resilience_src))
    assert len(breaker) >= 4, f"the breaker verdicts moved: {breaker}"
    convergence = set(re.findall(r'reason="([a-z_]+)"', tick_src))
    assert convergence, "no convergence reasons parsed — loop/tick.py moved"

    #: The controller's own tokens — see this test's docstring on why they are listed, not parsed.
    #: Each is asserted to really be in the controller's source (`controller.py` and its
    #: responsibility modules), so a rename there reds here instead of leaving a sentence for a
    #: token nothing produces.
    from tests.controller_sources import controller_source

    controller_src = controller_source()
    controller = {"retries_exhausted", "not_retried", "iterations_failed", "judge_escalated"}
    for token in sorted(controller):
        assert f'"{token}"' in controller_src, (
            f"{token!r} is listed here as a controller token but the controller no longer spells "
            "it — either it was renamed (update this set) or it is gone (drop it and its sentence)"
        )

    for reason in sorted(breaker | convergence | controller):
        assert f"{reason}:" in meta, (
            f"{reason!r} can reach an escalation artifact but attentionMeta.ts has no sentence "
            "for it — the panel would print the raw token"
        )
