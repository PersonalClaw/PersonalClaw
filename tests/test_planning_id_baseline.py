"""Shrink-only ratchet for the committed planning-id census (``planning-id-baseline.json``).

The census (``scripts/generate_planning_id_baseline.py``) counts, per file, the roadmap and
planning ids in the tree's comments, docstrings, test titles and doc prose. The roadmap is private
planning state kept outside this repository, so such an id tells a reader nothing and publishes
the private plan's own names. The tree still carries a known population, which later cleanups
remove; this suite keeps anything from ADDING to it:

  * a file whose count ROSE reds, naming the file — a new id was written;
  * a committed count ABOVE the file's current one reds too — an id was removed and the census
    was not regenerated, so the floor is looser than the tree;
  * the committed file is byte-identical to a fresh render, and the total never rises.

⚠️  FORBIDDEN-TO-RAISE RULE — "fix the text, not the baseline": when a count rises, say what the
    code does or why without the id. Never regenerate the census to bless a rise.

# how to update
    Regenerate ONLY when counts legitimately fell (an id removed, a file with ids deleted) or a
    file moved and took its ids with it, in that same commit::

        python scripts/generate_planning_id_baseline.py

The private half of the vocabulary is known to the census only as digests, so this file never
writes a real id in plain text either: the controls below are made-up entries the shipped policy
lists, assembled at runtime, and the one real id the suite plants is read out of the tree.
"""

from __future__ import annotations

import json
import re

import pytest

from scripts import check_publication_hygiene as hygiene
from scripts import generate_planning_id_baseline as gen

_FORBIDDEN_TO_RAISE = "fix the text, not the baseline"

#: The made-up entries the shipped policy lists for each private kind — built at runtime so the
#: suite reads as clean prose to the census it tests.
_WORK_ITEM_PREFIX = "ZQ" + "X"
_WORK_ITEM_CEILING = 9
_PLAN_NAME = "ZZ-CONTROL-" + "PLAN"
_PLAN_WORD_IN_CONTEXT = "ZZCONTROL" + "WORD"
_PLAN_WORD_ANY = "ZQW" + "9"


