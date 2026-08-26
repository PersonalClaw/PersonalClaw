// @vitest-environment jsdom
/**
 * APE-11 — the UI SDK's shell-primitive / token / generative-widget export surface,
 * asserted at the CALL SITE an app actually goes through: a fixture app bundle whose
 * source imports `@personalclaw/app-sdk/ui`, loaded and mounted by `ContributedPage`.
 *
 * Why this drives the real loader instead of asserting the export map:
 * a module map is a mechanism, and a map entry no bundle imports is an inert control.
 * The claim in the atom is that an app PAGE renders from host primitives and is
 * indistinguishable from a native page, so the test renders both and compares markup.
 *
 * Two jsdom gaps had to be stood in for, and neither weakens the assertion:
 *
 *  - `URL.createObjectURL` does not exist in jsdom at all (measured: `undefined`), so
 *    the loader's blob step cannot run. Nothing in the suite had ever exercised
 *    `loadContributedModule` before this file, which is why that never surfaced.
 *  - jsdom registers blob: URLs in its own registry, which Node's ESM loader cannot
 *    read, so `import(blobUrl)` could not resolve even with the stub.
 *
 * So `createObjectURL` returns a `data:` URL carrying the SAME bytes. Node's ESM loader
 * does import a data: module, so the bundle is genuinely fetched, genuinely rewritten,
 * genuinely imported, and the components it renders are genuinely the host's — only the
 * URL scheme carrying the bytes differs from production.
 */
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import {
  installAppSdk,
  resolvableAppSpecs,
  hasUiCapability,
  GenerativeWidget,
  type AppContext,
} from './appSdk'
import { ContributedPage } from '../pages/apps/ContributedPage'
import { Button } from '../ui/Button'
import { Surface } from '../ui/Surface'

// ── the data:-URL stand-in for jsdom's missing blob plumbing ──────────────────
// `createObjectURL` is synchronous but `Blob.text()` is not, so the Blob's first
// source part is recorded on construction and read back synchronously here.
const RealBlob = globalThis.Blob
const partText = new WeakMap<Blob, string>()
class RecordingBlob extends RealBlob {
  constructor(parts: BlobPart[] = [], opts?: BlobPropertyBag) {
    super(parts, opts)
    partText.set(this, String(parts[0] ?? ''))
  }
}

const realFetch = globalThis.fetch
let served = ''

beforeAll(() => {
  globalThis.Blob = RecordingBlob as unknown as typeof Blob
  ;(URL as { createObjectURL?: (b: Blob) => string }).createObjectURL = (b: Blob) =>
    'data:text/javascript;base64,' + Buffer.from(partText.get(b) ?? '', 'utf8').toString('base64')
  ;(URL as { revokeObjectURL?: (u: string) => void }).revokeObjectURL = () => {}
  globalThis.fetch = vi.fn(async () =>
    new Response(served, { status: 200, headers: { 'content-type': 'text/javascript' } }),
  ) as unknown as typeof fetch
  installAppSdk()
})

afterAll(() => {
  globalThis.Blob = RealBlob
  globalThis.fetch = realFetch
})

/** A fixture app's UI bundle, as shipped: plain ESM, bare specifiers, no JSX. It
 *  builds its page from the HOST primitives — the whole point of the atom. */
const FIXTURE_BUNDLE = `
import { createElement } from 'react'
import { Button, Surface } from '@personalclaw/app-sdk/ui'
export function mount() {
  return createElement(Surface, { tone: 'low', radius: 'xl' },
    createElement(Button, { variant: 'primary', size: 'sm' }, 'Save'))
}
`

/** The same bundle, plus the TOKEN half of the surface: it reads the resolved theme
 *  through the SDK and spreads `cssVars` rather than naming a host CSS variable. */
const TOKENS_BUNDLE = `
import { createElement } from 'react'
import { Surface, readAppTheme } from '@personalclaw/app-sdk/ui'
export function mount() {
  const theme = readAppTheme()
  return createElement(Surface, { tone: 'low' },
    createElement('span', { 'data-mode': theme.mode, style: theme.cssVars }, 'themed'))
}
`

