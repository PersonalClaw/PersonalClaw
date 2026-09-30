"""Every install in this repository takes PersonalClaw's third-party dependencies from ``uv.lock``.

🔴 The gateway image's dependency layer ran ``pip install ".[slack,…,stt,tts]"``, which resolves
each of ``pyproject.toml``'s ranges to whatever is newest on the day the image is built.
``faster-whisper`` asks for ``av>=11``; PyAV 19 removed the ``metadata_errors`` keyword that
faster-whisper's audio decoder passes to ``av.open``; so every audio upload in the shipped image
failed to transcribe, while every environment the suite runs in (``uv sync --locked``, PyAV 18)
decoded it. The image was the one build nothing tested, and the one build that drifted. The desktop
bundle's venv was built the same way.

So the rule, read from every install command in the tree: a command that installs a distribution
pyproject declares takes it from the lock (``uv sync --locked``, or ``pip install --require-hashes
--no-deps -r`` of a list ``uv export --locked`` wrote), or installs the project ALONE with
``--no-deps``. The few installs that cannot read the lock are listed in ``_RANGE_INSTALLS`` with
why: each installs a published release (or rehearses one), whose wheel carries the ranges and
nothing else — which is why the ranges have to say what works as well
(``tests/test_dependency_bounds.py``).
"""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _ROOT / "deploy" / "docker" / "Dockerfile.backend"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"

#: The installs that resolve pyproject's ranges on purpose, keyed by file and a piece of the
#: command, with the reason the lock cannot apply. Each entry must still match a command: an
#: exemption for a command that is gone is a hole waiting for the next one.
_RANGE_INSTALLS = {
    ("deploy/website/install.sh", "uv tool install"): (
        "the public installer installs the published wheel from PyPI, whose metadata carries "
        "pyproject's ranges and no lock"
    ),
    (".github/workflows/full.yml", "uv tool install ."): (
        "rehearses the public installer on this checkout, so it resolves exactly what that "
        "installer will"
    ),
    (".github/workflows/full.yml", "-e . pip-audit"): (
        "a report-only audit of what a fresh `pip install personalclaw` resolves today, which is "
        "the population the ranges govern; it builds nothing that ships"
    ),
    (".github/workflows/clean-machine-walkthroughs.yml", "pip install --quiet personalclaw"): (
        "installs the released wheel from PyPI: a stranger's documented path is the subject"
    ),
    (".github/workflows/clean-machine-walkthroughs.yml", "pip install --quiet -e ."): (
        "exercises the self-updater's classification of a pip install; the install kind is "
        "the subject, not the dependency set"
    ),
    ("scripts/fresh_install_validate.sh", "personalclaw==${version}"): (
        "validates the released wheel on a clean machine, the way a user installs it"
    ),
}

#: Installer spellings: the token that runs pip (a venv's own ``bin/pip`` included) and uv.
_PIP = re.compile(r"(?:^|/)pip3?$")
_UV = re.compile(r"(?:^|/)uv$|^\$\(UV\)$")
#: Options that take a value, so the value is not read as something to install.
_VALUED = set(
    "-c --constraint -i --index-url --extra-index-url -f --find-links -t --target --prefix --root "
    "--python -p --python-version --platform --only-binary --no-binary --root-user-action "
    "--progress-bar --cache-dir --with --from --index --default-index -o --output-file --extra "
    "--group --format --no-emit-package --prune --package".split()
)
#: Commands whose argv is text for a reader, not an install.
_TEXT = {"echo", "printf", "say", "print", "log"}


@dataclass(frozen=True)
class Install:
    path: str
    command: str
    tokens: tuple[str, ...]


def _declared() -> set[str]:
    """Every distribution pyproject declares, in its core list or any extra."""
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    specs = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        specs += extra
    names = {canonicalize_name(re.match(r"[A-Za-z0-9][\w.-]*", s).group(0)) for s in specs}
    return names - {"personalclaw"}


def _logical_lines(text: str) -> list[str]:
    """The file's lines with backslash continuations joined and full-line comments dropped."""
    out: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):  # a comment line, inside a continued RUN too, as Docker reads it
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        out.append(pending + line)
        pending = ""
    if pending:
        out.append(pending)
    return out


def _expand(token: str, variables: dict[str, str]) -> str:
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: variables.get(m.group(1), m.group(0)), token)


def _commands(path: str, text: str) -> list[Install]:
    """Every install command in *text*: pip, ``uv pip``, ``uv tool``, ``uv sync``, ``uv export``."""
    variables = dict(re.findall(r'^([A-Z_][A-Z0-9_]*)="([^"$]*)"$', text, re.M))
    found: list[Install] = []
    for line in _logical_lines(text):
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = line.split()
        command: list[str] = []
        for token in [*tokens, ";"]:
            if token and set(token) <= set(";&|()"):
                if command:
                    found.extend(_install(path, command, variables))
                command = []
            else:
                command.append(token)
    return found


