"""The documented `config get > f.json` → edit → `config set --file f.json` loop deleted
`providers[]`, with the API keys in it, at ✅ exit 0.

Issue 951 was closed by #3103, which fixed `personalclaw config set <key> <value>` — the one path
its title named. Two siblings on the same root cause were left, and together they are a
round-trip that destroys more than the original single-key write did:

  1. `config get` printed `AppConfig.to_dict()`, a serialize-from-known-fields snapshot of the
     ~40 sections the dataclass models. `providers`, `use_cases`, `slack` and `meta` are not among
     them — `providers` is read DIRECTLY off the raw dict — so a bare `config get providers`
     answered "❌ Unknown key" for a block sitting in the file, and `config get` with no key
     emitted a document that was systematically missing all four.
  2. `config set --file` wrote that document back WHOLESALE.

So the loop the docs describe handed the operator an incomplete file and then trusted it as
complete. Driven on `origin/main` @ `49b9ae4c8` against a home holding `sk-REAL-KEY-1`, all four
blocks were gone afterwards and the command printed `✅ Config loaded from …` and exited 0.

The fix is one primitive shared by every write, and preservation is DERIVED rather than
enumerated: `AppConfig.save()` carried a hand-written `("providers", "use_cases", "slack")`
copy-forward tuple, so a FOURTH unmodeled block would have been dropped by every write until
somebody remembered to extend it. `merge_unmodeled_top_keys` asks what the write already
serialised instead, which needs no list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps.secret_fields import SECRET_MASK

_REAL_KEY = "sk-REAL-KEY-DO-NOT-LOSE"


def _seed(home: Path) -> Path:
    """A config.json holding all four unmodeled blocks plus one nobody enumerated."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "agent": {"log_level": "INFO"},
                "providers": [
                    {"name": "openrouter", "type": "openai_compatible", "api_key": _REAL_KEY}
                ],
                "use_cases": {"chat": "openrouter"},
                "slack": {"bot_token": "xoxb-REAL"},
                "meta": {"created": "2026-01-01"},
                "some_future_app_block": {"opaque": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return cfg


def _pin(monkeypatch, cfg: Path):
    """Point every config path at the temp file. Never the real home."""
    from personalclaw import cli_config
    from personalclaw.config import loader as L

    monkeypatch.setattr(L, "config_path", lambda: cfg)
    monkeypatch.setattr(cli_config, "config_path", lambda: cfg)


# ── the shared primitives ────────────────────────────────────────────────────────────────────────


def test_merge_copies_forward_a_key_nobody_enumerated(tmp_path):
    """The whole point of deriving instead of listing: a block no tuple names still survives."""
    from personalclaw.config.loader import merge_unmodeled_top_keys

    merged = merge_unmodeled_top_keys(
        {"agent": {"log_level": "DEBUG"}},
        {"agent": {"log_level": "INFO"}, "providers": [1], "some_future_app_block": {"x": 1}},
    )
    assert merged["providers"] == [1]
    assert merged["some_future_app_block"] == {"x": 1}
    assert merged["agent"] == {"log_level": "DEBUG"}, "the write owns what it serialised"


def test_read_for_merge_treats_absent_and_empty_as_writable(tmp_path):
    """Absent is safe to write over; an empty file holds no block a write could destroy.

    Refusing on zero bytes would leave a config truncated by a crashed write permanently
    unwritable — a dead end rather than a protection.
    """
    from personalclaw.config.loader import read_config_for_merge

    assert read_config_for_merge(tmp_path / "nope.json") == {}
    empty = tmp_path / "empty.json"
    empty.write_text("   \n", encoding="utf-8")
    assert read_config_for_merge(empty) == {}


@pytest.mark.parametrize(
    "body",
    ['{"providers": [1], BROKEN', "[1, 2, 3]", '"a string"'],
    ids=["invalid", "array", "str"],
)
def test_read_for_merge_refuses_what_it_cannot_read(tmp_path, body):
    """A file whose CONTENT cannot be known is exactly when you must not overwrite it."""
    from personalclaw.config.loader import ConfigPreserveError, read_config_for_merge

    p = tmp_path / "config.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigPreserveError):
        read_config_for_merge(p)


# ── `config get` ─────────────────────────────────────────────────────────────────────────────────


def test_config_get_names_a_block_that_is_in_the_file(tmp_path, monkeypatch, capsys):
    """Defect 1. `config get providers` exited 1 with "Unknown key" for a block on disk.

    NAMING the block is the behaviour this pins. It first asserted the plaintext key as well,
    which railed #3125 green: `config get` printing `providers` was right, printing the API key
    in it was not. The block is still named; the credential inside it is now withheld.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key="providers"))
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out[0]["name"] == "openrouter", "the block on disk must still be named"
    assert out[0]["api_key"] == SECRET_MASK
    assert _REAL_KEY not in captured.out
    assert "providers[0].api_key" in captured.err, "a silent redaction reads as 'no secrets here'"


def test_config_get_reveal_prints_the_credential(tmp_path, monkeypatch, capsys):
    """The escape hatch, and the documented source for a file you mean to write back.

    Withholding by default is only viable because there IS a plaintext source; without one,
    `config set --file` would have no lossless input and the operator would reach for `config
    edit` on the real file instead.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key="providers", reveal=True))
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["api_key"] == _REAL_KEY
    assert captured.err == "", "nothing was withheld, so there is nothing to report"


