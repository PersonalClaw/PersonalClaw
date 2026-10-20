"""The source-tree checks split from
``tests/test_app_from_template.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import app_from_template_shared
import pytest
from app_from_template_shared import _args

from personalclaw.cli_app_new import ScaffoldError, app_cmd, from_template, scaffold

local_archive_server = app_from_template_shared.local_archive_server


@pytest.fixture(scope="module")
def template_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The template repository's shape: a fresh tool scaffold plus its root-level CI workflow."""
    app = scaffold(
        "app-template",
        "tool",
        dest=tmp_path_factory.mktemp("template-repo"),
        display_name="App Template",
        description="The PersonalClaw app template: clone it, rename it, ship it.",
        author="PersonalClaw contributors",
    )
    workflow = app.path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: ci\non: [push]\n", encoding="utf-8")
    # Vacuity: every positive test below extracts this tree, so it must hold the files the
    # platform leg installs from.
    for rel in ("app.json", "provider.py", ".github/workflows/ci.yml"):
        assert (app.path / rel).is_file(), f"the template fixture is missing {rel}"
    return app.path


def _template_tarball(tree: Path, root: str = "app-template-main") -> bytes:
    """*tree*, packed the way GitHub packs a repo tarball (one wrapper dir)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tree, arcname=root, recursive=True)
    return buf.getvalue()


def test_a_200_tarball_is_fetched_and_extracted(
    local_archive_server, template_tree: Path, tmp_path: Path
) -> None:
    """The whole default path — validate, fetch, extract — over a real socket."""
    server, url = local_archive_server
    server.reply = (200, {"Content-Type": "application/gzip"}, _template_tarball(template_tree))
    result = from_template(dest=tmp_path, url=url)
    assert result.source == url
    assert result.path == tmp_path / "app-template"
    assert "app.json" in result.files
    assert (result.path / "provider.py").is_file()


def test_an_existing_non_empty_target_is_refused_unless_forced(
    template_tree: Path, tmp_path: Path
) -> None:
    data = _template_tarball(template_tree)
    first = from_template(dest=tmp_path, archive=_write(tmp_path, data))
    assert first.files
    with pytest.raises(ScaffoldError) as exc:
        from_template(dest=tmp_path, archive=_write(tmp_path, data))
    assert "not empty" in str(exc.value)
    again = from_template(dest=tmp_path, archive=_write(tmp_path, data), force=True)
    assert again.path == first.path


def test_a_symlinked_target_is_refused(template_tree: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "app-template").symlink_to(real, target_is_directory=True)
    with pytest.raises(ScaffoldError) as exc:
        from_template(
            dest=tmp_path / "dst", archive=_write(tmp_path, _template_tarball(template_tree))
        )
    assert "symlink" in str(exc.value)


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "template.tar.gz"
    path.write_bytes(data)
    return path


def test_the_github_wrapper_directory_is_stripped(template_tree: Path, tmp_path: Path) -> None:
    """A GitHub tarball wraps the repo in ``<repo>-<ref>/``; the app must land at the root."""
    result = from_template(
        dest=tmp_path, archive=_write(tmp_path, _template_tarball(template_tree, "app-template-v9"))
    )
    assert (result.path / "app.json").is_file()
    assert not (result.path / "app-template-v9").exists()
    assert (result.path / ".github" / "workflows" / "ci.yml").is_file()


def test_the_fetched_template_installs_and_registers_its_provider(
    template_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone-to-installed claim, driven through the real install path in a fake home."""
    import personalclaw.config.loader as loader
    from personalclaw.apps import app_manager, manager
    from personalclaw.providers.registry import get_provider_registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setattr(manager, "config_dir", lambda: home)

    result = from_template(
        dest=tmp_path / "dst", archive=_write(tmp_path, _template_tarball(template_tree))
    )
    registry = get_provider_registry()
    try:
        installed = app_manager.install(result.path, origin="local", confirm=True)
        assert installed.ok, f"install refused: {installed.error}"
        assert app_manager.enable("app-template")
        ext = registry.get("app-template")
        assert ext is not None, "the template installed but registered no provider"
        assert ext.enabled, f"registered but not enabled — {ext.error}"
        assert ext.provider_config.type == "tool"
    finally:
        app_manager.disable("app-template")
        registry.deregister("app-template")


def test_the_cli_fetches_from_a_local_archive(
    template_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    archive = _write(tmp_path, _template_tarball(template_tree))
    code = app_cmd(
        _args(from_template=True, dest=str(tmp_path / "dst"), template_archive=str(archive))
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "app.json" in out
    assert (tmp_path / "dst" / "app-template" / "app.json").is_file()
