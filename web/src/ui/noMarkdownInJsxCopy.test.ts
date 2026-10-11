// @module-tag tree-scan
//
// ── JSX text is not Markdown. Anything backticked in JSX text renders the backticks LITERALLY ───
//
// Markdown lets a writer say "render `config.json` as code"; JSX treats the same backticks as
// ordinary characters and prints them. The Limits note in Settings › External access read
// "…they are deliberately a `config.json` edit." with the backticks on screen and the name
// unstyled, while the Remote access note on the same page already used `<code>config.json</code>`.
//
// So this rail parses every production `.tsx` under `web/src` with the TypeScript compiler API
// (the pattern `web/scripts/extractUiProps.mjs` uses), walks every `JsxText` node and every
// string-literal JSX attribute, and fails on a paired-backtick span in either, naming the file
// and line. Comments are trivia, not `JsxText`, so a backticked span in a comment cannot match.
//
// A single unpaired backtick is NOT a finding: it appears in keyboard labels and prose about the
// character itself. The bug is the paired span, the Markdown code convention.
//
// Positive controls: synthetic fixtures carrying the bug MUST be caught, so a walker that found
// nothing cannot pass vacuously. The fixtures are parsed from strings; nothing is written.

import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../test/sourceTree'

interface Finding { file: string; line: number; text: string; kind: string }

// Vitest runs with `web/` as its working directory (the root `test:web` script runs the `web`
// workspace's own `vitest run`), so the source tree is `<cwd>/src`.
const SRC = join(process.cwd(), 'src')

// Production components only: a test or doc file may render a backticked string on purpose.
// Listed and read through the shared walker and reader (`src/test/sourceTree.ts`), inside the
// tree-scan budget.
const productionTsx = (): string[] =>
  filesUnder(SRC, (name) => name.endsWith('.tsx') && !/\.(test|doc)\.tsx$/.test(name))

/** Every paired-backtick span inside JSX text or a string-literal JSX attribute.
 *  `text` defaults to the file's source; a synthetic fixture passes its own. */
function backtickedSpansIn(file: string, text: string = readSource(file)): Finding[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX)
  const out: Finding[] = []

  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node)) {
      const raw = node.getText()
      const m = /`[^`\n]+`/.exec(raw)
      if (m) {
        const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf) + raw.indexOf(m[0]))
        out.push({ file, line: pos.line + 1, text: m[0], kind: 'JsxText' })
      }
    }
    // `<X label="…">`: an attribute is a string, not Markdown, so a backticked span in one
    // renders literally too.
    if (ts.isJsxAttribute(node)) {
      const init = node.initializer
      if (init && ts.isStringLiteral(init)) {
        const m = /`[^`\n]+`/.exec(init.text)
        if (m) {
          const pos = sf.getLineAndCharacterOfPosition(init.getStart(sf))
          out.push({ file, line: pos.line + 1, text: m[0], kind: `JsxAttr(${node.name.getText(sf)})` })
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

describe('no Markdown backticks leak into JSX copy', () => {
  it('a single bare backtick is not a finding (unpaired is harmless)', () => {
    const source = `export function X() { return <p>use the back-tick \` char</p> }\n`
    expect(backtickedSpansIn('lone.tsx', source)).toEqual([])
  })

  it('a paired backtick span in JSX text IS a finding', () => {
    const source = `export function X() { return <p>this is a \`literal\` backtick span</p> }\n`
    const findings = backtickedSpansIn('paired.tsx', source)
    expect(findings).toHaveLength(1)
    expect(findings[0].kind).toBe('JsxText')
    expect(findings[0].text).toBe('`literal`')
    expect(findings[0].file).toBe('paired.tsx')
  })

  it('a paired backtick span in a string-literal JSX attribute is also a finding', () => {
    const source = `export function X() { return <p title="not editable: \`config.json\`">x</p> }\n`
    const findings = backtickedSpansIn('attr.tsx', source)
    expect(findings).toHaveLength(1)
    expect(findings[0].kind).toBe('JsxAttr(title)')
    expect(findings[0].text).toBe('`config.json`')
  })

  it('a backticked span inside a TS comment is NOT a finding (comments are not JsxText)', () => {
    const source = [
      `// a \`comment-only\` backtick span`,
      `/* another \`comment\` span, multi-line */`,
      `export function X() { return <p>plain copy without ts</p> }`,
      ``,
    ].join('\n')
    expect(backtickedSpansIn('comment.tsx', source)).toEqual([])
  })

  it('a <code>config.json</code> JSX element is NOT a finding (the rendered copy is correct)', () => {
    const source = `export function X() { return <p>editable in <code>config.json</code> only</p> }\n`
    expect(backtickedSpansIn('fixed.tsx', source)).toEqual([])
  })

  it('the whole production tree contains no paired backtick spans in JSX copy', () => {
    const files = productionTsx()
    // The walk must reach the tree, and the panel this rail was written for must be in it.
    expect(files.length, 'the scan must find the components').toBeGreaterThan(200)
    expect(files.some((f) => f.endsWith(join('pages', 'settings', 'ExternalAccessPanel.tsx')))).toBe(true)
    const all = files.flatMap((file) => backtickedSpansIn(file))
    expect(
      all,
      all.length
        ? `paired backtick spans found in JSX copy:\n${all
            .map((f) => `  ${f.file.replace(process.cwd() + '/', '')}:${f.line}  [${f.kind}]  ${f.text}`)
            .join('\n')}`
        : '',
    ).toEqual([])
  })
})
