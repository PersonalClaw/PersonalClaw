"""The tracked tree must stay fit to publish — and this rail must be able to say otherwise.

``PersonalClaw/PersonalClaw`` is a PUBLIC repository. ``git ls-files`` is precisely the list
of things every clone receives, and ``git rm`` takes a path out of the tree but never out of
history. So an unfit path is close to unrecoverable once merged, and the only moment it is
cheap to refuse is on the PR that adds it.

What this suite guards, measured on ``origin/main`` before the publication-hygiene pass:

* ``temp-screenshots/`` — **80 files, 20.79 MB** of per-PR before/after review evidence,
  accumulated across ~40 PRs. It held the three largest files the repository has ever
  tracked (1.80 MB, 1.38 MB, 0.97 MB), nothing rendered it, and its only mention anywhere
  was a parenthetical in ``docs/demo/CLICKPATH.md`` explaining why a *video* was too big to
  commit.
* ``scratch/`` — 38 files whose CONTENT is load-bearing (the app template is pinned
  byte-for-byte by ``test_app_from_template.py``; the registry is validated by a ``full.yml``
  job) under a directory name that tells a public reader to ignore it. Renamed to
  ``staged-repos/``, not deleted.
* ``.worktrees/`` and ``.local/`` — absent from ``.gitignore``, so a plain ``git add -A``
  staged 25 paths including 20 linked worktrees as **embedded git repositories**: gitlinks
  pointing at local absolute paths that exist on no other machine (#3413).
* two ``/Users/<maintainer>`` literals in ``web/`` tests — the author's real username and
  machine layout, in a public repo, as inert fixture strings.

⚠️  THIS RAIL IS INSIDE ITS OWN INPUT SET, and that ordering bit CI once. ``git ls-files``
    lists TRACKED files, so the rail's own three files enter the census only after they are
    staged — and a local run performed before staging measures a tree that does not yet
    contain the rail. That is how one commit produced a local ``PASS (0 unfit)`` and a red CI
    run: the suite carried the positive-control home path as a LITERAL, so once committed the
    file genuinely was a published real-home path. Two consequences, both load-bearing:
    fixture strings that the rules would match are assembled at RUNTIME (never literals), and
    ``test_the_rail_holds_itself_to_its_own_rules`` asserts these files are in the census and
    on no allowlist. Validate this rail with its changes STAGED, or it measures the wrong tree.

⚠️  A GREEN RAIL THAT CANNOT FIRE IS WORSE THAN NO RAIL, and is how gates in this repo die.
    So the assertion that the tree is clean is only one test here. The rest are POSITIVE
    CONTROLS: each seeds a real git repository containing one specific defect and asserts
    this rail reds on it, and the near-miss tests assert it does NOT red on the ordinary
    names that merely resemble a defect (``template.py``, ``bakeoff.py``, ``.env.example``).
    The real policy file is used throughout — only the TREE is a fixture — so a control that
    passes proves the shipped rules fire, not that a test-local copy of them would.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import check_publication_hygiene as hygiene

REPO_ROOT = Path(__file__).resolve().parents[1]

#: A home-directory owner name that is deliberately NOT on the placeholder allowlist, so the
#: positive control below proves the rule fires on a genuine-looking home path.
#:
#: ⚠️  ASSEMBLED AT RUNTIME, AND THAT IS THE WHOLE POINT. This suite is a tracked file inside
#:     the rail's own input set, so writing the name as a literal `/Users/<name>` would make
#:     this file a published real-home path — and the rail would be RIGHT to red on it. That
#:     is not hypothetical: it shipped, and CI caught it the moment the file became tracked
#:     while a pre-staging local run had passed. Every use below interpolates this constant,
#:     so the bytes that reach the tree are `/Users/{` — and `{` is outside the rule's
#:     `[A-Za-z0-9._-]` owner class, which is what makes the exemption unnecessary rather
#:     than merely unused. `test_the_rails_own_files_inline_no_home_path_literal` pins the
#:     mechanism; `test_the_positive_controls_owner_is_not_on_the_allowlist` pins that the
#:     tempting alternative fix — allowlisting the name — cannot silently neuter the control.
_UNLISTED_OWNER = "zz" + "notaplaceholder" + "zz"

#: The rail's own three files. They are inside its input set and on no allowlist — a rail that
#: exempts itself is the shape that lets a real defect hide in the exempted file later.
_RAIL_OWN_FILES = (
    "tests/test_publication_hygiene_baseline.py",
    "scripts/check_publication_hygiene.py",
    "publication-hygiene-baseline.json",
)


@pytest.fixture(scope="module")
def baseline() -> dict:
    return hygiene.load_baseline()


def _seed_repo(root: Path, files: dict[str, bytes | str]) -> Path:
    """A real one-commit git repo containing exactly ``files``.

    Real, not faked, because the rail's input is ``git ls-files`` — the only thing that
    actually answers "what does a clone get". A fixture that handed the checker a list of
    names would prove the regexes compile and nothing about the rail.
    """
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    # `-A -f`: the point is to reproduce what reaches a PR, and several of these paths are
    # ones a contributor's own .gitignore would have caught. The rail is the layer that
    # runs when .gitignore did not.
    subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
    return root


# ── the rail itself ──────────────────────────────────────────────────────────────────────


def test_the_tracked_tree_carries_no_unfit_path():
    """The whole point. Ships at ZERO, not at a measured population, because the decay was
    removed in the same change that added the rail — so any nonzero result is a regression
    somebody introduced, never a backlog to ratchet down."""
    found = hygiene.violations()
    assert found == [], "unfit paths are tracked in a PUBLIC repo:\n  " + "\n  ".join(found)


def test_the_rail_actually_inspected_the_tree():
    """Vacuity. ``violations()`` returning ``[]`` because it saw NOTHING looks identical to
    the tree being clean, and is the most common way a gate like this dies silently."""
    paths = hygiene.tracked_files()
    assert len(paths) > 4000, f"only {len(paths)} tracked paths — the census is broken"
    assert "README.md" in paths and "pyproject.toml" in paths


def test_the_rail_holds_itself_to_its_own_rules():
    """The rail's three own files must be INSIDE its input set, never exempted.

    Two failures live here, and the first one shipped. **The ordering trap:** the input is
    ``git ls-files``, so a file enters it only once TRACKED. Validating before staging
    measures a tree that does not yet contain the rail — which is how a local
    ``PASS (4989 tracked paths, 0 unfit)`` and a red CI run described the same commit. This
    assertion makes that gap impossible to re-open silently, because the files it names can
    only be absent from the census if someone untracked or exempted them.

    **And the self-exemption trap:** the cheap fix for a rail that flags its own fixture is
    to exempt its own path. That is precisely the shape that lets a REAL defect hide later —
    the exempted file is the one nobody re-reads. So neither this suite nor the checker nor
    the baseline is on any allowlist, and this test is what keeps that true.
    """
    paths = set(hygiene.tracked_files())
    for own in _RAIL_OWN_FILES:
        assert own in paths, f"{own} is outside the rail's own input — it must police itself"

    allowlisted = {e["path"] for e in hygiene.load_baseline()["allowed"]}
    assert not (
        allowlisted & set(_RAIL_OWN_FILES)
    ), "the rail exempted one of its own files — fix the fixture, not the scope"


def test_the_rails_own_files_inline_no_home_path_literal():
    """None of the rail's three own files may contain a matchable ``/Users/<name>``.

    This is the regression that red CI: the positive-control owner was written as a literal
    here, so once the file was committed it *was* a published real-home path and
    ``test_the_tracked_tree_carries_no_unfit_path`` correctly reported it.

    That test would catch a recurrence, so this one is scoped to earn its place rather than
    duplicate it: it checks only the three files whose JOB is to talk about home paths — the
    ones overwhelmingly likely to acquire such a literal — and it fails with the fix in the
    message ("interpolate ``_UNLISTED_OWNER``") instead of the rail's generic "delete the
    path, or move it". It catches ANY non-placeholder owner, not just this suite's control
    name, so an unrelated literal is caught here too and diagnosed the same way.
    """
    pattern = re.compile(hygiene.load_baseline()["real_home_path_rule"]["pattern"])
    placeholders = set(hygiene.load_baseline()["real_home_path_rule"]["placeholder_home_names"])
    offenders = {}
    for path in _RAIL_OWN_FILES:
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        inlined = sorted(o for o in set(pattern.findall(text)) if o not in placeholders)
        if inlined:
            offenders[path] = inlined
    assert offenders == {}, (
        f"{offenders} inline a real-looking home owner as a literal. Interpolate "
        f"_UNLISTED_OWNER instead — a literal here makes this repo publish a home path."
    )


def test_the_positive_controls_owner_is_not_on_the_allowlist():
    """``_UNLISTED_OWNER`` must stay OFF the placeholder allowlist, or the control goes inert.

    The tempting fix for the CI red was to add the control's name to the allowlist. That would
    have gone green while silently destroying the only assertion that proves the home-path rule
    *fires*: if the fixture name is allowlisted, ``tests/leak.py`` stops being a leak and the
    positive control asserts nothing. This is the test that makes that shortcut fail loudly,
    and it is why the fix was to stop making the name a tracked literal instead of widening
    the allowlist by one name for a test's benefit.
    """
    placeholders = set(hygiene.load_baseline()["real_home_path_rule"]["placeholder_home_names"])
    assert _UNLISTED_OWNER not in placeholders, (
        f"{_UNLISTED_OWNER!r} was added to the placeholder allowlist, which makes the "
        "home-path positive control vacuous. Revert that and interpolate the constant instead."
    )
    # …and the rule really does reject it, so the control is live rather than merely unlisted.
    assert re.compile(hygiene.load_baseline()["real_home_path_rule"]["pattern"]).findall(
        f"/Users/{_UNLISTED_OWNER}"
    ) == [_UNLISTED_OWNER]


# ── positive controls: each rule must fire on the defect it exists for ───────────────────


def test_a_readded_temp_screenshots_directory_reds(tmp_path):
    """The symptom the owner found. `temp-screenshots/` is a residue NAME, so the rule reds
    on the path alone — it never has to reason about the PNG's content."""
    root = _seed_repo(
        tmp_path / "r",
        {"temp-screenshots/some-pr/before.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 64},
    )
    found = hygiene.violations(root)
    assert any("residue-name: temp-screenshots/some-pr/before.png" in f for f in found), found


@pytest.mark.parametrize(
    "path",
    [
        "scratch/registry/app-registry.json",
        "tmp/notes.md",
        "wip-redesign/plan.md",
        "docs/old.md",
        "src/personalclaw/foo.py.orig",
        "web/src/App.tsx.rej",
        "notes.md~",
        "web/dist/index.js",
        "node_modules/left-pad/index.js",
        "coverage/lcov.info",
        ".worktrees/lane-x/README.md",
        ".local/state/gh/device-id",
        ".claude/settings.json",
        ".dev-home/.local_secret",
        ".secrets.env",
        ".env",
        ".env.local",
    ],
)
def test_each_denied_path_shape_reds(tmp_path, path):
    """One control per denied shape. Parametrised so a rule someone later weakens reds HERE,
    naming the shape that stopped being caught, instead of quietly widening what publishes."""
    root = _seed_repo(tmp_path / path.replace("/", "_"), {path: "x\n"})
    assert hygiene.violations(root), f"{path} published clean — a rule stopped firing"


def test_an_oversized_binary_reds_and_oversized_text_does_not(tmp_path):
    """The size half. A 1.8 MB PNG was the largest file this repo ever tracked; a 1.8 MB
    CHANGELOG is ordinary. The discriminator is binary-ness, so both halves are asserted —
    a rule that red on size alone would red the CHANGELOG and be switched off within a week.
    """
    cap = hygiene.load_baseline()["binary_size_rule"]["max_bytes"]
    root = _seed_repo(
        tmp_path / "r",
        {
            "docs/big.png": b"\x89PNG\r\n\x1a\n" + b"\0" * (cap + 1),
            "docs/big.md": "y" * (cap + 1),
        },
    )
    found = hygiene.violations(root)
    assert any("oversized-binary: docs/big.png" in f for f in found), found
    assert not any("docs/big.md" in f for f in found), f"text tripped the size rule: {found}"


def test_a_real_home_path_reds_and_a_placeholder_stays_green(tmp_path):
    """The content half, and the reason it is an ALLOWLIST of placeholder names: an unknown
    home-directory owner reds the first time it appears, without the maintainer's real
    username ever being written into the public repo to keep it out.

    The offending name is built at RUNTIME (see ``_UNLISTED_OWNER``) — this suite is itself
    a tracked file inside the rail's own input, so a literal here would be a published home
    path and the rail would correctly red on it. That shipped once; see
    ``test_the_home_path_fixture_is_never_a_tracked_literal``.
    """
    root = _seed_repo(
        tmp_path / "r",
        {
            "tests/leak.py": f"HOME = '/Users/{_UNLISTED_OWNER}'\n",
            "tests/fine.py": "HOME = '/Users/me'\nALSO = '/home/u/x'\n",
        },
    )
    found = hygiene.violations(root)
    assert any("real-home-path: tests/leak.py" in f for f in found), found
    assert any(_UNLISTED_OWNER in f for f in found), f"the rule did not name the owner: {found}"
    assert not any("tests/fine.py" in f for f in found), f"a placeholder red: {found}"


def test_a_binary_file_is_not_scanned_for_home_paths(tmp_path):
    """A PNG whose bytes happen to spell a home path is not a leak, and decoding every
    binary in the tree to find out would make the rail slow enough to be disabled."""
    root = _seed_repo(
        tmp_path / "r",
        {"docs/x.png": b"\x89PNG\r\n\x1a\n\0/Users/" + _UNLISTED_OWNER.encode() + b"/s\0"},
    )
    assert not any("real-home-path" in f for f in hygiene.violations(root))


# ── near misses: the rail must not red on ordinary names ─────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "src/personalclaw/action_providers/template.py",  # `temp` + `l`, not `temp` + delim
        "src/personalclaw/evals/bakeoff.py",  # `bak` + `e`
        ".env.example",  # the deliberately-tracked template
        "docs/guides/oldest-first.md",  # `old` + `e`
        "web/src/pages/debugger.tsx",  # `debug` + `g`
        "src/personalclaw/tempo.py",  # `temp` + `o`
        "tools/scratchpad_ui.py",  # `scratch` + `p`
    ],
)
def test_an_ordinary_name_that_merely_resembles_residue_stays_green(tmp_path, path):
    """The rule is delimiter-anchored for exactly these. A residue rule that red on any name
    merely STARTING with a residue word would red ``template.py`` — and a rail that reds
    ordinary work teaches everyone to edit the policy, which kills every rule in the file."""
    root = _seed_repo(tmp_path / path.replace("/", "_"), {path: "x\n"})
    found = hygiene.violations(root)
    assert found == [], f"{path} is ordinary but red: {found}"


