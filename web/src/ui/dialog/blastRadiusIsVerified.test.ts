import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { pyBetween, pyMethod } from '../../design/pySource'

// ── A destructive dialog's body is a CLAIM about the backend ────────────────────────────────────
//
// #1603 made every danger dialog carry a body; #1608 made every `confirmDelete` name its subject.
// Neither asked the next question: **is the body TRUE?** Two were checked against the handlers that
// implement them, and both were materially incomplete — not wrong, just quiet about the part a user
// cannot see:
//
//   tasks/TaskDetail        "Delete this task?" / "This cannot be undone." — the ONLY destructive
//                           dialog in the app whose subject appears in NEITHER its title nor its body
//                           (the seven other hand-rolled ones all interpolate theirs), and it omitted
//                           that deleting a prerequisite UNBLOCKS whatever was waiting on it.
//   knowledge/KnowledgeDetail  "This removes it from the knowledge base." — which is the one thing a
//                           user already knows from pressing Delete. It also takes their HIGHLIGHTS
//                           (the only content on that surface they wrote) and the FILE stored in the
//                           library.
//
// 🔑 SO THIS RAIL READS THE PYTHON. A body that describes behaviour has to fail when the behaviour
// changes, or it decays into confident fiction. (Three other web tests already read the backend
// source, so the idiom is the repo's own, not an invention here.)

const SRC = join(process.cwd(), 'src')
const PY = join(__dirname, '../../../../src/personalclaw')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const web = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')

describe('the task delete states what the backend really does', () => {
  it('claims the unblock, and the backend performs it', () => {
    const ui = web('pages/tasks/TaskDetail.tsx')
    expect(ui, 'the sentence exists').toMatch(
      /waiting on it \$\{dependents\.length === 1 \? 'becomes' : 'become'\} unblocked/,
    )
    // 🪤 AND THAT THE BODY ACTUALLY USES IT — caught by mutation: deleting the interpolation while
    // leaving the `unblocks` const in place kept the first assertion green, so the claim existed and
    // was never rendered. Third time this session a rail of mine checked a DEFINITION instead of its
    // SUPPLY; the composed string is the only thing a user sees.
    expect(ui, 'and the body interpolates it').toMatch(
      /body: `This cannot be undone\.\$\{unblocks\}`/,
    )
    const impl = py('tasks/native.py')
    const del = impl.slice(impl.indexOf('async def delete_task'), impl.indexOf('def graph(self)'))
    expect(del, 'edges pointing at the deleted task are dropped').toMatch(
      /d\.depends_on_task_id != task_id/,
    )
    expect(del, 'and every former dependent is re-evaluated').toMatch(
      /reconcile\.reconcile_blocked_status\(tasks, t\.id\)/,
    )
  })

  it('the count comes from the set the Blocks section shows', () => {
    // One definition: the sentence can only appear when it is true, and it cannot disagree with the
    // list right below it.
    const ui = web('pages/tasks/TaskDetail.tsx')
    expect(ui).toMatch(/const dependents = allTasks\.filter\(\(t\) => prereqIds\(t\)\.includes\(task\.id\)\)/)
    expect((ui.match(/allTasks\.filter\(\(t\) => prereqIds/g) ?? []).length, 'computed once').toBe(1)
  })

  it('names the task — it was the one dialog naming its subject nowhere', () => {
    const ui = web('pages/tasks/TaskDetail.tsx')
    expect(ui).toMatch(/title: `Delete "\$\{task\.title\}"\?`/)
    expect(ui, 'the anonymous form must not come back').not.toMatch(/title: 'Delete this task\?'/)
  })

  it('says NOTHING about comments, because the backend does not remove them', () => {
    // 🔑 THE MOST USEFUL ASSERTION HERE PINS AN ABSENCE. Task comments live in a separate
    // `_comments_<id>.json`, and `delete_task` unlinks only the task file — so the comments are
    // orphaned on disk. Filed as a backend defect; until it is fixed, copy must not claim the cleanup.
    //
    // When someone DOES fix it, this test fails — which is the point: it tells them the dialog can now
    // say so, instead of leaving the sentence stale forever.
    const impl = py('tasks/native.py')
    const del = impl.slice(impl.indexOf('async def delete_task'), impl.indexOf('def graph(self)'))
    expect(del, 'still no comments cleanup — if this fails, update the dialog copy too')
      .not.toMatch(/_comments_/)
    expect(web('pages/tasks/TaskDetail.tsx'), 'so the dialog claims nothing about them')
      .not.toMatch(/body: `This cannot be undone\.[^`]*comment/)
  })
})

