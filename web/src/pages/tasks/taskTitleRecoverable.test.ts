import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A task's title is recoverable wherever a task is listed ────────────────────────────────────
//
// The title IS the row — it is what the user wrote and the only thing telling one task from another.
// Measured at 390px on ten real tasks, in every place a task appears:
//
//   list row      `TasksListPage`              254px of 434  — 1.7x
//   DAG node      `TaskGraph`                  182px of 386  — 2.1x  (smallest slot, clips hardest)
//   dashboard     `widgets/TasksWidget`        266px of 434  — 1.6x
//
// All three with `title: null`, so the second half of what the user wrote was unreachable to a sighted
// phone user — while the row's accessible NAME already carried the whole thing (cycle 598 put the status
// in it too), making assistive tech the only complete reader. Nothing clips at 1440px.
//
// 🔑 WHY THESE THREE AND NOT FOUR. The side panel's header also clips a task title (239px of 578, 2.4x),
// but it is `ui/SidePanel`'s shared `<h2>` — used by knowledge, workflows, tasks and more. Fixing it
// improves every panel in the app, which is a bigger and better change than smuggling it in here. Filed.
//
// 🪤 AND THE MEASUREMENT ITSELF NEEDED FIXING FIRST. The probe that produced these numbers originally
// shared ONE browser context across routes, so visiting `?view=dag` persisted `tasks-view=dag` and a
// later visit to `#/tasks` rendered the DAG — attributing 10 DAG hits to the list and inflating
// `tasks-detail` from 2 to 12. A fresh context per route fixed it. The probe was measuring its own side
// effect.

const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

const LIST = strip(read('pages/tasks/TasksListPage.tsx'))
const DAG = strip(read('pages/tasks/TaskGraph.tsx'))
const WIDGET = strip(read('pages/dashboard/widgets/TasksWidget.tsx'))

describe('every place a task is listed hands over its full title', () => {
  it('the list row', () => {
    expect(LIST).toMatch(/block truncate text-\[0\.9375rem\][\s\S]{0,120}title=\{t\.title\}>\{t\.title\}<\/span>/)
  })

  it('the DAG node', () => {
    expect(DAG).toMatch(/truncate text-\[0\.8125rem\] leading-tight[\s\S]{0,160}title=\{t\.title\}>\{t\.title\}<\/div>/)
  })

  it('the dashboard widget row', () => {
    expect(WIDGET).toMatch(/truncate text-on-surface" title=\{t\.title\}>\{t\.title\}<\/span>/)
  })

  it('each title attribute is the rendered value, not a paraphrase', () => {
    // A title that could drift would name a different task than the row shows.
    for (const [name, src] of [['list', LIST], ['dag', DAG], ['widget', WIDGET]] as const) {
      const m = /title=\{t\.title\}>\{t\.title\}</.exec(src)
      expect(m, `${name}: title and text are one expression`).toBeTruthy()
    }
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    // If one ever stops truncating, its `title` becomes noise and this rail should be revisited.
    expect(LIST).toMatch(/block truncate/)
    expect(DAG).toMatch(/truncate text-\[0\.8125rem\]/)
    expect(WIDGET).toMatch(/truncate text-on-surface/)
  })

  it('the row name still carries the title AND the status — the half that already worked', () => {
    // cycle 598's fix, which is why AT was the complete reader. It must survive this change.
    expect(LIST).toMatch(/<RowHitTarget label=\{`\$\{t\.title\} — \$\{sm\.label\}`\} \/>/)
  })

  it('the shared SidePanel title is deliberately untouched — the vacuity floor for the scope note', () => {
    // The header above says this was left alone on purpose. If it ever gains a title, that note is
    // stale; if the element stops existing, the reasoning needs rewriting rather than silently passing.
    const panel = strip(read('ui/SidePanel.tsx'))
    expect(panel, 'the shared panel title still exists').toMatch(/data-type="title-l" className="text-on-surface truncate/)
    expect(panel, 'and still has no title of its own').not.toMatch(/text-on-surface truncate[^>]*title=/)
  })
})
