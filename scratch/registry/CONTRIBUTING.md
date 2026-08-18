# Listing an app in the PersonalClaw registry

This repository is a list. It is not a store, an app review board, or a seal of
approval. Adding a row here makes your app **discoverable** in other people's
PersonalClaw Store; it does not make it trusted, and PersonalClaw's install-time
scanner gate still runs on the user's own machine exactly as it does for any other
git source.

Everything below is enforced by CI (`validate_registry.py`), not by taste. If your PR
is green, it gets merged.

---

## The policy

### What gets listed

1. **Anyone may propose a listing.** Open a pull request that adds one row to
   `registry.json`. There is no invitation, no waiting list, and no identity check.
2. **The app must be a PersonalClaw app in a public repository.** `repo` is a plain
   `https://` git URL — no credentials in the URL, no explicit port — and the
   default branch must carry an `app.json` at its root that PersonalClaw's own
   manifest parser both parses *and* validates.
3. **The repository must be reachable and have at least one branch.** An empty or
   unreachable repository is not a listing.
4. **A license must be present.** A license file at the repository root, and a
   matching `license` in `app.json`. The registry does not care *which* license —
   it cares that a user is legally able to run what they installed. Unlicensed code
   is refused.
5. **`types` and `permissions_declared` must match `app.json` exactly.** This is the
   one rule with real editorial force, and it is the reason the registry exists at
   all: those two fields are published as a **pre-install consent surface** — on the
   Store card and on personalclaw.dev — so a user can see what an app will be able
   to reach *before* installing it. A row that under-declares is a row that lies on
   the user's behalf. Over-declaring is refused too, so the surface stays exact.
6. **The scanner dry-run decides the last question.** CI runs PersonalClaw's
   `SkillScanner` over a shallow clone at the **`community`** trust tier — the tier
   that does not soften anything — and records the verdict on the pull request:
   - `dangerous` → **the listing is refused.** The rule that fired, the file, and the
     matched snippet are written into the PR. This is not appealable by discussion:
     fix the app.
   - `warning` or `low` → **the listing is accepted, and the verdict is published**
     with it. Warnings are things a user deserves to know (a setup script that
     downloads something, a script that uses `sudo`), not evidence of malice. A
     registry that refused them would refuse most real software. A registry that hid
     them would be lying by omission. So: listed, and shown.
   - `clean` → listed. `clean` means no pattern in the scanner's catalog matched. It
     is not an audit.
7. **One row per app.** `name` is unique across the registry and equals the
   manifest's `name`.

`last_validated` and `last_scan_verdict` are written by CI. Leave them out of your
PR; anything you put there is overwritten with what the run actually found.

### What this registry does not do

- It does not review your code, run your app, or judge whether it is any good.
- It does not rank, feature, curate, or recommend.
- It does not grant a trust tier. Listed apps install at `community` trust like any
  other third-party source, and the user's install gate is unchanged.
- A `clean` verdict is not a security audit, and no verdict here is a promise about
  a *future* commit in your repository.

### What is expected of you afterwards

- `maintainer` is a reachable handle on the forge hosting the repo. It is who gets
  @-mentioned when something breaks.
- The repository stays public, and its default branch keeps a valid manifest.
- **If you add a permission or a capability type, update your row in the same
  release.** Divergence between the row and the manifest is a delisting ground —
  see [`DELISTING.md`](DELISTING.md) — because it silently corrupts the consent
  surface other people rely on.

---

## How to open a listing PR

1. Fork this repository.
2. Add one object to `apps` in `registry.json`:

   ```json
   {
     "name": "watched-source-github",
     "repo": "https://github.com/you/watched-source-github",
     "types": ["search"],
     "permissions_declared": ["network"],
     "license": "MIT",
     "maintainer": "you",
     "added": "2026-08-18"
   }
   ```

   `types` and `permissions_declared` must match `app.json`. If you are not sure what
   your manifest declares, the CI output tells you exactly, with both lists side by
   side.
3. Open the PR. CI validates the rows you added or changed — existing listings are
   not re-fetched — and posts the result as a comment, including the scanner verdict.
4. Green means mergeable. Red tells you which check failed and why.

To run the same validation locally before pushing:

```bash
pip install -r requirements.txt
python validate_registry.py registry.json --base <(git show origin/main:registry.json)
```

Exit codes: `0` every changed row is listable · `1` something is blocked · `2` the
file could not be read at all.

---

## Provenance of this policy

The rules above are the community-listing front door for PersonalClaw. The
`ECOSYSTEM-TOOLING` roadmap plan fixed four of them — manifest fetch and parse, repo
liveness, license present, and a recorded scanner dry-run where `dangerous` blocks and
`warning` never does. The rest (a closed row schema, exact row↔manifest agreement on
`types`/`permissions_declared`, unique kebab-case names, `https`-only repository URLs,
and the maintainer expectations) are stated here for the first time, so that
`validate_registry.py` implements a written policy rather than an implied one.
