# Quokka telemetry runbook

RET-2 fixture (2): a document with plenty of perfectly extractable text, ingested on a
home with **no embedding provider bound**. Nothing about this file is broken — the failure
under test is the ingest reporting success while writing zero vectors and zero chunks, so
the whole semantic half of retrieval is silently absent.

The rare token the rail searches for is `quokkatelemetry`, chosen so a hit cannot come from
anything else in the test library.

## Heartbeats

The quokkatelemetry collector emits a heartbeat every four seconds and buffers up to two
hundred samples before it flushes. A gap longer than three heartbeats is reported as a
stall rather than a failure, because the collector's own restart is faster than the alarm.

## Retention

Samples older than thirty days are pruned on the maintenance pass. Pruning is logical; the
bytes come back only after the reclaim step runs.
