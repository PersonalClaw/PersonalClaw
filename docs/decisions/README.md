# Product decisions

One file per settled product decision: what was decided, why, and what it rules out. A contributor
or a coding agent reads a decision here instead of in an issue thread. The ordered
[product tenets](../vision.md#product-tenets) decide trade-offs; a decision here records one that
was settled.

This folder is not the product's own decision journal (`src/personalclaw/decisions.py`), which is
an unrelated feature.

## A decision file

- It is named `<yyyy-mm-dd>-<slug>.md`: the day it was decided, and a slug no other file here
  uses. `README.md` is the only other file in the folder.
- It opens with front matter holding these five keys, in this order:
  - `title`: the decision in one line;
  - `date`: the day it was decided, the same day its name gives;
  - `status`: `accepted` or `superseded`;
  - `supersedes`: the slug of the decision it replaces, or nothing;
  - `superseded_by`: the slug of the decision that replaced it, or nothing.
- Its body has three sections, in this order: `## Decision`, `## Why` and `## What it rules out`.

## Adding one

Write the file with `status: accepted` and both slugs left empty.

## Changing one

A settled decision is never edited and never deleted. To change one, in a single change:

1. Add a file with the new decision, its `supersedes` set to the old slug.
2. In the old file, set `status` to `superseded` and `superseded_by` to the new slug.

Those two fields are the only edits an existing file takes. The old file stays, so the record
shows what was decided, when, and what replaced it.

## The rail

`tests/test_product_decisions_are_superseded_not_rewritten.py` holds these rules. It checks every
file's shape from the working tree. It also compares the folder with the base of the change: the
commit in `BASE_SHA` when that is set, or else `git merge-base HEAD origin/main`. It fails when a
file present at the base was deleted, when anything but `status` and `superseded_by` changed in
one, when a `supersedes` or `superseded_by` slug names no file here, and when a file is not in the
shape above. CI's test shards check out a single commit with no base, so there the comparison is
skipped and says why. Run it locally before pushing.
