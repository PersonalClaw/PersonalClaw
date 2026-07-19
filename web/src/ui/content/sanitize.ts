/** Fail-closed allowlist HTML/SVG sanitizer for content rendered IN the parent
 *  DOM (svg artifacts, the editorial `document` type). LLM-authored markup —
 *  possibly echoing untrusted crawled pages — must never inject script, event
 *  handlers, or dangerous URLs into the app origin.
 *
 *  Doctrine (matches cssSanitize.ts): positive allowlist, no external dep, the
 *  browser's own parser does the tree-building. Anything not explicitly allowed
 *  is dropped. This is a SECURITY control, not formatting — when in doubt, strip.
 *
 *  NOTE: script-bearing or interactive content (widget/html/react) does NOT come
 *  here — it renders in a sandboxed blob-iframe (origin-isolated). This path is
 *  for in-DOM static markup only.
 */

type Profile = 'svg' | 'document'

// Elements permitted for editorial documents (prose + structure + tables +
// images + inline SVG). No <script>, <iframe>, <object>, <embed>, <form>,
// <link>, <meta>, <base>, <style> (style handled separately/dropped).
const DOC_TAGS = new Set([
  'a', 'abbr', 'address', 'article', 'aside', 'b', 'bdi', 'bdo', 'blockquote', 'br',
  'caption', 'cite', 'code', 'col', 'colgroup', 'data', 'dd', 'del', 'details', 'dfn',
  'div', 'dl', 'dt', 'em', 'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'header', 'hr', 'i', 'img', 'ins', 'kbd', 'li', 'main', 'mark', 'nav',
  'ol', 'p', 'pre', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'section', 'small', 'span',
  'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead',
  'time', 'tr', 'u', 'ul', 'var', 'wbr', 'picture', 'source',
  // inline SVG inside a document is allowed (re-validated by the svg branch below)
  'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'text', 'tspan', 'defs', 'linearGradient', 'radialGradient', 'stop', 'use', 'symbol',
  'clipPath', 'mask', 'pattern', 'title', 'desc',
])

// Elements permitted for a standalone SVG artifact.
const SVG_TAGS = new Set([
  'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'text', 'tspan', 'textPath', 'defs', 'linearGradient', 'radialGradient', 'stop',
  'use', 'symbol', 'clipPath', 'mask', 'pattern', 'title', 'desc', 'marker',
  'foreignObject' /* still attr-filtered; no script attrs survive */,
  'filter', 'feGaussianBlur', 'feOffset', 'feBlend', 'feColorMatrix', 'feComposite',
  'feFlood', 'feMerge', 'feMergeNode', 'feMorphology', 'feDropShadow', 'image', 'switch',
])

// Attributes allowed on any element. event handlers (on*) are NEVER allowed.
const GLOBAL_ATTRS = new Set([
  'class', 'id', 'title', 'lang', 'dir', 'role', 'colspan', 'rowspan', 'datetime',
  'cite', 'alt', 'width', 'height', 'align', 'valign', 'aria-label', 'aria-hidden',
])
// SVG presentation attrs (safe — pure visual). A broad but bounded set.
const SVG_ATTRS = new Set([
  'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
  'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity', 'fill-opacity', 'opacity',
  'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'points', 'transform',
  'viewBox', 'preserveAspectRatio', 'width', 'height', 'gradientUnits', 'gradientTransform',
  'offset', 'stop-color', 'stop-opacity', 'fill-rule', 'clip-rule', 'clip-path', 'mask',
  'text-anchor', 'font-size', 'font-family', 'font-weight', 'letter-spacing', 'dx', 'dy',
  'xmlns', 'version', 'filter', 'flood-color', 'flood-opacity', 'in', 'in2', 'result',
  'stdDeviation', 'dur', 'values', 'type', 'd', 'patternUnits', 'spreadMethod', 'href',
])
// URL-bearing attrs that must pass the safe-URL check.
const URL_ATTRS = new Set(['href', 'src', 'xlink:href'])

