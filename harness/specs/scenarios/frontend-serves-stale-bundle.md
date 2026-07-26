---
id: frontend-serves-stale-bundle
type: triage-scenario
symptom: >
  A frontend change doesn't appear in the browser after a rebuild — the old UI keeps
  rendering — or the dashboard renders a version that predates recent SPA edits.
appliesTo:
  - web/**
requiredRules: []
acceptance:
  - The freshly built bundle is the one being served (verified by a visible change), OR
    the browser cache was the cause and a hard reload resolved it.
  - "`src/personalclaw/static/dist` is a SYMLINK to `web/dist`, not a copied directory."
---

# Symptom: frontend change not showing

## Probe order

1. **Is `static/dist` a symlink?** `src/personalclaw/static/dist` must be a *symlink* to
   `web/dist`. A `cp -R` (or a stray real directory) shadows the symlink and serves a
   **frozen, stale** SPA no matter how many times you rebuild. Check with `ls -l`.
2. **Did the SPA actually rebuild?** Frontend rebuilds are served live through the
   symlink (no gateway restart needed) — but you must run the build
   (`npm run build --workspace web` from the repo root, never `cd web`).
3. **Browser cache.** Once the served bundle is confirmed fresh, a hard reload
   (disable cache in devtools) rules out a client-side cached asset.
4. First-ever build after a clean clone needs one gateway restart so `/assets` routes
   register — after that, rebuilds are live.

## Known cause + mitigation

- **Cause:** stale served bundle (symlink shadowed by a real dir) or browser cache.
- **Mitigation:** ensure the symlink (`make web-build` creates it correctly); rebuild from
  the repo root; hard-reload the browser.
