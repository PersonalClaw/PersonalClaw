#!/usr/bin/env python3
"""Does this diff need the browser-driven frontend gates? Reads paths, prints a verdict.

`e2e-a11y`, `e2e-walkthrough` and `e2e-pwa` each boot a real gateway, build the SPA and drive
a real Chromium. MEASURED 2026-09-19 over 14 concluded `ci.yml` runs: `e2e-a11y` cost 19.9-29.4
min of wall clock (median 21.9) and was the run's tail in 10 of 14, so it added a median 6.4 and
up to 14.0 min to every PR's time-to-ripe — including PRs whose diff cannot reach a rendered
pixel. Over 60 recent merged PRs, 22 touched no `web/` file at all.

── THE ONE DESIGN RULE: THIS IS AN IGNORE-LIST, NOT A TRIGGER-LIST ──────────────────────────
The verdict DEFAULTS TO TRUE. A path is only out of reach when it matches a rule below that
says so, with a reason. A trigger-list ("run when `web/**` changes") is the same function
inverted and is unsafe for the opposite reason: a new top-level directory, a renamed asset
root, or a served file nobody thought of silently stops paying the gate and nothing reports
it. Default-deny means the failure mode of forgetting to update this file is a job that runs
when it did not have to — wasted minutes, never a missed regression.

── WHY `src/personalclaw/**` IS NOT IGNORABLE (the tempting, wrong entry) ───────────────────
It is 21% of recent merged PRs and skipping it would be the biggest possible saving, and it is
unsound. `web/playwright.config.ts`'s own header states the dependency: the harness "proxies
/api to a gateway the harness STARTS ITSELF — isolated, onboarded and token-authenticated.
Without a gateway the SPA cannot resolve identity, so it renders the ONBOARDING screen for
every route: no NavRail, no shell, no ⌘K listener. axe then reports a clean tree for 96
surfaces it never actually visited." So backend code decides whether the scan sees the app at
all. Sixteen of the eighteen routes in `web/e2e/routes.ts` carry `needsData: true`, their DOM
is rendered from API responses, and `chat.spec.ts` drives the scripted provider that lives in
`src/personalclaw/llm/`. A backend diff can therefore change scanned DOM and can break the
harness outright. It pays.

Verified before each entry below, not assumed:
  · no HTML/Jinja template and no static dir exists under `src/` (`find src -name '*.html'`
    → nothing), so "any served template" is `web/**` and nothing else;
  · `src/personalclaw/` imports neither `harness` nor `personalclaw_client` (grepped);
  · `packages/personalclaw-client-py/` is "NOT part of the core uv workspace" and has its own
    `client` job (ci.yml).

Usage — reads one path per line on stdin, writes `frontend=true|false` to stdout:
    git diff --name-only origin/main... | python3 scripts/ci_touches_frontend.py
"""

from __future__ import annotations

import sys
from typing import Iterable

#: Paths whose change CANNOT alter what a browser renders or whether the harness boots.
#: Each entry is a (predicate-description, reason) pair so a red is self-explaining and a
#: future reader can re-derive whether the reason still holds.
IGNORABLE: tuple[tuple[str, str], ...] = (
    (
        "docs/**",
        "prose only. Nothing under docs/ is served by the gateway or packaged into the wheel "
        "at runtime — pyproject.toml references it only as a Documentation URL.",
    ),
    (
        "*.md at the repo root",
        "README / CHANGELOG / CONTRIBUTING / AGENTS / SECURITY. Deliberately NOT '*.md "
        "anywhere': src/personalclaw/config/prompt_snippets/*.md is model input, not prose.",
    ),
    (
        "tests/**",
        "the pytest suite does not run inside the Playwright harness — the harness boots the "
        "gateway, not pytest — so a test-only change cannot alter scanned DOM.",
    ),
    (
        "harness/**",
        "repo-inner self-verification infra. src/personalclaw/ imports no `harness` module "
        "(grepped), so it is absent from the gateway the harness boots.",
    ),
    (
        "packages/personalclaw-client-py/**",
        "independently versioned/published client, NOT part of the core uv workspace (ci.yml's "
        "`client` job builds it in its own venv) and not imported by src/personalclaw/.",
    ),
    (
        ".github/** except workflows/ci.yml",
        "issue templates, dependabot config and the other workflows cannot change a rendered "
        "pixel. ci.yml is EXCLUDED from this exemption because it defines the three jobs "
        "themselves: editing it must re-validate them, or a broken job definition would be "
        "waved through by the very gate it broke.",
    ),
)

