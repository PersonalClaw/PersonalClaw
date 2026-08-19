import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── An accessibility prop the type checker cannot see ─────────────────────────────────────────────
//
// `ui/Button` declares its aria surface in camelCase — `ariaLabel`, `ariaExpanded`, `ariaPressed` —
// and spreads no rest props. So a call site written like this:
//
//   <Button aria-expanded={isOpen} aria-label={`Show delivery detail for ${r.label}`}>detail</Button>
//
// compiles cleanly and puts NOTHING in the DOM. **TypeScript does not excess-property-check a JSX
// attribute whose name contains a dash**, because such a name can never be a TS identifier — so the
// one tool that should catch a typo'd prop is structurally blind to this one.
//
// Measured on `#/settings/notifications` before the fix (169 interactive elements, so this is a real
// surface, not a corner):
//
//   27 chevron disclosure triggers with **no `aria-expanded` attribute at all**
//   all 27 sharing the single accessible name **"detail"** — the per-row name was written, and dropped
//
// The intent had been authored correctly and simply never arrived. That is the whole reason this rail
// exists: the defect leaves no trace at the call site, the compiler is silent, and the only witness is
// the rendered tree.
//
// 🪤 TWO WRONG CENSUSES CAME FIRST, AND BOTH OVER-REPORTED — the shape of the matcher is the finding:
//
//  1. Scanning a component's opening tag with a plain regex counted `aria-*` that live INSIDE a brace
//     expression. `ui/Popover` takes a RENDER PROP (`trigger: (open, toggle) => ReactNode`), so
//     `trigger={(open) => <button aria-expanded={open}>}` puts the attribute on the CALLER'S OWN
//     button, where it works perfectly. That mistake alone inflated the count to 44 sites across 7
//     "offending" primitives. A component's own props are the attributes at brace-depth ZERO.
//  2. Every lucide icon (`<Check aria-hidden>`, `<Star aria-label=…>`) forwards natively, so it is not
//     an app component and not a finding. Filtering to components DEFINED in `src/` is what leaves the
//     real set: TWO call sites, one primitive.
//
// So this file asserts the narrow, true thing, and its floors make sure it is still looking.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The attribute text a component tag owns: everything at brace-depth 0 up to its closing `>`.
 *  Skipping brace regions is the whole correctness of this scan — see trap 1 above. */
function ownAttrs(src: string, start: number): string {
  let depth = 0, out = ''
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '{') { depth++; continue }
    if (c === '}') { depth--; continue }
    if (depth === 0) {
      if (c === '>') return out
      out += c
    }
  }
  return out
}

/** Components defined in this tree (an imported icon or motion component is not ours, and forwards
 *  unknown DOM props by construction). */
function appComponents(): Map<string, string> {
  const defs = new Map<string, string>()
  for (const f of walk(SRC)) {
    const src = readFileSync(f, 'utf8')
    for (const m of src.matchAll(/export function ([A-Z]\w*)\s*\(/g)) defs.set(m[1], f.slice(SRC.length + 1))
    for (const m of src.matchAll(/export const ([A-Z]\w*)\s*=\s*(?:memo|forwardRef|\()/g)) defs.set(m[1], f.slice(SRC.length + 1))
  }
  return defs
}

function hyphenatedAriaOnAppComponents() {
  const defs = appComponents()
  const out: { file: string; comp: string; arias: string[]; def: string }[] = []
  for (const f of walk(SRC)) {
    const src = strip(readFileSync(f, 'utf8'))
    for (const m of src.matchAll(/<([A-Z][\w.]*)\b/g)) {
      const def = defs.get(m[1])
      if (!def) continue                                   // not ours → forwards natively
      const arias = [...ownAttrs(src, m.index! + m[0].length).matchAll(/\s(aria-[a-z]+)=/g)].map((x) => x[1])
      if (!arias.length) continue
      const target = readFileSync(join(SRC, def), 'utf8')
      // A component that spreads rest props, or declares the hyphenated name itself, forwards it.
      if (/\.\.\.(rest|props)\b/.test(target)) continue
      if (arias.some((a) => new RegExp(`['"\`]${a}['"\`]\\s*[:?]|\\[['"]${a}['"]\\]`).test(target))) continue
      out.push({ file: f.slice(SRC.length + 1), comp: m[1], arias, def })
    }
  }
  return out
}

describe('a hyphenated aria prop on one of our components is dropped, so nobody writes one', () => {
  it('no call site passes aria-* to an app component that cannot forward it', () => {
    const dropped = hyphenatedAriaOnAppComponents()
      .map((h) => `${h.file}  <${h.comp} ${h.arias.join(' ')}>  → ${h.def} declares camelCase only`)
    expect(dropped, `these compile and reach nothing:\n${dropped.join('\n')}\n\nUse the component's own camelCase prop (ariaLabel / ariaExpanded / ariaPressed).`)
      .toEqual([])
  })

  it('the scan is still looking — floors on both halves', () => {
    // Vacuity is the live risk: this matcher walks every tsx, resolves component definitions, and
    // brace-scans attributes. Any one of those silently returning nothing looks like a clean tree.
    const files = walk(SRC)
    expect(files.length, 'the tsx walk must resolve').toBeGreaterThan(200)
    const defs = appComponents()
    expect(defs.size, 'app components must resolve').toBeGreaterThan(150)
    expect(defs.has('Button'), 'ui/Button is the primitive this rail was written about').toBe(true)
    // And the brace-skipping must actually skip: Popover's render-prop callers hold `aria-expanded`
    // inside a brace expression, which this scan must NOT attribute to <Popover>.
    const filterMenu = strip(readFileSync(join(SRC, 'ui/FilterMenu.tsx'), 'utf8'))
    const at = filterMenu.indexOf('<Popover')
    expect(at, 'FilterMenu must still render a Popover').toBeGreaterThan(-1)
    expect(filterMenu.slice(at), 'and still put aria-expanded inside the trigger callback').toMatch(/aria-expanded=/)
    expect(ownAttrs(filterMenu, at + '<Popover'.length), 'which is NOT one of Popover\'s own attributes')
      .not.toMatch(/aria-/)
  })

  it('Button still declares the camelCase surface the fix depends on', () => {
    // If Button ever starts spreading rest props, the rail above stops flagging its callers — which
    // would be correct, but only if that is deliberate. Pin what is true today.
    const src = readFileSync(join(SRC, 'ui/Button.tsx'), 'utf8')
    for (const prop of ['ariaLabel', 'ariaExpanded', 'ariaPressed']) expect(src).toContain(prop)
    expect(src, 'and it renders them onto the button').toMatch(/aria-label=\{ariaLabel\}/)
    expect(src, 'no rest spread — the reason a hyphenated prop vanishes').not.toMatch(/\.\.\.(rest|props)\b/)
  })

  it('the two fixed call sites use the camelCase props', () => {
    const matrix = readFileSync(join(SRC, 'pages/settings/NotificationRulesMatrix.tsx'), 'utf8')
    expect(matrix).toMatch(/ariaExpanded=\{isOpen\}/)
    expect(matrix).toMatch(/ariaLabel=\{`\$\{isOpen \? 'Hide' : 'Show'\} delivery detail for \$\{r\.label\}`\}/)
    const audit = readFileSync(join(SRC, 'pages/settings/AuditPanel.tsx'), 'utf8')
    expect(audit).toMatch(/ariaExpanded=\{showMore\}/)
  })
})
