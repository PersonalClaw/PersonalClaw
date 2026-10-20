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

The census table lives in ``docs/``, so every test that reads it is the rail
``tests/rails/test_network_egress_hosts_tree.py``, run once per CI run in the rails job. This
module keeps the one test that reads no tree: the scan's teeth, on strings it writes.

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

from network_egress_hosts_shared import hosts_in


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
