"""The licence rail's checks fail on a tree that breaks them, and pass on one that keeps them.

The rail itself, on this repository, is ``tests/rails/test_licence_governance_tree.py``: it runs
once per CI run, in the rails job, and its module docstring says what the governance promise is
and what the rail cannot answer. The checks live in ``tests/licence_governance_shared.py``. Each
test here plants a tree under ``tmp_path`` and runs the REAL check over it, rather than
re-implementing it: a licence identifier that is not MIT, a narrowed grant, a holder that is
not this project or is the app itself, a file git ignores, and the fallback walk where git
cannot answer. A planted tree takes its ``LICENSE`` text from this repository's, so the grant
it carries is the grant the rail pins.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from licence_governance_shared import (
    _APPROVED_HOLDERS,
    _GIT_LOCATION_VARS,
    _ROOT,
    _SPDX_TAG,
    _git_files,
    _minimal_tree,
    _pyproject,
    check_every_declaration_is_mit,
    check_every_licence_file_carries_the_real_grant,
    check_every_licence_holder_is_a_real_holder,
    check_no_cla_file,
    check_no_licence_holder_is_an_artefact_name,
    declaration_sites,
)

# ── the negative case, for all three disjuncts ──────────────────────────────────────────


def _bundle(root: Path, name: str, display: str, holder: str) -> Path:
    """An app bundle under *root*: an ``app.json`` and a LICENSE naming *holder*.

    Shaped exactly like the scaffolder's output, so the artefact-holder rail is exercised
    against the real thing rather than a fixture that happens to be easier to catch.
    """
    bundle = root / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "app.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "displayName": display}, indent=2) + "\n",
        encoding="utf-8",
    )
    text = (_ROOT / "LICENSE").read_text(encoding="utf-8")
    (bundle / "LICENSE").write_text(
        re.sub(r"(?im)^copyright.*$", f"Copyright (c) 2026 {holder}", text, count=1),
        encoding="utf-8",
    )
    return bundle


def test_the_teeth_test_s_own_baseline_is_green(tmp_path: Path) -> None:
    """The control arm. A baseline that already failed would make every red below vacuous."""
    root = _minimal_tree(tmp_path)
    _bundle(root, "channel-null", "Null Channel", sorted(_APPROVED_HOLDERS)[0])
    check_every_declaration_is_mit(root)
    check_no_cla_file(root)
    check_every_licence_file_carries_the_real_grant(root)
    check_every_licence_holder_is_a_real_holder(root)
    check_no_licence_holder_is_an_artefact_name(root)


def test_the_rail_reds_when_a_licence_names_the_app_as_its_holder(tmp_path: Path) -> None:
    """The scaffolder's own defect, reproduced from its two derivations of an app identity.

    Both spellings, because the broken fallback was ``author or display`` and ``display``
    itself had two sources: an explicit ``--display-name``, and the title-cased expansion of
    the app NAME when that flag was omitted. A rail that caught only the manifest's
    ``displayName`` would have waved through `Copyright (c) 2026 Channel Null` — the string
    four published repositories actually shipped, which appears in no file as a field value.
    """
    root = _minimal_tree(tmp_path)

    # (a) the manifest's declared displayName
    _bundle(root, "channel-null", "Null Channel", "Null Channel")
    with pytest.raises(AssertionError, match="names the app itself"):
        check_no_licence_holder_is_an_artefact_name(root)

    # (b) the name expanded the way the scaffolder expands it with no --display-name. This is
    # the exemplars' real shape: `channel-null` -> `Channel Null`.
    _bundle(root, "channel-null", "Null Channel", "Channel Null")
    with pytest.raises(AssertionError, match="Channel Null"):
        check_no_licence_holder_is_an_artefact_name(root)

    # (c) the bare kebab name
    _bundle(root, "channel-null", "Null Channel", "channel-null")
    with pytest.raises(AssertionError, match="names the app itself"):
        check_no_licence_holder_is_an_artefact_name(root)

    # A real holder in the same bundle clears all three — so the red above is the HOLDER and
    # not the bundle's existence.
    _bundle(root, "channel-null", "Null Channel", "Ada Lovelace")
    check_no_licence_holder_is_an_artefact_name(root)


def test_the_rail_reds_on_an_unapproved_or_missing_holder(tmp_path: Path) -> None:
    """The holder half, which the grant hash excludes by design and so cannot catch."""
    root = _minimal_tree(tmp_path)

    # A person, not this project — correct for a third-party bundle, wrong for this tree.
    _bundle(root, "some-app", "Some App", "Ada Lovelace")
    with pytest.raises(AssertionError, match="Ada Lovelace"):
        check_every_licence_holder_is_a_real_holder(root)
    # …and the grant is untouched, proving the two rails are independent rather than one
    # assertion reported twice.
    check_every_licence_file_carries_the_real_grant(root)

    # No copyright line at all: an MIT grant that names nobody.
    text = (_ROOT / "LICENSE").read_text(encoding="utf-8")
    (root / "some-app" / "LICENSE").write_text(
        "\n".join(line for line in text.splitlines() if not line.lower().startswith("copyright"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="no copyright line at all"):
        check_every_licence_holder_is_a_real_holder(root)


def test_the_grant_rail_reaches_beyond_the_root_licence(tmp_path: Path) -> None:
    """The reason this replaced a root-only pin: five sibling licence files were unpinned.

    A bundled app's LICENSE is the copy that travels when the bundle leaves the wheel, so a
    narrowed grant there reaches users while the root file stays pristine.
    """
    root = _minimal_tree(tmp_path)
    bundle = _bundle(root, "some-app", "Some App", sorted(_APPROVED_HOLDERS)[0])
    check_every_licence_file_carries_the_real_grant(root)

    (bundle / "LICENSE").write_text(
        f"MIT License\n\nCopyright (c) 2026 {sorted(_APPROVED_HOLDERS)[0]}\n\n"
        "Permission is NOT granted.\n",
        encoding="utf-8",
    )
    # The identifier still says MIT and the holder is still right — only the GRANT moved.
    check_every_declaration_is_mit(root)
    check_every_licence_holder_is_a_real_holder(root)
    with pytest.raises(AssertionError, match="some-app/LICENSE"):
        check_every_licence_file_carries_the_real_grant(root)


def test_the_rail_reds_on_a_fake_spdx_change(tmp_path: Path) -> None:
    """Clause 3, disjunct 1 — a deliberately introduced non-MIT SPDX identifier.

    Three separate mutations, because a rail that catches one spelling and not the others misses
    how a licence actually changes: in a manifest's field, in a source file's header, or in the
    licence FILE itself.
    """
    root = _minimal_tree(tmp_path)

    # (a) an SPDX header in an ordinary source file. Composed from `_SPDX_TAG` rather than
    # written out, so this file's own bytes stay free of a real header — see that constant.
    (root / "app.py").write_text(f"# {_SPDX_TAG} BUSL-1.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="BUSL-1.1"):
        check_every_declaration_is_mit(root)
    (root / "app.py").unlink()

    # (b) the manifest's own field, on a line that is NOT the first — see `_pyproject`.
    (root / "pyproject.toml").write_text(_pyproject("Apache-2.0"), encoding="utf-8")
    with pytest.raises(AssertionError, match="Apache-2.0"):
        check_every_declaration_is_mit(root)
    (root / "pyproject.toml").write_text(_pyproject("MIT"), encoding="utf-8")

    # (c) the licence FILE rewritten — no identifier field anywhere touched
    (root / "LICENSE").write_text(
        "Business Source License 1.1\n\nUse is limited.\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="Business Source"):
        check_every_declaration_is_mit(root)
    with pytest.raises(AssertionError, match="do not carry the pinned MIT grant"):
        check_every_licence_file_carries_the_real_grant(root)

    # (d) and the subtler one: MIT's title kept, the grant quietly narrowed
    (root / "LICENSE").write_text(
        f"MIT License\n\nCopyright (c) 2026 {sorted(_APPROVED_HOLDERS)[0]}\n\n"
        "Permission is NOT granted.\n",
        encoding="utf-8",
    )
    check_every_declaration_is_mit(root)  # the identifier still says MIT — and that is the point
    check_every_licence_holder_is_a_real_holder(root)  # …and the holder is still right
    with pytest.raises(AssertionError, match="do not carry the pinned MIT grant"):
        check_every_licence_file_carries_the_real_grant(root)


def _git(root: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _checkout_with_scratch(tmp_path: Path) -> tuple[Path, Path]:
    """A git checkout like this one: an MIT tree, `.local/` ignored, and in it a third-party
    source that declares Apache-2.0 (composed from `_SPDX_TAG`, see that constant)."""
    root = _minimal_tree(tmp_path)
    (root / ".gitignore").write_text("/.local/\n/notes-only-mine/\n", encoding="utf-8")
    scratch = root / ".local" / "scratch" / "vendored" / "TargetManager.ts"
    scratch.parent.mkdir(parents=True)
    scratch.write_text(f"/**\n * {_SPDX_TAG} Apache-2.0\n */\nexport {{}};\n", encoding="utf-8")
    # An ignored folder no prune list names: only git's own list can leave it out.
    elsewhere = root / "notes-only-mine" / "LICENSE"
    elsewhere.parent.mkdir()
    elsewhere.write_text("Apache License\nVersion 2.0, January 2004\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return root, scratch


def test_a_file_git_ignores_is_not_judged(tmp_path: Path) -> None:
    """A checkout's scratch folder is not the project: a third-party file there declares
    another project's licence, and the rail judges only what this one ships."""
    root, _scratch = _checkout_with_scratch(tmp_path)
    check_every_declaration_is_mit(root)
    assert sorted(declaration_sites(root)) == ["LICENSE", "pyproject.toml"]


