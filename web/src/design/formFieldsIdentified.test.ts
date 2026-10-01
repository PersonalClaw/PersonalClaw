import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── The shared field primitives never render a field the browser cannot tell apart ─────────────────
//
// A form field with neither an id nor a name is one the browser cannot tell from the one beside it:
// it raises "A form field element should have an id or name attribute" and cannot autofill it. First
// run's two identity pills were exactly that, and so were sixteen fields in `ui/` — the number
// stepper, the checkbox, the combobox's filter, the prompt dialog's field (whose visible label was
// tied to nothing), the plan editors, the sheet cells, the token editors — and the settings rows'
// shared select and list field, which put a flagged field on every Settings page. They are the
// components every page builds on, so this rail holds them at zero: every `<input>`, `<textarea>` and
// `<select>` they render declares an `id` or a `name` (or spreads props that do).
//
// Read from source because the property is about what each component WRITES, and it has to hold for
// every branch, not only the ones a render happens to reach. Comments are blanked first, so prose
// that mentions `<input>` is not counted; `type="file"` and `type="hidden"` are out, as they are
// never autofilled. Pages still carry raw fields of their own; they are not part of this rail.

const SRC = join(process.cwd(), 'src')
const ROOTS = ['ui', 'app']
/** The settings pages' shared rows, which live beside the pages rather than in `ui/`. */
const SHARED = ['pages/settings/bento.tsx', 'pages/settings/settingsUI.tsx']

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return files(p)
    return /\.tsx$/.test(name) && !/\.(test|doc)\./.test(name) ? [p] : []
  })
}

/** Blank block and line comments, keeping offsets (and so line numbers) intact. */
function stripComments(s: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, ' ')
  return s.replace(/\/\*[\s\S]*?\*\//g, blank).replace(/(?<![:"'`])\/\/[^\n]*/g, blank)
}

/** Every field tag in a source file: its line and its full text (to the `>` that closes it). */
function fieldTags(source: string): { line: number; tag: string }[] {
  const s = stripComments(source)
  const out: { line: number; tag: string }[] = []
  for (const m of s.matchAll(/<(input|textarea|select)\b/g)) {
    let i = m.index! + m[0].length
    let depth = 0
    for (; i < s.length; i++) {
      const c = s[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) break
    }
    out.push({ line: s.slice(0, m.index).split('\n').length, tag: s.slice(m.index, i + 1) })
  }
  return out
}

const identified = (tag: string) => /\b(id|name)=/.test(tag) || tag.includes('{...')
const exempt = (tag: string) => /type="(file|hidden)"/.test(tag)

describe('every field in the shared components can be told apart', () => {
  const all = [...ROOTS.flatMap((r) => files(join(SRC, r))), ...SHARED.map((f) => join(SRC, f))].flatMap((p) =>
    fieldTags(readFileSync(p, 'utf8')).map((t) => ({ ...t, at: `${relative(SRC, p)}:${t.line}` })))

  it('scans the fields it claims to (vacuity floor)', () => {
    // 28 when this was written, seven of them in `ui/forms`; the floor fails loudly if the walk stops
    // finding them.
    expect(all.length).toBeGreaterThan(20)
    expect(all.filter((t) => t.at.startsWith('ui/forms.tsx')).length).toBeGreaterThanOrEqual(7)
    expect(all.some((t) => t.at.startsWith('ui/forms.tsx'))).toBe(true)
    expect(all.some((t) => t.at.startsWith('app/Onboarding.tsx'))).toBe(true)
    for (const f of SHARED) expect(all.some((t) => t.at.startsWith(f)), f).toBe(true)
  })

  it('🔴 declares an id or a name on every one', () => {
    const bare = all.filter((t) => !exempt(t.tag) && !identified(t.tag)).map((t) => t.at)
    expect(bare, 'a field with neither an id nor a name: give it a `useId()` id').toEqual([])
  })

  it('the scan recognises the bare shape it exists to catch', () => {
    // The matcher, not the tree: an aria-label-only field must read as bare, and a commented one not
    // at all, or every assertion above would pass on the defect.
    const [bare] = fieldTags('<input value={v} aria-label="Your name" onChange={(e) => f(e.target.value)} />')
    expect(identified(bare.tag)).toBe(false)
    expect(fieldTags('/* an <input> in prose */ // and <select> here')).toEqual([])
    expect(identified(fieldTags('<input id={fieldId} value={v} />')[0].tag)).toBe(true)
  })
})