def test_config_get_withholds_every_credential_in_the_whole_document(tmp_path, monkeypatch, capsys):
    """The issue's own measurement, as a rail: the sentinel count goes 3 → 0.

    Whole-document rather than per-block, because the next credential-bearing block will not be
    `providers` or `slack` — `merge_unmodeled_top_keys` exists precisely because that set grows.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None))
    captured = capsys.readouterr()
    assert _REAL_KEY not in captured.out
    assert "xoxb-REAL" not in captured.out
    dumped = json.loads(captured.out)
    assert dumped["providers"][0]["api_key"] == SECRET_MASK
    assert dumped["slack"]["bot_token"] == SECRET_MASK
    # …and the non-secret content of the same blocks is untouched, or the dump is useless.
    assert dumped["use_cases"] == {"chat": "openrouter"}
    assert dumped["meta"] == {"created": "2026-01-01"}
    assert dumped["providers"][0]["type"] == "openai_compatible"


def test_config_get_with_no_key_dumps_the_unmodeled_blocks(tmp_path, monkeypatch, capsys):
    """The dump is the INPUT to `config set --file`, so a gap here is a gap there."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None))
    dumped = json.loads(capsys.readouterr().out)
    for block in ("providers", "use_cases", "slack", "meta", "some_future_app_block"):
        assert block in dumped, f"{block} is on disk and missing from `config get`"


def test_config_get_still_refuses_a_key_that_is_genuinely_absent(tmp_path, monkeypatch, capsys):
    """The pair. Showing everything would satisfy the two tests above and be just as wrong."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(_args("get", key="no_such_block"))
    assert exc.value.code == 1
    assert "Unknown key" in capsys.readouterr().err


def test_config_get_degrades_to_the_model_view_on_an_unreadable_file(tmp_path, monkeypatch, capsys):
    """A READ path must not adopt the write path's refusal: `AppConfig.load()` has already warned
    and fallen back, and a `config get` that errors tells the operator less than one that shows
    what the loader resolved."""
    from personalclaw import cli_config

    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text("{ BROKEN", encoding="utf-8")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None))
    assert "agent" in json.loads(capsys.readouterr().out)


# ── `config set --file` ──────────────────────────────────────────────────────────────────────────


def test_the_documented_roundtrip_no_longer_deletes_providers(tmp_path, monkeypatch, capsys):
    """🔴 THE DEFECT, END TO END. `config get > f.json` then `config set --file f.json`.

    Also 🔴 #3125's trap: the handed-back file now carries the MASK where the key was, and
    `merge_unmodeled_top_keys` cannot save it — that merge is key-level and shallow, so it sees
    `providers` already present and copies nothing forward. Masking the read without teaching the
    write what a mask means turns this loop from a disclosure into the deletion of the only copy
    of the credential. The intermediate file is asserted to be masked precisely so this test
    cannot pass by the mask never having been applied.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None))
    handed_back = tmp_path / "f.json"
    handed_back.write_text(capsys.readouterr().out, encoding="utf-8")
    assert (
        json.loads(handed_back.read_text(encoding="utf-8"))["providers"][0]["api_key"]
        == SECRET_MASK
    ), "the round-trip source must be the masked document, or this proves nothing"

    cli_config._config_cmd(_args("set", file=str(handed_back)))

    after = json.loads(cfg.read_text(encoding="utf-8"))
    for block in ("providers", "use_cases", "slack", "meta", "some_future_app_block"):
        assert block in after, f"the round-trip deleted {block}"
    assert after["providers"][0]["api_key"] == _REAL_KEY
    assert after["slack"]["bot_token"] == "xoxb-REAL"
    assert SECRET_MASK not in cfg.read_text(encoding="utf-8"), "a mask must never reach disk"


