import { useCallback, useEffect, useRef } from 'react'
import { runtime } from '../../design/runtime'

/** rAF stream coalescer (P15) — batches WS `chat_chunk` appends into ONE flush per
 *  animation frame, with an adaptive reveal cursor that drains the backlog smoothly
 *  (low-pass token-rate estimate → clamped per-frame budget) so streaming reads as a
 *  steady write instead of a stuttery per-chunk state storm.
 *
 *  Mirrors TypingReveal's proven rAF discipline: progress lives in REFS (never in a
 *  setState updater — scheduling a frame inside an updater double-loops under
 *  StrictMode), a single guarded rAF, `cancelAnimationFrame` on unmount/reset.
 *
 *  Modes:
 *   • immediate (or `prefers-reduced-motion`, or `runtime.animSpeed === 0`) → each
 *     push flushes the full accumulated text synchronously. The global CSS
 *     reduced-motion rule only kills CSS transitions, so a JS rAF loop MUST self-gate.
 *   • animated → per-frame budget = clamp(ema * drainFactor * speed, MIN, MAX);
 *     drainFactor ramps as the backlog grows so we never lag past MAX_LAG chars.
 *
 *  The adaptive-budget MATH lives in a pure `CoalescerCore` (no rAF, no React) so it's
 *  unit-testable; the hook is a thin rAF+refs wrapper around it.
 *
 *  BOUNDARIES: a text run ends exactly two ways — `seal()` (land the tail, then clear) or
 *  `reset()` (clear without landing). Both CLEAR, which is the invariant: a finished run's
 *  text can never be re-emitted, so no caller needs a "did we already break?" flag.
 *
 *  RESUME: a run can also be re-based on a server snapshot (`resume`) — a reload, a
 *  reconnect or a remount rebuilds the transcript from session detail, and the live answer
 *  continues from the partial that snapshot shows. Chunks carry the gateway's stamp, and the
 *  snapshot reports the newest stamp it holds, so a chunk the transcript already shows is
 *  dropped instead of written twice. */

export const FRAME_MS = 16
export const MIN_BUDGET = 2       // chars/frame floor while animating (never stalls)
export const MAX_BUDGET = 400     // chars/frame ceiling (a huge paste drains in a few frames)
export const MAX_LAG = 1200       // backlog past this → ramp drainFactor hard to catch up
const EMA_ALPHA = 0.3             // low-pass weight for the chars/frame rate estimate

/** Pure, frameless core of the coalescer — all the accumulation + adaptive-budget
 *  math, testable without rAF/DOM. `tick()` advances the reveal by one frame's worth
 *  and returns the revealed prefix; the hook calls it once per animation frame. */
export class CoalescerCore {
  private pending = ''
  private revealed = 0
  private ema = 0
  private drain = 1
  // The newest chunk stamp (`chat_chunk.seq`) this transcript already shows. It is NOT per
  // run — boundaries keep it — because the gateway stamps chunks process-wide, and a resume
  // re-sets it from the snapshot it resumed from.
  private watermark = 0

  /** Append a chunk to the backlog. A stamped chunk at or below the watermark is already on
   *  screen — the snapshot the transcript was rebuilt from holds it — and is refused. */
  push(chunk: string, seq?: number): boolean {
    if (seq !== undefined && seq <= this.watermark) return false
    if (seq !== undefined) this.watermark = seq
    this.pending += chunk
    return true
  }

  /** Continue from a server snapshot that shows every chunk stamped `<= watermark`. With a
   *  `partial` the snapshot ends in the live answer, and this run becomes that text, already
   *  revealed — the transcript is painting it from the same snapshot. Without one, the run
   *  restarts empty. The watermark becomes the snapshot's, even when that is lower: the
   *  snapshot is authoritative for what the transcript now holds, and every frame after it is
   *  replayed on top (snapshotReplay.ts). */
  resume(partial: string | null, watermark: number): void {
    this.reset()
    if (partial) {
      this.pending = partial
      this.revealed = partial.length
    }
    this.watermark = watermark
  }

  /** Chars not yet revealed. */
  backlog(): number { return this.pending.length - this.revealed }

