"""The Nix channel follows the release it packages, and the ``nix`` workflow holds it there.

``flake.nix`` packages a published wheel. The dependencies nixpkgs cannot supply at a version
that wheel accepts are built from the release's own ``uv.lock`` entries, which
``scripts/nix_pins.py`` copies into ``nix/pins.toml``; the ``nix`` workflow builds and runs the
package (``scripts/nix_smoke.py``), checks those entries against the release's tag, and builds
this checkout's next release on the same package set. That workflow needs Nix and the tag. What
holds without either is held here, on every change: the pins name the release the recipe
packages and are the script's own output, the script copies a lock faithfully and reads drift
as a failure, the smoke keeps the token out of a log, and the workflow still runs on every file
that can move a bound.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "nix" / "personalclaw.nix"
PINS = REPO_ROOT / "nix" / "pins.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nix.yml"


def _load(name: str) -> ModuleType:
    """Import a script from ``scripts/`` (a tool the flake and the workflow run, not a module)."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_{name}", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nix_pins = _load("nix_pins")
nix_smoke = _load("nix_smoke")


def _entry(name: str, version: str, digest: str, *, extra: str = "") -> str:
    return (
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        "wheels = [\n"
        f'    {{ url = "https://files.example.test/{name}-{version}-py3-none-any.whl", '
        f'hash = "sha256:{digest}" }},\n'
        f"]\n{extra}"
    )


def _lock(*entries: str) -> str:
    return 'version = 1\nrequires-python = ">=3.12, <3.14"\n\n' + "\n".join(entries)


PINNED = {name: _entry(name, "1.0.0", "a" * 64) for name in nix_pins.PINS}
OTHER = _entry("aiohttp", "3.13.5", "b" * 64)


# ── the pins in the tree ─────────────────────────────────────────────────────────────────────


def test_the_pins_name_the_release_the_recipe_packages() -> None:
    """A release bump that moves the recipe and not the pins would build the new wheel on the old
    release's dependencies: the header says which release the entries came from, and it must be
    the recipe's."""
    version = nix_pins.recipe_version(RECIPE.read_text(encoding="utf-8"))
    text = PINS.read_text(encoding="utf-8")
    assert text.startswith(nix_pins.HEADER.format(version=version)), text.splitlines()[0]


def test_the_pins_file_is_the_scripts_own_output() -> None:
    """The file is what the script writes: re-rendered from its own entries it comes back byte
    for byte, which an edited header, reason or order would not, and it pins exactly the names
    the script pins. That the entries ARE the tag's is the workflow's check, which has the tag."""
    text = PINS.read_text(encoding="utf-8")
    version = nix_pins.recipe_version(RECIPE.read_text(encoding="utf-8"))
    lock = "\n".join(line for line in text.splitlines() if not line.startswith("#")) + "\n"
    assert nix_pins.render(lock, version) == text
    names = [entry["name"] for entry in tomllib.loads(text)["package"]]
    assert names == sorted(nix_pins.PINS), names


def test_every_pin_is_a_published_wheel_with_its_digest() -> None:
    for entry in tomllib.loads(PINS.read_text(encoding="utf-8"))["package"]:
        assert entry["source"] == {"registry": "https://pypi.org/simple"}, entry["name"]
        assert entry["wheels"], f"{entry['name']} has no wheel to build from"
        for wheel in entry["wheels"]:
            assert wheel["url"].startswith("https://files.pythonhosted.org/"), wheel["url"]
            assert wheel["hash"].startswith("sha256:") and len(wheel["hash"]) == 71, wheel


# ── scripts/nix_pins.py ──────────────────────────────────────────────────────────────────────


def test_render_copies_each_pinned_entry_verbatim_and_nothing_else() -> None:
    sub_table = '\n[package.optional-dependencies]\nsocks = [\n    { name = "pysocks" },\n]\n'
    entries = {**PINNED, "urllib3": _entry("urllib3", "2.8.0", "c" * 64, extra=sub_table)}
    rendered = nix_pins.render(_lock(OTHER, *entries.values()), "9.9.9")

    assert rendered.startswith(nix_pins.HEADER.format(version="9.9.9"))
    assert '"aiohttp"' not in rendered, "a package nobody pins was copied"
    for name, entry in entries.items():
        assert entry in rendered, f"{name}'s entry was not copied verbatim"
        assert f"# {name}: " in rendered, f"{name} carries no reason"
    assert "[package.optional-dependencies]" in rendered, "an entry lost its sub-table"
    order = [rendered.index(f'name = "{name}"') for name in sorted(entries)]
    assert order == sorted(order), "the entries are not in the lock's (name) order"