/** A bundle that reaches the generative-widget path the way an app does — through
 *  the gated `@personalclaw/app-sdk/genui` specifier, not a host-side import. */
const GENUI_BUNDLE = `
import { createElement } from 'react'
import { GenerativeWidget } from '@personalclaw/app-sdk/genui'
export function mount() {
  return createElement(GenerativeWidget, {
    spec: 'note = Callout(tone: "info", text: "from the bundle")',
    title: 'App widget',
  })
}
`

/** The SAME page a host author would write with direct imports — the native baseline
 *  the contributed page must be indistinguishable from. */
function NativePage() {
  return (
    <Surface tone="low" radius="xl">
      <Button variant="primary" size="sm">Save</Button>
    </Surface>
  )
}

// ── settled-motion residue: the byte-identity comparison's one normalisation ───
// Every host `Button` wraps its label in a `motion.span` carrying
// `animate={{ opacity, y }}`. framer-motion commits that element's RESTING values as an
// inline style AFTER mount, on its rAF frame loop — so the SAME markup serialises three
// different ways depending only on when it is read (all three measured here):
//
//   (1) `<span class="…">`                                       before the commit
//   (2) `<span class="…" style="transform: none;">`              mid-commit
//   (3) `<span class="…" style="opacity: 1; transform: none;">`  after it
//
// …and (3)'s two declarations are emitted in EITHER order: measured in one run, the
// contributed render settled `opacity` first and the native render `transform` first.
//
// That is what reds this test intermittently on CI and misattributes the failure to
// APE-11: on PR #2010 — a test-only diff touching neither `web/` nor the SDK — the `web`
// job failed here on a SHA that also had a PASSING run of the same job, with state (3)
// on the contributed side and state (1) on the native side.
//
// Awaiting settle on both sides is NOT sufficient on its own: measured 30/30 red,
// because (2) and (3)-in-the-other-order stay reachable per side. So the residue is
// normalised out of BOTH sides as well, and only the residue — the two declarations
// removed are `opacity: 1` and `transform: none`, the CSS INITIAL values, whose removal
// cannot change a rendered pixel. Everything else is still compared byte-for-byte,
// including the button's `font-variation-settings` and the sheen's `opacity: 0.9` and
// gradient; a `style` attribute carrying no resting-motion declaration is returned
// untouched. The rail for that claim is the last `describe` in this file.
const MOTION_RESTING_DECLARATIONS = new Set(['opacity: 1', 'transform: none'])

function stripSettledMotionStyles(html: string): string {
  return html.replace(/ style="([^"]*)"/g, (attribute, body: string) => {
    const declarations = body.split(';').map((d) => d.trim()).filter(Boolean)
    const kept = declarations.filter((d) => !MOTION_RESTING_DECLARATIONS.has(d))
    // Byte-transparent unless something motion-owned was actually removed.
    if (kept.length === declarations.length) return attribute
    return kept.length === 0 ? '' : ` style="${kept.join('; ')};"`
  })
}

/** The compared subtree: the innermost host div the page's `Surface` renders. */
const pageMarkup = (root: HTMLElement) =>
  root.querySelector('button')!.closest('div[class]')!.outerHTML

/** Drives a render past framer-motion's resting-value commit. Requires BOTH
 *  declarations, so the mid-commit state (2) above cannot be mistaken for a settled
 *  one. The timeout is generous for the same reason `vitest.config.ts` raises
 *  `testTimeout`: wall clock per test inflates ~3x under the full suite's workers. */
async function awaitMotionSettled(root: HTMLElement) {
  await waitFor(
    () => {
      const style = root.querySelector('button > span:last-child')!.getAttribute('style') ?? ''
      expect(style).toContain('opacity: 1')
      expect(style).toContain('transform: none')
    },
    { timeout: 5_000 },
  )
}

/** State (3) in either emission order — used only as a vacuity check. */
const SETTLED_RESIDUE = /style="(opacity: 1; transform: none|transform: none; opacity: 1);"/

