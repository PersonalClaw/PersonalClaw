import { useEffect, useRef, useState } from 'react'

/** Animated "autonomous writing" reveal of a file's content, shown in the cockpit
 *  editor area when the worker changes a file. Three modes:
 *   • write — start empty, reveal characters left→right, line by line (new file /
 *     the added side of a diff).
 *   • erase — start full, remove characters right→left, last line first (deleted
 *     file / the removed side of a diff).
 *
 *  Pacing is per-character but CAPPED to a fast total (≈0.5–1.5s) so a large file
 *  animates quickly: the per-tick character budget scales with file size. Calls
 *  `onDone` once the animation completes so the host can hand off to the real
 *  editor. Honors prefers-reduced-motion (completes instantly).
 */
const MIN_MS = 450
const MAX_MS = 1500
const FRAME_MS = 16

export function TypingReveal({ text, mode, theme, onDone }: {
  text: string
  mode: 'write' | 'erase'
  theme: 'light' | 'dark'
  onDone?: () => void
}) {
  // Number of characters currently shown (write: counting up; erase: counting down).
  const total = text.length
  const [shown, setShown] = useState(mode === 'write' ? 0 : total)
  const doneRef = useRef(onDone); doneRef.current = onDone
  const scrollRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || total === 0) {
      setShown(mode === 'write' ? total : 0)
      doneRef.current?.()
      return
    }
    // Cap total duration: scale the per-tick char budget to file size so a big
    // file isn't a slow crawl. duration grows with size but clamps to [MIN,MAX].
    const durationMs = Math.min(MAX_MS, Math.max(MIN_MS, total * 3))
    const ticks = Math.max(1, Math.round(durationMs / FRAME_MS))
    const perTick = Math.max(1, Math.ceil(total / ticks))
    let raf = 0
    let last = 0
    // Track progress in a local (not via the state updater) so the updater stays
    // PURE — scheduling the next frame inside setShown(...) risked duplicate RAF loops
    // under StrictMode's double-invoke (the animation would speed up / glitch).
    let n = mode === 'write' ? 0 : total
    const step = (ts: number) => {
      if (!last) last = ts
      if (ts - last >= FRAME_MS) {
        last = ts
        n = mode === 'write' ? Math.min(total, n + perTick) : Math.max(0, n - perTick)
        setShown(n)
        if ((mode === 'write' && n >= total) || (mode === 'erase' && n <= 0)) {
          doneRef.current?.()
          return  // animation complete → stop scheduling
        }
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [text, mode, total])

  // Keep the reveal head in view as it advances.
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [shown])

  const visible = text.slice(0, shown)
  const lines = visible.split('\n')
  const bg = theme === 'light' ? '#ffffff' : '#1e1e1e'
  const fg = theme === 'light' ? '#1f1f1f' : '#d4d4d4'
  const gutter = theme === 'light' ? '#9aa0a6' : '#6e7681'
  return (
    <pre ref={scrollRef} aria-label={mode === 'erase' ? 'Deleting file' : 'Writing file'}
      className="m-0 h-full overflow-auto p-0 font-mono text-[13px] leading-[1.5]"
      style={{ background: bg, color: fg, tabSize: 2 }}>
      <code className="block px-0 py-2.5">
        {lines.map((ln, i) => (
          <span key={i} className="grid grid-cols-[3.5ch_1fr] px-3">
            <span className="select-none pr-3 text-right" style={{ color: gutter }}>{i + 1}</span>
            <span className="whitespace-pre-wrap break-words">
              {ln}
              {/* a blinking caret at the reveal head (last visible line) */}
              {i === lines.length - 1 && shown < total && (
                <span className="reveal-caret inline-block" style={{ background: fg, width: '0.6ch', height: '1em', verticalAlign: 'text-bottom' }} />
              )}
            </span>
          </span>
        ))}
      </code>
    </pre>
  )
}
