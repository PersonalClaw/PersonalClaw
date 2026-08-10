import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runLook, nodeLook } from './workflowMeta'

// ── One word for a finished run ────────────────────────────────────────────────
//
// Three surfaces render the SAME backend wire value and narrated it three ways:
//
//   loopStatusMeta.complete   "Completed"     (LoopStatus.COMPLETE = "complete")
//   taskMeta terminal `done`  "Completed"
//   workflowMeta.complete     "Complete"      (RunStatus.COMPLETE = "complete")   ← the outlier
//
// A run that has finished is being DESCRIBED, so the past tense is the right form — and "Complete"
// additionally collides with the IMPERATIVE verb `TasksListPage` uses for its row action ("Complete"
// = complete this task). Two of three registries already said "Completed", so the majority and the
// grammar agree; `workflowMeta` was the single divergence.
//
// WHAT THIS DELIBERATELY DOES NOT TOUCH — verified distinctions, pinned so a later pass does not
// "finish the job" and flatten them:
//
//  · The three "Done" FILTER CHIPS (CodeSection, InboxPage, LoopsListPage) sit beside "All" /
//    "Active" / "Ongoing" and name A CATEGORY OF ROWS you narrow to, not the status of one thing.
//    Same already-ruled shape as the plural trigger filters ("Schedules" vs "Schedule"). A filter
//    chip is a noun phrase for a bucket; a status label describes an entity.
//  · `NODE_LOOK.done` stays "Done" — a node is a STEP, its wire value is `done` (not `complete`),
//    and a step reads as done rather than completed. Different vocabulary, different word.
//  · `TasksListPage`'s "Complete" row action stays — it is a VERB. Renaming it to "Completed" would
//    turn a command into a description.

const WORKFLOW_META = join(process.cwd(), 'src/pages/workflows/workflowMeta.ts')
const LOOP_META = join(process.cwd(), 'src/pages/loops/loopStatusMeta.ts')
const TASK_META = join(process.cwd(), 'src/pages/tasks/taskMeta.tsx')

describe('a finished run is "Completed" everywhere', () => {
  it('workflowMeta says Completed', () => {
    expect(runLook('complete').label).toBe('Completed')
  })

  it('the three status registries agree on the word', () => {
    // Read as source rather than importing all three: taskMeta pulls in lucide + JSX, and the point
    // is the LITERAL each registry ships for its terminal-success entry.
    expect(readFileSync(WORKFLOW_META, 'utf8')).toMatch(/complete: \{ label: 'Completed'/)
    expect(readFileSync(LOOP_META, 'utf8')).toMatch(/complete: \{ label: 'Completed'/)
    expect(readFileSync(TASK_META, 'utf8')).toMatch(/key: 'done', label: 'Completed'/)
  })

  it('no status registry still ships the bare adjective', () => {
    for (const f of [WORKFLOW_META, LOOP_META]) {
      expect(readFileSync(f, 'utf8'), `${f} should not label a status 'Complete'`)
        .not.toMatch(/label: 'Complete'[,}]/)
    }
  })

  it('keeps everything else about the status look intact', () => {
    // The regression guard: a label edit must not disturb tone/icon/spin, which other surfaces
    // branch on. (The pre-existing workflowMeta test asserts tone and spin; this pins them beside
    // the new label so a future rename cannot quietly swap the tone too.)
    const look = runLook('complete')
    expect(look.tone).toBe('text-success')
    expect(look.spin).toBeUndefined()
    expect(look.icon).toBeTruthy()
  })
})

describe('the deliberate non-conversions', () => {
  it('a NODE stays "Done" — a step, not a run', () => {
    // Different wire value (`done`, not `complete`) and different grain. Flattening these into one
    // word would claim a step and a run are the same kind of thing.
    expect(nodeLook('done').label).toBe('Done')
  })

  it('the filter chips keep their bucket wording', () => {
    // "Done" beside "All"/"Active"/"Ongoing" names a category of rows. Converging a filter chip onto
    // a status label is the mistake the trigger-filter ruling already recorded.
    const chips: Array<[string, RegExp]> = [
      ['src/pages/code/CodeSection.tsx', /key: 'done', label: 'Done'/],
      ['src/pages/inbox/InboxPage.tsx', /key: 'handled', label: 'Done'/],
      ['src/pages/loops/LoopsListPage.tsx', /key: 'done', label: 'Done'/],
    ]
    for (const [rel, re] of chips) {
      expect(readFileSync(join(process.cwd(), rel), 'utf8'), `${rel} filter chip should stay "Done"`)
        .toMatch(re)
    }
  })

  it('the task row ACTION stays the verb "Complete"', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')
    expect(src).toMatch(/label: 'Complete', onSelect: onComplete/)
  })
})
