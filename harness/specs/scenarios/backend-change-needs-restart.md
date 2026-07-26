---
id: backend-change-needs-restart
type: triage-scenario
symptom: >
  A backend `.py` change appears to have "no effect" — the API still returns the old
  behavior, or a new endpoint 404s — even though the file was clearly edited and saved.
appliesTo:
  - src/personalclaw/**/*.py
requiredRules: []
acceptance:
  - The dev gateway was restarted after the backend edit and the new behavior is observed.
  - No code change was made in pursuit of a "bug" that was only a stale running process.
---

# Symptom: backend edit seems ignored

## Probe order

1. **Did the gateway restart?** Backend Python changes NEVER hot-reload. `make serve` runs
   a fixed process; editing a `.py` file does nothing until the process is restarted.
2. Confirm you're hitting the dev gateway you think you are (port, dev home) — not a
   separately installed service (`personalclaw stop`/`restart` are service-first and may
   act on a real launchd/systemd gateway, not your foreground `make serve`).
3. Only after a clean restart, if the behavior is still wrong, treat it as a real bug.

## Known cause + mitigation

- **Cause:** stale running process serving pre-edit code.
- **Mitigation:** Ctrl-C the foreground `make serve`, then `make serve` again (a `.py`
  change). Use `make serve-fresh` if the FE `dist` symlink might also be stale. Tail
  `.dev-home/gateway.log` to confirm the restart picked up your change.

## Redaction

The gateway log may contain the tokenized ready URL — treat it as sensitive; never paste
it into a finding, a commit, or a shared channel.
