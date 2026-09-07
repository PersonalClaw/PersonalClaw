# Cross-repo deliverables (plan 34 — Distribution & Packaging)

These artifacts are **produced in the core repo but land in sibling repos** — the
maintainer hand-applies them (they're small and cross-repo). This directory is a
staging area so the content is version-controlled, reviewable, and CI-checkable
here before it's copied out.

**Status.**
1. `install.sh` → `personalclaw.dev` `public/install`, served at `/install` with
   `Content-Type: text/plain; charset=utf-8` (vercel.json). Applied 2026-07-21,
   byte-identical then; **drifted 2026-08-18 and re-applied — see §1.**
2. `openai`/`anthropic` `pythonDependencies` → the 12 SDK-backed provider apps in
   `PersonalClawApps` (all 10 openai-wire + 2 anthropic-wire apps, not just the two
   named below — see the corrected list in §2). Manifests validated + app tests green.

---

## 1. `install.sh` → the website repo (personalclaw.dev, plan 36) — T2.2

`deploy/website/install.sh` **in this repo is the source of truth** for the bytes served at
`https://personalclaw.dev/install`. The website repo's `public/install` is a **mirror**. It is
POSIX sh (`sh -n` + `dash -n` + `shellcheck -s sh`), `--container`-aware, and idempotent.

### The drift this section exists to prevent

The hand-apply landed byte-identical on 2026-07-21. On 2026-08-18, `72e43db3b` (PR #1642)
corrected one line here — the installer had told users `personalclaw setup` "configure your
name + first model provider", which it collects neither of and cannot; the wizard does
workspace dir (step 1) and timezone (step 4), and provider binding lives in the dashboard.
**Nobody re-applied the mirror**, so for three weeks personalclaw.dev handed every newcomer
advice that was wrong, while core linted and tested a file no user ever received. Measured
2026-09-06: staged `d7a852c1…`, served `8e06a1fd…`, one line apart.

There is no sync automation — there never was. The instruction below was the whole mechanism,
and an unenforced instruction drifts the first time one side is edited.

### To apply (and the two things that now stop you forgetting)

1. Copy `deploy/website/install.sh` → the website repo's `public/install`, served at
   `/install` with `Content-Type: text/plain; charset=utf-8`.
2. **Update `install.sh.sha256` in the same change** as any edit to `install.sh`:

   ```sh
   cd deploy/website && shasum -a 256 install.sh > install.sh.sha256
   ```

   `tests/test_website_installer.py` reds if the pin and the file disagree. That red is the
   only moment anyone is reminded the mirror exists, which is exactly what it is for.
3. `full.yml`'s `install-smoke` job compares the pin against the bytes personalclaw.dev
   really serves and reds on any difference — so a forgotten step 1 surfaces on the next push
   to `main` or nightly run, rather than three weeks later in a bug report.

### What CI actually covers (no longer an aspiration)

| Where | What | Cadence |
| --- | --- | --- |
| `ci.yml` `lint` | `shellcheck -s sh` · `sh -n` · `dash -n` · the offline module with `PERSONALCLAW_REQUIRE_INSTALL_PROOF=1` | every PR |
| `ci.yml` `test` | same module, leverless (a laptop without dash skips rather than reds) | every PR |
| `full.yml` `install-smoke` | staged **and** served installer in a bare `ubuntu:latest` container, `personalclaw --version`, plus the served-vs-pinned drift check | push to `main` + nightly |

A fetch failure in `install-smoke` reports **`unproven` and reds**. It never goes green: an
unanswered question is not a passing answer, and this repo has mistaken the two before.

The website repo does not need its own shellcheck job — the linting happens here, where the
file is edited.

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

**Corrected scope (applied 2026-07-21):** the dependency is required by *every* app
that constructs core's `OpenAIProvider`/`AnthropicProvider` (the inference path uses
the vendor SDK's own client — `llm/catalog.py:322` — while only `/v1/models`
discovery is SDK-free). That is **12 apps, not 2**:

- `"pythonDependencies": ["openai>=1.0"]` (openai wire) — `openai-models`,
  `openai-compatible`, `alibaba-models`, `deepseek-models`, `google-models`,
  `groq-models`, `mistral-models`, `together-models`, `vllm-models`, `meta-muse-spark`
- `"pythonDependencies": ["anthropic>=0.20"]` (anthropic wire) — `anthropic-models`,
  `anthropic-compatible`

Not affected: `ollama-models` (httpx — a core dep), `bedrock-models` (already declares
`boto3`), `openai-tools` (REST via `net.fetch`, no SDK). Specifiers match core's
canonical `[openai]`/`[anthropic]` extras exactly. The block is inserted before
`provider` (slack-channel precedent, plan 32 T1.5 `7538b63`). After install/update the
pipeline pip-installs these; a newly-introduced dep needs a gateway restart
(`restart_required` in the install result). The `[openai]` / `[anthropic]` packaging
extras remain the plain-pip/uv path for users who don't install an app.

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
