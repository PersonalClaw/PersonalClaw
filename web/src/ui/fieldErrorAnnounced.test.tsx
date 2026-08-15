import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { FieldError } from './forms'
import { InlineError } from './InlineError'

// ── A failure nobody was told about ───────────────────────────────────────────────────────
//
// Driven on `#/settings/design` with `POST /api/themes` forced to 500: the Save-theme failure
// text appeared on screen and the page held **zero** live regions —
//
//   error text on screen  : {"detail":"theme store is read-only"}
//   [role=alert] / [aria-live] on the page : []
//
// so a screen-reader user pressed Save, watched the button return to idle, and was told
// nothing. A censused **35 sites** rendered a failure as one line of danger-toned text with no
// role, while the app's other two failure surfaces — `InlineError` (the tinted band) and
// `LoadError` — both announce. This line was the family's silent member, and it was silent in
// 17 files at once because the idiom was copy-pasted, never owned:
//
//     {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
//
// `FieldError` is that line with a `role`. It renders the same classes, so nothing moves.
//
// 🪤 WHAT A LABELS/CONTRAST AUDIT CANNOT SEE. `ux-audit` and axe both pass this markup: the text
// has AA contrast, the element is valid, nothing is unnamed. "Was the user TOLD?" is not a
// property of the DOM at rest — it needs the failure DRIVEN and the live regions read after it.
// The mechanical pass on every surface in this change was 0 blocking, before and after.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('FieldError', () => {
  it('announces — that is the whole reason it exists', () => {
    const { container } = render(<FieldError>Could not save</FieldError>)
    expect(container.querySelector('[role="alert"]'), 'a failure the user did not request must interrupt').not.toBeNull()
  })

  it('renders the same line it replaced, so nothing moves', () => {
    const { container } = render(<FieldError>Could not save</FieldError>)
    const p = container.querySelector('p')!
    expect(p.className).toBe('text-danger text-[0.8125rem]')
    expect(p.textContent).toBe('Could not save')
  })

  it('takes per-site spacing without letting a site re-tone it', () => {
    const { container } = render(<FieldError className="mt-2">x</FieldError>)
    expect(container.querySelector('p')!.className).toBe('text-danger text-[0.8125rem] mt-2')
  })

  it('agrees with InlineError that a failure is an alert', () => {
    // Two shapes, one contract: the band and the line both interrupt. (EmptyState deliberately
    // does not — "you have none" is a normal answer, per loadErrorState.test.tsx.)
    const { container } = render(<InlineError>boom</InlineError>)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
  })
})

describe('no site hand-rolls the silent line any more', () => {
  const files = walk(SRC)
  /** The exact idiom that was copy-pasted 30 times, plus the `<div>`/`<span>` variants of it.
   *  Deliberately NOT a loose `text-danger` search: plenty of danger-toned text is a static
   *  label or a stored status, and neither should announce.
   *
   *  Covers BOTH the primary `0.8125rem` size AND the compact `0.75rem` one — the census was
   *  originally scoped to 0.8125rem only, which let four silent failures slip through at the
   *  smaller size (a failed create in TaskForm, a save failure in MemoryPanel, an Ollama pull
   *  error, a workflow error in a chat card). They announce now; the widened pattern keeps a new
   *  0.75rem silent line from reappearing. (These four carry `role="alert"` on the raw element
   *  rather than `FieldError`, which is size-locked to 0.8125rem — the rail's contract is "a
   *  dynamic failure announces", not "it is this component".) */
  const RAW = /<(p|div|span)[^>]*className="(?:[a-z0-9:.\-[\]/]+ )*text-danger text-\[0\.(?:8125|75)rem\]"[^>]*>\{\s*\w[\w.]*\s*\}<\/\1>/g

  const offenders = files.flatMap((f) => {
    const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    return [...src.matchAll(RAW)]
      // The create pages keep their own line: it ALREADY carries role="alert" plus an
      // `errRef` for focus management, which this primitive does not model. Named, not
      // silently excluded — converging them is the owner's open `InlineError` call.
      .filter((m) => !/role="alert"/.test(m[0]))
      .map((m) => ({ file: f.slice(SRC.length + 1), snippet: m[0].slice(0, 70) }))
      // ONE named exemption, and it is a real distinction rather than a miss:
      // `WorkflowRunDetail` renders `run.error` — the error a FINISHED run recorded. That is
      // stored data in a detail view, not a failure that just happened to you, and `role=alert`
      // would interrupt on render for something historical. Same reason `EmptyState` is not an
      // alert: only unrequested bad news gets to interrupt.
      .filter((o) => o.file !== 'pages/workflows/WorkflowRunDetail.tsx')
  })

  it('finds the primitive at the sites that used to hand-roll it (not vacuously green)', () => {
    const adopters = files.filter((f) => /<FieldError\b/.test(readFileSync(f, 'utf8')))
    // 35 sites across 18 files at the time of writing.
    expect(adopters.length, 'the conversion must actually be there').toBeGreaterThanOrEqual(18)
  })

  it('has no silent failure line left', () => {
    expect(offenders.map((o) => o.file), 'a failure rendered with no role announces to nobody').toEqual([])
  })
})