describe('the knowledge delete states what the backend really takes', () => {
  it('claims the highlights, and the schema cascades them', () => {
    expect(web('pages/knowledge/KnowledgeDetail.tsx'), 'the dialog counts them').toMatch(
      /Its \$\{annotations\.length\} highlight/,
    )
    const store = py('knowledge/store.py')
    // 🪤 SCOPED TO THE ANNOTATIONS TABLE. The first draft matched that FK clause anywhere in the file,
    // and several other tables carry the identical column — so deleting the cascade from `annotations`
    // left the assertion green on a schema that no longer removes the user's highlights. Take the one
    // DDL block the claim is about.
    const ddl = store.slice(store.indexOf('CREATE TABLE IF NOT EXISTS annotations'))
    const annotations = ddl.slice(0, ddl.indexOf(');'))
    expect(annotations, 'the annotations DDL must be found').toContain('quote TEXT NOT NULL')
    expect(annotations, 'and its item_id must cascade').toMatch(
      /item_id TEXT NOT NULL REFERENCES items\(id\) ON DELETE CASCADE/,
    )
    // 🪤 A CASCADE IN THE DDL IS INERT IN SQLITE UNLESS THE PRAGMA IS ON — the claim rests on both.
    // Scoped to the CONNECTION SETUP, because the file also toggles the pragma off and back on inside
    // migration blocks: a match anywhere passed while the connect-time enforcement was gone, which is
    // the state where nothing cascades during normal use. (Third hole of this exact shape in this one
    // rail — a backend repeats its idioms, so a cross-language pin has to name the block it means.)
    const connect = store.slice(store.indexOf('self.db = sqlite3.connect('))
    expect(connect.slice(0, 400), 'the connection itself enables foreign keys')
      .toMatch(/PRAGMA foreign_keys=ON/)
  })

  it('claims the stored file, and the handler unlinks it', () => {
    expect(web('pages/knowledge/KnowledgeDetail.tsx')).toMatch(
      /The file stored in your library is deleted too\./,
    )
    const h = py('dashboard/handlers/knowledge.py')
    const del = h.slice(h.indexOf('async def delete_item'), h.indexOf('async def delete_item_annotation'))
    expect(del, 'the tracked paths').toMatch(/victims = \[item\.get\("file_path"\), item\.get\("thumbnail_path"\)\]/)
    expect(del, 'and the derived media artifacts').toMatch(/files_root\.glob\(f"\{item_id\}\.\*"\)/)
    // Why the copy says "in your library" rather than "the file": the unlink is fenced to that dir, so
    // an indexed file living elsewhere is NOT removed and the sentence must stay true either way.
    expect(del, 'fenced to the library files dir').toMatch(/files_root = Path\(knowledge_files_dir\(\)\)\.resolve\(\)/)
  })

  it('both clauses are conditional — no claim about what this item does not have', () => {
    const ui = web('pages/knowledge/KnowledgeDetail.tsx')
    expect(ui, 'highlights clause gated on there being some').toMatch(/annotations\.length > 0\s*\n?\s*\?/)
    expect(ui, 'file clause gated on there being a file').toMatch(/item\.file_path \?/)
    // Same supply check as the task dialog: two composed clauses that the body never interpolates are
    // two sentences nobody reads.
    expect(ui, 'and the body composes both').toMatch(
      /body: `This removes it from the knowledge base\.\$\{highlights\}\$\{stored\}`/,
    )
  })
})

