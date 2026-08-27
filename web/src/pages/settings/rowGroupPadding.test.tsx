import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { RowGroup } from './settingsUI'

// ── One settings row-group primitive, on tokens instead of Tailwind's frozen defaults ──────────
//
// MEASURED BEFORE. `rounded-lg bg-surface-container px-4 py-1` appeared **43 times verbatim** —
// 42 across `pages/settings/**` plus a 43rd mirroring it in `ui/ListScaffold.tsx`'s `FormSkeleton`
// — and at all 42 settings sites it was a bare `<div>` carrying that class and nothing else. Four
// more sites were the SAME SHAPE (a group whose only child is a self-padding `Row`/`Field`) at a
// different vertical padding:
//
//   GuardrailsPanel.tsx:70    py-3   one <Field>   ← the brief's census missed this one
//   AgentDefaultsPanel.tsx:165 py-2  one <Row>
//   PacksPanel.tsx:262        py-3   one <Row>
//   PacksPanel.tsx:394        py-3   one <Row>
//
// `GuardrailsPanel` carried both spellings **26 lines apart in one file** (`py-3` at :70, `py-1` at
// :80 after this change's line shift), which is the tightest available proof this was drift and not
// a deliberate density choice.
//
// WHY THE SPELLING MATTERED — the actual defect, not a tidiness preference. `px-4` and `py-1` are
// Tailwind's OWN defaults, not the project scale, so they are frozen against the density and
// space-scale sliders (`system.md` trap 3: "Tailwind's own defaults leak past the scale … and bypass
// the roundness slider and cli density"). Driven on `#/settings/agent` before the change, a converted
// group's computed padding versus the token-spelled sibling in the same subtree:
//
//                                   comfortable    dense      cli      --space-scale: 1.4
//   raw `px-4 py-1`  (42 sites)     16 / 4px     16 / 4px   16 / 4px      16 / 4px      ← frozen
//   token `px-l py-m` (DesignPanel) 16 / 12px    12.8/9.6   10.88/8.16    22.4 / 16.8   ← tracks
//
// AFTER (driven at the same three densities, §"What I validated"): the converted group reads
// 16/4px at comfortable — byte-identical to before — then 12.8/3.2px at dense and 10.88/2.72px at
// cli. `--spacing-l` is `16px * --space-scale` and `--spacing-xs` is `4px * --space-scale`, so
// `px-l py-xs` IS `px-4 py-1` at default. 43 of the 47 adopted sites are therefore zero-pixel
// changes; the 4 near-misses converge onto the 42-site majority.
//
// ⚠️ WHAT THIS DOES NOT CLAIM. The ~38 remaining `py-3` groups in this tree are NOT drift and are
// deliberately untouched: they hold free-form content (a paragraph, a `Loading…` line, a flex
// cluster) where nothing inside pads itself, so 12px is doing real work. `RowGroup` therefore takes
// no `pad`/`className`/`tone` prop — a variant with no adopter is speculative API. The two
// row-bearing groups still at `px-l py-m` (`DesignPanel:195`, `:334`) are token-spelled already and
// so are NOT the defect this fixes; converging their VALUE is a visible 24px→16px change on the
// panel the census used as its reference, recorded rather than smuggled in here. The ratchet below
// stops that population growing.

const SETTINGS = join(process.cwd(), 'src/pages/settings')
const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** Strip `//` and block comments before scanning for a class string.
 *  🪤 Without this the rail reds on its own prose: this file and `settingsUI.tsx` both quote the
 *  banned spelling in a comment, and a text scanner cannot tell that from shipped markup. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const panels = () => readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))

