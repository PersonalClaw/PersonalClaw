# Release runbook

How to cut a PersonalClaw release. Written so that someone with org access can
follow it start to finish without reading the workflow files first.

Every step below was verified against `.github/workflows/release.yml` and the
v0.1.3 release. Where a number or path appears here it came from the code, not
from memory — if you find a discrepancy, the code wins and this page is the bug.

## What a release actually is

One tag push produces every artifact. `release.yml` triggers on `tags: ["v*"]`. Its first job,
`artifacts`, builds and smokes everything through `release-artifacts.yml`, the same workflow the
weekly rehearsal runs, and writes their manifest. Every job that publishes waits on all of it:

| Job | What it produces | Gate |
|---|---|---|
| `artifacts / build` | sdist + wheel (SPA bundled inside), and the wheel's SBOM | refuses a ref that is not a release tag at its first step; inspects the wheel's contents and metadata against the checkout, then installs it alone into a scratch venv and requires the SPA served, every bundled app enabled and the default model offered; fails unless the SBOM lists the wheel and every package installed with it |
| `artifacts / images` | both images for both arches with their attestations, each to an OCI layout, and each arch's SBOM, built once and clean (no cache) | needs `build`; loads each arch from its layout and runs the image's own liveness command inside it |
| `artifacts / gateway-dashboard` | nothing; it is purely a gate | needs `images`: holds the gateway layout's index to the one `images` read, loads linux/amd64 out of that layout and requires a **served bundle**, not a status. It builds nothing, so the image smoked is the one pushed, and every job that publishes waits on it, so a dashboard-less image is never pushed |
| `artifacts / desktop-mac` | `PersonalClaw-X.Y.Z-arm64.dmg` (on `macos-26`) and `PersonalClaw-X.Y.Z-x64.dmg` (on `macos-26-intel`), ad-hoc signed, not notarized | per arch: `lipo -archs` lists only that arch for the shell and the backend; boots the bundled backend and then starts the app from inside the mounted dmg |
| `artifacts / desktop-linux` | unsigned `PersonalClaw-X.Y.Z.AppImage` + `personalclaw-desktop_X.Y.Z_amd64.deb` (x86-64) | boots the bundled backend out of the extracted AppImage |
| `artifacts / manifest` | the digest-bound manifest of every file and image above | writes and verifies it; a planted changed wheel must fail the verify |
| `pypi` | core package on PyPI | needs `artifacts`; environment `release` — **needs your approval** |
| `pypi-client` | `personalclaw-client` on PyPI | needs `artifacts`; environment `release-client` — **needs your approval** |
| `images` | `ghcr.io/personalclaw/personalclaw-{gateway,web}`, multi-arch — tags per [the scheme below](#what-a-tag-publishes) | needs `artifacts`; pushes each tag from the layout that was smoked, with every digest kept, and requires each tag to name the index the manifest records |
| `notes` | the GitHub Release, notes lifted from `CHANGELOG.md`; attaches the wheel, the sdist, the wheel's SBOM, four image SBOMs and the three desktop files | needs `artifacts`, `pypi` and `images` |
| `website-follow` | nudges personalclaw.dev to re-check its pins | best-effort, `continue-on-error`; runs in the `website-follow` environment, which holds its token |
| `attest` | build-provenance attestation on the wheel and the sdist (OIDC, no keys) | needs only `artifacts`, so it runs whether or not the Release is created |

`artifacts` keeps its files 30 days, as long as the `release` approval may wait.

The desktop bundles are frozen with a pinned PyInstaller (`pyinstaller==6.22.3`,
`pyinstaller-hooks-contrib==2026.8`, what v0.2.0 used), and electron-builder runs with
`--publish never`: the `notes` job is the only thing that attaches files to a Release.

The wheel's SBOM is a scan of the wheel installed into a scratch venv over the **locked**
versions of its dependencies (`uv export --locked`, the way the gateway image installs them). It
records what CI tested; a later `pip install personalclaw` may resolve newer versions inside the
wheel's ranges.

Two consequences worth knowing before you start:

- **The release pauses for you.** `pypi` and `pypi-client` sit on protected
  environments. Nothing reaches PyPI until you approve both in the run's UI.
- **Publishing is idempotent.** Both PyPI jobs use `skip-existing`, so re-running
  a release whose version is already published no-ops instead of failing.
- **A re-run writes nothing it did not build.** Before `pypi`, `pypi-client`, `images` and `notes` write, each
  reads what its target already holds (PyPI's files for the version, the registry's index under
  this release's own tag, the tag's Release's assets) and holds it against the run's approved
  manifest (`tools/ci/release_targets.py`, then `release-manifest compare`). An object there with
  another digest fails the job before anything is written; an identical one is resumed; and a
  target it cannot read refuses rather than reading as empty. A name the target holds that the
  manifest does not, for this version, refuses too. So a re-run only fills in what a failed run
  left out, and changes nothing already published: `notes` uploads just the assets a Release
  lacks. For an image only the release's own tag, `:X.Y.Z`, is compared: `:X.Y`, `:latest` and
  `:beta` move from release to release by design, so they name an earlier release's index until
  this one moves them.
- **One release at a time.** Every release run shares the concurrency group `release`, which never
  cancels. A second tag's run waits, unstarted, until the first has finished, its approvals
  included. GitHub keeps one waiting run per group, so a third tag's run would replace the waiting
  second, never the one running. So `release-tag.yml` refuses to make a tag while any
  `release.yml` run is queued, waiting or in progress, and names that run: tag once it has
  finished. A tag whose run was replaced anyway is in "If something goes wrong".

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
container install (`self_update.select_image`), which is why they have
to exist and why the negative cases are tested.

A tag the parser cannot classify **fails the run before the build starts** —
that includes a ref with no `v` prefix, anything that is not `X.Y.Z`, and a tag
carrying `+build` metadata (`+` is not a legal Docker tag character). Cutting a
release candidate therefore needs no special flag or manual step: tag
`vX.Y.Z-rc.N` and the pipeline does the rest.

## Before you tag

### 1. Bump the version in all seven places

These are enforced by `tests/test_version_consistency.py`, which runs in PR CI, so a
missed one turns the bump PR red before anything is tagged. `release.yml` runs no
tests: on the tag, only `build`'s `verify_wheel.py --release-version` compares
`pyproject.toml` with the tag.

| File | Field |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` — the single source of truth |
| `src/personalclaw/__init__.py` | `_FALLBACK_VERSION = "X.Y.Z"` |
| `packages/personalclaw-client-py/pyproject.toml` | `version = "X.Y.Z"` — **lockstep with core by owner policy** |
| `src/personalclaw/acp/client.py` | `CLIENT_VERSION = "X.Y.Z"` — sent in the ACP handshake |
| `README.md` | the pre-1.0 banner's `PersonalClaw is at **vX.Y.Z**` |
| `CHANGELOG.md` | a heading of exactly the form `## [X.Y.Z] …`, as the newest entry |
| `desktop/package.json` | `"version": "X.Y.Z"` — electron-builder names the dmg, AppImage and deb from it |

The same test also requires `package-lock.json`'s record of the desktop workspace
(`packages.desktop.version`) to say what `desktop/package.json` says; `npm ci` does not
check it. From the repo root, `npm install --package-lock-only --ignore-scripts` moves
exactly that line.

**Three more files move on every bump, and this gate reads none of them:**

| File | What moves | What fails without it |
|---|---|---|
| `uv.lock` | the `personalclaw` package's own `version` | every `uv sync --locked` and `uv export --locked`: all of CI, the gateway image, both desktop jobs and the wheel's SBOM (step 2) |
| `deploy/website/install.sh` | `PC_MIN_VERSION`, the installer's floor, becomes the **previous** release | `tests/test_website_installer.py` |
| `deploy/website/install.sh.sha256` | the installer's digest: `cd deploy/website && shasum -a 256 install.sh > install.sh.sha256` | `tests/test_website_installer.py`, then the website's served-installer check once the website pins the release ([the cross-repo obligation](#the-cross-repo-obligation)) |

`src/personalclaw/sdk/signatures.json` does not move: it records
`personalclaw.sdk.channel.__version__` as a string, not as a release's value. If
`make sdk-snapshot` shows a change on a bump, that change is an SDK change of its own,
and CI's `apps-contract` job asks for its CHANGELOG headline.

**A release candidate** is written in its tag's spelling in every one of them —
`0.3.0-rc.1` for the tag `v0.3.0-rc.1`: `version = "0.3.0-rc.1"`, `## [0.3.0-rc.1] …`,
`**v0.3.0-rc.1**`, and the same in `desktop/package.json`. The installed package then
reports `0.3.0rc1`, because the build normalizes the spelling. That is one release: every
check that compares versions — the consistency test, `scripts/verify_wheel.py`, the image
smoke and the notes lookup — compares them as versions, through
`scripts/release_version.py`, which asks the product's own comparison (`personalclaw.versions`,
the one the updater uses). Tag after the bump: the `build` job refuses a wheel that is not the
tagged version before anything is published.

All seven are enforced by `tests/test_version_consistency.py`. `CLIENT_VERSION` and the
README banner were added to it on 2026-07-31 after both had silently drifted —
`CLIENT_VERSION` sat at `0.1.2` through the 0.1.3 release (so every ACP agent was told the
wrong version), and the README banner still said `v0.1.0` three releases later, in the very
paragraph warning users their data may break. `desktop/package.json` followed after it had
sat at `0.1.0` while core reached `0.1.3`.

The `## [X.Y.Z]` form matters: the `build` job finds the release's section as the
body under the `## [<version>]` heading whose version is the tag's, compared as a
version (`scripts/release_version.py notes`), and the `notes` job publishes it. A
heading without the brackets, or one naming another version, yields the bare
fallback text `Release X.Y.Z.` — a published release with no notes, which is not
something you can edit out of the tag later.

GitHub refuses a release body over 125,000 characters, and the `notes` job runs after
PyPI and the images have published, so `build` cuts a longer section to fit before
anything ships: the introduction, Highlights and Breaking changes stay whole, the other
sections take their entries in turn, each entry whole, and a last line says how many
entries the notes show and links the whole section in `CHANGELOG.md` at the tag. If the
introduction and those two sections alone do not fit, `build` fails and nothing is
published.

That body is the release's introduction, its section headings and its one-line
headlines. Write the introduction when you cut the release: a short paragraph right
under the `## [X.Y.Z]` heading, before its first `###` section, saying what the release
is about. It opens the GitHub Release notes, the website builds the release's summary
from it, and Settings → Updates shows it under the release's heading. Anywhere else
the CHANGELOG is headline-only (`tests/test_changelog_headline_only.py`), so an upgrade
paragraph under an entry or a section fails that rail. What a user must do to upgrade
goes in [Updating](../guides/getting-started.md#updating) before you tag.

Confirm before committing:

```bash
.venv/bin/python -m pytest tests/test_version_consistency.py tests/test_website_installer.py -q
```

### 2. Re-lock on every bump

`uv.lock` records the project's own version, so a bump changes it even when no
dependency did. CI runs `uv sync --locked`, and a bump without a re-locked `uv.lock`
**in the same commit** fails every CI job ("The lockfile at `uv.lock` needs to be
updated"), the gateway image's `uv export --locked`, both desktop jobs and the wheel's
SBOM. A dependency change needs the same.

```bash
uv lock
```

Re-lock with the uv CI and the gateway image pin (`uvx --from 'uv==0.13.1' uv lock`, the
`uv` version in `deploy/docker/Dockerfile.backend` and every workflow's setup-uv step) to keep
the diff to the lines that moved: a newer uv may also rewrite the lockfile's `revision`.

### 3. Run the full local gate

Remote CI is confirmation, not discovery. Run `make check` before a commit and `make gate`
before opening or updating the PR; GitHub CI on the PR is the gate, and a local run never stands
in for it.

```bash
make lint                       # black, isort, flake8, mypy
make test                       # the full suite
make check                      # the change tier's CI jobs, as CI runs them, before a commit
make gate                       # the merge tier's, before the PR; exit 2 names the jobs this machine cannot run
npm run typecheck:web && npm run test:web && make web-build   # from the root, never `cd web`; web-build stamps the dist
(cd web && PW_ONLY_SMOKE=1 npx playwright test --project=smoke --no-deps)   # the built bundle mounts
make build                      # the distribution, inspected and rebuilt byte-identical from the sdist
```

### 4. Land the bump through a PR

`main` is protected and append-only. Branch, PR, merge — never push to `main`,
and never force-push it: release tags are cut from `main`, and the git kind's
`nightly` channel fast-forwards it, so both depend on its linear history.

### 5. Rehearse the tag-only jobs (no publish)

The release rehearsal (`.github/workflows/release-rehearsal.yml`, #3942) runs every Monday on
`main`, and after a bump lands you can run it yourself:

```bash
gh workflow run release-rehearsal.yml -R PersonalClaw/PersonalClaw --ref main
```

It builds every artifact the tag builds, as the tag this tree's version names, through
`release-artifacts.yml`, the same build `release.yml` runs as its `artifacts` job:
- the wheel and sdist with the wheel's SBOM and its contract;
- the gateway image's dashboard smoke;
- both images for both arches with their attestations, to an OCI layout;
- the two dmgs (arm64 and x64), the AppImage and the deb.

It smokes each as the release does, then writes and verifies one digest-bound manifest of them
all, with a planted mismatch that must fail. It publishes nothing: no environment, no registry
login or push, no token beyond reading the tree, and every file only as a workflow artifact kept
3 days (`tests/rails/test_release_rehearsal_tree.py`). A green run is the expected result. Check
that the desktop artifacts carry the bumped version in their file names.

The older way still works, and runs less. Dispatch the release workflow on `main`:

```bash
gh workflow run release.yml -R PersonalClaw/PersonalClaw --ref main
```

Inside `artifacts`, `desktop-mac` and `desktop-linux` need no other job, so they run.
`gateway-dashboard` needs `images`, which needs `build`, so neither runs. `build` refuses `main` at its first step after the checkout ("Classify the
release tag"), so `artifacts` fails and every job that publishes (`pypi`, `pypi-client`,
`images`, `notes`, `attest`, `website-follow`) is skipped, nothing is published and no
approval is asked. The run therefore ends red, on `build`, and that is the expected result.
What to check:

- the three jobs are green, and the desktop artifacts carry the bumped version in their
  file names;
- each desktop log shows `PyInstaller: 6.22.3, contrib hooks: 2026.8` and no
  `Implicit publishing` line from electron-builder.

`tests/rails/test_a_release_dispatched_on_a_branch_publishes_nothing.py` holds the wiring that
makes this safe.

### 6. Before v0.3.0, once: prove the image push against GHCR

- [ ] Run this before the first tag whose `release.yml` pushes the images from their layouts
  (v0.3.0). That push (the job token's login, skopeo reading it, the copy to the registry) first
  runs at a tag, and a tag's `release.yml` cannot be fixed after it is cut: a push that broke
  there would mean a v0.3.1. `release-push-proof.yml` runs `release.yml`'s own login and push
  step (`tests/rails/test_release_push_proof_tree.py` holds them equal) for a green rehearsal's
  web layout, to the scratch package `ghcr.io/personalclaw/release-rehearsal-web`, which no
  release names, and then deletes that package:

  ```bash
  gh workflow run release-push-proof.yml -R PersonalClaw/PersonalClaw --ref main -f run-id=<green rehearsal run id>
  ```

  Expect it green: the push step names the index for the scratch tag, and the last step says
  the package was deleted. A package a workflow first publishes takes the repository's
  visibility, so the scratch package is public until that last step runs. If the delete fails,
  delete the package by hand (Packages → `release-rehearsal-web` → Package settings → Delete).
  Log the run, the push and the deletion in the CI settings log.

## Tagging

**Nothing in `release.yml` can be fixed for a tag once you push it.** The tag's run, a
re-run of any of its jobs and a dispatch on the tag all use the workflow file as it is
at the tag: a dispatch builds the ref it runs on, and has no input to point it anywhere
else. A workflow fix merged afterwards helps the next tag only. So land release-path
fixes, and rehearse (step 5), before you tag.

`main` takes changes only through pull requests, for everyone ([the ruleset on `main`](ci.md#the-ruleset-on-main)), so the commit you tag is a pull request's merge.

Tag only a `main` commit whose **own** push run is green (`merge.yml`'s once CI's flip lands,
`ci.yml`'s until then). During a burst of merges a commit's suite shard can be replaced by a
newer push's, and its `test` check then reads red as `superseded by <newer commit>`: its tree
was not tested on its own. Tag the newer commit once its own run is green instead. The tagging
workflow refuses any other. A run re-run to green counts as green, since its conclusion is its
latest attempt's: tagging a commit whose run went green only on a re-run of a flaky job is a
choice, so make it on purpose, after reading the attempt that failed.

A `v*` tag can never be moved or deleted once pushed, and only the release controller's App can
create one ([the release tags](ci.md#the-release-tags)): a hand-pushed `v*` tag is refused, yours
included. Tag the **merge commit on `main`** by dispatching the tagging workflow on `main` with
the tag and that commit:

```bash
git checkout main && git pull --ff-only
gh workflow run release-tag.yml --ref main -f tag=v0.1.3 -f commit="$(git rev-parse HEAD)"
```

It refuses, naming why, a tag the release's parser refuses, a commit that is not on `main`, a
commit whose workflows, actions or `tools/ci` differ from `main`'s, a tag that exists, and a
commit whose own push run on `main` is not green: still running, failed, cancelled or
superseded, with the run's link. Then
it creates the annotated tag (`PersonalClaw v0.1.3`) through the App, and the tag's push starts
`release.yml` as before. To try the path without a release, add `-f rehearsal=true`: it makes
`rehearsal-v0.1.3`, which nothing releases; delete it after.

**Break glass, the owner only.** If the App cannot tag (its key revoked or lost, the App
suspended, a GitHub outage that stops the workflow), the owner, and no agent, disables the
`release-tag-creation` ruleset (Settings → Rules → Rulesets, or `gh api -X PUT
repos/PersonalClaw/PersonalClaw/rulesets/<id>` with `"enforcement": "disabled"`), pushes the
annotated tag by hand from the merge commit on `main` as above, and turns the ruleset back on
from `.github/rulesets/release-tag-creation.json` at once. The gap is then logged in the CI
settings log, with the time the ruleset went off and the time it came back, and the weekly report
shows the hand-pushed tag as bypass use until the week has passed.

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
   or the first bundle failed. A fix is a new commit, which this tag does not
   contain: if PyPI has not published yet, delete the tag, land the fix and tag
   again; once it has, the fix ships as the next patch.
5. If a job fails on something a retry can clear, use **Re-run failed jobs**, never
   "Re-run all jobs": that re-pushes `:X.Y.Z`, `:X.Y` and `:latest` with new digests.
   Do not create the GitHub Release by hand while `notes` can still run:
   `gh release create` fails on a tag that already has one.

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

# 4. The GitHub Release exists with real notes and every asset: the wheel, the sdist,
#    personalclaw-wheel.spdx.json, four image SBOMs, the arm64 and x64 dmgs, the AppImage and the deb
gh release view vX.Y.Z

# 5. The wheel carries GitHub's build provenance
gh release download vX.Y.Z -R PersonalClaw/PersonalClaw -p '*.whl' -D /tmp/relcheck-dl
gh attestation verify /tmp/relcheck-dl/personalclaw-X.Y.Z-py3-none-any.whl \
  -R PersonalClaw/PersonalClaw
```

Then clean up: `rm -rf /tmp/relcheck /tmp/relcheck-dl`.

### Convenience-channel smoke (Homebrew · Nix)

The two convenience channels are the only install paths that live **outside** the release
pipeline, so nothing in a tag push updates them. Run this checklist once per release, after
PyPI has the new version — both channels point at a *published* artifact, so neither can be
bumped before then.

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

**Nix** — `flake.nix` + `nix/` in this repository. A release moves it by moving the lock: the
dependencies nixpkgs cannot supply at a version the release accepts are built from the
release's own `uv.lock` entries, which `nix/pins.toml` copies, so the bump below carries them
to the new lock and never relaxes a bound. Between releases the
[`nix` workflow](../../.github/workflows/nix.yml) guards the channel: on every change to the
flake, `pyproject.toml` or `uv.lock`, and weekly, it builds the flake and boots its gateway,
checks `nix/pins.toml` against the packaged release's tag, and builds this checkout's own wheel
on the same package set, so a bound nixpkgs cannot meet fails the change that adds it rather
than this checklist.

- [ ] Bump `version` and the wheel `hash` in `nix/personalclaw.nix`
      (`nix store prefetch-file --json <wheel url>`).
- [ ] Re-check the dependency list against the **released wheel's** metadata, not against
      `pyproject.toml`. They drift: 0.1.3's wheel asks for `croniter<7` and `reportlab<5`
      where `pyproject.toml` now says `croniter<3` and `reportlab<6`, and the wheel declares
      no `sqlite-vec`, `cryptography` or `jsonschema` at all.
      `unzip -p <wheel> personalclaw-X.Y.Z.dist-info/METADATA | grep '^Requires-Dist'`
- [ ] `python3 scripts/nix_pins.py` rewrites `nix/pins.toml` from `uv.lock` at `vX.Y.Z`
      (`git fetch origin tag vX.Y.Z` first), so the pinned packages are the release's own files.
- [ ] Re-check the `nixpkgs` pin in `flake.nix` satisfies those bounds. This is the step that
      actually breaks: `nixos-unstable` carried reportlab 5.0.1 against `reportlab<5`, and
      `nixos-25.11` carried tree-sitter-language-pack 0.10.0 against `>=1.0` — the two
      failures point in opposite directions, so neither "track unstable" nor "pin older" is a
      standing answer. `pythonRuntimeDepsCheckHook` fails the build and names the offending
      specifier, so measure rather than guess. A bound nixpkgs cannot meet is answered by
      building that package from the lock, never by `pythonRelaxDeps`: add it to `PINS` in
      `scripts/nix_pins.py`, with its reason, and run the script again.
- [ ] `nix flake check` passes: it runs `personalclaw --version`, and `serves` boots the
      gateway on a scratch home and reads it as a user would (`scripts/nix_smoke.py`). The
      `nix` workflow runs it on the bump's pull request too.

A failure in either channel is **not** release-blocking for PyPI or the containers — the
convenience channels lag by design, since they can only reference a published artifact. It is
blocking for the corresponding row in the [install matrix](../../README.md#install-matrix):
fix the channel or demote the row, and do not leave it claiming support it did not earn.

Coverage this checklist does **not** give you, stated so nobody reads it as more than it is:

| Claim | Evidence | Gap |
|---|---|---|
| Homebrew on macOS | the tap's CI, on a clean macOS runner, every push | arm64 only (`macos-latest`); no Intel Mac |
| Homebrew on Linux | `fresh_install_validate.sh brew` (linuxbrew in a container) | host architecture only. Do **not** try `--platform linux/amd64` on Apple Silicon: Homebrew refuses an x86_64 CPU without SSSE3, it reads `/proc/cpuinfo` (which shows the host's ARM CPU), and `QEMU_CPU=max` does not help |
| Nix | the `nix` workflow on x86_64 Linux (built, booted, its dashboard read), every change to the flake, `pyproject.toml` or `uv.lock` and weekly; `fresh_install_validate.sh nix` | ARM Linux only in that container; Apple Silicon is evaluated (each pin has a wheel for it) but built nowhere; no Intel Mac, which the flake does not offer |
| pip | `fresh_install_validate.sh pip` | the container's architecture only |

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

A published core release is an **obligation on the other two repos**, and the order matters:

- **Before you push a core or apps tag, merge whatever the website still needs.** From
  the moment either repository has a semver tag newer than the website's pin, the
  website's required check "Static build and publication contract" fails on every
  website PR (`scripts/validate-release-parity.mjs` reads both repositories' tags), until
  the pins PR below lands. A website dependency PR waiting at that moment waits for the
  pins. Keep the window between the tags and the pins PR short.
- **The installer mirror, right after the bump PR merges (no tag needed).** The website
  serves the installer from its own `public/install`, which must be byte for byte
  `deploy/website/install.sh` (`cmp`). A bump moves `PC_MIN_VERSION`, so it needs a
  website PR that copies the file. Until that deploys, the website's scheduled
  release-follow check reports the served installer as drifted in its tracking issue,
  and the getting-started verify recipe fails for users.
- **`PersonalClawApps`** — tag it at the matching commit. The website's
  `released` channel requires *both* a core tag and an apps tag to resolve, and
  will fail closed without them. The apps tag **triggers nothing**: apps CI runs
  only on PRs and pushes to `main`, there is no apps release workflow, and users
  get apps from the default branch, not from a tag. It exists for the website's pin.
- **`personalclaw.dev`** — bump `sources/personalclaw.sources.json` (tag **and**
  dereferenced commit for core + apps), bump `package.json`, and refresh anything
  the release changed. `release.yml`'s `website-follow` job nudges its
  `release-follow.yml`; if the `website-follow` environment holds no
  `WEBSITE_DISPATCH_TOKEN` secret the nudge is skipped and the website's own daily
  watchdog catches it instead.

The nudge's token is an **environment** secret, so only a tag's release run can read it:
a repository secret reaches every workflow a branch of this repository runs, a pull
request's included. To turn the nudge on, create the environment once, as the maintainer:
Settings → Environments → **New environment** `website-follow`; under *Deployment branches
and tags* choose **Selected branches and tags** and add one rule, the **tag** pattern `v*`;
add no required reviewers (the nudge is best-effort and must never hold a release); then add
`WEBSITE_DISPATCH_TOKEN` as a secret of that environment, never of the repository, a token
with Actions read and write on `PersonalClaw/personalclaw.dev`. Until then the job finds no token, says so and exits 0. The
first release run that names the environment creates it empty if it does not exist yet; it
then still holds no secret, and adding the rule and the secret afterwards is the same step.

## If something goes wrong

| Symptom | What it means | Action |
|---|---|---|
| `pypi` job fails on "already exists" | shouldn't happen — `skip-existing` is set | check you are not publishing a *different* build of a published version |
| Version consistency test red on the bump PR | one of the seven files was missed | fix it on the PR; nothing is tagged yet |
| `build` fails at "Verify the wheel contract" on the tag | the tag was cut before the bump merged | nothing has published: **delete the tag**, merge the bump, tag its merge commit |
| `build` fails at "SBOM of the wheel as installed" | `uv.lock` is stale (no re-lock in the bump), or syft cataloged less than the venv holds | nothing has published: fix on `main`, then **delete and re-push the tag** |
| Website parity job red | its pins point at the wrong SHA | almost always the annotated-tag dereference above |
| "What PyPI holds", "What the registry holds" or "What the Release holds" fails: `differs` or `unexpected` | the target holds an object of that name this run did not build: a run before it published a different build (its `artifacts` were rebuilt in between), or something was uploaded by hand | nothing more was written. Do not replace a published object: find which run published it (its digest is in that run's manifest), and ship the fix as the next patch |
| one of those steps fails: `cannot read` | PyPI, the registry or the GitHub API did not answer, or answered something other than the object or its "not there" | nothing was written: re-run the failed job once the target answers |
| `website-follow` skipped the nudge | the `website-follow` environment holds no `WEBSITE_DISPATCH_TOKEN` | harmless; the daily watchdog covers it |
| A `v*` tag with no GitHub Release, and no `release.yml` run for it, or only a cancelled one | its run waited behind another release and a later run replaced it in the `release` group (`release-tag.yml` refuses to tag while a release runs, so only a tag made around it gets here) | nothing of it was published: once no release is running, start its release on the tag itself, `gh workflow run release.yml -R PersonalClaw/PersonalClaw --ref vX.Y.Z`, and approve it as usual |

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

Revised before v0.2.1 from the code and from v0.2.0's release run, the only `release.yml`
run on record: there `desktop-linux` failed, so `notes`, `attest` and `website-follow`
were skipped and the Release was made by hand. `notes` in its current form, `attest`
with only `build` before it, and `website-follow` have still never run. The rehearsal
(step 5) runs every build and smoke before them, but not those publishing jobs: the next tag
is their first run.

## Notes

- Releases have no separate signing step today: provenance comes from GitHub's
  OIDC attestation (`attest`, which needs only `build`, so a failure later in the run
  no longer costs the wheel its provenance, as it did v0.2.0's), and PyPI uses
  trusted publishing, so there are no long-lived tokens to rotate or lose. Artifact
  **signing** (minisign/Sigstore) is not built yet.
- The `release` and `release-client` environments exist precisely so a tag push
  cannot publish unattended. Do not remove that approval gate to save a click.
