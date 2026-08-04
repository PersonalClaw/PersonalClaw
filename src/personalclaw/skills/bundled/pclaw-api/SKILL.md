---
name: pclaw-api
description: The operator's manual for DRIVING PersonalClaw's API and tools — how to find an exact tool/route signature, the mandatory verify-after-mutate loop, and what NOT to hand-roll. Load this before creating triggers, wiring models, driving app routes, or authoring skills through the API.
always: false
triggers: api, manifest, tool signature, drive personalclaw, /api, endpoint, route, agent-callable, doctor --paths, offline reference, wire a trigger, bind a model, call app route
---
# Driving PersonalClaw (operator's manual)

This is the **operator twin** of the `pclaw-features` skill. `pclaw-features` answers
"what can PersonalClaw do" in prose; **this** skill is how you *drive* it correctly
from the API and native tools — the exact-signature discipline that turns a driving
session from guess-and-retry into first-try success.

The companion to this skill is the **offline reference** shipped in the
distribution. It is the single source of truth for signatures — generated from the
same registry the live `GET /api/manifest` walks, so it never drifts from reality.

## 1. Orient, then drill (don't read everything)

The reference is four files. Read the **index first**, then open only the one
section you need:

- `reference/index.md` — the map + repo gotchas + what-not-to-do. Start here.
- `reference/tools.md` — every registered tool, grouped by provider, with its
  **exact input schema and worked examples**.
- `reference/routes.md` — the agent-callable `/api/*` HTTP routes with summaries.
- `reference/providers.md` — the provider-type taxonomy + registered providers.

**Find the reference from the binary alone** — you don't need to know the install
layout:

```
personalclaw doctor --paths
```

prints tab-separated `key<TAB>path` lines; the `reference` line is the directory
above. (It also prints `config`, `skills`, and `install`.) On a *running* gateway
the same content is live at `GET /api/manifest` — same source, two renderings.

## 2. Never guess a signature — copy it

Hallucinated parameters are the dominant driving failure. Before you call any tool
or route:

1. Look it up in `tools.md` / `routes.md` (or `GET /api/manifest`).
2. Copy the **exact** parameter names from the schema. The examples there use
   real, schema-verified arg names — start from an example and adjust.
3. If a name isn't in the schema, it doesn't exist — don't invent it.

The `examples[]` in each tool entry are checked against the live schema by the
drift test, so an example never carries an invented parameter. Trust them.

## 3. The mandatory verify loop (after every mutation)

A tool or route reporting success is **not** proof the change took. After any
mutating call — create/update/delete a trigger, bind a model, install a skill,
create a task — **read the entity back** and confirm the field you set is present:

- Wired a trigger with `hook_register` → list hooks / re-read and confirm it's there.
- Bound a model with `PUT /api/models/active/{use_case}` → `GET /api/models/active`
  and confirm the binding.
- Added a knowledge item with `knowledge_create` → `knowledge_search` (or
  `knowledge_get` by id) and confirm it's retrievable.
- Installed a skill → `skill_search` and confirm it's in the index.

If the read-back doesn't show your change, the call **silently missed** — treat
that as a failure and diagnose, don't report success.

## 4. Read the error envelope — it tells you the fix

Failures returned into your session carry a **WHAT / WHY / FIX** envelope and a
stable `code` (e.g. `ERR_TOOL_ARG_INVALID`, `ERR_MODEL_UNRESOLVED`,
`ERR_HOOK_PROVIDER_UNKNOWN`). Branch on the **code**, never on the prose. A
`DID YOU MEAN:` line lists the nearest valid values — pick from it directly. When
a hook/trigger rejects an unknown action provider, the allowed set arrives as the
suggestions; use one of those.

## 5. Worked patterns

- **Create + wire a trigger:** register the follow-up with `hook_register`
  (`hook_id`, `context_summary`), or schedule recurring work with
  `automation_create`. Then read it back (§3).
- **Add knowledge and verify retrieval:** `knowledge_create` (`type`, `title`,
  `content`) → `knowledge_search` for the title to confirm it's indexed.
- **Bind a model to a use case:** `PUT /api/models/active/{use_case}` with body
  `{"models": ["provider_name:model_id", ...]}` → `GET /api/models/active` to
  confirm.
- **Drive an app backend route:** call the app-route tool the manifest surfaces
  for that app's declared route (see `app_surfaces[]` in the manifest); don't
  reach into the app's process directly.
- **Author + install a skill:** persist a reusable how-to with `skill_remember`
  (`title`, `body`) → `skill_search` to confirm it's live.

## 6. Scope — what NOT to do

- **Don't hand-roll UI** when a tool or route already does the job. The manifest
  is the inventory of what exists; check it before building.
- **Don't edit an installed app's files** to change its behavior. Push edits with
  `POST /api/apps/{name}/update` `{source, confirm:true}` — the gateway runs the
  INSTALLED copy under `$PERSONALCLAW_HOME/apps/<name>/`, not the workspace tree.
- **Don't call a route the manifest doesn't mark `agent_callable`** as if it were
  an agent API — those are UI transport or websocket surfaces.
- **Don't replace the `static/dist` symlink with a copy** — a `cp -R` shadows it
  and serves a stale SPA; rebuild the frontend in place.

For the capability overview and channel-neutral "what can you do", see the
`pclaw-features` skill.
