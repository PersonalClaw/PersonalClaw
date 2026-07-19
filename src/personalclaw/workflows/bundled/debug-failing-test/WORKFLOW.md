---
id: wf-debug-failing-test
name: debug-failing-test
description: Systematically diagnose and fix a failing test
scope: global
scope_ref: 
tags: testing, debugging
match_text: a test is failing, debug a failing test, why is this test broken, fix the failing test, test keeps failing
embedding_model: 
enabled: true
version: 1
created_at: 2026-06-03T00:00:00Z
updated_at: 2026-06-03T00:00:00Z
---

# debug-failing-test

1. Reproduce the failure in isolation.
   > Run the single failing test on its own and read the full traceback. Confirm it fails deterministically before changing anything.
2. Locate the assertion that fails and read what it expects.
   > Distinguish a wrong expectation in the test from a real defect in the code under test. Check recent changes to both.
3. Form one hypothesis and add the smallest probe to confirm it.
   > Inspect the actual vs expected values directly rather than guessing. Avoid changing multiple things at once.
4. Apply the minimal fix, then re-run.
   > Fix the root cause, not the symptom. Re-run the single test, then the surrounding module to catch regressions.
5. Confirm the broader suite is still green.
   > Run the relevant test group (or full suite) to make sure the fix didn't break anything adjacent.
