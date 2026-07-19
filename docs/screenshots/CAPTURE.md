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
   empty states. Until a `demo` seed fixture ships (roadmap: DISCOVERABILITY-LAUNCH T3.1),
   drive a few real actions once — create a knowledge note, a task, run a short loop.
3. **Playwright**: `npm i -D playwright && npx playwright install chromium`.

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
