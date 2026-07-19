import { useEffect, useRef, useState } from 'react'

/** A line-level diff animation for a MODIFIED file (the worker edited an existing
 *  file). Per the directive: the new/added lines reveal first (write, left→right),
 *  then the removed/old lines disappear (erase, right→left). Unchanged lines stay.
 *
 *  Pacing mirrors TypingReveal: per-character but capped to a fast total per phase
 *  so large diffs animate quickly. Honors prefers-reduced-motion (instant).
 */
type Row = { kind: 'same' | 'add' | 'del'; text: string }

/** Line-level LCS diff of old→new. Returns rows in display order: a removed line
 *  appears (as 'del') just before the kept/added lines that follow it. */
export function lineDiff(oldText: string, newText: string): Row[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const n = a.length, m = b.length
  // LCS length table (O(n*m) — fine for typical file sizes).
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const rows: Row[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { rows.push({ kind: 'same', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push({ kind: 'del', text: a[i] }); i++ }
    else { rows.push({ kind: 'add', text: b[j] }); j++ }
  }
  while (i < n) { rows.push({ kind: 'del', text: a[i] }); i++ }
  while (j < m) { rows.push({ kind: 'add', text: b[j] }); j++ }
  return rows
}

const MIN_MS = 350
const MAX_MS = 1200
const FRAME_MS = 16
// The LCS table is O(n·m) nested arrays built synchronously at render. For a huge
// file edit (worker rewrites a multi-thousand-line file) that allocation freezes
// the tab — the animation is only a progressive-enhancement nicety, so above this
// cell budget we skip it entirely and hand straight off to the real editor.
const LCS_CELL_CAP = 1_000_000

export function DiffReveal({ oldText, newText, theme, onDone }: {
  oldText: string
  newText: string
  theme: 'light' | 'dark'
  onDone?: () => void
}) {
  // Cheap pre-check (line counts only) BEFORE the quadratic lineDiff: if the diff is
  // too large to animate without jank, skip it and let the host open the real editor.
  const tooLarge = useRef((oldText.split('\n').length + 1) * (newText.split('\n').length + 1) > LCS_CELL_CAP).current
  const rows = useRef<Row[]>(tooLarge ? [] : lineDiff(oldText, newText)).current
  const addChars = rows.filter((r) => r.kind === 'add').reduce((s, r) => s + r.text.length + 1, 0)
  const delChars = rows.filter((r) => r.kind === 'del').reduce((s, r) => s + r.text.length + 1, 0)
  // phase 1 = reveal added chars (0→addChars); phase 2 = erase removed chars
  // (0→delChars). A single counter per phase, advanced by rAF.
  const [phase, setPhase] = useState<'add' | 'del' | 'done'>(addChars > 0 ? 'add' : (delChars > 0 ? 'del' : 'done'))
  const [addShown, setAddShown] = useState(0)
  const [delGone, setDelGone] = useState(0)
  const doneRef = useRef(onDone); doneRef.current = onDone
  const scrollRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || tooLarge || (addChars === 0 && delChars === 0)) {
      setAddShown(addChars); setDelGone(delChars); setPhase('done'); doneRef.current?.()
      return
    }
    let raf = 0, last = 0, ph: 'add' | 'del' = addChars > 0 ? 'add' : 'del'
    const addDur = Math.min(MAX_MS, Math.max(MIN_MS, addChars * 3))
    const delDur = Math.min(MAX_MS, Math.max(MIN_MS, delChars * 3))
    const addPer = Math.max(1, Math.ceil(addChars / Math.max(1, addDur / FRAME_MS)))
    const delPer = Math.max(1, Math.ceil(delChars / Math.max(1, delDur / FRAME_MS)))
    // Track progress in locals (not via the state updaters) so the updaters stay PURE
    // — scheduling the next RAF / calling setPhase inside setAddShown(...) risked
    // duplicate RAF loops under StrictMode's double-invoke (the reveal would glitch).
    let aShown = 0, dGone = 0
    const step = (ts: number) => {
      if (!last) last = ts
      if (ts - last < FRAME_MS) { raf = requestAnimationFrame(step); return }
      last = ts
      if (ph === 'add') {
        aShown = Math.min(addChars, aShown + addPer)
        setAddShown(aShown)
        if (aShown >= addChars) {
          if (delChars > 0) { ph = 'del'; setPhase('del') }
          else { setPhase('done'); doneRef.current?.(); return }
        }
      } else {
        dGone = Math.min(delChars, dGone + delPer)
        setDelGone(dGone)
        if (dGone >= delChars) { setPhase('done'); doneRef.current?.(); return }
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [addChars, delChars, tooLarge])

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [addShown])

  const bg = theme === 'light' ? '#ffffff' : '#1e1e1e'
  const fg = theme === 'light' ? '#1f1f1f' : '#d4d4d4'
  const gutter = theme === 'light' ? '#9aa0a6' : '#6e7681'
  const addBg = theme === 'light' ? 'rgba(46,160,67,0.12)' : 'rgba(46,160,67,0.18)'
  const delBg = theme === 'light' ? 'rgba(248,81,73,0.12)' : 'rgba(248,81,73,0.18)'

  // How many add-chars have reached this row (cumulative across add rows in order).
  let addCursor = 0
  // How many del-chars have been erased (cumulative across del rows in REVERSE).
  const delErasedFromEnd = delGone
  // Precompute per-del-row erase: walk del rows from the end.
  const delRowsReversed = rows.map((r, idx) => ({ r, idx })).filter((x) => x.r.kind === 'del').reverse()
  const delShownLen = new Map<number, number>()
  let remainingErase = delErasedFromEnd
  for (const { r, idx } of delRowsReversed) {
    const cap = r.text.length + 1
    const erased = Math.min(cap, remainingErase)
    remainingErase -= erased
    delShownLen.set(idx, Math.max(0, r.text.length - erased))
  }

  return (
    <pre ref={scrollRef} aria-label="Editing file" className="m-0 h-full overflow-auto p-0 font-mono text-[13px] leading-[1.5]"
      style={{ background: bg, color: fg, tabSize: 2 }}>
      <code className="block py-2.5">
        {rows.map((r, idx) => {
          if (r.kind === 'same') return <DiffLine key={idx} text={r.text} gutter={gutter} />
          if (r.kind === 'add') {
            const start = addCursor; addCursor += r.text.length + 1
            const shownChars = Math.max(0, Math.min(r.text.length, addShown - start))
            const writing = phase === 'add' && addShown > start && addShown < addCursor
            // not yet reached → render nothing (keeps layout from jumping is a tradeoff;
            // we DO render the row container so the diff shape is stable)
            return <DiffLine key={idx} text={r.text.slice(0, shownChars)} gutter={gutter} bg={addBg} sign="+" caret={writing} fg={fg} />
          }
          // del row: fully visible until phase 2 erases its suffix R→L
          const shownLen = phase === 'del' || phase === 'done' ? (delShownLen.get(idx) ?? 0) : r.text.length
          if (phase === 'done' && shownLen === 0) return null  // fully erased → gone
          return <DiffLine key={idx} text={r.text.slice(0, shownLen)} gutter={gutter} bg={delBg} sign="-" />
        })}
      </code>
    </pre>
  )
}

function DiffLine({ text, gutter, bg, sign, caret, fg }: {
  text: string; gutter: string; bg?: string; sign?: '+' | '-'; caret?: boolean; fg?: string
}) {
  return (
    <span className="grid grid-cols-[2ch_1fr] px-3" style={bg ? { background: bg } : undefined}>
      <span className="select-none pr-2 text-center" style={{ color: gutter }}>{sign || ''}</span>
      <span className="whitespace-pre-wrap break-words">
        {text}
        {caret && <span className="reveal-caret inline-block" style={{ background: fg, width: '0.6ch', height: '1em', verticalAlign: 'text-bottom' }} />}
      </span>
    </span>
  )
}
