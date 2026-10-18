"""The source-tree checks split from
``tests/test_approval_read_only_supply.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from rails_repo import REPO

WEB = REPO / "web" / "src"


def _ts_files() -> list[Path]:
    """Every ``.ts``/``.tsx`` file under ``web/src``, from the suite's one source index."""
    return [p for p in source_index.web_files() if p.suffix.startswith(".ts")]


class TestBothPermissionSurfacesAgree:
    """Every surface describes a call from the radius the backend composed for it.

    Source-level assertions, because the surfaces are separate React trees and the drift #2821
    named is precisely "one of them forgot". A surface that derived its own facets from a tool's
    name would describe a shell call by "bash" rather than by what its command does.
    """

    #: The surfaces that read an approval's radius off the wire. A registry row reaches every
    #: queue card (the run page, Mission Control, To triage, the Inbox, a loop's page) through
    #: the one segment adapter, `approvalSegment.ts`.
    READERS = [
        "app/useApprovalToasts.ts",
        "pages/chat/approvalSegment.ts",
        "pages/chat/chatTypes.ts",
        "pages/chat/liveToolFrames.ts",
        "pages/companion/CompanionPage.tsx",
    ]

    def test_every_reader_decodes_the_radius_through_the_one_decoder(self) -> None:
        """Enumerated from the source rather than trusted: a new reader added without the
        decoder reds here instead of shipping a surface that casts the wire."""
        readers = sorted(
            p.relative_to(WEB).as_posix()
            for p in _ts_files()
            if not p.name.endswith((".test.ts", ".test.tsx"))
            and "blast_radius" in source_index.text(p)
        )
        # api.ts declares the field and approvalMeta.ts documents it; the rest READ it.
        assert [r for r in readers if r not in ("lib/api.ts", "pages/chat/approvalMeta.ts")] == (
            self.READERS
        ), readers
        for rel in self.READERS:
            text = source_index.text(WEB / rel)
            assert "blastRadiusOf(" in text, f"{rel} reads blast_radius without the one decoder"

    def test_no_surface_derives_a_radius_of_its_own(self) -> None:
        for p in _ts_files():
            if p.name.endswith((".test.ts", ".test.tsx")):
                continue
            text = source_index.text(p)
            assert "deriveBlastRadius" not in text, p
            assert "readOnlyOf" not in text, p

    def test_the_source_census_is_not_vacuous(self) -> None:
        """Both scans above are greps over a tree; prove the tree was actually read."""
        assert (WEB / "pages" / "chat" / "approvalMeta.ts").exists()
        assert len(_ts_files()) > 100
        assert "export function blastRadiusOf" in source_index.text(
            WEB / "pages" / "chat" / "approvalMeta.ts"
        )
