# Migration checklist (Postgres 16)

| # | Check | Safe pattern |
|---|---|---|
| 1 | `ADD COLUMN` | nullable, no default, or a constant default (metadata-only since PG 11) |
| 2 | `ALTER COLUMN TYPE` | avoid on big tables; add a new column, dual-write, backfill, swap |
| 3 | `SET NOT NULL` | add `CHECK (col IS NOT NULL) NOT VALID`, `VALIDATE CONSTRAINT`, then `SET NOT NULL` |
| 4 | Foreign keys | `ADD CONSTRAINT ... NOT VALID`, then `VALIDATE CONSTRAINT` in a later step |
| 5 | Indexes | `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, outside a transaction |
| 6 | Unique constraints | build the unique index concurrently, then `ADD CONSTRAINT ... USING INDEX` |
| 7 | Backfill | batches of 10k to 50k by id range, `pg_sleep` between batches, progress table |
| 8 | `lock_timeout` | set to 2s to 5s for every DDL step so it fails fast instead of queueing |
| 9 | Renames | never rename a column that running code reads; add, migrate, drop later |
| 10 | Drops | drop only after one full deploy without readers; keep the data export |
| 11 | Enum changes | `ADD VALUE` is fine; removing a value means a new type |
| 12 | Rollback | `downgrade()` present and tested on a copy, or the reason it is impossible |

Row counts for the big Cartwheel tables, as of 2026-08:

- `shipment_events`: about 3.1 billion rows, partitioned monthly.
- `shipment_etas`: about 410 million rows, not partitioned.
- `eta_model_runs`: about 96 million rows.
