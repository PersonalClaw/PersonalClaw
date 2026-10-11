// @module-tag tree-scan
//
// ── JSX text is not Markdown. Anything backticked in JSX text renders the backticks LITERALLY ───
//
// Markdown lets a writer say "render `config.json` as code"; JSX treats the same backticks as
// three ordinary characters (open-backtick, word, close-backtick) and prints all five to the
// page. That is the bug at `pages/settings/ExternalAccessPanel.tsx:319`, where the Limits note
// reads "…they are deliberately a `config.json` edit." with the backticks visible to a reader
// and the word unstyled — a Markdown impostor in a TSX file.
//
// The other JSX copy on that page already does the right thing (`<code>config.json</code>`),
// so the test below is a rail: every `.tsx` under `web/src` is parsed with the TypeScript
// compiler API (the same pattern `web/scripts/extractUiProps.mjs` uses), every `JsxText` node
// and every string-literal JSX attribute is walked, and any paired-backtick span in
// JSX-text/attribute position is a failure with the file:line attached. Comments are
// `SyntaxKind.SingleLineCommentTrivia` / `MultiLineCommentTrivia` and NOT `JsxText`, so they
// cannot match — a comment that says `// "use \`code\` here"` does not count.
//
// A single backtick (unpaired) is NOT enough to match. The bug is the paired span: `` `word` ``,
// which is the pattern the Markdown convention uses and the one that prints to the reader as
// `` `word` `` instead of as code. `` ` alone is allowed; it appears in template literals,
// keyboard shortcuts (`⌘\`` in TerminalDrawer), and prose like "use the back-tick character".
//
// A positive control: a backticked span inside a synthetic fixture MUST be caught, so an empty
// scan cannot pass vacuously (a test that finds nothing would otherwise be indistinguishable
// from one whose walker was wrong). The fixtures are parsed from strings; nothing is written.

import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../test/sourceTree'

interface Finding { file: string; line: number; text: string; kind: string }

// Vitest is invoked with `web/` as the working directory (the repo's `test:web` script runs
// `npm run test --workspace=web`, and `web/vitest.config.ts` resolves `include` paths against
// it), so `process.cwd()` is the `web/` dir itself and the source tree sits at `<cwd>/src`.
const SRC = join(process.cwd(), 'src')

// The tree is listed and read through the shared walker and reader (`src/test/sourceTree.ts`):
// one listing and one read per file in this test file's worker, inside the tree-scan budget.
const tsxFiles = (): string[] => filesUnder(SRC, (name) => name.endsWith('.tsx'))

/** Find every paired-backtick span sitting inside JSX text or a string-literal JSX attribute.
 *  `text` defaults to the file's source; a synthetic fixture passes its own. */
function backtickedSpansIn(file: string, text: string = readSource(file)): Finding[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX)
  const out: Finding[] = []

  const visit = (node: ts.Node): void => {
    // JsxText: text between tags, including whitespace. A pure-whitespace JsxText carries no
    // prose, so an empty match there cannot happen and we still scan it (a literal backtick
    // sandwiched between tags with no siblings would be caught here too).
    if (ts.isJsxText(node)) {
      const raw = node.getText()
      const m = /`[^`\n]+`/.exec(raw)
      if (m) {
        const start = node.getStart(sf) + raw.indexOf(m[0])
        const pos = sf.getLineAndCharacterOfPosition(start)
        out.push({ file, line: pos.line + 1, text: m[0], kind: 'JsxText' })
      }
    }
    // String-literal JSX attribute: `<X label="…">`. A backticked span inside the literal would
    // also render as text (attributes are strings, not Markdown), so it is in scope.
    if (ts.isJsxAttribute(node)) {
      const init = node.initializer
      if (init && ts.isStringLiteral(init)) {
        const v = init.text
        const m = /`[^`\n]+`/.exec(v)
        if (m) {
          const start = init.getStart(sf)
          const pos = sf.getLineAndCharacterOfPosition(start)
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
    // `` ` `` alone appears in template literals, prose like "use the back-tick character",
    // and keyboard labels. The rule catches paired spans, not lone characters.
    const source = `export function X() { return <p>use the back-tick \` char</p> }\n`
    expect(backtickedSpansIn('lone.tsx', source)).toEqual([])
  })

  it('a paired backtick span in JSX text IS a finding', () => {
    // Positive control: a synthetic fixture carrying the same shape of bug as the panel
    // under test. The fixture MUST be caught so an empty scan cannot pass vacuously.
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
    // The bug is a backticked span the reader sees; a comment never is, so it must not match.
    // `// a \`b\` span` and `/* a \`b\` span */` both parse as `CommentRange`, not `JsxText`,
    // and the walker does not visit them — the assertion is structural (no JsxText to match).
    const source = [
      `// a \`comment-only\` backtick span`,
      `/* another \`comment\` span, multi-line */`,
      `export function X() { return <p>plain copy without ts</p> }`,
      ``,
    ].join('\n')
    expect(backtickedSpansIn('comment.tsx', source)).toEqual([])
  })

  it('a <code>config.json</code> JSX element is NOT a finding (the rendered copy is correct)', () => {
    // The fix at ExternalAccessPanel.tsx:319 is to swap `` `config.json` `` for `<code>config.json</code>`.
    // Once swapped, the JsxText carries no backticks and the attribute values carry no backticks.
    const source = `export function X() { return <p>editable in <code>config.json</code> only</p> }\n`
    expect(backtickedSpansIn('fixed.tsx', source)).toEqual([])
  })

  it('catches the production bug at pages/settings/ExternalAccessPanel.tsx (the real-world target)', () => {
    // The pre-fix panel had one finding under the Limits section. After the fix the count is 0.
    // We assert the LOC by line: the bug lives at line 319 (JsxText, `\`config.json\``). A future
    // regression that re-introduces a paired span anywhere in the same file would be caught too,
    // and the test still names the panel so the failure is debuggable.
    const panel = tsxFiles().find((f) => f.endsWith(join('pages', 'settings', 'ExternalAccessPanel.tsx')))
    expect(panel, 'the panel must exist in the tree').toBeTruthy()
    const findings = backtickedSpansIn(panel!)
    // After the fix this MUST be empty. While the bug is unfixed, this test fails noisily with
    // the exact line — which is the property this rail exists to provide.
    expect(findings, JSON.stringify(findings, null, 2)).toEqual([])
  })

  it('the whole production tree contains no paired backtick spans in JSX copy', () => {
    // End-to-end rail: every .tsx under web/src. A paired-backtick span in any JsxText or
    // string-literal JSX attribute anywhere in the tree is a failure. The positive control
    // legs above prove the walker is not silently empty; this leg is the gate.
    const all: Finding[] = []
    for (const file of tsxFiles()) {
      all.push(...backtickedSpansIn(file))
    }
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