def test_the_tracked_env_example_carries_no_real_value():
    """``.env.example`` is deliberately published and deliberately exempt from the dotenv
    rule, so its harmlessness is a CLAIM — pinned here rather than verified once by hand.
    Every variable must be commented out; an uncommented assignment with a value means a
    real key was pasted into the template."""
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    live = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
    assert live == [], f".env.example carries uncommented assignments: {live}"


# ── the policy file must stay honest ────────────────────────────────────────────────────


def test_every_rule_carries_a_rationale(baseline):
    """A denylist entry without a stated reason becomes undeletable: nobody can tell whether
    removing it is safe, so it survives forever and the file stops being readable policy."""
    rules = [
        *baseline["path_rules"],
        baseline["binary_size_rule"],
        baseline["real_home_path_rule"],
    ]
    assert rules, "no rules loaded"
    for rule in rules:
        assert rule.get("rationale", "").strip(), f"{rule['name']} has no rationale"
        assert len(rule["rationale"]) > 80, f"{rule['name']}'s rationale is a placeholder"


def test_every_allowed_entry_is_still_tracked_and_still_needed(baseline):
    """A stale exemption is worse than no exemption: it is a standing hole nobody re-reads.
    Each entry must name a path that still exists, still matches the rule it is exempted
    from, and still explains itself."""
    tracked = set(hygiene.tracked_files())
    rules = {r["name"]: r for r in baseline["path_rules"]}
    assert baseline["allowed"], "the allowlist is empty — drop the mechanism or use it"
    for entry in baseline["allowed"]:
        assert entry["path"] in tracked, f"{entry['path']} is exempted but no longer tracked"
        assert entry["rule"] in rules, f"{entry['path']} cites unknown rule {entry['rule']}"
        pattern = rules[entry["rule"]]["pattern"]
        assert re.search(pattern, entry["path"]), (
            f"{entry['path']} no longer matches {entry['rule']} — the exemption is dead "
            "and should be deleted, not carried"
        )
        assert len(entry.get("rationale", "")) > 80, f"{entry['path']} has no real rationale"


