"""The source-tree checks split from
``tests/test_workflows_controller.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import source_index
import workflows_controller_shared

from personalclaw.workflows.models import Node

_isolated_home = workflows_controller_shared._isolated_home


pytestmark = pytest.mark.anyio


class TestPerNodeStallWindow:
    """🔴 `timeout_stall_secs` was DECLARED BY FOUR SHIPPED TEMPLATES AND READ BY NOTHING.

    `design-project.refine` asks 600s, `general-project.project` 900s,
    `goal-pursuit-open-ended.work` 900s and `goal-pursuit-verifiable.work` 1200s. But
    `liveness.enforce_stall_timeouts` consulted only `services.node_timeout_stall`, so every one
    silently got the 300s default. That fails in the WRONG DIRECTION: `timeout_stall` is meant to
    catch a SILENT node, not a slow one, and a node whose author measured it needing 20 minutes was
    cancelled at 5.
    """

    def _window(self, cfg: dict, *, run_default: int = 300) -> int:
        from personalclaw.workflows import liveness

        class _Svc:
            node_timeout_stall = run_default

        class _Fake:
            services = _Svc()

            def __init__(self, root):
                self.root = root

        root = Node.from_dict(
            {
                "kind": "sequence",
                "id": "r",
                "children": [{"kind": "stage", "id": "work", "config": cfg}],
            }
        )
        return liveness._node_stall_window(_Fake(root), "root.children[0]")

    def test_every_shipped_template_override_is_now_honoured(self) -> None:
        """The four real declarations, read from the bundled library rather than restated — a
        hand-copied list would stop tracking the templates it is meant to protect."""
        import json

        declared: list[tuple[str, int]] = []
        bundled = source_index.REPO / "src/personalclaw/workflows/bundled"
        for path in source_index.package_any_files():
            if path.name != "workflow.json" or path.parent.parent != bundled:
                continue
            spec = json.loads(source_index.text(path))

            def walk(node: object) -> None:
                if isinstance(node, dict):
                    cfg = node.get("config")
                    if isinstance(cfg, dict) and "timeout_stall_secs" in cfg:
                        declared.append((path.parent.name, int(cfg["timeout_stall_secs"])))
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(spec)

        assert (
            declared
        ), "no bundled template declares timeout_stall_secs — did the key get renamed?"
        for name, want in declared:
            assert self._window({"timeout_stall_secs": want}) == want, name
