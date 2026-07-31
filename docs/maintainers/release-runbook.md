# Release runbook

How to cut a PersonalClaw release. Written so that someone with org access can
follow it start to finish without reading the workflow files first.

Every step below was verified against `.github/workflows/release.yml` and the
v0.1.3 release. Where a number or path appears here it came from the code, not
from memory — if you find a discrepancy, the code wins and this page is the bug.

## What a release actually is

One tag push produces every artifact. `release.yml` triggers on `tags: ["v*"]`
and runs seven jobs:

| Job | What it produces | Gate |
|---|---|---|
| `build` | sdist + wheel (SPA bundled inside), SBOM | verifies the wheel serves the SPA with **no Node** present |
| `pypi` | core package on PyPI | environment `release` — **needs your approval** |
| `pypi-client` | `personalclaw-client` on PyPI | environment `release-client` — **needs your approval** |
| `images` | `ghcr.io/personalclaw/personalclaw-{gateway,web}`, multi-arch | — |
| `notes` | the GitHub Release, notes lifted from `CHANGELOG.md` | needs `build`, `pypi`, `images` |
| `website-follow` | nudges personalclaw.dev to re-check its pins | best-effort, `continue-on-error` |
| `attest` | build-provenance attestation on the wheel (OIDC, no keys) | — |

Two consequences worth knowing before you start:

- **The release pauses for you.** `pypi` and `pypi-client` sit on protected
  environments. Nothing reaches PyPI until you approve both in the run's UI.
- **Publishing is idempotent.** Both PyPI jobs use `skip-existing`, so re-running
  a release whose version is already published no-ops instead of failing.

## Before you tag

### 1. Bump the version in all six places

These are enforced by `tests/test_version_consistency.py` — miss one and CI goes
red *after* you have already pushed the tag, which is the annoying way to find out.

| File | Field |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` — the single source of truth |
| `src/personalclaw/__init__.py` | `_FALLBACK_VERSION = "X.Y.Z"` |
| `packages/personalclaw-client-py/pyproject.toml` | `version = "X.Y.Z"` — **lockstep with core by owner policy (2026-07-22)** |
| `src/personalclaw/acp/client.py` | `CLIENT_VERSION = "X.Y.Z"` — sent in the ACP handshake |
| `README.md` | the pre-1.0 banner's `PersonalClaw is at **vX.Y.Z**` |
| `CHANGELOG.md` | a heading of exactly the form `## [X.Y.Z] …`, as the newest entry |

All six are enforced by `tests/test_version_consistency.py`. The last two were added
to it on 2026-07-31 after both had silently drifted — `CLIENT_VERSION` sat at `0.1.2`
through the 0.1.3 release (so every ACP agent was told the wrong version), and the
README banner still said `v0.1.0` three releases later, in the very paragraph warning
users their data may break.

The `## [X.Y.Z]` form matters: the `notes` job extracts the release body with
`^## \[<ver>\][^\n]*\n(.*?)(?=^## \[|\Z)`. A heading without the brackets, or a
version mismatch, yields the bare fallback text `Release X.Y.Z.` — a published
release with no notes, which is not something you can edit out of the tag later.

Confirm before committing:

```bash
.venv/bin/python -m pytest tests/test_version_consistency.py -q
```

### 2. Re-lock if dependencies changed

CI runs `uv sync --locked`. A `pyproject.toml` dependency change without a
re-locked `uv.lock` **in the same commit** reddens everything downstream.

```bash
uv lock
```

### 3. Run the full local gate

Remote CI is confirmation, not discovery.

```bash
make lint                       # black, isort, flake8, mypy
make test                       # the full suite
cd web && npm run typecheck && npm test && npm run build
```

### 4. Land the bump through a PR

`main` is protected and append-only. Branch, PR, merge — never push to `main`,
and never force-push it (the `git pull`-based self-updater depends on its linear
history).

## Tagging

Tag the **merge commit on `main`**, not your branch tip:

```bash
git checkout main && git pull --ff-only
git tag -a v0.1.3 -m "PersonalClaw v0.1.3"
git push origin v0.1.3
```

