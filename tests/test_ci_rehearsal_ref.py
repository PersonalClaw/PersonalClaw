"""The release rehearsal builds as the tag the tree's own version names (#3942)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ci import rehearsal_ref


def _pyproject(tmp_path: Path, version: str | None) -> Path:
    path = tmp_path / "pyproject.toml"
    line = f'version = "{version}"\n' if version is not None else ""
    path.write_text(f'[project]\nname = "personalclaw"\n{line}', encoding="utf-8")
    return path


def test_the_tag_is_the_projects_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert rehearsal_ref.rehearsal_ref(_pyproject(tmp_path, "0.3.0")) == "v0.3.0"
    assert rehearsal_ref.main([str(_pyproject(tmp_path, "1.2.10"))]) == 0
    assert capsys.readouterr().out == "ref=v1.2.10\n"


@pytest.mark.parametrize("version", ["0.3.0rc1", "0.3.0.dev0", "0.3", "0.3.0+local", None])
def test_a_version_the_release_tag_parser_would_refuse_is_refused(
    tmp_path: Path, version: str | None
) -> None:
    assert rehearsal_ref.main([str(_pyproject(tmp_path, version))]) == 1
