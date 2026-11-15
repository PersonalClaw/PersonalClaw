/**
 * #514 — the project-name cap.
 *
 * The cap the field enforces is the SAME number the server enforces, read from the Python source so
 * the two cannot drift. (The task-list count badge retired with the old page's `TaskListRow`, PROJ PR
 * 4: a project's page counts its open tasks in its Tasks group, and a count its read did not give is
 * never drawn as 0, the group says its read failed, `aProjectPageFailsPartway.test.tsx`.)
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MAX_NAME_LEN } from '../../lib/api'

describe('the project-name cap is one number, not two', () => {
  const source = (rel: string) => readFileSync(join(process.cwd(), rel), 'utf8')

  it('matches the server constant exactly', () => {
    // 🔑 The anti-drift rail. A cap enforced in the form but not the store (or vice versa) is how
    // 3000 characters persisted in the first place.
    const py = source('../src/personalclaw/tasks/hierarchy.py')
    const declared = /^MAX_NAME_LEN = (\d+)$/m.exec(py)
    expect(declared, 'hierarchy.py must declare MAX_NAME_LEN').not.toBeNull()
    expect(MAX_NAME_LEN).toBe(Number(declared![1]))
  })

  it('both project-name inputs carry the cap', () => {
    // Structural: these two inputs live inside internal components (the New project dialog and the
    // project page's title, which renames it).
    const src = source('src/pages/projects/ProjectTitle.tsx') + source('src/pages/projects/NewProjectDialog.tsx')
    const capped = src.match(/maxLength=\{MAX_NAME_LEN\}/g) ?? []
    expect(capped.length, 'the New-project input AND the inline rename input').toBe(2)
  })

  it('the store refuses over-long rather than truncating', () => {
    // Pinned here too because it is the reason the form caps instead of trimming on submit.
    const py = source('../src/personalclaw/tasks/hierarchy.py')
    expect(py).toMatch(/must be at most \{MAX_NAME_LEN\} characters/)
    expect(py, 'a truncating slice would silently rename the user’s project')
      .not.toMatch(/name\s*=\s*name\[:\s*MAX_NAME_LEN\s*\]/)
  })
})
