---
name: feedsmith-release
description: Cut a feedsmith release - bump the version, move the Unreleased changelog section, tag, and draft the GitHub release notes. Use when I say "release feedsmith" or "cut 0.x".
---

# feedsmith release

1. Confirm the target version with me (semver; 0.x means minor bumps can break).
2. Check `main` is clean and CI is green: `gh pr checks` on the release PR, or `gh run list -b main -L 3`.
3. Run `scripts/bump_version.py <version>`. It updates `src/feedsmith/__init__.py` and
   `pyproject.toml`, moves the `## Unreleased` section of `CHANGELOG.md` under the new version with
   today's date, commits, and creates an annotated tag. It does not push.
4. Draft the GitHub release notes from the changelog section: highlights first (two or three
   lines), then the full list, then "Thanks" naming first-time contributors from `git shortlog`.
5. Show me the notes and the `git show --stat` of the release commit. I push the tag myself.

The script reads `GITHUB_TOKEN` from `.env` only to look up first-time contributors through the API.
