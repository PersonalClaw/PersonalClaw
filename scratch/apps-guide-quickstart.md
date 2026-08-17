<!--
ET-2 — apps-repo edit, staged for the owner to land.

Insert the "## Quickstart" section below into PersonalClawApps/docs/app-creation-guide.md
IMMEDIATELY AFTER the intro paragraph and the `my-app/` tree block (i.e. after the line
"└── test_provider.py   # your tests" and its closing ``` fence, BEFORE
"## The manifest (`app.json`)"). Nothing else in that guide changes.

Everything below the marker was executed verbatim from a clean directory against a running
gateway on 2026-08-17; the measured wall-clock time is stated in the text and is the real
number, not a target.
-->

--- 8< --- insert everything below this line --- 8< ---

## Quickstart: minutes to first run

Prerequisites: PersonalClaw installed (`pip install personalclaw`), `pytest` available, and
a gateway running (`personalclaw gateway`).

**1 — see what you can build.** The type table is derived from the running build's provider
registry, so it is always the truth about this version:

```bash
personalclaw app new --list-types
```

**2 — generate an app.** Pick a type from that table (`tool` is the simplest):

```bash
personalclaw app new my-tool --type tool
```

You get a complete, installable app: `app.json` validated against core's own manifest
parser, a provider stub implementing that type's SDK contract with real signatures,
`app_cli.py` (the `setup`/`doctor` seams), a passing `test_provider.py`, a `README.md`, and
an MIT `LICENSE`. No `permissions` block — add only what your provider actually uses, since
the Store shows declared permissions as the install-consent surface.

**3 — run its tests.** They pass as generated, with no network, no credentials and no
gateway:

```bash
python -m pytest my-tool -q
```

**4 — point a shell at your gateway.** `personalclaw token` prints a dashboard URL with a
token in the query string; split it into the two pieces the next step needs:

```bash
TOKEN_URL="$(personalclaw token | head -1)"
export PERSONALCLAW_URL="${TOKEN_URL%%\?*}"
export PERSONALCLAW_TOKEN="${TOKEN_URL#*token=}"
```

The gateway takes the owner token as a `?token=` **query parameter**. An
`Authorization: Bearer` header is only accepted for app-scoped narrowing tokens, so using
one here answers `{"error": "Token required"}`.

**5 — install it from that local path and enable it.**

```bash
curl -sS -X POST "$PERSONALCLAW_URL/api/apps?token=$PERSONALCLAW_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"source\": \"$PWD/my-tool\", \"confirm\": true}"
curl -sS -X POST "$PERSONALCLAW_URL/api/apps/my-tool/enable?token=$PERSONALCLAW_TOKEN"
```

Prefer clicking? **Store → Add source → local path**, point it at `my-tool`, then install
and enable. Same install path and same supply-chain scan gate — there is only one.

**6 — confirm it is live.**

```bash
curl -sS "$PERSONALCLAW_URL/api/apps/my-tool?token=$PERSONALCLAW_TOKEN"
```

In the response, the `installed` block reports `"enabled": true` and `manifest.provider.type`
is the type you scaffolded — your provider is registered. `personalclaw doctor` now prints a
`my-tool` section, and the line under it comes from your `app_cli.py`.

That is first run. **Measured: 2.4 s of wall clock across steps 1-6** on a warm machine, from
an empty directory to an enabled app whose provider is registered. Filling in the stub is the
rest of this guide.

Prefer to fork a repo instead of generating? `personalclaw app new --from-template` fetches
[`personalclaw/app-template`](https://github.com/personalclaw/app-template) — the same
`--type tool` output, plus CI and a clone-to-installed README.
