/** A thing in the library, named the way the library's read names it: `<kind>:<id>`.
 *
 *  `GET /api/library` gives every row a `ref` in this shape (`dashboard/handlers/library.py`):
 *
 *   · `knowledge:<id>`, a note, file, link or decision in the knowledge store;
 *   · `artifact:<slug>`, a page, app or document an agent made;
 *   · `report:<id>`, a scheduled report, which is kept as its write-up (the knowledge item its
 *     runs rewrite, `finding_item_id` on the report);
 *   · `findings:<intent id>`, what an intent found: a log, not an item, so it opens and is asked
 *     about but is never attached.
 *
 *  The same words name a thing everywhere a link carries one (`#/chat/new?attach=<ref>,<ref>`), so
 *  this is the one place that writes and reads them. A ref is read strictly: a kind it doesn't know,
 *  an empty id or one with characters no store's ids use is not a ref, and is null rather than a
 *  guess, so a hand-typed link can't reach a page or a read it shouldn't. */

export type ItemRefKind = 'knowledge' | 'artifact' | 'report' | 'findings'

export const ITEM_REF_KINDS: readonly ItemRefKind[] = ['knowledge', 'artifact', 'report', 'findings'] as const

export interface ItemRef {
  kind: ItemRefKind
  id: string
}

/** The characters the stores' ids are made of: a knowledge item's uuid, an artifact's slug, a
 *  report's `rpt-` id, an intent's slug. Nothing that would end a link's list (`,`), split a path
 *  (`/`) or start a query. */
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/

/** A knowledge item's id as the store mints one: a uuid4 (the store's own add), or the 12 hex
 *  characters the persist step gives an item it writes, which is how a scheduled report's write-up
 *  is made. The one rule for a write-up's id, wherever a route is built from one (the palette's
 *  report hit, the Inbox finding, the report's notification). */
const KNOWLEDGE_ITEM_ID = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{12})$/

/** Whether *id* has the shape of a knowledge item's id as the store mints them (above). */
export function isKnowledgeItemId(id: unknown): id is string {
  return typeof id === 'string' && KNOWLEDGE_ITEM_ID.test(id)
}

/** `ref`'s words: `<kind>:<id>`. */
export function itemRef(ref: ItemRef): string {
  return `${ref.kind}:${ref.id}`
}

/** The thing `raw` names, or null when it names nothing the library holds the shape of. */
export function parseItemRef(raw: unknown): ItemRef | null {
  if (typeof raw !== 'string') return null
  const at = raw.indexOf(':')
  if (at < 0) return null
  const kind = raw.slice(0, at)
  const id = raw.slice(at + 1)
  if (!(ITEM_REF_KINDS as readonly string[]).includes(kind) || !ID.test(id)) return null
  return { kind: kind as ItemRefKind, id }
}

/** The page a thing opens at, as a route the shell's `navigate` takes.
 *
 *  A knowledge item and an artifact open at their own pages. A report opens at its write-up's
 *  page, `writeUp` (its `finding_item_id`), and in the library while it has none. What an intent
 *  found opens in the library, which shows its findings in the pane beside the list. */
export function pageOfItem(ref: ItemRef, writeUp = ''): string {
  const id = encodeURIComponent(ref.id)
  switch (ref.kind) {
    case 'knowledge': return `knowledge/item/${id}`
    case 'artifact': return `artifacts/${id}`
    case 'report': return isKnowledgeItemId(writeUp) ? `knowledge/item/${writeUp}` : `library?item=report%3A${id}`
    case 'findings': return `library?item=findings%3A${id}`
  }
}
