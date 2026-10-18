"""The source-tree checks split from
``tests/test_token_estimate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from token_estimate_shared import _CANONICAL, _LOCAL_DEFINITION, _SRC


class TestOneDefinition:
    def test_no_module_defines_its_own_ratio(self) -> None:
        offenders: list[str] = []
        for path in source_index.package_files():
            if path == _CANONICAL:
                continue
            text = source_index.text(path)
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _LOCAL_DEFINITION.search(line):
                    offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "these modules restate the chars-per-token ratio instead of importing it from "
            "personalclaw.token_estimate:\n" + "\n".join(offenders)
        )
