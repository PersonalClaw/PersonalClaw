"""The dotenv credential write has no creation window and no truncation window.

`_dotenv_save_credential` used to do `ep.write_text(...)` and then `ep.chmod(0o600)`. Two defects
in that pair, and the final mode is identical either way — which is why a mode-only assertion
cannot tell the fixed code from the broken code:

* **A CREATION WINDOW.** `write_text` creates the file at the umask default (0644 under the common
  022) and the `chmod` narrowed it only *after* the secret was already on disk. On first creation
  the credential was world-readable for that window.
* **NO ATOMICITY.** A crash or a full disk mid-write left the file TRUNCATED — every other key in
  it lost — because the target was rewritten in place.

So the tests below assert the two things that DO distinguish: the call site (an atomic write asked
for 0600 up front) and the behaviour under a failed write (the previous credentials survive).

`apps/app_secret.py::_write_0600` fixes the umask half with `os.open(..., 0o600)` + `fchmod`; it
does not need atomicity because it writes one value to its own file. A credential file holding
every key needs both, which is what `atomic_write(mode=0o600, fsync=True)` gives.
"""

from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. This test writes CREDENTIALS — it must never see the real one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def test_a_freshly_created_credential_file_is_0600(home, monkeypatch):
    """End-to-end, under a deliberately loose umask so a umask-inherited mode would show."""
    monkeypatch.setattr(os, "umask", lambda _mask: 0o022, raising=False)
    from personalclaw.config.loader import _dotenv_save_credential

    _dotenv_save_credential("OPENAI_API_KEY", "sk-secret-1")
    ep = home / ".env"
    assert ep.exists(), "the credential file was not written"
    assert (
        stat.S_IMODE(ep.stat().st_mode) == 0o600
    ), f"credential file is {oct(stat.S_IMODE(ep.stat().st_mode))}, not 0600"


def test_the_write_asks_for_0600_UP_FRONT_and_fsyncs(home, monkeypatch):
    """The call site — the only place the creation window is visible.

    A `write_text` + `chmod` pair ends at the same mode, so this is what separates "narrow from
    the first byte" from "narrow a moment later".
    """
    seen: dict[str, object] = {}
    import personalclaw.atomic_write as aw

    real = aw.atomic_write

    def spy(path, content, **kw):
        seen.update({"path": str(path), "mode": kw.get("mode"), "fsync": kw.get("fsync")})
        return real(path, content, **kw)

    monkeypatch.setattr(aw, "atomic_write", spy)
    from personalclaw.config.loader import _dotenv_save_credential

    _dotenv_save_credential("KEY_A", "v1")
    assert seen, "the credential write did not go through atomic_write at all"
    assert seen["mode"] == 0o600, f"atomic_write was asked for {seen['mode']!r}, not 0o600"
    assert seen["fsync"] is True, "a credential write that is not fsynced can vanish on a crash"


def test_a_failed_write_leaves_THE_PREVIOUS_credentials_intact(home, monkeypatch):
    """The truncation half. In-place rewriting loses every other key on a mid-write failure."""
    from personalclaw.config.loader import _dotenv_save_credential

    _dotenv_save_credential("KEEP_ME", "original")
    ep = home / ".env"
    before = ep.read_text(encoding="utf-8")
    assert "KEEP_ME=original" in before

    # Fail at the last possible moment — after the temp file is written, as a full disk or a
    # crash would. An in-place writer has already destroyed the target by this point.
    import personalclaw.atomic_write as aw

    monkeypatch.setattr(
        aw.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device"))
    )
    with pytest.raises(OSError):
        _dotenv_save_credential("NEW_KEY", "v2")

    after = ep.read_text(encoding="utf-8")
    assert after == before, f"a failed write damaged the credential file:\n{after!r}"
    assert "KEEP_ME=original" in after, "the pre-existing credential was lost"


def test_an_upsert_preserves_other_keys_and_comments(home):
    """The behaviour the function exists for, pinned so the rewrite cannot have changed it."""
    ep = home / ".env"
    ep.write_text("# a comment\nOTHER=untouched\nTARGET=old\n", encoding="utf-8")
    from personalclaw.config.loader import _dotenv_save_credential

    _dotenv_save_credential("TARGET", "new")
    text = ep.read_text(encoding="utf-8")
    assert "# a comment" in text and "OTHER=untouched" in text
    assert "TARGET=new" in text and "TARGET=old" not in text


def test_the_mode_argument_is_load_bearing(home, monkeypatch):
    """Vacuity: prove `atomic_write` really applies the mode it is given.

    Without this, the call-site assertion above could be satisfied by a helper that accepts `mode`
    and ignores it — which is the same class of defect as the one being fixed.
    """
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    from personalclaw.atomic_write import atomic_write

    target = home / "probe.txt"
    atomic_write(target, "x", mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    atomic_write(target, "x", mode=0o644)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644, "atomic_write ignores its mode argument"
