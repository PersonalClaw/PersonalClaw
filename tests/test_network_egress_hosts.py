"""Anti-regrowth rail: nowhere new for the product to phone home.

`SECURITY.md` and the website both state that PersonalClaw sends no telemetry. It is
true — and nothing enforced it. An analytics endpoint, a crash reporter, or a "check for
news" ping could be added in any PR and no test, lint or review checklist would notice: the
privacy claim was documentation, not a control. That is the same defect shape as a config
field nothing reads, except the thing nobody reads here is a promise to the user.

So this is a census of DESTINATIONS. Every routable hostname appearing as a literal in
shipped code — core `src/personalclaw/**/*.py` and the SPA `web/src/**/*.{ts,tsx}` — must be
listed in `docs/architecture/network-egress-hosts.txt` with a judgment saying whether it is
fetched and by whose action. A new host reds CI; a listed host that has disappeared reds it
too, so the table stays an exact mirror rather than accumulating.

**What this cannot answer, stated so nobody reads more into a green run.** It is a census of
where, not of what: it cannot tell you what is sent to an approved host, and a new module
reaching an already-listed host does not fail. Static scanning can answer "is there anywhere
new it could go", which is the question a phone-home is; it cannot answer "what left".

Reserved and non-routable names are skipped BY RULE, not listed, because a name that cannot
resolve cannot be a destination — RFC 2606/6761 names, loopback and RFC 1918 addresses, and
`{...}` template placeholders. Skipping by rule rather than by allowlist entry is what keeps
the table short enough to actually read.

Shaped after `test_provider_boundary_residue.py`: patterns + a judgment table + a
stale-entry check + a teeth check.

**The section an entry sits under is a claim, and it is cross-checked** against what the code
can fetch. `huggingface.co` was filed under "Never fetched" (as a citation link in the voice
bake-off) while the bundled-model download fetched it and `local_models/hf_token.py`'s token
check did too. Every test above stayed green, because a host that appears SOMEWHERE in the
table satisfies the census whatever the table says about it. So the fetch sites are derived
two ways — the URL records a downloader reads (through the same parsers it uses) and the live
URL literals of every module that can open a network connection — and a fetched host must be
filed as fetched.
"""

from __future__ import annotations

from network_egress_hosts_shared import _SECTIONS, _classes, _record_fetches, _table, hosts_in

_FETCHED = frozenset({"unprompted", "user"})


def test_every_declared_host_carries_a_judgment():
    """A bare hostname is a list, not a decision.

    The judgment is the whole value of the table: "fetched, unprompted, on a schedule" and
    "an XML namespace that is never retrieved" are both single lines here and could not be
    more different, and no scanner can tell them apart.
    """
    thin = sorted(h for h, j in _table().items() if len(j) < 30)
    assert not thin, (
        "these entries have no real judgment — say whether the host is fetched and by "
        f"whose action: {thin}"
    )


def test_the_sweep_has_teeth(tmp_path):
    """The anti-regrowth proof, in both directions.

    Without this the scan could return nothing for any input and every test above would be
    green for a product that had just added an analytics endpoint.
    """
    sneaky = "requests.post('https://telemetry.example-vendor.net/v1/events', json=payload)\n"
    assert hosts_in(sneaky) == {
        "telemetry.example-vendor.net"
    }, "the scan missed an injected analytics endpoint — it would not catch a real one"
    assert (
        hosts_in("# see https://some-doc-host.net/guide for details\n") == set()
    ), "a single-line comment was treated as egress"
    assert (
        hosts_in("url = 'https://api.example.com/v1'\n") == set()
    ), "an RFC 2606 reserved name was treated as a destination"
    assert (
        hosts_in("url = f'https://{host}/api'\n") == set()
    ), "a template placeholder was treated as a destination"
    assert (
        hosts_in('placeholder="e.g. https://nas.local"\n') == set()
    ), "an mDNS `.local` name was treated as a vendor destination"


def test_every_entry_sits_under_a_known_section():
    """An entry before the first header, or under a header nobody classifies, claims nothing."""
    known = {cls for _prefix, cls in _SECTIONS}
    loose = sorted(
        f"{h} ({c or 'before any header'})" for h, c in _classes().items() if c not in known
    )
    assert not loose, f"entries outside the four sections: {loose}"


def test_a_host_a_shipped_record_fetches_is_filed_as_fetched():
    """A URL in shipped DATA is fetched by the code that reads it — never "never fetched".

    The bundled default model downloads from the sign-off record's `source_url`; a watched
    source created from a recipe polls the recipe's `spec` URL. Neither literal is in a `.py`
    file, so only reading the records says where those requests go.
    """
    classes = _classes()
    misfiled = sorted(
        f"{host} ({where}) is filed as {classes.get(host) or 'unlisted'!r}"
        for host, where in _record_fetches().items()
        if classes.get(host) not in _FETCHED
    )
    assert not misfiled, (
        "a shipped record fetches these hosts, but the census does not say they are fetched:\n"
        + "\n".join(f"  {m}" for m in misfiled)
    )


def test_the_release_check_is_the_only_unprompted_destination():
    """The one host the product contacts without the user asking for that thing.

    Pinned deliberately: this is the sentence the privacy posture rests on, and it should
    take a failing test to change it. ``api.github.com`` remains the sole unprompted
    destination — but it is now suppressible: ``updates.check_enabled=false`` is the egress
    kill switch, and with it set the check makes ZERO calls
    (``test_self_update.py::test_the_check_makes_zero_calls_when_checking_is_disabled`` +
    ``test_do_update_check_kill_switch_runs_no_subprocess``). The schedule is real; the
    opt-out is now real too, and defaults ON so this rail's "unprompted" claim still holds.
    """
    unprompted = sorted(h for h, j in _table().items() if "FETCHED, unprompted" in j)
    assert unprompted == ["api.github.com"], (
        "the set of unprompted destinations changed. Adding one is a product decision about "
        f"the privacy claim, not an implementation detail: {unprompted}"
    )
