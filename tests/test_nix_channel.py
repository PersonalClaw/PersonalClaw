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
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
from nix_channel_shared import REPO_ROOT

RECIPE = REPO_ROOT / "nix" / "personalclaw.nix"
PINS = REPO_ROOT / "nix" / "pins.toml"


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


PYTHON_NIX = REPO_ROOT / "nix" / "python.nix"


def test_every_pin_is_imported_by_the_name_its_build_checks() -> None:
    """nix/python.nix checks each pin's wheel by importing it: as the distribution's name with
    ``_`` for ``-``, unless its ``importNames`` names another module (Pillow is ``PIL``). A pin
    neither name reaches fails that check on every system, which only a Nix build would show."""
    table = re.search(r"importNames = \{(.*?)\};", PYTHON_NIX.read_text(encoding="utf-8"), re.S)
    assert table, "nix/python.nix has no importNames table"
    names = dict(re.findall(r'^\s*([\w-]+) = "([\w.]+)";$', table.group(1), re.M))
    for pin in nix_pins.PINS:
        module = names.get(pin, pin.replace("-", "_"))
        assert (
            importlib.util.find_spec(module) is not None
        ), f"{pin} is checked as {module}, no module"


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