def test_the_baseline_is_valid_json_and_names_its_checker(baseline):
    """The rails in this repo are discoverable from their data file; this one says which
    module measures it, so a red leads to the code instead of to a grep."""
    assert baseline["generated_from"] == "scripts/check_publication_hygiene.py"
    assert (REPO_ROOT / baseline["generated_from"]).is_file()
    assert baseline["how_to_fix_a_red"].strip()
    # Round-trips: an editor that broke the JSON must fail here, not at the next red.
    json.loads(hygiene.baseline_path().read_text(encoding="utf-8"))


def test_the_removed_paths_are_gone_and_the_kept_ones_remain():
    """The specific decisions of the publication-hygiene pass, pinned so a later merge that
    resurrects one of them reds by name rather than by rule."""
    tracked = set(hygiene.tracked_files())
    assert not [p for p in tracked if p.startswith("temp-screenshots/")]
    assert not [p for p in tracked if p.startswith("scratch/")]
    assert "AGENT.md" not in tracked, "AGENT.md is one character from AGENTS.md — a trap"
    # …and the deliberate keeps, so "hygiene" never becomes an excuse to delete these.
    assert "docs/screenshots/light/01-dashboard.png" in tracked
    assert "docs/screenshots/dark/01-dashboard.png" in tracked
    assert "staged-repos/app-template/app.json" in tracked
    assert "staged-repos/registry/app-registry.json" in tracked
    assert ".env.example" in tracked
    assert "AGENTS.md" in tracked and "CLAUDE.md" in tracked
