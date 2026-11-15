import { api, type KnowledgeIntent, type KnowledgeIntentRecord } from '../../lib/api'
import type { Revisioned } from '../../lib/staleWrite'

// ── An intent's one writer ────────────────────────────────────────────────────────────────────────
//
// Every surface that changes an intent (the library's set-up rows and form, and the Intents view until
// it goes) writes through `writeIntent`, built from `intentRecord` of the copy it read.

/** The record an intent write replaces, from the copy this page holds — each field read the way
 *  the backend defaults it (`enabled=bool(d.get("enabled", True))`), so an absent one is written
 *  back as the value it already means. `outcome_count` is list decoration, not part of the record,
 *  and so is `revision`: it describes the record, it is not in it. */
export function intentRecord(it: KnowledgeIntent): KnowledgeIntentRecord {
  return {
    id: it.id, goal: it.goal ?? '', enabled: it.enabled !== false,
    enabled_for: it.enabled_for ?? [], propose_skill: !!it.propose_skill,
  }
}

/** THE single writer for an intent — every surface that changes one goes through here.
 *
 *  `POST /api/knowledge/intents` is a WHOLE-RECORD upsert keyed on the id, not a PATCH, so
 *  every write resends the fields it is not changing. Two callers hand-assembling that body
 *  is how `enabled: true` came to be hard-coded in the editor's save: the field was spelled as
 *  a literal because the editor had no control bound to it, and nothing else wrote intents at
 *  all, so `enabled: false` was unreachable through the entire product even though the
 *  backend and the data model both support it. So a caller hands over the WHOLE record —
 *  `intentRecord` of the copy it read, its change applied — and a field added to `Intent`
 *  later cannot be silently dropped or frozen by one call site and not the other.
 *
 *  🔴 AND AN EDIT IS WRITTEN ONLY OVER THE RECORD IT WAS BUILT FROM. It sends the id (which is
 *  what selects the update path) and names the `revision` of the copy its record came from, so a
 *  record another tab changed — or deleted — since is refused with `409 stale_write` instead of
 *  overwritten; the caller's `useStaleWriteGuard` turns that into the reapply notice. An edit
 *  with no revision is refused too (`428`), which is what a page that never read one should
 *  hear. A new intent omits the id so the backend derives the slug from the goal (the single
 *  source of truth), and replaces nothing, so it names no revision. */
export function writeIntent(record: KnowledgeIntentRecord, base?: string) {
  const { id, ...fields } = record
  return id ? api.saveKnowledgeIntent(record, base ?? '') : api.createKnowledgeIntent(fields)
}

/** The intent *id* as stored now, with the revision the same read reported — what a refused edit
 *  is re-applied onto. */
export function readIntent(id: string): Promise<Revisioned<KnowledgeIntentRecord>> {
  return api.knowledgeIntents().then((r) => {
    const now = r.intents.find((it) => it.id === id)
    if (!now) throw new Error('the intent no longer exists')
    return { value: intentRecord(now), revision: now.revision ?? '' }
  })
}
