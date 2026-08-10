import { describe, expect, it, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ToggleRow } from './settingsUI'

// ── One config-switch row for the settings panels ─────────────────────────────
//
// Five panels declared `ToggleRow` privately — Sources, Legibility, Ambient, Packs and
// AgentDefaults — and **four of the five were byte-identical across all 12 lines**, down to the
// 1500ms flash timeout. Their `cfg` prop wore five different names (`SourcesCfg`, `LegibilityCfg`,
// `AmbientCfg`, `PacksCfg`, `AgentCfg`) that are each literally `Record<string, unknown>`, so even
// the apparent type variation was five aliases for one type.
//
// The fifth was a REAL variation: AgentDefaults adds `danger`, a warning glyph shown only while the
// switch is ON, for a setting that relaxes a safety default. That is why the shared version takes
// `danger` as an opt-in prop rather than the dedup flattening it away — the queue's own warning was
// that "a dedup which silently picks one behaviour is a behaviour change".
//
// Why five copies mattered: each owned its own "toggle → patch → flash for 1500ms" state, so that
// timing had five places to drift, on rows that sit in the same settings tree and read as one
// family. Nothing would have failed if one had gone to 800ms.
//
// The migration was verified HTML-identical before landing — the shared component and both old
// variants were rendered side by side over 6 states (off, on, missing key, truthy non-boolean,
// danger+on, danger+off) and asserted to produce the same innerHTML. That harness was temporary;
// the behaviour assertions below pin the same states against the shared copy.

const SETTINGS = join(process.cwd(), 'src/pages/settings')

describe('ToggleRow lives in settingsUI only', () => {
  it('no panel declares a private copy', () => {
    const definers = readdirSync(SETTINGS)
      .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f) && f !== 'settingsUI.tsx')
      .filter((f) => /function ToggleRow\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(definers, `private ToggleRow in: ${definers.join(', ')}`).toEqual([])
  })

  it('all five migrated panels import it', () => {
    for (const f of ['SourcesPanel', 'LegibilityPanel', 'AmbientPanel', 'PacksPanel', 'AgentDefaultsPanel']) {
      const src = readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')
      expect(src, `${f} should import ToggleRow from ./settingsUI`)
        .toMatch(/import \{[^}]*\bToggleRow\b[^}]*\} from '\.\/settingsUI'/)
    }
  })
})

describe('ToggleRow behaviour', () => {
  const patchFor = () => vi.fn()

  it('reads its state from cfg[field]', () => {
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: true }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true')
  })

  it('treats a missing key as OFF rather than crashing', () => {
    // A config key the backend has not written yet is the normal first-run state.
    const { container } = render(<ToggleRow label="Poll" cfg={{}} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('false')
  })

  it('coerces a truthy non-boolean to ON', () => {
    // `Boolean(cfg[field])` — config JSON has carried 1/0 for flags before.
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: 1 }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true')
  })

  it('patches the field with the new value and a flash callback', () => {
    const patch = patchFor()
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: false }} field="poll" patch={patch as never} />)
    fireEvent.click(container.querySelector('[role="switch"]')!)
    // The third argument is what makes "Saved ✓" appear; a panel that dropped it would save
    // silently, which is the shape a hand-rolled copy got wrong most easily.
    expect(patch).toHaveBeenCalledWith('poll', true, expect.any(Function))
  })

  it('shows the danger glyph only while ON', () => {
    const on = render(<ToggleRow label="YOLO" cfg={{ f: true }} field="f" patch={patchFor() as never} danger />)
    const off = render(<ToggleRow label="YOLO" cfg={{ f: false }} field="f" patch={patchFor() as never} danger />)
    // The warning marks an ACTIVE relaxation of a safety default — on an off switch it would be
    // noise, which is exactly the distinction the dedup had to preserve.
    expect(on.container.querySelector('svg.text-warn')).not.toBeNull()
    expect(off.container.querySelector('svg.text-warn')).toBeNull()
  })

  it('omits the danger glyph entirely when the prop is absent', () => {
    // The four non-danger panels pass nothing; they must render exactly as before.
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: true }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('svg.text-warn')).toBeNull()
  })
})