@pytest.mark.parametrize(
    ("drop", "double"),
    [("urllib3", None), (None, "croniter")],
    ids=["an entry missing", "two entries of one name"],
)
def test_render_refuses_a_lock_it_cannot_pin_from(drop: str | None, double: str | None) -> None:
    entries = [entry for name, entry in PINNED.items() if name != drop]
    if double:
        entries.append(_entry(double, "2.0.0", "d" * 64))
    with pytest.raises(nix_pins.Unreadable, match=drop or double):
        nix_pins.render(_lock(*entries), "9.9.9")


def test_check_mode_passes_the_lock_it_wrote_and_fails_any_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recipe = tmp_path / "personalclaw.nix"
    recipe.write_text('  version = "9.9.9";\n', encoding="utf-8")
    monkeypatch.setattr(nix_pins, "RECIPE", recipe)
    monkeypatch.setattr(nix_pins, "PINS_FILE", tmp_path / "pins.toml")
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock(OTHER, *PINNED.values()), encoding="utf-8")

    assert nix_pins.main(["--lock", str(lock)]) == 0
    assert nix_pins.main(["--check", "--lock", str(lock)]) == 0

    drifted = PINNED["urllib3"].replace("a" * 64, "e" * 64)
    lock.write_text(_lock(OTHER, *{**PINNED, "urllib3": drifted}.values()), encoding="utf-8")
    capsys.readouterr()
    assert nix_pins.main(["--check", "--lock", str(lock)]) == 1
    assert "+    { url" in capsys.readouterr().err, "the failure does not show what changed"

    assert nix_pins.main(["--check", "--lock", str(tmp_path / "absent.lock")]) == 2


# ── scripts/nix_smoke.py ─────────────────────────────────────────────────────────────────────


def test_the_smoke_prints_no_token() -> None:
    log = (
        'PERSONALCLAW_READY:{"port": 4242, "token": "s3cr3t-value"}\n'
        "Dashboard: http://127.0.0.1:4242/?token=s3cr3t-value\n"
    )
    redacted = nix_smoke.redact(log)
    assert "s3cr3t-value" not in redacted
    assert '"token": "[redacted]"' in redacted and "?token=[redacted]" in redacted


def test_the_smoke_finds_a_child_that_could_not_import() -> None:
    log = [
        "01:42:17 INFO personalclaw.gateway: dashboard on 127.0.0.1:4242\n",
        "01:42:17 WARNING personalclaw.providers.availability: availability probe exited 1: "
        "/nix/store/python3.13: No module named personalclaw\n",
        "ModuleNotFoundError: No module named 'aiohttp'\n",
    ]
    assert nix_smoke.import_failures(log) == [line.rstrip() for line in log[1:]]
    assert nix_smoke.import_failures(log[:1]) == []


# ── .github/workflows/nix.yml ────────────────────────────────────────────────────────────────


def _workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_the_workflow_runs_on_every_file_that_can_move_a_bound() -> None:
    """The channel broke because a bound in pyproject.toml and uv.lock shipped before anything
    built the flake against it; a path dropped from these lists is a change that ships unbuilt."""
    triggers = _workflow().get("on") or _workflow()[True]
    for event in ("pull_request", "push"):
        paths = set(triggers[event]["paths"])
        assert {"flake.nix", "flake.lock", "nix/**", "pyproject.toml", "uv.lock"} <= paths, event
        assert {"scripts/nix_pins.py", "scripts/nix_smoke.py"} <= paths, event
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["schedule"], "nothing rebuilds the channel when the tree does not move"


def test_the_workflow_builds_runs_checks_and_asks_about_the_next_release() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {}
    (job,) = workflow["jobs"].values()
    assert job["permissions"] == {}, "the installer writes the job's token where the gateway reads"
    assert job["runs-on"] == "ubuntu-24.04"
    assert isinstance(job["timeout-minutes"], int)
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    for command in (
        "nix flake check --print-build-logs",
        "nix flake check --all-systems --no-build",
        "python3 scripts/nix_pins.py --check",
        "--file nix/next-release.nix",
    ):
        assert command in runs, command
