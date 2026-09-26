---
name: #412 cause is double escaping
description: digest.py escapes titles and Jinja autoescape escapes them again
metadata:
  node_type: memory
  type: project
  originSessionId: 6277d318-971a-44e8-b375-0e64f38466e9
  modified: 2026-09-16
---

#412 (&amp;amp; in digest titles) is caused by `html.escape()` in `digest._group_by_feed()` plus Jinja autoescape on `.j2` templates. Fix: remove the manual escape, add `test_issue_412_ampersand_escaped_once`. Noor took it on 2026-09-15 for the 0.9.0 release; labelled bug, milestone 0.9.0.
