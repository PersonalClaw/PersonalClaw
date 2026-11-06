// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FolderOpen, HardDrive } from 'lucide-react'
import { Segmented } from './Segmented'

// ── A folded view switch's name holds the value it shows ─────────────────────────────────────────
//
// Folded to its one pill, a switch was named by its dimension alone ("File root"), while the pill
// showed the value ("Workspace"). A voice user says what they see, and "click Workspace" matched
// nothing (WCAG 2.5.3, label in name). The name is the dimension and the value now.

const OPTIONS = [
  { key: 'ws', label: 'Workspace', icon: FolderOpen },
  { key: 'home', label: 'Home folder', icon: HardDrive },
]

// jsdom lays nothing out, so the switch never measures a fold; give its probe a width the row
// cannot hold.
beforeEach(() => { Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => 400 }) })
// The stub is the prototype's own property, so deleting it gives `Element`'s getter back.
afterEach(() => { delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth })

describe('a folded view switch', () => {
  it('is named by its dimension and the value it shows', () => {
    render(<div><Segmented collapse="menu" ariaLabel="File root" value="ws" onChange={() => {}} options={OPTIONS} /></div>)
    const pill = screen.getByRole('button', { name: 'File root: Workspace' })
    expect(pill.getAttribute('aria-haspopup')).toBe('listbox')
    expect(pill.textContent).toContain('Workspace')
  })

  it('and by the value alone where it has no dimension to say', () => {
    render(<div><Segmented collapse="menu" value="home" onChange={() => {}} options={OPTIONS} /></div>)
    expect(screen.getByRole('button', { name: 'Home folder' })).toBeTruthy()
  })
})
