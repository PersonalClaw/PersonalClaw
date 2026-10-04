# API overview

The gateway serves a REST + WebSocket API on the dashboard port (default `10000`).
Everything an agent or a script calls lives under `/api/*`.

This page is the **orientation**: what every route has in common, and the handful of
behaviours that are easy to get wrong. It is not the route list.

> **The route list is [the HTTP route reference](api-routes.md)** — every registration,
> generated from the gateway's own route table and byte-compared against a fresh render
> in CI, so its count is measured rather than asserted. Nothing on this page enumerates
> routes, because a hand-kept second list is how a reference starts lying: the table this
> page used to carry claimed to list every route and covered a little over a third of
> them, with no way for a reader to tell which third.

## Base URL and auth

Every request goes to the dashboard origin — `http://127.0.0.1:10000` by default.

Authentication depends on the configured auth mode:

- **`token`** (the default) — send the owner token as `Authorization: Bearer <token>`.
  `personalclaw token` prints a URL for a browser, and the value after `?token=` in it is
  that token.
- **local-network bypass** — when `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` is set, callers
  from a private IP skip the token. The gateway still binds `0.0.0.0`, so this is a
  convenience for your own LAN, not a security boundary.
- **`none`** — no auth, and the gateway **forces a loopback bind** so nothing off the
  machine can reach it.

From a script, keep the token out of the URL: request logs record URLs, and a pasted URL
lands in your shell history.

```bash
TOKEN_URL="$(personalclaw token | head -1)"
PERSONALCLAW_TOKEN="${TOKEN_URL#*token=}"
curl -H "Authorization: Bearer $PERSONALCLAW_TOKEN" http://127.0.0.1:10000/api/status
```

The header is stateless: it authenticates the request and sets no cookie. Opening the
`?token=` URL is the browser's way in instead — the gateway binds it to the first address
that uses it and exchanges it for the `HttpOnly` `pc_token_<port>` session cookie. A
Bearer that cannot authorize on its own (expired, revoked, malformed, an app's token, or
another surface's) answers `403` with the code `auth_bearer_invalid`; a request carrying
two different owner tokens, one in the header and one in `?token=`, answers `403`
`auth_credential_conflict`. Neither response contains the token.

**How long a token lasts, and how many there can be.** `personalclaw token` mints one for 20
hours unless `--ttl` says otherwise, and prints how long on stderr; `GET /api/token/local`
answers `expires_in`, `expires_at` and `open_within` (until when the `?token=` link can still
sign a browser in, at most 24 hours). Nothing lasts longer than 90 days: `?ttl=` longer than
that answers `400 token_ttl_too_long`, and a `ttl` that is not a duration like `30m`, `20h` or
`7d` answers `400 token_ttl_invalid`, each with a `message` that says what to ask for instead. Up to 20 tokens a browser has not opened can be live at
once, and past that the one used least recently is signed out — so a script that mints a token
per run keeps its own in use, and never signs out a browser, a paired phone or the desktop app,
which have limits of their own. Settings → Devices lists every sign-in and signs any of them out.

A browser (cookie or `?token=`) that cannot be signed in gets `403` and a `message` that says
why and how to sign in, with `detail.reason` and `detail.at`, and `X-Auth-Required: true`:

| Code | `detail.reason` | When |
|---|---|---|
| `session_signed_out` | `signed_out`, `signed_out_elsewhere`, `signed_out_others`, `signed_out_everywhere`, `limit`, `replaced`, `superseded`, `key_replaced` | Its session was ended — the message says by what, and when. `superseded`: a startup sign-in link no browser opened, replaced when the gateway started again. `key_replaced`: the owner replaced the key every sign-in is signed with (Settings → Security, or `personalclaw auth rotate-key`), which ends them all. |
| `session_signed_out` | `ended` | A genuine session nobody remembers ending (more than a week ago, or the store was cleared). |
| `session_expired` | `expired` | Its session ran its lifetime. |
| `session_expired` | `link_expired` | A `?token=` link past the 24 hours it can be opened in. |
| `session_required` | `link_used` | A `?token=` link already opened from another address. |
| `session_required` | `not_signed_in` | No sign-in at all — and, word for word, garbage or a token another key signed. |

A `Bearer` refusal is always the one `auth_bearer_invalid`, whatever went wrong.

Reaching the API from outside the machine is a tunnel-and-password problem, not an API
mode: see [remote access](../guides/remote-access.md) and the
[security model](../architecture/security.md).

**Two caller identities, not one.** The owner's session — the browser's cookie or the
Bearer header — can call everything. An **app-scoped token** (minted per installed app)
is deliberately narrower: sent in the Bearer header beside an owner session, it narrows
that session to the app's declared permissions (alone in the header it authorizes
nothing), and the portability routes, the security audit reads and the credential/secret
routes all refuse it outright. A route that refuses an app token says so in its handler docstring — treat
`403` from an app token as the designed answer, not a bug.

