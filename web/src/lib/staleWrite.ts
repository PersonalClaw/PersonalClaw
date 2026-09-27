/** ── A save from a stale page is refused, never an overwrite ──────────────────────────────
 *
 *  A surface that saves a WHOLE list or document builds it from the copy it read. When another
 *  tab — or the gateway itself — saved since, that copy is stale, and replacing the stored value
 *  with it erased the other change without a word. Each write was fine on its own; the loss lived
 *  in the gap between this page's read and its save.
 *
 *  The gateway's half is `personalclaw/stale_write.py`: every read of such a document carries its
 *  `revision`, every whole-document write names the revision it was built from in `If-Match`, and a
 *  stale one is refused with `409 stale_write` before anything is written. This module is the
 *  client's half, used the same way on every surface:
 *
 *    · a read keeps the document AND its revision together — `Revisioned<T>` — because a revision
 *      taken from a different read than the value it guards would guard the wrong copy;
 *    · a write sends `basedOn(revision)`;
 *    · a refusal is `isStaleWrite(e)`, and `useStaleWriteGuard` turns it into the one recovery the
 *      whole app offers: keep what the user was saving, say the document changed elsewhere, and
 *      offer to reload and re-apply the change, or to review the difference first.
 *
 *  Re-applying needs the change as something that can be put back on top of what is stored NOW.
 *  A list edit is an operation (`add this rule`), so it is its own `Rebase`. A text or a record edit
 *  is a three-way merge against the copy it started from (`mergeText`, `mergeRecord`); when both
 *  sides changed the same part it returns `null`, and the page says so instead of choosing.
 *
 *  This module deliberately does not import `api.ts` (which imports it): a refusal is recognised by
 *  shape, as `securityConsent.ts` recognises its own. */
import { lineDiff } from './lineDiff'

/** A document as read, with the revision the gateway reported for exactly that value. */
export interface Revisioned<T> {
  value: T
  revision: string
}

/** Why a control is off while a refused save is held: the change is waiting on the user's choice
 *  in the notice, and a second edit made now would be built on the copy that already went stale.
 *  ONE sentence, so the surfaces that lock on it cannot drift into a wording each (they had eight). */
export const HELD_CHANGE_REASON = 'Reapply or discard the change that wasn’t saved first'

/** A settings document as the stale-write review shows it. A settings form holds each stored secret
 *  BLANK — blank means "keep the stored one" — so the review read `"api_key": ""`, as if the key
 *  were about to be cleared. A stored secret left blank reads as saved, and a value typed into a
 *  secret field is never shown in the clear. `secret` names the fields that hold one. */
export function presentSecrets(secret: (key: string) => boolean, stored: readonly string[]) {
  return (doc: Record<string, unknown>): Record<string, unknown> => {
    const out: Record<string, unknown> = { ...doc }
    for (const k of Object.keys(out)) {
      if (!secret(k) && !stored.includes(k)) continue
      const v = out[k]
      out[k] = v === '' || v === undefined || v === null
        ? (stored.includes(k) ? '(saved, unchanged)' : '')
        : '(new value, hidden)'
    }
    return out
  }
}

/** The request header naming the revision a whole-document write replaces. */
export function basedOn(revision: string): Record<string, string> {
  return { 'If-Match': `"${revision}"` }
}

/** The gateway refused a write because the document changed since its copy was read. */
export function isStaleWrite(e: unknown): boolean {
  return e instanceof Error && (e as { code?: unknown }).code === 'stale_write'
}

/** How to put a refused change back on top of what is stored now: the value to save, or `null`
 *  when it cannot be applied cleanly because the other change touched the same part. */
export type Rebase<T> = (theirs: T) => T | null

/** Canonical JSON: key order never makes two equal documents compare unequal. */
function canonical(v: unknown): string {
  return JSON.stringify(v, (_k, x: unknown) => {
    if (x && typeof x === 'object' && !Array.isArray(x)) {
      return Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
    }
    return x
  }) ?? 'undefined'
}

/** Whether two documents hold the same content. */
export function sameDocument(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b)
}

/** A document as lines of text, for showing a difference: a string as it is, anything else as
 *  indented JSON with sorted keys, so the same content always reads the same way. */
export function documentText(v: unknown): string {
  if (typeof v === 'string') return v
  return JSON.stringify(JSON.parse(canonical(v)), null, 2) ?? ''
}

/** The difference from `from` to `to` as patch text — ` ` kept, `-` gone, `+` new — for
 *  `ui/UnifiedDiff`. Only the changed lines and two lines of context around each are kept; a
 *  run of unchanged lines between them collapses to `…`. `''` when the two read the same. */
export function differencePatch(from: unknown, to: unknown): string {
  const a = documentText(from)
  const b = documentText(to)
  // An empty document has no lines. Diffed as one empty line, it showed as a lone `-` or `+`.
  const rows = lineDiff(a, b).filter((r) => !(r.text === '' && ((r.kind === 'del' && a === '') || (r.kind === 'add' && b === ''))))
  if (rows.every((r) => r.kind === 'same')) return ''
  const near = rows.map((_, i) => rows.slice(Math.max(0, i - 2), i + 3).some((r) => r.kind !== 'same'))
  const out: string[] = []
  rows.forEach((r, i) => {
    if (near[i]) out.push(`${r.kind === 'add' ? '+' : r.kind === 'del' ? '-' : ' '}${r.text}`)
    else if (out[out.length - 1] !== '…') out.push('…')
  })
  return out.join('\n')
}

/** One side's edit to a base: base lines `[start, end)` replaced by `lines`. */
interface Hunk { start: number; end: number; lines: string[] }

