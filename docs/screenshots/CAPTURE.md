# Capturing the visual showcase

Screenshots in [`SHOWCASE.md`](../../SHOWCASE.md) are generated reproducibly — real UI,
illustrative seeded data, both themes — not hand-curated. This keeps the showcase honest
and easy to refresh each release.

## Layout

```
docs/screenshots/
├── light/NN-<route>.png    # light theme
├── dark/NN-<route>.png     # dark theme
├── capture.mjs             # the Playwright capture script
└── CAPTURE.md              # this file
```

`NN` numbering is stable so `SHOWCASE.md` / `README.md` references don't drift when new
pages are added — append new routes, don't renumber existing ones.

## Prerequisites

1. **A running gateway with a model provider configured.** Most screens gate on a chat
   binding (onboarding needs a model before the main app renders). Configure one provider
   app + key + binding, or seed a demo instance (below).
2. **Seeded scenario data** (recommended) so screens show believable content rather than
   empty states. `personalclaw gateway --seed demo-home --seed-replace` populates an
   isolated `$PERSONALCLAW_HOME` with two projects, ten tasks across every status,
   markdown memory, five knowledge docs and one completed loop, and skips onboarding —
   so the Knowledge and loop-cockpit shots need no hand-driving. Semantic/episodic
   memory *records* are still absent (that store is SQLite-only with no text tier), so
   drive the memory-records views by hand if a shot needs them.
3. **A model bound into that seeded home**, if the capture includes chat, an approval or
   an artifact. The fixture binds no provider — it is a byte-identical copy on every
   machine, so it cannot carry one — and those three surfaces exist only as the product
   of a turn: sessions is `0`, `/api/approvals` is `[]` (an approval is written by the
   tool-permission gate on a real turn), `/api/artifacts` is `[]` (artifacts are
   agent-produced, with no hand-authoring affordance in the UI). Add
   `--seed-local-model` to the seed command and a local Ollama is bound in the same
   step; with no Ollama present it writes nothing and says so, so the command is safe to
   keep in the runbook either way. Details in
   [`docs/reference/cli.md`](../reference/cli.md#binding-a-model-into-a-seeded-home).
   Do NOT stage a screen the product cannot produce — capture the empty state or drop
   the beat instead.
4. **Playwright**: `npm i -D playwright && npx playwright install chromium`.

## Run

```bash
# Option A — token auth (production-like):
personalclaw gateway --json-ready          # copy the printed port + token
PCLAW_URL=http://localhost:<port> PCLAW_TOKEN=<token> node docs/screenshots/capture.mjs

# Option B — loopback, token-free (quick local capture):
PERSONALCLAW_AUTH_MODE=none personalclaw gateway --port 10000 --no-open
PCLAW_URL=http://localhost:10000 node docs/screenshots/capture.mjs
```

The script visits every route in `ROUTES` (edit the array as pages are added), toggles the
theme via `localStorage.mode` + `data-mode` (how the SPA persists it), and writes
`light/` + `dark/` PNGs at a 1440×900 @2x viewport.

## Conventions

- **Both themes, always.** The design system styles light and dark with equal care; the
  showcase reflects that.
- **No real personal data.** Capture against a throwaway `PERSONALCLAW_HOME`, never your
  real `~/.personalclaw`. Seed illustrative data only.
- **Refresh per release** so the showcase never drifts from the shipped UI.
