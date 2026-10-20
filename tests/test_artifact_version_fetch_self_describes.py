"""A version fetch self-describes as the version it carries (#504).

``GET /api/artifacts/{slug}/versions/{n}`` returned the correct historical CONTENT for
every ``n``, but the payload's ``version`` field always reported the HEAD version:
``native.get()`` read the head metadata, swapped only ``art.content``, and returned —
so v1 and v5 both said ``version: 5``. Measured on a 5-version artifact, every response
self-described as 5. A caller that labels a version from its own payload (the natural
thing to do) mislabels every historical fetch, and the response is self-inconsistent:
``version: 5`` sitting next to v1's bytes.

Both branches of ``get()`` had the shape — text (``_version_content``) and binary
(``_raw_ref(slug, version)``). The fix sets ``art.version = version`` alongside each
content swap. The head fetch (no ``version=``) is asserted unchanged in the same tests
so the fix cannot overshoot.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A real native artifact provider rooted in tmp_path.

    Same seam as test_artifacts_iterate_and_refs: replace the module-level registry
    cache entry itself (monkeypatch.setitem restores it) and pass the root explicitly,
    since NativeArtifactProvider resolves config_dir() eagerly in __init__.
    """
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    prov = NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setitem(registry._providers, "native", prov)
    return prov


# ── text branch ───────────────────────────────────────────────────────────────


def test_a_text_version_fetch_reports_its_own_version(provider):
    art = provider.create(name="Report", content="v1 body", kind="html")
    provider.update(art.slug, content="v2 body", snapshot=True)

    v1 = provider.get(art.slug, version=1)
    v2 = provider.get(art.slug, version=2)

    # The payload's self-description matches the bytes it carries.
    assert (v1.version, v1.content) == (1, "v1 body")
    assert (v2.version, v2.content) == (2, "v2 body")


def test_the_head_fetch_still_reports_the_head_version(provider):
    art = provider.create(name="Report", content="v1 body", kind="html")
    provider.update(art.slug, content="v2 body", snapshot=True)

    head = provider.get(art.slug)
    assert head.version == 2
    assert head.content == "v2 body"


def test_a_missing_version_is_still_none_not_a_mislabeled_head(provider):
    art = provider.create(name="Report", content="v1 body", kind="html")
    assert provider.get(art.slug, version=99) is None


# ── binary branch ─────────────────────────────────────────────────────────────


def test_a_binary_version_fetch_reports_its_own_version(provider):
    art = provider.create_binary(name="Chart", data=b"\x89PNGv1", mime="image/png")
    provider.update_binary(art.slug, data=b"\x89PNGv2", mime="image/png")

    v1 = provider.get(art.slug, version=1)
    head = provider.get(art.slug)

    assert v1.version == 1
    # The content ref points at the pinned version, consistent with the label.
    assert v1.content.endswith("/raw?version=1")
    assert head.version == 2
