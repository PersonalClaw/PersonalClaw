import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { tabListKeys } from './tabListKeys'

// ── Four strips announced tabs, and three of them could not be reached at all ────────────────
//
// `#/terminal` was the worst, and it took two live sessions to see it — with sessions off the strip
// does not render, which is why FOUR previous audits of that surface reported it clean. Measured
// with two sessions open:
//
//   2 × [role=tab]      `tabindex` **null** on both · `aria-selected` **null** on both
//   [role=tablist]      **0**
//   Tab presses         **0 of 45** ever landed on a tab
//   axe                 `aria-required-parent` (critical) ×2 · `nested-interactive` (serious) ×2
//   close buttons       16×16 (SC 2.5.8 wants 24), all named the same "Close session"
//
// A census of every `role="tab"` in the tree found the shape repeated: **4 of 6 sites had no owning
// tablist**, and three of those built their tabs from `div`s.
//
//   ui/Segmented                     tablist ✓  roving ✓  selected ✓   ← left alone, see below
//   pages/chat/ChatActivityPanel     tablist ✓  roving ✓  selected ✓  arrows ✓  ← the canonical one
//   pages/loops/LoopCockpitPage      tablist ✗  roving ✗  selected ✓
//   pages/files/FilesSection         tablist ✗  tabIndex={0} on EVERY tab  selected ✗
//   pages/terminal/TerminalPage      tablist ✗  roving ✗  selected ✗
//   pages/terminal/TerminalDrawer    tablist ✗  roving ✗  selected ✗
//
// 🔑 THE CANONICAL FORM ALREADY EXISTED, so nothing was invented: `ChatActivityPanel` shipped the
// right thing — `role="tablist"` + `aria-label` + roving `tabIndex` + an arrow/Home/End handler. Its
// handler moved here and that panel now reads from this copy, so the count of implementations went
// 1-correct-plus-3-missing → **one, shared by four**.
//
// 🔑 `ui/Segmented`'s OPEN OWNER RULING LANDED IN #3472, AND IT WENT THE OTHER WAY: it is a
// RADIOGROUP. The note here used to record the question (46 call sites — 50 by the time it was
// ruled — and 0 tabpanels) and warn that giving it tab-style arrow navigation would quietly decide
// it. The ruling made the count the argument: there is no `role="tabpanel"` anywhere in the app
// except `ChatActivityPanel`'s two, so every one of those call sites announced a tabbed interface
// that does not exist, and a screen reader said "tab 2 of 6" for the task form's Status field.
//
// The prediction in the old note held exactly: it needed arrow keys either way, so the handler
// did not change at all — only the roles did. `Segmented` still does not use `tabListKeys`, but
// now because it is not a tab strip rather than because a question was open. §"Segmented is a
// radiogroup" below is the same expectation, inverted.
//
// 🪤 AND ONE axe FINDING IS LEFT STANDING ON PURPOSE. A closable tab is `nested-interactive` unless
// its close control stops being a control. Both alternatives were built and measured on `#/terminal`:
// a real `<button>` tab with the close button as a presentational sibling clears that rule but
// raises **`aria-required-children` (critical)**, because a tablist's owned children must be tabs and
// axe does not look through the wrapper — and `aria-owns` listing the tab ids changed nothing, since
// the tabs are already DOM descendants. Rendering the glyph as a non-interactive `<span>` and closing
// only via Delete goes fully green, but removes the close control from the accessibility tree, which
// is optimising the scanner at the user's expense. So: 2 blocking findings → 1, the critical one
// gone, and the residual named here rather than chased.
//
// Driven after, keyboard only:
//   #/terminal      Tab → "Session 1"; ArrowRight → Session 2 (focus AND selection), ArrowRight
//                   wraps to Session 1, ArrowLeft → Session 2, End/Home → last/first
//   #/files         two files open: ArrowRight wraps last→first, Home → first, close box 24×24
//   loop cockpit    "Loop views" tablist, roving tabIndex; one tab, so arrows no-op by design
//   close buttons   now "Close Session 1" / "Close Session 2" — one name per control

function Strip({ onSelect = vi.fn(), n = 3, selected = 0 }: { onSelect?: (i: number) => void; n?: number; selected?: number }) {
  return (
    <div role="tablist" aria-label="Test" onKeyDown={tabListKeys(onSelect)}>
      {Array.from({ length: n }, (_, i) => (
        <button key={i} type="button" role="tab" aria-selected={i === selected} tabIndex={i === selected ? 0 : -1}>
          tab{i}
        </button>
      ))}
    </div>
  )
}

const tabs = () => screen.getAllByRole('tab')