## Request and response conventions

- **JSON in, JSON out.** Bodies are JSON objects unless a route takes an upload
  (`multipart/form-data`) or raw bytes (`PUT /api/artifacts/{slug}/raw`).
- **Path parameters** are `{name}` in the reference. A path shown with a regex in source
  (`{tail:.*}`) appears canonicalized without it.
- **`PATCH` is a partial merge; `PUT` replaces.** Config, entity settings and most
  detail routes follow this — patching one field leaves the rest alone, and a `PUT` with
  a field omitted clears it.
- **A write that replaces a whole document names the revision it replaces.** A route that
  replaces a whole list, object or text from what the client read — a settings list, an
  agent profile, a skill — reports that document's `revision` on its read, and the write
  must send it back: `If-Match: "<revision>"`. If the document changed since that read,
  the write is refused with `409 stale_write` and nothing is written; a write that names no
  revision gets `428 revision_required`. Neither refusal carries the current revision: read
  the document again, re-apply your change to what it holds now, and save with the new
  revision. A revision is a digest of the document's content, so every writer — another
  tab, the agent, a background job, the CLI — changes it; a successful write's response
  carries the new one. Most reads put it in a `revision` field beside the document (a
  record's fields that are not part of what its editor writes back, like run state or
  timestamps, are left out of it, so a background update does not refuse your save);
  `GET /api/file-read` sends it as the `ETag` header. On `GET /api/config/personalclaw`,
  `revisions` maps each whole-document field's dotted path to its revision
  (`PATCH {path, value}` over it). A write that sets only single-valued fields (a name, a
  status, a model) replaces nothing else, so those routes need no revision for it.
- **A list of names is edited one entry at a time.** A config field that is a list of
  names takes `PATCH /api/config/personalclaw {path, add}` or `{path, remove}` (replacing
  the whole list is still `{path, value}` over its revision); session, artifact and
  knowledge-item tags take `add`/`remove` edits (`add_tags`/`remove_tags` on the artifact
  and knowledge PATCH), and a notification rule's `targets` and `conditions.keywords` take
  one `{add}` or `{remove}` edit. Each is applied to what is stored when it lands, so it
  needs no revision and cannot undo anyone else's change. The tag and notification-rule
  routes refuse a whole list with `400`.
- **Errors.** Routes added under the current convention return
  `{"error": {"code": "<stable_snake_code>", "message": "<human text>"}}`, and the `code`
  is append-only — never reworded once shipped, so it is safe to branch on. Older routes
  predate that convention and return their own shapes; the honest rule is to branch on
  `code` where it is present and on the HTTP status otherwise, and not to assume a single
  envelope across the whole surface.
- **A boolean is the JSON `true` or `false`.** A request field that is a switch or a consent
  takes the real boolean and nothing else: the text `"false"`, a number, `null` or any other
  value is refused with `400 field_not_a_boolean`, whose message names the field, and nothing is
  changed. A field left out takes the route's own default, which is never a yes to a consent.
  Where a route has no default of its own (a message's link previews follow the channel's
  setting unless you say), `null` is the same as leaving the field out.
