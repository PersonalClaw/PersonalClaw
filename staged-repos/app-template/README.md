# PersonalClaw app template

A complete, installable PersonalClaw **tool** app. It is not a sketch — it passes its own
tests, passes CI, and installs into the Store as-is. Start it, see it run, *then* make it
yours.

Every file here is what `personalclaw app new app-template --type tool` emits, plus this
README and the CI workflow. If you would rather generate a named app directly:

```bash
personalclaw app new my-tool --type tool
```

## Minutes to first run

Five steps. Copy-paste each one.

**1 — get the template.**

```bash
personalclaw app new --from-template
cd app-template
```

(Or `git clone https://github.com/personalclaw/app-template && cd app-template`.)

**2 — run its tests.** They need no network, no credentials and no gateway.

```bash
python -m pytest . -q
```

**3 — point a shell at your gateway.** `personalclaw token` prints a dashboard URL with a
token in the query string; these three lines split that URL into the pieces the next step
needs. The gateway takes the owner token as a `?token=` **query parameter** — an
`Authorization: Bearer` header is only accepted for app-scoped narrowing tokens, so it
will answer `{"error": "Token required"}` here.

```bash
TOKEN_URL="$(personalclaw token | head -1)"
export PERSONALCLAW_URL="${TOKEN_URL%%\?*}"
export PERSONALCLAW_TOKEN="${TOKEN_URL#*token=}"
```

**4 — install it from this directory and enable it.**

```bash
curl -sS -X POST "$PERSONALCLAW_URL/api/apps?token=$PERSONALCLAW_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"source\": \"$PWD\", \"confirm\": true}"
curl -sS -X POST "$PERSONALCLAW_URL/api/apps/app-template/enable?token=$PERSONALCLAW_TOKEN"
```

Prefer clicking? **Store → Add source → local path**, point it at this directory, then
install and enable. Same install path, same scan gate — there is only one.

**5 — see it.** The app is in the Store, enabled, and its provider is registered:

```bash
curl -sS "$PERSONALCLAW_URL/api/apps/app-template?token=$PERSONALCLAW_TOKEN"
```

In the response, the `installed` block reports `"enabled": true` and `manifest.provider.type`
is `tool`. `personalclaw doctor` now prints an `app-template` section, and the line under it
comes from `app_cli.py`.

## Make it yours

Renaming is four edits, all mechanical. The app's identity is its manifest `name`; every
per-type registry keys the provider by the `name` property, so the two must agree.

1. `app.json` — `name`, `displayName`, `description`, `loggerRoots` (underscored form of
   the name), and `author`.
2. `provider.py` — the class name, the `logging.getLogger(...)` root, and what the `name`
   and `display_name` properties return.
3. `test_provider.py` — the imported class name and the two expected strings.
4. `LICENSE` — the copyright holder.

Then re-run step 2. Renaming the directory is optional: the platform reads the manifest,
not the folder.

## What to fill in

`provider.py` implements `ToolProvider` from `personalclaw.sdk.tool`:

- `list_tools` — returns `[]`. Return your tool descriptors here.
- `invoke` — raises `NotImplementedError`. Run the named tool and return its result.

Import core **only** through `personalclaw.sdk.*`. A deep core import (`personalclaw.agent`,
`personalclaw.config`, …) fails the boundary check in CI and breaks on the next release.

## Declare only what you need

`app.json` declares no `permissions` block. Add one only for what the provider actually
uses — the Store shows declared permissions as the install-consent surface, so an
over-broad declaration costs you installs.

## CI

`.github/workflows/ci.yml` runs the same checks the PersonalClaw apps repo runs, adapted
to a single app at the repo root: the manifest parses through core's own parser and
round-trips, the tests pass, app code imports core only via `personalclaw.sdk.*`, and every
commit in a PR carries a DCO `Signed-off-by` trailer (`git commit -s`).

## License

MIT — see `LICENSE`. Change the holder to your name.
