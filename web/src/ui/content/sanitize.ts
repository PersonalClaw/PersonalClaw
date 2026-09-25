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
// URL-bearing attrs that must pass the safe-URL check. `cite` (<blockquote>, <q>,
// <del>, <ins>) is a URL attribute too — it is allowed by GLOBAL_ATTRS, so without
// it here its value would be the one URL in the output that is never checked.
const URL_ATTRS = new Set(['href', 'src', 'xlink:href', 'cite'])

// Schemes permitted in an allowlisted URL attribute. Everything else —
// javascript:, vbscript:, blob:, filesystem:, about:, and anything merely
// unrecognised — is dropped.
const SAFE_SCHEMES = new Set(['http', 'https', 'mailto', 'tel'])
// data: is for inline images only. data:text/html is a script-bearing document.
const SAFE_DATA_URL = /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/

/** Normalise a URL value the way the URL parser does *before* it reads the scheme,
 *  so this check and the browser agree on where the scheme ends.
 *
 *  This is load-bearing, not tidying. The URL spec removes EVERY ASCII tab, LF and
 *  CR from a URL and strips leading/trailing C0-control-or-space before parsing the
 *  scheme. So `java<TAB>script:alert(1)` — which `java&#9;script:` in the markup
 *  delivers as a literal tab, because the HTML parser decodes entities before we
 *  ever see the value — is the `javascript:` scheme to the DOM, while a scheme regex
 *  run on the raw text sees no scheme at all. Normalising first is what closes that
 *  gap; testing first is what opened it.
 *
 *  Note `trim()` alone cannot do this: it removes neither interior whitespace nor
 *  the non-whitespace C0 controls (`\x01`, `\x1b`) that the parser strips at the
 *  edges.
 *
 *  One deliberate deviation from the spec: DEL (\x7f) is stripped at the edges too.
 *  The parser percent-encodes it instead, but no real URL begins or ends with DEL,
 *  and this is a fail-closed control.
 */
function normalizeUrl(v: string): string {
  return v
    .replace(/[\x09\x0a\x0d]/g, '')     // tab/LF/CR: removed ANYWHERE by the parser
    .replace(/^[\x00-\x20\x7f]+/, '')   // leading C0-control-or-space (and DEL)
    .replace(/[\x00-\x20\x7f]+$/, '')   // trailing C0-control-or-space (and DEL)
    .toLowerCase()
}

/** True only for a URL this sanitizer can positively classify as safe.
 *
 *  Fail-closed by construction: there is no "anything else is probably relative"
 *  branch. A value is safe because it has an allowlisted scheme, or because it has
 *  no scheme at all — and nothing else reaches a `return true`.
 *
 *  A scheme-less value resolves against the app's own base URL. `//host` and `/\host`
 *  resolve off-origin, which is not an escalation: an absolute http(s) URL is
 *  allowlisted anyway. What matters is that neither can name a script scheme. */
function isSafeUrl(v: string): boolean {
  const s = normalizeUrl(v)
  if (!s) return false
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(s)
  if (!scheme) return true          // relative reference: path, #fragment or ?query
  if (SAFE_SCHEMES.has(scheme[1])) return true
  if (scheme[1] === 'data') return SAFE_DATA_URL.test(s)
  return false
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
