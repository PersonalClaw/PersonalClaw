"""``npm run typecheck`` must read every TypeScript file under ``web/``.

Before this rail there were two programs — ``web/tsconfig.json`` (``include: ["src"]``) and
``web/tsconfig.sw.json`` (the service worker alone) — and **no** program covered
``web/e2e/``. Fourteen files sat outside the typecheck command (the thirteen ``.ts`` files
under ``web/e2e/`` plus ``playwright.config.ts``), so a type error in a spec shipped
silently. One had: a skip message in ``walkthrough.spec.ts`` read ``.skip`` off the recipe
object instead of off the result, so every skip reason printed ``undefined`` instead of the
reason the recipe supplied. Thirteen of the fourteen are now inside a program;
``e2e/a11y.spec.ts`` is the one documented exception below.

The hole was invisible for the same reason the docs-lint scope was: *nothing asserted what
the gate looks at.* A passing typecheck says only that the files it opened were clean, never
that it opened the files that matter. So this test asserts the SCAN SET, by walking the
tracked ``web/**/*.ts(x)`` files and requiring each to fall inside some program's ``include``
and outside its ``exclude``.

A deliberate exclusion stays possible — it just has to be written down here, which makes it a
reviewable edit rather than a quiet one.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB = REPO_ROOT / "web"

# The programs ``npm run typecheck`` chains, in the order the script runs them.
PROGRAMS = ("tsconfig.json", "tsconfig.sw.json", "tsconfig.e2e.json")

# Files deliberately outside every program. Each entry must name WHY, and the reason must be
# a fact about the tree rather than a preference — an entry here is a file whose types
# nothing checks.
DOCUMENTED_EXCLUSIONS: dict[str, str] = {
    # Two copies of `playwright-core` resolve in this tree (1.62.1 hoisted for
    # `@axe-core/playwright`'s peer range, 1.63.0 nested under `playwright`), so
    # `AxeBuilder({ page })` is handed a `Page` from one and declares one from the other —
    # five structurally identical TS2322s at the five construction sites. `npm dedupe`
    # collapses them to one 1.63.0 and clears all five (measured), but it rewrites
    # package-lock.json by ~6900 lines: a dependency-resolution change, not a typecheck one.
    "e2e/a11y.spec.ts": "duplicate playwright-core copies; needs a lockfile dedupe (5x TS2322)",
    # Two `TS7016`s: `./scripts/buildUiDocs.mjs` and `./scripts/buildServiceWorker.mjs` are
    # plain `.mjs` with no declarations, so each import is an implicit `any`. Typing them
    # means either `allowJs` (which pulls the whole scripts tree into the program) or hand
    # -written `.d.ts` files — a build-configuration decision.
    "vite.config.ts": "untyped ./scripts/*.mjs imports; needs allowJs or .d.ts (2x TS7016)",
    # One `TS2769`: the config object does not match any `defineConfig` overload. A real
    # finding, and fixing it changes how the test runner is configured.
    "vitest.config.ts": "config object matches no defineConfig overload (1x TS2769)",
}

_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


def _load_jsonc(path: Path) -> dict:
    """Parse a tsconfig. They carry ``//`` line comments, which JSON does not allow.

    Only whole-line comments are stripped — the style every tsconfig here uses — so a ``//``
    inside a string value (a URL, say) cannot be mangled into invalid JSON.
    """
    return json.loads(_LINE_COMMENT.sub("", path.read_text(encoding="utf-8")))


def _tracked_web_ts() -> list[str]:
    """Tracked ``web/**/*.ts`` and ``*.tsx`` paths, relative to ``web/`` (POSIX, sorted)."""
    proc = subprocess.run(
        ["git", "ls-files", "web"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    out = []
    for line in proc.stdout.splitlines():
        if not line.endswith((".ts", ".tsx")) or line.endswith(".d.ts"):
            continue
        out.append(line[len("web/") :])
    return sorted(out)


def _matches(spec: str, rel: str) -> bool:
    """Does a tsconfig ``include``/``exclude`` entry cover ``rel``?

    Only the two shapes these configs actually use are supported — a bare directory
    (``"src"``, ``"e2e"``) and an exact file (``"src/sw.ts"``). A glob would silently match
    nothing here, so it is rejected loudly rather than read as "covers everything".
    """
    assert "*" not in spec, (
        f"tsconfig entry {spec!r} uses a glob, which this rail does not interpret — "
        "either use a directory/file path or teach _matches() the glob"
    )
    return rel == spec or rel.startswith(spec.rstrip("/") + "/")


def _covered_by(rel: str) -> str | None:
    """The first chained program that reads ``rel``, or ``None``."""
    for name in PROGRAMS:
        cfg = _load_jsonc(WEB / name)
        includes = cfg.get("include") or []
        excludes = cfg.get("exclude") or []
        if any(_matches(i, rel) for i in includes) and not any(_matches(e, rel) for e in excludes):
            return name
    return None


def test_every_chained_program_exists_and_is_parseable():
    """The scan set is only meaningful if the programs it is derived from are real."""
    for name in PROGRAMS:
        path = WEB / name
        assert path.is_file(), f"{name} is chained by npm run typecheck but does not exist"
        assert _load_jsonc(path).get("include"), f"{name} declares no include"


def test_typecheck_script_chains_every_program():
    """``npm run typecheck`` must actually run each program this rail reasons about.

    Without this, the rail could pass over a program that no command ever executes — the
    shape of a gate that reads green because it never ran.
    """
    script = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["scripts"]["typecheck"]
    # tsconfig.json is tsc's implicit default when no -p is given.
    assert "tsc --noEmit" in script, script
    for name in PROGRAMS[1:]:
        assert f"-p {name}" in script, f"{name} is not chained by `npm run typecheck`: {script}"


def test_every_tracked_web_typescript_file_is_in_a_typecheck_program():
    """The rail: no tracked ``web/**`` TypeScript file may sit outside every program."""
    tracked = _tracked_web_ts()
    assert len(tracked) > 100, (
        f"only {len(tracked)} tracked web TypeScript files found — the census is measuring "
        "nothing, so a pass would prove nothing"
    )
    uncovered = [rel for rel in tracked if _covered_by(rel) is None]
    undocumented = sorted(set(uncovered) - set(DOCUMENTED_EXCLUSIONS))
    assert not undocumented, (
        "these tracked web TypeScript files are outside EVERY typecheck program, so nothing "
        f"checks their types: {undocumented}\n\n"
        "Add them to a program's `include` (preferred), or — if there is a real reason they "
        "cannot be checked — record the reason in DOCUMENTED_EXCLUSIONS so the gap is "
        "reviewable rather than silent."
    )


def test_every_documented_exclusion_is_still_excluded_and_still_exists():
    """A stale exclusion is worse than none: it reads as a known gap that is actually closed.

    Both directions are checked — the file must still exist (a renamed spec would otherwise
    leave a dead entry), and it must still be genuinely uncovered (if a program started
    reading it, the entry is an obsolete apology and should be deleted).
    """
    for rel, why in DOCUMENTED_EXCLUSIONS.items():
        assert (WEB / rel).is_file(), f"DOCUMENTED_EXCLUSIONS names {rel}, which does not exist"
        assert why.strip(), f"{rel} is excluded with no reason given"
        program = _covered_by(rel)
        assert program is None, (
            f"{rel} is now covered by {program}, so its DOCUMENTED_EXCLUSIONS entry is stale "
            "— delete the entry rather than leaving a closed gap documented as open"
        )


def test_the_e2e_specs_are_covered_which_is_the_hole_this_rail_closed():
    """Name the regression explicitly: every e2e spec but the documented one is checked."""
    specs = [rel for rel in _tracked_web_ts() if rel.startswith("e2e/")]
    assert len(specs) >= 12, f"expected the e2e spec corpus, found {specs}"
    missed = [rel for rel in specs if _covered_by(rel) is None and rel not in DOCUMENTED_EXCLUSIONS]
    assert not missed, f"e2e specs outside every typecheck program: {missed}"
    assert _covered_by("playwright.config.ts") is not None, (
        "playwright.config.ts is outside every program — it was one of the thirteen files "
        "the e2e program was added to cover"
    )
