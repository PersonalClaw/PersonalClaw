"""The credential guard protects a credential directory that is a symlink, and what it links to.

🔴 ``is_sensitive_path`` resolved the requested path but built each protected location as
``<home>/<dir>`` without resolving it. Dotfile managers (GNU Stow, rcm, a hand-made
``ln -s``) make ``~/.aws`` and ``~/.ssh`` symlinks into a dotfiles checkout, and then every
file under them resolved to the checkout and matched nothing: ``~/.aws/credentials`` and
``~/.ssh/id_ed25519`` read back as not sensitive.

Every fixture is a real directory tree under ``tmp_path`` with ``HOME`` pointed at it, so
each case is the filesystem shape a user has, not a mocked resolver.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw.security import is_sensitive_path


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _file(path: Path, text: str = "secret") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _dotfiles(home: Path) -> Path:
    return home / "dotfiles"


# ── a protected directory that is a symlink ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "linked, secret",
    [(".aws", "credentials"), (".ssh", "id_ed25519"), (".gnupg", "private-keys-v1.d/key.key")],
)
def test_a_file_under_a_symlinked_credential_directory_is_sensitive(home, linked, secret):
    target = _dotfiles(home) / linked.lstrip(".")
    _file(target / secret)
    (home / linked).symlink_to(target, target_is_directory=True)

    assert is_sensitive_path(f"~/{linked}/{secret}"), f"~/{linked} is a symlink, and it is open"
    assert is_sensitive_path(str(home / linked / secret))


def test_the_directory_a_credential_link_points_at_is_sensitive(home):
    """The same bytes under the link target's own name."""
    target = _file(_dotfiles(home) / "aws" / "credentials").parent
    (home / ".aws").symlink_to(target, target_is_directory=True)

    assert is_sensitive_path(str(target / "credentials"))
    assert is_sensitive_path(str(target))


def test_a_credential_file_that_is_a_symlink_is_sensitive_by_its_own_name(home):
    """Stow links files, not only directories: ``~/.ssh`` is real and ``id_ed25519`` in it
    links to the dotfiles copy."""
    key = _file(_dotfiles(home) / "ssh" / "id_ed25519")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").symlink_to(key)

    assert is_sensitive_path("~/.ssh/id_ed25519")


def test_a_linked_key_is_refused_to_a_caller_that_resolved_it_first(home):
    """Most callers realpath before they ask, so the key reaches the guard as the dotfiles
    path. ``hooks.validate_file_path`` is the dashboard file reader's gate."""
    from personalclaw.hooks import validate_file_path

    key = _file(_dotfiles(home) / "ssh" / "id_ed25519")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").symlink_to(key)

    assert is_sensitive_path(os.path.realpath(home / ".ssh" / "id_ed25519"))
    assert validate_file_path(str(home / ".ssh" / "id_ed25519")) is None
    assert validate_file_path(str(key)) is None
    assert validate_file_path(str(_file(_dotfiles(home) / "ssh" / "README.md"))) is not None


def test_a_link_added_or_removed_later_is_seen_by_the_next_check(home):
    key = _file(_dotfiles(home) / "ssh" / "id_ed25519")
    (home / ".ssh").mkdir()
    assert not is_sensitive_path(str(key))  # nothing links to it yet

    (home / ".ssh" / "id_ed25519").symlink_to(key)
    assert is_sensitive_path(str(key))

    (home / ".ssh" / "id_ed25519").unlink()
    assert not is_sensitive_path(str(key))


def test_a_dangling_credential_link_is_still_protected(home):
    """A link whose target is not mounted yet (an encrypted volume, a network home)."""
    (home / ".aws").symlink_to(home / "not-mounted" / "aws", target_is_directory=True)

    assert is_sensitive_path("~/.aws/credentials")


def test_a_home_that_is_itself_a_symlink_protects_both_spellings(tmp_path, monkeypatch):
    real = tmp_path / "volumes" / "ada"
    _file(real / ".ssh" / "id_rsa")
    link = tmp_path / "home-link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(link))

    assert is_sensitive_path(str(link / ".ssh" / "id_rsa"))
    assert is_sensitive_path(str(real / ".ssh" / "id_rsa"))


def test_a_link_from_elsewhere_into_a_credential_directory_is_sensitive(home, tmp_path):
    """Already true before the fix (the request side was resolved). Kept so the other half of
    'both sides' stays true."""
    key = _file(home / ".ssh" / "id_rsa")
    innocent = tmp_path / "notes.txt"
    innocent.symlink_to(key)

    assert is_sensitive_path(str(innocent))


# ── what stays readable ───────────────────────────────────────────────────────────────────────


def test_the_rest_of_the_dotfiles_checkout_stays_readable(home):
    target = _file(_dotfiles(home) / "aws" / "credentials").parent
    (home / ".aws").symlink_to(target, target_is_directory=True)
    readme = _file(_dotfiles(home) / "README.md", "my dotfiles")
    zshrc = _file(_dotfiles(home) / "zsh" / ".zshrc", "export PATH")

    assert not is_sensitive_path(str(readme))
    assert not is_sensitive_path(str(zshrc))
    assert not is_sensitive_path(str(_dotfiles(home) / "aws-notes.md"))  # a sibling prefix


def test_an_ordinary_file_is_not_sensitive(home):
    notes = _file(home / "notes" / "todo.md", "buy milk")

    assert not is_sensitive_path(str(notes))
    assert not is_sensitive_path("~/notes/todo.md")
    assert os.path.exists(notes)
