import { describe, expect, it, vi } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RETURN_TO_LATEST_LABEL, SessionMapReturnLatest, scrollToLatest } from './SessionMapReturnLatest'

// ── SSM-9 — return-to-latest control (refactor existing pill) ─────────────────────────────────
//
// done_when: the existing Jump-to-latest pill (`ChatPage.tsx:3057-3068`) becomes the map's
// canonical return-to-newest, still `aria-label='Jump to latest message'`, shown only when
// `scrolledUp`; grep/test proves exactly ONE implementation with no duplicate control.
//
// 🔑 THREE OF THE FOUR SUB-CLAUSES WERE ALREADY TRUE BEFORE THIS ATOM, which is what makes the
// fourth the whole job and this file's vacuity floor the point. Measured on `origin/main`
// 4803c515a before any change: the pill was at `ChatPage.tsx:3057-3068`, carried
// `aria-label="Jump to latest message"`, was gated on `scrolledUp`, and
// `git grep -n 'Jump to latest message' -- web/src` returned EXACTLY ONE hit. A test asserting
// only those three would have passed on the unmodified tree — it would have measured the
// pre-existing pill and reported the atom done. What was false is the load-bearing half: the
// control was `ChatPage`'s own inline JSX and the Session Map had no relationship to it at all,
// so nothing stopped the rail (SSM-11) or the coarse-pointer drawer (SSM-10) from minting a
// second back-to-newest. That is the regression this file exists to make impossible.
//
// VACUITY FLOOR — five ways "exactly one implementation" is satisfiable by something that
// protects nothing, and what is done about each:
//
//  1. A HAND-WRITTEN LIST OF FILES cannot see a duplicate in a directory the list forgot. So the
//     single-implementation census is DERIVED: `allSources()` walks every `.ts`/`.tsx` under `src`
//     and the assertion is on the resulting set, not on a literal path list. A new file anywhere
//     under `src` is in the corpus the moment it exists.
//  2. A CORPUS THAT DID NOT LOAD makes every "exactly one" trivially true (or trivially false in a
//     way someone deletes the test over). The walk asserts a floor on its own size and that it
//     found two named subjects, so a mistyped root fails loudly instead of passing emptily.
//  3. A TEXT SCAN COUNTS PROSE, so under a raw scan DOCUMENTING this rule in a source file would
//     break it and the next contributor would "fix" it by deleting the docs. Comments are blanked
//     (length-preserving) before any match, and the stripper carries its own self-check below.
//     MEASURED, so the claim is sized rather than assumed: across the 655-file corpus both
//     censuses are IDENTICAL stripped and unstripped today — no non-test source discusses either
//     yet, so the stripper is currently DEFENSIVE, not load-bearing. It becomes load-bearing on
//     the first source comment that mentions them, which was verified by adding exactly that file:
//     it goes red on both censuses raw and stays green stripped. This repo has the same defect on
//     file twice already (`design/hitTargetThinHandle.test.ts` for CSS then TSX,
//     `design/primitiveAdoption.baseline.json` for a 13-unit hole), which is why it is paid for up
//     front here instead of after the first false red.
//  4. ASSERTING ON THE LABEL ALONE is evaded by a duplicate that invents a new name. So there is a
//     SECOND, independent derivation over the GESTURE (a smooth scroll to the transcript end),
//     with a known-positive/known-negative pair proving the matcher discriminates: it must see the
//     map module, and it must NOT see `ChatPage`'s two follow-the-stream `block: 'end'` scrolls,
//     which are a different concern and stay where they are.
//  5. "THE MAP OWNS IT" is satisfiable by a file that merely SITS in the map's directory while
//     `ChatPage` still renders its own copy. So the host side is asserted too: `ChatPage` must
//     import and render the map's control, and must contain neither the label nor the gesture.
//
// 🪤 THE BLIND SPOT, STATED RATHER THAN IMPLIED: a duplicate that invents BOTH a new accessible
// name AND a new scroll mechanism passes both derivations. Nothing text-shaped can close that —
// at that point it is a genuinely different affordance, and the honest guard is review of
// SSM-10/SSM-11, not a regex. What IS closed is every cheap copy: the same name, the same
// gesture, or the pill moving back inline into `ChatPage`.

const SRC = join(process.cwd(), 'src')

/** The single-implementation subject, as a repo-relative path so a failure names the file. */
const OWNER = 'pages/chat/SessionMapReturnLatest.tsx'
/** The host that renders it. Named only so the corpus can prove it read a big real file. */
const HOST = 'pages/ChatPage.tsx'

/** Blank every comment, length-preserving, before any scan — see vacuity floor #3. Same shape as
 *  `design/hitTargetThinHandle.test.ts`'s, and for the same reason: a scanner that cannot tell a
 *  declaration from a sentence about a declaration is measuring the wrong thing. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

/** Every non-test `.ts`/`.tsx` under `src`. `.ts` is included deliberately: a duplicate control
 *  could be minted by a helper module, and a `.tsx`-only walk would not look at one. */
