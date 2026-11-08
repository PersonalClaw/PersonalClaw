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
//   dashboard     `widgets/TasksWidget`        266px of 434  — 1.6x  (gone since, with Home's Tasks band)
//
// All three with `title: null`, so the second half of what the user wrote was unreachable to a sighted
// phone user — while the row's accessible NAME already carried the whole thing (the status is
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

describe('every place a task is listed hands over its full title', () => {
  // A title is an agent's line and carries markdown, so the row draws it rendered (`MarkdownLine`)
  // and its tooltip is the same line as plain text (`previewLine`): the two read one value, so the
  // tooltip names the task the row shows, with no mark in it.
  const RECOVERED = (close: string) =>
    new RegExp(String.raw`title=\{previewLine\(t\.title\)\}><MarkdownLine text=\{t\.title\} /></${close}>`)

  it('the list row', () => {
    expect(LIST).toMatch(/block truncate text-\[0\.9375rem\]/)
    expect(LIST).toMatch(RECOVERED('span'))
  })

  it('the DAG node', () => {
    expect(DAG).toMatch(/truncate text-\[0\.8125rem\] leading-tight/)
    expect(DAG).toMatch(RECOVERED('div'))
  })

  it('the row name still carries the title AND the status, as plain text', () => {
    // The earlier fix, which is why AT was the complete reader. It must survive this change.
    expect(LIST).toMatch(/<RowHitTarget label=\{`\$\{previewLine\(t\.title\)\} — \$\{sm\.label\}`\} \/>/)
  })

  it('the task panel passes its rendered title a plain tooltip', () => {
    // `ui/SidePanel`'s shared <h2> takes its tooltip from a string title, or, for a rendered one,
    // from the `tooltip` its caller passes.
    const panel = strip(read('ui/SidePanel.tsx'))
    expect(panel, 'the shared panel title still exists').toMatch(/data-type="title-l" className="text-on-surface truncate/)
    expect(panel).toMatch(/title=\{tooltip \?\? \(typeof title === 'string' \? title : undefined\)\}/)
    expect(LIST).toMatch(/title=\{<MarkdownLine text=\{open\.title\} \/>\} tooltip=\{previewLine\(open\.title\)\}/)
  })
})
