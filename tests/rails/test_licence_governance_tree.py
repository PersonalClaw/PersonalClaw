"""The source-tree checks split from
``tests/test_licence_governance.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from licence_governance_shared import _minimal_tree, check_no_cla_file


@pytest.mark.parametrize(
    "name",
    [
        "CLA.md",
        "cla.txt",
        "CONTRIBUTOR_LICENSE_AGREEMENT.md",
        "contributor-agreement.md",
        "ICLA.pdf",
    ],
)
def test_the_rail_reds_when_a_cla_file_appears(tmp_path: Path, name: str) -> None:
    """Clause 3, disjunct 2 — every spelling, including one nested out of sight."""
    root = _minimal_tree(tmp_path)
    nested = root / ".github"
    nested.mkdir()
    (nested / name).write_text("sign here\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="contributor licence agreement has appeared"):
        check_no_cla_file(root)


def test_the_rail_reds_on_a_test_phone_home_host(tmp_path: Path, monkeypatch) -> None:
    """Clause 3, disjunct 3 — a test phone-home host, driven through the OWNING rail.

    ``test_network_egress_hosts.py`` owns the egress third of the same promise and its
    negative case is already exercised there at the scanner level. What was missing was
    running the *assertion* red, so this points that module's core root at a tmp tree
    carrying an analytics endpoint and watches its rail fail. Nothing in the real tree is
    touched, and the egress rail is not re-implemented here.
    """
    import test_network_egress_hosts_tree as egress

    fake_core = tmp_path / "core"
    fake_core.mkdir()
    fake_web = tmp_path / "web"
    fake_web.mkdir()

    # Control arm first: a core with no hosts at all leaves the rail green, so the red below
    # is caused by the injected host and not by the monkeypatch itself. `_ROOT` moves with
    # the scan roots because the rail reports findings as paths relative to it.
    (fake_core / "clean.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(egress, "_CORE", fake_core)
    monkeypatch.setattr(egress, "_WEB", fake_web)
    monkeypatch.setattr(egress, "_ROOT", tmp_path)
    # The fake core ships no DATA records (no bundled-chat sign-off, no source recipes), so the
    # rail's record half reads none. That half, and its own "the sign-off must parse" floor, are
    # exercised against the real tree by `test_network_egress_hosts` itself; reading the real
    # records here would judge a tree this test did not build.
    monkeypatch.setattr(egress, "_record_fetches", dict)
    egress.test_every_egress_host_is_a_declared_destination()

    (fake_core / "phone_home.py").write_text(
        "import requests\n"
        "requests.post('https://telemetry.personalclaw-metrics.net/v1/events', json=payload)\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="telemetry.personalclaw-metrics.net"):
        egress.test_every_egress_host_is_a_declared_destination()
