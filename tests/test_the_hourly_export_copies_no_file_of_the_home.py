"""The hourly export copies no file of the home: not your prompt override, not what sits beside it.

`<home>/prompt.md` (your override of the agent's system prompt) is a file-shaped tree entry. The
shard export read a tree entry that is a file by blobbing its FOLDER, which is the home itself. So
once you had a prompt override, `personalclaw backup export`, the hourly incremental export that
`durability.auto_backup` runs by default, and every sync cycle copied every file in the home into
`shards/agent_prompt_override/blobs/`: the credential store's values (`.env`), the session-signing
key (`.local_secret`), the other stores' raw databases. Shards are the copy that leaves the
machine, and secrets never shard.

The shards carry no folder or file store at all now: a snapshot holds those, and is what a restore
reads (`test_the_snapshot_is_the_backup.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

TOKEN = "fake-github-token-fixture_hourly_backup_0123456789abcd"
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


def _exported(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _assert_no_file_of_the_home(exported: dict[str, bytes]) -> None:
    leaked = [
        n
        for n, data in exported.items()
        if TOKEN.encode() in data or SESSION_KEY.encode() in data or PROMPT.encode() in data
    ]
    assert leaked == [], f"files of the home in the shards: {leaked}"
    assert not any(n.startswith("agent_prompt_override/") for n in exported)


@pytest.mark.parametrize("for_sync", [False, True], ids=["the hourly export", "a sync"])
def test_a_shard_export_carries_no_file_of_the_home(home, tmp_path, for_sync):
    from personalclaw.durability.shards import export_shards

    out = tmp_path / "shards"
    export_shards(home, out, for_sync=for_sync)

    _assert_no_file_of_the_home(_exported(out))


def test_the_copies_an_earlier_export_made_are_removed(home, tmp_path):
    """An export before the fix left the home's files in this entry's blobs, and a blob is never
    rewritten once it exists. The next export removes them."""
    import hashlib

    from personalclaw.durability.shards import export_shards

    out = tmp_path / "shards"
    export_shards(home, out)
    leaked = (home / ".env").read_bytes()
    digest = hashlib.sha256(leaked).hexdigest()
    stray = out / "agent_prompt_override" / "blobs" / digest[:2] / digest
    stray.parent.mkdir(parents=True)
    stray.write_bytes(leaked)

    export_shards(home, out)

    assert not stray.exists()
    _assert_no_file_of_the_home(_exported(out))
