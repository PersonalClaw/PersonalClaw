/** Use in chat names the chats and projects it offers. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { row } from '../../test/libraryHarness'
import { LAST_CHAT_KEY, LAST_PROJECT_KEY, useInChatGroups, type ActionInputs } from './libraryActions'

const inputs = (over: Partial<ActionInputs> = {}): ActionInputs => ({
  navigate: vi.fn(),
  writeUps: new Map([['rpt-1a2b3c4d', '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d']]),
  projects: [{ id: 'p-trip', name: 'Trip planning' }, { id: 'p-home', name: 'Homelab' }],
  chatTitle: (key) => (key === 'chat-1' ? 'Lisbon weekend' : ''),
  pickChat: vi.fn(),
  pickProject: vi.fn(),
  ...over,
})

const labels = (groups: ReturnType<typeof useInChatGroups>) => groups.map((g) => [g.group, g.rows.map((r) => r.label)])

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(LAST_CHAT_KEY, 'chat-1')
  localStorage.setItem(LAST_PROJECT_KEY, 'p-trip')
})

describe('Use in chat names chats and projects', () => {
  it('🔑 a note: Attach to <chat> under Chats, Add to <project> under Projects, and Add to project… always', () => {
    expect(labels(useInChatGroups(row('knowledge:k1', 'Router manual'), inputs()))).toEqual([
      ['', ['New chat with this', 'Ask about this']],
      ['Chats', ['Attach to Lisbon weekend', 'Attach to another chat…']],
      ['Projects', ['Add to Trip planning', 'Add to project…']],
    ])
  })

  it('🔑 an item in another project reads Move to, and one already in it has no quick row', () => {
    const elsewhere = row('artifact:lisbon', 'Lisbon trip plan', { project: { id: 'p-home', name: 'Homelab' } })
    const projects = useInChatGroups(elsewhere, inputs()).find((g) => g.group === 'Projects')!
    expect(projects.rows.map((r) => [r.label, r.hint])).toEqual([['Move to Trip planning', 'Now in Homelab'], ['Add to project…', 'Pick any project']])
    const here = row('artifact:lisbon', 'Lisbon trip plan', { project: { id: 'p-trip', name: 'Trip planning' } })
    expect(useInChatGroups(here, inputs()).find((g) => g.group === 'Projects')!.rows.map((r) => r.label)).toEqual(['Add to project…'])
  })

  it('🔑 a findings log offers Ask about this only', () => {
    expect(labels(useInChatGroups(row('findings:power-bill', 'Cheap ways to cut the power bill'), inputs()))).toEqual([['', ['Ask about this']]])
  })

  it('🔑 a report has no project rows, and attaches its write-up', () => {
    const groups = useInChatGroups(row('report:rpt-1a2b3c4d', 'Weekly perf digest'), inputs())
    expect(groups.map((g) => g.group)).toEqual(['', 'Chats'])
    const nav = vi.fn()
    useInChatGroups(row('report:rpt-1a2b3c4d', 'Weekly perf digest'), inputs({ navigate: nav }))[0].rows[0].onSelect()
    expect(nav).toHaveBeenCalledWith(expect.stringContaining('knowledge%3A0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'))
  })

  it('with no chat you were last in, Chats offers only another chat', () => {
    localStorage.removeItem(LAST_CHAT_KEY)
    expect(useInChatGroups(row('knowledge:k1', 'Router manual'), inputs()).find((g) => g.group === 'Chats')!.rows.map((r) => r.label))
      .toEqual(['Attach to another chat…'])
  })
})
