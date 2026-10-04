// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── #492, second clause: no frame may CLAIM isolation it does not have ────────────────
//
// `ChatEmbed` described its iframe as "a sandboxed iframe — full isolation, the app never
// touches the chat internals" directly above
// `sandbox: 'allow-scripts allow-same-origin allow-forms'`, with `src` built from
// `location.origin`. `allow-scripts` + `allow-same-origin` together is the documented
// no-op pair: the framed document can reach its parent and clear its own sandbox
// attribute. So the one frame in the tree that claimed full isolation was the one frame
// that had none — and the reader most likely to rely on that comment is the next person
// deciding whether app UI needs isolating.
//
// The attribute itself is CORRECT and stays: the chat route needs the host session, so
// `allow-same-origin` is required, and the deny-by-default residue (no top-level
// navigation, no popups, no downloads) is worth keeping. What was wrong was the sentence.
//
// This rails the PROPERTY rather than the sentence: a frame that opts out of origin
// isolation must not describe itself as isolated. Counted, because the count is the fact
// that makes the rule cheap — every other sandboxed frame in `web/src` is
// `sandbox="allow-scripts"` off a blob (null) origin, which IS origin-isolated. Exactly
// one frame is same-origin, and a second appearing silently is the regression to catch.

const ROOT = join(process.cwd(), 'src')