const declaring: AppContext = {
  name: 'ui-fixture',
  permissions: {},
  uiCapabilities: ['shell-primitives'],
}
const silent: AppContext = { name: 'ui-fixture', permissions: {}, uiCapabilities: [] }
const genuiApp: AppContext = {
  name: 'ui-fixture',
  permissions: {},
  uiCapabilities: ['generative-widget'],
}

describe('APE-11: a fixture app page renders from host primitives via the UI SDK', () => {
  it('is byte-identical to the same page written natively', async () => {
    served = FIXTURE_BUNDLE
    const app = render(<ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/page.js" />)
    // The app's markup lands in the innermost host div ContributedPage mounts into.
    await waitFor(() => expect(app.container.querySelector('button')).not.toBeNull())
    const native = render(<NativePage />)

    // Symmetry, half one: BOTH renders are driven past the resting-value commit before
    // EITHER is read, so neither side's bytes depend on how long the `waitFor` above
    // happened to take. Settling one side only would turn the intermittent failure into
    // an intermittent PASS — the same bug with the signal removed.
    await awaitMotionSettled(app.container)
    await awaitMotionSettled(native.container)

    const contributed = pageMarkup(app.container)
    const baseline = pageMarkup(native.container)

    // Vacuity for the normalisation below: the residue it exists for must really be
    // present in what was just read, or `stripSettledMotionStyles` is dead code here and
    // this test would be green for the wrong reason. If a framer-motion upgrade stops
    // writing resting values, `awaitMotionSettled` times out and says so out loud.
    for (const [side, html] of [['contributed', contributed], ['native', baseline]] as const) {
      expect(html, `the ${side} side must carry the settled-motion residue`).toMatch(SETTLED_RESIDUE)
    }

    // Non-vacuity: the compared markup must actually be a rendered primitive, not two
    // empty strings agreeing. A Button carries its variant's token classes.
    expect(baseline).toContain('<button')
    expect(baseline.length).toBeGreaterThan(120)

    // Symmetry, half two: the normalisation is applied to BOTH sides. This is still
    // `toBe` over the whole serialised subtree — byte-identity is NOT weakened to a
    // substring or `toContain` check; two no-op declarations neither page authored are
    // all that is removed.
    expect(stripSettledMotionStyles(contributed)).toBe(stripSettledMotionStyles(baseline))
  })

  it('serialises to the same bytes read before or after the motion commit', async () => {
    served = FIXTURE_BUNDLE
    const app = render(<ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/settle.js" />)
    await waitFor(() => expect(app.container.querySelector('button')).not.toBeNull())
    const beforeCommit = pageMarkup(app.container)
    await awaitMotionSettled(app.container)
    const afterCommit = pageMarkup(app.container)

    // The property the flake violated, stated directly: one unchanged render must
    // compare equal to itself no matter which side of framer-motion's commit the read
    // landed on. Whether `beforeCommit` actually caught state (1) is itself
    // timing-dependent — which is the point; this holds either way, and holding either
    // way is what makes the comparison above deterministic rather than lucky.
    expect(stripSettledMotionStyles(beforeCommit)).toBe(stripSettledMotionStyles(afterCommit))
    // Non-vacuity: a real rendered primitive, not two empty strings agreeing.
    expect(afterCommit).toContain('<button')
    expect(afterCommit).toMatch(SETTLED_RESIDUE)
  })

  it('renders the host Button and Surface, not a lookalike', async () => {
    served = FIXTURE_BUNDLE
    const { container } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/page2.js" />,
    )
    await waitFor(() => expect(container.querySelector('button')).not.toBeNull())
    const btn = container.querySelector('button')!
    // `bg-surface-low` is Surface's `tone="low"` class and `rounded-xl` its `radius`;
    // both come from the host component, so an app that hand-rolled chrome fails here.
    expect(btn.closest('.bg-surface-low.rounded-xl')).not.toBeNull()
    expect(btn.textContent).toContain('Save')
  })

  it('leaves the /ui import UNRESOLVED for an app that declared no capability', async () => {
    served = FIXTURE_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...silent }} src="/apps/ui-fixture/ui/page3.js" />,
    )
    // The bare specifier survives the rewrite, so the import fails and the page
    // surfaces the load error instead of silently rendering unstyled chrome.
    await findByText(/Failed to load ui-fixture/)
    expect(container.querySelector('button')).toBeNull()
  })

  it('reaches the TOKEN contract through the same subpath, not a guessed CSS variable', async () => {
    served = TOKENS_BUNDLE
    const { container } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/tokens.js" />,
    )
    await waitFor(() => expect(container.querySelector('[data-mode]')).not.toBeNull())
    const span = container.querySelector('[data-mode]') as HTMLElement
    // The host resolved the mode for the app; the app named no host token itself.
    expect(['dark', 'light']).toContain(span.getAttribute('data-mode'))
    // cssVars map app-facing names onto host tokens by `var(--color-…)` reference, so
    // the app inherits the light-mode flip instead of freezing a hex it read once.
    expect(span.getAttribute('style')).toContain('var(--color-surface)')
    expect(span.getAttribute('style')).toContain('--app-surface')
  })

  it('renders a generative widget imported through the gated /genui subpath', async () => {
    served = GENUI_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...genuiApp }} src="/apps/ui-fixture/ui/widget.js" />,
    )
    expect(await findByText('from the bundle')).toBeInTheDocument()
    // A host-registered component rendered, not a dropped-line error.
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('leaves /genui UNRESOLVED for an app that declared only shell-primitives', async () => {
    served = GENUI_BUNDLE
    const { findByText } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/widget2.js" />,
    )
    // Paired with the test above: the same bundle that renders for a declaring app
    // must fail for one that declared the OTHER capability, or the gate is per-app
    // in name only.
    await findByText(/Failed to load ui-fixture/)
  })
})

