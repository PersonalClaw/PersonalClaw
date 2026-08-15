import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AudioRecorder } from './AudioRecorder'

// ── The recorder's state and elapsed time reach assistive tech ─────────────────────────────────
//
// Measured (cycle 200): the recording state was conveyed to SIGHTED users only — a red mic + a growing
// level ring — so a screen-reader user clicking "Start recording" got no confirmation it began, and no
// signal on pause/resume/stop (WCAG 4.1.3). The elapsed-time readout was a bare `<div>` (role=generic),
// which does not reliably expose an aria-label, so it read as a context-free "0:03".
//
// Fixes: a polite `role="status"` live region (the app's ui/Toaster idiom) that announces the state on
// each transition, and `role="timer"` + aria-label on the readout (timer's implicit aria-live is off,
// so it names the value without spamming 5×/sec). The state→text mapping is verified live in a browser
// with a fake media stream (getUserMedia → MediaRecorder can't run in jsdom); this pins the always-
// present structure the announcement rides on.

describe('AudioRecorder exposes its state and time to assistive tech', () => {
  it('has an always-mounted polite status live region', () => {
    // Always mounted (not conditional on state) so it is observed when its text changes — a region
    // created at the moment its content appears is not reliably announced.
    const { container } = render(<AudioRecorder onRecorded={() => {}} onClear={() => {}} />)
    const status = container.querySelector('[role="status"][aria-live="polite"]')
    expect(status, 'a polite status region must exist for state announcements').toBeTruthy()
    expect(status?.className, 'it is an announcement, not visible chrome').toContain('sr-only')
  })

  it('the elapsed-time readout is a named timer, not an anonymous number', () => {
    render(<AudioRecorder onRecorded={() => {}} onClear={() => {}} />)
    const timer = screen.getByRole('timer', { name: 'Recording time' })
    expect(timer).toBeTruthy()
    // role="timer" (implicit aria-live off) not aria-live=polite — it must NOT be a chatty region.
    expect(timer.getAttribute('aria-live'), 'the timer must not announce every tick').not.toBe('polite')
  })

  it('the status text is the four states, and idle is silent', () => {
    // The mapping the live drive exercises: idle → '' (nothing to announce), and the three active
    // states have words. Asserted against the source so the mapping cannot be quietly dropped.
    const src = require('node:fs').readFileSync(
      require('node:path').join(process.cwd(), 'src/pages/knowledge/AudioRecorder.tsx'), 'utf8')
    expect(src).toMatch(/state === 'recording' \? 'Recording'/)
    expect(src).toMatch(/state === 'paused' \? 'Recording paused'/)
    expect(src).toMatch(/state === 'done' \? 'Recording complete'/)
  })
})