describe('no destructive dialog names its subject NOWHERE', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  /** The balanced `{…}` object literal starting at `i`. */
  function objectAt(src: string, i: number): string {
    let depth = 0
    for (let j = i; j < Math.min(src.length, i + 1600); j++) {
      if (src[j] === '{') depth++
      else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(i, j + 1) }
    }
    return src.slice(i, i + 1600)
  }

  it('every hand-rolled danger dialog identifies what it is about', () => {
    // 🪤 #1608's ratchet was keyed on `confirmDelete(` callers, so a HAND-ROLLED `confirm({danger:true})`
    // was outside its population entirely — which is how the task delete kept asking about "this task"
    // for another five cycles. Keyed here on the danger dialogs themselves.
    //
    // The bar is "title OR body identifies it", not "the title interpolates": seven of the eight
    // subject-less TITLES name their subject in the BODY instead (`"${s.title}" and its history will be
    // permanently removed.`), which is just as clear to a reader and must not be reported.
    // 🪤 AND THE RULE IS "A DIALOG OPENED FROM A LIST", which the first draft missed by reporting
    // three surfaces that each have exactly ONE subject: a project cockpit, a file viewer, a run
    // detail. There "this run" / "this file" is the page you are on, named in its own header, and the
    // bodies are already the best in the app ("Work already written to the workspace is kept. Pause
    // instead…"). Interpolating a name there would add nothing.
    //
    // Their exemption rests on surface CARDINALITY, which no regex can verify — so the honest guard is
    // this fixed list plus the assertion below that each still holds exactly one such dialog. A second
    // one appearing in any of them changes the count and fails, which is when the judgement needs
    // re-making by a person.
    const SINGLE_SUBJECT_SURFACES = [
      'pages/code/CodeCockpitPage.tsx',      // one project per cockpit
      'pages/files/browse/FileViewer.tsx',   // one open file
      'pages/workflows/WorkflowRunDetail.tsx',  // one run
    ]
    const anonymous: string[] = []
    for (const abs of walk(SRC)) {
      if (SINGLE_SUBJECT_SURFACES.includes(abs.replace(SRC + '/', ''))) continue
      const src = strip(readFileSync(abs, 'utf8'))
      for (const m of src.matchAll(/confirm\(\s*\{/g)) {
        const obj = objectAt(src, src.indexOf('{', m.index!))
        if (!/danger:\s*true/.test(obj)) continue
        const titleAndBody = (obj.match(/title:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
          + (obj.match(/body:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
        if (!titleAndBody.includes('${')) {
          anonymous.push(`${abs.replace(SRC + '/', '')}: ${(obj.match(/title: ([^\n]*)/)?.[1] ?? '?').slice(0, 46)}`)
        }
      }
    }
    expect(anonymous, 'a dialog that identifies its subject in neither title nor body').toEqual([])
  })

  it('each single-subject surface still holds exactly one such dialog', () => {
    // The vacuity floor for the exemption above: if one of these grows a second anonymous danger
    // dialog, its "the subject is the page" reason no longer covers both.
    const counts: Record<string, number> = {}
    for (const rel of ['pages/code/CodeCockpitPage.tsx', 'pages/files/browse/FileViewer.tsx',
      'pages/workflows/WorkflowRunDetail.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      let n = 0
      for (const m of src.matchAll(/confirm\(\s*\{/g)) {
        const obj = objectAt(src, src.indexOf('{', m.index!))
        if (!/danger:\s*true/.test(obj)) continue
        const titleAndBody = (obj.match(/title:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
          + (obj.match(/body:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
        if (!titleAndBody.includes('${')) n++
      }
      counts[rel] = n
    }
    expect(counts).toEqual({
      'pages/code/CodeCockpitPage.tsx': 1,
      'pages/files/browse/FileViewer.tsx': 1,
      'pages/workflows/WorkflowRunDetail.tsx': 1,
    })
  })
})

describe('two more bodies: one corrected, one confirmed', () => {
  it('the bulk dismiss says what survives, and this page is what keeps them', () => {
    // 🪤 The irreversibility half was already true (restore 409s on anything not FILTERED, and nothing
    // un-dismisses). What was missing is that dismissal is not deletion: this page's own `handled` filter
    // includes dismissed items, so they stay readable one tab away. Both halves are now asserted TOGETHER,
    // because the copy is only honest as a pair.
    const ui = web('pages/inbox/InboxPage.tsx')
    expect(ui, 'the whole sentence').toContain(
      'There is no undo — but they stay readable under Handled.',
    )
    expect(ui, "and the filter that makes the second half true").toMatch(
      /filter === 'handled' \? \(it\.status === 'handled' \|\| it\.status === 'sent' \|\| it\.status === 'dismissed'\)/,
    )
  })

  it('the no-undo half is still true — restore refuses a dismissed item', () => {
    const h = py('dashboard/handlers_inbox.py')
    const restore = h.slice(h.indexOf('async def api_inbox_restore'))
    expect(restore.slice(0, 1400), 'only a FILTERED item can be restored').toMatch(
      /if item\.status != ItemStatus\.FILTERED\.value:[\s\S]{0,120}status=409/,
    )
    // If an un-dismiss path ever appears, the copy owes the user that instead — this fails first.
    expect(web('pages/inbox/InboxDetail.tsx'), 'the only Restore control is for the filtered case')
      .toMatch(/A second-opinion check flagged this claim/)
  })

  it('the chat delete really does take the history it promises', () => {
    expect(web('pages/ChatPage.tsx')).toContain('and its history will be permanently removed.')
    const h = py('dashboard/chat_handlers.py')
    const del = h.slice(h.indexOf('async def api_chat_session_delete'))
    expect(del.slice(0, 6000), 'the on-disk artifacts are purged').toMatch(
      /conversation_log\.delete_session\(history_key\)/,
    )
    // 🪤 And the reason the claim is worth pinning rather than assuming: the handler had to grow a
    // disk-purge fallback because "Delete" used to 404 for a non-resident session, leaving its JSONL on
    // disk and letting the chat RESURRECT on reopen — the exact opposite of what this sentence promises.
    expect(del.slice(0, 6000)).toMatch(/letting it resurrect on reopen/)
  })
})

describe('the project delete, and the two workflow bodies', () => {
  it('says the LISTS go and the TASKS stay — which is the way round the code works', () => {
    // 🪤 It said "task lists detached", which implies the lists survive unattached. They do not:
    // `delete_project` unlinks each list file. What survives is the TASKS — separate files that become
    // orphaned-by-list. The reassurance was pointed at the wrong noun.
    const ui = web('pages/projects/ProjectsSection.tsx')
    expect(ui).toContain('task lists are removed — the tasks themselves stay')
    expect(ui, 'the misleading word must not come back').not.toContain('task lists detached')
    const h = pyMethod(py('tasks/hierarchy.py'), '    def delete_project')
    expect(h, 'list files are unlinked, not detached').toMatch(
      /self\._list_path\(tl\.id\)\.unlink\(missing_ok=True\)/,
    )
    expect(h, 'and the tasks are explicitly NOT the provider\'s job here').toMatch(
      /the task provider owns task deletion/,
    )
  })

  it('the workspace-untouched half holds for the dialog that says it', () => {
    // The rmtree DOES take the project's own `worktrees/` — but those exist only for bound loops/code
    // work, and that case is refused without ?force and gets its own dialog. So this sentence is true
    // wherever it is shown. Both halves pinned, because the guard is what makes the copy safe.
    const h = pyMethod(py('tasks/hierarchy.py'), '    def delete_project')
    expect(h, 'the project dir goes wholesale').toMatch(/shutil\.rmtree\(self\._project_dir\(project_id\)/)
    const handler = py('tasks/hierarchy_handlers.py')
    expect(handler, 'bound work is refused without force').toMatch(
      /rmtree its worktrees out\s*\n?\s*#?\s*from under git/,
    )
    expect(web('pages/projects/ProjectsSection.tsx'), 'and the force path has its own warning')
      .toMatch(/STOPS and REMOVES any bound loops/)
  })

  it('a workflow run really does keep its own spec copy', () => {
    expect(web('pages/workflows/WorkflowsListPage.tsx')).toContain(
      'Existing runs keep their own copy of the spec and are unaffected.',
    )
    expect(py('workflows/service.py'), 'the run persists its own spec at start')
      .toMatch(/store\.write_spec\(run\.id, spec\)/)
  })

  it('cancel stops the run without deleting what finished', () => {
    expect(web('pages/workflows/WorkflowRunDetail.tsx')).toContain('In-flight steps are stopped. Completed work is kept.')
    const cancel = py('workflows/service.py').slice(
      py('workflows/service.py').indexOf('def cancel_run('),
      py('workflows/service.py').indexOf('async def delete_run('),
    )
    // The distinction the copy rests on: cancel requests a terminal status; DELETING a run is a separate,
    // explicit call. If cancel ever started removing rows, "Completed work is kept" would be false.
    expect(cancel, 'cancel does not delete').not.toMatch(/delete|rmtree|unlink/)
    expect(py('workflows/service.py'), 'deletion is its own deliberate operation')
      .toMatch(/async def delete_run\(/)
  })
})

describe('four more bodies, all already true — pinned so they stay that way', () => {
  it('the tag delete really re-parents children instead of deleting the branch', () => {
    // 🔑 THE FRAGILE ONE. This body promises "Its N nested tags become top-level rather than being
    // deleted", and nothing in `delete_tag` re-parents anything — the promise rests entirely on
    // `ON DELETE SET NULL` on the tags self-FK, which SQLite honours only with the pragma on. Same
    // two-part dependency as the annotations cascade, so both parts are pinned: lose either and a
    // parent delete silently destroys the branch beneath it while the dialog says it will not.
    const ui = web('pages/knowledge/TagManager.tsx')
    expect(ui).toContain('become top-level rather than being deleted')
    const store = py('knowledge/store.py')
    const ddl = store.slice(store.indexOf('CREATE TABLE IF NOT EXISTS tags'))
    expect(ddl.slice(0, ddl.indexOf(');')), 'the self-FK sets null').toMatch(
      /parent_id INTEGER REFERENCES tags\(id\) ON DELETE SET NULL/,
    )
    const connect = store.slice(store.indexOf('self.db = sqlite3.connect('))
    expect(connect.slice(0, 400), 'and the connection enforces foreign keys').toMatch(/PRAGMA foreign_keys=ON/)
    // And the untag half of the same sentence.
    expect(ui).toMatch(/This removes the tag from \$\{t\.usage_count\} item/)
    expect(pyMethod(store, '    def delete_tag'), 'the docstring states the same contract')
      .toMatch(/Children are re-parented to root rather than deleted/)
  })

  it('the shelf delete leaves the items alone', () => {
    expect(web('pages/knowledge/KnowledgeListPage.tsx')).toContain(
      'The shelf goes away. The items on it stay in your library.',
    )
    const del = pyMethod(py('knowledge/store.py'), '    def delete_collection')
    expect(del, 'membership rows and the collection row go').toMatch(/DELETE FROM collection_items/)
    expect(del, 'and the items table is never touched').not.toMatch(/DELETE FROM items/)
  })

  it('the skill delete really removes the directory', () => {
    expect(web('pages/skills/SkillInspector.tsx')).toContain('This removes it from disk. This cannot be undone.')
    expect(pyMethod(py('skills/loader.py'), '    def delete_skill'), 'rmtree, not a registry flag')
      .toMatch(/shutil\.rmtree\(skill_dir\)/)
  })

  it('the Ollama delete really reaches the host — across the app boundary', () => {
    // 🪤 A CROSS-REPO CLAIM, and the reason it is worth pinning: nothing in core implements this. The
    // handler calls `catalog.delete_model`, and the only implementation lives in the REMOVABLE
    // `ollama-models` app bundle, which is exactly where provider logic is supposed to live. A grep of
    // core alone says the promise is unimplemented; it is not.
    expect(web('pages/settings/OllamaModelManager.tsx')).toContain(
      "This frees disk on the Ollama host and can't be undone.",
    )
    const h = py('dashboard/handlers/providers.py')
    expect(h, 'core delegates to the catalog').toMatch(/await catalog\.delete_model\(model\)/)
    expect(h, 'and only for a provider whose catalog can do it').toMatch(/isinstance\(catalog, ModelManager\)/)
  })
})

describe('the stop-project dialog, and the file delete', () => {
  it('warns that a running task loses its worktree — the half that costs work', () => {
    // 🔴 Stop is TERMINAL and its teardown force-removes every task worktree. The old body reassured
    // ("Work already written to the workspace is kept") without saying that in-flight work is discarded,
    // which is the one thing a terminal action owes the user.
    const ui = web('pages/code/CodeCockpitPage.tsx')
    expect(ui).toContain('a task still running loses its own worktree and branch')
    expect(ui, 'and it now says how kept work got there').toContain('already merged into your workspace is kept')
    // The mechanism, both halves. `--force` discards uncommitted work; `-D` takes the branch even
    // unmerged, so committed-but-unmerged work goes too.
    const wt = pyMethod(py('loop/worktree.py'), 'def cleanup_all')
    expect(wt, 'the worktree is force-removed').toMatch(/"worktree", "remove", "--force"/)
    expect(wt, 'and its branch force-deleted').toMatch(/"branch", "-D", branch_name\(name\)/)
    // …and the reason the "kept" half is true: a FINISHED task is merged back first.
    expect(py('loop/kinds/sdlc.py'), 'a finished task merges into the workspace')
      .toMatch(/worktree\.merge_worktree\(ws, tid/)
  })

  it('stop really is terminal, which is why the warning matters', () => {
    const stop = pyMethod(py('loop/manager.py'), 'async def stop')
    expect(stop, 'teardown then a terminal status').toMatch(/_teardown\(svc, loop_id\)[\s\S]{0,200}LoopStatus\.STOPPED/)
    expect(pyMethod(py('loop/manager.py'), 'async def _teardown'), 'and teardown is what cleans worktrees')
      .toMatch(/worktree\.cleanup_all\(loop\.workspace_dir/)
  })

  it('the folder delete really recurses', () => {
    const ui = web('pages/files/FilesSection.tsx')
    expect(ui).toContain('This deletes the folder and all its contents. This cannot be undone.')
    // Conditional on `is_dir`, so a file does not get the folder sentence.
    expect(ui).toMatch(/entry\.is_dir \? 'This deletes the folder and all its contents/)
    const h = py('dashboard/handlers/files.py')
    const del = h.slice(h.indexOf('async def api_file_delete'))
    expect(del.slice(0, 2200), 'a directory is rmtree-d').toMatch(/shutil\.rmtree\(path\)/)
    expect(del.slice(0, 2200), 'and a root is refused').toMatch(/refusing to delete a root directory/)
  })
})

describe('the conflict-resolve body, per choice', () => {
  const ui = () => web('pages/settings/DurabilityPanel.tsx')

  it('promises reversibility ONLY for the choice that earns it', () => {
    // 🔴 It said "The version you don't pick stays in the shared store" for all three choices. Only
    // `keep_local` discards the REMOTE row — the one the shared store actually holds. The other two
    // discard THIS machine's row, which no store keeps.
    expect(ui()).toMatch(/choice === 'keep_local'/)
    expect(ui(), 'the reversible branch names the other side').toContain(
      "The other machine's version stays in the shared store, so you can still decide differently from that side.",
    )
    expect(ui(), 'and the destructive branch says what is gone').toContain(
      "That copy is not kept anywhere else — only a snapshot has it.",
    )
    expect(ui(), 'the unconditional promise must not come back').not.toContain(
      "The version you don't pick stays in the shared store",
    )
  })

  it('the backend pushes nothing, which is what makes the keep_local half true', () => {
    const mod = py('durability/conflict_resolve.py')
    expect(mod, 'resolving is a LOCAL write').toMatch(/Nothing is pushed from here/)
    // …and the detector re-holds the id, so the other side really can still decide.
    // 🪤 Bounded by the NEXT bullet, not by a character count — the span is longer than the 400 I first
    // guessed, which is the same mistake `pySource`'s own docstring exists to prevent.
    const keepLocal = pyBetween(mod, '``keep_local``', '``take_remote``')
    expect(keepLocal, 'the keep_local bullet must be found').toMatch(/three shas are unchanged/)
    expect(keepLocal, 'the divergence is detected and held again').toMatch(/HOLDS the\s+id again/)
  })

  it('take_remote and accept_proposal really do overwrite this machine\'s row', () => {
    const mod = py('durability/conflict_resolve.py')
    expect(pyBetween(mod, '``take_remote``', '``accept_proposal``'), 'take_remote converges onto the remote sha')
      .toMatch(/local becomes the remote sha/)
    const proposal = mod.slice(mod.indexOf('``accept_proposal``'))
    expect(proposal.slice(0, proposal.indexOf('"' + '""')), 'accept_proposal writes a third sha')
      .toMatch(/local becomes a THIRD sha/)
    // The write is whole-entry substitution of the chosen row — nothing archives the old one.
    expect(pyMethod(mod, 'def _write_chosen_row'), 'the old row is simply replaced')
      .toMatch(/Substitute ``row`` for ``entity_id``/)
  })

  it('a resolved record is never silently re-applied — the other half of "decide again"', () => {
    // The copy says you decide again from the OTHER SIDE, not by re-resolving here, and the backend
    // enforces exactly that.
    expect(py('durability/conflict_resolve.py')).toMatch(
      /``already_resolved``\s*the record was reviewed already \(never re-applied silently\)/,
    )
  })
})