@pytest.fixture(scope="module")
def committed() -> dict:
    path = gen.baseline_path()
    assert path.is_file(), (
        "planning-id-baseline.json is missing — generate it with "
        "`python scripts/generate_planning_id_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fresh() -> dict:
    """One census of the tree for the whole module: it reads every tracked text file."""
    return gen.build_inventory()


def _count(path: str, text: str) -> int:
    return gen.count_ids(path, text)


# ── the ratchet ──────────────────────────────────────────────────────────────────────────────


def test_no_file_gained_a_planning_id(committed, fresh):
    rose = gen.regressions(committed["per_file"], fresh["per_file"])
    assert not rose, (
        "a comment, docstring, test title or doc gained a roadmap or planning id:\n  "
        + "\n  ".join(rose)
        + "\n\nSay what the code does or why without the id — it points into planning state a "
        "reader of this repository cannot open. FORBIDDEN: regenerating "
        "planning-id-baseline.json to bless the rise."
    )


def test_the_baseline_is_not_stale_on_the_shrink_side(committed, fresh):
    stale = gen.stale_high(committed["per_file"], fresh["per_file"])
    assert not stale, (
        "planning ids were removed but the census was not regenerated, so its floor is looser "
        "than the tree:\n  "
        + "\n  ".join(stale)
        + "\n\nRun `python scripts/generate_planning_id_baseline.py` in this commit."
    )


def test_the_total_never_rises(committed, fresh):
    """A moved file reds at its new path and asks for a regeneration; this is the property that
    regeneration must keep."""
    assert fresh["totals"]["ids"] <= committed["totals"]["ids"], (
        f"the tree carries {fresh['totals']['ids']} planning ids, more than the committed "
        f"{committed['totals']['ids']}"
    )


def test_the_committed_baseline_byte_matches_a_fresh_render(fresh):
    assert gen.baseline_path().read_text(encoding="utf-8") == gen.render(fresh), (
        "planning-id-baseline.json does not match a fresh render. If ids were removed, "
        "regenerate it with `python scripts/generate_planning_id_baseline.py` in the same commit; "
        "if a count rose, fix the text instead."
    )


def test_the_render_is_deterministic():
    files = [(path, text) for path, text in gen.tracked_texts() if path.startswith("docs/")]
    assert files, "no tracked docs to render"
    assert gen.render(gen.census(files)) == gen.render(gen.census(list(reversed(files))))


def test_the_baseline_is_well_shaped_and_names_no_id(committed):
    """Paths and counts only: listing what was found would publish the names the digests keep
    out of the tree."""
    assert set(committed) == {"generated_from", "per_file", "totals"}
    assert committed["generated_from"] == gen.GENERATED_FROM
    per_file = committed["per_file"]
    assert list(per_file) == sorted(per_file), "per_file is not sorted"
    for path, count in per_file.items():
        assert isinstance(path, str) and path and not path.startswith("/"), path
        assert isinstance(count, int) and count > 0, (path, count)
    assert committed["totals"] == {"files": len(per_file), "ids": sum(per_file.values())}


def test_the_census_reads_the_tree():
    """The vacuity floor: a census that reads nothing finds nothing, and zero would then pass."""
    read = [gen.language(path) for path, _ in gen.tracked_texts()]
    assert len(read) > 1000, f"the census reads only {len(read)} files"
    missing = {"py", "js", "md", "hash"} - set(read)
    assert not missing, f"the census read no file of: {sorted(missing)}"


# ── it catches a new id, and not a known one ─────────────────────────────────────────────────


def _a_real_id() -> tuple[str, str]:
    """``(file, id)``: a real work-item id or plan name from a span of a tracked file, read at
    runtime — the suite writes no real id down."""
    vocab = gen.shipped_vocabulary()
    for path, text in gen.tracked_texts():
        for kind, span in gen.spans(path, text):
            for match in gen._WORK_ITEM.finditer(span):
                ceiling = vocab.prefix_ceiling(match.group("prefix"))
                if ceiling is not None and int(match.group("num")) <= ceiling:
                    return path, match.group(0)
    raise AssertionError("the tree carries no work-item id to plant; use the control instead")


def test_a_new_id_reds_and_the_known_ones_do_not(committed):
    """The counts are taken into locals first, so a failure prints numbers and paths — never the
    real id, which a public CI log must not carry either."""
    path, real_id = _a_real_id()
    known = committed["per_file"].get(path, 0)
    counted = _count(path, (gen.REPO / path).read_text(encoding="utf-8"))
    assert counted == known, f"{path}'s known ids are not all in the baseline"
    assert gen.regressions(committed["per_file"], {path: known}) == []
    planted = "\n".join(["def later():", f"    # decided in {real_id}", "    return 1", ""])
    counted = _count("src/personalclaw/later.py", planted)
    assert counted == 1, "a real id written into a new comment must be counted"
    grown = {**committed["per_file"], "src/personalclaw/later.py": 1}
    assert gen.regressions(committed["per_file"], grown) == [
        "src/personalclaw/later.py: planning ids rose 0 -> 1"
    ]


def test_a_removed_id_asks_for_a_regeneration_and_is_not_a_rise():
    assert gen.stale_high({"a.py": 2}, {"a.py": 1}) == ["a.py: committed 2 > current 1"]
    assert gen.regressions({"a.py": 2}, {"a.py": 1}) == []
    assert gen.stale_high({"a.py": 1}, {}) == ["a.py: committed 1 > current 0"]


_ID = f"{_WORK_ITEM_PREFIX}-7"


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("src/personalclaw/m.py", f"x = 1  # see {_ID}\n", 1),
        ("src/personalclaw/m.py", f'"""{_ID} decides this."""\n', 1),
        ("src/personalclaw/m.py", f'class C:\n    """Per {_ID}."""\n', 1),
        ("src/personalclaw/m.py", f'def f():\n    """Per {_ID}."""\n', 1),
        ("tests/test_m.py", f'def test_x():\n    """Per {_ID}."""\n', 1),
        ("web/src/m.ts", f"const a = 1 // per {_ID}\n", 1),
        ("web/src/m.ts", f"/* per {_ID} */\nconst a = 1\n", 1),
        ("web/src/m.test.ts", f"it('does what {_ID} said', () => {{}})\n", 1),
        ("web/src/m.css", f"/* per {_ID} */\na {{ color: red }}\n", 1),
        (".github/workflows/x.yml", f"on: push  # per {_ID}\n", 1),
        ("docs/guides/x.md", f"The fix follows {_ID}.\n", 1),
        ("docs/x.html", f"<p>x</p><!-- per {_ID} -->\n", 1),
        ("src/personalclaw/m.py", f'X = "{_ID}"\n', 0),
        ("src/personalclaw/m.py", f'X = f"{{a}} {_ID}"\n', 0),
        ("web/src/m.ts", f"const s = '{_ID}' + `{_ID}`\n", 0),
        ("web/src/m.ts", f"const r = /{_ID}\\/x/g // a regex\n", 0),
        ("docs/guides/x.md", f"```\n{_ID}\n```\n", 0),
        ("tests/fixtures/x.md", f"{_ID}\n", 0),
        ("docs/reference/x.md", f"{_ID}\n", 0),
        ("src/personalclaw/skills/x.md", f"{_ID}\n", 0),
    ],
)
def test_each_kind_of_text_is_read_and_code_is_not(path, text, expected):
    assert _count(path, text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"per {_WORK_ITEM_PREFIX}-{_WORK_ITEM_CEILING}", 1),
        (f"per {_WORK_ITEM_PREFIX}-{_WORK_ITEM_CEILING + 1}, past the ceiling", 0),
        (f"per {_WORK_ITEM_PREFIX}-2d-ii and {_WORK_ITEM_PREFIX}-3/2", 2),
        (f"the {_WORK_ITEM_PREFIX}-2 algorithm", 0),
        (f"as {_PLAN_NAME} planned", 1),
        (f"as {_PLAN_NAME.title()} planned", 1),
        (f"see docs/{_PLAN_NAME.lower()}.md", 0),
        (f"{_PLAN_NAME}-EXTRA is a longer name", 1),
        (f"{_PLAN_WORD_IN_CONTEXT} S4 ends", 2),
        (f"the {_PLAN_WORD_IN_CONTEXT} step", 0),
        (f"the {_PLAN_WORD_ANY} engine", 1),
    ],
)
def test_each_private_kind_fires_on_its_control(text, expected):
    assert _count("src/personalclaw/m.py", f"# {text}\n") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("landed in S12", 1),
        ("stored in S3", 0),
        ("an S12 bucket", 0),
        (f"{_PLAN_NAME} S3 ended", 2),
        ("T2.3 and T1.4-T1.6", 2),
        ("see the plan's §4.4", 1),
        ("plan 99 said so", 1),
        ("rev 9, F-12 and G3", 3),
        ("seam 4, Slice 2, C1.1, ledger 123, SC#3", 5),
        ("this atom forbids it", 1),
        ('the "atom" feed and the atom (a single control)', 0),
        ("the done_when clause of a declared-done class-B change", 3),
    ],
)
def test_the_generic_shapes_and_their_exceptions(text, expected):
    assert _count("src/personalclaw/m.py", f"# {text}\n") == expected