function isSafeUrl(v: string): boolean {
  const s = v.trim().toLowerCase()
  if (!s) return false
  // allow relative, anchors, mailto/tel, http(s); allow data:image/* (inline imgs);
  // block javascript:, vbscript:, data:text/html, and any other scheme.
  if (s.startsWith('#') || s.startsWith('/') || s.startsWith('./') || s.startsWith('../')) return true
  if (s.startsWith('mailto:') || s.startsWith('tel:')) return true
  if (s.startsWith('http://') || s.startsWith('https://')) return true
  if (/^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(s)) return true
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return false  // any explicit scheme not allowed above
  return true  // scheme-less relative-ish
}

function allowedTag(profile: Profile, tag: string): boolean {
  const t = tag.toLowerCase()
  return profile === 'svg' ? SVG_TAGS.has(t) || SVG_TAGS.has(tag) : DOC_TAGS.has(t) || SVG_TAGS.has(tag)
}

function allowedAttr(name: string): boolean {
  const n = name.toLowerCase()
  if (n.startsWith('on')) return false           // no event handlers, ever
  if (n === 'style') return false                // inline style dropped (CSS-injection vector)
  if (GLOBAL_ATTRS.has(n) || GLOBAL_ATTRS.has(name)) return true
  if (SVG_ATTRS.has(name) || SVG_ATTRS.has(n)) return true
  if (URL_ATTRS.has(n) || URL_ATTRS.has(name)) return true
  if (n.startsWith('aria-') || n.startsWith('data-')) return true
  return false
}

/** Recursively prune a node tree to the allowlist. Mutates in place. */
function clean(node: Element, profile: Profile): void {
  // Remove disallowed children first (iterate a static copy — we mutate).
  for (const child of Array.from(node.children)) {
    if (!allowedTag(profile, child.tagName)) {
      child.remove()
      continue
    }
    // Strip disallowed / unsafe attributes.
    for (const attr of Array.from(child.attributes)) {
      const name = attr.name
      if (!allowedAttr(name)) { child.removeAttribute(name); continue }
      if ((URL_ATTRS.has(name.toLowerCase()) || URL_ATTRS.has(name)) && !isSafeUrl(attr.value)) {
        child.removeAttribute(name)
      }
    }
    clean(child, profile)
  }
}

/** Sanitize an HTML/SVG string for in-DOM injection. Fail-closed: parses with
 *  the browser, prunes to the profile's allowlist, drops script/handlers/unsafe
 *  URLs, and returns the serialized safe markup. On any parse failure → ''. */
export function sanitizeInlineHtml(raw: string, profile: Profile = 'document'): string {
  if (typeof raw !== 'string' || !raw.trim()) return ''
  try {
    // SVG is parsed as image/svg+xml (well-formed), documents as text/html.
    const mime = profile === 'svg' ? 'image/svg+xml' : 'text/html'
    const doc = new DOMParser().parseFromString(raw, mime as DOMParserSupportedType)
    // A parse error yields a <parsererror> node — fail closed.
    if (doc.querySelector('parsererror')) {
      // text/html never reports parsererror; for svg fall back to wrapping + html parse
      if (profile === 'svg') {
        const htmlDoc = new DOMParser().parseFromString(raw, 'text/html')
        const svg = htmlDoc.querySelector('svg')
        if (!svg) return ''
        clean(svg, 'svg')
        // also strip attrs on the root svg
        for (const attr of Array.from(svg.attributes)) {
          if (!allowedAttr(attr.name)) svg.removeAttribute(attr.name)
        }
        return svg.outerHTML
      }
      return ''
    }
    if (profile === 'svg') {
      const svg = doc.documentElement
      if (!svg || svg.tagName.toLowerCase() !== 'svg') return ''
      for (const attr of Array.from(svg.attributes)) if (!allowedAttr(attr.name)) svg.removeAttribute(attr.name)
      clean(svg, 'svg')
      return svg.outerHTML
    }
    const body = doc.body
    if (!body) return ''
    clean(body, 'document')
    return body.innerHTML
  } catch {
    return ''
  }
}
