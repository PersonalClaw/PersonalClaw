/** A widget document fetches nothing: what it runs, styles and sets in type is inside it.
 *
 *  PersonalClaw is local-first and tracks nothing, so the frame an agent's widget or react
 *  artifact renders in must reach no third party — not for Tailwind, not for React, not for a
 *  compiler. Both halves are pinned: the policy the document carries names no other origin, and
 *  the document itself references none (the Tailwind CSS rides in a `<style>`, React and the
 *  compiled component in inline `<script>`s). */
import { describe, it, expect } from 'vitest'
import { buildSrcdoc, buildReactSrcdoc } from './widgetSrcdoc'

const THEME = { '--bg': 'black' }
const CSS = '.p-4{padding:1rem}'
const CODE = 'function App(){return null}'
const RUNTIME = 'window.React={}'

const DOCUMENTS: Array<[string, () => string]> = [
  ['an inline chat widget', () => buildSrcdoc({ html: '<div class="p-4">hi</div>', css: CSS, themeVars: THEME, mode: 'dark', transparentBody: true, editMode: true })],
  ['a downloaded widget', () => buildSrcdoc({ html: '<div class="p-4">hi</div>', css: CSS, themeVars: THEME, mode: 'light', includeHost: false })],
  ['a react artifact', () => buildReactSrcdoc({ code: CODE, runtime: RUNTIME, css: CSS, themeVars: THEME, mode: 'dark' })],
]

const parse = (doc: string) => new DOMParser().parseFromString(doc, 'text/html')

/** Sources a document may name without reaching anywhere: keywords, and data held in the page. */
const LOCAL_SOURCES = new Set(["'none'", "'unsafe-inline'", "'unsafe-eval'", 'data:', 'blob:'])

/** The sources in a document's policy that are somewhere else, as `directive source`. */
function remoteSources(doc: string): string[] {
  const meta = parse(doc).querySelector('meta[http-equiv="Content-Security-Policy"]')
  if (!meta) return ['(no policy at all)']
  return (meta.getAttribute('content') ?? '').split(';').map((d) => d.trim()).filter(Boolean)
    .flatMap((d) => {
      const [name, ...sources] = d.split(/\s+/)
      return sources.filter((s) => !LOCAL_SOURCES.has(s)).map((s) => `${name} ${s}`)
    })
}

/** Everything in a document that makes the browser fetch: an element pointing at a URL, or a
 *  stylesheet importing or linking one. Comments are not references (Tailwind's licence banner
 *  names its homepage). */
function references(doc: string): string[] {
  const parsed = parse(doc)
  const elements = [...parsed.querySelectorAll('[src], link[href]')].map((el) => el.outerHTML)
  const styles = [...parsed.querySelectorAll('style')]
    .map((s) => (s.textContent ?? '').replace(/\/\*[\s\S]*?\*\//g, ''))
    .flatMap((css) => css.match(/@import[^;]*|url\([^)]*\)|https?:\/\/[^\s"')]+/g) ?? [])
  return [...elements, ...styles]
}

describe('a widget document fetches nothing', () => {
  it.each(DOCUMENTS)('%s carries a policy that names no other origin', (_name, build) => {
    const doc = build()
    expect(remoteSources(doc)).toEqual([])
    const policy = parse(doc).querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content') ?? ''
    // Off the network entirely, and nothing to load that the page does not already hold.
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain("connect-src 'none'")
  })

  it.each(DOCUMENTS)('%s references nothing outside itself', (_name, build) => {
    expect(references(build())).toEqual([])
  })

  it.each(DOCUMENTS)('%s holds its Tailwind CSS inline', (_name, build) => {
    const styles = [...parse(build()).querySelectorAll('style')].map((s) => s.textContent ?? '')
    expect(styles.some((s) => s.includes(CSS))).toBe(true)
  })

  it('a react artifact holds React and its component inline', () => {
    const [, , [, build]] = DOCUMENTS
    const scripts = [...parse(build()).querySelectorAll('script')].map((s) => s.textContent ?? '')
    expect(scripts.some((s) => s.includes(RUNTIME))).toBe(true)
    expect(scripts.some((s) => s.includes(CODE))).toBe(true)
  })

  it('the checks see a document that does fetch', () => {
    // Detection direction: both checkers must report what the old CDN documents carried.
    const fetching = `<!DOCTYPE html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.example.com; style-src https:">
<script src="https://cdn.example.com/tailwind.js"></script>
<style>@import url("https://fonts.example.com/a.css"); b{background:url(https://img.example.com/x.png)}</style>
</head><body></body></html>`
    expect(remoteSources(fetching)).toEqual(['script-src https://cdn.example.com', 'style-src https:'])
    // The script tag, the stylesheet import, and the background image.
    expect(references(fetching)).toHaveLength(3)
  })
})

describe('what a document inlines cannot end the element it sits in', () => {
  it('CSS cannot close its <style> and open a script', () => {
    const doc = buildSrcdoc({ html: '<p>x</p>', css: 'a{}</style><script>window.escaped=1</script>', themeVars: THEME, mode: 'dark', includeHost: false })
    const scripts = [...parse(doc).querySelectorAll('script')].map((s) => s.textContent ?? '')
    expect(scripts.some((s) => s.includes('window.escaped=1'))).toBe(false)
  })

  it('a component cannot close its <script> and open another', () => {
    const doc = buildReactSrcdoc({ code: 'var s = "</script><script>window.escaped=1</script>"', runtime: RUNTIME, css: '', themeVars: THEME, mode: 'dark' })
    const scripts = [...parse(doc).querySelectorAll('script')].map((s) => (s.textContent ?? '').trim())
    // The component is still ONE script, whose text is what the component said.
    expect(scripts.filter((s) => s.startsWith('window.escaped'))).toEqual([])
    expect(scripts.some((s) => s.includes('var s = "<\\/script><script>window.escaped=1<\\/script>"'))).toBe(true)
  })
})
