import { stampActivityOrigin, type Segment } from './chatTypes'

/** A transcript update for the trailing assistant turn's segments. */
export type SegmentsUpdate = (segs: Segment[]) => Segment[]

/**
 * Who owns the coalescer's live text segment — and the ONE place its decisions are taken.
 *
 * "Is the trailing text segment the live run?" decides two things: whether a flush REPLACES
 * that segment or pushes a new one, and whether an activity line lands above it or below it.
 * Both used to be read off a mutable flag INSIDE the transcript updater. React may apply an
 * updater only after later WS callbacks have run, and `chat_done` releases the run, so the
 * answer to that question depended on whether React happened to render between two frames:
 *
 *  - #3513: the terminal full-text flush read "not live" and pushed the answer a second time.
 *  - The stats line that ends every turn, when it shares a render batch with `chat_done`
 *    (measured 1 ms apart in both doubled runs of day56b s14, 8–19 ms in the clean ones): it
 *    read "not live", went BELOW the answer, and the terminal flush — which had decided
 *    "replace the tail" synchronously — found an activity line there and pushed the answer
 *    again.
 *
 * So ownership is not readable at all. Every decision is taken at DISPATCH and handed out
 * baked into the updater, which is then a pure function of the segments it is applied to.
 * Updaters apply in dispatch order, so the ownership timeline and the segment timeline are
 * the same timeline, whenever React renders.
 */
export class TextRunOwnership {
  private live = false

  /** The coalescer's flush of `revealed`: replace the live tail it owns, or open the run. */
  flush(revealed: string): SegmentsUpdate {
    const replace = this.live
    this.live = true
    return (segs) => applyCoalescedFlush(segs, revealed, replace)
  }

  /** A native activity line, placed against the run as it stands NOW (see insertActivity). */
  activity(text: string, activityKind: string, origin?: string): SegmentsUpdate {
    const live = this.live
    return (segs) => stampActivityOrigin(segs, insertActivity(segs, text, activityKind, live), origin)
  }

  /** The trailing text segment was hydrated from the server's in-flight partial and the
   *  coalescer was resumed onto it: it IS the live run, so the next flush replaces it. */
  adopt(): void {
    this.live = true
  }

  /** Release ownership at any server or client text-run boundary. */
  release(): void {
    this.live = false
  }
}

/** Pure segment-attribution reducers for the chat stream coalescer.
 *
 *  These encode the hard-won invariants behind K42/K44/K45 — the bugs where a streamed reply
 *  rendered twice, or turn N+1 absorbed turn N's answer. Callers reach them through
 *  `TextRunOwnership`, which supplies the ownership decision each one takes as an argument. */

/** A flush of `revealed` text into the assistant segment list.
 *  - `replace` (the caller owns the live run) and the tail is text → REPLACE it in place.
 *  - Otherwise → PUSH a new text segment; the caller now owns it. */
export function applyCoalescedFlush(segs: Segment[], revealed: string, replace: boolean): Segment[] {
  const next = segs.slice()
  const last = next[next.length - 1]
  if (replace && last && last.kind === 'text') {
    next[next.length - 1] = { kind: 'text', text: revealed }
  } else {
    next.push({ kind: 'text', text: revealed })
  }
  return next
}

/** Insert a native activity line (e.g. "recalled context") into the segment list.
 *  Discipline (K42): if we're mid-run (`live` + trailing text), insert BEFORE that text run —
 *  never after — so the coalescer's active text stays the tail and the next flush replaces
 *  it in place rather than pushing a duplicate. Also the correct reading order (a preamble
 *  belongs above the answer). De-dupes against the adjacent activity line. Returns the same
 *  array (by identity) when nothing changes. */
export function insertActivity(
  segs: Segment[],
  text: string,
  activityKind: string,
  live: boolean,
): Segment[] {
  if (segs.some((sg) => sg.kind === 'tool')) return segs // ACP tool cards win — no activity noise
  const insertAt = (live && segs[segs.length - 1]?.kind === 'text') ? segs.length - 1 : segs.length
  const neighbor = segs[insertAt - 1] ?? segs[insertAt]
  if (neighbor && neighbor.kind === 'activity' && neighbor.text === text) return segs
  const next = segs.slice()
  next.splice(insertAt, 0, { kind: 'activity', text, activityKind })
  return next
}
