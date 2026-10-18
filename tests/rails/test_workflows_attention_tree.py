"""The source-tree checks split from
``tests/test_workflows_attention.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
import workflows_attention_shared

_isolated_home = workflows_attention_shared._isolated_home


class TestTitleAndBody:
    def test_the_SHIPPED_TEMPLATES_still_contain_promptless_gates(self) -> None:
        """The vacuity floor. If every bundled gate ever authored a prompt, the change above would
        be unreachable and the two tests before it would prove nothing about the product."""
        import json

        def walk(node: object, out: list) -> None:
            if isinstance(node, dict):
                if "kind" in node:
                    out.append(node)
                for value in node.values():
                    walk(value, out)
            elif isinstance(node, list):
                for value in node:
                    walk(value, out)

        package = source_index.REPO / "src" / "personalclaw"
        every = source_index.package_any_files()
        packs = sorted(
            p
            for p in source_index.under(every, package / "packs" / "bundled")
            if p.suffix == ".json"
            and p.parent.name == "templates"
            and p.parent.parent.parent == package / "packs" / "bundled"
        )
        workflows = [
            p
            for p in source_index.under(every, package / "workflows" / "bundled")
            if p.suffix == ".json"
        ]
        files = packs + workflows
        assert len(files) > 10, f"the template corpus must be discoverable, found {len(files)}"
        gates, promptless = 0, 0
        for path in files:
            try:
                doc = json.loads(source_index.text(path))
            except Exception:  # noqa: BLE001 — a malformed fixture is not this test's subject
                continue
            nodes: list = []
            walk(doc.get("root") or doc, nodes)
            for node in nodes:
                if str(node.get("kind")) not in ("gate", "approval"):
                    continue
                gates += 1
                cfg = node.get("config") or {}
                if not (cfg.get("prompt") or cfg.get("message")):
                    promptless += 1
        assert gates >= 10, f"only {gates} gates found — the walk is missing nodes"
        assert promptless >= 1, "no promptless gate ships, so the fallback is unreachable again"