def test_a_reveal_sourced_roundtrip_is_also_lossless(tmp_path, monkeypatch, capsys):
    """The other documented source. `--reveal` carries no masks, so nothing is restored."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None, reveal=True))
    handed_back = tmp_path / "f.json"
    handed_back.write_text(capsys.readouterr().out, encoding="utf-8")

    cli_config._config_cmd(_args("set", file=str(handed_back)))

    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["providers"][0]["api_key"] == _REAL_KEY
    assert after["slack"]["bot_token"] == "xoxb-REAL"


def test_an_edit_beside_a_masked_credential_still_applies(tmp_path, monkeypatch, capsys):
    """The realistic edit: change one field, hand the whole masked document back.

    This is the case that made masking the read alone unacceptable — the operator's edit must
    land AND the credential they never saw must survive it.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    cli_config._config_cmd(_args("get", key=None))
    doc = json.loads(capsys.readouterr().out)
    doc["agent"]["log_level"] = "DEBUG"
    doc["slack"]["command"] = "pclaw"
    handed_back = tmp_path / "f.json"
    handed_back.write_text(json.dumps(doc), encoding="utf-8")

    cli_config._config_cmd(_args("set", file=str(handed_back)))

    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["agent"]["log_level"] == "DEBUG", "the operator's edit must still apply"
    assert after["slack"]["command"] == "pclaw"
    assert after["providers"][0]["api_key"] == _REAL_KEY
    assert after["slack"]["bot_token"] == "xoxb-REAL"


