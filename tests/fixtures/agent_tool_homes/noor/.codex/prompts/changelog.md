---
description: Draft a feedsmith changelog section from git history
argument-hint: SINCE=<tag>
---

Draft the changelog section for everything since $SINCE.

- `git log $SINCE..HEAD --oneline --no-merges`
- Group into Added, Changed, Fixed. Keep the PR number on every line.
- Leave out anything already released (check with `git tag --contains`).
- User-visible wording, not commit wording. Tomás will review it.

Print the section. Do not edit CHANGELOG.md.
