import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Field, TextInput } from '../../ui/forms'

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

// ── A local `Field` silently strips its inputs' accessible names ───────────────
//
// `ui/forms.tsx` splits one job across two components: `Field` publishes a label id through
// `FieldLabelCtx`, and every control (`TextInput`, `TextArea`, `Select`, …) reads it via
// `useFieldLabelId()` and claims that label with `aria-labelledby`:
//
//     const claimsFieldLabel = !!labelId && !name
//     aria-labelledby={claimsFieldLabel ? labelId : undefined}
//     aria-label={claimsFieldLabel ? undefined : ariaLabel}
//
// So a LOCAL Field — a plain `<div>{label}</div>` — is not a style variant. It provides no context,
// `claimsFieldLabel` is false, and because these call sites pass no `ariaLabel` either, the control
// ends up with NEITHER. `ToolsPage` had exactly that, and the cost was measured on the live DOM:
//
//     BEFORE  {placeholder: "filesystem-mcp", ariaLabel: null, ariaLabelledby: null, name: null}
//     AFTER   {placeholder: "filesystem-mcp", accessibleName: "Name"}
//
// A placeholder is NOT an accessible name (it disappears on input and is not exposed as one), so all
// seven inputs in the "Add tool server" modal were unnamed to assistive tech.
//
// THE OTHER THREE `Field`s ARE NOT THIS. Verified, and pinned below:
//  · `ui/forms.tsx` — the primitive itself (uppercase caption + label-id context).
//  · `pages/settings/settingsUI.tsx` — a bordered SETTINGS ROW (`border-b`, stacked control), a
//    different layout job. Its call sites pass `ariaLabel` on the control where one is needed, so it
//    does not strip names the way a bare label div does.
//  · `pages/projects/ProjectsSection.tsx` — also local, but it wraps a RAW `<input>`, not a form
//    primitive, so there is no context to claim in the first place. Converging it means migrating the
//    raw input onto `TextInput` too — a wider change, deliberately left for its own pass rather than
//    smuggled in here. Logged in the session ledger.

const TOOLS_PAGE = join(process.cwd(), 'src/pages/tools/ToolsPage.tsx')
const SRC = join(process.cwd(), 'src')

/** Look up an element by id WITHOUT a CSS selector: React's `useId()` emits `:r0:`, and a colon is
 *  not valid in a selector — `querySelector('#:r0:')` throws. Attribute matching is selector-safe. */
function byId(root: HTMLElement, id: string): Element | null {
  return root.ownerDocument.body.querySelector(`[id="${CSS.escape(id)}"]`)
}

/** The accessible name a control actually resolves, following aria-labelledby. */
function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return byId(root, by)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('a control inside the shared Field claims its label', () => {
  it('resolves the Field label as its accessible name', () => {
    const { container } = render(
      <Field label="Command" hint="The executable that starts the server.">
        <TextInput value="" onChange={() => {}} placeholder="npx" />
      </Field>,
    )
    const input = container.querySelector('input')!
    expect(accessibleName(input, container as HTMLElement)).toBe('Command')
  })

  it('the aria-labelledby target actually exists', () => {
    // A dangling id is worse than no id: AT reports no name while the markup looks correct.
    const { container } = render(
      <Field label="Endpoint URL"><TextInput value="" onChange={() => {}} /></Field>,
    )
    const by = container.querySelector('input')!.getAttribute('aria-labelledby')
    expect(by).toBeTruthy()
    expect(byId(container as HTMLElement, by!)?.textContent).toBe('Endpoint URL')
  })

  it('a BARE label div strips the name — the shape that was in ToolsPage', () => {
    // This is the defect reproduced: same markup a local Field produced, no context, no ariaLabel.
    const { container } = render(
      <div>
        <div>Command</div>
        <TextInput value="" onChange={() => {}} placeholder="npx" />
      </div>,
    )
    const input = container.querySelector('input')!
    expect(accessibleName(input, container as HTMLElement)).toBeNull()
  })

  it('an explicit ariaLabel still wins outside a Field', () => {
    // The escape hatch must keep working — a control with its own name, or one outside any Field.
    const { container } = render(<TextInput value="" onChange={() => {}} ariaLabel="Search tools" />)
    expect(container.querySelector('input')!.getAttribute('aria-label')).toBe('Search tools')
  })
})

describe('ToolsPage uses the shared Field', () => {
  const src = readFileSync(TOOLS_PAGE, 'utf8')

  it('declares no local Field', () => {
    expect(/function Field\b/.test(src), 'ToolsPage should not declare its own Field').toBe(false)
  })

  it('imports Field from the form family', () => {
    expect(src).toMatch(/import \{[^}]*\bField\b[^}]*\} from '\.\.\/\.\.\/ui\/forms'/)
  })

  it('still wraps its inputs in Field, so the labels are published', () => {
    // Deleting the local Field without importing the shared one would ALSO remove the label text —
    // this pins that the fields survived the swap.
    expect((src.match(/<Field label=/g) ?? []).length).toBe(8)
  })
})

describe('no page reimplements Field around a form-family control', () => {
  it('a local Field never wraps a TextInput/TextArea/Select', () => {
    // The rail: a local Field is only harmless when nothing inside it reads FieldLabelCtx. If a file
    // declares its own Field AND uses a form-family control, that control has lost its name.
    const offenders: string[] = []
    const walk = (dir: string): string[] => {
      const out: string[] = []
      for (const n of readdirSync(dir)) {
        const p = join(dir, n)
        if (statSync(p).isDirectory()) { out.push(...walk(p)); continue }
        if (/\.tsx$/.test(n) && !/\.test\.tsx$/.test(n)) out.push(p)
      }
      return out
    }
    for (const abs of walk(join(SRC, 'pages'))) {
      const src = readFileSync(abs, 'utf8')
      if (!/function Field\b/.test(src)) continue
      // settingsUI is the deliberate second Field (a settings ROW, not a form field) — and it is an
      // export, so its consumers are explicit rather than shadowed.
      if (abs.endsWith('settingsUI.tsx')) continue
      if (/<(TextInput|TextArea|Select)\b/.test(src)) offenders.push(abs.slice(SRC.length + 1))
    }
    expect(
      offenders,
      `local Field + a form-family control (the control loses its accessible name):\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })
})
