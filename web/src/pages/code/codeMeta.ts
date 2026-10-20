/** The delete-confirmation body for a Code project.
 *
 *  🔴 IT LIVED IN TWO PLACES AND BOTH WERE WRONG THE SAME WAY. `CodeSection` and `CodeCockpitPage` each
 *  built this string from a byte-identical template, and both said — for a project with a bound
 *  workspace — *"Your workspace folder and its files are left untouched."*
 *
 *  🪤 THE SAME PREDICATE THAT SELECTED THAT REASSURANCE ARMED THE DESTRUCTION. The copy branched on
 *  `p.workspace_dir`; `loop/manager.py`'s `teardown_for_delete` ends the run with
 *  `end_run(…, discard=True)` → `worktree.discard(…)` for each of the loop's own tasks →
 *  `worktree.py`'s `git worktree remove --force` **and `git branch -D`**, executed with the user's
 *  workspace as cwd. So exactly when the dialog promised safety, PersonalClaw force-deleted branches
 *  from the user's repository.
 *
 *  🔑 THE PRECISE TRUTH, because this correction is worth getting exactly right rather than merely
 *  scarier: `_worktrees_root` puts the task worktrees under `config_dir()`, **not** inside the user's
 *  folder — so the working tree and its files genuinely ARE untouched, and that clause survives. What is
 *  false is the *repository*: each of the loop's `pclaw/task-*` branches is force-deleted, taking any
 *  commit made on one and never merged. Merged work is on the user's own branch and is safe. Recovery
 *  is `git reflog` / `git fsck --lost-found` before gc — forensics, not a product path.
 *
 *  🪤 AND "removes its plan" UNDERSTATED THE REST. `manager.py` also calls `tasks_link.teardown_tasks`,
 *  which deletes every task across the loop's per-phase lists. Tasks are a first-class surface here —
 *  the cockpit renders a whole right-rail Tasks panel over `task_list_ids` — and they survive stop,
 *  complete and fail, dying only on delete (`manager.py`'s own docstring draws that line). So the dialog
 *  named the smallest of the three losses.
 *
 *  🪤 ROOT CAUSE, WORTH REMEMBERING: both call sites' comments reasoned carefully about `store.delete`'s
 *  `rmtree` of the loop's own managed folder — the INNER function, whose contract they described
 *  correctly. The OUTER handler runs `teardown_for_delete` first, and that is what reaches into the
 *  user's repo. **Copy written against a callee goes stale the first time a caller does more.** Hoisting
 *  it here is the structural half of the fix: one sentence, one place, verified against the handler.
 */
/** Structural on purpose: the two call sites hold different records (`Loop` in the list,
 *  `CodeProject` in the cockpit) and this needs three fields from either. `workspace_dir` is optional
 *  in BOTH — which is the whole branch condition, so typing it as required would have been a lie that
 *  `tsc` caught immediately.
 *
 *  `kept` is the work the project's ended run kept because it was never merged (`api.uLoopKeptWork`).
 *  A delete is the one thing that discards it without asking task by task, so the dialog names it:
 *  what is lost is then a choice made knowing which work it is. */
export function codeDeleteBody(
  p: { name: string; status: string; workspace_dir?: string },
  kept: { title: string; branch: string; task_id: string }[] = [],
): string {
  const running = ['running', 'planning', 'intake'].includes(p.status)
  const lead = running
    ? `"${p.name}" is still working — deleting it stops the worker. `
    : `"${p.name}" will be removed. `
  // Named before the file/branch clause because it is the least recoverable of the three.
  const tasks = 'Its plan and every task under it are permanently deleted. '
  const named = kept.map((w) => `\u201c${w.title.trim() || w.branch || w.task_id}\u201d`)
  const lost = named.length
    ? ` That includes the work kept from ${named.join(', ')}; merge it from the project's page first to keep it.`
    : ''
  const files = p.workspace_dir
    ? 'Your workspace folder and the files in it are left untouched, but PersonalClaw’s own '
      + 'pclaw/task-* branches there are force-deleted along with their worktrees — any work on one '
      + 'not yet merged, committed or not, is lost. Work you have merged is safe.' + lost
    // Greenfield: no bound repo, so the generated code lives in the loop's managed folder and
    // `store.delete` rmtree's it. This half was always accurate.
    : 'This project keeps its files in its own managed folder — deleting it also removes those files. '
      + 'Move anything you want to keep out first.'
  return `${lead}${tasks}${files}`
}