function allSources(dir: string, prefix = ''): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name
    if (e.isDirectory()) { out.push(...allSources(join(dir, e.name), rel)); continue }
    if (/\.tsx?$/.test(e.name) && !/\.(test|spec)\./.test(e.name)) out.push(rel)
  }
  return out
}

const SOURCES = allSources(SRC)
const CODE = new Map(SOURCES.map((rel) => [rel, code(readFileSync(join(SRC, rel), 'utf8'))]))

/** Does this source perform the return-to-newest GESTURE — a smooth scroll to an element's end?
 *
 *  Read out of the option object rather than matched as a flat string, so key order cannot hide a
 *  copy, and BOTH keys are required: `block: 'end'` alone is the follow-the-stream scroll
 *  (`ChatPage.tsx:346`/`:1461`), which is instant and conditional and is not this control. */
function scrollsToLatest(src: string): boolean {
  for (const m of src.matchAll(/scrollIntoView\(\s*\{([^}]*)\}/g)) {
    const opts = m[1]
    if (/behavior:\s*'smooth'/.test(opts) && /block:\s*'end'/.test(opts)) return true
  }
  return false
}

const filesWithLabel = SOURCES.filter((rel) => CODE.get(rel)!.includes(RETURN_TO_LATEST_LABEL))
const filesWithGesture = SOURCES.filter((rel) => scrollsToLatest(CODE.get(rel)!))

describe('SSM-9 · the Session Map\'s return-to-newest control', () => {
  it('is absent when the transcript is at the bottom, and present when it is not', async () => {
    const { rerender } = render(<SessionMapReturnLatest scrolledUp={false} onReturnToLatest={() => {}} />)
    // Asserted ABSENT first: "shown only when scrolledUp" is satisfiable by an always-mounted
    // control if only the present case is checked.
    expect(screen.queryByRole('button', { name: RETURN_TO_LATEST_LABEL })).toBeNull()

    rerender(<SessionMapReturnLatest scrolledUp onReturnToLatest={() => {}} />)
    expect(screen.getByRole('button', { name: RETURN_TO_LATEST_LABEL })).toBeInTheDocument()

    // …and it goes away again, so the gate is the live prop rather than a mount-time read.
    rerender(<SessionMapReturnLatest scrolledUp={false} onReturnToLatest={() => {}} />)
    await waitFor(() => expect(screen.queryByRole('button', { name: RETURN_TO_LATEST_LABEL })).toBeNull())
  })

  it('keeps the accessible name the atom pins, exactly', () => {
    render(<SessionMapReturnLatest scrolledUp onReturnToLatest={() => {}} />)
    // The literal, not the constant, so renaming the constant's VALUE cannot quietly rename the
    // control while this file follows it.
    expect(RETURN_TO_LATEST_LABEL).toBe('Jump to latest message')
    const control = screen.getByRole('button', { name: 'Jump to latest message' })
    expect(control).toHaveAttribute('aria-label', 'Jump to latest message')
    // 🔑 THE OWNER'S REFERENCE (2026-09-25) MAKES IT A CIRCULAR DOWN-ARROW, superseding §A.7's text
    // pill. What §A.7 was protecting survives: the NAME is unchanged, and the words the control no
    // longer shows become its tooltip, so a pointer user can still read what it does before pressing
    // it (the reference's own friction list faults unexplained icon-only controls).
    expect(control).toHaveAttribute('title', 'Jump to latest message')
    expect(control.className.split(/\s+/)).toContain('rounded-full')
    expect(control.textContent, 'the circular form shows the arrow only').toBe('')
    expect(control.querySelector('svg'), 'the down-arrow is the control').not.toBeNull()
  })

  it('returns to the newest message on click — once per click', () => {
    const onReturnToLatest = vi.fn()
    render(<SessionMapReturnLatest scrolledUp onReturnToLatest={onReturnToLatest} />)
    fireEvent.click(screen.getByRole('button', { name: RETURN_TO_LATEST_LABEL }))
    // Exactly one: a control that fires twice per press scrolls, re-renders and scrolls again,
    // which reads as a stutter rather than a jump.
    expect(onReturnToLatest).toHaveBeenCalledTimes(1)
  })

  it('the gesture scrolls the transcript end into view smoothly, and tolerates no anchor', () => {
    const scrollIntoView = vi.fn()
    scrollToLatest({ scrollIntoView } as unknown as Element)
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'end' })
    // The transcript can have no end anchor yet (an unmounted or empty transcript). A no-op, not
    // a crash — asserted, because `?.` is exactly the kind of thing a refactor drops.
    expect(() => scrollToLatest(null)).not.toThrow()
    expect(() => scrollToLatest(undefined)).not.toThrow()
  })
})

