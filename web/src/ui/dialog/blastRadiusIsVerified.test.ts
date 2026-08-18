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

describe('three more bodies, checked against their handlers', () => {
  it('the provider delete states the selections it drops, and the handler drops them', () => {
    const ui = web('pages/settings/ModelBackends.tsx')
    expect(ui, 'the clause exists').toContain('Any use case set to one of its models loses that selection.')
    expect(ui, 'and the body composes it').toMatch(
      /body: `Models it provides will no longer be available\.\$\{selections\}\$\{key\}`/,
    )
    const h = py('dashboard/handlers/providers.py')
    const del = h.slice(h.indexOf('async def api_provider_delete'), h.indexOf('async def api_provider_test'))
    expect(del, 'the handler drops the active-model refs').toMatch(/_drop_provider_active_models\(name\)/)
    // …and drops them for EVERY use case, which is why the copy does not name one.
    const dropper = h.slice(h.indexOf('def _drop_provider_active_models'))
    expect(dropper.slice(0, 700), 'across every use case').toMatch(/for use_case, refs in list\(active\.items\(\)\)/)
  })

  it('the credential clause is conditional AND true — the handler never touches the store', () => {
    const ui = web('pages/settings/ModelBackends.tsx')
    expect(ui, 'gated on a credential actually being stored').toMatch(
      /provider\.credential_status === 'ok' \? ' Its saved credential stays in the store\.'/,
    )
    const h = py('dashboard/handlers/providers.py')
    const del = h.slice(h.indexOf('async def api_provider_delete'), h.indexOf('async def api_provider_test'))
    // 🪤 THE CLAIM IS AN ABSENCE, so it is pinned as one: if the handler ever starts deleting the
    // credential, this fails and the sentence has to be re-written rather than left reassuring people
    // about a key that is gone.
    expect(del, 'no credential deletion in the delete path').not.toMatch(
      /credential|keyring|secret|delete_key|remove_credential/i,
    )
  })

  it('the schedule delete really removes the run history it promises', () => {
    expect(web('pages/schedule/ScheduleDetail.tsx')).toContain('Its run history is removed too.')
    const h = py('dashboard/handlers/triggers.py')
    const del = h.slice(h.indexOf('if request.method == "DELETE":'))
    expect(del.slice(0, 3000), 'the second half of the delete').toMatch(
      /await _runs_store\(\)\.delete_for_job\(raw\)/,
    )
  })

  it('the MCP remove needs no extra clause — checked, and a hypothesis killed', () => {
    // 🪤 I EXPECTED A CROSS-APP BLAST RADIUS HERE AND WAS WRONG. The DELETE branch writes two stores,
    // `_PERSONALCLAW_MCP_JSON` and `_GLOBAL_MCP_JSON` — and "global" reads like a shared file, next to a
    // `_CC_GLOBAL_JSON = ~/.claude.json` in the same module. But `_canonical_mcp_json()` resolves to
    // `config_dir() / "mcp.json"`, i.e. PersonalClaw's own home, and the delete never touches the
    // claude-code file. So "Its tools will no longer be available." is complete, and this test exists to
    // keep it complete: if the delete ever reaches the CC config, the copy owes the user that fact.
    const h = py('dashboard/handlers/mcp.py')
    expect(h, 'the canonical store is PersonalClaw-scoped').toMatch(
      /def _canonical_mcp_json[\s\S]{0,200}?return config_dir\(\) \/ "mcp\.json"/,
    )
    // 🪤 SLICED TO THE WHOLE BRANCH, NOT A FIXED WINDOW. The first version took
    // `.slice(0, 1500)` from the DELETE branch, and the store loop sits ~27 lines in — just past 1500
    // characters — so adding `_CC_GLOBAL_JSON` to the real loop PASSED. (My first guess at why was
    // wrong too: I assumed an earlier handler's branch had been matched, but there is exactly one in
    // this module. The window was simply too short.) A character budget is not a scope; bound the slice
    // by the code that ENDS the region.
    const fn = h.slice(h.indexOf('async def api_mcp_server_detail'))
    const del = fn.slice(fn.indexOf('if request.method == "DELETE":'), fn.indexOf('# PUT — register or update'))
    expect(del, 'the delete branch must be found').toMatch(/for store in \(/)
    expect(del, 'and it does not write the claude-code config').not.toMatch(/_CC_GLOBAL_JSON/)
    expect(web('pages/tools/ToolsPage.tsx'), 'so the body stays as it is')
      .toContain('Its tools will no longer be available.')
  })
})
