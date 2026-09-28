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

And, measured later: an office document published with its saving software's custom
properties, which sat in a compressed XML part that no text rule reads. The office-document
rule reads them, and the two content rules now read an office document's XML parts too.

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
    passes proves the shipped rules fire, not that a test-local copy of them would. The one
    exception is the internal-reference rule's VOCABULARY, which is private and so never in
    this repository: its controls read a list of invented words through the parser the real
    list goes through, and the real tree meets the real list only where
    ``PERSONALCLAW_PRIVATE_DENYLIST`` names it (the maintainer's landing), skipping elsewhere.
"""

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import docx
import openpyxl
import pptx
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

#: The internal-reference rule's vocabulary is PRIVATE: it lives in a file outside every
#: repository, which ``PERSONALCLAW_PRIVATE_DENYLIST`` names, and this public suite never sees
#: it. So the rule's positive controls read a list of INVENTED words instead — one entry per kind,
#: spelled as the real list spells its entries, through the same parser. Every word here means
#: nothing anywhere, and none may ever be swapped for a real name: this file is public.
_SYNTHETIC = {
    "word": "zzbrindlecopse",
    "phrase": "zzmarrowfen quelt",
    "host": "zzwhitlowgate.invalid",
    "code": "zzv-17",
    "id": "zzv_Qm4pR8sT2wX6yZ",
}
#: A salt the synthetic list names: a digest of an entry made with it must red too.
_SYNTHETIC_SALT = "zzsynthetic/publication-hygiene/v0"
_SYNTHETIC_LIST = (
    "# invented entries: the rail's own tests\n"
    + "".join(f"{kind:<7} {text}\n" for kind, text in _SYNTHETIC.items())
    + f"salt    {_SYNTHETIC_SALT}\n"
)
_DENYLIST = hygiene.parse_private_denylist(_SYNTHETIC_LIST, "the synthetic list")


def _planted(kind: str) -> str:
    """A line of ordinary prose carrying the *kind* entry the way a real reference would.
    The phrase wraps across a line break, as a wrapped comment carries one."""
    head, _, tail = _SYNTHETIC["phrase"].partition(" ")
    return {
        "word": f"# Validated per the {_SYNTHETIC['word'].upper()} guidance.\n",
        "phrase": f"# Sign in with {head}\n#   {tail} before the first run.\n",
        "host": f"See https://docs.{_SYNTHETIC['host']}/page for the rule.\n",
        "code": f"# ({_SYNTHETIC['code'].upper()} boundary validation)\n",
        "id": f"# cited as ``{_SYNTHETIC['id']}``\n",
    }[kind]


def _write_list(directory: Path, text: str = _SYNTHETIC_LIST) -> Path:
    """The synthetic list as a file, which is how the rail is handed a real one."""
    path = directory / "private-denylist.txt"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def baseline() -> dict:
    return hygiene.load_baseline()


@pytest.fixture(scope="module")
def private_findings(baseline) -> list[str]:
    """The internal-reference rule over the REAL tree with the REAL private list, computed once.

    Skipped where the list is not named — in CI, a fork, a contributor's clone — for the
    reason the rule itself is: the vocabulary cannot be published. The maintainer's landing
    names it, so this runs before anything lands; the synthetic tests below run everywhere.
    """
    listed = hygiene.private_denylist()
    if listed is None:
        pytest.skip(f"{hygiene.PRIVATE_DENYLIST_ENV} is not set: the vocabulary is private")
    return hygiene.internal_references(hygiene.tracked_files(), listed, baseline)


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
    somebody introduced, never a backlog to ratchet down. The internal-reference rule runs
    in it where the private list is named, and is skipped (with the others still run) where not."""
    found = hygiene.violations(denylist=hygiene.private_denylist())
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
    found = hygiene.violations(root, denylist=_DENYLIST)
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
    assert hygiene.violations(
        root, denylist=_DENYLIST
    ), f"{path} published clean — a rule stopped firing"


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
    found = hygiene.violations(root, denylist=_DENYLIST)
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
    found = hygiene.violations(root, denylist=_DENYLIST)
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
    assert not any("real-home-path" in f for f in hygiene.violations(root, denylist=_DENYLIST))


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
    found = hygiene.violations(root, denylist=_DENYLIST)
    assert found == [], f"{path} is ordinary but red: {found}"


def test_the_tracked_env_example_carries_no_real_value():
    """``.env.example`` is deliberately published and deliberately exempt from the dotenv
    rule, so its harmlessness is a CLAIM — pinned here rather than verified once by hand.
    Every variable must be commented out; an uncommented assignment with a value means a
    real key was pasted into the template."""
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    live = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
    assert live == [], f".env.example carries uncommented assignments: {live}"


# ── internal references: a public repo must not name a non-public system ─────────────────


def test_the_tracked_tree_names_no_internal_system(private_findings):
    """The internal-reference rule on its own, so its verdict stays legible while another rule
    is red. Ships at zero: every reference was rewritten to the principle it stood for in the
    change that added the rule (measured on the tree before it: 88 references in 43 files)."""
    named = [f for f in private_findings if " names a denied " in f]
    assert named == [], "a PUBLIC repo names a non-public system:\n  " + "\n  ".join(named)


def test_no_tracked_file_carries_a_digest_or_encoding_of_a_private_entry(private_findings):
    """No form of the private vocabulary is published: no digest of an entry (hex or base64,
    plain or salted with a salt the policy publishes) and no base64 or hex of one. A published
    digest list is the leak this rule was moved out of the tree to end — anyone can hash a
    dictionary of likely names against it — so it is asserted on its own."""
    encoded = [f for f in private_findings if " carries the " in f]
    assert encoded == [], "a PUBLIC repo carries an encoded private name:\n  " + "\n  ".join(
        encoded
    )


def test_the_private_lists_encoded_forms_fire_on_the_tree_it_reads():
    """Vacuity for the test above, with the REAL list: a digest of a real entry — plain, and
    salted with each salt the list names — built in memory and appended to a real tracked
    file's text, must red. So a green there is the tree being clean, not the encoded forms
    never matching anything."""
    listed = hygiene.private_denylist()
    if listed is None:
        pytest.skip(f"{hygiene.PRIVATE_DENYLIST_ENV} is not set: the vocabulary is private")
    kind = next(k for k in hygiene.DENYLIST_ENTRY_KINDS if listed[k])
    entry = min(listed[kind])
    planted = [hashlib.sha256(entry.encode("utf-8")).hexdigest()] + [
        hygiene.internal_reference_digest(kind, entry, salt) for salt in sorted(listed["salt"])
    ]
    matcher = hygiene.InternalReferenceMatcher(listed)
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert matcher.located_encodings(readme) == []
    found = matcher.located_encodings(readme + "\n" + "\n".join(planted) + "\n")
    assert [(encoding, hit) for _, encoding, hit, _ in found] == [("sha256", kind)] + [
        ("salted sha256", kind)
    ] * len(listed["salt"])


def test_the_internal_reference_scan_reads_the_tree_it_claims_to():
    """Vacuity. A reader that silently decoded nothing would keep the tree tests green forever,
    so the text census must cover most of the tracked tree — and an entry appended to a REAL
    tracked file's own content must still red through the matcher, as written and encoded."""
    texts = dict(hygiene._text_blobs(hygiene.tracked_files(), REPO_ROOT))
    assert len(texts) > 4000, f"only {len(texts)} tracked text files were read"
    matcher = hygiene.InternalReferenceMatcher(_DENYLIST)
    readme = texts["README.md"]
    assert matcher.located(readme) == [] and matcher.located_encodings(readme) == []
    assert matcher.located(readme + "\n" + _planted("word")), "the planted word did not fire"
    digest = hashlib.sha256(_SYNTHETIC["word"].encode("utf-8")).hexdigest()
    assert matcher.located_encodings(readme + "\n" + digest), "the planted digest did not fire"


@pytest.mark.parametrize("kind", sorted(_SYNTHETIC))
def test_each_internal_reference_kind_reds_on_its_planted_entry(tmp_path, kind):
    """One positive control per kind, through a real seeded repo and a list read by the same
    parser the real one is. The planted line is line 2, and the wrapped phrase must be placed on
    its first word's line."""
    root = _seed_repo(tmp_path / kind, {"docs/notes.md": "An ordinary line.\n" + _planted(kind)})
    found = [
        f
        for f in hygiene.violations(root, denylist=_DENYLIST)
        if f.startswith("internal-reference:")
    ]
    expected = f"internal-reference: docs/notes.md:2 names a denied {kind} "
    assert len(found) == 1 and found[0].startswith(expected), found


@pytest.mark.parametrize(
    "spelled",
    [
        lambda w: w.upper(),
        lambda w: f"search_{w}_helper",
        lambda w: f"{w}-mcp",
        lambda w: f"{w.capitalize()}Service",
    ],
    ids=["upper-case", "snake_case", "kebab-case", "CamelCase"],
)
def test_a_denied_word_is_read_inside_an_identifier(tmp_path, spelled):
    """A name reaches the tree inside identifiers as often as in prose: a test named after it,
    a ``Service`` class, an MCP server id. Each spelling folds to the same word."""
    body = f"value = {spelled(_SYNTHETIC['word'])}\n"
    root = _seed_repo(tmp_path / "r", {"src/module.py": body})
    found = [
        f
        for f in hygiene.violations(root, denylist=_DENYLIST)
        if f.startswith("internal-reference:")
    ]
    assert found and found[0].startswith("internal-reference: src/module.py:1 names a denied word")


def test_a_denied_name_in_a_path_reds(tmp_path):
    """A file NAME is published exactly as its content is, so paths are checked too."""
    name = f"docs/{_SYNTHETIC['word']}-notes.md"
    root = _seed_repo(tmp_path / "r", {name: "Ordinary content.\n"})
    found = hygiene.violations(root, denylist=_DENYLIST)
    assert any(f.startswith(f"internal-reference: {name} — its PATH") for f in found), found


@pytest.mark.parametrize(
    "text",
    [
        _SYNTHETIC["word"] + "ish",
        "docs." + _SYNTHETIC["host"] + ".example",
        "not" + _SYNTHETIC["host"],
        _SYNTHETIC["code"].split("-")[0] + "-1.6.0",
        _SYNTHETIC["code"].split("-")[0] + "-1234",
        _SYNTHETIC["id"].split("_")[0] + "_processed1",
        _SYNTHETIC["phrase"].split(" ")[0] + " unrelated",
    ],
    ids=[
        "a-longer-word",
        "the-host-as-a-prefix",
        "a-label-ending-in-the-host",
        "a-version-string",
        "four-digits",
        "a-snake-case-name",
        "the-phrase-head-alone",
    ],
)
def test_a_near_miss_of_each_entry_stays_green(tmp_path, text):
    """Each kind's boundary, pinned beside its control. The tracked tree is the near-miss
    corpus at scale — it names public cloud services and their hosts hundreds of times and
    must stay green — and these pin the shapes one kind could plausibly over-read."""
    root = _seed_repo(tmp_path / "r", {"docs/notes.md": f"An ordinary {text} line.\n"})
    found = [
        f
        for f in hygiene.violations(root, denylist=_DENYLIST)
        if f.startswith("internal-reference:")
    ]
    assert found == [], f"{text!r} is not a reference, but red: {found}"


def _encoded(case: str) -> tuple[str, str, str]:
    """``(token, encoding, kind)``: one encoded form of a synthetic entry, and how the rail must
    name it. Built at runtime, like every other planted value here."""
    word = _SYNTHETIC["word"].encode("utf-8")
    head = _SYNTHETIC["phrase"].split(" ")[0].encode("utf-8")

    def sha(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    return {
        "md5": (hashlib.md5(word).hexdigest(), "md5", "word"),
        "sha1": (hashlib.sha1(word).hexdigest(), "sha1", "word"),
        "sha256": (sha(word), "sha256", "word"),
        "sha512": (hashlib.sha512(word).hexdigest(), "sha512", "word"),
        "upper-case-hex": (sha(word).upper(), "sha256", "word"),
        "a-capitalised-spelling": (sha(word.capitalize()), "sha256", "word"),
        "an-echoed-newline": (sha(word + b"\n"), "sha256", "word"),
        "a-base64-digest": (
            base64.b64encode(hashlib.sha256(word).digest()).decode("ascii"),
            "base64 sha256",
            "word",
        ),
        "a-url-safe-base64-digest": (
            base64.urlsafe_b64encode(hashlib.sha256(word).digest()).decode("ascii"),
            "base64 sha256",
            "word",
        ),
        "a-salt-the-list-names": (
            hygiene.internal_reference_digest("word", _SYNTHETIC["word"], _SYNTHETIC_SALT),
            "salted sha256",
            "word",
        ),
        "base64": (base64.b64encode(word).decode("ascii"), "base64", "word"),
        "url-safe-base64": (base64.urlsafe_b64encode(word).decode("ascii"), "base64", "word"),
        "hex": (word.hex(), "hex", "word"),
        "a-head-on-its-own": (sha(head), "sha256", "phrase-head"),
    }[case]


_ENCODED_CASES = [
    "md5",
    "sha1",
    "sha256",
    "sha512",
    "upper-case-hex",
    "a-capitalised-spelling",
    "an-echoed-newline",
    "a-base64-digest",
    "a-url-safe-base64-digest",
    "a-salt-the-list-names",
    "base64",
    "url-safe-base64",
    "hex",
    "a-head-on-its-own",
]


@pytest.mark.parametrize("case", _ENCODED_CASES)
def test_an_encoded_entry_reds(tmp_path, case):
    """The shapes a well-meant denylist takes when it tries to keep the names out of the tree
    — and each one keeps them in: a digest (hex or base64, any common algorithm, a spelling's
    case or trailing newline aside), a digest salted with a salt the list names, or the name's
    own base64 or hex. Planted in a set literal, as a list of them would be."""
    token, encoding, kind = _encoded(case)
    root = _seed_repo(
        tmp_path / "r", {"docs/notes.md": f"An ordinary line.\nDENIED = {{'{token}'}}\n"}
    )
    found = [
        f
        for f in hygiene.violations(root, denylist=_DENYLIST)
        if f.startswith("internal-reference:")
    ]
    expected = f"internal-reference: docs/notes.md:2 carries the {encoding} of a denied {kind} "
    assert len(found) == 1 and found[0].startswith(expected), found


def test_an_encoded_entry_in_a_path_reds(tmp_path):
    """A file named after a digest publishes it as surely as a line does."""
    token, encoding, kind = _encoded("sha256")
    name = f"tests/fixtures/{token}.json"
    root = _seed_repo(tmp_path / "r", {name: "{}\n"})
    found = hygiene.violations(root, denylist=_DENYLIST)
    expected = f"internal-reference: {name} — its PATH carries the {encoding} of a denied {kind} "
    assert any(f.startswith(expected) for f in found), found


@pytest.mark.parametrize(
    "token",
    [
        lambda: hashlib.sha256(b"zzsomethingelse").hexdigest(),
        lambda: hashlib.sha256(_SYNTHETIC["word"].encode("utf-8")).hexdigest()[:-1],
        lambda: _SYNTHETIC["word"].encode("utf-8").hex() + "00",
        lambda: base64.b64encode(b"zzsomethingelse").decode("ascii"),
    ],
    ids=[
        "a-digest-of-an-unlisted-name",
        "one-character-short",
        "glued-to-more-hex",
        "other-base64",
    ],
)
def test_a_near_miss_of_an_encoded_entry_stays_green(tmp_path, token):
    """An encoded form is matched as a WHOLE token: a digest of a name the list does not hold,
    a truncated one, or one glued to more hex is not the entry, and a rail that read hashes by
    prefix would red every lockfile in the tree."""
    root = _seed_repo(tmp_path / "r", {"docs/notes.md": f"An ordinary line.\nX = '{token()}'\n"})
    found = [
        f
        for f in hygiene.violations(root, denylist=_DENYLIST)
        if f.startswith("internal-reference:")
    ]
    assert found == [], found


def test_without_the_list_the_rule_is_skipped_and_every_other_rule_runs(tmp_path, monkeypatch):
    """Where the variable is not set, the internal-reference rule — and only it — is skipped:
    the same tree reds on a real home path and stays silent on a planted name. Naming the list
    then reds on the name, so the skip, not the tree, is what made the difference."""
    root = _seed_repo(
        tmp_path / "r",
        {
            "docs/notes.md": "An ordinary line.\n" + _planted("word"),
            "tests/leak.py": f"HOME = '/Users/{_UNLISTED_OWNER}'\n",
        },
    )
    monkeypatch.delenv(hygiene.PRIVATE_DENYLIST_ENV, raising=False)
    assert hygiene.private_denylist() is None
    found = hygiene.violations(root, denylist=hygiene.private_denylist())
    assert any(f.startswith("real-home-path: tests/leak.py") for f in found), found
    assert not any(f.startswith("internal-reference:") for f in found), found
    monkeypatch.setenv(hygiene.PRIVATE_DENYLIST_ENV, str(_write_list(tmp_path)))
    found = hygiene.violations(root, denylist=hygiene.private_denylist())
    assert any(
        f.startswith("internal-reference: docs/notes.md:2 names a denied word") for f in found
    )
    assert any(f.startswith("real-home-path: tests/leak.py") for f in found), found


def test_the_environment_names_the_list_and_an_empty_value_names_none(tmp_path):
    listed = _write_list(tmp_path)
    assert hygiene.private_denylist({}) is None
    assert hygiene.private_denylist({hygiene.PRIVATE_DENYLIST_ENV: "  "}) is None
    assert hygiene.private_denylist({hygiene.PRIVATE_DENYLIST_ENV: str(listed)}) == _DENYLIST


def test_the_list_derives_the_head_each_compound_kind_is_checked_behind():
    """A line names only what a reader would find; the head each compound kind is looked for
    behind is derived from it, so the list cannot hold an entry the matcher never runs."""
    assert set(_DENYLIST) == {*hygiene.INTERNAL_REFERENCE_KINDS, "salt"}
    for kind, text in _SYNTHETIC.items():
        for entry_kind, candidate in hygiene.denylist_entries(kind, text):
            assert candidate in _DENYLIST[entry_kind], (kind, entry_kind)
    assert _DENYLIST["phrase-head"] == {_SYNTHETIC["phrase"].split(" ")[0]}
    assert _DENYLIST["code"] == {"zzv-99"} and _DENYLIST["id"] == {"zzv_m14"}
    assert _DENYLIST["salt"] == {_SYNTHETIC_SALT}, "a salt is taken as written"
    assert hygiene.denylist_size(_DENYLIST) == len(_SYNTHETIC)


@pytest.mark.parametrize(
    "line, reason",
    [
        ("word", "KIND, whitespace"),
        ("colour zzcerulean", "unknown kind"),
        ("word zzone zztwo", "one run of letters and digits"),
        ("phrase zzsolitary", "exactly two words"),
        ("host zzlocalhost", "a dotted host name"),
        ("code zzv-1234", "1-3 digits"),
        ("id zzv_short", "an id is"),
    ],
)
def test_a_malformed_line_is_refused_by_its_line_never_skipped(tmp_path, line, reason):
    """A named list that cannot be read as written fails the rail: a typo must never turn
    "checked" into "silently passed". The refusal names the line, never the entry — the entry
    is private, and a refusal is printed wherever the rail runs."""
    path = _write_list(tmp_path, "# a header line\n" + line + "\n")
    with pytest.raises(hygiene.PrivateDenylistError, match=re.escape(reason)) as refused:
        hygiene.load_private_denylist(path)
    assert f"{path}:2:" in str(refused.value)
    entry = line.partition(" ")[2]
    assert not entry or entry not in str(refused.value), "a refusal echoed the private entry"


def test_an_empty_or_unreadable_list_is_refused(tmp_path):
    """A list with no entry would deny nothing and report that it ran; a path that names no
    file is a typo. Both fail — through the checker too, which must not print the skip."""
    empty = _write_list(tmp_path, "# only comments\n\n")
    with pytest.raises(hygiene.PrivateDenylistError, match="holds no entry"):
        hygiene.load_private_denylist(empty)
    with pytest.raises(hygiene.PrivateDenylistError, match="holds no entry"):
        hygiene.parse_private_denylist(f"salt {_SYNTHETIC_SALT}\n")
    missing = tmp_path / "missing.txt"
    with pytest.raises(hygiene.PrivateDenylistError, match="cannot be read"):
        hygiene.load_private_denylist(missing)
    for named in (empty, missing):
        run = _checker("--scan", str(tmp_path), denylist=named)
        assert run.returncode == 1 and "the private denylist is unusable" in run.stdout, run.stdout
        assert hygiene.INTERNAL_REFERENCE_SKIPPED not in run.stdout


def test_the_policy_publishes_no_internal_vocabulary(baseline):
    """No entry, no digest and no salt for this rule in the public policy: the rule carries
    only its name and its reasons, and those say where the vocabulary lives instead."""
    rule = baseline["internal_reference_rule"]
    assert set(rule) == {"name", "rationale", "how_to_fix"}, sorted(rule)
    assert hygiene.PRIVATE_DENYLIST_ENV in rule["rationale"]
    assert hygiene.PRIVATE_DENYLIST_ENV in rule["how_to_fix"]
    assert not re.search(r"[0-9a-f]{32,}", json.dumps(rule)), "the rule text carries a digest"


def _checker(*args: str, denylist: Path | None) -> subprocess.CompletedProcess:
    """The checker as a maintainer runs it. *denylist* is required, so no test inherits the
    developer's own ``PERSONALCLAW_PRIVATE_DENYLIST`` by accident."""
    env = {k: v for k, v in os.environ.items() if k != hygiene.PRIVATE_DENYLIST_ENV}
    if denylist is not None:
        env[hygiene.PRIVATE_DENYLIST_ENV] = str(denylist)
    script = str(REPO_ROOT / "scripts" / "check_publication_hygiene.py")
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("kind", hygiene.DENYLIST_ENTRY_KINDS)
def test_the_digest_helper_refuses_an_internal_reference_kind(kind, capsys):
    """``--digest`` serves the planning-id rule only. An internal name digested into this
    repository is the leak itself, so the helper that used to make one says where the name
    goes instead."""
    with pytest.raises(SystemExit) as refused:
        hygiene.main(["--digest", kind, _SYNTHETIC[kind]])
    assert refused.value.code == 2
    err = capsys.readouterr().err
    assert "vocabulary is private" in err and hygiene.PRIVATE_DENYLIST_ENV in err, err


def test_scan_holds_text_outside_the_tree_to_the_same_rule(tmp_path):
    """``--scan`` is the rule for what is about to be published but is not tracked — an
    unpacked wheel, a PR body saved to a file. Red on a planted entry, green on clean text,
    and without the list a skip that says it checked nothing rather than a PASS."""
    dirty, clean = tmp_path / "dirty", tmp_path / "clean"
    dirty.mkdir()
    clean.mkdir()
    (dirty / "body.md").write_text(_planted("id"), encoding="utf-8")
    (clean / "body.md").write_text("Never trust an upload's declared type.\n", encoding="utf-8")
    listed = _write_list(tmp_path)
    red, green = _checker("--scan", str(dirty), denylist=listed), _checker(
        "--scan", str(clean), denylist=listed
    )
    assert red.returncode == 1 and "body.md:1 names a denied id" in red.stdout, red.stdout
    assert green.returncode == 0 and "PASS (1 files scanned, 0 found; 5 private" in green.stdout
    skipped = _checker("--scan", str(dirty), denylist=None)
    assert skipped.returncode == 0, skipped.stdout
    assert skipped.stdout.splitlines() == [
        hygiene.INTERNAL_REFERENCE_SKIPPED,
        "publication-hygiene: SKIPPED (1 files, nothing checked)",
    ]


# ── office documents: the metadata no text rule can read ─────────────────────────────────

#: A made-up value for a planted identity field: on no placeholder list, so every rule that
#: reads the field must refuse it — and nobody's name.
_PLANTED = "zz-planted-person"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORDML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"'
)
#: The property-name prefix a sensitivity label is written under, assembled so that this file
#: carries no label marker of its own.
_LABEL_PREFIX = "MSIP" + "_Label_"


def _zip(parts: dict[str, str | bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()


def _core(creator: str = "", modifier: str = "") -> str:
    return (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:creator>{creator}</dc:creator><cp:lastModifiedBy>{modifier}</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )


def _app(company: str = "", manager: str = "", template: str = "Normal.dotm") -> str:
    return (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'extended-properties"><Template>{template}</Template><Company>{company}</Company>'
        f"<Manager>{manager}</Manager></Properties>"
    )


def _body(inner: str) -> str:
    return f'<w:document xmlns:w="{_WORDML}"><w:body>{inner}</w:body></w:document>'


def _rels(relationship_type: str) -> str:
    return (
        f'<Relationships xmlns="{_RELATIONSHIPS}"><Relationship Id="r1" '
        f'Type="{relationship_type}" Target="docProps/custom.xml"/></Relationships>'
    )


def _ooxml(parts: dict[str, str | bytes] | None = None) -> bytes:
    """A minimal, clean Office Open XML package, with *parts* replacing or adding parts."""
    return _zip(
        {
            "[Content_Types].xml": _CONTENT_TYPES,
            "_rels/.rels": f'<Relationships xmlns="{_RELATIONSHIPS}"/>',
            "docProps/core.xml": _core(),
            "docProps/app.xml": _app(),
            "word/document.xml": _body(""),
            **(parts or {}),
        }
    )


def _custom(name: str) -> str:
    return (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
        'docPropsVTypes"><property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" '
        f'name="{name}"><vt:lpwstr>x</vt:lpwstr></property></Properties>'
    )


def _odt(meta: str = "", content: str = "") -> bytes:
    return _zip(
        {
            "mimetype": "application/vnd.oasis.opendocument.text",
            "meta.xml": (
                f"<office:document-meta {_ODF}><office:meta>{meta}</office:meta>"
                "</office:document-meta>"
            ),
            "content.xml": (
                f"<office:document-content {_ODF}><office:body>{content}</office:body>"
                "</office:document-content>"
            ),
        }
    )


def _saved(document) -> bytes:
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


_COMPOUND = bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 504
_UNREADABLE_COMPOUND = (
    "is a compound document, whose author and custom properties this rule cannot read"
)
_UNREADABLE_CLAIM = "is not readable as the office document its extension names"
_CUSTOM_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)
_LABEL_RELATIONSHIP = (
    "http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels"
)


@pytest.mark.parametrize(
    "path, content, problem",
    [
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/custom.xml": _custom("zzControl")}),
            "carries custom properties (docProps/custom.xml)",
            id="custom-properties-part",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"_rels/.rels": _rels(_CUSTOM_RELATIONSHIP)}),
            "attaches custom properties (_rels/.rels)",
            id="custom-properties-relationship",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/custom.xml": _custom(_LABEL_PREFIX + "0000_Enabled")}),
            "carries a sensitivity label (docProps/custom.xml)",
            id="label-in-custom-properties",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docMetadata/LabelInfo.xml": "<labelList/>"}),
            "carries a sensitivity label (docMetadata/LabelInfo.xml)",
            id="label-part",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"_rels/.rels": _rels(_LABEL_RELATIONSHIP)}),
            "attaches a sensitivity label (_rels/.rels)",
            id="label-relationship",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/core.xml": _core(creator=_PLANTED)}),
            "names someone in <creator> (docProps/core.xml)",
            id="creator",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/core.xml": _core(modifier=_PLANTED)}),
            "names someone in <lastModifiedBy> (docProps/core.xml)",
            id="last-modified-by",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/app.xml": _app(company=_PLANTED)}),
            "names someone in <Company> (docProps/app.xml)",
            id="company",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"docProps/app.xml": _app(manager=_PLANTED)}),
            "names someone in <Manager> (docProps/app.xml)",
            id="manager",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml(
                {"word/document.xml": _body(f'<w:ins w:id="0" w:author="{_PLANTED}"/>')}
            ),
            "names someone in <ins author> (word/document.xml)",
            id="revision-author",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml(
                {
                    "word/comments.xml": (
                        f'<w:comments xmlns:w="{_WORDML}"><w:comment w:id="0" w:author="Author" '
                        f'w:initials="{_PLANTED}"/></w:comments>'
                    )
                }
            ),
            "names someone in <comment initials> (word/comments.xml)",
            id="comment-initials",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml(
                {
                    "word/people.xml": (
                        '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/'
                        'wordml"><w15:person w15:author="Author"><w15:presenceInfo '
                        f'w15:providerId="None" w15:userId="{_PLANTED}"/></w15:person>'
                        "</w15:people>"
                    )
                }
            ),
            "names someone in <presenceInfo userId> (word/people.xml)",
            id="signed-in-author",
        ),
        pytest.param(
            "docs/b.xlsx",
            lambda: _ooxml(
                {
                    "xl/comments1.xml": (
                        f"<comments><authors><author>{_PLANTED}</author></authors></comments>"
                    )
                }
            ),
            "names someone in <author> (xl/comments1.xml)",
            id="workbook-comment-author",
        ),
        pytest.param(
            "docs/b.xlsx",
            lambda: _ooxml(
                {
                    "xl/persons/person.xml": (
                        f'<personList><person displayName="{_PLANTED}" id="{{0}}" '
                        'userId="Author" providerId="None"/></personList>'
                    )
                }
            ),
            "names someone in <person displayName> (xl/persons/person.xml)",
            id="workbook-person",
        ),
        pytest.param(
            "docs/c.pptx",
            lambda: _ooxml(
                {
                    "ppt/commentAuthors.xml": (
                        f'<cmAuthorLst><cmAuthor id="0" name="{_PLANTED}" initials=""/>'
                        "</cmAuthorLst>"
                    )
                }
            ),
            "names someone in <cmAuthor name> (ppt/commentAuthors.xml)",
            id="presentation-comment-author",
        ),
        pytest.param(
            "docs/d.odt",
            lambda: _odt(meta=f"<meta:initial-creator>{_PLANTED}</meta:initial-creator>"),
            "names someone in <initial-creator> (meta.xml)",
            id="opendocument-initial-creator",
        ),
        pytest.param(
            "docs/d.odt",
            lambda: _odt(
                content=(
                    f"<office:annotation><dc:creator>{_PLANTED}</dc:creator>" "</office:annotation>"
                )
            ),
            "names someone in <creator> (content.xml)",
            id="opendocument-annotation",
        ),
        pytest.param(
            "docs/d.odt",
            lambda: _odt(meta='<meta:user-defined meta:name="zzControl">x</meta:user-defined>'),
            "carries custom properties (meta.xml)",
            id="opendocument-custom-property",
        ),
        pytest.param(
            "docs/e.fodt",
            lambda: (
                f'<?xml version="1.0"?><office:document {_ODF}><office:meta>'
                f"<dc:creator>{_PLANTED}</dc:creator></office:meta></office:document>"
            ),
            "names someone in <creator>",
            id="flat-opendocument",
        ),
        pytest.param(
            "docs/f.rtf",
            lambda: "{\\rtf1{\\info{\\author " + _PLANTED + "}}}",
            "names someone in \\author",
            id="rtf-author",
        ),
        pytest.param(
            "docs/f.rtf",
            lambda: "{\\rtf1{\\*\\userprops {\\propname x}}}",
            "carries custom properties (\\userprops)",
            id="rtf-custom-properties",
        ),
        pytest.param(
            "docs/f.rtf",
            lambda: "{\\rtf1{\\*\\revtbl {Unknown;}{" + _PLANTED + ";}}}",
            "names someone in its revision table",
            id="rtf-revision-author",
        ),
        pytest.param(
            "docs/g.doc", lambda: _COMPOUND, _UNREADABLE_COMPOUND, id="binary-office-file"
        ),
        pytest.param(
            "docs/g.bin", lambda: _COMPOUND, _UNREADABLE_COMPOUND, id="compound-by-content"
        ),
        pytest.param(
            "docs/h.docx", lambda: b"PK\x03\x04 cut short", _UNREADABLE_CLAIM, id="truncated"
        ),
        pytest.param(
            "docs/h.pages",
            lambda: _zip({"Index/Document.iwa": b"\x00\x01"}),
            _UNREADABLE_CLAIM,
            id="unreadable-package",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"word/document.xml": "<w:document"}),
            "has a part that is not well-formed XML (word/document.xml)",
            id="malformed-part",
        ),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"word/embeddings/oleObject1.bin": _COMPOUND}),
            "embeds a compound document this rule cannot read (word/embeddings/oleObject1.bin)",
            id="embedded-object",
        ),
        pytest.param(
            "docs/c.pptx",
            lambda: _ooxml(
                {
                    "ppt/embeddings/sheet.xlsx": _ooxml(
                        {"docProps/core.xml": _core(creator=_PLANTED)}
                    )
                }
            ),
            "names someone in <creator> (docProps/core.xml) inside ppt/embeddings/sheet.xlsx",
            id="embedded-document",
        ),
        pytest.param(
            "docs/renamed.bin",
            lambda: _ooxml({"docProps/core.xml": _core(creator=_PLANTED)}),
            "names someone in <creator> (docProps/core.xml)",
            id="renamed-document",
        ),
    ],
)
def test_each_office_document_defect_reds(tmp_path, path, content, problem):
    """One positive control per field and per form, through a seeded repo and the SHIPPED
    policy. Parametrised so a rule someone later weakens reds HERE, naming the field that
    stopped being read, instead of quietly publishing it."""
    root = _seed_repo(tmp_path / "r", {path: content()})
    found = [
        f for f in hygiene.violations(root, denylist=_DENYLIST) if f.startswith("office-document:")
    ]
    assert f"office-document: {path} {problem}" in found, found


