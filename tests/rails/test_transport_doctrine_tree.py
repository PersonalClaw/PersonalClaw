"""The source-tree checks split from
``tests/test_transport_doctrine.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

from transport_doctrine_shared import _read


def test_workflow_engine_sse_events_are_all_registered_in_the_frontend():
    """Every event the v2 workflow engine publishes MUST be in the FE
    WORKFLOW_LIFECYCLE union — EventSource silently DROPS event types with no registered
    listener, so an unlisted publish is a live update that never arrives, invisible in
    every test that does not assert the list itself. Same drift class as the loop cockpits
    above; pinned here so a new `_publish(...)` without the matching FE
    listener fails CI instead of silently never reaching an open run view.
    """
    from tests.controller_sources import controller_source

    controller = controller_source()
    service = _read("src/personalclaw/workflows/service.py")
    fe = _read("web/src/pages/workflows/useWorkflowStream.ts")

    # `self._publish("EVENT", {...})` in the controller and `ctl._publish(...)` in its
    # responsibility modules (together the sole publisher of run/node lifecycle) + the
    # blocking-mode progress tick the service layer emits.
    published = set(re.findall(r'\b(?:self|ctl)\._publish\(\s*"(workflow_[a-z_]+)"', controller))
    # Measured at 17 when the controller was split into its responsibility modules: a scan that
    # finds fewer has stopped reading one of them, and would pass on what it no longer sees.
    assert len(published) >= 17, f"the publish scan narrowed to {sorted(published)}"
    published |= set(re.findall(r'_publish\(\s*"(workflow_[a-z_]+)"', service))

    m = re.search(r"export const WORKFLOW_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m, "couldn't find the WORKFLOW_LIFECYCLE union in useWorkflowStream.ts"
    registered = set(re.findall(r"'(workflow_[a-z_]+)'", m.group(1)))

    missing = published - registered
    assert not missing, (
        f"The workflow engine publishes SSE events the FE useWorkflowStream never listens "
        f"for: {sorted(missing)} — add them to WORKFLOW_LIFECYCLE or they'll silently never "
        f"reach the run view."
    )