  /** Whether this run holds any text at all — revealed or not.
   *
   *  NOT `backlog() > 0`: a fully-revealed run still HOLDS its text (`drainAll` moves the
   *  cursor, it does not empty the buffer), and a boundary has to know the difference
   *  between "nothing to land" and "already landed". Used by `seal()` so a boundary on an
   *  empty run emits nothing instead of writing an empty text segment. */
  hasText(): boolean { return this.pending.length > 0 }

  /** The revealed prefix (what the consumer should render right now). */
  revealedText(): string { return this.pending.slice(0, this.revealed) }

  /** Reveal everything immediately; returns the full text. */
  drainAll(): string { this.revealed = this.pending.length; return this.pending }

  /** Clear the run for a fresh segment/turn (the watermark outlives runs — see above). */
  reset(): void { this.pending = ''; this.revealed = 0; this.ema = 0; this.drain = 1 }

  /** Advance the reveal by one frame's adaptive budget; returns the revealed prefix.
   *  `speed` scales pace (runtime.animSpeed); ≤0 means the caller should be in
   *  immediate mode, but we still clamp to a floor so a stray call makes progress.
   *
   *  Word-boundary snapping (CHAT-CRAFT S3): once the budget cursor lands, back it up
   *  to the last whitespace/CJK boundary within the just-revealed window so reveals
   *  land on whole words instead of cutting mid-word. Skipped when the backlog is
   *  past MAX_LAG (catch-up ALWAYS wins — snapping must never make us fall behind) and
   *  when snapping would erase all forward progress this frame (never stall). */
  tick(speed: number): string {
    const backlog = this.backlog()
    if (backlog <= 0) return this.revealedText()
    const s = Math.max(0.1, speed)
    // Ramp the drain factor up while the backlog is large, ease back toward 1 when caught up.
    this.drain = backlog > MAX_LAG ? Math.min(8, this.drain + 1) : Math.max(1, this.drain - 0.25)
    // EMA of the recent per-frame backlog; the budget tracks it within [MIN, MAX].
    this.ema = EMA_ALPHA * backlog + (1 - EMA_ALPHA) * this.ema
    const budget = Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Math.ceil(this.ema * this.drain * s)))
    const from = this.revealed
    let to = Math.min(this.pending.length, from + budget)
    // Snap back to a word/CJK boundary within (from, to) — but not when catching up,
    // not when we've already revealed to the very end, and not if it would undo all
    // progress. If the char AFTER `to` is itself a boundary, we're already aligned.
    if (backlog <= MAX_LAG && to < this.pending.length && !this._isBoundary(this.pending[to])) {
      const snapped = this._lastBoundary(from + 1, to)
      if (snapped > from) to = snapped
    }
    this.revealed = to
    return this.revealedText()
  }

  /** A char we may reveal up to: ASCII whitespace or a CJK ideograph (each CJK glyph
   *  is a word, so any CJK char is a valid stop). */
  private _isBoundary(ch: string): boolean {
    if (ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r') return true
    const c = ch.codePointAt(0) ?? 0
    return (c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3040 && c <= 0x30ff)
  }

  /** The greatest index in [lo, hi] whose char (or the char before it) is a boundary,
   *  so the revealed prefix ends on a whole word; -1 if none. We prefer to end the
   *  reveal JUST AFTER a space (include the trailing space) or right before a CJK glyph. */
  private _lastBoundary(lo: number, hi: number): number {
    for (let i = hi; i >= lo; i--) {
      // ending right before a CJK glyph is a clean word stop
      if (this._isBoundary(this.pending[i])) return i
      // ending just after a run of whitespace is a clean stop too
      if (i > 0 && this._isBoundary(this.pending[i - 1])) return i
    }
    return -1
  }
}