> **Annotated tags dereference.** `git rev-parse v0.1.3` gives you the *tag
> object* SHA, not the commit. Anything that needs the commit — notably the
> website's source pins — must use `git rev-parse 'v0.1.3^{commit}'`. Pinning the
> tag-object SHA is the classic error here, and personalclaw.dev's parity gate
> will catch it and fail closed.

## While it runs

1. Open the run in Actions. Wait for `build` to go green.
2. **Approve the `release` environment** → `pypi` publishes core.
3. **Approve the `release-client` environment** → `pypi-client` publishes the client.
4. `images`, `notes` and `attest` finish on their own.

## After it finishes

Verify the release from the outside, the way a user meets it — not by trusting
green checkmarks.

```bash
# 1. PyPI has it, and it installs clean
python -m venv /tmp/relcheck && /tmp/relcheck/bin/pip install -q personalclaw==X.Y.Z
/tmp/relcheck/bin/personalclaw --version

# 2. The wheel really bundles the SPA (the `build` job asserts this, confirm anyway)
/tmp/relcheck/bin/python -c "import personalclaw, pathlib; \
  d = pathlib.Path(personalclaw.__file__).parent / 'static' / 'dist'; \
  print('SPA files:', len(list(d.rglob('*'))) if d.exists() else 'MISSING')"

# 3. The images pull and are multi-arch (needs `docker login ghcr.io`, or a
#    token with read:packages if you check via the API instead)
docker pull ghcr.io/personalclaw/personalclaw-gateway:X.Y.Z
docker manifest inspect ghcr.io/personalclaw/personalclaw-gateway:X.Y.Z \
  | grep -c architecture        # expect >1 for multi-arch

# 4. The GitHub Release exists with real notes
gh release view vX.Y.Z
```

Then clean up: `rm -rf /tmp/relcheck`.

### The cross-repo obligation

A published core release is an **obligation on the other two repos**:

- **`PersonalClawApps`** — tag it at the matching commit. The website's
  `released` channel requires *both* a core tag and an apps tag to resolve, and
  will fail closed without them.
- **`personalclaw.dev`** — bump `sources/personalclaw.sources.json` (tag **and**
  dereferenced commit for core + apps), bump `package.json`, and refresh anything
  the release changed. `release.yml` nudges its `release-follow.yml`; if the
  `WEBSITE_DISPATCH_TOKEN` secret is absent the nudge is skipped and the
  website's own daily watchdog catches it instead.

## If something goes wrong

| Symptom | What it means | Action |
|---|---|---|
| `pypi` job fails on "already exists" | shouldn't happen — `skip-existing` is set | check you are not publishing a *different* build of a published version |
| Version consistency test red | one of the six files was missed | fix, PR, then **delete and re-push the tag** |
| Website parity job red | its pins point at the wrong SHA | almost always the annotated-tag dereference above |
| `website-follow` skipped | no `WEBSITE_DISPATCH_TOKEN` | harmless; the daily watchdog covers it |

**A published version is immutable.** PyPI does not allow re-uploading a
version, so a bad release is fixed by publishing the next patch — never by trying
to replace it. Yank on PyPI only if the artifact is actively harmful.

## Provenance of this page

Walked end to end against the shipped **v0.1.3** on 2026-07-31. Verified live:
the six version surfaces agree; `tests/test_version_consistency.py` passes;
`git rev-parse v0.1.3` and `v0.1.3^{commit}` genuinely differ (the dereference
trap above is real, not theoretical); the `release` and `release-client`
environments both exist on the repo; PyPI carries core **and** client at 0.1.3
(lockstep holds); the GitHub Release exists with real CHANGELOG-derived notes;
and `src/personalclaw/static/dist` is the symlinked SPA path the wheel check uses.

Not verified from a dev machine: the GHCR image pull (needs `docker login` or a
`read:packages` token). Treat step 3 as the one item to confirm by hand.

## Notes

- Releases have no separate signing step today: provenance comes from GitHub's
  OIDC attestation (`attest`), and PyPI uses trusted publishing, so there are no
  long-lived tokens to rotate or lose. Artifact **signing** (minisign/Sigstore)
  is SECURITY-HARDENING's S2, unbuilt as of v0.1.3.
- The `release` and `release-client` environments exist precisely so a tag push
  cannot publish unattended. Do not remove that approval gate to save a click.
