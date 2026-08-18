import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

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
