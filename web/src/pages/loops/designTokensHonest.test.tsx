import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TokensView, ContrastView } from './DesignCockpitPage'

// ── One swallowed rejection produced FIVE false statements, two of them downloadable files ────────
//
// `DesignCockpitPage` loaded its token set with
//
//     api.uLoopDesignTokens(id, scheme).then(setTokens).catch(() => {})
//
// and then FIVE separate consumers inferred their entire state from `tokens === null`. That is what
// makes a dropped rejection expensive: it is not one bug, it is one cause with five faces.
//
//   1. `TokensView`  → "Loading tokens…"  FOREVER
//   2. `ContrastView` → "Loading tokens…"  FOREVER (identical, different tab)
//   3. Exports → "CSS variables" Download permanently dead, with NO reason given
//   4. Exports → "Token set (JSON)" NOT gated at all → downloads `{"resolved":{}}`
//   5. Exports → "DESIGN.md" NOT gated → `buildDesignMd(loop, null)` lists no token groups
//
// 🔑 A PERMANENT "Loading…" IS THE WORST OF THE THREE POSSIBLE MESSAGES. It is false, it blames the
// network, and it gives the user nothing to do — so they sit waiting on a request that already
// finished. An error at least ends the wait.
//
// 🔴 BUT (4) AND (5) ARE THE ONES THAT ACTUALLY COST THE USER SOMETHING. Neither row was disabled,
// and both interpolate `tokens?.… || {}`. So a failed read handed over a plausible, well-formed
// artifact whose CONTENT was wrong. The JSON row's own copy is "feed into any build pipeline" —
// which is exactly what makes it dangerous: the file leaves the app and lands in a build, where
// "resolved: {}" reads as a deliberate empty token set rather than a failed download.
// A dead button is a VISIBLE defect a user can report. A fabricated export is an INVISIBLE one they
// act on. Both are fixed here, but they are not the same severity and the diff treats (4)/(5) as the
// reason this change exists.
//
// 🪤 THE DESIGN.md GATE IS `!authoredDoc && !tokens`, NOT `!tokens`. `downloadDesignMd` PREFERS the
// loop's authored DESIGN.md artifact and only falls back to `buildDesignMd(loop, tokens)`. So an
// unread token set matters there ONLY when there is no authored doc to serve instead — gating on
// `!tokens` alone would have disabled a download that was going to be perfectly correct. Narrowing
// one branch of a fallback chain means checking every branch of it.
//
// 🪤 AND THE REASON TEXT ITSELF BRANCHES. `!tokens` is true for the in-flight read too, so a flat
// "could not be read" would trade one false statement for another — reintroducing the exact
// conflation this change removes, inside the fix.

const okTokens = {
  scheme: 'light',
  css: ':root{--x:1}',
  resolved: { color: { semantic: { light: { primary: '#ff6b5b' } } } },
} as never

describe('a tokens view distinguishes “not read yet” from “could not be read”', () => {
  it('an in-flight read still says Loading — the honest state, unchanged', () => {
    render(<TokensView tokens={null} scheme="light" />)
    expect(screen.getByText('Loading tokens…')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a FAILED read stops saying Loading and offers the retry it never had', () => {
    const onRefresh = vi.fn()
    render(<TokensView tokens={null} tokensErr={new Error('gateway down')} onRefresh={onRefresh} scheme="light" />)
    expect(screen.queryByText('Loading tokens…'), 'a finished request is not still loading').toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Couldn’t read this design system’s tokens/i)).toBeInTheDocument()
    screen.getByRole('button', { name: /Try again/i }).click()
    expect(onRefresh, 'without this the honest message is a dead end').toHaveBeenCalled()
  })

  it('the Contrast tab gets the same treatment — it had the identical lie', () => {
    // 🪤 Two tabs, one copy-pasted `if (!tokens) return <div>Loading tokens…</div>`. Fixing only the
    // Tokens tab would leave the user one click away from the same false message.
    render(<ContrastView tokens={null} tokensErr={new Error('gateway down')} onRefresh={vi.fn()} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('a SUCCESSFUL read renders the real view — or the assertions above prove nothing', () => {
    render(<TokensView tokens={okTokens} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the source no longer swallows the token read, and the exports no longer fabricate', () => {
  const src = () => readFileSync(join(process.cwd(), 'src/pages/loops/DesignCockpitPage.tsx'), 'utf8')
  const code = () => src().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

  it('the rejection is recorded instead of dropped', () => {
    // The load-bearing half: with `.catch(() => {})` in place, every DOM test above could pass on a
    // prop that production never supplies.
    expect(code(), 'uLoopDesignTokens must not swallow').not.toMatch(/uLoopDesignTokens\([^)]*\)[\s\S]{0,120}?\.catch\(\(\) => \{\}\)/)
    expect(code()).toMatch(/setTokensErr\(e/)
  })

  it('a retry clears the previous failure', () => {
    // Otherwise the error line outlives the error and the view keeps refusing tokens it just read.
    expect(code()).toMatch(/setTokensErr\(null\)/)
  })

  it('the JSON export is gated — it used to download a fabricated empty token set', () => {
    // 🔴 The highest-cost half of this change. `resolved: tokens?.resolved || {}` on an unread read
    // produced a syntactically perfect file asserting the design system had no tokens.
    const row = code().match(/title="Token set \(JSON\)"[\s\S]{0,400}?\/>/)?.[0] ?? ''
    expect(row, 'the JSON row must exist to be gated').not.toBe('')
    expect(row, 'an unread token set must not be exportable').toMatch(/disabled=\{!tokens\}/)
    expect(row, 'and a dead button must say why').toMatch(/disabledReason=/)
  })

  it('the DESIGN.md export is gated on BOTH conditions, not just the token read', () => {
    // 🪤 `!tokens` alone would disable a download that the authored artifact would have satisfied
    // correctly. This asserts the narrower, correct gate — and would fail an over-broad "fix".
    // 🪤 The `not.toBe('')` guard below is load-bearing, and it earned its place: the first draft of
    // this test used a 500-char window, the row is longer than that, and the match came back empty.
    // Without the guard, `expect('').toMatch(…)` would simply have failed with a confusing message —
    // or worse, a `.not.toMatch` assertion would have PASSED against nothing at all.
    const row = code().match(/title="DESIGN\.md"[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(row, 'the row must be matched before it can be asserted on').not.toBe('')
    expect(row, 'the authored doc makes an unread token set irrelevant').toMatch(/disabled=\{!authoredDoc && !tokens\}/)
  })

  it('ExportRow can carry a reason at all — before this it structurally could not', () => {
    // The row accepted `disabled` with no reason channel, so no caller could explain its own gate.
    // `Button.disabledReason` also swaps native `disabled` for `aria-disabled`, keeping the tab stop.
    expect(code()).toMatch(/function ExportRow\([^)]*disabledReason/)
    expect(code(), 'and it must reach the Button, not just sit in the signature').toMatch(/disabledReason=\{disabledReason\}/)
  })

  it('the disabled reason distinguishes loading from failure', () => {
    // Otherwise the fix reintroduces its own bug: "could not be read" during a normal load.
    expect(code(), 'a mid-load gate must not claim the read failed').toMatch(/tokensErr\s*\n?\s*\?/)
    expect(code()).toMatch(/Still reading the token set/)
  })
})
