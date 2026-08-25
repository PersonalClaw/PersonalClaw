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
    const contributed = app.container.querySelector('button')!.closest('div[class]')!.outerHTML

    const native = render(<NativePage />)
    const baseline = native.container.querySelector('button')!.closest('div[class]')!.outerHTML

    // Non-vacuity: the compared markup must actually be a rendered primitive, not two
    // empty strings agreeing. A Button carries its variant's token classes.
    expect(baseline).toContain('<button')
    expect(baseline.length).toBeGreaterThan(120)
    expect(contributed).toBe(baseline)
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