#: The one path inside `.github/` that still pays — it defines the gated jobs.
_SELF = ".github/workflows/ci.yml"


def _is_ignorable(path: str) -> bool:
    """True when `path` provably cannot affect the browser-driven gates.

    Mirrors IGNORABLE above one-to-one. Kept as explicit branches rather than a glob table so
    the `ci.yml` carve-out is impossible to express by accident.
    """
    if path == _SELF:
        return False
    if path.startswith("docs/"):
        return True
    if path.startswith("tests/"):
        return True
    if path.startswith("harness/"):
        return True
    if path.startswith("packages/personalclaw-client-py/"):
        return True
    if path.startswith(".github/"):
        return True
    if "/" not in path and path.endswith(".md"):
        return True
    return False


def touches_frontend(paths: Iterable[str]) -> tuple[bool, list[str]]:
    """Return (verdict, the paths that forced it).

    An EMPTY path set returns `(True, [])`. A diff we could not enumerate is not a diff we know
    to be harmless: the base ref failing to resolve, an API call returning nothing, or a merge
    base that moved would otherwise arrive as a green skip — the absence of an answer dressed as
    a passing one, which is the failure class this repo's rails exist to catch.

    🪤 The empty-set default lives HERE, not in `main()`. It first did live only in `main()`,
    and `tests/test_ci_e2e_path_gate.py::test_an_unenumerated_diff_runs_the_gates` caught it:
    every future caller of this function would have inherited `False` — silently skipping the
    gates on an unknown diff — while the CLI looked correct.
    """
    cleaned = [p.strip() for p in paths if p.strip()]
    if not cleaned:
        return (True, [])
    reaching = sorted({p for p in cleaned if not _is_ignorable(p)})
    return (bool(reaching), reaching)


def main(argv: list[str]) -> int:
    paths = [line.strip() for line in sys.stdin if line.strip()]
    unenumerated = not paths
    verdict, reaching = touches_frontend(paths)

    print(f"frontend={'true' if verdict else 'false'}")
    summary = [
        "### Browser-gate verdict: "
        + ("**run** the frontend e2e jobs" if verdict else "**skip** the frontend e2e jobs"),
        "",
        f"{len(paths)} changed path(s) enumerated.",
    ]
    if unenumerated:
        summary += [
            "",
            "> **No changed path was enumerated, so the gates RUN.** An unknown diff is not a "
            "known-harmless one: if the compare API returned nothing, or a base ref failed to "
            "resolve, skipping here would turn the absence of an answer into a passing one.",
        ]
    if reaching:
        shown = reaching[:20]
        summary += ["", "Paths that can reach a rendered pixel or the harness boot:", ""]
        summary += [f"- `{p}`" for p in shown]
        if len(reaching) > len(shown):
            summary.append(f"- …and {len(reaching) - len(shown)} more")
    else:
        summary += [
            "",
            "Every changed path is in the ignore-list, so `e2e-a11y`, `e2e-walkthrough` and",
            "`e2e-pwa` cannot observe this diff. The reasons, verified rather than assumed:",
            "",
        ]
        summary += [f"- **{what}** — {why}" for what, why in IGNORABLE]
    print("\n".join(summary), file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via tests/test_ci_e2e_path_gate.py
    raise SystemExit(main(sys.argv[1:]))
