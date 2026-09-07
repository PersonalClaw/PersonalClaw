import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A skeleton with no size is a 0px invisible element, and the component reports success ──────────
//
// `Skeleton` renders `<div className={`skeleton rounded-md ${className}`} aria-hidden="true" />` —
// a div with NO CONTENT. With no height class it is **0 pixels tall**: it mounts, it renders, nothing
// throws, and the user sees nothing at all.
//
// Five call sites shipped exactly that, and the worst was a whole panel body:
//
//     workflows/LedgerRailsPanel.tsx:94   if (loading) return <Skeleton />
//     workflows/IntrospectPanel.tsx:58    <Skeleton />                       (inside a SidePanel)
//     workflows/OutboxPanel.tsx:145       <Skeleton /><Skeleton /><Skeleton />
//     workflows/OutboxPanel.tsx:197       <Skeleton />
//     workflows/OutboxPanel.tsx:223       <Suspense fallback={<Skeleton />}>
//
// So three workflows panels rendered an EMPTY BODY for the entire fetch. That does not read as
// "loading" — it reads as broken, which is the one impression a loading state exists to prevent.
//
// 🔑 THE FIX IS THE TYPE, NOT THE FIVE SITES. `className` is now REQUIRED, so a sizeless skeleton no
// longer compiles. That was free to adopt: of 55 call sites the 50 correct ones already passed a
// size, as do all of `ListScaffold`'s own internal uses. This test guards the guard — a later pass
// "tidying" the prop back to optional would silently reopen the whole class.
//
// 🪤 A DEFAULT HEIGHT WAS CONSIDERED AND REJECTED. `className` is APPENDED, so a default `h-4` would
// produce `skeleton rounded-md h-4 h-72` and leave the winner to stylesheet source order rather than
// to the caller. That is less predictable than the bug it would paper over.
//
// 🪤 THIS FILE MUST STRIP COMMENTS BEFORE SCANNING. The comments explaining the fix quote the old
// bare form verbatim — including the block above. A naive scan finds 4 "bare skeletons" in prose that
// exists to describe their removal, which is exactly the trap this project's type-scale ratchet
// already documents (it counts its pattern in comment text too).

const SRC = join(process.cwd(), 'src')

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const abs = join(dir, e)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(e) && !/\.test\.tsx$/.test(e)) out.push(abs)
  }
  return out
}

const FILES = walk(SRC)

describe('every skeleton placeholder has a size', () => {
  it('no call site renders a bare <Skeleton /> — it would be invisible', () => {
    const bare: string[] = []
    for (const abs of FILES) {
      const body = strip(readFileSync(abs, 'utf8'))
      // `<Skeleton />` or `<Skeleton  />` with nothing between the name and the close.
      for (const _m of body.matchAll(/<Skeleton\s*\/>/g)) bare.push(abs.slice(SRC.length + 1))
    }
    expect(bare, `these render a 0px invisible element:\n${bare.join('\n')}`).toEqual([])
  })

  it('and the population is real, so the assertion above is not vacuous', () => {
    // If the scan found no skeletons at all, the test above would pass trivially forever.
    const total = FILES.reduce(
      (n, abs) => n + [...strip(readFileSync(abs, 'utf8')).matchAll(/<Skeleton\b/g)].length,
      0,
    )
    expect(total, 'Skeleton call sites outside comments').toBeGreaterThanOrEqual(45)
  })

  it('`className` is REQUIRED on the primitive, so a sizeless one cannot compile', () => {
    // 🔑 The real guard. Without this the five defects are fixed and the trap is still armed for the
    // next caller. `className?: string` or a `= ''` default both reopen it.
    const kit = readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8')
    const decl = strip(kit).match(/export function Skeleton\([^)]*\)/)?.[0] ?? ''
    expect(decl, 'the Skeleton declaration must be found before it is checked').not.toBe('')
    expect(decl, 'an optional className is what allowed a 0px skeleton').not.toMatch(/className\?/)
    expect(decl, 'nor may it default to empty').not.toMatch(/className\s*=\s*''/)
    expect(decl).toMatch(/className:\s*string/)
  })

  it('the three panels that rendered nothing now render a shaped, announced placeholder', () => {
    // The bare atom is `aria-hidden` by design, so those panels were silent as well as invisible.
    // The shaped kit primitives carry `role="status"` + an sr-only LoadingStatus.
    //
    // 🪤 NO `what` NOUN IS ASSERTED, AND THAT IS THE CORRECTED VERSION. My first draft passed and
    // asserted nouns ("this run’s ledger", "artifacts") borrowed from each file's `InlineError` copy
    // and panel title. `ui/loadingNounPairing.test.ts` red them as INVENTED, correctly: it only
    // accepts a noun traceable to a declaration it can verify — a sibling `LoadError what=`, a
    // `results={{ noun }}`, or an empty-state title. Prose I judged similar is not one of those, and
    // that rail's stated principle is "no noun here is invented". These panels report failure through
    // `InlineError`, so no declaration exists to source one from. Deferring the noun is the right
    // outcome; loosening that rail to accept my judgement would not be.
    const cases: [string, RegExp][] = [
      ['pages/workflows/LedgerRailsPanel.tsx', /<FormSkeleton sections=\{1\} rows=\{3\} title=\{false\} \/>/],
      ['pages/workflows/IntrospectPanel.tsx', /<FormSkeleton sections=\{1\} rows=\{4\} title=\{false\} \/>/],
      ['pages/workflows/OutboxPanel.tsx', /<ListSkeleton rows=\{3\} \/>/],
    ]
    for (const [rel, re] of cases) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must render a shaped skeleton`).toMatch(re)
    }
  })

  it('the two Outbox detail placeholders are SIZED atoms, deliberately not shaped', () => {
    // One stands in for a single artifact's detail body and one fills a bordered box for a lazy
    // view — neither is a list or a form, so the shaped primitives would be the wrong silhouette.
    const src = strip(readFileSync(join(SRC, 'pages/workflows/OutboxPanel.tsx'), 'utf8'))
    expect(src).toMatch(/<Skeleton className="h-24 w-full" \/>/)
    expect(src).toMatch(/fallback=\{<Skeleton className="h-full w-full" \/>\}/)
  })
})
