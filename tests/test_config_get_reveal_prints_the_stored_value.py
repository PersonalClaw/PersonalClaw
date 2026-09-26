"""``personalclaw config get --reveal`` prints a stored credential, not the reference to it.

🔴 THE DEFECT (``origin/main``). #3607 moved provider keys, and #3617 the webhook token, into the
credential store; ``config.json`` holds ``{{secret:PCSECRET_…}}`` in their place. ``config get``
masks them, and says so on stderr: "``personalclaw config get --reveal`` prints them". It did not.
``--reveal`` skipped the mask and printed the document as the file holds it, so the owner, at their
own terminal, got the reference back and no way to see the token they had set.

``--reveal`` is the owner's CLI reading the owner's home, and every use is an audit row
(``config_get_reveal``). A reference whose key the store no longer holds is printed as it is, and
named on stderr, rather than as an empty value that reads as "unset".
"""

from __future__ import annotations

import argparse
import json

import pytest

from personalclaw.config import loader as config_loader

TOKEN = "whk_fixtureRevealToken_0a1b2c3d4e5f6789"
API_KEY = "sk-fixtureRevealProviderKey-00998877665544"


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    return config_loader.config_dir()


def _cli(**kwargs) -> None:
    from personalclaw.cli_config import _config_cmd

    args = {"config_action": "get", "key": None, "value": None, "file": None, "reveal": False}
    _config_cmd(argparse.Namespace(**{**args, **kwargs}))


def _with_a_stored_token_and_provider_key(home, capsys) -> None:
    _cli(config_action="set", key="hooks.webhook_token", value=TOKEN)
    from personalclaw.config.secret_refs import store_provider_options

    doc = json.loads((home / "config.json").read_text(encoding="utf-8"))
    doc["providers"] = [
        {
            "name": "work-openai",
            "type": "openai",
            "options": store_provider_options("work-openai", "openai", {"api_key": API_KEY}),
        }
    ]
    (home / "config.json").write_text(json.dumps(doc), encoding="utf-8")
    raw = (home / "config.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and API_KEY not in raw, "precondition: both are stored, not inline"
    capsys.readouterr()


def test_reveal_prints_the_stored_token_for_one_key(home, capsys) -> None:
    _with_a_stored_token_and_provider_key(home, capsys)
    _cli(key="hooks.webhook_token", reveal=True)
    assert capsys.readouterr().out.strip() == TOKEN


def test_reveal_of_the_whole_document_carries_every_stored_value(home, capsys) -> None:
    _with_a_stored_token_and_provider_key(home, capsys)
    _cli(reveal=True)
    doc = json.loads(capsys.readouterr().out)
    assert doc["hooks"]["webhook_token"] == TOKEN
    assert doc["providers"][0]["options"]["api_key"] == API_KEY
    assert "{{secret:" not in json.dumps(doc)


def test_without_reveal_nothing_changes(home, capsys) -> None:
    _with_a_stored_token_and_provider_key(home, capsys)
    _cli(reveal=False)
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and API_KEY not in captured.out
    assert "--reveal" in captured.err


def test_a_reference_with_nothing_stored_is_printed_as_it_is_and_named(home, capsys) -> None:
    doc = {"hooks": {"webhook_token": "{{secret:PCSECRET_CONFIG_HOOKS__WEBHOOK_TOKEN}}"}}
    (home / "config.json").write_text(json.dumps(doc), encoding="utf-8")
    _cli(key="hooks.webhook_token", reveal=True)
    captured = capsys.readouterr()
    assert captured.out.strip() == "{{secret:PCSECRET_CONFIG_HOOKS__WEBHOOK_TOKEN}}"
    assert "hooks.webhook_token" in captured.err and "credential store" in captured.err


def test_the_revealed_document_round_trips_through_config_set_file(home, capsys, tmp_path) -> None:
    """The module's own documented loop: ``config get --reveal > f.json``, edit, ``config set
    --file f.json``. With real values in the file, the write has to store every one of them again,
    the provider key included, or the reveal would have put it in ``config.json`` in plaintext."""
    from personalclaw.config.credentials import get_credential
    from personalclaw.config.secret_refs import ref_key

    _with_a_stored_token_and_provider_key(home, capsys)
    _cli(reveal=True)
    exported = tmp_path / "config.edit.json"
    exported.write_text(capsys.readouterr().out, encoding="utf-8")
    _cli(config_action="set", file=str(exported))
    raw = (home / "config.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and API_KEY not in raw, "the round trip wrote a credential inline"
    written = json.loads(raw)
    key = ref_key(written["hooks"]["webhook_token"])
    assert key and get_credential(key) == TOKEN
    key = ref_key(written["providers"][0]["options"]["api_key"])
    assert key and get_credential(key) == API_KEY


def test_a_reference_the_owner_typed_to_a_vault_key_is_left_as_written(
    home, capsys, monkeypatch
) -> None:
    """Only a value PersonalClaw moved into the store is revealed. ``{{secret:MY_VAULT_KEY}}``
    typed by hand shares a Secrets-panel credential; printing its value would put it inline in
    the file on the next ``config set --file``."""
    from personalclaw.config.credentials import save_credential

    # A named credential is mirrored into os.environ; registering it first removes it at teardown.
    monkeypatch.setenv("MY_VAULT_KEY", "")
    save_credential("MY_VAULT_KEY", "vault-value-fixture-5566")
    doc = {"hooks": {"webhook_token": "{{secret:MY_VAULT_KEY}}"}}
    (home / "config.json").write_text(json.dumps(doc), encoding="utf-8")
    _cli(key="hooks.webhook_token", reveal=True)
    assert capsys.readouterr().out.strip() == "{{secret:MY_VAULT_KEY}}"