describe('tabListKeys', () => {
  it('ArrowRight moves to the next tab and selects it', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} />)
    tabs()[0].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith(1)
    expect(document.activeElement).toBe(tabs()[1])
  })

  it('wraps in both directions, so neither end dead-ends', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={2} />)
    tabs()[2].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenLastCalledWith(0)
    tabs()[0].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowLeft' })
    expect(onSelect).toHaveBeenLastCalledWith(2)
  })

  it('Home and End jump to the ends', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={1} />)
    tabs()[1].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'End' })
    expect(onSelect).toHaveBeenLastCalledWith(2)
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'Home' })
    expect(onSelect).toHaveBeenLastCalledWith(0)
  })

  it('falls back to the SELECTED tab when focus is not on one', () => {
    // The case this exists for: clicking a tab's close button leaves focus off the strip, and the
    // next arrow press must still move relative to what is selected rather than from nowhere.
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={1} />)
    ;(document.activeElement as HTMLElement)?.blur()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith(2)
  })

  it('ignores keys that are not navigation', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} />)
    for (const key of ['a', 'Enter', ' ', 'Tab', 'ArrowUp', 'Escape']) {
      fireEvent.keyDown(screen.getByRole('tablist'), { key })
    }
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('does nothing for a single tab, so a one-session strip is not a trap', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} n={1} />)
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('skips a disabled tab', () => {
    const onSelect = vi.fn()
    render(
      <div role="tablist" aria-label="Test" onKeyDown={tabListKeys(onSelect)}>
        <button type="button" role="tab" aria-selected tabIndex={0}>a</button>
        <button type="button" role="tab" aria-selected={false} tabIndex={-1} disabled>b</button>
        <button type="button" role="tab" aria-selected={false} tabIndex={-1}>c</button>
      </div>,
    )
    screen.getByRole('tab', { name: 'a' }).focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    // Index 1 among the ENABLED tabs — the disabled one is not a stop, and the callback's index is
    // therefore the enabled-tab index, which is what the call sites map through.
    expect(onSelect).toHaveBeenCalledWith(1)
  })
})

describe('every tab strip in the tree is a real tablist', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** Comments blanked, newlines kept — a census measures the PROGRAM, not the explanation of it.
   *
   *  🪤 MEASURED WHILE LANDING #3472. `Segmented.tsx` stopped declaring `role="tab"` and started
   *  EXPLAINING, in its docstring, that it no longer does — and because the filter below read raw
   *  source, the file re-entered the census on its own prose and then failed the tablist check with
   *  `no role="tablist", no aria-selected`. A census that counts the sentence documenting a fix as
   *  an instance of the defect will always red the commit that fixes it. */
  const codeOf = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/^[ \t]*\/\/.*$/gm, '')

  const sites = () => walk(SRC)
    .map((abs) => ({ file: abs.slice(SRC.length + 1), src: codeOf(readFileSync(abs, 'utf8')) }))
    .filter((f) => /role="tab"/.test(f.src))

  it('finds the population (not vacuously green)', () => {
    expect(sites().length, 'the role="tab" census must not go empty').toBeGreaterThanOrEqual(6)
  })

  it('each one declares a tablist, roving tabIndex and aria-selected', () => {
    const bad: string[] = []
    for (const { file, src: code } of sites()) {
      const problems = [
        /role="tablist"/.test(code) ? '' : 'no role="tablist"',
        /aria-selected/.test(code) ? '' : 'no aria-selected',
        // Roving: exactly one tab is reachable. `tabIndex={0}` on every tab is the FilesSection
        // defect — N stops in the tab order — so a bare `tabIndex={0}` next to role="tab" fails.
        /tabIndex=\{[^}]*\?\s*0\s*:\s*-1\}/.test(code) ? '' : 'no roving tabIndex',
      ].filter(Boolean)
      if (problems.length) bad.push(`${file}: ${problems.join(', ')}`)
    }
    expect(bad, `a strip announces tabs without being a tablist:\n${bad.join('\n')}`).toEqual([])
  })

  it('the arrow-key handler has ONE implementation, shared by four strips', () => {
    const adopters = sites().filter((f) => /tabListKeys\(/.test(f.src)).map((f) => f.file)
    expect(adopters.length, `adopters: ${adopters.join(', ')}`).toBeGreaterThanOrEqual(4)
    // And nobody re-grew a local copy: the threshold chain lives in lib/tabListKeys only.
    for (const { file, src } of sites()) {
      if (file === 'lib/tabListKeys.ts') continue
      expect(src, `${file} re-implements arrow navigation`).not.toMatch(/'ArrowLeft'.*'ArrowRight'|ArrowLeft' \?/)
    }
  })

  it('ui/Segmented is a RADIOGROUP, so it is not in this census at all (#3472)', () => {
    // The inverse of what this expectation used to assert, and the reason the census above needed a
    // comment stripper: `Segmented` documents the roles it gave up, in prose, in this file's reach.
    const seg = codeOf(readFileSync(join(SRC, 'ui/Segmented.tsx'), 'utf8'))
    expect(seg, 'the ruling landed: single-choice fields are radio groups').toMatch(/role="radiogroup"/)
    expect(seg).toMatch(/role="radio"/)
    expect(seg).toMatch(/aria-checked=/)
    expect(seg, 'a radiogroup must not announce tabs').not.toMatch(/role="tab(list)?"/)
    expect(seg, 'nor a tab selection state').not.toMatch(/aria-selected/)
    // Still not an adopter, but now because it is not a tab strip — its own arrow handler is the
    // radiogroup model (move AND select), which is what tabListKeys deliberately is not.
    expect(seg, 'Segmented is not a tab strip and must not borrow tab keys').not.toMatch(/tabListKeys/)
    expect(sites().map((f) => f.file), 'Segmented left the tab census').not.toContain('ui/Segmented.tsx')
  })

  it('and the only tab strips left are the six that reveal something', () => {
    // The vacuity floor for the clause above: it is also satisfied by a tree with no tab strips at
    // all. These six are hand-rolled view switchers, untouched by #3472, and each one swaps a pane.
    expect(sites().map((f) => f.file).sort()).toEqual([
      'pages/chat/ChatActivityPanel.tsx',
      'pages/files/FilesSection.tsx',
      'pages/loops/LoopCockpitPage.tsx',
      'pages/settings/MemoryPanel.tsx',
      'pages/terminal/TerminalDrawer.tsx',
      'pages/terminal/TerminalPage.tsx',
    ])
  })
})