- **A switched-off feature is an answer, not an error.** While a feature's switch is off,
  the reads a page loads to render it answer `200 {"enabled": false}`: the flag alone, with
  no empty collection beside it that a client could misread as "none". That covers the eval
  reports, the feedback producers and a thumbs pair's verdict, the Doctor's report, fix
  catalog and remediation snapshot, the Learning page's five reads, and the rooms list. An
  eval report whose command has not run yet answers `200 {"ran": false}` the same way
  (judge bench, ablation, learning benchmark, retrieval). A drill-down, an action or a write
  on a switched-off feature still refuses with its code (`evals_disabled`,
  `doctor_disabled`, `rooms_disabled`, …): it addresses something of a surface that is off.
- **Read back after a write.** The API does not promise that a mutating response body is
  the full post-write state. After a `POST`/`PUT`/`PATCH`/`DELETE`, `GET` the entity to
  confirm the change took. This is the single most useful habit when driving the gateway
  programmatically.

## Where routes are registered

Useful when you want the authoritative contract for a route, which is always its handler
docstring rather than any prose:

- `src/personalclaw/dashboard/routes.py` — the gateway's route table: its own mounts, in
  the order they match, and the `register_*_routes(app)` calls that pull in the rest.
- `src/personalclaw/dashboard/server.py` — the routes it registers around that table:
  the ones the MCP tools call, the provider extensions', the knowledge library's, the
  static files and the SPA fallback (`dashboard/fallbacks.py`).
- `src/personalclaw/dashboard/handlers/` — the bulk of the `/api/*` surface.
- Per-domain handler modules beside their domain: `tasks/handlers.py`,
  `workflows/handlers.py`, `artifacts/handlers.py`, `lexicon/handlers.py`,
  `providers/*_routes.py`.

The route reference is generated by walking exactly these registrations, which is why it
cannot fall behind them.

## Behaviours that are easy to get wrong

These are not derivable from a route's name or its docstring's first line, and each one
has cost someone a debugging session.

- **`POST /api/tools/invoke` gates on *effective* risk.** A call whose resolved risk is
  `destructive`, or a shell command the screen could not check (`unchecked`), is refused
  with `403 risk_confirmation_required` unless the body says `"confirm_risk":
  "destructive"`. A command screened read-only resolves `safe`, so a plain `bash "ls"`
  needs nothing. The nine filesystem/shell tools are confined to the
  configured workspace root; an unresolved root refuses with `503 workspace_unresolved`
  rather than running them in the gateway's own directory. The tool is resolved by its
  name over the same providers an agent turn has, to the one provider serving that name,
  so a `provider` in the body is not read, and an external MCP server's tool
  (`mcp/<server>/<tool>`) runs through the provider that serves it to agents. A name the
  agent's hard deny-list refuses (its built-in patterns, or a pattern in
  `hooks.auto_deny_tools`) is refused here too, with `403 tool_denied_by_policy`.
- **A tool name has one provider.** `bash`, `read_file` and the other platform tools are
  the platform's, names under `mcp/` are the MCP Tool Servers app's, a provider core ships
  outranks an installed app's, and otherwise the provider that claimed a name first keeps
  it. A provider offering a name someone else holds is refused whole, when its names are
  read (at enable, and on every read after): `POST /api/providers/{name}/enable` answers
  `409` with the reason, `POST /api/apps/{name}/enable`, install and update answer `ok`
  with the reason in `providerErrors`, the provider's row in `GET /api/providers` is off
  with the same sentence in `error`, and the security log has an `outcome=refused` row.
- **`POST /api/durability/import` validates when you omit `mode`.** Omitting it changes
  nothing at all, and `?mode=merge` fills in what the home lacks. A merge's `summary.items`
  says what became of each store the archive held: merged (a database row by row, as a merge
  restore merges it, so the archive's knowledge library and learning log come into the ones
  this home has), copied into a home without it, or left unchanged and why (this home keeps
  its own settings, feedback and any other single-file store it already has).
  `summary.left_unchanged` names each part the merge could not bring in (a store, or
  `store (table)`), as a restore's `left_unchanged` does, and Settings → Import / Export says
  so in the error tone.
  `POST /api/durability/archive/{id}/restore` is the same shape: no `mode` returns the plan,
  and `mode=merge` (with `confirm: true`) merges. Both refuse a replace the same way,
  `409 gateway_running`: a replace rewrites state the running gateway holds open, so the
  message names `personalclaw restore <archive> --mode replace`, which takes a snapshot or an
  export archive with the gateway stopped, and which refuses a replace in the same words while
  the gateway runs (a merge runs from either). A merge's answer carries `restart`, the sentence
  saying that the gateway picks up everything it brought in once it restarts. Credentials and
  rebuildable caches never travel in an export.
