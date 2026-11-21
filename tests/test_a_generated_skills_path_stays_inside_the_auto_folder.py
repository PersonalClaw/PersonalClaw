"""A skill generated from an intent is written under the skills folder's ``auto/``, whatever the
intent's id says.

``generate_skill_from_intent`` names the skill after its intent (``_slugify_intent``), and the id
comes from the request's path. The slug keeps only lowercase letters, digits and hyphens, so a
traversal never reached the filesystem, but nothing at the path itself said so: the folder it
checks and writes is now resolved and held to ``auto/`` (``_auto_skill_path``), and refused if it
ever lands outside.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.dashboard.handlers.knowledge import _auto_skill_path, _slugify_intent
from personalclaw.skills.loader import AUTO_SKILL_NAMESPACE


@pytest.mark.parametrize(
    "intent_id",
    ["../../etc/passwd", "/etc/passwd", "a\x00b", "..", "auto/../../x", "C:\\Windows"],
    ids=["dot-dot", "absolute", "nul", "bare-dot-dot", "auto-then-out", "windows-path"],
)
def test_an_intent_id_of_any_shape_names_a_skill_under_auto(tmp_path: Path, intent_id: str):
    """🔴 Red before: there was no containment to call."""
    target = _auto_skill_path(tmp_path, _slugify_intent(intent_id, "a goal"))
    auto = (tmp_path / AUTO_SKILL_NAMESPACE).resolve()
    assert target.is_relative_to(auto) and target != auto
    assert target.parent == auto
    assert "\x00" not in str(target)


@pytest.mark.parametrize("slug", ["../escape", "/etc", "..", "a/../../b", ""])
def test_a_slug_that_would_land_outside_auto_is_refused(tmp_path: Path, slug: str):
    """The containment itself: a slug ``_slugify_intent`` would never make is still refused, so
    the folder's bound doesn't rest on the slug's character set alone."""
    with pytest.raises(ValueError, match="outside"):
        _auto_skill_path(tmp_path, slug)


def test_a_link_under_auto_that_leads_out_is_refused(tmp_path: Path):
    """A link already under ``auto/`` with the slug's name, pointing outside, is refused: the held
    folder is resolved before it is used. A link that stays under ``auto/`` is still a skill there.
    """
    auto = tmp_path / AUTO_SKILL_NAMESPACE
    auto.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (auto / "linked-out").symlink_to(tmp_path / "elsewhere")
    (auto / "kept").mkdir()
    (auto / "linked-in").symlink_to(auto / "kept")
    with pytest.raises(ValueError, match="outside"):
        _auto_skill_path(tmp_path, "linked-out")
    assert _auto_skill_path(tmp_path, "linked-in") == (auto / "kept").resolve()
