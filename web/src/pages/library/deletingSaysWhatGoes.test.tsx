/** Deleting an intent or a report's write-up says what goes, in the approved words.
 *
 *  An intent's findings are kept by value and exist nowhere else, so its delete counts them, and never
 *  overstates the loss: one that found nothing loses only itself. A goal is a sentence, so the title
 *  takes it up to a line's length. A write-up's delete names the report that keeps running. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { answers, resetGateway, row } from '../../test/libraryHarness'

const asked: { title: string; body: unknown }[] = []
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<typeof import('../../ui/dialog')>()),
  confirmDestructive: (title: string, body: unknown) => { asked.push({ title, body }); return Promise.resolve(false) },
  confirm: (o: { title: string; body: unknown }) => { asked.push(o); return Promise.resolve(false) },
}))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { deleteIntent } = await import('./setup/SetupPanel')
const { moreRows } = await import('./libraryActions')

const INTENT = { id: 'homelab', goal: 'Anything that could improve my homelab', enabled: true, revision: 'r1' }
const WEEKLY = { id: 'rpt-1a2b3c4d', name: 'Weekly homelab', schedule: { kind: 'cron', cron_expr: '0 8 * * 1' } }

beforeEach(() => { resetGateway(); asked.length = 0 })

describe('deleting an intent says what goes', () => {
  it.each([
    [14, 'This deletes the intent and the 14 findings it kept. You can\'t undo this.'],
    [1, 'This deletes the intent and the 1 finding it kept. You can\'t undo this.'],
    [0, 'This deletes the intent. It hasn\'t found anything yet. You can\'t undo this.'],
  ])('🔑 with %i findings it says so', async (count, body) => {
    await deleteIntent({ ...INTENT, outcome_count: count } as never)
    expect(asked).toEqual([{ title: 'Delete “Anything that could improve my homelab”?', body }])
  })

  it('🔑 says its report stops, and that the last one stays', async () => {
    await deleteIntent({ ...INTENT, goal: 'Cheap ways to cut the power bill', outcome_count: 22 } as never, WEEKLY as never)
    expect(asked[0]).toEqual({
      title: 'Delete “Cheap ways to cut the power bill”?',
      body: 'This deletes the intent and the 22 findings it kept. Its weekly report stops. The last one stays in your library. You can\'t undo this.',
    })
  })

  it('a sentence-long goal is cut to a line in the title', async () => {
    const goal = 'Anything that could improve my homelab, especially storage reliability and backup verification and power use'
    await deleteIntent({ ...INTENT, goal, outcome_count: 3 } as never)
    expect(asked[0].title.length).toBeLessThan(goal.length)
    expect(asked[0].title).toMatch(/^Delete “Anything that could improve my homelab/)
  })
})

describe("deleting a report's write-up names the report", () => {
  it('🔑 asks in the approved title, and the body names the report that keeps running', async () => {
    answers.libraryBulk = { results: [] }
    const inputs = {
      navigate: vi.fn(), writeUps: new Map([['rpt-1a2b3c4d', '3f9a0c1d2e4b']]), projects: [], chatTitle: () => '',
      pickChat: vi.fn(), pickProject: vi.fn(),
    }
    await moreRows(row('report:rpt-1a2b3c4d', 'Weekly perf digest'), inputs).find((r) => r.label === 'Delete write-up…')!.onSelect()
    expect(asked[0]).toMatchObject({ title: 'Delete this write-up?', body: '“Weekly perf digest” keeps running and writes a new one next time.' })
  })
})
