import { describe, expect, it, vi } from 'vitest'
import { render, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SavedToast, ToggleRow, NumberRow } from './settingsUI'

// ── A saved setting must be announced, not only shown ───────────────────────────────────────────
//
// `SavedToast` is the ONLY success surface a settings control has: the config PATCH is silent, the
// control's own value was already changed optimistically, and the confirmation disappears after
// 1500ms. So a user who cannot see the span is never told the setting persisted — WCAG 4.1.3
// (Status Messages), the same gap already fixed for the recorder and for onboarding progress.
//
// Census of the family before choosing where to fix it (measured on the tree this landed on):
//
//   SavedToast rendered directly   25 instances / 13 files
//   ...via ToggleRow               19 instances /  8 files
//   ...via NumberRow               28 instances /  7 files
//   live regions in settingsUI     0
//
// 72 confirmations, none announced, all flowing through ONE primitive — so the fix belongs in
// `SavedToast` and every panel inherits it. Fixing a single panel would have fragmented the family.
//
// Deliberately NOT `ResultAnnouncement`: that component is list-specific (`count`/`noun`/`active`)
// and can only say "N tasks" / "No matching tasks". Its doc warns against a second copy of the
// LIST-RESULT region, which is why `AudioRecorder` and `Onboarding` carry their own regions too.

const SRC = join(process.cwd(), 'src')
const settingsUI = () => readFileSync(join(SRC, 'pages/settings/settingsUI.tsx'), 'utf8')

describe('SavedToast announces the save', () => {
  it('renders a polite status region that is EMPTY at rest', () => {
    // Always mounted: a region created at the moment its text appears is not reliably observed.
    const { container } = render(<SavedToast show={false} />)
    const region = container.querySelector('[role="status"]')
    expect(region, 'the region must exist even when nothing has been saved').not.toBeNull()
    expect(region!.getAttribute('aria-live')).toBe('polite')
    expect(region!.textContent).toBe('')
    // ...and it must not be visible chrome — the visual "Saved ✓" already covers sighted users.
    expect(region!.className).toContain('sr-only')
  })

  it('carries the confirmation text once shown', () => {
    render(<SavedToast show={true} />)
    const region = document.querySelector('[role="status"]')!
    expect(region.textContent).toBe('Saved')
  })

  it('does not announce the confirmation twice', () => {
    // The visual span repeats what the region says, so it must be out of the a11y tree — otherwise
    // the user hears "Saved" from the region and "Saved check mark" again when navigating the row.
    const { container } = render(<SavedToast show={true} />)
    const visual = [...container.querySelectorAll('span')].find((s) => s.textContent?.includes('✓'))
    expect(visual, 'the visual confirmation must still render for sighted users').toBeTruthy()
    expect(visual!.getAttribute('aria-hidden')).toBe('true')
    // Exactly one node in the a11y tree says "Saved".
    expect(container.querySelectorAll('[role="status"]').length).toBe(1)
  })

  it('the region is mounted before the flash, in the real ToggleRow path', () => {
    // The primitive is only useful if the rows that own the `saved` state render it at rest too.
    const { container } = render(
      <ToggleRow label="LAN discovery" cfg={{ discovery_enabled: false }} field="discovery_enabled"
        patch={() => {}} />,
    )
    const region = container.querySelector('[role="status"]')
    expect(region, 'ToggleRow must mount the region before anything is saved').not.toBeNull()
    expect(region!.textContent).toBe('')
  })

  it('NumberRow inherits it too — the other shared config control', () => {
    const { container } = render(
      <NumberRow label="Retention" cfg={{ days: 90 }} field="days" min={1} max={365} patch={() => {}} />,
    )
    expect(container.querySelector('[role="status"]')).not.toBeNull()
  })

  it('a real save through ToggleRow announces, then falls silent', () => {
    // Drive the actual patch callback the way a click does, so this asserts the WIRING and not just
    // the markup: flash() runs on the patch's onSaved, and the announcement clears after 1500ms.
    vi.useFakeTimers()
    try {
      let onSaved: (() => void) | undefined
      const { container } = render(
        <ToggleRow label="LAN discovery" cfg={{ discovery_enabled: false }} field="discovery_enabled"
          patch={(_k, _v, cb) => { onSaved = cb as () => void }} />,
      )
      const toggle = container.querySelector('button')!
      act(() => { toggle.click() })
      expect(onSaved, 'ToggleRow must hand the row its flash callback').toBeTypeOf('function')
      act(() => { onSaved!() })
      expect(document.querySelector('[role="status"]')!.textContent).toBe('Saved')
      act(() => { vi.advanceTimersByTime(1600) })
      expect(document.querySelector('[role="status"]')!.textContent).toBe('')
    } finally {
      vi.useRealTimers()
    }
  })

  it('the pre-fix shape does not come back', () => {
    const src = settingsUI()
    // The region must be in SavedToast, not bolted onto the animated span (AnimatePresence
    // unmounts that one, which is the shape that does not announce).
    expect(src).toMatch(/<span role="status" aria-live="polite" className="sr-only">\{show \? 'Saved' : ''\}<\/span>/)
    expect(/<motion\.span initial=\{\{ opacity: 0, scale: 0\.8, y: 2 \}\}[^>]*aria-live/.test(src),
      'aria-live on the animated span would mount with its content and not be observed').toBe(false)
  })
})