describe('APE-11: resolvableAppSpecs is the gate', () => {
  const ungated = ['react-dom/client', 'react-dom', 'react', '@personalclaw/app-sdk', 'lucide-react']

  it('always resolves the ungated head, declaration or not', () => {
    for (const spec of ungated) {
      expect(resolvableAppSpecs(undefined)).toContain(spec)
      expect(resolvableAppSpecs(declaring)).toContain(spec)
    }
  })

  it('adds /ui only for shell-primitives and /genui only for generative-widget', () => {
    expect(resolvableAppSpecs({ uiCapabilities: ['shell-primitives'] })).toContain('@personalclaw/app-sdk/ui')
    expect(resolvableAppSpecs({ uiCapabilities: ['shell-primitives'] })).not.toContain('@personalclaw/app-sdk/genui')
    expect(resolvableAppSpecs({ uiCapabilities: ['generative-widget'] })).toContain('@personalclaw/app-sdk/genui')
    expect(resolvableAppSpecs({ uiCapabilities: ['generative-widget'] })).not.toContain('@personalclaw/app-sdk/ui')
    // One declaration must not grant the other — the reason they are two map entries.
    expect(resolvableAppSpecs(silent)).not.toContain('@personalclaw/app-sdk/ui')
    expect(resolvableAppSpecs(silent)).not.toContain('@personalclaw/app-sdk/genui')
  })

  it('hasUiCapability is false for absent, empty and unrelated declarations', () => {
    expect(hasUiCapability(undefined, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({}, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: [] }, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: ['generative-widget'] }, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: ['shell-primitives'] }, 'shell-primitives')).toBe(true)
  })

  it('exposes BOTH subpaths on the host module map, so the gate is the only thing withholding them', () => {
    const map = (window as unknown as { __personalclaw_modules: Record<string, Record<string, unknown>> })
      .__personalclaw_modules
    expect(Object.keys(map['@personalclaw/app-sdk/ui'])).toEqual(
      expect.arrayContaining(['Button', 'Surface', 'useTheme', 'readAppTheme']),
    )
    expect(Object.keys(map['@personalclaw/app-sdk/genui'])).toEqual(['GenerativeWidget'])
    // The pre-APE-11 alias is gone: /ui is its own module, not the base surface again.
    expect(map['@personalclaw/app-sdk/ui']).not.toBe(map['@personalclaw/app-sdk'])
    expect(map['@personalclaw/app-sdk/ui'].useAppApi).toBeUndefined()
  })
})

