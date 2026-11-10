// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../test/sourceTree'

// ── A scroll box that holds a rendered body gives the body's edge room ───────────────────────────
//
// A rendered body (`<Markdown>`, block mode) clips to its own box, and where the browser reads no
// clip margin (Safari) it takes a 4px padding inside a -4px margin for a focus ring's room. In a scroll
// box with no padding of its own, that pair is 4px of scroll on both axes even for one line: a stray
// scrollbar, and Chromium makes the box a tab stop named by its whole content. So every scroll box
// whose nearest body is a block `<Markdown>` has a padding of its own on both axes, 4px or more at
// every font size and density. A component that draws its text as one (`BODIES`) counts as one. The
// scan reads multi-line JSX, a body under a condition or in a list's map, and classes built from a
// `const` in the same file.

const SRC = join(process.cwd(), 'src')
/** The components a body is drawn by: `Markdown`, and those that draw their text through a bare
 *  block `Markdown` (`ToolOutput`, for output that reads as markdown; `InboxMessageBody`, for the
 *  owner's own message; `PlanningArtifactDoc`). */
const BODIES = new Set(['Markdown', 'ToolOutput', 'InboxMessageBody', 'PlanningArtifactDoc'])
const SCROLLS = /^overflow(-[xy])?-(auto|scroll)$/
/** The spacing tokens that give 4px or more at every density (`xs` is 4px times the density, under 4px
 *  below the default). */
const ROOMY_TOKENS = new Set(['s', 'm', 'l', 'xl', '2xl', '3xl'])
/** A padding class that gives at least 4px on `axis` at every font size and density: a body takes its
 *  room in px (its 4px clip margin, or a 4px padding inside a -4px margin), so a step in rem gives it
 *  only from 1.5 up (6px at 100%, 5.1px at the smallest font size), and a bracketed value from 4px. */
const pads = (cls: string, axis: 'x' | 'y') => {
  const m = /^p([xy])?-(.+)$/.exec(cls)
  if (!m || (m[1] && m[1] !== axis)) return false
  const v = m[2]
  const px = /^\[(\d+(?:\.\d+)?)px\]$/.exec(v)
  if (px) return parseFloat(px[1]) >= 4
  if (/^\d+(?:\.\d+)?$/.test(v)) return parseFloat(v) >= 1.5
  return ROOMY_TOKENS.has(v)
}

/** The class words an expression can hold, through literals, templates, conditions, sums, calls and
 *  the `const`s of its file. */
function classesOf(node: ts.Node | undefined, consts: Map<string, ts.Expression>, seen = new Set<string>()): string[] {
  if (!node) return []
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text.split(/\s+/).filter(Boolean)
  if (ts.isTemplateExpression(node)) {
    return [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].flatMap((t) => t.split(/\s+/).filter(Boolean))
      .concat(node.templateSpans.flatMap((s) => classesOf(s.expression, consts, seen)))
  }
  if (ts.isIdentifier(node)) {
    if (seen.has(node.text)) return []
    seen.add(node.text)
    return classesOf(consts.get(node.text), consts, seen)
  }
  if (ts.isJsxExpression(node)) return classesOf(node.expression, consts, seen)
  const out: string[] = []
  node.forEachChild((kid) => { out.push(...classesOf(kid, consts, seen)) })
  return out
}

/** Every block `<Markdown>` whose nearest scroll box lacks a padding of its own on both axes, 4px or more at every font size and density. */
function bodiesWithNoRoom(path: string, text: string): { found: string[]; hosts: number } {
  const file = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const consts = new Map<string, ts.Expression>()
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) consts.set(node.name.text, node.initializer)
    node.forEachChild(visit)
  }
  visit(file)
  const found: string[] = []
  let hosts = 0
  const classAttr = (el: ts.JsxOpeningLikeElement) =>
    el.attributes.properties.find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === 'className')
  const check = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && BODIES.has(node.tagName.getText())) {
      const attrs = node.attributes.properties.map((p) => (ts.isJsxAttribute(p) ? p.name.getText() : ''))
      if (!attrs.includes('inline') && !attrs.includes('line')) {
        // Up to the component's own function; a function a list's map calls is still inside its box.
        const inBox = (up: ts.Node) => !ts.isFunctionLike(up) || ((ts.isArrowFunction(up) || ts.isFunctionExpression(up)) && ts.isCallExpression(up.parent))
        for (let up: ts.Node | undefined = node.parent; up && inBox(up); up = up.parent) {
          const opening = ts.isJsxElement(up) ? up.openingElement : undefined
          if (!opening || opening === node) continue
          const attr = classAttr(opening)
          const classes = attr ? classesOf(attr.initializer, consts) : []
          if (!classes.some((c) => SCROLLS.test(c))) continue
          hosts++
          if (!(classes.some((c) => pads(c, 'x')) && classes.some((c) => pads(c, 'y')))) {
            found.push(`${relative(SRC, path)}:${file.getLineAndCharacterOfPosition(opening.getStart()).line + 1}`)
          }
          break
        }
      }
    }
    node.forEachChild(check)
  }
  check(file)
  return { found, hosts }
}

describe('a scroll box that holds a rendered body gives its edge room', () => {
  const files = filesUnder(SRC, (name) => name.endsWith('.tsx') && !name.includes('.test.'))

  it('every such box has a padding of its own on both axes, 4px or more at every font size and density', () => {
    let hosts = 0
    const found = files.flatMap((path) => {
      const r = bodiesWithNoRoom(path, readSource(path))
      hosts += r.hosts
      return r.found
    })
    expect(found).toEqual([])
    // The scan is not vacuous: it finds the boxes this was written for.
    expect(hosts, 'scroll boxes holding a body').toBeGreaterThanOrEqual(21)
  })

  it('plant: a box with no padding, or too little, is caught, through a condition, a const, a map and a body component', () => {
    const planted = [
      'const frame = "max-h-40 overflow-y-auto"',
      'export function A({ x }: { x: boolean }) {',
      '  return <div className={`${frame} text-sm`}>',
      '    {x ? (',
      '      <Markdown>{"a"}</Markdown>',
      '    ) : null}',
      '  </div>',
      '}',
      'export function B() { return <div className="max-h-40 overflow-auto px-m py-[4px]"><Markdown>{"b"}</Markdown></div> }',
      'export function C() { return <div className="max-h-40 overflow-auto"><Markdown inline>{"c"}</Markdown></div> }',
      'export function D({ xs }: { xs: string[] }) { return <div className="max-h-40 overflow-auto">{xs.map((x) => <Markdown key={x}>{x}</Markdown>)}</div> }',
      'export function E() { return <div className="max-h-96 overflow-y-auto"><ToolOutput text="e" /></div> }',
      // Room in rem, or under 4px: short of the body's 4px below the default font size or density.
      'export function F() { return <div className="max-h-40 overflow-auto p-1"><Markdown>{"f"}</Markdown></div> }',
      'export function G() { return <div className="max-h-40 overflow-auto p-xs"><Markdown>{"g"}</Markdown></div> }',
      'export function H() { return <div className="max-h-40 overflow-auto p-[2px]"><Markdown>{"h"}</Markdown></div> }',
    ].join('\n')
    expect(bodiesWithNoRoom(join(SRC, 'planted.tsx'), planted).found).toEqual(['planted.tsx:3', 'planted.tsx:11', 'planted.tsx:12', 'planted.tsx:13', 'planted.tsx:14', 'planted.tsx:15'])
  })
})
