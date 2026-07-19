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
 *  unit-testable; the hook is a thin rAF+refs wrapper around it. */

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

  /** Append a chunk to the backlog. */
  push(chunk: string): void { this.pending += chunk }

  /** Chars not yet revealed. */
  backlog(): number { return this.pending.length - this.revealed }

  /** The revealed prefix (what the consumer should render right now). */
  revealedText(): string { return this.pending.slice(0, this.revealed) }

  /** Reveal everything immediately; returns the full text. */
  drainAll(): string { this.revealed = this.pending.length; return this.pending }

  /** Clear all state for a fresh segment/turn. */
  reset(): void { this.pending = ''; this.revealed = 0; this.ema = 0; this.drain = 1 }

  /** Advance the reveal by one frame's adaptive budget; returns the revealed prefix.
   *  `speed` scales pace (runtime.animSpeed); ≤0 means the caller should be in
   *  immediate mode, but we still clamp to a floor so a stray call makes progress. */
  tick(speed: number): string {
    const backlog = this.backlog()
    if (backlog <= 0) return this.revealedText()
    const s = Math.max(0.1, speed)
    // Ramp the drain factor up while the backlog is large, ease back toward 1 when caught up.
    this.drain = backlog > MAX_LAG ? Math.min(8, this.drain + 1) : Math.max(1, this.drain - 0.25)
    // EMA of the recent per-frame backlog; the budget tracks it within [MIN, MAX].
    this.ema = EMA_ALPHA * backlog + (1 - EMA_ALPHA) * this.ema
    const budget = Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Math.ceil(this.ema * this.drain * s)))
    this.revealed = Math.min(this.pending.length, this.revealed + budget)
    return this.revealedText()
  }
}

export interface StreamCoalescer {
  /** Append a streamed chunk. Schedules one rAF (animated) or flushes now (immediate). */
  push: (chunk: string) => void
  /** Drain the entire backlog immediately + emit. Call on segment/turn boundaries. */
  flushNow: () => void
  /** Clear all buffered state for a fresh segment/turn. */
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

  const flushNow = useCallback(() => {
    stop(); lastTsRef.current = 0
    onFlushRef.current(coreRef.current!.drainAll())
  }, [])

  const reset = useCallback(() => { stop(); lastTsRef.current = 0; coreRef.current!.reset() }, [])

  const push = useCallback((chunk: string) => {
    coreRef.current!.push(chunk)
    if (isImmediate()) { flushNow(); return }
    if (!rafRef.current) rafRef.current = requestAnimationFrame(frame)
  }, [frame, flushNow])

  useEffect(() => () => stop(), [])

  return { push, flushNow, reset }
}
