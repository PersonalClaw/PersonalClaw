import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Field, TextArea, TextInput } from '../ui/forms'

// ── Raw <input>/<textarea> inside a Field cannot claim its label ───────────────
//
// A form control gets its accessible name by claiming the surrounding Field's label via
// `aria-labelledby` — but only the form-family components read `FieldLabelCtx`. A RAW `<input>` or
// `<textarea>` cannot, no matter which Field wraps it. So "wrapped in a Field" is not enough; the
// control has to be a primitive (or carry its own aria-label).
//
// Found by driving the UI, not by reading: a sweep that CLICKED each area's create affordance and
// then probed for controls with no resolvable accessible name reported three surfaces —
//
//     #/projects  after "New project"  →  2  (raw <input> + raw <textarea> in a LOCAL Field)
//     #/files     after "New file"     →  1  (inline create row, no Field at all)
//     #/tools     ?add=1               →  1  (raw <textarea>; its 7 TextInput siblings were fixed
//                                             one change earlier, but a raw element could not be)
//
// That last one is the instructive case: the previous change migrated ToolsPage onto the shared
// Field and fixed seven inputs, and this textarea STILL had no name — because the fix was to the
// label publisher, and a raw element is not a subscriber. Fixing the wrapper does not fix a raw child.
//
// Fixes: Projects' two controls → `TextInput`/`TextArea`, and its LOCAL Field now publishes a label id
// via `FieldLabelProvider` instead of being replaced — swapping in the shared Field moved 27.9% of the
// modal's pixels (hint below in uppercase vs above in sentence case), which is an open owner taste
// call, not this change's business. A second LAYOUT is allowed; a second layout that breaks the label
// CONTRACT is not. Tools' textarea → `TextArea` (which also retired the last consumer of a hand-copied
// `mcpInputCls`); Files' inline input keeps its own `aria-label`, which tracks the MODE ("New folder
// name" vs "New file name") because there is no label to claim and the icon distinguishing them is
// decorative.
//
// NO TREE-WIDE RAIL HERE, and that is a scoping decision worth recording. The precise defect — "a raw
// element that is the IMMEDIATE CHILD of a <Field>" — is not expressible by line-window scanning: a
// 4-line window missed a valid aria-label three attribute-lines down, and a 12-line window reached
// from one <Field> into the NEXT Field's correctly-labelled control. Broadening to "every raw form
// element in pages/ self-names" IS decidable, but it reports 130 sites across ~45 files, most of them
// probably fine (named by a wrapping <label>, or checkbox/radio patterns). Landing that would mean
// either shipping a red gate or "fixing" 130 controls without driving one of them. The backlog is
// logged in the session ledger for a pass that can verify each site; the per-surface DOM probe stays
// the detector, because it reads the rendered accessibility tree instead of guessing from source shape.

const SRC = join(process.cwd(), 'src')

function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return root.ownerDocument.body.querySelector(`[id="${CSS.escape(by)}"]`)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('a form primitive inside a Field claims its label; a raw element cannot', () => {
  it('TextInput in a Field is named', () => {
    const { container } = render(
      <Field label="Name"><TextInput value="" onChange={() => {}} placeholder="Project name…" /></Field>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Name')
  })

  it('TextArea in a Field is named', () => {
    const { container } = render(
      <Field label="Brief"><TextArea value="" onChange={() => {}} rows={4} /></Field>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement)).toBe('Brief')
  })

  it('a RAW input in the very same Field is NOT named — the defect, reproduced', () => {
    // The whole point: the Field is correct and publishing, and this control still has no name,
    // because a raw element never reads the context. This is why the fix is the CONTROL, not the wrapper.
    const { container } = render(
      <Field label="Name"><input placeholder="Project name…" /></Field>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBeNull()
  })

  it('a raw element with its own aria-label IS named — the escape hatch', () => {
    // What the Files inline create row does: no Field to claim, so it names itself.
    const { container } = render(<input placeholder="file name" aria-label="New file name" />)
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('New file name')
  })
})

describe('the three fixed surfaces', () => {
  it('ProjectsSection keeps its own Field layout but PUBLISHES its label id', () => {
    const src = readFileSync(join(SRC, 'pages/projects/ProjectsSection.tsx'), 'utf8')
    // The local Field stays: its hint-above-in-sentence-case layout is the subject of an open
    // owner taste call, and swapping it for the shared Field moved 27.9% of the modal's pixels.
    expect(/function Field\b/.test(src), 'the local Field is a kept layout, not drift').toBe(true)
    // What it MUST do is honour the contract — publish a label id its children can claim.
    expect(src).toMatch(/import \{[^}]*\bFieldLabelProvider\b[^}]*\} from '\.\.\/\.\.\/ui\/forms'/)
    expect(src).toMatch(/<FieldLabelProvider value=\{labelId\}>/)
    expect(src).toMatch(/<span id=\{labelId\}/)
    // And its children must be primitives, which are the only things that read the context.
    expect(src).toMatch(/<TextInput autoFocus value=\{name\}/)
    expect(src).toMatch(/<TextArea value=\{brief\}/)
  })

  it('ToolsPage env field is a TextArea, and the hand-copied class is gone', () => {
    const src = readFileSync(join(SRC, 'pages/tools/ToolsPage.tsx'), 'utf8')
    expect(src).toMatch(/<TextArea value=\{env\}/)
    // The class existed only to re-create field chrome the primitive already owns; leaving it would
    // be dead code that still looks load-bearing.
    expect(/const mcpInputCls =/.test(src), 'mcpInputCls should be deleted with its last consumer').toBe(false)
  })

  it('the Files inline create input names itself, and tracks the mode', () => {
    const src = readFileSync(join(SRC, 'pages/files/FilesSection.tsx'), 'utf8')
    // Mode-dependent: "folder" and "file" are different questions, and the icon is decorative.
    expect(src).toMatch(/aria-label=\{creating === 'dir' \? 'New folder name' : 'New file name'\}/)
  })
})
