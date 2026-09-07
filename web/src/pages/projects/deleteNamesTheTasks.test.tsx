/** Deleting a project destroyed every task in it, and the dialog promised the opposite.
 *
 * `ProjectsSection.del()` asked for confirmation with:
 *
 *     "Its context directory and task lists are removed — the tasks themselves stay."
 *
 * `tasks/hierarchy_handlers.py` resolves every task scoped to the project and deletes each one —
 * `list_all_tasks(project=…, limit=10_000)` → `delete_task(t.id)` → `native.py`'s `path.unlink()`.
 * No trash, no soft-delete, no version history; the tombstone stores only an id, so it is a sync
 * breadcrumb and not a recovery path. Titles, descriptions, action plans, exit criteria, research
 * notes, execution notes and comments all go.
 *
 * 🔴 THE FAILURE MODE IS THE ASSERTION, NOT THE OMISSION. A dialog that said nothing about tasks
 * would have left the user uninformed. This one told them the tasks were safe, so the click that
 * destroyed a project's entire task history was the one that read as cautious.
 *
 * 🪤 AND THE COMMENT ABOVE IT WAS THE CAUSE. It reasoned carefully — and from the wrong function.
 * `hierarchy.delete_project` genuinely does unlink the list files and leave the task rows alone; its
 * docstring says so, and reading it produces exactly the sentence that shipped. But #457 added the
 * cascade in the CALLING handler, above that function, because orphaned rows pointing at dead list
 * ids were "unreachable from every scoped view". The docstring is still accurate about the function
 * it documents. The dialog was describing an inner contract instead of the operation the button runs.
 * **Copy written against a callee goes stale the first time a caller does more** — which is the
 * transferable lesson, and why the rails below assert against the HANDLER, not against `delete_project`.
 *
 * 🔑 THE CASCADE IS NOT THE BUG. #457's reasoning holds: unreachable orphans are worse than deleted
 * rows. This change is entirely about informed consent, and deliberately leaves the behaviour alone.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const WEB = process.cwd()
const REPO = join(WEB, '..')
const page = readFileSync(join(WEB, 'src/pages/projects/ProjectsSection.tsx'), 'utf8')
/** Comments stripped: this fix's own comments necessarily QUOTE the false sentence to explain it, and
 *  a scan that counts prose as code has been wrong repeatedly in this repo. Blanked in place. */
const code = page
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

/** The two dialogs `del()` can show: the ordinary confirm, and the 409/force re-confirm. */
const plainBody = code.match(/title: `Delete project[\s\S]*?body: '([^']*)'/)?.[1] ?? ''
const forceBody = code.match(/title: 'Project still has active work'[\s\S]*?body: `([^`]*)`/)?.[1] ?? ''

describe('the plain delete dialog names the task loss', () => {
  it('found the dialog body', () => {
    expect(plainBody, 'the confirm body must still be a single-quoted literal here').not.toBe('')
  })

  it('🔴 the false promise is GONE', () => {
    // The exact clause that shipped. Not a loose "tasks" search: the defect was this assertion.
    expect(plainBody, 'it must no longer claim the tasks survive').not.toMatch(/tasks themselves stay/i)
    expect(plainBody).not.toMatch(/tasks?[^.]{0,30}(stay|survive|are kept|remain)/i)
  })

  it('it says the tasks are deleted, permanently', () => {
    expect(plainBody).toMatch(/task/i)
    expect(plainBody, 'permanence is the part that changes the decision').toMatch(/permanently|cannot be undone/i)
  })

  it('and it names what inside a task is lost, not just "tasks"', () => {
    // "N tasks deleted" reads as rows in a list. The thing people cannot rewrite is the prose.
    expect(plainBody, 'the user-authored content is the real loss').toMatch(/notes/i)
    expect(plainBody).toMatch(/exit criteria/i)
  })

  it('🪤 it interpolates NO count', () => {
    // This page cannot obtain an honest one: `api.tasks()` defaults to `limit=50` across the whole
    // account and the server's `total` saturates at 500, so a number here would understate the loss
    // on exactly the large projects where it is worst. Same ruling as `repeatableResetConfirms`.
    const dialogArgs = code.match(/title: `Delete project[\s\S]*?danger: true/)?.[0] ?? ''
    expect(dialogArgs, 'found the dialog options').not.toBe('')
    expect(dialogArgs, 'no count is interpolated').not.toMatch(/\.length/)
  })

  it('🪤 the one true reassurance is KEPT', () => {
    // Deleting this clause would be the over-correction: the bound `workspace_dir` really is untouched,
    // and stripping every comforting word from a danger dialog is its own kind of dishonesty.
    expect(plainBody).toMatch(/[Ww]orkspace files on disk are left untouched/)
  })
})

describe('the force re-confirm names it too — the same cascade runs on that path', () => {
  it('found the force dialog body', () => {
    expect(forceBody, 'the 409 re-confirm must still be a template literal here').not.toBe('')
  })

  it('🪤 it was INCOMPLETE rather than wrong, and that is why it survived', () => {
    // It enumerates loops, workers, worktrees, branches and chats, every clause accurate — and never
    // mentioned tasks. Its "can't be undone" therefore read as being about the loops it had just
    // listed, so the SECOND dialog gave the user no way to learn the larger loss either.
    expect(forceBody, 'the task loss is named').toMatch(/task/i)
    expect(forceBody).toMatch(/permanently/i)
  })

  it('and its existing enumeration is intact — this change only adds', () => {
    for (const clause of [/bound loops/i, /worktrees/i, /UNBOUND/, /can't be undone/i]) {
      expect(forceBody, `the force dialog must keep: ${clause}`).toMatch(clause)
    }
  })
})

describe('VACUITY: the cascade these dialogs warn about is real', () => {
  const py = readFileSync(join(REPO, 'src/personalclaw/tasks/hierarchy_handlers.py'), 'utf8')

  it('the delete handler really does delete every task in the project', () => {
    // 🪤 Bounded to the next top-level `async def`, NOT to the next blank line — a `[\s\S]*?\n\n`
    // window stops inside the docstring, well before the cascade block.
    const handler = py.match(/async def api_projects_delete[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the delete handler').not.toBe('')
    expect(handler, 'and the window reached the cascade').toMatch(/list_all_tasks\(project=/)
    expect(handler, 'it deletes each one').toMatch(/delete_task\(t\.id\)/)
  })

  it('and the provider really unlinks the file — there is nothing to restore from', () => {
    // If a trash/soft-delete ever lands, "cannot be undone" becomes an overclaim and this reds first.
    const native = readFileSync(join(REPO, 'src/personalclaw/tasks/native.py'), 'utf8')
    const dele = native.match(/async def delete_task[\s\S]*?(?=\n    async def |\n    def |$)/)?.[0] ?? ''
    expect(dele, 'found delete_task').not.toBe('')
    expect(dele, 'a hard unlink, not a soft delete').toMatch(/\.unlink\(/)
  })

  it('🔑 the cascade is deliberate, and this change does not touch it', () => {
    // Pins the REASON, so a later reader does not "fix" the behaviour to match the old copy. If #457's
    // rationale is ever removed from the handler, the copy question is genuinely open again.
    const handler = py.match(/async def api_projects_delete[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'the cascade still explains itself').toMatch(/unreachable from every scoped view/)
  })
})