def test_set_file_refuses_a_mask_it_cannot_resolve(tmp_path, monkeypatch, capsys):
    """Fail CLOSED. An unresolvable mask is refused, never written as the credential.

    Reached when the document's shape no longer lines up with the file — a provider renamed or
    inserted between the `get` and the `set`. Restoring by index there would write one
    instance's key onto another, so no pair means no restore, and no restore means no write.
    """
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    before = cfg.read_bytes()
    _pin(monkeypatch, cfg)

    incoming = tmp_path / "in.json"
    incoming.write_text(
        json.dumps({"providers": [{"name": "renamed-since", "api_key": SECRET_MASK}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(_args("set", file=str(incoming)))
    assert exc.value.code == 1
    assert "providers[0].api_key" in capsys.readouterr().err
    assert cfg.read_bytes() == before, "a refused write must leave the file alone"


def test_set_key_refuses_the_mask_as_a_value(tmp_path, monkeypatch, capsys):
    """The other way a mask can arrive: an operator copying what `config get` printed."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    before = cfg.read_bytes()
    _pin(monkeypatch, cfg)

    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(_args("set", key="agent.log_level", value=SECRET_MASK))
    assert exc.value.code == 1
    assert "placeholder" in capsys.readouterr().err
    assert cfg.read_bytes() == before


def test_a_file_that_omits_a_block_does_not_delete_it(tmp_path, monkeypatch, capsys):
    """Deleting by OMISSION is the failure mode: the path whose purpose is restoring a config the
    operator believes is complete must not treat a missing key as "remove this"."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    _pin(monkeypatch, cfg)

    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"agent": {"log_level": "DEBUG"}}), encoding="utf-8")
    cli_config._config_cmd(_args("set", file=str(partial)))

    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["providers"][0]["api_key"] == _REAL_KEY
    assert after["agent"]["log_level"] == "DEBUG", "the file's own content must still apply"


def test_set_file_refuses_rather_than_overwriting_an_unreadable_config(tmp_path, monkeypatch):
    """Same rule as `save()`, now from the same primitive: unreadable is not writable."""
    from personalclaw import cli_config

    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(f'{{"providers": [{{"api_key": "{_REAL_KEY}"}}], BROKEN', encoding="utf-8")
    before = cfg.read_bytes()
    _pin(monkeypatch, cfg)

    incoming = tmp_path / "in.json"
    incoming.write_text(json.dumps({"agent": {"log_level": "DEBUG"}}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(_args("set", file=str(incoming)))
    assert exc.value.code == 1
    assert cfg.read_bytes() == before
    assert _REAL_KEY.encode() in cfg.read_bytes()


def test_set_file_refuses_a_json_document_that_is_not_an_object(tmp_path, monkeypatch, capsys):
    """A JSON array parses fine and is still not a config; writing it would replace the file."""
    from personalclaw import cli_config

    cfg = _seed(tmp_path / "home")
    before = cfg.read_bytes()
    _pin(monkeypatch, cfg)

    incoming = tmp_path / "in.json"
    incoming.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(_args("set", file=str(incoming)))
    assert exc.value.code == 1
    assert cfg.read_bytes() == before


# ── `AppConfig.save()` — derived, not enumerated ─────────────────────────────────────────────────


def test_save_preserves_a_block_the_retired_tuple_never_named(tmp_path, monkeypatch):
    """The hand-written `("providers", "use_cases", "slack")` list was itself the next bug: a
    fourth unmodeled block would be dropped by every write until someone extended it."""
    from personalclaw.config import loader as L

    cfg = _seed(tmp_path / "home")
    monkeypatch.setattr(L, "config_path", lambda: cfg)

    L.AppConfig().save()

    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["some_future_app_block"] == {"opaque": True}
    assert after["providers"][0]["api_key"] == _REAL_KEY
    assert after["use_cases"] == {"chat": "openrouter"}
    assert after["slack"] == {"bot_token": "xoxb-REAL"}


def test_save_still_stamps_its_own_meta_over_the_existing_one(tmp_path, monkeypatch):
    """`meta` is the one unmodeled key a save OWNS, so copy-forward must not win there."""
    from personalclaw.config import loader as L

    cfg = _seed(tmp_path / "home")
    monkeypatch.setattr(L, "config_path", lambda: cfg)

    L.AppConfig().save()

    meta = json.loads(cfg.read_text(encoding="utf-8"))["meta"]
    assert "lastTouchedVersion" in meta
    assert meta.get("created") != "2026-01-01", "the stamped meta must replace the old block"


# ── the rail ─────────────────────────────────────────────────────────────────────────────────────


#: The two safe shapes a config write may take. Both CONSULT the existing document; that is the
#: property, and it is what every instance of #951 skipped.
#:
#: * start from the raw file and mutate it — `read_config_for_merge` then `_dict_put`
#:   (`config set <key> <value>`), so nothing is ever absent to begin with;
#: * start from a document the write owns and copy the file's remaining top-level keys forward —
#:   `merge_unmodeled_top_keys` (`save()`, `config set --file`).
#:
#: Deliberately NOT "must call `merge_unmodeled_top_keys`": the first shape is safe without it,
#: and a rail that demanded it would push the single-key path into a merge it does not need.
_CONSULTED_THE_FILE = ("merge_unmodeled_top_keys", "read_config_for_merge")


def test_no_config_write_reaches_disk_without_consulting_the_existing_file():
    """The shape of this whole bug family, banned at the source.

    Every instance of #951 is the same two lines: take a document that was not derived from the
    file on disk and hand it to `atomic_write`. Three separate call sites did it — `save()`,
    `config set <key> <value>`, and `config set --file` — and each was fixed on its own, which is
    why fixing the first two still left a provider-deleting round-trip. The rail is a rail
    because the next writer will not have read this file.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
    offenders = []
    for path in (src / "cli_config.py", src / "config" / "loader.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for num, line in enumerate(lines, 1):
            if "atomic_write(" not in line:
                continue
            window = "\n".join(lines[max(0, num - 30) : num])
            if not any(tell in window for tell in _CONSULTED_THE_FILE):
                offenders.append(f"{path.name}:{num}: {line.strip()}")
    assert not offenders, (
        "a config write reached `atomic_write` without reading the existing document or merging "
        "its unmodeled top-level keys forward, which is how #951 deleted `providers[]`:\n"
        + "\n".join(offenders)
    )


def test_the_rail_finds_the_writes_it_is_supposed_to_be_guarding():
    """Floor 1 — VACUITY. A scan that matched no `atomic_write` would pass unconditionally, and
    this rail's whole value is that it covers every config write rather than the three known ones.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
    found = sum(
        line.count("atomic_write(")
        for path in (src / "cli_config.py", src / "config" / "loader.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if "atomic_write(" in line and not line.strip().startswith("#")
    )
    assert found >= 3, f"only {found} config writes found — the three known sites must all match"


def test_the_rail_can_tell_a_consulted_write_from_a_blind_one():
    """Floor 2 — DISCRIMINATION. The tells must separate the two, or it passes on anything."""
    merged = "doc = merge_unmodeled_top_keys(d, read_config_for_merge(p))\natomic_write(p, x)"
    mutated = "doc = read_config_for_merge(p)\n_dict_put(doc, key, v)\natomic_write(p, x)"
    blind = "doc = cfg.to_dict()\natomic_write(p, x)"
    assert any(t in merged for t in _CONSULTED_THE_FILE)
    assert any(t in mutated for t in _CONSULTED_THE_FILE)
    assert not any(t in blind for t in _CONSULTED_THE_FILE), "the #951 shape must read as unsafe"


def _args(action: str, **kw):
    """The argparse.Namespace shape `_config_cmd` reads."""
    import argparse

    ns = argparse.Namespace(config_action=action, key=None, value=None, file=None, reveal=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns
