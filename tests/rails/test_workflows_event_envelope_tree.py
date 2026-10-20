"""The source-tree checks split from
``tests/test_workflows_event_envelope.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import workflows_event_envelope_shared

_isolated_home = workflows_event_envelope_shared._isolated_home


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestEnvelope:
    async def test_no_call_site_publishes_a_bare_epoch_key(self) -> None:
        """Regression, found by this suite. `workflow_node_started` passed the NODE's epoch as
        `epoch`, overriding the envelope's RUN epoch — so `node_started` and `node_done` for
        the same node reported different run epochs, and a consumer folding the lower one
        would treat the next real event as superseded and silently stop updating.

        A node's epoch belongs under `node_epoch`. Asserted structurally because the payload
        dicts are spread into the envelope, so a re-introduction would be invisible."""
        from tests.controller_sources import controller_source

        # The controller AND its responsibility modules: a payload built in one of them is spread
        # into the same envelope. `_publish` itself sets the envelope epoch; no OTHER line may pass
        # one in a payload.
        source = controller_source()
        offenders = [
            line.strip()
            for line in source.splitlines()
            if '"epoch":' in line and "self._run_epoch()" not in line
        ]
        assert not offenders, f"payload sets a bare epoch, overriding the envelope: {offenders}"