def _install(path: str, argv: list[str], variables: dict[str, str]) -> list[Install]:
    words = list(argv)
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):  # leading `NAME=value` assignments
        words.pop(0)
    if words[:1] == ["RUN"]:
        words = words[1:]
    if not words or words[0] in _TEXT:
        return []
    for i, word in enumerate(words):
        rest = words[i + 1 :]
        pip = _PIP.search(word) and rest[:1] == ["install"]
        uv = _UV.search(word) and (
            rest[:1] in (["sync"], ["export"])
            or rest[:2] in (["pip", "install"], ["tool", "install"])
        )
        if pip or uv:
            tokens = tuple(_expand(w, variables) for w in words[i:])
            return [Install(path, " ".join(words[i:]), tokens)]
    return []


def _targets(tokens: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """What an install names: its requirement arguments, and the files ``-r`` reads."""
    start = tokens.index("install") + 1
    targets, files = [], []
    it = iter(tokens[start:])
    for token in it:
        if token in ("-r", "--requirement"):
            files.append(next(it, ""))
        elif token in ("-e", "--editable"):
            targets.append(next(it, ""))
        elif token in _VALUED:
            next(it, None)
        elif not token.startswith("-"):
            targets.append(token)
    return targets, files


def _is_the_project(target: str) -> bool:
    if target in (".", "./") or target.startswith((".[", "./[")):
        return True
    name = re.match(r"[A-Za-z0-9][\w.-]*", target)
    return bool(name) and canonicalize_name(name.group(0)) == "personalclaw"


def _from_ranges(install: Install, declared: set[str]) -> str | None:
    """Why *install* resolves pyproject's ranges instead of the lock; ``None`` when it does not."""
    tokens = install.tokens
    verb = tokens[1] if len(tokens) > 1 else ""
    if _UV.search(tokens[0]) and verb in ("sync", "export"):
        if "--locked" in tokens or "--frozen" in tokens:
            return None
        return f"`uv {verb}` without --locked re-resolves the ranges"
    targets, files = _targets(tokens)
    no_deps = "--no-deps" in tokens
    if files and not ("--require-hashes" in tokens and no_deps):
        return "a requirements list installed without --require-hashes --no-deps is not the lock"
    for target in targets:
        if _is_the_project(target) and not no_deps:
            return f"installs {target} with its dependencies, resolved from pyproject's ranges"
        name = re.match(r"[A-Za-z0-9][\w.-]*", target)
        if name and canonicalize_name(name.group(0)) in declared and not _is_the_project(target):
            return f"installs {name.group(0)}, which pyproject declares, from a range"
    return None


def _install_files() -> list[Path]:
    """Every file here that runs an install: images, workflows, shell scripts and the Makefile."""
    files = [
        *(_ROOT / "deploy").rglob("Dockerfile*"),
        *(_ROOT / ".github" / "workflows").glob("*.yml"),
        *(_ROOT / "scripts").glob("*.sh"),
        *(_ROOT / "deploy").rglob("*.sh"),
        _ROOT / "Makefile",
    ]
    return sorted(p for p in files if p.is_file())


def _census() -> list[Install]:
    return [
        install
        for path in _install_files()
        for install in _commands(
            path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8")
        )
    ]


def _exemption(install: Install) -> tuple[str, str] | None:
    for key in _RANGE_INSTALLS:
        where, piece = key
        if install.path == where and piece in install.command:
            return key
    return None


# ── the rule ────────────────────────────────────────────────────────────────────────────────


def test_no_install_path_takes_personalclaws_dependencies_from_the_ranges() -> None:
    declared = _declared()
    offenders = [
        f"{install.path}: `{install.command}` — {why}"
        for install in _census()
        if (why := _from_ranges(install, declared)) and _exemption(install) is None
    ]
    assert not offenders, (
        "these installs resolve pyproject's ranges on the day they run, so what they build is "
        "not what CI tested. Install from uv.lock (`uv sync --locked`, or `uv export --locked` "
        "then `pip install --require-hashes --no-deps -r`), then the project with --no-deps; or, "
        "if it installs a published release, add it to _RANGE_INSTALLS with why:\n"
        + "\n".join(offenders)
    )


def test_every_range_install_on_the_list_still_exists_and_still_needs_to_be_there() -> None:
    declared = _declared()
    matched = {_exemption(i): i for i in _census() if _from_ranges(i, declared)}
    stale = [
        f"{where}: {piece!r}" for where, piece in _RANGE_INSTALLS if (where, piece) not in matched
    ]
    assert not stale, "these exemptions match no range install any more; delete them: " + ", ".join(
        stale
    )


def test_the_census_reads_the_installs_that_matter() -> None:
    """Vacuity floor: a parse that found nothing would make the rule above unfailable."""
    census = _census()
    by_file = {i.path for i in census}
    assert len(census) >= 12, [i.command for i in census]
    for path in (
        "deploy/docker/Dockerfile.backend",
        ".github/workflows/release.yml",
        ".github/workflows/ci.yml",
        "deploy/website/install.sh",
    ):
        assert path in by_file, f"the census read no install in {path}"


def test_the_rule_refuses_the_installs_that_shipped_the_drift() -> None:
    """Positive control, on the exact commands the image and the desktop bundle used to run."""
    declared = _declared()
    drifted = {
        "the image's dependency layer": (
            "RUN mkdir -p src/personalclaw && \\\n"
            "    /opt/venv/bin/pip install --no-cache-dir \\\n"
            "        --extra-index-url https://download.pytorch.org/whl/cpu \\\n"
            '        ".[slack,eval,httpx,anthropic,openai,embeddings,stt,tts]" && \\\n'
            "    /opt/venv/bin/pip uninstall -y personalclaw\n"
        ),
        "the image's test stage": (
            "RUN /opt/venv/bin/pip install --no-cache-dir \\\n"
            "        black isort flake8 mypy pytest pytest-asyncio hypothesis\n"
        ),
        "the desktop venv": (
            "uv pip install --python .venv/bin/python '.[anthropic,openai,slack]' pyinstaller\n"
        ),
        "an unlocked sync": "run: uv sync --extra dev\n",
        "an unhashed list": "pip install -r requirements.txt\n",
    }
    for what, text in drifted.items():
        installs = _commands("fixture", text)
        assert installs, f"{what}: the parser read no install"
        assert any(_from_ranges(i, declared) for i in installs), f"{what} passed the rule"


def test_the_rule_admits_the_lock_and_ignores_advice() -> None:
    declared = _declared()
    locked = (
        "RUN uv export --locked --no-emit-project --extra stt -o /build/requirements.txt && \\\n"
        "    /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps "
        "-r /build/requirements.txt\n"
        "RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --no-build-isolation .\n"
        "run: uv sync --locked --extra dev\n"
        "uv pip install --python .venv/bin/python pyinstaller\n"
        "echo \"install them with: pip install -e '.[dev]'\"\n"
    )
    installs = _commands("fixture", locked)
    assert len(installs) == 5, [i.command for i in installs]
    assert [i.command for i in installs if _from_ranges(i, declared)] == []


# ── the two builds that ship ──────────────────────────────────────────────────────────────


def _stage(text: str, name: str) -> str:
    start = text.index(f" AS {name}\n")
    end = text.find("\nFROM ", start)
    return text[start : end if end != -1 else None]


def test_the_image_installs_the_locked_list_its_dependency_layer_exports() -> None:
    """The layer exports the lock for the image's extras and installs exactly that file, and its
    cache key is pyproject and the lock, so a re-lock rebuilds it."""
    deps = _stage(_DOCKERFILE.read_text(encoding="utf-8"), "deps")
    assert re.search(r"^COPY pyproject\.toml uv\.lock \./$", deps, re.M), deps
    installs = _commands("Dockerfile.backend", deps)
    exports = [i for i in installs if i.tokens[1:2] == ("export",)]
    assert len(exports) == 1, [i.command for i in installs]
    export = exports[0].tokens
    for flag in ("--locked", "--no-emit-project", "--emit-index-url"):
        assert flag in export, f"the image's `uv export` lacks {flag}"
    written = export[export.index("-o") + 1]
    lists = [_targets(i.tokens)[1] for i in installs if "install" in i.tokens]
    assert [written] in lists, f"nothing installs {written}, the list the lock was exported to"


def test_the_lock_gives_linux_the_cpu_torch_build_the_image_always_shipped() -> None:
    """The image installs the lock, so the lock must say Linux's torch is the CPU build: PyPI's
    Linux torch pulls nineteen CUDA runtime packages (2.2 GB of wheels) the image never carried."""
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        uv = tomllib.load(fh).get("tool", {}).get("uv", {})
    indexes = [i for i in uv.get("index", []) if i.get("name") == "pytorch-cpu"]
    assert len(indexes) == 1, "pyproject declares no `pytorch-cpu` index for uv to lock torch from"
    [index] = indexes
    assert index["url"] == "https://download.pytorch.org/whl/cpu"
    assert (
        index.get("explicit") is True
    ), "a non-explicit extra index lets ANY package resolve from it; only torch may"
    assert uv.get("sources", {}).get("torch") == [
        {"index": "pytorch-cpu", "marker": "sys_platform == 'linux'"}
    ]
    with (_ROOT / "uv.lock").open("rb") as fh:
        packages = tomllib.load(fh)["package"]
    linux = [
        p
        for p in packages
        if p["name"] == "torch" and "sys_platform == 'linux'" in p.get("resolution-markers", [])
    ]
    assert linux, "uv.lock records no Linux-only torch"
    assert {p["source"]["registry"] for p in linux} == {index["url"]}
    cuda = sorted(
        p["name"]
        for p in packages
        if p["name"].startswith(("nvidia-", "cuda-")) or p["name"] == "triton"
    )
    assert not cuda, f"uv.lock carries CUDA runtime packages: {cuda}"


def test_the_desktop_bundles_sync_the_lock() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    syncs = [i for i in _commands("release.yml", text) if i.tokens[1:2] == ("sync",)]
    assert len(syncs) >= 2, "release.yml: expected the macOS and Linux desktop venvs to sync"
    for sync in syncs:
        assert "--locked" in sync.tokens and "--no-editable" in sync.tokens, sync.command