export interface StreamCoalescer {
  /** Append a streamed chunk. Schedules one rAF (animated) or flushes now (immediate). A
   *  chunk stamped at or below the watermark is already on screen and is dropped. */
  push: (chunk: string, seq?: number) => void
  /** Continue the live answer from a server snapshot (see `CoalescerCore.resume`). The
   *  partial is not re-emitted — the snapshot's transcript already paints it. */
  resume: (partial: string | null, watermark: number) => void
  /** Reveal everything buffered NOW, keeping the run open. Not a boundary — boundaries are
   *  `seal` and `reset`, which clear. For the frames replayed onto an adopted snapshot: they
   *  paint with it, at once, rather than animating back in text the tab was already showing. */
  reveal: () => void
  /** END this text run at a boundary: land whatever is still buffered into the run's own
   *  segment, then CLEAR the buffer so the next `push` opens a fresh one.
   *
   *  🔴 The clear is the whole point (K44 / issue #548). A drain-only flush left the finished
   *  run's text in `pending`, so the NEXT boundary flush — a `tool_call` opening the next turn,
   *  say — re-emitted the previous turn's entire answer into the new turn's bubble. Callers
   *  used to defer the clearing to a "break" flag consulted in ONE branch of six, which is a
   *  guard the other five silently skipped. Sealing here removes the flag and the choice. */
  seal: () => void
  /** DISCARD this text run: clear the buffer without emitting. For a boundary the CLIENT
   *  creates — a fresh send, regenerate, edit-resend, a queued turn being dequeued, or a
   *  session switch — where the transcript tail has already moved on, so landing the old
   *  text would write it into the new turn. */
  reset: () => void
}

export function useStreamCoalescer(
  onFlush: (revealedSoFar: string) => void,
  opts: { immediate?: boolean } = {},
): StreamCoalescer {
  const onFlushRef = useRef(onFlush); onFlushRef.current = onFlush
  const immediateRef = useRef(opts.immediate); immediateRef.current = opts.immediate

  const coreRef = useRef<CoalescerCore | null>(null)
  if (!coreRef.current) coreRef.current = new CoalescerCore()
  const rafRef = useRef(0)
  const lastTsRef = useRef(0)

  const isImmediate = () =>
    immediateRef.current === true
    || runtime.animSpeed === 0
    || (typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

  const stop = () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0 } }

  const frame = useCallback((ts: number) => {
    rafRef.current = 0
    if (!lastTsRef.current) lastTsRef.current = ts
    if (ts - lastTsRef.current < FRAME_MS) { rafRef.current = requestAnimationFrame(frame); return }
    lastTsRef.current = ts
    const core = coreRef.current!
    onFlushRef.current(core.tick(runtime.animSpeed))
    if (core.backlog() > 0) rafRef.current = requestAnimationFrame(frame)
  }, [])

  // Reveal-everything-now, WITHOUT clearing — for immediate mode, where every push emits the
  // whole accumulated run and the next push must extend it, and for `reveal`. Never a boundary:
  // this is the drain-without-clear that leaked a finished run into the next turn when
  // boundaries called it (#548). Boundaries get `seal`, which clears.
  const drain = useCallback(() => {
    stop(); lastTsRef.current = 0
    onFlushRef.current(coreRef.current!.drainAll())
  }, [])

  const reset = useCallback(() => { stop(); lastTsRef.current = 0; coreRef.current!.reset() }, [])

  const seal = useCallback(() => {
    const core = coreRef.current!
    stop(); lastTsRef.current = 0
    // Emit only when the run HOLDS text. An unconditional emit on an empty run wrote an empty
    // text segment above the tool card of every turn that opens with a tool call.
    if (core.hasText()) onFlushRef.current(core.drainAll())
    core.reset()
  }, [])

  const push = useCallback((chunk: string, seq?: number) => {
    if (!coreRef.current!.push(chunk, seq)) return
    if (isImmediate()) { drain(); return }
    if (!rafRef.current) rafRef.current = requestAnimationFrame(frame)
  }, [frame, drain])

  const resume = useCallback((partial: string | null, watermark: number) => {
    stop(); lastTsRef.current = 0
    coreRef.current!.resume(partial, watermark)
  }, [])

  // Only when there IS a backlog: an emit on an empty run would write an empty text segment,
  // the same reason `seal` checks `hasText()`.
  const reveal = useCallback(() => {
    if (coreRef.current!.backlog() > 0) drain()
  }, [drain])

  useEffect(() => () => stop(), [])

  return { push, seal, reset, resume, reveal }
}
