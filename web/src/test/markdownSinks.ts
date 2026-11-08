// ── Test support for the sinks that show content as markdown ─────────────────────────────────
//
// One set of fixtures and checks for every surface that renders text the app did not write: the
// renderer's own tests and each surface's. A surface's test renders its sink with these strings
// and asserts with these helpers, so "renders markdown", "starts no block" and "stays inert" mean
// the same thing everywhere.

/** A one-line field the way an agent writes it: two code spans and a bold run. */
export const MD_LINE = 'Skip `model_calls` in `export_digest()` in **published.py**'

/** A body: a heading, a list, a code span and a fence. */
export const MD_BLOCK = '## Plan\n\n- read `notes.md` first\n- **ship** the digest\n\n```sh\nmake test\n```\n\nThen *tell* the owner.'

/** Embedded HTML a stored body may hold: [what it is, the markup, what must not exist, the text a
 *  reader sees (the markup itself unless stated)]. A bare formatting CLOSER after an
 *  attribute-bearing opener is the one asymmetry: the opener is text, and the HTML parser drops the
 *  closer as the stray end tag it now is, so the reader sees the opener and the words, and nothing
 *  was created either way. */
export const EMBEDDED_HTML: Array<[string, string, string, string?]> = [
  [
    'a form that posts what is typed elsewhere',
    '<form action="https://collect.example.com/login"><input name="password" type="password"><button>Sign in</button></form>',
    'form, input, button',
  ],
  ['a frame with a document of its own', '<iframe srcdoc="<script>parent.document.title=1</script>"></iframe>', 'iframe'],
  ['a style block', '<style>body { display: none }</style>', 'style'],
  ['an image tag with an inline handler', '<img src="https://images.example.com/p.png" onerror="alert(1)">', 'img'],
  ['a link with a script scheme', '<a href="javascript:alert(1)">open the report</a>', 'a'],
  ['a script', '<script>alert(1)</script>', 'script'],
  ['a refresh directive', '<meta http-equiv="refresh" content="0;url=https://phish.example.com/">', 'meta'],
  ['an embedded object', '<object data="https://plugins.example.com/x.swf"></object>', 'object'],
  ['an svg with a load handler', '<svg onload="alert(1)"><circle r="4"></circle></svg>', 'svg'],
  ['a formatting tag carrying a handler', '<details open ontoggle="alert(1)"><summary>More</summary></details>', 'details, summary'],
  ['a formatting tag carrying a style', '<b style="position:fixed;inset:0">covered</b>', 'b', '<b style="position:fixed;inset:0">covered'],
  ['a widget block', '<widget title="Chart"><script>parent.postMessage("go", "*")</script></widget>', 'iframe'],
]

