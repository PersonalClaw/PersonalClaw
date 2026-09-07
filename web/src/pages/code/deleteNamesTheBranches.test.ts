/** Deleting a Code project force-deletes branches in the user's repo, and the dialog promised safety.
 *
 * Both `CodeSection` and `CodeCockpitPage` built a byte-identical confirmation body which said, for a
 * project with a bound workspace:
 *
 *     "Your workspace folder and its files are left untouched."
 *
 * 🪤 THE SAME PREDICATE SELECTED THAT REASSURANCE AND ARMED THE DESTRUCTION. The copy branched on
 * `p.workspace_dir`. `loop/manager.py` runs the teardown under
 * `if loop is not None and (loop.workspace_dir or "").strip():` → `worktree.cleanup_all(…)` →
 * `worktree.py`'s `git worktree remove --force` **and `git branch -D`**, executed with the user's
 * workspace as cwd. Exactly when the dialog promised safety, PersonalClaw deleted branches from the
 * user's repository.
 *
 * 🔑 THE PRECISE TRUTH, because a correction is only worth making if it is exact rather than merely
 * scarier. `_worktrees_root` puts the task worktrees under `config_dir()`, **not** inside the user's
 * folder — so the working tree and its files genuinely ARE untouched, and that clause survives. What was
 * false is the *repository*: every `pclaw/task-*` branch is force-deleted, taking any commit made on one
 * and never merged. Merged work sits on the user's own branch and is safe. Recovery is
 * `git reflog` / `git fsck --lost-found` before gc — forensics, not a product path.
 *
 * 🪤 AND "removes its plan" UNDERSTATED THE REST: `manager.py` also calls `tasks_link.teardown_tasks`,
 * deleting every task across the loop's per-phase lists. `manager.py`'s own docstring draws the line —
 * tasks survive stop, complete and fail, and die only on delete — and the cockpit renders a whole
 * right-rail Tasks panel over them. The dialog named the smallest of the three losses.
 *
 * 🪤 ROOT CAUSE: both call sites' comments reasoned about `store.delete`'s `rmtree` of the loop's own
 * managed folder — the INNER function, described correctly. The OUTER handler runs `teardown_for_delete`
 * first. **Copy written against a callee goes stale the first time a caller does more** — the third
 * instance of that exact shape found in this campaign, after the project-delete dialog and the rail that
 * certified it. The duplication is why it survived: two copies, neither anchored to the handler.
 *
 * 🔑 The same page ALREADY told the truth on Stop — "a task still running loses its own worktree and
 * branch" — while Delete, which does strictly more, denied it. A rail below pins that, because the
 * honest sentence existing 100 lines away is the strongest evidence the dialog could have been right.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { codeDeleteBody } from './codeMeta'

const WEB = process.cwd()
const REPO = join(WEB, '..')
const read = (rel: string) => readFileSync(join(WEB, 'src', rel), 'utf8')
const py = (rel: string) => readFileSync(join(REPO, 'src/personalclaw', rel), 'utf8')
/** Comments blanked IN PLACE: this fix's own comments quote the false sentence to explain it. */
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

const bound = codeDeleteBody({ name: 'api', status: 'ready', workspace_dir: '/repo' })
const green = codeDeleteBody({ name: 'api', status: 'ready' })
const running = codeDeleteBody({ name: 'api', status: 'running', workspace_dir: '/repo' })

describe('the bound-workspace body tells the truth about the repo', () => {
  it('🔴 it no longer claims blanket safety', () => {
    // The exact clause that shipped. Its first half is TRUE and is kept; what may not return is the
    // full stop after "untouched", which is what made it a blanket promise.
    expect(bound, 'the blanket form must be gone')
      .not.toMatch(/files are left untouched\.\s*$/)
  })

  it('it names the branch deletion, and that the branches are PersonalClaw’s own', () => {
    expect(bound).toMatch(/pclaw\/task-\*/)
    expect(bound, 'force-deleted, not merely removed').toMatch(/force-deleted/)
  })

  it('🔑 it distinguishes unmerged from merged — the difference decides whether to cancel', () => {
    // A user who has merged their work can delete freely; one who has not must not. A warning that
    // failed to separate those would be either useless or falsely alarming.
    expect(bound, 'the loss is scoped to unmerged commits').toMatch(/not yet merged is lost/)
    expect(bound, 'and merged work is stated safe').toMatch(/merged is safe/)
  })

  it('🪤 the TRUE half of the original clause survives', () => {
    // The working tree really is untouched — `_worktrees_root` is under `config_dir()`. Deleting this
    // would be the over-correction: it would imply the user's files are at risk, which they are not.
    expect(bound).toMatch(/workspace folder and the files in it are left untouched/)
  })
})

