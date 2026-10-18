"""The source-tree checks split from
``tests/test_ci_action_pins.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ci_action_pins_shared import OTHER, REPO, SHA, FakeAPI, _commit, _ref, _workflow

from tools.ci import action_pins


def test_local_actions_and_container_actions_are_not_asked_about() -> None:
    api = FakeAPI({})
    files = _workflow("uses: ./.github/actions/setup", f"uses: docker://alpine@sha256:{'d' * 64}")
    assert action_pins.check(files, api) == []
    assert api.calls == {}


def test_main_refuses_a_tree_with_no_pins(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert action_pins.main([str(tmp_path)]) == 1


def test_the_workflow_runs_it_weekly_and_on_pin_changes_read_only() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "action-pins.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "pull_request", "workflow_dispatch"}
    assert set(triggers["pull_request"]["paths"]) == {
        ".github/workflows/**",
        ".github/actions/**",
        "tools/ci/action_pins.py",
    }
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["sha-matches-tag"]
    assert job["permissions"] == {"contents": "read"}
    runs = [s for s in job["steps"] if "action_pins.py" in str(s.get("run", ""))]
    assert len(runs) == 1 and set(runs[0]["env"]) == {"GH_TOKEN", "EVENT"}
    # On a pull request an unresolved answer warns; everywhere else it fails.
    run = str(runs[0]["run"])
    assert 'if [ "$EVENT" = pull_request ]; then' in run
    assert run.count("--unresolved-warns") == 1


def test_on_a_pull_request_an_unresolved_answer_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "a.yml").write_text(
        f"jobs:\n  it:\n    steps:\n      - uses: a/b@{SHA} # v1.0.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        action_pins, "github_fetch", lambda token: FakeAPI({_ref("a/b", "v1.0.0"): (500, None)})
    )
    assert action_pins.main(["--unresolved-warns", str(tmp_path)]) == 0
    assert "::warning::" in capsys.readouterr().out
    assert action_pins.main([str(tmp_path)]) == 1
    monkeypatch.setattr(
        action_pins, "github_fetch", lambda token: FakeAPI({_ref("a/b", "v1.0.0"): _commit(OTHER)})
    )
    assert action_pins.main(["--unresolved-warns", str(tmp_path)]) == 1


def test_main_reads_yaml_workflows_and_local_actions(tmp_path: Path) -> None:
    """A pin in a `.yaml` workflow or a local action's `action.yml` is read like any other."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "setup").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "a.yaml").write_text("x\n", encoding="utf-8")
    (tmp_path / ".github" / "actions" / "setup" / "action.yml").write_text("x\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "notes.txt").write_text("x\n", encoding="utf-8")
    found = [
        p.relative_to(tmp_path).as_posix() for p in action_pins.workflow_and_action_files(tmp_path)
    ]
    assert found == [".github/actions/setup/action.yml", ".github/workflows/a.yaml"]
