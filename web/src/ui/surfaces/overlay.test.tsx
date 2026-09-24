/** The L2 overlay producer's client half (AMBIENT-SURFACES §6 / AS-6).
 *
 *  The backend suite (`tests/test_surface_overlay.py`) owns path containment and the DATA
 *  shape. This one owns the two clauses that can only be checked where the registry lives,
 *  and it drives the REAL registry (core components really registered, `validateInvocation`
 *  really called) with only `api.surfaceOverlays` stubbed:
 *
 *  * **clause 2** — an unknown component name refuses the WHOLE overlay. The deliberate
 *    contrast with the chat path gets its own test: `GenUiWidget` DROPS one bad line, an
 *    overlay refuses entire. Both legs are asserted so the difference is measured, not
 *    assumed.
 *  * **clause 3** — shadowing goes through the SAME `registerLayerComponent` an app's L1
 *    module uses, so a `define` that takes `Table` is refused and NOTHING of that file
 *    stays registered.
 *  * **clause 4** — props are host-schema validated (`excess-args` / `missing-required`).
 *  * **clause 6** — safe mode does not even FETCH, and the band renders nothing.
 *
 *  Every refusal has its accepting leg through the same code path: a green refusal suite
 *  would otherwise be satisfiable by a loader nothing ever reaches. */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { SurfaceOverlayDoc, SurfaceOverlayPayload } from '../../lib/api'
import { api } from '../../lib/api'
import { allComponents, getComponent } from '../genui/registry'
import { registerCoreGenUiComponents } from '../genui/components'
import { GenUiWidget } from '../genui/GenUiWidget'
import { LAYER_USER } from './layers'
import { SurfaceOverlay } from './SurfaceOverlay'
import {
  CODE_OVERLAY_COMPONENT,
  loadSurfaceOverlays,
  overlayComponentNames,
  overlayRefusalsFor,
  overlaysFor,
  resetSurfaceOverlays,
  validateOverlayBody,
} from './overlay'

registerCoreGenUiComponents()

const BODY = 'a = Callout(tone: "info", text: "hello")'

function doc(over: Partial<SurfaceOverlayDoc> = {}): SurfaceOverlayDoc {
  return { file: 'mine.json', surface: 'dashboard', title: 'Mine', body: BODY, define: [], ...over }
}

function payload(over: Partial<SurfaceOverlayPayload> = {}): SurfaceOverlayPayload {
  return { overlays: [], refusals: [], dir: '/tmp/home/surfaces', ...over }
}

let fetched = 0

function serve(p: SurfaceOverlayPayload) {
  vi.spyOn(api, 'surfaceOverlays').mockImplementation(async () => {
    fetched += 1
    return p
  })
}

beforeEach(() => {
  window.location.hash = '#/dashboard'
  fetched = 0
  resetSurfaceOverlays()
})

afterEach(() => {
  vi.restoreAllMocks()
  resetSurfaceOverlays()
})

// ── the accepting path (the vacuity leg the refusals lean on) ──────────────────

