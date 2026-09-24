/** Where the reader left off in an article, per item (KNOWLEDGE-LIBRARY S3, T3.3).
 *
 *  `KL-7` shipped a scroll-progress ring that REPORTS how far through the article the reader
 *  is; nothing persisted it, so "continue reading" had a shelf to fill and no position to
 *  resume to. This module is that missing writer: `ReadingView` records the fraction on scroll
 *  and restores it on mount, and `LibraryHome` reads it to order the shelf by what you touched
 *  last and to say how far in you are.
 *
 *  🔑 CLIENT-SIDE ON PURPOSE, and the shelf does not depend on it. Shelf MEMBERSHIP is server
 *  truth (`read_state = 'reading'`, set through the existing read-state endpoint), so a fresh
 *  browser still shows the right articles — it just resumes them at the top until you scroll
 *  once. A position is a per-device convenience, in the same class as `lib/activeProject`; the
 *  fact that you are mid-article is library state and lives in the store.
 *
 *  Every accessor is failure-tolerant: private mode, a quota-full origin and a hand-corrupted
 *  value all read as "no saved position", which resumes at the top rather than throwing inside
 *  a render.
 */

const KEY = 'knowledge-reading-positions'

/** The fraction 0..1 scrolled, plus when it was recorded (ms epoch, for shelf ordering). */
export interface ReadingPosition { pct: number; ts: number }

/** Below this the reader has not really started, and "resume" would scroll them nowhere. */
const MIN_PCT = 0.02
/** At or above this the article is finished: there is nothing left to resume TO, so the
 *  position is dropped rather than parked at the bottom of a piece you already read. */
const DONE_PCT = 0.98
/** Keep the map bounded — a library can hold thousands of items and this is a convenience
 *  cache, not a record. Oldest-touched entries fall off first. */
const MAX_ENTRIES = 200

type PositionMap = Record<string, ReadingPosition>

function isPosition(v: unknown): v is ReadingPosition {
  if (!v || typeof v !== 'object') return false
  const p = v as Partial<ReadingPosition>
  return typeof p.pct === 'number' && Number.isFinite(p.pct) && typeof p.ts === 'number'
}

/** Every saved position, keyed by item id. `{}` when there are none OR the read failed —
 *  the caller cannot act differently on the two, and a shelf that stops rendering because a
 *  localStorage value was hand-edited is worse than one that forgets where you were. */
export function readingPositions(): PositionMap {
  let raw: string | null = null
  try { raw = localStorage.getItem(KEY) } catch { return {} }
  if (!raw) return {}
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return {} }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
  const out: PositionMap = {}
  for (const [id, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (isPosition(v)) out[id] = { pct: Math.min(1, Math.max(0, v.pct)), ts: v.ts }
  }
  return out
}

/** One item's saved position, or null. */
export function getReadingPosition(id: string): ReadingPosition | null {
  if (!id) return null
  return readingPositions()[id] ?? null
}

function write(map: PositionMap): void {
  // Trim by recency BEFORE writing so the cap is enforced on the stored value, not just in
  // memory: a map trimmed only on read grows forever on disk.
  const entries = Object.entries(map).sort((a, b) => b[1].ts - a[1].ts).slice(0, MAX_ENTRIES)
  try { localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries))) }
  catch { /* private mode / quota — a resume point is best-effort */ }
}

/** Record how far into `id` the reader is.
 *
 *  A fraction under `MIN_PCT` or at/over `DONE_PCT` CLEARS the entry instead of storing it:
 *  the two ends of an article are the two cases with nothing to resume, and keeping them
 *  would show "0% — continue" and "100% — continue" rows on the home shelf.
 */
export function setReadingPosition(id: string, pct: number): void {
  if (!id || !Number.isFinite(pct)) return
  const map = readingPositions()
  if (pct < MIN_PCT || pct >= DONE_PCT) {
    if (!(id in map)) return
    delete map[id]
  } else {
    map[id] = { pct: Math.min(1, Math.max(0, pct)), ts: Date.now() }
  }
  write(map)
}

/** Forget where the reader was — used when an item leaves the reading shelf. */
export function clearReadingPosition(id: string): void {
  if (!id) return
  const map = readingPositions()
  if (!(id in map)) return
  delete map[id]
  write(map)
}