@pytest.mark.parametrize(
    "path, content",
    [
        pytest.param("docs/a.docx", _ooxml, id="a-clean-package"),
        pytest.param(
            "docs/a.docx",
            lambda: _ooxml({"word/document.xml": _body('<w:ins w:id="0" w:author="Author"/>')}),
            id="words-scrubbed-revision-author",
        ),
        pytest.param("docs/b.docx", lambda: _saved(docx.Document()), id="python-docx"),
        pytest.param("docs/c.xlsx", lambda: _saved(openpyxl.Workbook()), id="openpyxl"),
        pytest.param(
            "docs/archive.zip",
            lambda: _zip({"docProps/custom.xml": _custom("zzControl")}),
            id="a-zip-that-is-no-document",
        ),
        pytest.param(
            "docs/f.rtf",
            lambda: "{\\rtf1{\\info{\\author }{\\company }}{\\*\\revtbl {Unknown;}}}",
            id="rtf-with-empty-fields",
        ),
        pytest.param(
            "docs/notes.md",
            lambda: "An RTF file opens with {\\rtf1 and names its {\\author X} inside.\n",
            id="text-that-quotes-rtf",
        ),
    ],
)
def test_an_office_document_that_names_no_one_stays_green(tmp_path, path, content):
    """The near misses: a package whose every field is empty or a declared placeholder, what
    this project's own document libraries write, a zip that merely holds a part of that name,
    and prose that quotes a document format. None of them is a defect."""
    root = _seed_repo(tmp_path / "r", {path: content()})
    found = hygiene.violations(root, denylist=_DENYLIST)
    assert found == [], f"{path} names no one, but red: {found}"


