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
// 🪤 `ui/Segmented` IS DELIBERATELY UNTOUCHED. Its tab-vs-option semantics is an OPEN OWNER RULING
// (46 call sites, 0 tabpanels), and giving it tab-style arrow navigation would quietly decide that
// question. Worth noting for whoever rules: it needs arrow keys EITHER way — a radiogroup uses them
// too — so only the roles and the activation semantics are actually in question.
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

  const sites = () => walk(SRC)
    .map((abs) => ({ file: abs.slice(SRC.length + 1), src: readFileSync(abs, 'utf8') }))
    .filter((f) => /role="tab"/.test(f.src))

  it('finds the population (not vacuously green)', () => {
    expect(sites().length, 'the role="tab" census must not go empty').toBeGreaterThanOrEqual(6)
  })

  it('each one declares a tablist, roving tabIndex and aria-selected', () => {
    const bad: string[] = []
    for (const { file, src } of sites()) {
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
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
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${file} re-implements arrow navigation`).not.toMatch(/'ArrowLeft'.*'ArrowRight'|ArrowLeft' \?/)
    }
  })

  it('ui/Segmented is the one deliberate exception, and it is named', () => {
    // Its tab-vs-option semantics is an open owner ruling; adding tab-style arrow navigation would
    // decide it. If the ruling lands, this expectation is what should change first.
    const seg = readFileSync(join(SRC, 'ui/Segmented.tsx'), 'utf8')
    expect(seg).toMatch(/role="tablist"/)
    expect(seg, 'Segmented must NOT adopt tab arrow-nav before the owner rules').not.toMatch(/tabListKeys/)
  })
})