def test_a_documents_own_section_sign_is_not_a_plan_reference():
    assert _count("docs/security/x.md", "As §4 of this document says.\n") == 0
    assert _count("src/personalclaw/m.py", "# As §4 says.\n") == 1


# ── the private half is digests, and the controls are real entries ────────────────────────────


@pytest.fixture(scope="module")
def rule() -> dict:
    return hygiene.load_baseline()["planning_id_rule"]


def test_the_vocabulary_is_digests_and_no_kind_is_only_its_control(rule):
    denied = rule["denied"]
    assert set(denied) == set(hygiene.PLANNING_ID_KINDS)
    digest = re.compile(r"[0-9a-f]{64}")
    assert all(digest.fullmatch(d) and n > 0 for d, n in denied["work-item-prefix"].items())
    assert all(digest.fullmatch(d) for d in denied["plan-name"])
    assert all(
        digest.fullmatch(d) and mode in ("any", "in-context")
        for d, mode in denied["plan-word"].items()
    )
    for kind in hygiene.PLANNING_ID_KINDS:
        assert len(denied[kind]) >= 3, f"{kind} lists nothing but its control(s)"


def test_every_control_is_a_real_entry_of_the_shipped_policy(rule):
    salt = hygiene.load_baseline()["internal_reference_rule"]["digest_salt"]

    def digest(kind: str, text: str) -> str:
        return hygiene.internal_reference_digest(kind, text, salt)

    denied = rule["denied"]
    assert denied["work-item-prefix"][digest("work-item-prefix", _WORK_ITEM_PREFIX)] == (
        _WORK_ITEM_CEILING
    )
    assert digest("plan-name", _PLAN_NAME.lower()) in denied["plan-name"]
    assert denied["plan-word"][digest("plan-word", _PLAN_WORD_IN_CONTEXT.lower())] == "in-context"
    assert denied["plan-word"][digest("plan-word", _PLAN_WORD_ANY.lower())] == "any"


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("work-item-prefix", _WORK_ITEM_PREFIX),
        ("plan-name", _PLAN_NAME),
        ("plan-word", _PLAN_WORD_ANY),
    ],
)
def test_the_digest_command_prints_the_policy_line(kind, text, rule, capsys):
    assert hygiene.main(["--digest", kind, text]) == 0
    kind_out, digest = capsys.readouterr().out.split()
    assert kind_out == kind and digest in rule["denied"][kind]


def test_the_digest_command_refuses_a_malformed_planning_candidate():
    with pytest.raises(SystemExit):
        hygiene.main(["--digest", "work-item-prefix", "not a prefix"])


def test_the_forbidden_to_raise_line_is_present():
    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
