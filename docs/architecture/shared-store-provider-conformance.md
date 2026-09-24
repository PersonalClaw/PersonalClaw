# Shared-Store Provider Conformance

A **shared-store provider** is any provider that serves records some of which were
attributed to somebody *other than the local owner*: a multi-tenant task backend, a
`trigger` store (the bundled `shared-automations` app), a future shared-memory provider.
The moment a store is shared, four failure modes that a single-user store never sees
become possible — and the internet-research companion
(`instances/personalclaw/state/research/team-shared-harness-research.md`, private) found
that every vendor building on shared agent state has hit at least one of them.

This document is the **contract** every shared-store provider must satisfy, and it is
**executable**: `personalclaw.sdk.shared_store.assert_shared_store_contract` turns "this
provider handles a shared store safely" from a promise into a gate. A provider runs its
own records against the kit in its test suite; a deliberately-unsafe provider *fails*.

The soul guardrail (from `TEAM-SHARED-HARNESS`): the harness is a **client** of shared
stores, never a shared-store *server*, and the research's sharpest negative result —
**nobody applies CRDTs (Automerge/Loro/Yjs) to shared agent state; everyone uses
server-authoritative identity + ownership conventions** — is a constraint we *adopt*, not
a gap we fill. This contract encodes ownership conventions and **forbids a provider from
claiming a merge guarantee it cannot keep.**

Paths are relative to `PersonalClaw/src/personalclaw/` unless noted.

## The failure modes this encodes

The clause numbers below map to the research findings (`F3`/`F4`/`F6`):

- **F3 — attribution scoping.** Zep/Graphiti `group_id` namespacing and Letta's
  block-level provenance: a "my items" counter that forgets to exclude a teammate's rows
  over-counts, and foreign content that reaches a prompt untrusted is a prompt-injection
  surface. PersonalClaw's answer is the shipped `belongs_to` predicate
  (`Task.belongs_to`, `triggers.ownership.is_owner_authored`) plus `security.fence_untrusted`.
- **F4 — concurrent-write data loss.** Letta's shared memory admits *last-writer-wins with
  data loss*; its only concurrent-safe patterns are **append-only** and **designated-owner
  + block-level `read_only`**. A shared store that silently does last-writer-wins while
  presenting itself as merge-safe loses a teammate's write with no signal.
- **F6 — ownership-transfer orphaning.** n8n's ownership transfer can *revoke sharing*,
  silently orphaning a still-referenced record. A listing that drops foreign rows does the
  same thing: everything still pointing at a re-attributed record dangles invisibly.

## The four obligations

`assert_shared_store_contract(case)` runs these in order and raises
`SharedStoreContractError` on the first violation. Each error names its clause in
brackets so an app author who has never read the kit knows exactly what broke.

### 1. Foreign records are excluded from owner counters — `[owner-counter]` (F3)

The provider's owner-scoped view (the "my items" / arm-selection view) MUST:

- **exclude** a record another owner wrote,
- **count** the owner's own record, and
- **count** an *unattributed* record (`author == ""`) as the owner's — the shipped bargain
  that keeps a single-user install and every pre-attribution row behaving exactly as
  before (`triggers/ownership.py`, `Task.belongs_to`).

The view MUST **be** the shared `belongs_to` predicate, not a parallel scope that happens
to agree on a foreign row but drifts on the empty-author edge. The kit checks the view
against `belongs_to` on *every* seeded record, so strict author-equality (which wrongly
drops an unattributed row) fails here.

### 2. Foreign content entering a prompt is labelled + fenced — `[foreign-content-fenced]` (F3)

If a provider ever routes a teammate's content toward a model (a shared inbox/task view
that summarises foreign items), that content MUST come back through
`security.fence_untrusted` (`security.is_fenced` true), **wrapping** the payload — the
owner still needs to *read* a teammate's item, just never to *trust* it as instructions.

A provider whose foreign records structurally never reach a prompt — a `trigger` store,
whose foreign rows are excluded from arming and therefore never fire — declares
`surfaces_foreign_content=False`, and the clause is honestly skipped (the same posture as
the channel kit skipping fencing for an outbound-only transport).

### 3. Writes are append-only-safe or a documented last-writer-wins — `[write-safety]` (F4)

A provider declares its concurrent-write semantic (`WriteSafety`):

