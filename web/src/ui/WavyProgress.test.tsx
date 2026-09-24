import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { WavyProgress } from './WavyProgress'

// ── A named progressbar, and an unnamed wave that should stay unnamed ─────────────────────────
//
// This component already shipped `role="progressbar"` + `aria-valuemin/max/now` for its determinate
// path — and **no accessible name**, so assistive tech announced "progressbar, 42%" with no subject.
// In a LIST of downloadable models that does not say which one is downloading. Missing half of
// Name/Role/Value (4.1.2), and axe's `aria-progressbar-name` would have caught it — except the bar
// only renders while a download job is `running`, so no audit of `#/settings/models` ever reached it.
//
// 🔑 THE SAME BLIND SPOT AS EVERY OTHER FINDING THIS SESSION: the tools see a surface's default
// state. Cycle 178 fixed this exact gap on `ui/ProgressRing` (which had no role at all) and recorded
// this one as the follow-up; this is it.
//
// 🪤 THE INDETERMINATE MODE MUST STAY UNNAMED, and that is a distinction rather than an oversight.
// It renders `aria-hidden` with no role, because the caller prints its own line — "downloading ·
// 120 / 400 MB" — directly above. A second, valueless progressbar would announce nothing useful
// twice. The type enforces the pair: `value` REQUIRES `label`, and omitting `value` FORBIDS it
// (the shape `WidgetRow` uses), so neither half can drift by convention.
//
// 🪤 NOT DRIVEN IN THE BROWSER, stated rather than implied: the bar needs a live download job
// (`job.state === 'running'`, streamed over SSE). Both branches are asserted here instead.

describe('WavyProgress', () => {
  it('names a determinate bar and reports its value', () => {
    const { container } = render(<WavyProgress value={0.42} label="Downloading llama3" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('progressbar')
    expect(svg.getAttribute('aria-label')).toBe('Downloading llama3')
    expect(svg.getAttribute('aria-valuenow')).toBe('42')
    expect(svg.getAttribute('aria-valuemin')).toBe('0')
    expect(svg.getAttribute('aria-valuemax')).toBe('100')
  })

  it('clamps the reported value to the bar it draws', () => {
    const over = render(<WavyProgress value={1.4} label="x" />).container.querySelector('svg')!
    const under = render(<WavyProgress value={-0.3} label="x" />).container.querySelector('svg')!
    expect(over.getAttribute('aria-valuenow')).toBe('100')
    expect(under.getAttribute('aria-valuenow')).toBe('0')
  })

  it('leaves the indeterminate wave hidden and roleless', () => {
    const { container } = render(<WavyProgress />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('aria-hidden')).not.toBeNull()
    expect(svg.getAttribute('role')).toBeNull()
    expect(svg.getAttribute('aria-valuenow')).toBeNull()
  })

  it('the only call site names its determinate bar and leaves the other bare', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/LocalModelManager.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    expect(code, 'determinate bars name the model').toMatch(/<WavyProgress width=\{200\} value=\{frac\} label=\{`Downloading \$\{m\.name\}`\}/)
    expect(code, 'the indeterminate one stays unnamed').toMatch(/<WavyProgress width=\{200\} \/>/)
  })

  it('the doc records the pairing, because the drift guard reads it', () => {
    // A `ui/` primitive's props must be documented in the same change — cycle 178 learned that from a
    // red `uiDocs.drift` gate rather than from CI.
    const doc = readFileSync(join(process.cwd(), 'src/ui/WavyProgress.doc.ts'), 'utf8')
    expect(doc).toMatch(/name: 'label'/)
    expect(doc).toMatch(/aria-hidden on purpose|aria-hidden deliberately/)
  })
})
