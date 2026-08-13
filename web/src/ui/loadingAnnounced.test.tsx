import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { LoadingStatus, ListSkeleton, FormSkeleton, CardGridSkeleton } from './ListScaffold'

// ── A live region with no text announces nothing, however well it is marked up ────────────────────
//
// Cycle 142 could not drive this lens and recorded it as UNMEASURED rather than passing. Driven here
// properly — a fresh context per route (cold `sessionStorage`), every `/api/**` response held back,
// and a 150ms poll for the FIRST frame at which a skeleton exists:
//
//   surface        before                                          after
//   #/tasks        region present, aria-label="Loading", SPOKEN=[]  SPOKEN=["Loading…"]
//   #/knowledge    region present, aria-label="Loading", SPOKEN=[]  SPOKEN=["Loading…"]
//   #/artifacts    no skeleton rendered in this dev home            —
//   settings/apps  no skeleton rendered in this dev home            —
//
// 🔑 THE MARKUP WAS ALREADY RIGHT, WHICH IS WHY NOTHING CAUGHT IT. `role="status" aria-busy="true"
// aria-label="Loading"` passes every rule axe has. But a live region is announced by its CONTENT
// changing, and the content was styled `<div>`s with no text — an `aria-label` is a NAME, not an
// announcement. Perfectly marked up, entirely silent, from the first frame to the moment data arrived.
//
// 🔑 AND THE RESULT-COUNT STATUS DOES NOT COVER IT. `ui/ListControls`' ResultAnnouncement speaks only
// while a query or filter narrows — deliberately, because cycle 121 fixed the opposite defect (an idle
// surface announcing "39 items" unprompted). So a cold first load announced nothing before OR after.
//
// 🪤 ONE CLAIM DIED ON RE-MEASUREMENT. A 4s-per-request hold left an `aria-busy` region up AFTER the
// list had arrived, which looked like a stale-busy lie. With a 1.2s hold it was gone, and with no
// interception at all there were ZERO busy regions 4s after load: the "stale" region was a
// second-wave request still in flight, i.e. an artifact of the probe's own throttle.
//
// 🪤 `#/settings/apps` CARRIED `aria-busy` ON THREE SECTIONS WITH NO ROLE AND NO NAME — not a live
// region at all, and unfindable. It is one region now.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('LoadingStatus is the announcement', () => {
  it('renders sr-only text a live region can announce', () => {
    render(<div role="status" aria-busy="true"><LoadingStatus /></div>)
    const region = screen.getByRole('status')
    expect(region.textContent?.trim()).toBe('Loading…')
    expect(region.querySelector('.sr-only'), 'invisible to sighted users').toBeTruthy()
  })

  it('names what is loading when it knows', () => {
    render(<div role="status" aria-busy="true"><LoadingStatus what="tasks" /></div>)
    expect(screen.getByRole('status').textContent?.trim()).toBe('Loading tasks…')
  })

  it('a status region is NOT named from its content — which is why this had to be text', () => {
    // 🪤 The assumption this test killed: dropping `aria-label` does not move the name onto the
    // sr-only text, because name-from-content does not apply to `status`. The region is unnamed and
    // that is fine — a live region is announced by its CONTENT, and one hard-coded name saying the
    // same word is a second string that can drift from the one people actually hear.
    render(<div role="status" aria-busy="true"><LoadingStatus what="providers" /></div>)
    expect(screen.queryByRole('status', { name: 'Loading providers…' }), 'status takes no name from content').toBeNull()
    expect(screen.getByRole('status').textContent?.trim()).toBe('Loading providers…')
  })
})

describe('every shared skeleton says something', () => {
  const CASES: [string, () => JSX.Element][] = [
    ['ListSkeleton', () => <ListSkeleton rows={2} />],
    ['FormSkeleton', () => <FormSkeleton sections={1} rows={1} />],
    ['CardGridSkeleton', () => <CardGridSkeleton cards={2} />],
  ]
  for (const [name, el] of CASES) {
    it(`${name} mounts a status region with text`, () => {
      const { container } = render(el())
      const region = container.querySelector('[role="status"][aria-busy="true"]')
      expect(region, `${name} must still be a busy status region`).toBeTruthy()
      expect(region!.textContent?.trim(), `${name} announced nothing`).toBe('Loading…')
    })
  }

  it('each passes `what` through to the announcement', () => {
    const { container } = render(<ListSkeleton rows={1} what="prompts" />)
    expect(container.querySelector('[role="status"]')!.textContent?.trim()).toBe('Loading prompts…')
  })

  it('the label is gone, because the announced text is the one that matters', () => {
    // Not "the text became the name" — `status` takes no name from content (see above). The label was
    // a second hard-coded string that could drift from what people hear, so it went.
    const src = readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8')
    expect(src, 'two strings for one region is drift').not.toMatch(/aria-busy="true" aria-label="Loading"/)
  })
})

describe('the census is closed: no busy region without an announcement', () => {
  it('every file with a busy region renders LoadingStatus', () => {
    // THE RATCHET. A new skeleton that marks itself busy and says nothing is exactly the defect this
    // cycle measured; it fails here instead of shipping.
    const offenders = walk(SRC)
      .filter((abs) => /aria-busy="true"/.test(readFileSync(abs, 'utf8')))
      .filter((abs) => !/LoadingStatus/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these mark themselves busy and announce nothing:\n${offenders.join('\n')}`).toEqual([])
  })

  it('finds the population — the scan is not vacuous', () => {
    const files = walk(SRC).filter((abs) => /aria-busy="true"/.test(readFileSync(abs, 'utf8')))
    // At the MEASURED population (cycle 134's rule): ui/ListScaffold + ChatPage + ProvidersPanel +
    // ModelBackends + AppsPanel + NodeInspectorDrawer + WorkspacePanel.
    expect(files.length, 'files with a busy region').toBeGreaterThanOrEqual(7)
  })

  it('the hand-rolled regions kept their specific wording', () => {
    const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    expect(read('pages/ChatPage.tsx')).toMatch(/LoadingStatus what="conversation"/)
    expect(read('pages/settings/ProvidersPanel.tsx')).toMatch(/LoadingStatus what="providers"/)
    expect(read('pages/settings/ModelBackends.tsx')).toMatch(/LoadingStatus what="model providers"/)
    expect(read('pages/workflows/NodeInspectorDrawer.tsx')).toMatch(/LoadingStatus what="node detail"/)
    expect(read('pages/workflows/WorkspacePanel.tsx')).toMatch(/LoadingStatus what="the run’s workspace"/)
    expect(read('pages/settings/AppsPanel.tsx')).toMatch(/LoadingStatus what="app settings"/)
  })
})