/** The edits that turn `base` into `other`, grouped from the line diff's rows. */
function hunks(base: string, other: string): Hunk[] {
  const out: Hunk[] = []
  let cur: Hunk | null = null
  let i = 0
  for (const row of lineDiff(base, other)) {
    if (row.kind === 'same') {
      if (cur) { out.push(cur); cur = null }
      i++
    } else if (row.kind === 'del') {
      cur ??= { start: i, end: i, lines: [] }
      cur.end = ++i
    } else {
      cur ??= { start: i, end: i, lines: [] }
      cur.lines.push(row.text)
    }
  }
  if (cur) out.push(cur)
  return out
}

const isInsertion = (h: Hunk) => h.start === h.end

/** Whether two edits of the same base collide: they change a line in common, both insert at one
 *  point (which comes first is a choice), or one inserts inside the lines the other replaces.
 *  Edits that only TOUCH — one line changed and the next line changed, or lines appended right
 *  after an edited one — have a single outcome, so they are applied in order. */
function collide(x: Hunk, y: Hunk): boolean {
  if (isInsertion(x) && isInsertion(y)) return x.start === y.start
  if (isInsertion(x)) return y.start < x.start && x.start < y.end
  if (isInsertion(y)) return x.start < y.start && y.start < x.end
  return x.start < y.end && y.start < x.end
}

/** Which of two independent edits comes first in the merged text: the one earlier in the base, and
 *  at one point an insertion before the lines replaced from there. */
function first(x: Hunk, y: Hunk): boolean {
  return x.start < y.start || (x.start === y.start && isInsertion(x) && !isInsertion(y))
}

/** Three-way line merge: `mine`'s edits to `base` applied onto `theirs`, or `null` when an edit of
 *  each side collides with one of the other's (`collide`) and they differ. */
export function mergeText(base: string, mine: string, theirs: string): string | null {
  const b = base.split('\n')
  const a = hunks(base, mine)
  const t = hunks(base, theirs)
  const out: string[] = []
  let pos = 0, ia = 0, it = 0
  while (ia < a.length || it < t.length) {
    const ha = a[ia], ht = t[it]
    let h: Hunk
    if (ha && ht && collide(ha, ht)) {
      // Both sides edited here. The same edit twice is one edit; two different ones are a conflict.
      if (ha.start !== ht.start || ha.end !== ht.end || ha.lines.join('\n') !== ht.lines.join('\n')) return null
      h = ha; ia++; it++
    } else if (ha && (!ht || first(ha, ht))) { h = ha; ia++ }
    else { h = ht; it++ }
    // Never re-apply over lines an earlier edit already replaced — that is a collision `collide`
    // did not name, and refusing is the only answer that cannot lose a line.
    if (h.start < pos) return null
    out.push(...b.slice(pos, h.start), ...h.lines)
    pos = h.end
  }
  out.push(...b.slice(pos))
  return out.join('\n')
}

type Rec = Record<string, unknown>

function isStringList(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === 'string')
}

/** Three-way merge of one field: `null` when both sides changed it differently. */
function mergeValue(base: unknown, mine: unknown, theirs: unknown): { value: unknown } | null {
  if (sameDocument(mine, base)) return { value: theirs }
  if (sameDocument(theirs, base) || sameDocument(theirs, mine)) return { value: mine }
  if (typeof base === 'string' && typeof mine === 'string' && typeof theirs === 'string') {
    const merged = mergeText(base, mine, theirs)
    return merged === null ? null : { value: merged }
  }
  // A list of names is a set here (an agent's skills, a server's tools): keep what they have,
  // add what I added, drop what I removed.
  if (isStringList(base) && isStringList(mine) && isStringList(theirs)) return { value: rebaseList(base, mine)(theirs) }
  if (base && mine && theirs && typeof base === 'object' && typeof mine === 'object' && typeof theirs === 'object'
    && !Array.isArray(base) && !Array.isArray(mine) && !Array.isArray(theirs)) {
    const merged = mergeRecord(base as Rec, mine as Rec, theirs as Rec)
    return merged === null ? null : { value: merged }
  }
  return null
}

/** Three-way merge of a record, field by field: the fields I changed from `mine`, every other
 *  field from `theirs`, or `null` when a field was changed differently on both sides. */
export function mergeRecord<T extends Rec>(base: T, mine: T, theirs: T): T | null {
  const out: Rec = {}
  for (const k of new Set([...Object.keys(base), ...Object.keys(mine), ...Object.keys(theirs)])) {
    const merged = mergeValue(base[k], mine[k], theirs[k])
    if (merged === null) return null
    if (merged.value !== undefined) out[k] = merged.value
  }
  return out as T
}

/** The `Rebase` for a list of names edited from `before` to `after`, as a set: the names it added
 *  join what is stored, the names it removed leave it, and every other stored name stays. */
export function rebaseList(before: string[], after: string[]): (theirs: string[]) => string[] {
  const added = after.filter((x) => !before.includes(x))
  const removed = new Set(before.filter((x) => !after.includes(x)))
  return (theirs) => [...theirs.filter((x) => !removed.has(x)), ...added.filter((x) => !theirs.includes(x))]
}

/** The `Rebase` for a text edit started from `base`. */
export function rebaseText(base: string, mine: string): Rebase<string> {
  return (theirs) => mergeText(base, mine, theirs)
}

/** The `Rebase` for a record edit started from `base`. */
export function rebaseRecord<T extends Rec>(base: T, mine: T): Rebase<T> {
  return (theirs) => mergeRecord(base, mine, theirs)
}
