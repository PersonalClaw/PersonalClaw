import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── A plain-text sink shows its backticks ─────────────────────────────────────────────────────────
//
// A settings hint, a switch's disabled reason and a companion note are plain text: whatever string
// they are handed is printed as it is. Nine of them were written the way markdown writes a command —
// "run `personalclaw push init` on the gateway" — so the backticks reached the screen as backticks,
// in the Companion, Durability, External access, Account and Memory panels and on the companion page.
// Each now writes the command as a `<code>` element (the sinks take a ReactNode, as `Section`'s hint
// always has), and a tooltip, which cannot hold an element, names it without the markup.
//
// This rail scans for the shape rather than the nine sentences, so a tenth cannot land: a quoted
// string literal (a JSX attribute's "…", or a '…' literal) carrying a backtick-delimited span. A
// template literal is not a string that can carry one, and comments are blanked first.

const SRC = join(import.meta.dirname, '..')

/** The two files whose backticked strings ARE markdown, read by a markdown renderer. */
const MARKDOWN_SOURCES: Record<string, string> = {
  'pages/ChatPage.tsx': 'the slash-command help, posted as a chat message the transcript renders as markdown',
  'pages/loops/DesignCockpitPage.tsx': 'the design brief the page exports as a markdown document',
}

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) sources(abs, out)
    else if (entry.name.endsWith('.tsx') && !/\.(test|doc)\.tsx$/.test(entry.name)) out.push(abs)
  }
  return out
}

/** Comments blanked in place, so a line number still points at its line. */
const code = (src: string): string =>
  src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|\s)\/\/.*$/gm, '$1')

const ATTRIBUTE = /\b[\w-]+="([^"\n]*`[^`"\n]+`[^"\n]*)"/g
const LITERAL = /'([^'\n]*`[^`'\n]+`[^'\n]*)'/g

/** Every quoted string in `src` that carries a backtick-delimited span, as `line: text`. */
function codeSpansInPlainStrings(src: string): string[] {
  const hits: string[] = []
  code(src).split('\n').forEach((line, i) => {
    for (const m of line.matchAll(ATTRIBUTE)) hits.push(`${i + 1}: ${m[0]}`)
    for (const m of line.matchAll(LITERAL)) {
      // A `${…}` means the quotes belong to two literals around a template, not to one string.
      if (m[1].includes('${')) continue
      // An odd count of quotes before the match makes its opening quote a CLOSING one: the span
      // then sits between two literals, not inside either.
      if ((line.slice(0, m.index).match(/'/g) ?? []).length % 2) continue
      hits.push(`${i + 1}: ${m[0]}`)
    }
  })
  return hits
}

describe('the scan recognises the shape it guards against', () => {
  it('a hint attribute and a quoted literal that carry a command in backticks', () => {
    expect(codeSpansInPlainStrings('<Row hint="run `personalclaw push init` first">')).toHaveLength(1)
    expect(codeSpansInPlainStrings("setNote('run `personalclaw push init`.')")).toHaveLength(1)
  })

  it('and not what cannot show backticks: templates, comments, and code as an element', () => {
    expect(codeSpansInPlainStrings('notify(`Saved ${n} items`, \'info\')')).toEqual([])
    expect(codeSpansInPlainStrings("const a = x ? 'one' : `two ${y}` + 'three'")).toEqual([])
    expect(codeSpansInPlainStrings('// run `personalclaw push init` first')).toEqual([])
    expect(codeSpansInPlainStrings('<Row hint={<>run <code>personalclaw push init</code></>}>')).toEqual([])
  })
})

describe('no plain-text string shows a markdown code span', () => {
  const files = sources(SRC)

  it('reads the real tree (vacuity floor)', () => {
    expect(files.length).toBeGreaterThan(200)
    for (const rel of Object.keys(MARKDOWN_SOURCES)) {
      expect(files.some((f) => relative(SRC, f) === rel), `${rel} must still exist`).toBe(true)
    }
  })

  it('leaves none outside the markdown sources', () => {
    const found = files
      .filter((abs) => !(relative(SRC, abs) in MARKDOWN_SOURCES))
      .flatMap((abs) => codeSpansInPlainStrings(readFileSync(abs, 'utf8')).map((h) => `${relative(SRC, abs)}:${h}`))
    expect(found, 'write the command as a <code> element, or without the markup where the sink is a tooltip').toEqual([])
  })
})
