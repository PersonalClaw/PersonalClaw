import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Native form controls inherit the theme's colour scheme; they must not pin one ────────────────
//
// `color-scheme` decides how the browser paints the parts of a control CSS cannot reach — a
// `<select>`'s dropdown popup, an `<input type="date">` picker, autofill, scrollbars. Every one of
// these controls carried a hardcoded `[color-scheme:dark]` in its class list, so in LIGHT mode a
// light page opened a DARK native popup.
//
// Measured on a live gateway in light theme, before this change:
//
//     5 of 5 visible select/date controls reached  →  computed color-scheme: dark
//     (#/settings/tool-output, #/triggers/new, #/tasks/new ×3)
//
// The companion fix made the ROOT scheme follow the theme (`:root:not(.light)`), which handles
// page-level chrome like scrollbars. It does nothing for these, because an element-level
// declaration beats an inherited one — so the two changes are both required and this is the half
// that reaches form controls.
//
// 19 code sites across 16 files dropped the override (plus the `forms.doc.ts` anatomy string that
// advertised it as blessed chrome). The primitives `Select` and `DateInput` are two of them, which
// is why every call site that routes through the form family is fixed by those alone.

const SRC = join(process.cwd(), 'src')

/** Source with comments removed. The scan below looks for a pinned scheme in CODE, and the header
 *  of this very file quotes `[color-scheme:dark]` while explaining the defect — so a raw scan flags
 *  itself. Stripping comments keeps the rail honest about its own documentation, and means no file
 *  can be failed for merely *describing* the pattern it must not use. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')   // block + JSX comments
    .replace(/(^|[^:])\/\/.*$/gm, '$1') // line comments, without eating https://
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx|css)$/.test(name)) out.push(p)
  }
  return out
}

describe('colour scheme is inherited, never pinned on a control', () => {
  const files = walk(SRC)

  it('reads a real tree (not vacuously green)', () => {
    // A rail that scans nothing passes forever. Anchor it: the form primitives must be in scope.
    expect(files.length).toBeGreaterThan(200)
    expect(files.some((f) => f.endsWith('ui/forms.tsx'))).toBe(true)
  })

  it('no file pins color-scheme on an element', () => {
    const offenders = files
      .filter((f) => /\[color-scheme:\s*(dark|light)\]/.test(code(readFileSync(f, 'utf8'))))
      .map((f) => f.slice(SRC.length + 1))
    expect(offenders, `these pin a scheme instead of inheriting the theme's: ${offenders.join(', ')}`).toEqual([])
  })

  it('the form primitives keep the rest of their chrome', () => {
    // Removing the pin must not have taken the field chrome with it — the controls should still
    // carry surface, radius and focus ring, which is what makes them look like the field family.
    const forms = readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8')
    const select = forms.slice(forms.indexOf('export function Select'))
    expect(select).toMatch(/bg-surface-container/)
    expect(select).toMatch(/focus:ring-2/)
    expect(select).toMatch(/rounded-md/)
  })

  it('the docs no longer advertise a pinned dark scheme', () => {
    // `uiDocs.drift` compares authored docs against the components; a doc still claiming
    // "color-scheme:dark" would describe chrome the component no longer has.
    const doc = readFileSync(join(SRC, 'ui/forms.doc.ts'), 'utf8')
    expect(doc).not.toMatch(/color-scheme:\s*dark/)
    expect(doc, 'and it should say what happens instead').toMatch(/theme-inherited color-scheme/)
  })

  it('the root still owns the scheme per theme', () => {
    // The other half of the pair. If this regresses, inheriting becomes inheriting the WRONG value.
    const tokens = readFileSync(join(SRC, 'design/tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    expect(tokens).toMatch(/\.light\s*\{[\s\S]*?color-scheme:\s*light/)
    expect(tokens).toMatch(/:root:not\(\.light\)\s*\{\s*color-scheme:\s*dark/)
  })
})
