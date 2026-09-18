"""The Vite dev-server port is documented in two places; this keeps both equal to the config.

Issue #131: the Makefile's ``serve-web`` help block and CONTRIBUTING's dev-server table both
told a newcomer the Vite server listens on ``:3000``. It listens on ``:3100`` — ``server.port``
in ``web/vite.config.ts``, chosen deliberately so it does not collide with the other dev
servers that squat 3000. Following the docs landed a first-time frontend contributor on a dead
port with no hint why, which is the most expensive kind of doc bug: it fires on step one.

The port is a config value restated in prose, so it will drift again unless something reads
the config. This rail does: it parses ``server.port`` out of ``web/vite.config.ts`` and asserts
each doc quotes THAT number, not a copy of it. Fixing the docs by hand and stopping there would
have left the next port change silently wrong.

⚠️  When this reds, the fix is the DOC (or, if the port genuinely moved, both docs) — never
    loosen the anchors below to stop matching. The anchors are also the vacuity floor: each
    ``assert ... is not None`` proves the rail found the line it claims to be checking, so a
    renamed target or a reorganized table fails loudly instead of passing against nothing.

🪤  THE STALE-PORT CHECK IS ANCHORED ON ``:3000``, NOT ON ``3000``, and that is deliberate.
    The Makefile's help block says "deliberately not 3000" — the wrong value appears there on
    purpose, as the thing a reader is being warned off. A bare-numeral check would red on the
    warning itself, so tightening it to ``"3000" not in text`` breaks a correct doc. What makes
    a doc wrong is quoting 3000 as *the port* (``:3000``), which is the form checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: ``server: { port: NNNN, ... }`` in the Vite config — the authoritative value.
_VITE_PORT_RE = re.compile(r"^\s*port:\s*(\d+)\s*,?\s*$", re.M)

#: The Makefile's ``## serve-web:`` help block: from its own ``## serve-web`` line to the
#: first line that is not a ``##`` comment. Bounding by what ENDS the block (rather than a
#: character window) is what keeps this from spilling into the next target's help.
_MAKE_BLOCK_RE = re.compile(r"^## serve-web:.*?(?=^(?!##))", re.M | re.S)

#: The CONTRIBUTING dev-server table row for the same target.
_CONTRIB_ROW_RE = re.compile(r"^\|\s*`make serve-web`\s*\|.*$", re.M)


def _vite_port() -> str:
    config = (REPO_ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    m = _VITE_PORT_RE.search(config)
    assert m is not None, "no `port:` found in web/vite.config.ts — has server.port moved?"
    return m.group(1)


def test_vite_port_is_the_one_we_think_it_is() -> None:
    """Guard the rail itself: 3000 would make every assertion below vacuously true."""
    port = _vite_port()
    assert port != "3000", (
        "web/vite.config.ts now says 3000, the very value #131 was about. If the port really "
        "moved, update both docs and delete this guard deliberately."
    )


def test_makefile_serve_web_help_quotes_the_configured_port() -> None:
    port = _vite_port()
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    block = _MAKE_BLOCK_RE.search(makefile)
    assert block is not None, "no `## serve-web:` help block in the Makefile"
    text = block.group(0)
    assert f":{port}" in text, f"Makefile serve-web help does not document :{port}\n{text}"
    assert ":3000" not in text, f"Makefile serve-web help still says :3000\n{text}"


def test_contributing_serve_web_row_quotes_the_configured_port() -> None:
    port = _vite_port()
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    row = _CONTRIB_ROW_RE.search(contributing)
    assert row is not None, "no `make serve-web` row in CONTRIBUTING.md's target table"
    text = row.group(0)
    assert f":{port}" in text, f"CONTRIBUTING serve-web row does not document :{port}\n{text}"
    assert ":3000" not in text, f"CONTRIBUTING serve-web row still says :3000\n{text}"