/** Link targets the renderer refuses: [what it is, the href]. */
export const REFUSED_HREFS: Array<[string, string]> = [
  ['a script scheme', 'javascript:alert(1)'],
  ['a script scheme split by a tab', 'java&#9;script:alert(1)'],
  ['a document in a data URL', 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='],
  ['a telephone link', 'tel:+15550100'],
  ['a local file', 'file:///etc/hosts'],
  ['a path on this gateway', '/api/knowledge/items'],
  ['a route inside the app', '#/settings/security'],
  ['a host with no scheme', 'accounts.example.com/login'],
  ['a protocol-relative host', '//accounts.example.com/login'],
]

/** Every hostile row, as each is planted in a line: each embedded-HTML row's markup, each refused
 *  address as a link's target, and a tracking image. */
export const HOSTILE_ROWS: string[] = [
  ...EMBEDDED_HTML.map(([, markup]) => markup),
  ...REFUSED_HREFS.map(([, href]) => `[open the report](${href})`),
  '![tracker](https://images.example.com/p.png)',
]

/** One line holding every hostile row: what each surface plants at its own sinks. Rendered
 *  anywhere, it must leave no element, no handler and no anchor. The leading word keeps it one
 *  paragraph: a line that opens with `<form` is an HTML block, and nothing in it is parsed. */
export const HOSTILE = ['Notes', ...HOSTILE_ROWS].join(' ')

/** A line that CommonMark reads as a block, one per construct `line` mode turns off:
 *  [the construct's micromark name, the line]. Each renders as itself in `line` mode, and an
 *  excerpt (inline mode) flattens it to something else, so each plant proves its construct's name.
 *  (An indented line is the one more: `line` mode trims it as any paragraph is trimmed, and draws
 *  no code for it.) */
export const LEADING: Array<[string, string]> = [
  ['headingAtx', '# of retries'],
  ['setextUnderline', 'Ship it\n==='],
  ['thematicBreak', '***'],
  ['list (a bullet)', '- draft the notes'],
  ['list (a number and a dot)', '2024. Year review'],
  ['list (a number and a paren)', '1) Fix'],
  ['blockQuote', '> 5 files'],
  ['codeFenced', '~~~ notes'],
  ['definition', '[Bug]: crash'],
  ['gfmFootnoteDefinition', '[^1]: see the note'],
  ['table', 'a | b\n--- | ---'],
  ['mathFlow', '$$ total'],
]

/** Each inline construct, in a line: [what it is, the line, the words a reader sees]. The plain
 *  previews (`previewText`, `previewLine`) and the renderer's `line` mode all read each one so. */
export const INLINE_CONSTRUCTS: Array<[string, string, string]> = [
  ['a code span', 'Run `make test` now', 'Run make test now'],
  ['a code span holding underscores', 'Fix the `__init__.py` import', 'Fix the __init__.py import'],
  ['bold, both ways', 'A **firm** and __fixed__ date', 'A firm and fixed date'],
  ['italic, both ways', 'An *open* and _loose_ end', 'An open and loose end'],
  ['an underscore inside a word', 'Skip model_calls here', 'Skip model_calls here'],
  ['strikethrough, double and single', '~~old~~ and ~gone~ plans', 'old and gone plans'],
  ['a link', 'Read [the notes](https://example.com/notes) first', 'Read the notes first'],
  ['a link whose address holds parentheses', 'Reply to [the thread](javascript:alert(1)) today', 'Reply to the thread today'],
  ['an image', 'Logo ![the mark](https://images.example.com/logo.png) beside it', 'Logo the mark beside it'],
  ['an autolink', 'See <https://example.com/a> now', 'See https://example.com/a now'],
  ['an email autolink', 'Write <owner@example.com> today', 'Write owner@example.com today'],
  ['a formatting tag', 'Press <kbd>K</kbd> to search', 'Press K to search'],
  ['a tag that is not formatting', 'Keep <span>this</span> as is', 'Keep <span>this</span> as is'],
  ['an escaped mark', 'Five \\*stars\\* exactly', 'Five *stars* exactly'],
  ['an entity', 'Tom &amp; Jerry &#38; friends', 'Tom & Jerry & friends'],
  ['inline math', 'Area is $$r^2$$ here', 'Area is r^2 here'],
  ['an HTML line break', 'Done<br>Next: deploy', 'Done Next: deploy'],
  ['an HTML line break written as a closer', 'Done</br>Next', 'Done Next'],
  ['an HTML comment', 'Ship <!-- note --> it', 'Ship it'],
]

/** Elements that make a row a block, or a control inside a control. */
const BLOCKS = 'p, div, pre, h1, h2, h3, h4, h5, h6, ul, ol, li, blockquote, table, section, hr, details'

/** `el` shows a line's marks rendered: a `code` and a `strong`, no literal backtick or `**`, and
 *  no block inside it. */
export function rendersLine(el: Element): string[] {
  const problems: string[] = []
  if (!el.querySelector('code')) problems.push('no code element')
  if (!el.querySelector('strong')) problems.push('no strong element')
  const text = el.textContent ?? ''
  if (text.includes('`')) problems.push('a literal backtick')
  if (text.includes('**')) problems.push('a literal **')
  const block = el.querySelector(BLOCKS)
  if (block) problems.push(`a block descendant: ${block.tagName.toLowerCase()}`)
  return problems
}

/** `el` shows a body's blocks rendered: a heading, a list, a code span and a code block. */
export function rendersBlock(el: Element): string[] {
  const problems: string[] = []
  if (!el.querySelector('h1, h2, h3, h4')) problems.push('no heading')
  if (!el.querySelector('ul > li, ol > li')) problems.push('no list')
  if (!el.querySelector(':not(pre) > code')) problems.push('no code span')
  if (!el.querySelector('pre')) problems.push('no code block')
  if ((el.textContent ?? '').includes('**')) problems.push('a literal **')
  return problems
}

/** The elements a sink of text may never hold. */
const LIVE = ['a', 'img', 'input', 'iframe', 'form', 'script', 'style', 'object', 'svg', 'button', 'meta']

/** `el` holds nothing live: no a, img, input, iframe, form, script, style, object, svg, button or
 *  meta, and no `on*` attribute anywhere. A body's sink (`{ body: true }`) holds the app's own
 *  controls, which the rest of it must not be: an https image is a "Load image" button with an icon,
 *  and a fence has Copy. So with `body`, a button and an svg pass, and a handler on one still does
 *  not. */
export function inert(el: Element, { body = false }: { body?: boolean } = {}): string[] {
  const tags = body ? LIVE.filter((tag) => tag !== 'button' && tag !== 'svg') : LIVE
  const live = Array.from(el.querySelectorAll(tags.join(', '))).map((n) => n.tagName.toLowerCase())
  const handlers = Array.from(el.querySelectorAll('*')).flatMap((n) =>
    Array.from(n.attributes).filter((a) => /^on/i.test(a.name)).map((a) => `${n.tagName.toLowerCase()}.${a.name}`),
  )
  return [...live, ...handlers]
}

/** `s` is plain text fit for an attribute: no markdown mark and no tag. */
export function plain(s: string): string[] {
  const problems: string[] = []
  if (/[`]/.test(s)) problems.push('a backtick')
  if (/\*\*|__/.test(s)) problems.push('a bold mark')
  if (/<\/?[a-z][^>]*>/i.test(s)) problems.push('a tag')
  if (/^\s{0,3}#{1,6}\s/.test(s)) problems.push('a heading mark')
  return problems
}

/** A member chain (`task.title`, `item?.reason`, `step!.title`), an identifier, or a literal: the
 *  operands a JSX expression joins with `??` or `||`. */
const MEMBER = String.raw`[\w$]+(?:(?:\?\.|!?\.)[\w$]+)+`
const OPERAND = String.raw`(?:${MEMBER}|[\w$]+|'[^']*'|"[^"]*")`
const SINK = new RegExp(String.raw`\{\s*(${OPERAND}(?:\s*(?:\?\?|\|\|)\s*${OPERAND})*)\s*\}`, 'g')
const A_MEMBER = new RegExp(`^${MEMBER}$`)

/** JSX text in `source` that interpolates one of `fields` straight into the markup, as
 *  `{task.title}`, `{item?.reason ?? ''}` or `{sub.title || sub.task || '(task)'}`: a sink that shows
 *  the field raw. A field handed to a prop (`text={task.title}`), a template literal, a call's
 *  argument or a `<Markdown>`'s child is not a raw sink, and is not found; nor is a field inside a
 *  ternary, which a reviewer reads. Each markdown PR runs it over its own files with the fields it
 *  renders. Returns `line: match` for each. */
export function rawSinks(source: string, fields: readonly string[]): string[] {
  const wanted = new Set(fields)
  const found: string[] = []
  source.split('\n').forEach((text, i) => {
    for (const m of text.matchAll(SINK)) {
      const drawsAField = m[1].split(/\s*(?:\?\?|\|\|)\s*/).some((operand) =>
        A_MEMBER.test(operand) && wanted.has(operand.split(/\?\.|!?\./).pop() ?? ''))
      if (!drawsAField) continue
      const before = text.slice(0, m.index).trimEnd()
      // After `=` it is an attribute, after `$` a template literal, after `(` `,` `:` `?` `&` `|` an
      // expression's operand: none of them is text on the page. The child of a `<Markdown>` is the
      // renderer's input, which is the fix.
      if (/[=$(,:?&|]$/.test(before) || /<Markdown\b[^<>]*>$/.test(before)) continue
      found.push(`${i + 1}: ${m[0]}`)
    }
  })
  return found
}
