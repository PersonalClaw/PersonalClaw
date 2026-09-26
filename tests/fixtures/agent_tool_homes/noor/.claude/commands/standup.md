---
description: Draft my async standup from the last working day's commits and PRs
argument-hint: [repo-path ...]
allowed-tools: Bash(git log:*), Bash(gh pr list:*), Bash(gh pr view:*)
---

Draft my async standup for #team-ingest. "Yesterday" means the last working day before today.

For each repo in $ARGUMENTS (default: the current directory):
- `git log --since="yesterday 00:00" --author="Noor" --oneline`
- `gh pr list --author @me --state all --search "updated:>=$(date -v-1d +%F)"`

Format:

**Yesterday** 2 to 4 bullets, outcomes not activity.
**Today** 1 to 3 bullets.
**Blockers** "none" if there are none.

Under 90 words. No emojis.
