/** Tests for the fail-closed HTML/SVG sanitizer — the boundary between
 *  model-authored markup and the app origin (which holds the user's session).
 *
 *  Two things this file is built to avoid, both of which have bitten this repo:
 *
 *  1. **A strip-only suite cannot tell a correct sanitizer from one that strips
 *     everything.** So every "this is removed" group has a matching "this still
 *     renders" group, and the legitimate-content group asserts on real output.
 *
 *  2. **A `no javascript: in the output` regex is unfalsifiable on its own** — it
 *     passes when the selector is blind, and the whole defect being fixed here was
 *     a scheme test that could not see a scheme the DOM could. So the security
 *     assertion does not grep the string: it injects the output exactly as
 *     `dangerouslySetInnerHTML` does and asks the DOM's own URL parser what every
 *     surviving attribute resolves to. `dangerousAttrs` carries its own vacuity
 *     control (see the first test) so a green cannot mean "the helper saw nothing".
 */
import { describe, it, expect } from 'vitest'
import { sanitizeInlineHtml } from './sanitize'

/** Resolve EVERY attribute value in `html` through the DOM and return the ones that
 *  land on a script-bearing scheme. Deliberately checks every attribute rather than
 *  a hand-written name list: the SVG path's serializer renames `xlink:href` to
 *  `ns1:href`, and an earlier version of this helper that matched on names silently
 *  skipped it — i.e. reported "clean" about markup it had not looked at. */
