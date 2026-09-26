// Frontend replay driver (Self-Verification §2.2, vitest side).
//
// Feeds a recorded chat-stream trace through the SAME pure folds the live UI uses
// (coalesceReducers.ts) and returns the terminal segment list, so a recorded trace
// becomes a replayable regression for the K42/K44/K45 coalescer bug class instead of a
// hand-written unit test. The run-stream equivalent drives runFold.ts's foldReducer.
//
// A trace step is one recorded stream event. We model the minimal event vocabulary the
// coalescer reacts to; a real recorded trace (from PERSONALCLAW_TRACE_DIR) is normalized
// into this shape by the test. The driver is pure — no React, no fetch — matching the
// folds it exercises, so it runs in plain vitest.

import { TextRunOwnership } from '../pages/chat/coalesceReducers'
import { foldReducer, emptyRunFlags, type RunFlags } from '../pages/loops/runFold'
import type { Segment } from '../pages/chat/chatTypes'

/** One chat-stream trace step. `flush` reveals coalesced text; `activity` inserts a
 *  native activity line; `boundary` ends the live text run (tool/approval/done/send). */
export type ChatStep =
  | { kind: 'flush'; text: string }
  | { kind: 'activity'; text: string; activityKind?: string }
  | { kind: 'boundary' }

/** Replay a chat trace through the coalescer folds — the same `TextRunOwnership` updaters
 *  the live ChatPage dispatches, applied as each step lands. Returns the terminal segments. */
export function replayChat(steps: ChatStep[]): { segs: Segment[] } {
  let segs: Segment[] = []
  const run = new TextRunOwnership()
  for (const step of steps) {
    if (step.kind === 'flush') segs = run.flush(step.text)(segs)
    else if (step.kind === 'activity') segs = run.activity(step.text, step.activityKind ?? '')(segs)
    // A boundary (tool/approval/segment/chat_done or a fresh send) ends the active run.
    else run.release()
  }
  return { segs }
}

/** Replay a run-stream trace (a sequence of lifecycle event strings + optional data)
 *  through foldReducer, returning the terminal RunFlags. */
export function replayRun(
  steps: { event: string; data?: unknown }[],
  initial: RunFlags = emptyRunFlags(),
): RunFlags {
  let flags = initial
  for (const s of steps) flags = foldReducer(flags, s.event, s.data)
  return flags
}

/** A structural metric over a replayed chat: how many ADJACENT duplicate text segments
 *  exist (the K44 "answer rendered twice" signature). Zero on a healthy trace. */
export function adjacentDuplicateTextCount(segs: Segment[]): number {
  let dupes = 0
  for (let i = 1; i < segs.length; i++) {
    const a = segs[i - 1]
    const b = segs[i]
    if (a.kind === 'text' && b.kind === 'text' && a.text === b.text) dupes++
  }
  return dupes
}
