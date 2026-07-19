import type { Segment } from './chatTypes'

/** Pure segment-attribution reducers for the chat stream coalescer.
 *
 *  These encode the hard-won invariants behind K42/K44/K45 — the bugs where a
 *  streamed reply rendered twice, or turn N+1 absorbed turn N's answer. That logic
 *  used to live inline in ChatPage's onFlush / activity_event handlers (untested,
 *  which is exactly how the bugs escaped). Extracting it here makes the contract a
 *  regression suite instead of a comment.
 *
 *  The coalescer's "active text run" is tracked by a boolean `coalescing`: true means
 *  the trailing text segment IS the run the coalescer owns (so a flush REPLACES it);
 *  false means the next flush opens a NEW run (so it PUSHES). A boundary
 *  (tool/approval/segment/chat_done) or a fresh send resets `coalescing` to false via
 *  the caller before the next chunk. */

/** A flush of `revealed` text into the assistant segment list.
 *  - If we own the trailing text run (`coalescing` + tail is text) → REPLACE it in place.
 *  - Otherwise → PUSH a new text segment and take ownership.
 *  Returns the next segments + the next `coalescing` value (always true after a flush:
 *  we now own the run we just wrote). */
export function applyCoalescedFlush(
  segs: Segment[],
  revealed: string,
  coalescing: boolean,
): { segs: Segment[]; coalescing: boolean } {
  const next = segs.slice()
  const last = next[next.length - 1]
  if (coalescing && last && last.kind === 'text') {
    next[next.length - 1] = { kind: 'text', text: revealed }
  } else {
    next.push({ kind: 'text', text: revealed })
  }
  return { segs: next, coalescing: true }
}

/** Insert a native activity line (e.g. "recalled context") into the segment list.
 *  Discipline (K42): if we're mid-run (`coalescing` + trailing text), insert BEFORE
 *  that text run — never after — so the coalescer's active text stays the tail and the
 *  next flush replaces it in place rather than pushing a duplicate. Also the correct
 *  reading order (a preamble belongs above the answer). De-dupes against the adjacent
 *  activity line. Returns the same array (by identity) when nothing changes. */
export function insertActivity(
  segs: Segment[],
  text: string,
  activityKind: string,
  coalescing: boolean,
): Segment[] {
  if (segs.some((sg) => sg.kind === 'tool')) return segs // ACP tool cards win — no activity noise
  const insertAt = (coalescing && segs[segs.length - 1]?.kind === 'text') ? segs.length - 1 : segs.length
  const neighbor = segs[insertAt - 1] ?? segs[insertAt]
  if (neighbor && neighbor.kind === 'activity' && neighbor.text === text) return segs
  const next = segs.slice()
  next.splice(insertAt, 0, { kind: 'activity', text, activityKind })
  return next
}