describe('SSM-9 · exactly one implementation, derived over src', () => {
  it('read a real corpus (vacuity floor: an empty walk proves nothing)', () => {
    expect(
      SOURCES.length,
      `The census corpus is ${SOURCES.length} files. A mistyped root yields an empty corpus and ` +
        `every "exactly one" assertion below becomes trivial.`,
    ).toBeGreaterThan(300)
    expect(SOURCES, 'the walk must have found its own subject').toContain(OWNER)
    expect(SOURCES, 'the walk must have found the host that renders it').toContain(HOST)
    expect(SOURCES.some((f) => /\.(test|spec)\./.test(f)), 'test files must be excluded').toBe(false)
    expect(CODE.get(HOST)!.length, 'ChatPage did not read').toBeGreaterThan(10_000)
  })

  it('the comment stripper works, so documenting the rule cannot break it', () => {
    // Vacuity floor #3, self-checked: if this stops blanking prose, the two censuses below start
    // counting sentences and go red here first, rather than silently counting this file.
    expect(code(`// a comment mentioning ${RETURN_TO_LATEST_LABEL}`)).not.toContain(RETURN_TO_LATEST_LABEL)
    expect(code(`/** a doc block mentioning ${RETURN_TO_LATEST_LABEL} */`)).not.toContain(RETURN_TO_LATEST_LABEL)
    // …and it must NOT blank the declaration it is protecting.
    expect(code(`const L = '${RETURN_TO_LATEST_LABEL}'`)).toContain(RETURN_TO_LATEST_LABEL)
    // A URL is not a comment — the guard that keeps `https://` intact has to stay.
    expect(code(`const u = 'https://example.invalid/x'`)).toContain('https://example.invalid/x')
  })

  it('exactly one source declares the control\'s accessible name, and it is the map\'s', () => {
    expect(
      filesWithLabel,
      `"${RETURN_TO_LATEST_LABEL}" must be declared in exactly one non-test source. Found ` +
        `${filesWithLabel.length}: ${filesWithLabel.join(', ')}. A second one is a duplicate ` +
        `return-to-newest control — SSM-9 exists to keep there being one.`,
    ).toEqual([OWNER])
    // "The MAP's canonical return-to-newest": the one implementation lives in the map's own
    // module set, beside the rail and the card, not in a page that happens to render it.
    expect(OWNER).toMatch(/^pages\/chat\/SessionMap/)
  })

  it('exactly one source performs the return-to-newest gesture', () => {
    expect(
      filesWithGesture,
      `A smooth scroll to a transcript end must exist in exactly one non-test source. Found ` +
        `${filesWithGesture.length}: ${filesWithGesture.join(', ')}. This is the derivation that ` +
        `catches a duplicate control minted under a DIFFERENT name.`,
    ).toEqual([OWNER])
  })

  it('the gesture matcher discriminates (known positive + known negative)', () => {
    // Known positive: the real module, read from disk, must match.
    expect(scrollsToLatest(CODE.get(OWNER)!), 'the matcher cannot see the control it is about').toBe(true)
    // Known negative, and this is the pair that proves the matcher is not just "always false":
    // ChatPage still scrolls to the end to FOLLOW the stream, and those must not be counted.
    const host = CODE.get(HOST)!
    expect(host, 'the follow-the-stream scrolls must still be in ChatPage').toMatch(/scrollIntoView\(\{ block: 'end' \}\)/)
    expect(scrollsToLatest(host), 'an instant conditional follow is not a return-to-newest').toBe(false)
    // And a hand-built duplicate DOES trip it, in either key order — the shape a copy/paste takes.
    expect(scrollsToLatest(`el.scrollIntoView({ behavior: 'smooth', block: 'end' })`)).toBe(true)
    expect(scrollsToLatest(`el.scrollIntoView({ block: 'end', behavior: 'smooth' })`)).toBe(true)
  })

  it('the transcript RENDERS the map\'s control instead of defining one', () => {
    const host = CODE.get(HOST)!
    expect(host, 'ChatPage must import the map\'s control').toMatch(
      /import \{[^}]*SessionMapReturnLatest[^}]*\} from '\.\/chat\/SessionMapReturnLatest'/,
    )
    // 🪤 Matched with a TERMINATOR, not as a substring: `toContain('<SessionMapReturnLatest')` was
    // measured GREEN against `<SessionMapReturnLatestX` — a marker string that is a substring of
    // its own negation, which is how this assertion passed while the pill rendered nowhere.
    expect(host, 'ChatPage must render it').toMatch(/<SessionMapReturnLatest[\s/>]/)
    // The half that was false before this atom: no inline pill of its own, under either
    // derivation. Redundant with the two censuses by construction — kept because THIS is the
    // file a regression would land in, and a failure here names it directly.
    expect(host, 'the pill must not have come back inline').not.toContain(RETURN_TO_LATEST_LABEL)
  })
})