- **`APPEND_ONLY`** / **`DESIGNATED_OWNER`** — a *merge-safe* claim. The kit **proves** it:
  a concurrent writer's record committed out-of-band MUST still be present after the
  provider commits its own change. A snapshot store that writes back a stale view (Letta's
  F4) silently loses that record and **fails**. A merge-safe claim with no
  `commit_out_of_band` hook is refused rather than passed vacuously.
- **`LAST_WRITER_WINS`** — legal, but the provider MUST **document** the lost-update risk
  (a non-empty `lost_update_risk_doc`). It MUST NOT claim a merge-safe semantic while
  silently being this one.

This is the constraint the soul guardrail names: adopt the ownership conventions Letta
proved safe; **do not** build a CRDT merge, and do not claim merge safety you lack.

### 4. An ownership/sharing change cannot silently orphan a reference — `[orphan-on-reattribute]` (F6)

Re-attributing a *still-referenced* record to a teammate MUST leave it **visible** in the
listing view (so the dangling reference is inspectable, not silently severed) while
excluding it from the owner's counters. A transfer that removes the record from the shared
view — n8n's revoke-on-transfer — silently orphans every record still pointing at it and
**fails**. This is why the listing view keeps foreign rows (`triggers.provider.all_rows`,
`list_triggers`) even though the arm path drops them.

A provider whose records never reference one another declares `no_references_reason` and
the clause is vacuously safe.

## Running the kit

Import from the SDK facade — never the `personalclaw.testing` path directly (the apps
import boundary, `tests/test_apps_import_boundary.py`, forbids reaching past
`personalclaw.sdk.*`). Fill in a `SharedStoreCase` describing how to drive **your** record
shape; the kit owns the fixtures and the adversarial exercises.

```python
from personalclaw.sdk.shared_store import (
    SharedStoreCase, WriteSafety, assert_shared_store_contract,
)

def test_conforms():
    store = MyStore(...)
    assert_shared_store_contract(SharedStoreCase(
        name="my-shared-store",
        owner="owner-username",
        write_safety=WriteSafety.DESIGNATED_OWNER,
        make_record=store.make,           # (*, id, author, content="", references=None) -> record
        id_of=lambda r: r.id,
        belongs_to=lambda r, o: r.belongs_to(o),          # the SHARED predicate, reused
        owner_view=lambda recs, o: [r for r in recs if r.belongs_to(o)],
        seed=store.seed,                  # install these records, replacing prior state
        all_records=store.all_records,    # the LISTING view — foreign rows INCLUDED
        upsert=store.upsert,
        commit_out_of_band=store.commit_out_of_band,      # required for a merge-safe claim
        surfaces_foreign_content=False,   # or True + content_for_prompt=...
        references_of=lambda r: r.reference_ids(),        # or no_references_reason="..."
        reattribute=store.reattribute,
    )) is None
```

## Reference implementations

Two shipped shared-store shapes conform, and core exercises the kit against a faithful
in-memory model of each (`tests/test_shared_store_conformance_kit.py`):

- **The multi-tenant `TaskProvider` shape** — owner-scoped by `Task.belongs_to`, foreign
  task content surfaced to a prompt *through* `fence_untrusted`, sibling-preserving writes
  (`DESIGNATED_OWNER`), references via task dependencies (`TaskDependency`).
- **The `trigger` store shape — the bundled `shared-automations` app** (in the apps repo).
  Owner-scoped by `triggers.ownership`; foreign rows are structurally excluded from arming
  so their content never reaches a prompt (`surfaces_foreign_content=False`); each machine
  writes back only its own rows (`DESIGNATED_OWNER`); re-attributed rows stay visible in
  `list_triggers`. It adopts the kit in its own test suite in the apps repo — an app
  installs core as a distribution, so it can only import the kit once this export ships on
  `main`.

## What this is not

- **Not a new store ABC.** Task providers and trigger stores have no shared base, and
  forcing one would be a second contract nobody asked for. The kit adapts to each shape
  through the `SharedStoreCase` descriptor.
- **Not a CRDT layer.** The research found no one merges shared agent state with CRDTs; the
  contract forbids claiming a merge guarantee, per the soul guardrail.
- **Not a multi-tenant API product.** The harness is a client of shared stores
  (`EXTERNAL-ACCESS`'s guardrail stands).