- **Secret values are write-only.** `/api/secrets` returns presence flags, names and
  derived consumer links — never a value — and there is deliberately no per-secret read
  endpoint. The credential-store migrate/rollback routes likewise carry key names and
  counts only. A secret stored with `project_id` is that project's: only its runs read it,
  ahead of a global secret of the same name, and a project row's consumer links are the
  workflows that name it. A name starting `PCSECRET_` or `PCPROJ_` is refused on store and
  on delete (`400 secret_name_reserved`): those are where PersonalClaw keeps a setting's own
  key and a project's secrets.
- **Unattended core updates are a config field, not an endpoint.** `updates.auto`
  (`off` | `staged`) is written through `PATCH /api/config/personalclaw`; the dedicated
  `/api/update/auto` route was retired.
- **Installed apps run from `$PERSONALCLAW_HOME/apps/<name>/`, not your workspace
  clone.** Push code changes with `POST /api/apps/{name}/update` `{source}`. An edit
  that changes what the app gets (permissions, scheduled jobs, packages, hooks, UI)
  answers 409 with the review; re-send it with the `consent` that review carries.
  Editing the clone does nothing to the running app.
- **An install needs consent, always.** `POST /api/apps/preview {source}` returns what
  the app would get plus a `consent` digest of the exact bytes; `POST /api/apps`
  `{source, consent}` installs only those bytes. A request without it installs nothing.
- **A write that needs the owner's yes answers with the question.** Sent without
  `"confirm": true`, it is refused `400 confirmation_required` with `{field, consent, title}` in
  `error.detail`, and nothing is written. A write that loosens a security setting also carries
  `change`, what it changes from and to (`"$33.50 → $10,033.50"`, `"Off → On"`, `"Adds “~/Projects”"`),
  and for a raise of ten times the value in effect or more, `caution`, one more sentence to show
  with it; the error's `message` says both. A client that will ask the owner itself sends
  `X-PersonalClaw-Consent: ask` and gets the same body as a `200` marked
  `X-PersonalClaw-Consent-Asked: 1`, which the dashboard does for every write, so its Allow
  dialogs log no failed request. Treat that `200` as the question, never as a success.
- **Task comment authors are server-derived.** `POST /api/tasks/{task_id}/comments`
  rejects an `author` in the body; the configured username wins.
- **Locked dashboard presets refuse mutation.** `PUT`/`DELETE` on a locked
  `/api/dashboard/views/{id}` answers `403` by design.
- **An MCP server's tool has one switch: its server's `disabledTools` in `mcp.json`.**
  `POST /api/mcp/toggle-tool {server, tool, enabled}` takes the name the server gives the
  tool (`hello`), which is the `serverTool` of its row in `GET /api/tools`. The
  `mcp/<server>/<tool>` form is refused with `400`, because in that list it matches
  nothing. `POST /api/tools/toggle` switches a native provider's tool in `tool_prefs.json`
  and refuses an MCP server's tool with `409`. A tool switched off either way is left out
  of a native agent's tools, answers `403 tool_disabled` from `POST /api/tools/invoke` and
  reads `disabled: true` in `GET /api/tools`. An ACP agent reads the same `disabledTools`.
  Every switch is the owner's: an app token is refused `POST /api/tools/toggle`,
  `POST /api/tools/provider-toggle` and the MCP switch, whatever its manifest declares.

## The same surface, three ways

One census, rendered for three different readers — so they cannot disagree:

| Where | What it is |
|---|---|
| [`api-routes.md`](api-routes.md) | This documentation site's route reference — every registration, with a family index. |
| `GET /api/manifest` | The live manifest from a running gateway: routes, registered tools with their exact input schemas, and providers. |
| `personalclaw doctor --paths` | Prints the offline reference directory shipped inside the installed package — the same route census plus full tool signatures, readable with no gateway running. |

---

See also: [HTTP route reference](api-routes.md) ·
[Configuration reference](configuration.md) · [CLI reference](cli.md)
