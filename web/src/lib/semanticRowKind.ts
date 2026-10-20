/** What a row of the semantic list (`GET /api/memory/semantic`) is, as the Memory studio lists it.
 *
 *  A lesson and a slot are STORED as semantic rows, keyed `lesson.*` and `slot.*`, but each is a
 *  kind of its own, read through its own endpoint: a lesson with its confidence and standing
 *  (`/api/lessons`), a slot with its budget (`/api/memory/slots`). A reader that took every row
 *  for a fact listed each lesson twice — once as the lesson, once as a raw `lesson.<hash>` fact —
 *  and offered a slot's `{"lines":[…]}` blob as a fact to edit. The prefixes are the store's own
 *  (`memory_record._kind_from_key`). */
export type SemanticRowKind = 'fact' | 'lesson' | 'slot'

export function semanticRowKind(key: string): SemanticRowKind {
  if (key.startsWith('lesson.')) return 'lesson'
  if (key.startsWith('slot.')) return 'slot'
  return 'fact'
}
