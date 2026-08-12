import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PanelHeader, Section } from './settingsUI'

// ── Every settings sub-route is a page, so it needs a page heading ───────────────
//
// `PanelHeader` is the title of a settings sub-route — the top-level heading of that page — and it
// rendered as `h2`, with nothing above it. Measured outlines before this change:
//
//   #/settings              h1: Settings  ·  h2: System · h2: AI & Models · …        ✅
//   #/settings/memory       h2: Memory                                    ← 0 h1s
//   #/settings/models       (no h1)                                       ← 0 h1s
//   #/settings/legibility   h2: Legibility · h3: Discover · …             ← 0 h1s, starts at h2
//   #/settings/inbox        h2: Inbox · h3: Alerts · h3: Collection · …   ← 0 h1s, starts at h2
//   #/tasks (control)       h1: Tasks                                                 ✅
//
// After: every route has exactly one `h1` and no skipped level — `h1: Legibility → h2: Discover`
// instead of `h2 → h3`. `Section` moved `h3 → h2` for that reason; a level-3 section under a level-1
// title skips a level (axe `heading-order`).
//
// Both changes are TAG-ONLY. Size comes from `data-type="title-l"` and the section's own class, so all
// six captures (settings, settings-memory, inbox × both themes) are byte-identical.
//
// 🪤 THE ONE TRAP: `InboxSettingsPanel` EXISTS TWICE — `pages/settings/` (this route) and
// `pages/inbox/` (embedded in `#/inbox`'s side panel, under that page's own `h1`). `#/inbox` renders
// the INBOX copy, which does not use `PanelHeader` at all, so no caller needs a lower level and a
// `level` prop would have been speculative API. The test below pins that, because the day the drawer
// starts using `PanelHeader` is the day `#/inbox` grows a second `h1`.

const SETTINGS = join(process.cwd(), 'src', 'pages', 'settings')

describe('PanelHeader is the page heading of a settings sub-route', () => {
  it('renders an h1', () => {
    const { container } = render(<PanelHeader title="Memory" hint="how memory works" />)
    const h = container.querySelector('h1')
    expect(h, 'the panel title is the top-level heading of its page').toBeTruthy()
    expect(h!.textContent).toBe('Memory')
    expect(h!.getAttribute('data-type'), 'size comes from the type role, not the tag').toBe('title-l')
    expect(container.querySelector('h2'), 'and it must not ALSO emit a level-2').toBeNull()
  })

  it('Section renders an h2, so the outline does not skip a level', () => {
    const { container } = render(<Section title="Retention"><p>x</p></Section>)
    expect(container.querySelector('h2')?.textContent).toBe('Retention')
    expect(container.querySelector('h3'), 'h3 under an h1 title skips a level').toBeNull()
  })

  it('every settings panel uses it (not vacuously green)', () => {
    const files = readdirSync(SETTINGS).filter((f) => /Panel\.tsx$/.test(f))
    const users = files.filter((f) => /<PanelHeader\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    expect(users.length, 'PanelHeader is the shared page title — most panels must use it').toBeGreaterThan(20)
  })

  it("the inbox DRAWER copy does not use PanelHeader — #/inbox already has an h1", () => {
    // `pages/inbox/InboxSettingsPanel.tsx` is the copy `#/inbox` mounts inside a SidePanel. If it ever
    // adopts `PanelHeader`, that page gets a second `h1` and this rail says so before a user hears it.
    const drawer = readFileSync(join(process.cwd(), 'src', 'pages', 'inbox', 'InboxSettingsPanel.tsx'), 'utf8')
    expect(drawer.length, 'the drawer copy must exist').toBeGreaterThan(500)
    expect(/<PanelHeader\b/.test(drawer), 'the embedded copy must not emit a page-level heading').toBe(false)
  })
})
