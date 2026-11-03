// @module-tag tree-scan
//
// ── Copy that sends someone to Settings names a page that is there (#5897) ─────────────────────
//
// Settings' forty pages folded into seventeen, and the links moved with them, but words did not:
// "Open Settings → Doctor" opened Health, and "clear incident mode in Settings → Guardrails" named
// a page that had become a section of Security. A person reading the sentence looks for a page
// that the list no longer has.
//
// So every place the app's copy writes "Settings → …" (or "›") names a page by its label, and a
// section, when it names one, by its heading on that page: "Settings → Security → Guardrails".
// This rail parses every production `.ts` and `.tsx` under `web/src` with the TypeScript compiler
// API, and reads string literals, template text and JSX text.
// Never comments: they are trivia and keep the names they had (their rewrite is its own item).
// `tests/rails/test_settings_names_in_text_are_pages_tree.py` holds the gateway's and the CLI's
// strings to the same names.
//
// A file is parsed only when its text holds an arrow after "Settings", so the scan reads the few
// files that can hold a finding. Planted fixtures below must be caught, and the scan must find at
// least as many names as the tree holds today, so a walker that reads nothing cannot pass.

import { describe, expect, it } from 'vitest'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../../test/sourceTree'
import { SETTINGS_PAGES } from './settingsPages'

const { SECTIONS } = await import('./SettingsPage')

// Vitest runs with `web/` as its working directory, so the source tree is `<cwd>/src`. The desktop
// app's files are its own job's to read, not this one's (`tools/ci/catalogue.toml`'s inputs).
const SRC = join(process.cwd(), 'src')
const REPO = join(process.cwd(), '..')

const ARROW = /Settings ?[→›] ?/g

/** Each page's label, with the headings of the sections it draws. */
const PAGES: { label: string; sections: string[] }[] = SETTINGS_PAGES.map((p) => ({
  label: p.label,
  sections: p.sections.map((id) => SECTIONS.find((s) => s.id === id)?.label ?? `(no section ${id})`),
}))

const longestFirst = (xs: string[]) => [...xs].sort((a, b) => b.length - a.length)

/** Whether `rest` ends a name where it stops: at its end, or at anything but a letter or digit. */
const ends = (rest: string) => rest === '' || !/^[\p{L}\p{N}]/u.test(rest)

/** What follows "Settings →" in `text` at `at`, read as a page and maybe a section of it: null when
 *  it is one, else the words it names. */
function wrongName(text: string, at: number): string | null {
  const rest = text.slice(at)
  for (const page of longestFirst(PAGES.map((p) => p.label))) {
    if (!rest.startsWith(page) || !ends(rest.slice(page.length))) continue
    const after = rest.slice(page.length)
    const arrow = /^ ?[→›] ?/.exec(after)
    if (!arrow) return null
    const named = after.slice(arrow[0].length)
    if (startsWithName(named, PAGES.find((p) => p.label === page)!.sections)) return null
    // A deeper name that is no section's is a heading on the page, which these tables do not hold:
    // "Settings → Usage → Model prices". One that is another page's section is misfiled.
    const others = PAGES.filter((p) => p.label !== page).flatMap((p) => p.sections)
    return startsWithName(named, others) ? `${page} → ${named.slice(0, 40)}` : null
  }
  return rest.slice(0, 40) || '(nothing: the name is filled in at run time)'
}

/** Whether `text` starts with one of `names`, the whole name. */
const startsWithName = (text: string, names: string[]) => names.some((n) => text.startsWith(n) && ends(text.slice(n.length)))

/** The operating system's own Settings, which a sentence about the microphone or reduced motion
 *  names: "System Settings" on a Mac and the Settings app on Windows. */
const OS_SETTINGS = /(\bSystem |\bWindows,? )$/

interface Named { file: string; line: number; wrong: string | null }

/** Every "Settings →" in the string literals, template text and JSX text of `text`. */
function namesIn(file: string, text: string = readSource(file)): Named[] {
  const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ESNext, true, kind)
  const out: Named[] = []
  const read = (node: ts.Node, value: string) => {
    for (const m of value.matchAll(ARROW)) {
      if (OS_SETTINGS.test(value.slice(0, m.index))) continue
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
      out.push({ file, line, wrong: wrongName(value, m.index! + m[0].length) })
    }
  }
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) read(node, node.text)
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) read(node, node.text)
    else if (ts.isJsxText(node)) read(node, node.text.replace(/\s+/g, ' '))
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

const production = (): string[] =>
  filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.(test|doc)\.tsx?$/.test(name) && !name.endsWith('.d.ts'))

describe('a Settings name in copy is a page, or a section on its page', () => {
  it('reads a page, and a page with one of its sections, as right', () => {
    expect(namesIn('a.tsx', `const a = 'Open Settings → Health to run it.'\n`)).toEqual([{ file: 'a.tsx', line: 1, wrong: null }])
    expect(namesIn('b.tsx', `export const X = () => <p>Clear it in Settings › Security › Guardrails.</p>\n`))
      .toEqual([{ file: 'b.tsx', line: 1, wrong: null }])
    expect(namesIn('c.ts', 'const c = `Settings → Devices & Access → Companion apps`\n')).toEqual([{ file: 'c.ts', line: 1, wrong: null }])
    // A heading on the page, deeper than the tables go, and the operating system's own Settings.
    expect(namesIn('e.ts', `const e = 'Settings → Usage → Model prices'\n`)).toEqual([{ file: 'e.ts', line: 1, wrong: null }])
    expect(namesIn('g.ts', `const g = 'Settings › Chat › Agent routing'\n`)).toEqual([{ file: 'g.ts', line: 1, wrong: null }])
    expect(namesIn('f.ts', `const f = 'Turn it on in System Settings › Privacy & Security'\n`)).toEqual([])
  })

  it('🪤 catches a retired page, a section on the wrong page, and a page name that runs on', () => {
    const wrong = (src: string, file = 'x.tsx') => namesIn(file, src).map((n) => n.wrong)
    expect(wrong(`const a = 'Open Settings → Doctor'\n`)).toEqual(['Doctor'])
    expect(wrong(`const a = <p>in Settings › Guardrails.</p>\n`)).toEqual(['Guardrails.'])
    expect(wrong(`const a = 'Settings → Security → Doctor'\n`)).toEqual(['Security → Doctor'])
    expect(wrong(`const a = 'Settings → Healthy'\n`)).toEqual(['Healthy'])
    expect(wrong('const a = `Settings → ${page}`\n', 'x.ts')).toEqual(['(nothing: the name is filled in at run time)'])
  })

  it('leaves comments alone: they are not copy', () => {
    expect(namesIn('d.ts', `// Settings → Doctor\n/* Settings › Guardrails */\nconst d = 1\n`)).toEqual([])
  })

  it('🔑 every Settings name in the app\'s copy is a page, or a section on its page', () => {
    const named = production()
      .filter((file) => /Settings ?[→›]/.test(readSource(file)))
      .flatMap((file) => namesIn(file))
    // Vacuity floor: the copy names a Settings page in 82 places today.
    expect(named.length).toBeGreaterThanOrEqual(82)
    const wrong = named.filter((n) => n.wrong !== null).map((n) => `${relative(REPO, n.file)}:${n.line}  Settings → ${n.wrong}`)
    expect(wrong, 'copy naming a Settings page that is not there').toEqual([])
  })
})