describe('an accepted overlay', () => {
  it('loads, and its band renders the tree it declares', async () => {
    serve(payload({ overlays: [doc()] }))
    const { findByText } = render(<SurfaceOverlay surface="dashboard" />)
    expect(await findByText('hello')).toBeInTheDocument()
    expect(overlaysFor('dashboard')).toHaveLength(1)
    expect(overlayRefusalsFor('dashboard')).toHaveLength(0)
    expect(fetched).toBe(1)
  })

  it('renders NOTHING when the home has no overlays (byte-identical to today)', async () => {
    serve(payload())
    const { container } = render(<SurfaceOverlay surface="dashboard" />)
    await loadSurfaceOverlays()
    expect(container.querySelector('[data-testid="surface-overlay"]')).toBeNull()
  })

  it('does not render an overlay aimed at a DIFFERENT surface', async () => {
    serve(payload({ overlays: [doc({ surface: 'somewhere-else' })] }))
    await loadSurfaceOverlays()
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('fetches once per session even when two bands mount', async () => {
    serve(payload({ overlays: [doc()] }))
    await Promise.all([loadSurfaceOverlays(), loadSurfaceOverlays()])
    await loadSurfaceOverlays()
    expect(fetched).toBe(1)
  })
})

// ── clause 2: an unknown component refuses the WHOLE overlay ───────────────────

describe('clause 2 — an unknown component name is refused at load', () => {
  it('refuses the whole overlay and NAMES the component', async () => {
    serve(payload({ overlays: [doc({ body: `${BODY}\nb = NoSuchThing(x: 1)` })] }))
    const { findByTestId } = render(<SurfaceOverlay surface="dashboard" />)
    const notice = await findByTestId('overlay-refusal')
    expect(notice.textContent).toContain('NoSuchThing')
    expect(notice.textContent).toContain('mine.json')
    // The GOOD half of the same overlay is gone too — that is the point.
    expect(document.body.textContent).not.toContain('hello')
    expect(overlaysFor('dashboard')).toHaveLength(0)
    expect(overlayRefusalsFor('dashboard')[0].error.code).toBe(CODE_OVERLAY_COMPONENT)
  })

  it('🪤 the chat path DROPS the same line instead — the contrast is measured', () => {
    // Same body, rendered as a chat widget: the bad line becomes one notice and the good
    // line still paints. If this ever matched the overlay behaviour, the test above would
    // be asserting a global rule rather than the overlay-specific one.
    const { container } = render(
      <GenUiWidget content={`${BODY}\nb = NoSuchThing(x: 1)`} title="Chat" />,
    )
    expect(container.textContent).toContain('hello')
    expect(container.textContent).toContain('Unknown component "NoSuchThing"')
  })

  it('a surface-less (backend) refusal still surfaces, on the home surface', async () => {
    serve(
      payload({
        refusals: [
          {
            file: 'broken.json',
            error: {
              code: 'ERR_SURFACE_OVERLAY_INVALID',
              what: "'broken.json' not valid JSON",
              why: 'because',
              fix: 'Rewrite it.',
              suggestions: [],
            },
          },
        ],
      }),
    )
    const { findByTestId } = render(<SurfaceOverlay surface="dashboard" />)
    expect((await findByTestId('overlay-refusal')).textContent).toContain('broken.json')
  })
})

// ── clause 4: props are host-schema validated ─────────────────────────────────

describe('clause 4 — props go through the host schema', () => {
  it('refuses an excess arg', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(text: "hi", nope: 1)' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('unknown arg')
  })

  it('refuses a missing required arg', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(tone: "info")' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('missing required arg')
  })

  it('accepts the same component WITH its declared args (the control leg)', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(text: "hi", tone: "info")' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')).toHaveLength(0)
    expect(overlaysFor('dashboard')).toHaveLength(1)
  })

  it('validateOverlayBody is the one walk both legs use', () => {
    expect(validateOverlayBody(BODY)).toBe('')
    expect(validateOverlayBody('a = Nope()')).toContain('Unknown component "Nope"')
    expect(overlayComponentNames(`${BODY}\nb = Badge(text: "x")`)).toEqual(['Callout', 'Badge'])
  })
})

// ── clause 3: shadowing, through the SAME register-time refusal ────────────────

