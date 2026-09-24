#!/usr/bin/env python3
"""Type-check each bundled app's own modules — ONE BUNDLE PER MYPY RUN.

Why this script exists instead of one more path on ``mypy``'s argv:

``mypy`` maps every file it is given to a module name. A bundled app lives at
``src/personalclaw/apps/native/<name>/`` — a directory whose name contains hyphens and
which has no ``__init__.py``, so it is not a package — and the module inside it is
conventionally called ``provider.py``. With ``mypy_path = "src"`` that file maps to the
top-level module ``provider``. One such bundle is fine. The SECOND one makes mypy abort
the whole run before checking anything:

    src/personalclaw/apps/native/personalclaw-ui-docs/provider.py: error: Duplicate
    module named "provider" (also at "src/personalclaw/apps/native/ollama-models/provider.py")
    Found 1 error in 1 file (errors prevented further checking)

That is not a naming accident to be papered over. The native capability contract
(``apps/native_contract.py``) *expects* it — ``providers/loader.py`` namespaces bundle
modules in ``sys.modules`` precisely because "two apps commonly ship the same bare
``provider.py``". So the collision is a property of the design, and the fix belongs in how
the checker is invoked, not in what the apps are allowed to call their files.

The alternative considered and rejected: exclude the bundled tree from mypy entirely. Today
``personalclaw-ui-docs/provider.py`` IS type-checked; dropping it would silently retire the
gate over every line of bundled provider code at the moment a second bundle arrives — a
weakened gate dressed up as a config tidy.

One process per bundle, so each run sees exactly one ``provider``. Exit code is 1 if any
bundle fails, and every bundle is attempted so a contributor sees all the violations at
once (same reason the CI lint step runs each tool even after one fails).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = REPO_ROOT / "src" / "personalclaw" / "apps" / "native"


def bundle_modules(bundle: Path) -> list[Path]:
    """Every Python file a bundle ships, ``__pycache__`` excluded."""
    return sorted(p for p in bundle.rglob("*.py") if "__pycache__" not in p.parts)


def main(argv: list[str]) -> int:
    python = argv[1] if len(argv) > 1 else sys.executable
    if not NATIVE_DIR.is_dir():
        print(f"[lint_bundled_apps] no bundled-app tree at {NATIVE_DIR}", file=sys.stderr)
        return 1
    bundles = sorted(p for p in NATIVE_DIR.iterdir() if p.is_dir() and (p / "app.json").is_file())
    checked = 0
    failed: list[str] = []
    for bundle in bundles:
        modules = bundle_modules(bundle)
        if not modules:
            continue  # an app.json-only bundle has nothing to type-check
        checked += 1
        print(f"[lint_bundled_apps] mypy {bundle.name} ({len(modules)} module(s))", flush=True)
        proc = subprocess.run(
            [python, "-m", "mypy", *[str(m) for m in modules]],
            cwd=REPO_ROOT,
            check=False,
        )
        if proc.returncode != 0:
            failed.append(bundle.name)
    if not checked:
        # The vacuity floor, matching tests/test_native_capability_contract.py's: a run that
        # checked nothing must not report success, or this script becomes a green no-op the
        # day the exemplar regresses to a core dotted path.
        print(
            "[lint_bundled_apps] FAIL: no bundled app ships a module — nothing was checked. "
            "APE-5's claim is that a bundled app CAN own its provider code; if that is no "
            "longer true, delete this script rather than letting it pass vacuously.",
            file=sys.stderr,
        )
        return 1
    if failed:
        print(f"[lint_bundled_apps] FAIL: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"[lint_bundled_apps] OK: {checked} bundled app(s) type-check clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
