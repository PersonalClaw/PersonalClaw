"""No symlink under ``src/`` may point outside ``src/`` — the shipped package is self-contained.

🔴 THE DEFECT THIS EXISTS FOR (2026-09-25, a release blocker). The bundled-chat app's sign-off
record, ``src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt``, was a git
SYMLINK to ``../../../../../docs/architecture/bundled-model-signoff.txt``. Every build that had
``docs/`` beside ``src/`` — a checkout, ``uv build``'s wheel and sdist — dereferenced it and
looked fine. ``deploy/docker/Dockerfile.backend`` copies ``src/``, ``pyproject.toml`` and
``setup.py`` and nothing else, so inside the image the link pointed at nothing, setuptools
installed no record, and the gateway could neither offer nor fetch its default chat model:
``chat_download_offer`` null behind all-200 responses, a "no sign-off record found" warning on
every boot, and a first chat that said to start the download "from the chat screen or Settings →
Models" when neither offered one.

The package tree is the unit every channel ships — wheel, sdist, container image, frozen desktop
bundle — and each of them copies ``src/`` by its own rules. A link that escapes it makes what
ships depend on what ELSE a given copy happened to include. So the rule is structural: no
escaping link at all, not "no escaping link that some build fails to dereference today".

**The census is ``git ls-files``** — what every clone and every CI build receives. Not a
filesystem walk: ``src/personalclaw/static/dist`` is a gitignored dev symlink to ``web/dist``
that ``make web-build`` creates on purpose, and a walk would red on every machine that ran it
while saying nothing about what ships. A link's TARGET is read from its index blob (a symlink is
stored as a blob holding the target path), so the rail judges the committed link, not whatever
the working tree happens to hold.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: git's mode for a symbolic link, file or directory alike.
_SYMLINK_MODE = "120000"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def tracked_entries(repo: Path, tree: str = "src") -> list[tuple[str, str, str]]:
    """Every index entry under *tree*, as ``(mode, blob_sha, path)``."""
    entries: list[tuple[str, str, str]] = []
    for record in _git(repo, "ls-files", "-s", "-z", "--", tree).decode("utf-8").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, sha, _stage = meta.split()
        entries.append((mode, sha, path))
    return entries


def escaping_symlinks(repo: Path, tree: str = "src") -> list[str]:
    """The tracked symlinks under *tree* whose target resolves OUTSIDE *tree*, each described.

    Resolution is lexical, against the link's own directory — which is exactly what a copy of
    *tree* on its own sees. A chain of links inside the tree is judged link by link, so one that
    hops out on its second step is still caught, on that step.
    """
    found: list[str] = []
    for mode, sha, path in tracked_entries(repo, tree):
        if mode != _SYMLINK_MODE:
            continue
        target = _git(repo, "cat-file", "blob", sha).decode("utf-8")
        if posixpath.isabs(target):
            found.append(f"{path} -> {target} (an absolute path)")
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        if resolved != tree and not resolved.startswith(tree + "/"):
            found.append(f"{path} -> {target} (resolves to {resolved}, outside {tree}/)")
    return found


def test_no_symlink_under_src_points_outside_it() -> None:
    """The real tree: zero escaping links, over a census that demonstrably read the tree."""
    entries = tracked_entries(_REPO_ROOT)
    # Vacuity floor: a census that listed nothing would report zero escaping links too.
    assert len(entries) > 1000, f"git ls-files saw only {len(entries)} entries under src/"
    escaping = escaping_symlinks(_REPO_ROOT)
    assert escaping == [], (
        "a symlink under src/ points outside it, so the shipped package is not self-contained "
        "— any channel that copies src/ without the link's target (the container image copies "
        "only src/, pyproject.toml and setup.py) ships a dangling link and silently drops the "
        "file. Make the file real where it ships and have the other location reference it:\n  "
        + "\n  ".join(escaping)
    )


def _seed_repo(root: Path, files: dict[str, str], links: dict[str, str]) -> Path:
    """A real git index holding *files* and *links* — the rail's actual input, not a fake."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")
    for rel, target in links.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, root / rel)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def test_the_rail_catches_an_escaping_link_and_passes_one_that_stays_inside(
    tmp_path: Path,
) -> None:
    """Positive control and near-miss control in one real index.

    The first link is the measured defect's exact shape. The inside links are the ordinary
    relative links a package may legitimately carry, and they must NOT red — a rail that fired
    on every symlink would be removed the first time someone needed one.
    """
    repo = _seed_repo(
        tmp_path / "repo",
        files={
            "docs/architecture/record.txt": "model_id: x\n",
            "src/pkg/apps/other/data.txt": "data\n",
            "src/pkg/real.txt": "real\n",
        },
        links={
            # The defect: a link out of src/ into docs/.
            "src/pkg/apps/app/record.txt": "../../../../docs/architecture/record.txt",
            # Absolute targets escape by definition.
            "src/pkg/abs": "/etc/hosts",
            # Up to the repository root itself — outside src/ even though nothing follows.
            "src/pkg/root": "../..",
            # Inside: a sibling app's file, and a detour that normalises back in.
            "src/pkg/apps/app/sibling.txt": "../other/data.txt",
            "src/pkg/detour.txt": "apps/../real.txt",
        },
    )
    found = escaping_symlinks(repo)
    joined = "\n".join(found)
    assert len(found) == 3, joined
    assert "src/pkg/apps/app/record.txt -> ../../../../docs/architecture/record.txt" in joined
    assert "src/pkg/abs -> /etc/hosts (an absolute path)" in joined
    assert "src/pkg/root -> ../.." in joined
    assert "sibling.txt" not in joined and "detour.txt" not in joined
