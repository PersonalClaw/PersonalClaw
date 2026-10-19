"""The getting-started guide and every surface a newcomer reads say the same, true thing.

The tree half of ``tests/test_getting_started_walkthrough.py``, which drives the guide's first two
commands in a subprocess. These read the guide, the other docs a newcomer meets, the website
installer and the shipped source: the project's GitHub owner is spelled one way, the guide does
not promise what ``setup`` does not collect, and the four Docker surfaces state one command. They
read the tree, so they are a rail: they run once per CI run, in the rails job.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from rails_repo import REPO

REPO_ROOT = REPO


# ── Remote friction: GitHub owner casing ─────────────────────────────────────

#: The canonical owner, measured: `https://github.com/PersonalClaw/PersonalClaw` answers 200
#: with no redirect, and an anonymous `git clone` of it succeeds with no credentials.
_CANONICAL_OWNER = "PersonalClaw"

#: Literal `github.com/<owner>/<repo>` URLs only. `api.github.com/repos/...` has `repos` in the
#: owner slot and is deliberately out of scope, as are f-string URLs built from a constant.
_GH_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

#: Shipped code and the docs a newcomer reads. Roadmap plans are excluded on purpose: they
#: quote the *old* `keyurgolani/*` repos as historical record, and rewriting a record to
#: satisfy a lint rule is a false positive.
_SCAN_ROOTS = (
    "src",
    "deploy",
    "docs/guides",
    "docs/reference",
    "README.md",
    "CONTRIBUTING.md",
)

_TEXT_SUFFIXES = {".py", ".md", ".sh", ".yaml", ".yml", ".toml", ".ts", ".tsx", ".json"}


def _scan_github_urls() -> list[tuple[Path, str, str]]:
    hits: list[tuple[Path, str, str]] = []
    for root in _SCAN_ROOTS:
        target = REPO_ROOT / root
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for path in files:
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for owner, repo in _GH_URL.findall(text):
                hits.append((path, owner, repo))
    return hits


def test_project_github_urls_use_the_canonical_owner_casing() -> None:
    """`github.com/personalclaw/personalclaw` in shipped code is a consistency defect.

    Two of them shipped: the knowledge crawler's outbound `User-Agent` (the project's public
    identity to every site it fetches) and the systemd unit's `Documentation=` field, which
    `personalclaw service install` writes onto the user's machine — a command the guide's
    "Where to go next" recommends. GitHub resolves the lowercase form (200, no redirect), so
    this is legibility, not a broken link; it is still the project's own name spelled wrong
    in state we hand to third parties.
    """
    hits = _scan_github_urls()

    # Vacuity floor: a rail that matched nothing looks clean forever.
    assert len(hits) >= 10, f"URL scan found only {len(hits)} github.com URLs — rail is inert"
    assert len({p for p, _, _ in hits}) >= 5, "URL scan reached fewer than 5 files"

    ours = [(p, owner, repo) for p, owner, repo in hits if owner.lower() == "personalclaw"]
    assert len(ours) >= 8, f"only {len(ours)} own-org URLs matched — the owner filter is inert"

    wrong = [
        f"{p.relative_to(REPO_ROOT)}: github.com/{owner}/{repo}"
        for p, owner, repo in ours
        if owner != _CANONICAL_OWNER
    ]
    assert not wrong, "non-canonical GitHub owner casing:\n  " + "\n  ".join(wrong)


def test_the_guide_does_not_promise_setup_collects_a_provider_credential() -> None:
    """Guide § 1 claimed `setup` gathers "name + first provider credential". It gathers
    neither: a fresh install has no provider app to hold a credential, which § 3 then
    correctly explains. The walkthrough followed § 1, expected a configured provider, and
    had none — so the two sections contradicted each other at the newcomer's expense.
    """
    guide = source_index.text(REPO_ROOT / "docs" / "guides" / "getting-started.md")
    installer = (REPO_ROOT / "deploy" / "website" / "install.sh").read_text(encoding="utf-8")

    assert "name + first provider credential" not in guide
    assert "name + first model provider" not in installer
    # And the correction is present, not merely the claim removed.
    assert "does **not** ask for a model provider credential" in guide


# ── One acquisition step, stated identically on every Docker surface ──

#: Every surface that hands a reader a checkout-free way to acquire PersonalClaw. Four
#: places tell that story, and before this rail only two of them told it: `grep -c
#: 'ghcr.io/personalclaw'` was 1 in the README and 3 in the container guide, but **0** in
#: both the getting-started guide and the website installer's `--container` output. A
#: newcomer who landed on either of those two was sent to a Compose path that begins "from
#: a checkout" — which is precisely the step the single-container image exists to remove.
_DOCKER_SURFACES = (
    "README.md",
    "docs/guides/getting-started.md",
    "docs/guides/containers.md",
    "deploy/website/install.sh",
)

#: The published gateway image. Four surfaces agreeing on a command that pulls the wrong
#: image would satisfy agreement and fail the reader, so the floor below names it.
_GATEWAY_IMAGE = "ghcr.io/personalclaw/personalclaw-gateway:"


def _docker_run_command(path: Path) -> str:
    """The `docker run` line *path* hands a reader, however that file is formatted.

    One extraction rule for all four surfaces — three markdown files and a POSIX shell
    script — because an agreement test that reads each surface its own way can only compare
    what its own parsers happen to agree on. A line is the command iff its stripped form
    begins ``docker run ``: that reaches inside a markdown fence and inside the installer's
    ``print_container`` heredoc alike, while skipping the prose mentions (README's
    "``docker rm``/``docker run``" sentence starts with a backtick, not the command).

    Deliberately NOT ``tools.docker_single_container_smoke.readme_docker_run``: that one
    returns a whole fenced block, and `containers.md` puts `docker run` and `docker exec`
    in one fence, so it cannot compare the *command* across these four.
    """
    text = path.read_text(encoding="utf-8")
    joined = re.sub(r"\s*\\\n\s*", " ", text)  # a backslash-continued command is one line
    found = [ln.strip() for ln in joined.splitlines() if ln.strip().startswith("docker run ")]

    assert found, (
        f"{path.relative_to(REPO_ROOT)} states no `docker run ` command, so a reader there "
        "is never told the checkout-free path exists."
    )
    assert len(found) == 1, (
        f"{path.relative_to(REPO_ROOT)} states {len(found)} different `docker run` commands "
        f"— a reader has to guess which one to copy: {found!r}"
    )
    return found[0]


def test_every_docker_surface_states_the_acquisition_command() -> None:
    """The half that was 2/4: all four surfaces must carry the command at all."""
    for name in _DOCKER_SURFACES:
        command = _docker_run_command(REPO_ROOT / name)
        assert _GATEWAY_IMAGE in command, (
            f"{name}'s command does not run the published gateway image "
            f"({_GATEWAY_IMAGE}…): {command!r}"
        )


def test_the_four_docker_surfaces_cannot_drift_apart() -> None:
    """The drift half of the rail: the four copies must be the SAME command.

    `tests/test_docker_single_container.py` already proves the README's command publishes
    its port, persists state and does not disable auth. Nothing made those properties true
    of the *other three* copies — so this asserts byte-equality, which is what makes that
    suite's guarantees transitive to every surface a reader might actually land on. The
    alternative to four identical copies is one copy plus three links, and copies were chosen
    on purpose: a reader is handed the command where they already are.
    """
    commands = {name: _docker_run_command(REPO_ROOT / name) for name in _DOCKER_SURFACES}

    # Vacuity floor: comparing fewer than four surfaces would agree trivially.
    assert len(commands) == 4, f"the surface list collapsed to {sorted(commands)}"

    distinct = sorted(set(commands.values()))
    assert len(distinct) == 1, "the Docker acquisition command has drifted:\n" + "\n".join(
        f"  {name}: {cmd}" for name, cmd in sorted(commands.items())
    )