describe('the task deletion is named, on every branch of the copy', () => {
  it('all three variants say the tasks go', () => {
    for (const [label, body] of [['bound', bound], ['greenfield', green], ['running', running]] as const) {
      expect(body, `${label}: tasks are named`).toMatch(/every task under it are permanently deleted/)
    }
  })

  it('the greenfield managed-folder warning is unchanged — it was always accurate', () => {
    expect(green).toMatch(/keeps its files in its own managed folder/)
    expect(green).toMatch(/Move anything you want to keep out first/)
    // 🪤 And greenfield must NOT claim the workspace is safe: there is no bound workspace to be safe.
    expect(green).not.toMatch(/left untouched/)
  })

  it('the running variant still says the worker stops', () => {
    expect(running).toMatch(/still working — deleting it stops the worker/)
  })
})

describe('one owner: the sentence cannot drift back into two copies', () => {
  it('both call sites delegate, and neither builds a body inline', () => {
    for (const rel of ['pages/code/CodeSection.tsx', 'pages/code/CodeCockpitPage.tsx']) {
      const code = strip(read(rel))
      expect(code, `${rel} delegates`).toMatch(/body: codeDeleteBody\(p\)/)
      // The duplication is what let the defect survive in two places at once.
      expect(code, `${rel} must not hand-roll the body again`)
        .not.toMatch(/left untouched/)
    }
  })
})

describe('VACUITY: the destruction this copy warns about is real', () => {
  it('the teardown is armed by the SAME predicate the copy branches on', () => {
    // This is the finding in one assertion. If the guard ever stops keying on `workspace_dir`, the
    // copy's branch is wrong and this reds first.
    const mgr = py('loop/manager.py')
    expect(mgr, 'armed on a bound workspace').toMatch(
      /if loop is not None and \(loop\.workspace_dir or ""\)\.strip\(\):/,
    )
    expect(mgr, 'and it calls the worktree sweep').toMatch(/worktree\.cleanup_all\(/)
  })

  it('the sweep really force-deletes branches, with the user’s workspace as cwd', () => {
    const wt = py('loop/worktree.py')
    expect(wt, 'worktree removal is forced').toMatch(/"worktree", "remove", "--force"/)
    expect(wt, 'and the branch is force-deleted').toMatch(/"branch", "-D"/)
  })

  it('🔑 but the worktrees live OUTSIDE the workspace — which is why the kept clause is true', () => {
    // Pins the fact the surviving half of the sentence rests on. If worktrees ever move INTO the
    // user's folder, "the files in it are left untouched" becomes false and this reds.
    expect(py('loop/worktree.py'), 'rooted under config_dir(), not the workspace')
      .toMatch(/under\s*\n?\s*``config_dir\(\)``, NOT under the workspace itself/)
  })

  it('the tasks really are deleted on delete — and only on delete', () => {
    const mgr = py('loop/manager.py')
    expect(mgr, 'delete tears down tasks').toMatch(/teardown_tasks\(/)
    // The discriminator, in the codebase's own words: stop/complete/fail deliberately keep them, so
    // "permanently deleted" is right for THIS dialog and would be wrong on the Stop one.
    expect(mgr, 'stop/complete/fail keep them').toMatch(/WITHOUT touching its Tasks/)
  })

  it('🔑 the Stop dialog on the same page already said this, which is the sharpest evidence', () => {
    // Delete does strictly more than Stop and said strictly less. Kept as a rail so the two cannot
    // drift apart again in the other direction.
    expect(strip(read('pages/code/CodeCockpitPage.tsx')), 'Stop names the worktree + branch loss')
      .toMatch(/loses its own worktree and branch/)
  })
})
