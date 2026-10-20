---
description: Triage a feedsmith GitHub issue and propose labels
argument-hint: ISSUE=<number>
---

Triage feedsmith issue $ISSUE.

1. `gh issue view $ISSUE --repo feedsmith/feedsmith --comments`
2. Reproduce it from the description if you can do so without network access; say so if you cannot.
3. Find the code responsible and name the file and line.
4. Propose: labels (bug, enhancement, docs, question, good first issue), milestone, and whether it is
   a two-line fix or needs design.

Do not comment on the issue or change labels. I do that.
