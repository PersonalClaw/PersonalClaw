# Cross-repo deliverables (plan 34 — Distribution & Packaging)

These artifacts are **produced in the core repo but land in sibling repos** — the
maintainer hand-applies them (they're small and cross-repo). This directory is a
staging area so the content is version-controlled, reviewable, and CI-checkable
here before it's copied out.

---

## 1. `install.sh` → the website repo (personalclaw.dev, plan 36) — T2.2

`deploy/website/install.sh` (in this repo) is the bootstrap one-liner served at
`https://personalclaw.dev/install`. It is POSIX sh (validated with `sh -n` +
`dash -n`), `--container`-aware, and idempotent.

**To apply:** copy `deploy/website/install.sh` into the website repo at the path
plan 36 S1 chose for static assets (e.g. `public/install` or `static/install`),
and serve it at `/install` with `Content-Type: text/plain; charset=utf-8`. Wire
the plan 33 `full.yml` weekly smoke that runs it in a bare `ubuntu` container.
`shellcheck` it in the website repo's CI (clean as written).

Usage the README/getting-started already document:

```sh
curl -fsSL https://personalclaw.dev/install | sh          # install via uv
curl -fsSL https://personalclaw.dev/install | sh -s -- --container   # print compose snippet
```

---

## 2. Provider-app manifests → the apps repo (PersonalClawApps) — T1.4

`openai` and `anthropic` are no longer core dependencies (plan 34 T1.4). The
branded provider apps must declare their SDK so the app-install pipeline
(`app_manager._install_python_deps`, verified in plan 32 T2.1) installs it into
the shared venv. In each app's `app.json` / `manifest.json`, add (or extend) the
top-level `dependencies` object — mirroring the slack-channel precedent
(plan 32 T1.5, commit `7538b63`):

**openai-models** `app.json`:

```json
"dependencies": { "pythonDependencies": ["openai>=1.0"] }
```

**anthropic-models** `app.json`:

```json
"dependencies": { "pythonDependencies": ["anthropic>=0.20"] }
```

If the OpenAI STT/TTS providers ship as their own apps (not under openai-models),
add the same `"openai>=1.0"` line to those manifests. After install/update the
pipeline pip-installs these; a newly-introduced dep needs a gateway restart
(`restart_required` in the install result). The `[openai]` / `[anthropic]`
packaging extras remain the plain-pip/uv path for users who don't install an app.

---

## Owner real-world steps (already handled per owner, or pending)

- **T2.1** — first PyPI publish via `release.yml` (env `release`) + verify
  `uv tool install personalclaw` / `pipx install personalclaw` on a clean machine.
- **V1** — clean-VM/empty-container wheel install → onboarding → first chat, Node
  absent (the CI verify-wheel step already exercises the Node-free serve path).
- **V2** — follow the new getting-started verbatim on a clean machine (uv path).
- **V3** — container clean-VM: two commands → dashboard via TLS → create
  session+memory → `compose down && up` → state intact.
- **V4** — per-kind self-update walkthroughs (git one-tag-behind; pip
  one-version-behind; container instructions; desktop stub; changelog panel).

S5 (Homebrew / Nix) is out of scope for this plan iteration (owner decision
2026-07-21).
