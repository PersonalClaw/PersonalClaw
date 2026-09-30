"""Every way PersonalClaw is installed can call an external MCP server.

🔴 The client SDK (``mcp``) was an OPTIONAL extra, and three of the four install paths never
asked for it: the public installer (``uv tool install personalclaw``), a bare ``pip install
personalclaw``, and the desktop bundle, which ``release.yml`` builds with the ``anthropic``,
``openai`` and ``slack`` extras. Only the container image named the extra. On the other three the
agent's MCP registry was empty, so no server's tools reached an agent, while the Tools page probed
each stdio server with a handshake of its own and drew it "ready". The one error that said why
advised ``pip install 'personalclaw[mcp]'``, which on a ``uv tool`` install puts the SDK into an
environment the gateway does not run from.

The fix is the dependency, not the advice. ``mcp`` is a core requirement, so no install path can
lack it and no copy has to explain how to add it. These rails hold that from each install path's
side: the command as its own file carries it resolves ``mcp``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]


def _resolves(extras: set[str]) -> set[str]:
    """The distributions ``personalclaw[<extras>]`` asks for: the core requirements, every named
    extra's, and the extras those name in turn (``dev`` → ``test``, …). An extra pyproject does not
    define asks for nothing, which is what pip and uv do with one (they warn and go on)."""
    project = _project()
    optional = project["optional-dependencies"]
    names: set[str] = set()
    pending, seen = set(extras), set()
    specs = list(project["dependencies"])
    while pending:
        extra = pending.pop()
        if extra in seen:
            continue
        seen.add(extra)
        specs += optional.get(extra, [])
        for spec in list(specs):
            req = Requirement(spec)
            if canonicalize_name(req.name) == "personalclaw":
                pending |= set(req.extras) - seen
    for spec in specs:
        req = Requirement(spec)
        if canonicalize_name(req.name) != "personalclaw":
            names.add(canonicalize_name(req.name))
    return names


def _extras(bracket: str | None) -> set[str]:
    return {e.strip() for e in (bracket or "").strip("[]").split(",") if e.strip()}


def _installer_extras() -> set[str]:
    """What ``deploy/website/install.sh`` (the ``curl … | sh`` one-liner) installs."""
    text = (_ROOT / "deploy" / "website" / "install.sh").read_text(encoding="utf-8")
    assert re.search(r'^PC_PACKAGE="personalclaw"$', text, re.M), "install.sh installs another name"
    found = re.findall(r'uv tool install [^\n]*"\$PC_PACKAGE(\[[^\]]*\])?>=', text)
    assert found, 'install.sh has no `uv tool install "$PC_PACKAGE…"` line to read'
    return set().union(*(_extras(b) for b in found))


def _extra_flags(command: str) -> set[str]:
    """The extras a ``uv sync`` / ``uv export`` command names, one ``--extra`` flag each."""
    return set(re.findall(r"--extra[= ]+([A-Za-z0-9_-]+)", command))


def _desktop_extras() -> list[set[str]]:
    """Every venv ``release.yml`` builds a desktop bundle from (macOS and Linux)."""
    text = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    found = re.findall(r"uv sync --locked [^\n]*", text)
    assert len(found) >= 2, f"release.yml: expected the two desktop syncs, read {found}"
    return [_extra_flags(c) for c in found]


def _image_extras() -> set[str]:
    """What the gateway image's dependency layer installs: the extras it exports from the lock."""
    text = (_ROOT / "deploy" / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    deps = text[text.index(" AS deps\n") :]
    deps = re.sub(r"\\\n\s*", " ", deps[: deps.find("\nFROM ")])
    found = re.findall(r"uv export --locked [^\n]*", deps)
    assert found, "Dockerfile.backend's deps stage has no `uv export --locked` to read"
    return set().union(*(_extra_flags(c) for c in found))


def test_the_mcp_sdk_is_a_core_requirement_with_its_bound():
    """The declaration itself. Importability proves nothing here: the dev venv has the SDK either
    way, which is exactly why a path that lacked it stayed green."""
    project = _project()
    mcp = [Requirement(s) for s in project["dependencies"] if Requirement(s).name == "mcp"]
    assert mcp, "`mcp` is not in [project] dependencies, so a default install cannot call MCP tools"
    assert mcp[0].marker is None, f"the core `mcp` requirement is conditional: {mcp[0]}"
    assert (
        "mcp" not in project["optional-dependencies"]
    ), "an `mcp` extra next to the core requirement is a second way to ask for the same thing"


@pytest.mark.parametrize(
    ("path", "extras"),
    [
        ("the public installer (`curl … | sh`)", lambda: [_installer_extras()]),
        ("`pip install personalclaw`", lambda: [set()]),
        ("the desktop bundle (release.yml)", _desktop_extras),
        ("the gateway image", lambda: [_image_extras()]),
    ],
    ids=["installer", "pip", "desktop", "image"],
)
def test_every_install_path_resolves_the_mcp_sdk(path, extras):
    for asked in extras():
        assert "mcp" in _resolves(asked), f"{path} installs personalclaw{sorted(asked)} without mcp"


def test_the_image_names_no_extra_that_does_not_exist():
    """The image's extras list is pinned by hand. An extra pyproject no longer defines installs
    nothing and only warns, so a stale name there would read as a promise the image does not keep.
    """
    defined = set(_project()["optional-dependencies"])
    assert _image_extras() <= defined, f"undefined extras: {sorted(_image_extras() - defined)}"


#: Every surface a user reads advice on: the package, the SPA, the docs and the repo's front page.
_COPY = [
    *(_ROOT / "src").rglob("*.py"),
    *(_ROOT / "src").rglob("*.md"),
    *(_ROOT / "web" / "src").rglob("*.ts"),
    *(_ROOT / "web" / "src").rglob("*.tsx"),
    *(_ROOT / "docs").rglob("*.md"),
    _ROOT / "README.md",
    _ROOT / "CONTRIBUTING.md",
]


def test_the_copy_census_reads_real_files():
    """Vacuity floor: a moved tree would make the rail below pass over nothing."""
    assert len([p for p in _COPY if p.is_file()]) > 500


def test_no_copy_tells_a_user_to_install_the_mcp_extra():
    """The SDK ships with every install, so advice to add it is advice to install an extra that
    does not exist. CHANGELOG.md is history and is not read."""
    offenders = [
        f"{p.relative_to(_ROOT)}:{n}"
        for p in _COPY
        if p.is_file()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "personalclaw[mcp]" in line
    ]
    assert not offenders, "still tells a user to install `personalclaw[mcp]`: " + ", ".join(
        offenders
    )
