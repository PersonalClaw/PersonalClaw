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
import tomllib

from every_install_path_installs_the_lock_shared import (
    _ROOT,
    _commands,
    _declared,
    _from_ranges,
    _targets,
)

_DOCKERFILE = _ROOT / "deploy" / "docker" / "Dockerfile.backend"


# ── the rule ────────────────────────────────────────────────────────────────────────────────


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
