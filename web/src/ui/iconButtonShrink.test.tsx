import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ArrowLeft } from 'lucide-react'
import { IconButton } from './IconButton'

// ── A declared size is not a floor ───────────────────────────────────────────────
//
// `IconButton` sets its box with inline `style={{ width: size, height: size }}`. As a flex child with
// the default `flex-shrink: 1`, that width is a STARTING POINT, not a minimum — a crowded row takes it
// back. Measured at 390×844 on the settings sub-routes, where the back arrow sits in a breadcrumb row:
//
//     size={36}  →  rendered 20 × 36
//
// 20px fails the 24px SC 2.5.8 minimum on the width axis, and the undersized-target spacing exception
// cannot rescue it: the next target ("Settings", the crumb) sits **4px** away, so the 24px circles
// intersect. Note axe's `target-size` rule did NOT flag it — the box is not small in both axes — so this
// rests on the criterion's own wording, measured.
//
// Census at 390px across 12 surfaces, comparing each icon button's rendered width against its declared
// `style.width`: **2 squeezed sites, both this control** (`#/settings/models`, `#/settings/legibility`);
// every other icon button already rendered at its declared size. After `shrink-0`: 0 squeezed, no new
// page overflow anywhere, and the breadcrumb's truncating section label went 20px → 58px because the
// duplicate crumb dropped below `sm` in the same change.
//
// This is the second primitive with this exact bug — `Segmented`'s tab was the first (its `size-8` was
// likewise a size and not a floor). 🔑 **A width utility or inline width says nothing until `shrink-0`
// or `min-w-*` says it cannot be taken away.**

describe('IconButton cannot be squeezed below its declared size', () => {
  it('declares shrink-0 on the button itself', () => {
    const src = readFileSync(join(process.cwd(), 'src', 'ui', 'IconButton.tsx'), 'utf8')
    expect(src, 'an inline width is not a minimum for a flex child').toMatch(
      /inline-flex shrink-0 items-center justify-center rounded-pill/,
    )
  })

  it('renders shrink-0 at every size', () => {
    for (const size of [28, 36, 40]) {
      const { container, unmount } = render(
        <IconButton icon={ArrowLeft} label={`back-${size}`} size={size} onClick={() => {}} />,
      )
      const btn = container.querySelector('button')!
      expect(btn.className, `size=${size}`).toMatch(/\bshrink-0\b/)
      // The declared box must still be the declared box — this is a floor, not a resize.
      expect(btn.style.width, `size=${size}`).toBe(`${size}px`)
      expect(btn.style.height, `size=${size}`).toBe(`${size}px`)
      unmount()
    }
  })
})

describe('the settings breadcrumb spends its width on where you are', () => {
  const src = readFileSync(join(process.cwd(), 'src', 'pages', 'settings', 'SettingsPage.tsx'), 'utf8')

  it('drops the duplicate "Settings" crumb below sm', () => {
    // The back arrow is already named "Back to Settings", so the crumb + chevron are the only duplicate
    // in the row. Keeping them at 390px left 4px for the section name once the arrow was un-squeezed.
    expect(src).toMatch(/hidden shrink-0 items-center gap-1 sm:inline-flex/)
  })

  it('keeps the back arrow unconditional — navigation may not be viewport-gated', () => {
    const back = src.slice(src.indexOf('label="Back to Settings"') - 200, src.indexOf('label="Back to Settings"') + 80)
    expect(back).toMatch(/<IconButton icon=\{ArrowLeft\}/)
    expect(back, 'the arrow must not be hidden at any width').not.toMatch(/hidden\s+\w*:?(inline|flex|block)/)
  })
})
