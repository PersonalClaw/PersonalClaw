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

import { asTheUrlParserReadsIt, isArtifactImage } from '../../lib/artifactImage'
import { CSS_DANGEROUS_FUNC_RE, CSS_VALUE_ALLOWED_RE } from '../widget/cssSanitize'

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

// Attributes allowed on any element. event handlers (on*) are NEVER allowed. Nor is `class`:
// the markup renders in the app's own page, so it keeps no class of its own, in either profile.
// The previews' wrappers carry their classes outside the sanitized markup.
const GLOBAL_ATTRS = new Set([
  'id', 'title', 'lang', 'dir', 'role', 'colspan', 'rowspan', 'datetime',
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
// The attributes that make an element load an image, by element. Each keeps only a source the page
// itself holds (a data image) or the artifact library: an image anywhere else is a request to a
// host the markup's author picked, made the moment the preview opens. `srcset` is never allowed.
const IMAGE_SOURCE_ATTRS: Record<string, string[]> = {
  img: ['src'],
  source: ['src'],
  image: ['href', 'xlink:href'],
}

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
 *  resolve off-origin, which is not an escalation for a link: an absolute http(s) URL is
 *  allowlisted anyway. What matters is that neither can name a script scheme. An image
 *  source is held to less: only data the document holds or the artifact library
 *  (`keepPageImages`). */
function isSafeUrl(v: string): boolean {
  const s = normalizeUrl(v)
  if (!s) return false
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(s)
  if (!scheme) return true          // relative reference: path, #fragment or ?query
  if (SAFE_SCHEMES.has(scheme[1])) return true
  if (scheme[1] === 'data') return SAFE_DATA_URL.test(s)
  return false
}

/** True for an image source the page holds or this gateway's artifact library serves. */
function isPageImage(v: string): boolean {
  return SAFE_DATA_URL.test(normalizeUrl(v)) || isArtifactImage(v)
}

/** The host an image source names, or `''` for a path on this gateway or no address at all. */
function sourceHost(v: string): string {
  const here = 'this-gateway.invalid'
  try {
    const url = new URL(v.trim(), `https://${here}/`)
    return url.host === here ? '' : url.host
  } catch {
    return ''
  }
}

/** Drop each image source on *el* the page may not load. An `img` left with none becomes its
 *  words, and the host it named: "(image from example.com)". Returns the node now in its place. */
function keepPageImages(el: Element): Node {
  const tag = el.tagName.toLowerCase()
  const sources = IMAGE_SOURCE_ATTRS[tag] ?? []
  let from = ''
  let dropped = false
  // By the lower-cased name: an SVG is parsed as XML, where `HREF` is not `href`, and then
  // injected as HTML, where it is.
  for (const attr of Array.from(el.attributes)) {
    if (!sources.includes(attr.name.toLowerCase()) || isPageImage(attr.value)) continue
    from = from || sourceHost(attr.value)
    el.removeAttribute(attr.name)
    dropped = true
  }
  if (!dropped || tag !== 'img') return el
  const words = el.getAttribute('alt')?.trim() || 'image'
  const text = el.ownerDocument.createTextNode(from ? `${words} (image from ${from})` : words)
  el.replaceWith(text)
  return text
}

/** The paint and reference attributes, the ones whose value may name a `url(…)`. */
const PAINT_REFERENCE_ATTRS = new Set(['fill', 'stroke', 'filter', 'mask', 'clip-path'])

/** Each `url(…)` a paint or reference value names: its body in group 3. CSS whitespace only, the
 *  leading run taken whole (the lookahead), and no whitespace or parenthesis in the body, which CSS
 *  reads as a bad url. So each place it is tried, it is tried once: a long `url(` that never
 *  closes is read in one pass. */
const URL_FUNCTION = /url\((?=([\t\n\f\r ]*))\1(['"]?)([^'"()\t\n\f\r ]*)\2[\t\n\f\r ]*\)/gi

/** Whether *value* names only parts of this same document: each `url(…)` in it is `url(#id)`,
 *  and what is left is a plain value by cssSanitize's rule. A fallback colour after it
 *  (`url(#g) red`) is kept. That rule allows no backslash: CSS reads an escape before it looks
 *  for a function, so an escaped `url(` is still one. Nor any function that loads something
 *  (`image-set(`, `image(`, `element(`, `paint(`), whatever it names, nor a url( the pattern
 *  can't read. */
function referencesOnlyItself(value: string): boolean {
  for (const match of value.matchAll(URL_FUNCTION)) {
    if (!match[3].startsWith('#')) return false
  }
  const rest = value.replace(URL_FUNCTION, ' ').trim()
  return !rest || (CSS_VALUE_ALLOWED_RE.test(rest) && !CSS_DANGEROUS_FUNC_RE.test(rest))
}

/** Drop each attribute of *el* that is not allowed, or that points outside the document: a
 *  script-scheme URL, a paint or reference `url(…)` to anything but this document's own parts,
 *  and an `href` on anything but a link or an image source that is not a same-document `#id`
 *  (`use`, a pattern, a gradient, `textPath`). Image sources are held to their own rule
 *  (`keepPageImages`). */
function cleanAttributes(el: Element): void {
  const tag = el.tagName.toLowerCase()
  const imageSources = IMAGE_SOURCE_ATTRS[tag] ?? []
  for (const attr of Array.from(el.attributes)) {
    const name = attr.name
    const lower = name.toLowerCase()
    if (!allowedAttr(name)) { el.removeAttribute(name); continue }
    if ((URL_ATTRS.has(lower) || URL_ATTRS.has(name)) && !isSafeUrl(attr.value)) { el.removeAttribute(name); continue }
    if (PAINT_REFERENCE_ATTRS.has(lower) && !referencesOnlyItself(attr.value)) { el.removeAttribute(name); continue }
    const isHref = lower === 'href' || lower === 'xlink:href'
    if (isHref && tag !== 'a' && !imageSources.includes(lower) && !asTheUrlParserReadsIt(attr.value).startsWith('#')) {
      el.removeAttribute(name)
    }
  }
}

function allowedTag(profile: Profile, tag: string): boolean {
  const t = tag.toLowerCase()
  return profile === 'svg' ? SVG_TAGS.has(t) || SVG_TAGS.has(tag) : DOC_TAGS.has(t) || SVG_TAGS.has(tag)
}

function allowedAttr(name: string): boolean {
  const n = name.toLowerCase()
  if (n.startsWith('on')) return false           // no event handlers, ever
  if (n === 'style') return false                // inline style dropped (CSS-injection vector)
  if (n === 'class') return false                // no class of its own (see GLOBAL_ATTRS)
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
    // Strip disallowed / unsafe attributes, and every reference outside the document.
    cleanAttributes(child)
    if (keepPageImages(child) !== child) continue
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
        cleanAttributes(svg)
        return svg.outerHTML
      }
      return ''
    }
    if (profile === 'svg') {
      const svg = doc.documentElement
      if (!svg || svg.tagName.toLowerCase() !== 'svg') return ''
      cleanAttributes(svg)
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