def test_a_presentation_python_pptx_made_reds_until_its_modifier_is_cleared(tmp_path):
    """python-pptx's template records a real person as the last modifier, which is why it is not
    on the placeholder list — proved against the library itself, without writing the name."""
    root = _seed_repo(tmp_path / "r", {"docs/deck.pptx": _saved(pptx.Presentation())})
    found = [
        f for f in hygiene.violations(root, denylist=_DENYLIST) if f.startswith("office-document:")
    ]
    assert found == [
        "office-document: docs/deck.pptx names someone in <lastModifiedBy> (docProps/core.xml)"
    ]


def test_the_content_rules_read_an_office_documents_parts(tmp_path):
    """A home path in a template field and a denied word in the body both sit in COMPRESSED
    parts no text reader sees; each reds under its own rule, located at its part."""
    body = _body(f"<w:p><w:r><w:t>{_SYNTHETIC['word']}</w:t></w:r></w:p>")
    blob = _ooxml(
        {
            "docProps/app.xml": _app(template=f"/Users/{_UNLISTED_OWNER}/t.dotx"),
            "word/document.xml": body,
        }
    )
    root = _seed_repo(tmp_path / "r", {"docs/a.docx": blob})
    found = hygiene.violations(root, denylist=_DENYLIST)
    home = f"real-home-path: docs/a.docx!docProps/app.xml names home directory '{_UNLISTED_OWNER}'"
    assert home in found, found
    expected = "internal-reference: docs/a.docx!word/document.xml:1 names a denied word"
    assert any(f.startswith(expected) for f in found), found


