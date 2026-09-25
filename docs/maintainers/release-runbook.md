# Release runbook

How to cut a PersonalClaw release. Written so that someone with org access can
follow it start to finish without reading the workflow files first.

Every step below was verified against `.github/workflows/release.yml` and the
v0.1.3 release. Where a number or path appears here it came from the code, not
from memory — if you find a discrepancy, the code wins and this page is the bug.

## What a release actually is

One tag push produces every artifact. `release.yml` triggers on `tags: ["v*"]`
and runs ten jobs:

| Job | What it produces | Gate |
|---|---|---|
| `build` | sdist + wheel (SPA bundled inside), SBOM | verifies the wheel serves the SPA with **no Node** present |
| `pypi` | core package on PyPI | environment `release` — **needs your approval** |
| `pypi-client` | `personalclaw-client` on PyPI | environment `release-client` — **needs your approval** |
| `gateway-dashboard` | nothing; it is purely a gate | boots the gateway image built from this commit and requires a **served bundle**, not a status — `images` needs it, so a dashboard-less image is never pushed |
| `images` | `ghcr.io/personalclaw/personalclaw-{gateway,web}`, multi-arch — tags per [the scheme below](#what-a-tag-publishes) | needs `build` and `gateway-dashboard`; then pulls each arch and runs the image's own liveness command inside it |
| `desktop-mac` | unsigned `.dmg` (Apple silicon only) | boots the bundled backend from inside the mounted dmg |
| `desktop-linux` | unsigned `.AppImage` + `.deb` (x86-64) | boots the bundled backend out of the extracted AppImage |
| `notes` | the GitHub Release, notes lifted from `CHANGELOG.md` | needs `build`, `pypi`, `images`, `desktop-linux`, `desktop-mac` |
| `website-follow` | nudges personalclaw.dev to re-check its pins | best-effort, `continue-on-error` |
| `attest` | build-provenance attestation on the wheel (OIDC, no keys) | — |

Two consequences worth knowing before you start:

- **The release pauses for you.** `pypi` and `pypi-client` sit on protected
  environments. Nothing reaches PyPI until you approve both in the run's UI.
- **Publishing is idempotent.** Both PyPI jobs use `skip-existing`, so re-running
  a release whose version is already published no-ops instead of failing.

### What a tag publishes

The tag you push decides which pointers move. `scripts/release_tags.py` is the
one parser that decides it — the `images` job's tag list and the `notes` job's
`gh release create` flags both come from it, and
`tests/test_release_tag_semantics.py` asserts it — so this table is the contract,
not a description of it:

| You push | GitHub Release | Image tags |
|---|---|---|
| `v0.3.0` | **Latest** | `:0.3.0`, `:0.3`, `:latest` |
| `v0.3.0-rc.1`, `v0.3.0-beta.2` | **Pre-release** | `:0.3.0-rc.1`, `:beta` |

The half worth internalizing: **a prerelease moves no stable pointer.** Not
`:latest` (the compose file's default is `${PERSONALCLAW_IMAGE_TAG:-latest}`, so
moving it would drag every default container install onto a candidate), and not
the moving minor `:X.Y` — not even its own. Once `v0.3.0` ships, `:0.3` means the
stable 0.3 line, and anyone who pinned `:0.3` did not opt into candidates. The
moving `:X.Y` and `:beta` tags are what `updates.channel` resolves to for a
container install (`self_update.select_image_tag`), which is why they have
to exist and why the negative cases are tested.

A tag the parser cannot classify **fails the run before the build starts** —
that includes a ref with no `v` prefix, anything that is not `X.Y.Z`, and a tag
carrying `+build` metadata (`+` is not a legal Docker tag character). Cutting a
release candidate therefore needs no special flag or manual step: tag
`vX.Y.Z-rc.N` and the pipeline does the rest.

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
and never force-push it: release tags are cut from `main`, and the git kind's
`nightly` channel fast-forwards it, so both depend on its linear history.

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
4. `gateway-dashboard`, `images`, the two `desktop-*` jobs, `notes` and `attest`
   finish on their own. If `gateway-dashboard` reds, **no image was pushed** and
   the fault is in the image, not the tag: read its log for which of healthz, `/`
   or the first bundle failed, fix the image, and re-run the tag.

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

### Convenience-channel smoke (Homebrew · Nix)

The two convenience channels are the only install paths that live **outside** the release
pipeline, so nothing in a tag push updates or verifies them. Run this checklist once per
release, after PyPI has the new version — both channels point at a *published* artifact, so
neither can be bumped before then.

Every step runs in a throwaway container, because a dev box cannot test the claim these
channels make: that they work on a machine that does not already have the dependency. One
command does all three install paths:

```bash
VERSION=X.Y.Z scripts/fresh_install_validate.sh          # pip · brew · nix
VERSION=X.Y.Z scripts/fresh_install_validate.sh pip nix  # or one leg at a time
```

Then the per-channel work a release actually requires:

**Homebrew** — [`PersonalClaw/homebrew-tap`](https://github.com/PersonalClaw/homebrew-tap):

- [ ] Bump `Formula/personalclaw.rb`: **`url` and `sha256`, and nothing else**. The tap's
      README carries the one-liner that prints both lines; the URL cannot be hand-edited from
      the version because `brew style` requires PyPI's opaque digest path.
- [ ] Push to the tap's `main` and confirm its `formula` workflow is green. That job is the
      only evidence for the Homebrew row: it runs the real
      `brew install personalclaw/tap/personalclaw` on a clean GitHub-hosted macOS runner.
- [ ] `brew upgrade personalclaw` on a machine that already had the previous version — a
      fresh install and an upgrade take different code paths through the keg.

**Nix** — `flake.nix` + `nix/personalclaw.nix` in this repository:

- [ ] Bump `version` and the wheel `hash` in `nix/personalclaw.nix`
      (`nix store prefetch-file --json <wheel url>`).
- [ ] Re-check the dependency list against the **released wheel's** metadata, not against
      `pyproject.toml`. They drift: 0.1.3's wheel asks for `croniter<7` and `reportlab<5`
      where `pyproject.toml` now says `croniter<3` and `reportlab<6`, and the wheel declares
      no `sqlite-vec`, `cryptography` or `jsonschema` at all.
      `unzip -p <wheel> personalclaw-X.Y.Z.dist-info/METADATA | grep '^Requires-Dist'`
- [ ] Re-check the `nixpkgs` pin in `flake.nix` satisfies those bounds. This is the step that
      actually breaks: `nixos-unstable` carried reportlab 5.0.1 against `reportlab<5`, and
      `nixos-25.11` carried tree-sitter-language-pack 0.10.0 against `>=1.0` — the two
      failures point in opposite directions, so neither "track unstable" nor "pin older" is a
      standing answer. `pythonRuntimeDepsCheckHook` fails the build and names the offending
      specifier, so measure rather than guess.
- [ ] `nix flake check` passes (it runs `personalclaw --version` and greps for the version).

A failure in either channel is **not** release-blocking for PyPI or the containers — the
convenience channels lag by design, since they can only reference a published artifact. It is
blocking for the corresponding row in the [install matrix](../../README.md#install-matrix):
fix the channel or demote the row, and do not leave it claiming support it did not earn.

Coverage this checklist does **not** give you, stated so nobody reads it as more than it is:

| Claim | Evidence | Gap |
|---|---|---|
| Homebrew on macOS | the tap's CI, on a clean macOS runner, every push | arm64 only (`macos-latest`); no Intel Mac |
| Homebrew on Linux | `fresh_install_validate.sh brew` (linuxbrew in a container) | host architecture only. Do **not** try `--platform linux/amd64` on Apple Silicon: Homebrew refuses an x86_64 CPU without SSSE3, it reads `/proc/cpuinfo` (which shows the host's ARM CPU), and `QEMU_CPU=max` does not help |
| Nix | `fresh_install_validate.sh nix` | the container's architecture only |
| pip | `fresh_install_validate.sh pip` | ditto |

### Windows smoke (rung 1 — Docker Desktop)

Do this once per release on a Windows box with Docker Desktop (WSL2 backend).
It is the only evidence behind the *Windows via Docker Desktop* row in the
[support matrix](../guides/platforms.md#support-matrix) — if it is not run, that
row is a claim rather than a fact. Follow
[Windows via Docker Desktop](../guides/platforms.md#windows-via-docker-desktop)
verbatim; the point is to catch drift between the guide and reality.

```powershell
# 1. Pull the tagged release and bring the stack up
#    (set PERSONALCLAW_IMAGE_TAG=X.Y.Z in .env first, so this is not `latest`)
docker compose -f deploy/compose/compose.yaml pull
docker compose -f deploy/compose/compose.yaml up -d

# 2. Both services healthy (gateway must report healthy, not just "running")
docker compose -f deploy/compose/compose.yaml ps
```

Then, in a Windows browser:

- [ ] `https://localhost:3443` loads the dashboard (self-signed warning is expected — click through)
- [ ] `http://localhost:3000` 308-redirects to the HTTPS port
- [ ] **one chat turn** against a real provider returns a streamed reply (proves the gateway, the SSE/WS path through nginx, and credentials)
- [ ] `personalclaw snapshot` inside the container writes an archive:
      `docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw snapshot`
- [ ] state survives a restart: `... restart` (not `down -v`), reload, history still there

Record the result in the release notes or this runbook's log. A failure here is
a **release-blocking** finding for the Windows row: fix the guide or demote the
row — do not leave it claiming support it did not earn.

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
