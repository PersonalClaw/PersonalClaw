"""The hourly backup exports your prompt override, and nothing else in the home with it.

`<home>/prompt.md` (your override of the agent's system prompt) is a file-shaped tree entry. The
shard export read a tree entry that is a file by blobbing its FOLDER, which is the home itself. So
once you had a prompt override, `personalclaw backup export`, the hourly incremental export that
`durability.auto_backup` runs by default, and every sync cycle copied every file in the home into
`shards/agent_prompt_override/blobs/`: the credential store's values (`.env`), the session-signing
key (`.local_secret`), the other stores' raw databases. Shards are the copy that leaves the
machine, and secrets never shard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TOKEN = "ghp_fixture_hourly_backup_0123456789abcd"
SESSION_KEY = "fixture-session-key-5e4d3c2b1a09"
PROMPT = "You are terse, and you answer in one line.\n"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "prompt.md").write_text(PROMPT, encoding="utf-8")
    (home / ".env").write_text(f"FIXTURE_TOKEN={TOKEN}\n", encoding="utf-8")
    (home / ".local_secret").write_text(SESSION_KEY, encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _blobs(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "/blobs/" in p.relative_to(root).as_posix()
    }


def _assert_only_the_prompt(blobs: dict[str, bytes]) -> None:
    leaked = [
        n for n, data in blobs.items() if TOKEN.encode() in data or SESSION_KEY.encode() in data
    ]
    assert leaked == [], f"credential values in the shards: {leaked}"
    mine = {n: d for n, d in blobs.items() if n.startswith("agent_prompt_override/")}
    assert list(mine.values()) == [PROMPT.encode()], sorted(mine)


def test_a_shard_export_carries_the_prompt_override_alone(home, tmp_path):
    from personalclaw.durability.shards import export_shards

    out = tmp_path / "shards"
    export_shards(home, out)

    _assert_only_the_prompt(_blobs(out))


def test_the_copies_an_earlier_export_made_are_removed(home, tmp_path):
    """An export before the fix left the home's files in this entry's blobs, and a blob is never
    rewritten once it exists. The next export of the entry keeps the prompt override alone."""
    import hashlib

    from personalclaw.durability.shards import export_shards

    out = tmp_path / "shards"
    leaked = (home / ".env").read_bytes()
    digest = hashlib.sha256(leaked).hexdigest()
    stray = out / "agent_prompt_override" / "blobs" / digest[:2] / digest
    stray.parent.mkdir(parents=True)
    stray.write_bytes(leaked)

    export_shards(home, out)

    assert not stray.exists()
    _assert_only_the_prompt(_blobs(out))


def test_the_hourly_job_carries_it_alone_too(home):
    from personalclaw.durability.service import run_incremental_export

    result = run_incremental_export()

    assert result.ok, result.detail
    _assert_only_the_prompt(_blobs(home / "shards"))