def test_the_same_file_tracked_is_judged(tmp_path: Path) -> None:
    """The control: the identical file, once git tracks it, is the project declaring
    Apache-2.0, so the rail fails on it. Staged is enough; so is untracked but not ignored."""
    root, scratch = _checkout_with_scratch(tmp_path)
    shipped = root / "web" / "TargetManager.ts"
    shipped.parent.mkdir()
    shipped.write_bytes(scratch.read_bytes())
    with pytest.raises(AssertionError, match=r"web/TargetManager\.ts declares \['Apache-2\.0'\]"):
        check_every_declaration_is_mit(root)
    _git(root, "add", "web/TargetManager.ts")
    with pytest.raises(AssertionError, match=r"web/TargetManager\.ts declares \['Apache-2\.0'\]"):
        check_every_declaration_is_mit(root)
    _git(root, "add", "-f", ".local/scratch/vendored/TargetManager.ts")
    with pytest.raises(AssertionError, match=r"\.local/scratch/vendored/TargetManager\.ts"):
        check_every_declaration_is_mit(root)


def test_a_real_non_mit_declaration_still_fails_in_a_checkout(tmp_path: Path) -> None:
    """Reading git's list must not blind the rail: the project's own manifest changed."""
    root, _scratch = _checkout_with_scratch(tmp_path)
    (root / "pyproject.toml").write_text(_pyproject("Apache-2.0"), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"pyproject\.toml declares \['Apache-2\.0'\]"):
        check_every_declaration_is_mit(root)


