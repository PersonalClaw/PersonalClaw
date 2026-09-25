import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── PWA installability criteria, asserted directly (MOBILE-COMPANION T3.1) ───
//
// The atom's done-when says "Lighthouse installability passes". Lighthouse is a
// browser audit and is not part of this repo's dependency set, so the criteria it
// checks are asserted here instead, one per test, against the real shipped files:
//
//   * a parseable manifest with `name`/`short_name`,
//   * `start_url` present — and pointing at a route that actually exists,
//   * `display` in the installable set,
//   * PNG icons at 192 and 512, whose REAL pixel dimensions match the declared
//     `sizes` (a mismatch is the classic silent installability failure),
//   * a maskable icon,
//   * the manifest linked from index.html in a way that survives this origin's
//     session auth.
//
// The one criterion no static test can cover — "a service worker with a fetch
// handler is registered on a secure origin" — is covered behaviourally in
// `web/e2e/pwa.spec.ts`.

const WEB_DIR = join(__dirname, '..', '..')
const PUBLIC_DIR = join(WEB_DIR, 'public')

type Icon = { src: string; sizes: string; type: string; purpose?: string }
type Manifest = {
  name?: string
  short_name?: string
  start_url?: string
  scope?: string
  display?: string
  theme_color?: string
  background_color?: string
  icons: Icon[]
}

const manifest = JSON.parse(
  readFileSync(join(PUBLIC_DIR, 'manifest.webmanifest'), 'utf8'),
) as Manifest

/** Real pixel dimensions from a PNG's IHDR chunk — the declared `sizes` string is
 *  a claim, this is the fact. */
function pngSize(file: string): { width: number; height: number } {
  const buf = readFileSync(join(PUBLIC_DIR, file))
  expect(buf.subarray(1, 4).toString('ascii'), `${file} is not a PNG`).toBe('PNG')
  expect(buf.subarray(12, 16).toString('ascii'), `${file} has no IHDR`).toBe('IHDR')
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) }
}

describe('manifest.webmanifest — installability', () => {
  it('is valid JSON with a name and a short_name', () => {
    expect(manifest.name).toBeTruthy()
    // Home-screen labels are truncated around 12 characters on both platforms.
    expect(manifest.short_name!.length).toBeLessThanOrEqual(12)
  })

  it('starts at the companion route', () => {
    expect(manifest.start_url).toBe('/#/companion')
  })

  it('🔴 start_url is a route the shell will not correct away (#3506)', () => {
    // This used to assert only that `App.tsx` CONTAINS the string `'companion'` — and it was
    // green the whole time an installed PWA opened on the dashboard. The route was registered
    // and then rewritten: `App.tsx`'s unknown-hash corrector (#306) tested `ROUTABLE`, and
    // `companion` is deliberately outside it because it renders from an early return.
    //
    // "A route the SPA serves" therefore needs two facts, not one: the shell renders it, AND
    // the corrector admits it. `SHELL_ROUTES` is the set that carries both, so that is what is
    // read. Still by string rather than by import — importing App.tsx pulls in the whole SPA.
    const app = readFileSync(join(WEB_DIR, 'src', 'app', 'App.tsx'), 'utf8')
    const hash = manifest.start_url!.split('#')[1]
    expect(hash).toBe('/companion')
    const declared = app.match(/const SHELL_ROUTES = new Set\(\[([^\]]*)\]\)/)
    expect(declared, 'App.tsx declares no SHELL_ROUTES for the corrector to consult').toBeTruthy()
    const shellRoutes = [...declared![1].matchAll(/'([^']+)'/g)].map((m) => m[1])
    expect(shellRoutes, `start_url ${manifest.start_url} is not a shell route`)
      .toContain(hash.replace(/^\//, ''))
    // The behavioural half — that the hash is still the hash after the app boots — is
    // `web/e2e/pwa.spec.ts`, because no static read can see a navigation.
  })

  it('declares an installable display mode', () => {
    expect(['standalone', 'fullscreen', 'minimal-ui']).toContain(manifest.display)
  })

  it('scopes the app to the whole origin so the SPA can navigate in-app', () => {
    expect(manifest.scope).toBe('/')
    expect(manifest.start_url!.startsWith(manifest.scope!)).toBe(true)
  })

  it('declares theme and background colors for the splash screen', () => {
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i)
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i)
  })

  it('ships PNG icons at 192 and 512 whose real pixels match the declared sizes', () => {
    for (const declared of ['192x192', '512x512']) {
      const icon = manifest.icons.find((i) => i.sizes === declared && i.type === 'image/png')
      expect(icon, `no PNG icon declared at ${declared}`).toBeDefined()
      const [w, h] = declared.split('x').map(Number)
      expect(pngSize(icon!.src)).toEqual({ width: w, height: h })
    }
  })

  it('ships a maskable icon so Android does not letterbox it', () => {
    const maskable = manifest.icons.filter((i) => (i.purpose ?? '').split(' ').includes('maskable'))
    expect(maskable.length).toBeGreaterThan(0)
  })

  it('references only icons that exist and are served from a stable path', () => {
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith('/icons/'), `${icon.src} is outside /icons/`).toBe(true)
      expect(() => readFileSync(join(PUBLIC_DIR, icon.src))).not.toThrow()
    }
  })
})

describe('index.html — how the manifest reaches the browser', () => {
  const html = readFileSync(join(WEB_DIR, 'index.html'), 'utf8')

  it('links the manifest with use-credentials', () => {
    // Load-bearing. A manifest is fetched with credentials omitted by default, and
    // this origin requires the pc_token_<port> cookie (the PWA paths are NOT in
    // token_auth's bypass sets, by design). Without this attribute the fetch 403s
    // and the ONLY symptom is an install prompt that never appears.
    expect(html).toMatch(
      /<link\s+rel="manifest"\s+href="\/manifest\.webmanifest"\s+crossorigin="use-credentials"/,
    )
  })

  it('declares a theme-color matching the manifest', () => {
    expect(html).toContain(`<meta name="theme-color" content="${manifest.theme_color}" />`)
  })

  it('declares an apple-touch-icon — iOS ignores manifest icons for the home screen', () => {
    expect(html).toMatch(/<link rel="apple-touch-icon" href="\/icons\/icon-192\.png"/)
  })

  it('declares standalone capability for iOS, which ignores manifest display', () => {
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />')
    expect(html).toContain('<meta name="mobile-web-app-capable" content="yes" />')
  })
})
