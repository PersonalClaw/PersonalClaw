/** Per-type export targets (the registry's `exports` slot). Each takes the current
 *  content + title and downloads/copies it. Kept tiny + dependency-free; the
 *  infographic PNG/SVG path lazy-loads the AntV engine it already uses to render. */
import { sanitizeInlineHtml } from './sanitize'
import { loadInfographicEngine } from './antvEngine'

function download(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

function safeName(title: string, fallback: string): string {
  return (title.replace(/[^a-zA-Z0-9-_ ]/g, '').trim() || fallback)
}

/** A document → a standalone, self-contained HTML file (sanitized body + a minimal
 *  editorial stylesheet inlined) so it opens cleanly anywhere, not just in-app. */
export function exportDocumentHtml(content: string, title: string): void {
  const body = sanitizeInlineHtml(content, 'document')
  const doc = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title.replace(/[<>&]/g, '')}</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 16px/1.7 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; color: #1a1a1a; background: #fff; }
  main { max-width: 72ch; margin: 0 auto; padding: 3rem 1.5rem; }
  h1,h2,h3,h4 { line-height: 1.25; margin: 2em 0 0.6em; font-weight: 650; }
  h1 { font-size: 2rem; } h2 { font-size: 1.5rem; } h3 { font-size: 1.2rem; }
  p, li { margin: 0.6em 0; } ul,ol { padding-left: 1.4em; }
  a { color: #2f54eb; } img { max-width: 100%; height: auto; }
  blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid #ddd; color: #555; }
  pre { overflow:auto; background:#f5f5f5; padding:1em; border-radius:8px; } code { font-family: ui-monospace, monospace; }
  table { border-collapse: collapse; width: 100%; } th,td { border: 1px solid #ddd; padding: 0.5em 0.7em; text-align: left; }
  @media (prefers-color-scheme: dark) { body { color:#e6e6e6; background:#141414; } blockquote{color:#aaa;border-color:#333} pre{background:#1e1e1e} th,td{border-color:#333} }
</style></head><body><main>${body}</main></body></html>`
  download(`${safeName(title, 'document')}.html`, new Blob([doc], { type: 'text/html;charset=utf-8' }))
}

/** Copy a document's sanitized HTML to the clipboard (for pasting elsewhere). */
export async function copyDocumentHtml(content: string): Promise<void> {
  const body = sanitizeInlineHtml(content, 'document')
  try { await navigator.clipboard?.writeText(body) } catch { /* clipboard blocked */ }
}

/** An infographic → SVG. Renders the DSL headless via the AntV engine (already the
 *  preview's engine) and downloads the composed SVG. */
export async function exportInfographicSvg(content: string, title: string): Promise<void> {
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-99999px;top:0;width:960px;height:720px'
  document.body.appendChild(host)
  try {
    const Infographic = await loadInfographicEngine()
    const ig = new Infographic({ container: host, width: 960, height: 720 })
    ig.render(content)
    // Give the engine a tick to compose the SVG into the (offscreen) host.
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    const svg = host.querySelector('svg')
    const out = svg ? new XMLSerializer().serializeToString(svg) : ''
    ig.destroy()
    if (out) download(`${safeName(title, 'infographic')}.svg`, new Blob([out], { type: 'image/svg+xml;charset=utf-8' }))
  } finally {
    host.remove()
  }
}

// NOTE: no PNG export. AntV infographics emit <foreignObject>, and drawing an
// SVG that contains a foreignObject onto a <canvas> taints the canvas, so
// canvas.toBlob() throws SecurityError ("Tainted canvases may not be exported").
// That's a browser security invariant, not a fixable bug — SVG (vector, lossless,
// AntV-native) is the correct and only export for infographics.
