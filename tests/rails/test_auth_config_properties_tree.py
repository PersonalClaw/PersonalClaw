"""The source-tree checks split from
``tests/test_auth_config_properties.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import re

import pytest
import source_index
from rails_repo import REPO

REPO_SRC = REPO / "src"


_RESIDUE_TERMS = [
    r"\bAcpProvider\b",  # old class name, replaced by AcpAgentProvider
    r"SlackMcpClient",  # deleted class
    r"_find_slack_mcp",  # deleted function
]


@pytest.mark.parametrize("term", _RESIDUE_TERMS)
def test_no_residue_in_python_src(term):
    """Symbols of deleted code paths must not appear in src/."""
    pattern = re.compile(term)
    violations: list[str] = []

    for py_file in source_index.package_files():
        # Skip test files for the residue pattern check
        if "test" in py_file.parts:
            continue
        text = source_index.text(py_file)
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip pure comment lines — residue in comments is documentation
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                rel = py_file.relative_to(REPO_SRC.parent)
                violations.append(f"  {rel}:{lineno}: {line.strip()[:120]}")

    assert not violations, f"banned term {term!r} found in source:\n" + "\n".join(violations[:10])