function sources(dir: string): string[] {
  return filesUnder(dir, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
}

/** Files declaring an iframe sandbox that keeps the frame in the PARENT's origin. */
function sameOriginFrames(): { file: string; text: string }[] {
  const hits: { file: string; text: string }[] = []
  for (const file of sources(ROOT)) {
    const text = readSource(file)
    for (const m of text.matchAll(/sandbox[:=]\s*['"]([^'"]*)['"]/g)) {
      const tokens = m[1].split(/\s+/)
      if (tokens.includes('allow-scripts') && tokens.includes('allow-same-origin')) {
        hits.push({ file: file.slice(ROOT.length + 1), text })
      }
    }
  }
  return hits
}

describe('a same-origin sandbox never claims isolation (#492)', () => {
  it('exactly one frame in web/src opts out of origin isolation', () => {
    const hits = sameOriginFrames()
    expect(hits.map((h) => h.file)).toEqual(['app/appSdk.tsx'])
  })

  it('that frame does not describe itself as isolated', () => {
    const [hit] = sameOriginFrames()
    // Scoped to the component, not the file: `appSdk.tsx` legitimately discusses the
    // widget frames elsewhere, which ARE isolated.
    const start = hit.text.indexOf('export function ChatEmbed')
    const doc = hit.text.slice(hit.text.lastIndexOf('/**', start), hit.text.indexOf('}', start))
    expect(doc).not.toMatch(/full isolation/i)
    // Deliberately NOT a blanket ban on the word: the first draft of this rail forbade
    // /isolat(ed|ion)/ outside a negation and went red on "NOT an isolation boundary" —
    // the marker matching its own negation. The positive assertions below are the real
    // rail, because they cannot be satisfied by a comment that overclaims.
    // And it says the true thing, so the next reader does not have to re-derive it.
    expect(doc).toMatch(/same-origin by construction/)
    expect(doc).toMatch(/NOT an isolation boundary/)
  })

  it('the isolated frames are still allowed to say so', () => {
    // The rail must not become "never write the word isolation". `WidgetFrame` runs
    // `sandbox="allow-scripts"` off a blob (null) origin — genuinely origin-isolated —
    // and its comment is accurate. If this ever fails, the rail above got too greedy.
    const widget = readSource(join(ROOT, 'ui/widget/WidgetFrame.tsx'))
    expect(widget).toMatch(/sandbox="allow-scripts"/)
    expect(widget).not.toMatch(/allow-same-origin/)
  })
})

// ── Every frame, not only the ones that declare a sandbox ─────────────────────────────────────
//
// The rail above counts frames whose `sandbox` holds both `allow-scripts` and `allow-same-origin`,
// so a frame with NO `sandbox` attribute at all was invisible to it — and that is exactly how the
// deployed-artifact Preview pane framed a model-authored page in the dashboard's own origin, where
// its script reached the dashboard window and the owner's session through `window.parent`. A
// missing attribute is the most permissive sandbox there is, so it has to be the first thing
// counted. Every frame-creating site in `web/src` is found here — a JSX `<iframe>` or
// `<motion.iframe>`, a `createElement('iframe', {…})`, and a DOM `createElement('iframe')` with
// the `setAttribute('sandbox', …)` that follows it — and each must declare its sandbox as a
// literal this rail can read. The token set is `allow-scripts` and nothing else: framed content
// runs its scripts in an opaque origin, with no popups, no top-level navigation and no forms. The
// one exception is the same-origin chat embed named above, by file.

interface FrameSite { file: string; line: number; sandbox: string[] | null }

/** The index just past the character that ends the construct opening at `from`: the `>` that
 *  closes a JSX opening tag (`until: 'tag'`), or the `}` that closes an object literal. Braces,
 *  string literals and comments are stepped over, so a `>` in an arrow function or a comparison
 *  does not end a tag and an apostrophe in a comment does not open a string. */
function endOf(text: string, from: number, until: 'tag' | 'object'): number {
  let depth = 0
  let quote = ''
  for (let i = from; i < text.length; i++) {
    const c = text[i]
    if (quote) { if (c === quote && text[i - 1] !== '\\') quote = ''; continue }
    if (c === '/' && text[i + 1] === '/') { i = text.indexOf('\n', i); if (i === -1) break; continue }
    if (c === '/' && text[i + 1] === '*') { i = text.indexOf('*/', i) + 1; if (i === 0) break; continue }
    if (c === '"' || c === "'" || c === '`') quote = c
    else if (c === '{') depth++
    else if (c === '}' && --depth === 0 && until === 'object') return i + 1
    else if (c === '>' && depth === 0 && until === 'tag') return i + 1
  }
  return text.length
}

const tokens = (value: string | undefined) => (value === undefined ? null : value.trim().split(/\s+/))
const lineOf = (text: string, index: number) => text.slice(0, index).split('\n').length

/** `text` with its comments blanked out, newlines kept, so a comment that mentions `<iframe>`
 *  is not a frame and every index and line number still points where it did. A `//` counts as
 *  a comment only after the start of a line or a space, so `'https://…'` is left alone. */
function codeOf(text: string): string {
  const blank = (s: string) => s.replace(/[^\n]/g, ' ')
  return text.replace(/\/\*[\s\S]*?\*\//g, blank).replace(/(^|\s)\/\/[^\n]*/g, (m, lead: string) => lead + blank(m.slice(lead.length)))
}

function frameSites(): FrameSite[] {
  const sites: FrameSite[] = []
  for (const path of sources(ROOT)) {
    const text = codeOf(readSource(path))
    const file = path.slice(ROOT.length + 1)
    for (const m of text.matchAll(/<(?:motion\.)?iframe\b/g)) {
      const tag = text.slice(m.index, endOf(text, m.index, 'tag'))
      const value = tag.match(/\bsandbox=(?:"([^"]*)"|\{\s*['"]([^'"]*)['"]\s*\})/)
      sites.push({ file, line: lineOf(text, m.index), sandbox: tokens(value ? value[1] ?? value[2] : undefined) })
    }
    for (const m of text.matchAll(/createElement\(\s*['"]iframe['"]\s*,\s*\{/g)) {
      const open = m.index + m[0].length - 1
      const props = text.slice(open, endOf(text, open, 'object'))
      const value = props.match(/\bsandbox:\s*['"]([^'"]*)['"]/)
      sites.push({ file, line: lineOf(text, m.index), sandbox: tokens(value?.[1]) })
    }
    for (const m of text.matchAll(/\b(\w+)\s*=\s*(?:\w+\.)?createElement\(\s*['"]iframe['"]\s*\)/g)) {
      // The frame's own statements follow its creation; the next frame created ends them.
      const rest = text.slice(m.index + m[0].length)
      const next = rest.search(/createElement\(\s*['"]iframe['"]/)
      const scope = next === -1 ? rest : rest.slice(0, next)
      const value = scope.match(new RegExp(`\\b${m[1]}\\.setAttribute\\(\\s*['"]sandbox['"]\\s*,\\s*['"]([^'"]*)['"]`))
      sites.push({ file, line: lineOf(text, m.index), sandbox: tokens(value?.[1]) })
    }
  }
  return sites
}

/** The one frame that stays in the dashboard's origin, and why: see `ChatEmbed`. */
const SAME_ORIGIN_FRAME = 'app/appSdk.tsx'

describe('every frame in web/src runs its content in an origin of its own', () => {
  it('finds the frames there are, so the rules below are not vacuous', () => {
    const files = new Set(frameSites().map((s) => s.file))
    for (const f of [
      'pages/artifacts/ArtifactDeploy.tsx', 'ui/widget/WidgetFrame.tsx', 'ui/widget/ReactWidgetFrame.tsx',
      'ui/content/renderers.tsx', 'pages/artifacts/ArtifactCard.tsx', SAME_ORIGIN_FRAME,
    ]) expect(files, `the scan missed the frame in ${f}`).toContain(f)
  })

  it('every frame declares a sandbox — a missing attribute is the widest one there is', () => {
    const bare = frameSites().filter((s) => s.sandbox === null).map((s) => `${s.file}:${s.line}`)
    expect(bare).toEqual([])
  })

  it('every frame but the chat embed is sandboxed to its scripts alone', () => {
    const wider = frameSites()
      .filter((s) => s.file !== SAME_ORIGIN_FRAME && s.sandbox !== null && s.sandbox.join(' ') !== 'allow-scripts')
      .map((s) => `${s.file}:${s.line} sandbox="${s.sandbox?.join(' ')}"`)
    expect(wider).toEqual([])
  })

  it('the deployed page in the Preview pane is one of them', () => {
    const deployed = frameSites().filter((s) => s.file === 'pages/artifacts/ArtifactDeploy.tsx')
    expect(deployed.map((s) => s.sandbox)).toEqual([['allow-scripts']])
  })
})