def test_where_git_cannot_answer_the_walk_prunes_the_scratch_folder(tmp_path: Path) -> None:
    """The fallback, for a tree git does not track: `.local/` is pruned like any other
    folder that cannot hold this project's declaration, and the rest is still read."""
    root = _minimal_tree(tmp_path)
    scratch = root / ".local" / "scratch" / "TargetManager.ts"
    scratch.parent.mkdir(parents=True)
    scratch.write_text(f"// {_SPDX_TAG} Apache-2.0\n", encoding="utf-8")
    assert _git_files(root) is None, "the planted tree must not be inside a git checkout"
    check_every_declaration_is_mit(root)
    (root / "app.py").write_text(f"# {_SPDX_TAG} BUSL-1.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="BUSL-1.1"):
        check_every_declaration_is_mit(root)


def test_the_declaration_sweep_ignores_other_peoples_licences(tmp_path: Path) -> None:
    """The skip rule, proven — otherwise the rail reds on every dependency bump.

    ``package-lock.json`` carries MPL-2.0 today. If the sweep read it, the honest fix would
    be to stop reading lockfiles; asserting the rule here is what stops someone instead
    widening ``_EXPECTED`` to make the red go away, which would disarm the whole rail. That the
    rule is still needed (this repository's lockfile still carries one) is the rail's to say:
    ``test_the_lockfile_skip_rule_is_still_needed``.
    """
    root = _minimal_tree(tmp_path)
    (root / "package-lock.json").write_text('{"license": "MPL-2.0"}\n', encoding="utf-8")
    check_every_declaration_is_mit(root)
    assert sorted(declaration_sites(root)) == ["LICENSE", "pyproject.toml"]