def test_the_office_rule_reads_the_document_the_tree_carries():
    """Vacuity. The tree test's zero means something only if the rule OPENED the office document
    this repository publishes, and the content rules read its parts."""
    fixture = "tests/fixtures/word_authored.docx"
    assert hygiene.office_kind((REPO_ROOT / fixture).read_bytes()) == "package"
    texts = dict(hygiene._text_blobs([fixture], REPO_ROOT))
    assert f"{fixture}!word/document.xml" in texts, sorted(texts)
    assert "Word fixture" in texts[f"{fixture}!word/document.xml"]


def test_a_document_the_rule_cannot_fully_read_is_refused_not_trusted(monkeypatch):
    """The caps fail CLOSED: a part larger than the rule reads, more parts than it reads, and
    documents nested deeper than it follows are each a refusal, never a silent pass."""
    rule = hygiene.load_baseline()["office_document_rule"]
    nested = _ooxml({"word/embeddings/sheet.xlsx": _ooxml()})
    monkeypatch.setattr(hygiene, "_OFFICE_PART_CAP", 64)
    assert "has a part this rule cannot read (docProps/app.xml)" in (
        hygiene.office_document_problems(_ooxml(), rule)
    )
    monkeypatch.undo()
    monkeypatch.setattr(hygiene, "_OFFICE_MAX_PARTS", 3)
    assert hygiene.office_document_problems(_ooxml(), rule) == [
        "has more parts than this rule reads (5 > 3)"
    ]
    monkeypatch.undo()
    monkeypatch.setattr(hygiene, "_OFFICE_MAX_DEPTH", 0)
    assert hygiene.office_document_problems(nested, rule) == [
        "nests documents deeper than this rule reads (word/embeddings/sheet.xlsx)"
    ]