function dangerousAttrs(html: string): string[] {
  const host = document.createElement('div')
  host.innerHTML = html
  const hits: string[] = []
  host.querySelectorAll('*').forEach((el) => {
    for (const at of Array.from(el.attributes)) {
      const probe = document.createElement('a')
      probe.setAttribute('href', at.value)
      const proto = probe.protocol
      if (proto !== 'javascript:' && proto !== 'vbscript:' && proto !== 'data:') continue
      // data:image/* is allowed by policy (inline images); data:text/html is not.
      if (proto === 'data:' && /^data:image\//i.test(at.value.trim())) continue
      hits.push(`${el.tagName}@${at.name}=${JSON.stringify(at.value)} -> ${proto}`)
    }
  })
  return hits
}

/** The URL-bearing attribute names the sanitizer validates, as they appear in output. */
const URL_ATTR_RE = /\s(?:href|src|cite|xlink:href|ns\d+:href)\s*=/i

/** Payloads that MUST lose their URL attribute. Each is the raw attribute text as it
 *  appears in markup, so the HTML/XML parser's own decoding runs before the
 *  sanitizer sees it — which is the point: `java&#9;script:` arrives as a literal
 *  tab, and the URL parser then removes that tab and reads the javascript: scheme. */
const REJECTED: Array<[string, string]> = [
  // --- the reported bypass, and the same mechanism in every character it accepts.
  // `trim()` removes only leading/trailing whitespace, so an interior tab/LF/CR
  // survived to a scheme regex that cannot match one — while the URL parser strips
  // tab/LF/CR ANYWHERE and reads `javascript:`.
  ['interior tab', 'java\tscript:alert(1)'],
  ['interior LF', 'java\nscript:alert(1)'],
  ['interior CR', 'java\rscript:alert(1)'],
  ['interior CRLF', 'java\r\nscript:alert(1)'],
  ['tab before the colon', 'javascript\t:alert(1)'],
  ['a tab between every letter', 'j\ta\tv\ta\ts\tc\tr\ti\tp\tt:alert(1)'],
  ['interior tab, upper case', 'JAVA\tSCRIPT:alert(1)'],
  // --- the same bypass delivered by an HTML entity. The parser decodes entities in
  // attribute values BEFORE the sanitizer runs, so these are the tab/LF cases again.
  ['entity tab (decimal)', 'java&#9;script:alert(1)'],
  ['entity tab (hex)', 'java&#x09;script:alert(1)'],
  ['entity tab (named &Tab;)', 'java&Tab;script:alert(1)'],
  ['entity LF (named &NewLine;)', 'java&NewLine;script:alert(1)'],
  // --- a DIFFERENT mechanism with the same fail-open landing: a leading C0 control
  // that is not whitespace, so `trim()` leaves it, so `^[a-z]` cannot match — while
  // the URL parser strips leading C0-control-or-space and reads the scheme.
  ['leading \\x01 (SOH)', '\x01javascript:alert(1)'],
  ['leading \\x1b (ESC)', '\x1bjavascript:alert(1)'],
  // --- the same trick against the other script schemes and against data:text/html,
  // i.e. the bypass was not specific to javascript:.
  ['vbscript with interior tab', 'vb\tscript:msgbox(1)'],
  ['data:text/html with interior tab', 'da\tta:text/html,<script>alert(1)</script>'],
  // --- forms that were ALREADY rejected before this fix; kept so a future
  // "simplification" of the scheme test cannot quietly reopen them.
  ['plain javascript:', 'javascript:alert(1)'],
  ['mixed-case javascript:', 'JaVaScRiPt:alert(1)'],
  ['upper-case javascript:', 'JAVASCRIPT:alert(1)'],
  ['entity-encoded j', '&#106;avascript:alert(1)'],
  ['entity-encoded J', '&#74;avascript:alert(1)'],
  ['entity-encoded hex j', '&#x6a;avascript:alert(1)'],
  ['entity-encoded colon', 'javascript&colon;alert(1)'],
  ['leading space', ' javascript:alert(1)'],
  ['leading tab', '\tjavascript:alert(1)'],
  ['leading vertical tab', '\x0bjavascript:alert(1)'],
  ['leading form feed', '\x0cjavascript:alert(1)'],
  ['plain vbscript:', 'vbscript:msgbox(1)'],
  ['data:text/html', 'data:text/html,<script>alert(1)</script>'],
  ['data:text/html;base64', 'data:text/html;base64,PHNjcmlwdD48L3NjcmlwdD4='],
  ['data:image/svg+xml with a comma', 'data:image/svg+xml,<svg onload="alert(1)"/>'],
  ['blob:', 'blob:https://evil.example/x'],
  ['filesystem:', 'filesystem:https://evil.example/temporary/x'],
  ['about:blank', 'about:blank'],
  // an unrecognised scheme is rejected because it is unrecognised — this is the
  // fail-closed fallthrough, and before the fix it was a `return true`.
  ['an unrecognised scheme', 'weird-custom-scheme:payload'],
]

/** Every URL-bearing attribute and element path the sanitizer allows, so a payload
 *  cannot reach the DOM through a seam the suite never drove. */
function docCarriers(attrText: string): Array<[string, string]> {
  return [
    ['a/href', `<a href="${attrText}">click</a>`],
    ['a/HREF (upper)', `<a HREF="${attrText}">click</a>`],
    ['img/src', `<img src="${attrText}" alt="x">`],
    // NB: no legitimate second URL in these carriers — the assertion below is that
    // NO url attribute survives, so a decoy `<img src="/ok.png">` would fail it.
    ['source/src', `<picture><source src="${attrText}"></picture>`],
    ['blockquote/cite', `<blockquote cite="${attrText}">q</blockquote>`],
    ['q/cite', `<q cite="${attrText}">q</q>`],
    ['svg a/xlink:href', `<svg><a xlink:href="${attrText}"><text>t</text></a></svg>`],
    ['svg a/href', `<svg><a href="${attrText}"><text>t</text></a></svg>`],
    ['svg use/xlink:href', `<svg><use xlink:href="${attrText}"/></svg>`],
    ['foreignObject a/href', `<svg><foreignObject><a href="${attrText}">t</a></foreignObject></svg>`],
  ]
}

describe('sanitizeInlineHtml — the security assertion can actually see a leak', () => {
  it('dangerousAttrs reports a javascript: URL that IS present (vacuity control)', () => {
    // Without this, every `toEqual([])` below could be passing because the helper is
    // blind rather than because the markup is clean.
    expect(dangerousAttrs('<a href="javascript:alert(1)">x</a>')).toHaveLength(1)
    expect(dangerousAttrs('<blockquote cite="javascript:alert(1)">x</blockquote>')).toHaveLength(1)
    expect(dangerousAttrs('<a href="vbscript:msgbox(1)">x</a>')).toHaveLength(1)
    expect(dangerousAttrs('<img src="data:text/html,<b>x</b>">')).toHaveLength(1)
    // and it does NOT flag the values that are legitimately allowed
    expect(dangerousAttrs('<a href="https://example.com/a">x</a>')).toEqual([])
    expect(dangerousAttrs('<a href="./rel.md">x</a>')).toEqual([])
    expect(dangerousAttrs('<img src="data:image/png;base64,iVBORw0KGgo=">')).toEqual([])
  })

  it('resolves an interior-tab scheme the way the DOM does, not the way a regex does', () => {
    // This is the defect in one line: the URL parser removes the tab and reads a
    // scheme; a `^[a-z][a-z0-9+.-]*:` test on the raw text reads no scheme at all.
    const probe = document.createElement('a')
    probe.setAttribute('href', 'java\tscript:alert(1)')
    expect(probe.protocol).toBe('javascript:')
    expect(/^[a-z][a-z0-9+.-]*:/i.test('java\tscript:alert(1)')).toBe(false)
  })
})

describe('sanitizeInlineHtml — unsafe URLs are dropped (document profile)', () => {
  for (const [payloadName, payload] of REJECTED) {
    for (const [carrierName, markup] of docCarriers(payload)) {
      it(`drops ${payloadName} on ${carrierName}`, () => {
        const out = sanitizeInlineHtml(markup, 'document')
        expect(dangerousAttrs(out), `output still resolves to a script scheme: ${out}`).toEqual([])
        expect(out, `the URL attribute survived: ${JSON.stringify(out)}`).not.toMatch(URL_ATTR_RE)
      })
    }
  }
})

describe('sanitizeInlineHtml — unsafe URLs are dropped (svg profile)', () => {
  for (const [payloadName, payload] of REJECTED) {
    it(`renders ${payloadName} inert inside a standalone svg`, () => {
      const raw =
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">' +
        `<use xlink:href="${payload}"/><image href="${payload}"/></svg>`
      const out = sanitizeInlineHtml(raw, 'svg')
      // The security contract, asked of the DOM rather than of a regex. Note this is
      // the right assertion and "the attribute is gone" is NOT, for the svg profile:
      // XML attribute-value normalisation turns a LITERAL tab into a space before the
      // sanitizer sees it, so `java<TAB>script:` arrives as `java script:` — which the
      // URL parser reads as a relative path, because it does not strip interior
      // spaces. Those values are therefore kept, and are inert. The entity forms
      // (`&#9;`) are NOT normalised to a space, arrive as a real tab, and are dropped.
      expect(dangerousAttrs(out), `output still resolves to a script scheme: ${out}`).toEqual([])
    })
  }

  it('drops the entity-delivered tab bypass outright in the svg profile', () => {
    // The character-reference form survives XML attribute normalisation as a real
    // tab, so this is the form the svg path was genuinely exploitable through.
    const raw =
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">' +
      '<use xlink:href="java&#9;script:alert(1)"/></svg>'
    const out = sanitizeInlineHtml(raw, 'svg')
    expect(out).not.toMatch(URL_ATTR_RE)
    expect(dangerousAttrs(out)).toEqual([])
  })
})

describe('sanitizeInlineHtml — script, handlers and style are dropped', () => {
  const cases: Array<[string, string, 'document' | 'svg']> = [
    ['a <script> element', '<script>alert(1)</script><p>kept</p>', 'document'],
    ['an SVG <script> element', '<svg><script>alert(1)</script><circle r="1"/></svg>', 'document'],
    ['an <iframe>', '<iframe src="https://evil.example"></iframe><p>kept</p>', 'document'],
    ['an <object>', '<object data="x"></object><p>kept</p>', 'document'],
    ['an <embed>', '<embed src="x"><p>kept</p>', 'document'],
    ['a <form> and its controls', '<form><button formaction="javascript:alert(1)">b</button></form><p>kept</p>', 'document'],
    ['an onerror handler', '<img src="/ok.png" onerror="alert(1)" alt="x"><p>kept</p>', 'document'],
    ['an onclick handler', '<div onclick="alert(1)">t</div><p>kept</p>', 'document'],
    ['an ONCLICK handler (upper case)', '<div ONCLICK="alert(1)">t</div><p>kept</p>', 'document'],
    ['an onload on the svg root', '<svg onload="alert(1)"><circle r="1"/></svg>', 'svg'],
    ['a style attribute', '<div style="background:url(javascript:alert(1))">t</div><p>kept</p>', 'document'],
    ['a <style> element', '<style>body{display:none}</style><p>kept</p>', 'document'],
    ['a srcset', '<img srcset="javascript:alert(1)" src="/ok.png" alt="x">', 'document'],
    ['SMIL <animate> on href', '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="href" values="javascript:alert(1)"/></svg>', 'svg'],
    ['SMIL <set> on href', '<svg xmlns="http://www.w3.org/2000/svg"><set attributeName="href" to="javascript:alert(1)"/></svg>', 'svg'],
  ]
  for (const [what, raw, profile] of cases) {
    it(`drops ${what}`, () => {
      const out = sanitizeInlineHtml(raw, profile)
      expect(out).not.toMatch(/<script/i)
      expect(out).not.toMatch(/<iframe/i)
      expect(out).not.toMatch(/<style/i)
      expect(out).not.toMatch(/\son[a-z]+\s*=/i)
      expect(out).not.toMatch(/\sstyle\s*=/i)
      expect(out).not.toMatch(/\ssrcset\s*=/i)
      expect(dangerousAttrs(out)).toEqual([])
    })
  }

  it('re-parsing the output yields no handler attribute and no script element (mXSS floor)', () => {
    // The consumers serialize -> re-parse (dangerouslySetInnerHTML), which is where a
    // mutation-XSS payload would surface. Drive the round trip rather than trusting
    // that the first parse settled it.
    const payloads = [
      '<svg><title><a id="</title><img src=1 onerror=alert(1)>"></title></svg>',
      '<svg></p><title><a id="</title><img src=1 onerror=alert(1)>">',
      '<noscript><p title="</noscript><img src=1 onerror=alert(1)>">',
      '<svg><desc><![CDATA[</desc><img src=1 onerror=alert(1)>]]></desc></svg>',
    ]
    for (const raw of payloads) {
      const out = sanitizeInlineHtml(raw, 'document')
      const host = document.createElement('div')
      host.innerHTML = out
      expect(host.querySelectorAll('script'), `script survived from ${raw}`).toHaveLength(0)
      const handlers: string[] = []
      host.querySelectorAll('*').forEach((el) => {
        for (const at of Array.from(el.attributes)) if (/^on/i.test(at.name)) handlers.push(`${el.tagName}@${at.name}`)
      })
      expect(handlers, `handler survived from ${raw}`).toEqual([])
      expect(dangerousAttrs(out)).toEqual([])
    }
  })
})

describe('sanitizeInlineHtml — legitimate content still renders', () => {
  // Without this group the suite cannot distinguish a correct sanitizer from one
  // that returns '' for everything.
  const SAFE_URLS: Array<[string, string]> = [
    ['a bare relative file', 'img.png'],
    ['a relative path', 'docs/notes.md'],
    ['an explicit ./ path', './a/b.png'],
    ['a ../ path', '../a/b.png'],
    ['a root-relative path', '/api/health'],
    ['a fragment', '#section-2'],
    ['a query-only reference', '?q=1'],
    ['a relative path containing a colon', 'docs/2026-09-24:notes.md'],
    ['a fragment containing a colon', '#a:b'],
    ['mailto:', 'mailto:someone@example.com'],
    ['tel:', 'tel:+15550100'],
    ['http://', 'http://example.com/a'],
    ['https:// with query and fragment', 'https://example.com/a?q=1#f'],
    ['HTTPS:// upper case', 'HTTPS://example.com/a'],
    ['a protocol-relative URL', '//example.com/a'],
    ['data:image/png', 'data:image/png;base64,iVBORw0KGgo='],
    ['data:image/jpeg', 'data:image/jpeg;base64,/9j/4AAQ'],
    ['data:image/svg+xml', 'data:image/svg+xml;base64,PHN2Zy8+'],
  ]
  for (const [what, url] of SAFE_URLS) {
    it(`keeps ${what} on a link and an image`, () => {
      const out = sanitizeInlineHtml(`<a href="${url}">t</a><img src="${url}" alt="x">`, 'document')
      expect(out, `href was dropped from ${url}`).toContain(`href="${url}"`)
      expect(out, `src was dropped from ${url}`).toContain(`src="${url}"`)
    })
  }

  it('keeps an href a model wrapped across lines (the parser strips the newlines)', () => {
    // This is why the fix normalises rather than rejecting on sight: pretty-printed
    // markup legitimately carries LF inside an attribute value, and the URL parser
    // ignores it. Rejecting any value containing a control character would drop this.
    const out = sanitizeInlineHtml('<a href="\n   https://example.com/a\n ">t</a>', 'document')
    expect(dangerousAttrs(out)).toEqual([])
    const host = document.createElement('div')
    host.innerHTML = out
    expect(host.querySelector('a')?.getAttribute('href')).toBeTruthy()
    expect((host.querySelector('a') as HTMLAnchorElement).protocol).toBe('https:')
  })

  it('keeps a value the browser resolves as a relative path even though it holds a colon-ish scheme', () => {
    // An interior SPACE is not removed by the URL parser, so `java script:` is a
    // relative path, not a scheme — and the sanitizer must not over-block it.
    const out = sanitizeInlineHtml('<a href="my notes: draft.md">t</a>', 'document')
    expect(out).toMatch(URL_ATTR_RE)
    expect(dangerousAttrs(out)).toEqual([])
  })

  it('keeps prose, headings and inline formatting', () => {
    const out = sanitizeInlineHtml(
      '<h1>Title</h1><p>a <strong>b</strong> <em>c</em> <code>d</code> <abbr title="t">e</abbr></p><hr>',
      'document',
    )
    expect(out).toContain('<h1>Title</h1>')
    expect(out).toContain('<strong>b</strong>')
    expect(out).toContain('<em>c</em>')
    expect(out).toContain('<code>d</code>')
    expect(out).toContain('<abbr title="t">e</abbr>')
    expect(out).toContain('<hr>')
  })

  it('keeps tables with their structure and spans', () => {
    const out = sanitizeInlineHtml(
      '<table><caption>c</caption><thead><tr><th colspan="2">h</th></tr></thead>' +
        '<tbody><tr><td rowspan="2">a</td><td>b</td></tr></tbody></table>',
      'document',
    )
    for (const frag of ['<table>', '<caption>c</caption>', '<thead>', '<th colspan="2">', '<td rowspan="2">', '<tbody>'])
      expect(out).toContain(frag)
  })

  it('keeps lists, blockquotes and figures', () => {
    const out = sanitizeInlineHtml(
      '<ul><li>a</li></ul><ol><li>b</li></ol><dl><dt>t</dt><dd>d</dd></dl>' +
        '<blockquote cite="https://example.com/s">q</blockquote>' +
        '<figure><img src="/i.png" alt="x"><figcaption>cap</figcaption></figure>',
      'document',
    )
    for (const frag of ['<ul><li>a</li></ul>', '<ol><li>b</li></ol>', '<dt>t</dt>', '<dd>d</dd>',
      'cite="https://example.com/s"', '<figcaption>cap</figcaption>'])
      expect(out).toContain(frag)
  })

  it('keeps inline SVG inside a document', () => {
    const out = sanitizeInlineHtml(
      '<p>before</p><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="red"/>' +
        '<path d="M0 0H10" stroke="blue" stroke-width="2"/></svg><p>after</p>',
      'document',
    )
    expect(out).toContain('<p>before</p>')
    expect(out).toContain('<p>after</p>')
    expect(out).toContain('viewBox="0 0 10 10"')
    expect(out).toContain('fill="red"')
    expect(out).toContain('d="M0 0H10"')
    expect(out).toContain('stroke-width="2"')
  })

  it('keeps a standalone SVG artifact under the svg profile', () => {
    const out = sanitizeInlineHtml(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">' +
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/></linearGradient></defs>' +
        '<rect x="1" y="1" width="18" height="18" fill="url(#g)"/>' +
        '<text x="2" y="10" font-size="4">hi</text></svg>',
      'svg',
    )
    expect(out).toContain('<svg')
    expect(out).toContain('viewBox="0 0 20 20"')
    expect(out).toContain('<linearGradient id="g">')
    expect(out).toContain('stop-color="#f00"')
    expect(out).toContain('<rect')
    expect(out).toContain('width="18"')
    expect(out).toContain('font-size="4"')
    expect(out).toContain('hi')
  })

  it('returns the empty string for empty input and for an svg profile with no svg root', () => {
    expect(sanitizeInlineHtml('')).toBe('')
    expect(sanitizeInlineHtml('   ')).toBe('')
    expect(sanitizeInlineHtml('<p>not an svg</p>', 'svg')).toBe('')
  })

  it('recovers malformed SVG through the documented HTML-parse fallback, inertly', () => {
    // A failed XML parse yields <parsererror>, and the svg profile then re-parses as
    // HTML rather than giving up (sanitize.ts lines 123-133). So the contract here is
    // "does not throw, and whatever comes back is inert" — NOT "returns ''".
    const out = sanitizeInlineHtml('<svg><unclosed', 'svg')
    expect(out).toBe('<svg></svg>')
    expect(dangerousAttrs(out)).toEqual([])
    const withPayload = sanitizeInlineHtml('<svg><a href="java\tscript:alert(1)"><unclosed', 'svg')
    expect(withPayload).not.toMatch(URL_ATTR_RE)
    expect(dangerousAttrs(withPayload)).toEqual([])
  })
})
