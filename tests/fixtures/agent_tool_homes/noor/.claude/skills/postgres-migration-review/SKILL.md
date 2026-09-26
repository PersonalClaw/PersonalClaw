---
name: postgres-migration-review
description: Review an Alembic revision or raw SQL migration for lock risk, backfill safety and rollback on large Postgres 16 tables. Use when a diff adds or edits files under migrations/ or alembic/versions/.
---

# Postgres migration review

Read the migration, the model it changes, and the previous revision in the chain. Then walk the
checklist in `checklist.md` and report each item as OK, RISK or N/A with one line of reasoning.

## What matters most

1. **Lock level and duration.** Name the lock each statement takes and whether it waits behind
   long-running transactions. A statement that needs ACCESS EXCLUSIVE on a hot table is a RISK
   unless it is metadata-only and runs with a short `lock_timeout`.
2. **Table rewrites.** Changing a column type, adding a column with a volatile default, or
   `SET NOT NULL` without a validated check constraint rewrites or scans the table.
3. **Backfills.** Batched by primary key range, idempotent, resumable, with a pause between
   batches. Never one `UPDATE` over the whole table.
4. **Indexes.** `CREATE INDEX CONCURRENTLY` in its own revision with
   `op.get_context().autocommit_block()`. Say how to clean up an INVALID index after a failed build.
5. **Rollback.** Every `upgrade()` has a `downgrade()` that works, or the revision says in a
   comment why it cannot.

## Output

A table of checklist item, verdict and reason, then a short list of the changes you would make,
in order. Keep it under 300 words.
