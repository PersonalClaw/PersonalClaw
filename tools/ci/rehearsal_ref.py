"""The tag a release of this tree would be, for the release rehearsal (#3942).

    python3 -I tools/ci/rehearsal_ref.py pyproject.toml

prints ``ref=vX.Y.Z`` from the project's own ``version``, for ``$GITHUB_OUTPUT``. The rehearsal
builds as that tag, so every job reads the version a release of this tree would, and the wheel's
contract checks it against the wheel. A version that is not plain X.Y.Z (no pre-release, no local
part) is refused, exit 1: the release's tag parser (scripts/release_tags.py) would refuse its tag.
Standard library only, so a job runs it as a script.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PLAIN = re.compile(r"^\d+\.\d+\.\d+$")


def rehearsal_ref(pyproject: Path) -> str:
    """``vX.Y.Z`` from the ``[project] version`` of *pyproject*."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
    version = project.get("version")
    if not isinstance(version, str) or not PLAIN.match(version):
        raise ValueError(f"{pyproject}: [project] version {version!r} is not X.Y.Z")
    return f"v{version}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("rehearsal_ref.py: give the pyproject.toml to read", file=sys.stderr)
        return 2
    try:
        print(f"ref={rehearsal_ref(Path(args[0]))}")
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"rehearsal_ref.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
