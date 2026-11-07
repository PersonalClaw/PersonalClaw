"""Regression tests for tools/ci/build_identity.py (#3911, #4585).

These exercise the pure functions only: parsing a job's set-up log, collecting build records
against this run's jobs, and reading a library built from its sdist. No network, no files
outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools" / "ci" / "build_identity.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_identity", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_identity = _load()

SETUP = """\
2026-01-01T00:00:00.0000000Z ##[group]Runner Image
2026-01-01T00:00:00.0000000Z Image: ubuntu-24.04
2026-01-01T00:00:00.0000000Z Version: 20260101.1.0
2026-01-01T00:00:00.0000000Z Included Software: https://example.invalid
2026-01-01T00:00:00.0000000Z ##[endgroup]
2026-01-01T00:00:00.0000000Z Download action repository 'actions/checkout@v4' (SHA:0123456789abcdef0123456789abcdef01234567)
2026-01-01T00:00:00.0000000Z Download action repository 'actions/setup-python@v5' (SHA:89abcdef0123456789abcdef0123456789abcdef)
"""


def test_parse_setup_reads_image_and_actions():
    setup = build_identity.parse_setup(SETUP)
    assert setup.image == "ubuntu-24.04"
    assert setup.version == "20260101.1.0"
    assert [a.action for a in setup.actions] == ["actions/checkout", "actions/setup-python"]
    assert setup.actions[0].sha == "0123456789abcdef0123456789abcdef01234567"


def test_parse_setup_refuses_missing_image():
    with pytest.raises(build_identity.Refused):
        build_identity.parse_setup("2026-01-01T00:00:00Z nothing here\n")


def test_parse_setup_refuses_download_without_sha():
    text = SETUP + (
        "2026-01-01T00:00:00.0000000Z Download action repository 'evil/thing@v1'\n"
    )
    with pytest.raises(build_identity.Refused):
        build_identity.parse_setup(text)


def _record(**overrides):
    base = {
        "version": build_identity.RECORD_VERSION,
        "runner_name": "runner-a",
        "image_os": "ubuntu24",
        "image_version": "20260101.1.0",
        "toolchain": {tool: "1.0" for tool in build_identity.TOOLS},
        "images": [],
        "bases": [],
        "built_from_source": [],
        "artifacts": ["dist/a.whl"],
    }
    base.update(overrides)
    return base


def test_collect_builds_one_record():
    jobs = [{"id": 7, "name": "build", "runner_name": "runner-a"}]
    builds = build_identity.collect([_record()], jobs, lambda job_id: SETUP)
    assert len(builds) == 1
    build = builds[0]
    assert build["job"] == "build"
    assert build["runner"]["image"] == "ubuntu-24.04"
    assert build["runner"]["image_os"] == "ubuntu24"
    assert build["artifacts"] == ["dist/a.whl"]
    assert build["toolchain"]["python"] == "1.0"


def test_collect_refuses_duplicate_runner():
    jobs = [{"id": 7, "name": "build", "runner_name": "runner-a"}]
    with pytest.raises(build_identity.Refused):
        build_identity.collect([_record(), _record()], jobs, lambda job_id: SETUP)


def test_collect_refuses_record_without_job():
    with pytest.raises(build_identity.Refused):
        build_identity.collect([_record()], [], lambda job_id: SETUP)


def test_collect_refuses_empty_records():
    with pytest.raises(build_identity.Refused):
        build_identity.collect([], [], lambda job_id: SETUP)


def test_from_source_reads_record(tmp_path):
    digest = "sha256:" + "0" * 64
    built = {
        "package": "cryptography",
        "version": "1.0",
        "sdist": digest,
        "wheel": "cryptography-1.0.whl",
        "wheel_sha256": digest,
        "rust": "1.80",
        "openssl": "3.0",
        "installed_openssl": "3.0",
    }
    path = tmp_path / "built.json"
    path.write_text(json.dumps(built), encoding="utf-8")
    found = build_identity.from_source([str(path)])
    assert found == [built]


def test_from_source_refuses_openssl_mismatch(tmp_path):
    digest = "sha256:" + "0" * 64
    built = {
        "package": "cryptography",
        "version": "1.0",
        "sdist": digest,
        "wheel": "cryptography-1.0.whl",
        "wheel_sha256": digest,
        "rust": "1.80",
        "openssl": "3.0",
        "installed_openssl": "3.1",
    }
    path = tmp_path / "built.json"
    path.write_text(json.dumps(built), encoding="utf-8")
    with pytest.raises(build_identity.Refused):
        build_identity.from_source([str(path)])
