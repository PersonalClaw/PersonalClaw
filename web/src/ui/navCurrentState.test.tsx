import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NavRail } from './NavRail'
import { Home, Inbox } from 'lucide-react'

// ── The active nav item was distinguished ONLY visually ─────────────────────────
//
// `NavRail` marked the current page with font weight (470 vs 400) and a tone change, and announced
// nothing. Measured on the live DOM at `#/tasks`: **18 nav buttons, 1 visually distinct, 0 carrying
// any state attribute** — a screen-reader user heard eighteen identical buttons with no sense of
// where they were.
//
// `aria-current="page"` is the NAVIGATION token. Deliberately not `aria-selected`, which belongs to
// listbox/tab options — and the app already uses that one correctly in `Segmented`, `ProjectPicker`,
// `SlashMenu`, `MentionMenu` and `ChatActivityPanel`. So the nav was the outlier against six working
// siblings, not a missing convention.
//
// Verified across four routes that the announcement AGREES with the visual state (the thing that
// makes it a fix rather than a decoration):
//
//     #/tasks    aria-current=["Tasks"]    visually-active=["Tasks"]    ✓
//     #/inbox    aria-current=["Inbox"]    visually-active=["Inbox"]    ✓
//     #/files    aria-current=["Files"]    visually-active=["Files"]    ✓
//     #/prompts  aria-current=["Prompts"]  visually-active=["Prompts"]  ✓
//
// 🪤 **Do not try to verify `aria-current` through CDP `getPartialAXTree` properties or Playwright's
// `ariaSnapshot()` — neither PRINTS it.** I chased that for three probes. The attribute is real and
// selectable (`el.matches('[aria-current="page"]')` is true, and a CSS `[aria-current="page"]` rule
// applies), so the absence from those two views is a TOOLING DISPLAY GAP, not evidence the attribute
// is inert. Assert the rendered attribute instead. Contrast cycle 37, where CDP *did* answer the
// question for `title` — so "ask the a11y tree" is right, but check the tool actually reports the
// property you are asking about before drawing a conclusion from silence.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const ITEMS = [
  { id: 'home', label: 'Home', icon: Home },
  { id: 'inbox', label: 'Inbox', icon: Inbox },
]

describe('NavRail announces which item is current', () => {
  it('exactly the active item carries aria-current="page"', () => {
    const { container } = render(
      <NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />,
    )
    const current = [...container.querySelectorAll('[aria-current="page"]')]
    expect(current).toHaveLength(1)
    expect(current[0].getAttribute('aria-label')).toBe('Inbox')
  })

  it('the inactive item carries NO aria-current at all', () => {
    // `aria-current="false"` is valid ARIA but noisy — an absent attribute is the correct
    // representation of "not current", so the implementation uses `undefined`.
    const { container } = render(
      <NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />,
    )
    const home = container.querySelector('[aria-label="Home"]')!
    expect(home.hasAttribute('aria-current')).toBe(false)
  })

  it('it FOLLOWS activeId rather than being pinned to one item', () => {
    // A hardcoded `aria-current` on the first item would pass the test above and be wrong.
    const { container, rerender } = render(
      <NavRail items={ITEMS} activeId="home" onSelect={() => {}} collapsed={false} />,
    )
    expect(container.querySelector('[aria-current="page"]')?.getAttribute('aria-label')).toBe('Home')
    rerender(<NavRail items={ITEMS} activeId="inbox" onSelect={() => {}} collapsed={false} />)
    expect(container.querySelector('[aria-current="page"]')?.getAttribute('aria-label')).toBe('Inbox')
    expect(container.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
  })

  it('the announcement agrees with the VISUAL active state', () => {
    // The two must not drift apart: a nav that looks like Inbox and announces Home is worse than
    // one that announces nothing. Both derive from the same `active` boolean — pinned at the source.
    const src = strip(readFileSync(join(SRC, 'ui/NavRail.tsx'), 'utf8'))
    expect(src).toMatch(/const active = item\.id === activeId/)
    expect(src).toMatch(/aria-current=\{active \? 'page' : undefined\}/)
    expect(src).toMatch(/withWeight\(\{ height: 32 \}, active \? 470 : 400\)/)
  })

  it('the nav uses aria-current, NOT aria-selected', () => {
    // `aria-selected` is for listbox/tab options; using it on navigation would be a category error,
    // and the app already applies it correctly elsewhere (Segmented, ProjectPicker, the menus).
    const src = strip(readFileSync(join(SRC, 'ui/NavRail.tsx'), 'utf8'))
    expect(/aria-selected/.test(src), 'navigation wants aria-current, not aria-selected').toBe(false)
  })
})
