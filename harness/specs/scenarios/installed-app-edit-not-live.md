---
id: installed-app-edit-not-live
type: triage-scenario
symptom: >
  An edit to an app under the workspace `apps/`/`PersonalClawApps` tree doesn't change the
  running app's behavior in the gateway, even after a gateway restart.
appliesTo:
  - apps/**
requiredRules:
  - app-sdk-boundary
acceptance:
  - "The app edit was pushed to the installed copy via `POST /api/apps/{name}/update`
    and the new behavior is observed in the running gateway."
  - No time was spent debugging the workspace source when the gateway was running an
    older installed copy.
---

# Symptom: app edit has no effect on the running gateway

## Probe order

1. **The gateway runs INSTALLED app copies**, not your workspace tree. Installed apps live
   under `$PERSONALCLAW_HOME/apps/<name>/`. Editing the repo `apps/` source does not touch
   the installed copy the gateway loads.
2. Confirm the app is even discovered: this workspace's apps clone is `PersonalClawApps`,
   not `apps`, so the gateway needs `PERSONALCLAW_FIRST_PARTY_APPS_DIR` pointed at it (or
   an `apps` symlink) to see them in the Store at all.

## Known cause + mitigation

- **Cause:** the gateway loads the installed copy; the workspace edit never reached it.
- **Mitigation:** push the edit with `POST /api/apps/{name}/update {source, confirm:true}`,
  then exercise the app. Do not edit the installed copy under the dev home directly —
  it's overwritten on the next update.

## Related

Any app code touched must still honor [[app-sdk-boundary]] — the update pushes the same
code the boundary test checks.
