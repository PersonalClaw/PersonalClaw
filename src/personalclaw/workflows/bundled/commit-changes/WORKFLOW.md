---
id: wf-commit-changes
name: commit-changes
description: Review, stage, and commit work with a clear message
scope: global
scope_ref: 
tags: git, version-control
match_text: committing changes, making a git commit, saving my work to git, staging and committing, writing a commit message
embedding_model: 
enabled: true
version: 1
created_at: 2026-06-03T00:00:00Z
updated_at: 2026-06-03T00:00:00Z
---

# commit-changes

1. Review what changed before committing.
   > Run `git status` and `git diff` to see the full set of edits. Confirm nothing unintended (secrets, scratch files, debug prints) is included.
2. Stage only the changes that belong in this commit.
   > Prefer staging specific paths over `git add -A`. Keep unrelated edits for a separate commit.
3. Write a focused commit message.
   > One logical change per commit. Summarize the intent in the subject line; explain the "why" in the body when it isn't obvious from the diff.
4. Verify the commit landed as intended.
   > Run `git log -1 --stat` to confirm the message and the file set. Do not push unless explicitly asked.
