"""The source-tree checks split from
``tests/test_channel_delivery_per_provider.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import channel_delivery_per_provider_shared
import source_index
from rails_repo import REPO

_clean_registry = channel_delivery_per_provider_shared._clean_registry


_CORE = REPO / "src" / "personalclaw"


def _code(path: Path) -> str:
    """Source with comment-only lines stripped — the fix's own comments quote the shape they
    replaced, and a raw-text rail would read that explanation as the defect."""
    return "\n".join(
        line for line in source_index.text(path).split("\n") if not line.strip().startswith("#")
    )


class TestNoSecondSlotComesBack:
    def test_the_mirror_path_resolves_the_ORIGIN_handle(self) -> None:
        """`chat_runner`'s three mirror sites (user echo, tool stream, response) must all use the
        origin-scoped handle. Reading the owner-reachable attribute there is the bug. The handle is
        resolved where the turn's thread is chosen (`_the_thread_told`), from the channel the
        chat's link names, and the mirror's handle is the one that returns."""
        code = _code(_CORE / "dashboard" / "chat_runner.py")
        told = code[code.index("async def _the_thread_told(") :]
        # Its own body only: up to the next function, an async one too, so lines moved into a
        # helper after it are not read as its own.
        after = re.search(r"\n(?:async )?def ", told[1:])
        told = told[: after.start() + 1] if after else told
        assert "provider = state.channel_provider_for(session_key)" in told
        assert "delivery = state.delivery_for(provider)" in told
        assert (
            "_mirror_delivery, _mirror_thread, _mirror_chan, _mirror_note = await _the_thread_told("
            in code
        )
        assert "state.channel_delivery" not in code, (
            "a mirror site went back to the owner-reachable handle — a reply carries the origin "
            "channel's id and is answerable by exactly one provider"
        )

    def test_no_module_assigns_the_attribute_as_a_slot(self) -> None:
        """The setter makes an assignment a registration, so an assignment is not itself wrong —
        but core assigning it would mean core is choosing a provider, which only a transport can
        do. Zero core writers was already true before this change; the rail keeps it true."""
        offenders = [
            p.relative_to(_CORE).as_posix()
            for p in source_index.package_files()
            if "channel_delivery =" in _code(p) and p.name != "state.py"
        ]
        assert offenders == [], f"core assigned the channel-delivery attribute: {offenders}"

    def test_the_registry_is_the_only_holder(self) -> None:
        """A private slot anywhere else is the shape that drifted. `channel_delivery.py` owns the
        dict; nobody else may keep one."""
        offenders = [
            p.relative_to(_CORE).as_posix()
            for p in source_index.package_files()
            if "_channel_delivery: " in _code(p) and p.name != "channel_delivery.py"
        ]
        assert offenders == [], f"a second delivery slot reappeared in {offenders}"

    def test_the_census_is_not_vacuous(self) -> None:
        """🪤 Floor for the two rails above: if the scan read nothing, both pass on empty lists."""
        files = list(source_index.package_files())
        assert len(files) > 100
        assert "_REGISTRY" in _code(_CORE / "channel_delivery.py")
        assert "def delivery_for" in _code(_CORE / "channel_delivery.py")
