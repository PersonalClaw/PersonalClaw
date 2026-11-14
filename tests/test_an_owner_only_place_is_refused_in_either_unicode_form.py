"""An owner-only place is refused whichever Unicode form its path is written in.

A path can hold a letter written as one code point or as a base letter and a combining mark
(Unicode NFC and NFD), and a filesystem may keep or hand back either. The owner-only fence
(``owner_only``) compares each path an agent would change with each owner-only place in one form,
so a place is refused by either spelling, and an ordinary file beside it still writes.
"""

from __future__ import annotations

import contextlib
import unicodedata

import pytest
from names_matched_byte_for_byte import matched_byte_for_byte

from personalclaw import owner_only

pytestmark = pytest.mark.security

_FORMS = ["NFC", "NFD"]


def _in(form: str, text: str) -> str:
    return unicodedata.normalize(form, text)


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
@pytest.mark.parametrize(
    "entry", ["config.json", "mcp.json", "hooks/startup.py", "agents/helper.json", "grants/x.json"]
)
def test_personalclaws_own_places_are_refused_in_either_unicode_form(tmp_path, kept, asked, entry):
    home = tmp_path / _in(kept, "Cåfé") / ".personalclaw"
    home.mkdir(parents=True)

    assert owner_only.kind_of(_in(asked, f"{home}/{entry}"), home) == owner_only.HOME
    assert owner_only.kind_of(_in(asked, f"{home}/notes.txt"), home) == ""


@pytest.mark.parametrize("matched", ["as-is", "bytes"])
@pytest.mark.parametrize("named", _FORMS)
def test_a_link_in_an_owner_only_folder_named_in_its_other_form_is_refused(
    tmp_path, monkeypatch, named, matched
):
    """The home named in one form, its folder kept in the other: a filesystem that matches names
    byte for byte finds the folder, and the link inside it, only by the spelling it was made with
    (:mod:`names_matched_byte_for_byte`), so each place is looked for by both."""
    home = tmp_path / _in("NFD" if named == "NFC" else "NFC", "Zoë") / ".personalclaw"
    home.mkdir(parents=True)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "config.json").write_text("{}\n")
    (home / "config.json").symlink_to(dotfiles / "config.json")
    named_home = _in(named, str(home))
    with (
        matched_byte_for_byte(monkeypatch, tmp_path)
        if matched == "bytes"
        else contextlib.nullcontext()
    ):
        assert owner_only.kind_of(str(dotfiles / "config.json"), named_home) == owner_only.HOME
        assert owner_only.kind_of(str(dotfiles / "notes.txt"), named_home) == ""


#: A letter whose two spellings fold apart when case is folded before the letter is decomposed:
#: alpha with an iota subscript, then a diaeresis.
_SUBSCRIPT = "\u1fb3\u0308"


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_an_owner_only_place_is_refused_whichever_way_a_subscripted_letter_is_spelled(
    tmp_path, kept, asked
):
    home = tmp_path / _in(kept, f"x{_SUBSCRIPT}y") / ".personalclaw"
    home.mkdir(parents=True)

    assert owner_only.kind_of(_in(asked, f"{home}/mcp.json"), home) == owner_only.HOME
    assert owner_only.kind_of(_in(asked, f"{home}/notes.txt"), home) == ""


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (".gitconfig", owner_only.GIT),
        (".zshrc", owner_only.SHELL),
        ("Library/LaunchAgents/com.example.helper.plist", owner_only.LOGIN),
    ],
)
def test_the_owners_own_files_are_refused_in_either_unicode_form(
    tmp_path, monkeypatch, kept, asked, entry, kind
):
    user = tmp_path / _in(kept, "Zoë")
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "ZDOTDIR"):
        monkeypatch.delenv(variable, raising=False)
    home = tmp_path / "personalclaw"

    assert owner_only.kind_of(_in(asked, f"{user}/{entry}"), home) == kind
    assert owner_only.kind_of(_in(asked, f"{user}/notes.txt"), home) == ""


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_a_command_that_writes_an_owner_only_place_is_named_in_either_unicode_form(
    tmp_path, kept, asked
):
    home = tmp_path / _in(kept, "Cåfé") / ".personalclaw"
    home.mkdir(parents=True)
    spelled = _in(asked, str(home))

    named = owner_only.named_in(f"echo '{{}}' > {spelled}/mcp.json", cwd=str(tmp_path), home=home)
    assert named is not None and named.kind == owner_only.HOME, named
    assert (
        owner_only.named_in(f"echo x > {spelled}/notes.txt", cwd=str(tmp_path), home=home) is None
    )


@pytest.mark.parametrize("asked", _FORMS)
def test_a_repositorys_git_settings_are_refused_in_either_unicode_form(tmp_path, asked):
    """Git's own names are compared as they are, ASCII, whatever the folder above them holds."""
    repo = tmp_path / "Résumé"
    for entry in (".git/config", ".git/hooks/pre-commit", ".gitmodules"):
        assert owner_only.kind_of(_in(asked, f"{repo}/{entry}"), tmp_path) == owner_only.GIT, entry
    assert owner_only.kind_of(_in(asked, f"{repo}/README.md"), tmp_path) == ""