def test_an_office_finding_prints_the_office_advice(capsys, baseline):
    """The fix for a document is to clear a field, not to move the file, so the report gives the
    office rule's own advice rather than the path rules'."""
    line = "office-document: docs/a.docx names someone in <creator> (docProps/core.xml)"
    assert hygiene._report([line], "clean", baseline) == 1
    out = capsys.readouterr().out
    assert baseline["office_document_rule"]["how_to_fix"] in out
    assert baseline["how_to_fix_a_red"] not in out


def test_the_planted_value_is_on_no_placeholder_list(baseline):
    """The controls above are live only while their value is refused. Adding it to the list
    would turn every identity control green without a single rule having fired."""
    placeholders = baseline["office_document_rule"]["placeholder_values"]
    assert _PLANTED not in placeholders
    assert len(placeholders) == len(set(placeholders)), "a placeholder is listed twice"


# ── the policy file must stay honest ────────────────────────────────────────────────────


def test_every_rule_carries_a_rationale(baseline):
    """A denylist entry without a stated reason becomes undeletable: nobody can tell whether
    removing it is safe, so it survives forever and the file stops being readable policy."""
    rules = [
        *baseline["path_rules"],
        baseline["binary_size_rule"],
        baseline["real_home_path_rule"],
        baseline["office_document_rule"],
        baseline["internal_reference_rule"],
    ]
    assert rules, "no rules loaded"
    for rule in rules:
        assert rule.get("rationale", "").strip(), f"{rule['name']} has no rationale"
        assert len(rule["rationale"]) > 80, f"{rule['name']}'s rationale is a placeholder"
    assert len(baseline["office_document_rule"]["how_to_fix"]) > 80
    assert len(baseline["internal_reference_rule"]["how_to_fix"]) > 80


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