describe('RowGroup is the one settings row-group surface', () => {
  it('renders a container-tone lg-radius slab padded from the spacing scale', () => {
    const { container } = render(<RowGroup><span>row</span></RowGroup>)
    const el = container.firstElementChild as HTMLElement
    expect(el, 'RowGroup must render an element').toBeTruthy()
    const cls = el.className.split(/\s+/)
    // The four facts every one of the 47 adopted sites is now relying on.
    expect(cls, 'one tonal step: bg-surface-container').toContain('bg-surface-container')
    expect(cls, 'rounded-lg').toContain('rounded-lg')
    expect(cls, 'horizontal padding from the scale, not Tailwind px-4').toContain('px-l')
    expect(cls, 'vertical padding from the scale, not Tailwind py-1').toContain('py-xs')
    // And the frozen spellings must not come back through the primitive itself.
    expect(cls, 'px-4 is the frozen spelling this replaced').not.toContain('px-4')
    expect(cls, 'py-1 is the frozen spelling this replaced').not.toContain('py-1')
    expect(el.textContent).toBe('row')
  })

  it('px-l/py-xs really are 16px/4px scaled by --space-scale', () => {
    // VACUITY GUARD for the zero-pixel claim above. The whole "43 of 47 sites move by 0px at default"
    // statement rests on these two token definitions. If either base value is ever retuned, the
    // claim is stale and must be re-measured rather than left asserted in a comment.
    const tokens = read('design/tokens.css')
    expect(tokens, '--spacing-l must be 16px * --space-scale (== Tailwind px-4 at scale 1)')
      .toMatch(/--spacing-l:\s*calc\(16px\s*\*\s*var\(--space-scale\)\)/)
    expect(tokens, '--spacing-xs must be 4px * --space-scale (== Tailwind py-1 at scale 1)')
      .toMatch(/--spacing-xs:\s*calc\(4px\s*\*\s*var\(--space-scale\)\)/)
    // And --space-scale must actually be what density re-scales, or "tracks the sliders" is false.
    expect(tokens, 'dense re-scales --space-scale').toMatch(/--space-scale:\s*0\.8/)
    expect(tokens, 'cli re-scales --space-scale').toMatch(/--space-scale:\s*0\.68/)
  })

  it('no shipped markup still hand-rolls the slab', () => {
    // The 42 exact sites + the ListScaffold mirror. Comment-stripped, so the prose above is exempt
    // and only real markup counts.
    const offenders: string[] = []
    for (const f of panels()) {
      if (/rounded-lg bg-surface-container px-4 py-1/.test(code(readFileSync(join(SETTINGS, f), 'utf8')))) {
        offenders.push(`pages/settings/${f}`)
      }
    }
    if (/rounded-lg bg-surface-container px-4 py-1/.test(code(read('ui/ListScaffold.tsx')))) {
      offenders.push('ui/ListScaffold.tsx')
    }
    expect(offenders, `hand-rolled row-group slab in: ${offenders.join(', ')}`).toEqual([])
  })

  it('the 42 exact sites really did adopt it', () => {
    // Keyed on the PRIMITIVE, not on what a scan found, so this number can rise and the rail still
    // means something. 46 settings adoptions = 42 exact + 4 near-misses.
    const uses = panels()
      .map((f) => (readFileSync(join(SETTINGS, f), 'utf8').match(/<RowGroup[\s>]/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    expect(uses, 'settings must carry at least the 46 adoptions this change made').toBeGreaterThanOrEqual(46)
  })

  it('each of the 4 near-miss sites is now the primitive, at the majority padding', () => {
    // Pinned by their distinguishing content rather than by line number, which drifts.
    const guardrails = read('pages/settings/GuardrailsPanel.tsx')
    expect(guardrails.match(/<RowGroup>\s*<Field label="Scan mode"/),
      'GuardrailsPanel outbound-scan group (was py-3, one Field)').toBeTruthy()

    const agent = read('pages/settings/AgentDefaultsPanel.tsx')
    expect(agent.match(/<RowGroup>\s*<Row label="Default agent"/),
      'AgentDefaultsPanel default-agent group (was py-2)').toBeTruthy()

    const packs = read('pages/settings/PacksPanel.tsx')
    expect(packs.match(/<RowGroup key=\{p\.name\}>\s*<Row label=/),
      'PacksPanel store row (was py-3, and its `key` is why a regex sweep missed it)').toBeTruthy()
    expect(packs.match(/<RowGroup>\s*<Row label=\{`\$\{pack\.name\}/),
      'PacksPanel installed-pack row (was py-3)').toBeTruthy()
  })

  it('no panel re-declares a private copy', () => {
    const definers = panels()
      .filter((f) => f !== 'settingsUI.tsx')
      .filter((f) => /function RowGroup\b|const RowGroup\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(definers, `private RowGroup in: ${definers.join(', ')}`).toEqual([])
  })

  it('FormSkeleton stays padding-identical to the loaded group', () => {
    // This is the skeleton the converted panels themselves render while `useQuery` resolves, so any
    // divergence is a loading→loaded REFLOW — not a cosmetic mismatch. `src/ui` may not import from
    // `src/pages`, so it spells the same three facts one layer down via `Surface`.
    const scaffold = read('ui/ListScaffold.tsx')
    const slab = scaffold.match(/<Surface tone="container" radius="lg" className="[^"]*"/)?.[0] ?? ''
    expect(slab, 'FormSkeleton must render a container Surface').toContain('<Surface')
    expect(slab, 'same horizontal padding as RowGroup').toContain('px-l')
    expect(slab, 'same vertical padding as RowGroup').toContain('py-xs')
    // Vacuity guard: assert the two really are the same string, so a future edit to RowGroup's
    // padding cannot silently desync the skeleton.
    const rowGroup = read('pages/settings/settingsUI.tsx')
      .match(/export function RowGroup[\s\S]*?\n\}/)?.[0] ?? ''
    const pad = rowGroup.match(/className="([^"]*)"/)?.[1]
    expect(pad, "RowGroup's padding must be readable").toBe('px-l py-xs')
    expect(slab, `FormSkeleton must carry RowGroup's exact padding (${pad})`).toContain(`className="${pad}"`)
  })

  it('the competing py-m row-group padding does not spread', () => {
    // 7 container groups in settings sit at `px-l py-m` (DesignPanel ×5, DiagnosticsPanel, and
    // ModelBackends' raw-spelled one). Five are free-form and legitimately roomier; two
    // (DesignPanel:195 `<Row>`, :334 `<Field>`) are row-bearing and are the recorded open question.
    // Ratcheted, not frozen: converging one LOWERS this, adding an eighth reds CI.
    const count = panels()
      .map((f) => (readFileSync(join(SETTINGS, f), 'utf8').match(/px-l py-m/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    expect(count, 'a new row group belongs in RowGroup, not a fresh px-l py-m slab').toBeLessThanOrEqual(7)
  })
})