describe('APE-11: the generative-widget contribution path', () => {
  it('renders a host-registered genui component from an app-supplied spec', () => {
    const { container, getByText } = render(
      <GenerativeWidget spec={'note = Callout(tone: "info", text: "From the app")'} title="App widget" />,
    )
    expect(getByText('From the app')).toBeInTheDocument()
    expect(getByText('App widget')).toBeInTheDocument()
    // Non-vacuity: the widget really rendered a component, not just its own chrome.
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('cannot reach a component the HOST never registered — the registry stays host-owned', () => {
    const { getByRole } = render(
      <GenerativeWidget spec={'x = AppOwnedThing(text: "escalation")'} title="App widget" />,
    )
    // Dropped with a typed, visible error rather than executing app-named markup.
    expect(getByRole('alert').textContent).toContain('Unknown component "AppOwnedThing"')
  })
})

describe('APE-11: the settled-motion normalisation the byte-identity test rests on', () => {
  // The label span in each state measured above, quoted verbatim.
  const PRE_COMMIT = '<span class="relative inline-flex items-center gap-s">Save</span>'
  const MID_COMMIT =
    '<span class="relative inline-flex items-center gap-s" style="transform: none;">Save</span>'
  const SETTLED =
    '<span class="relative inline-flex items-center gap-s" style="opacity: 1; transform: none;">Save</span>'
  const SETTLED_REVERSED =
    '<span class="relative inline-flex items-center gap-s" style="transform: none; opacity: 1;">Save</span>'

  it('collapses every observed residue state onto the pre-commit bytes', () => {
    // Vacuity: the normaliser is NOT the identity function on the shape it exists for.
    // Without this, the two tests above could pass with a no-op normaliser on any
    // machine fast enough never to observe the residue.
    expect(stripSettledMotionStyles(SETTLED)).not.toBe(SETTLED)
    for (const state of [PRE_COMMIT, MID_COMMIT, SETTLED, SETTLED_REVERSED]) {
      expect(stripSettledMotionStyles(state)).toBe(PRE_COMMIT)
    }
  })

  it('strips ONLY declarations whose removal cannot change a rendered pixel', () => {
    // Each of these is a real authored style on the compared subtree, or a real
    // mid-animation value. All must survive byte-for-byte — a normaliser that ate any of
    // them would let a genuine SDK-vs-native divergence through, which is the failure
    // mode a `toContain` "fix" would have had.
    const authored = [
      // the button's own `style={fvs(470)}`
      '<b style="font-variation-settings: &quot;wght&quot; 470;">x</b>',
      // the sheen span's authored opacity + gradient
      '<i style="opacity: 0.9; background: radial-gradient(circle at 50% 50%, color-mix(in srgb, var(--color-on-primary) 22%, transparent), transparent 60%);">x</i>',
      // a transform that is NOT the resting value (the `loading` label lift)
      '<u style="transform: translateY(-4px);">x</u>',
      // opacity 0 is the loading cross-fade, not a no-op
      '<s style="opacity: 0;">x</s>',
      // prefix safety: set membership is exact, so `opacity: 1` does not swallow this
      '<em style="opacity: 10;">x</em>',
      // the TOKENS bundle's cssVars spread
      '<span style="--app-surface: var(--color-surface);">x</span>',
    ]
    for (const html of authored) expect(stripSettledMotionStyles(html)).toBe(html)

    // A mixed attribute keeps its authored half and loses only the no-ops.
    expect(
      stripSettledMotionStyles('<span style="opacity: 1; background: red; transform: none;">x</span>'),
    ).toBe('<span style="background: red;">x</span>')

    // Nothing outside a `style` attribute is in scope, not even a value that reads like
    // one. `data-style="…"` is likewise not ` style="…"`.
    const decoy = '<span data-note="opacity: 1; transform: none;" data-style="opacity: 1;">x</span>'
    expect(stripSettledMotionStyles(decoy)).toBe(decoy)
  })
})