describe('clause 3 — a define may ADD a component name, never shadow one', () => {
  const define = [{ name: 'MyPanel', description: 'mine', body: BODY }]

  it('registers a composite at L2 and renders it from the overlay body', async () => {
    serve(payload({ overlays: [doc({ body: 'p = MyPanel()', define })] }))
    const { findByText } = render(<SurfaceOverlay surface="dashboard" />)
    expect(await findByText('hello')).toBeInTheDocument()
    const reg = getComponent('MyPanel')
    expect(reg?.layer).toBe(LAYER_USER)
    expect(reg?.source).toBe('overlay:mine.json')
  })

  it('refuses a composite that takes a CORE name, and leaves nothing registered', async () => {
    const before = allComponents().length
    serve(
      payload({
        overlays: [doc({ body: 'p = MyPanel()', define: [...define, { name: 'Table', description: '', body: BODY }] })],
      }),
    )
    await loadSurfaceOverlays()
    const refusal = overlayRefusalsFor('dashboard')[0]
    expect(refusal.error.what).toContain('Table')
    expect(refusal.error.fix).toContain('never take a core one')
    // Rollback: MyPanel registered BEFORE the refusal is gone, and `Table` is still core.
    expect(getComponent('MyPanel')).toBeUndefined()
    expect(getComponent('Table')?.source).toBe('')
    expect(allComponents()).toHaveLength(before)
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('a composite whose OWN body names an unknown component is refused', async () => {
    serve(payload({ overlays: [doc({ body: 'p = Bad()', define: [{ name: 'Bad', description: '', body: 'x = Nope()' }] })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('composite "Bad"')
    expect(getComponent('Bad')).toBeUndefined()
  })

  it('a composite takes NO args, so passing one is refused (clause 1 has no substitution)', async () => {
    serve(payload({ overlays: [doc({ body: 'p = MyPanel(value: 1)', define })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('unknown arg')
  })

  it('two overlays cannot take the same composite name', async () => {
    serve(
      payload({
        overlays: [doc({ file: 'a.json', body: 'p = MyPanel()', define }), doc({ file: 'b.json', body: 'p = MyPanel()', define })],
      }),
    )
    await loadSurfaceOverlays()
    expect(overlaysFor('dashboard').map((o) => o.file)).toEqual(['a.json'])
    expect(overlayRefusalsFor('dashboard')[0].file).toBe('b.json')
  })
})

// ── clause 6: safe mode ───────────────────────────────────────────────────────

describe('clause 6 — safe mode forces maxLayer=0, so no overlay loads', () => {
  it('does not even FETCH, and the band renders nothing', async () => {
    window.location.hash = '#/dashboard?safe=1'
    serve(payload({ overlays: [doc()] }))
    const { container } = render(<SurfaceOverlay surface="dashboard" />)
    await act(async () => { await loadSurfaceOverlays() })
    expect(fetched).toBe(0)
    expect(container.querySelector('[data-testid="surface-overlay"]')).toBeNull()
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('the SAME payload loads with safe mode off (the control leg)', async () => {
    serve(payload({ overlays: [doc()] }))
    await loadSurfaceOverlays()
    expect(fetched).toBe(1)
    expect(overlaysFor('dashboard')).toHaveLength(1)
  })
})

// ── the render path reaches the loader ────────────────────────────────────────

describe('the dashboard call site', () => {
  it('🪤 DashboardPage renders the band for the `dashboard` surface', () => {
    // A SOURCE assertion, deliberately, and named as one: `DashboardPage` mounts
    // `AgentWorld`, framer-motion and a dozen data widgets, so mounting it here would
    // measure that scaffolding rather than this seam. The behavioural half is every test
    // above, which drives the real band. The Python suite holds the same rail from the
    // other side — `OVERLAYABLE_SURFACES` may not name a surface with no call site.
    const src = readFileSync(join(process.cwd(), 'src/pages/dashboard/DashboardPage.tsx'), 'utf8')
    expect(src).toContain('<SurfaceOverlay surface="dashboard" />')
    expect(src).toContain("from '../../ui/surfaces/SurfaceOverlay'")
  })

  it('🪤 the loader has no dynamic-code path', () => {
    // A TEXT scan, and an alias would evade it (`window['ev'+'al']`). It is worth having
    // anyway: clause 1 says an overlay is DATA, and the cheapest way that stops being true
    // is somebody reaching for `new Function` to make composite args work.
    const src = readFileSync(join(process.cwd(), 'src/ui/surfaces/overlay.tsx'), 'utf8')
    expect(src).not.toMatch(/\beval\s*\(/)
    expect(src).not.toMatch(/new Function\b/)
    expect(src).not.toMatch(/import\s*\(/)
  })
})
