#!/usr/bin/env python3
"""Can this diff change what the first-party apps build against? Reads paths, prints ``sdk=…``.

``ci.yml``'s ``apps-contract`` job checks out PersonalClawApps and runs the apps' SDK contract
checks against the core under review (``scripts/apps_sdk_contract.py``). It has something to say
exactly when a published SDK signature can have changed — and that is a diff under
``src/personalclaw/sdk/``. The checked-in signature snapshot lives there, and
``tests/test_sdk_signature_snapshot.py`` fails any change to a published signature that does not
update it, including a change to a core function the SDK only RE-EXPORTS: #3599 edited
``context.py`` alone, and under this rule its PR would have had to regenerate
``src/personalclaw/sdk/signatures.json``. So "touches the SDK directory" is complete for what the
job checks, which is why this can be a trigger-list where ``ci_touches_frontend.py`` must be an
ignore-list.

The job's own machinery triggers it too, so a change to the check is checked by it. An EMPTY
path list means the enumeration failed, and an unanswered question runs the job.

Usage — one path per line on stdin, ``sdk=true|false`` on stdout:
    git diff --name-only origin/main... | python3 scripts/ci_touches_sdk.py
"""

from __future__ import annotations

import sys
from typing import Iterable

#: A path that starts with one of these (a directory) or equals one (a file) triggers the job.
TRIGGERS: tuple[str, ...] = (
    "src/personalclaw/sdk/",
    "scripts/apps_sdk_contract.py",
    "scripts/ci_touches_sdk.py",
    "scripts/sdk_signature_snapshot.py",
    ".github/workflows/ci.yml",
)


def touches_sdk(paths: Iterable[str]) -> bool:
    changed = [p.strip() for p in paths if p.strip()]
    if not changed:
        return True
    return any(
        path.startswith(t) if t.endswith("/") else path == t for path in changed for t in TRIGGERS
    )


def main() -> int:
    print(f"sdk={'true' if touches_sdk(sys.stdin) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
