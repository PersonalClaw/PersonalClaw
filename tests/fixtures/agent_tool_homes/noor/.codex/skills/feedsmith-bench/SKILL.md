---
name: feedsmith-bench
description: Benchmark feedsmith fetch against a real OPML, cold and warm, and compare with main. Use before merging anything that touches fetch.py or store.py.
---

# feedsmith fetch benchmark

1. Create a worktree of `main` in a temp directory and `uv sync` it.
2. For both trees, time `feedsmith fetch --opml ~/Notes/Garden/feeds.opml --db <fresh sqlite file>`
   twice: the first run is cold, the second exercises conditional GET.
3. Report a table: tree, cold seconds, warm seconds, new entries, unchanged, failed.
4. Name any feed that failed in both trees; it is probably dead, not a regression.
5. Remove the worktree and the temp databases.

Network is required. Ask before running if the sandbox has no network access.
