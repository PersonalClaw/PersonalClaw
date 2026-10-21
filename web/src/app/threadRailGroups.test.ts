import { describe, expect, it } from 'vitest'
import { NO_PROJECT, type ProjectThreads, type Thread } from '../lib/useThreads'
import { NO_PROJECT_LABEL, railGroups, type RailProject } from './threadRail'

// ── The sidebar's project groups (#3814) ─────────────────────────────────────────────────────────
//
// The rail lists the projects with their threads beneath. The groups come in the order the project
// list is served in (the built-in projects first, then by name), each named by its project, and the
// "No project" group comes last, after every project. A project with no thread is still a group:
// the rail says it is empty rather than leaving it out, so an empty project never reads as one that
// failed to load. An archived project is listed only while a thread of it is.

function thread(key: string, projectId: string): Thread {
  return { key, kind: 'chat', id: key.split(':')[1], title: key, projectId, route: `chat/${key.split(':')[1]}`, pip: null, pinned: false, activeAt: 0 }
}

function group(projectId: string, ...keys: string[]): ProjectThreads {
  return { projectId, threads: keys.map((k) => thread(k, projectId)) }
}

const PROJECTS: RailProject[] = [
  { id: 'p-personal', name: 'Personal', status: 'active' },
  { id: 'p-repeat', name: 'Repeatable', status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
  { id: 'p-house', name: 'House move', status: 'active' },
]

/** Each group as `name → [thread key]`, in order. */
function shape(groups: ReturnType<typeof railGroups>): [string, string[]][] {
  return groups.map((g) => [g.name, g.threads.map((t) => t.key)])
}

describe('the rail groups', () => {
  it('follow the project list, each named by its project, with "No project" last', () => {
    const threads = [group(NO_PROJECT, 'chat:loose'), group('p-house', 'chat:boxes'), group('p-garden', 'chat:seeds', 'chat:fence')]
    expect(shape(railGroups(threads, PROJECTS))).toEqual([
      ['Personal', []],
      ['Repeatable', []],
      // Within a group, the named order (theSidebarShowsThreadsInItsNamedOrder.test.ts): here a
      // tie, broken by key.
      ['Garden', ['chat:fence', 'chat:seeds']],
      ['House move', ['chat:boxes']],
      [NO_PROJECT_LABEL, ['chat:loose']],
    ])
    expect(NO_PROJECT_LABEL).toBe('No project')
  })

  it('keeps a project the list does not hold, after the listed ones and before "No project"', () => {
    // A project deleted since its chat was made, or made since the list was read.
    const threads = [group(NO_PROJECT, 'chat:loose'), group('p-gone', 'chat:orphan'), group('p-garden', 'chat:seeds')]
    const groups = railGroups(threads, PROJECTS)
    expect(groups.map((g) => g.projectId)).toEqual(['p-personal', 'p-repeat', 'p-garden', 'p-house', 'p-gone', NO_PROJECT])
    expect(groups.find((g) => g.projectId === 'p-gone')?.threads.map((t) => t.key)).toEqual(['chat:orphan'])
  })

  it('has no "No project" group when every thread has a project', () => {
    const groups = railGroups([group('p-garden', 'chat:seeds')], PROJECTS)
    expect(groups.some((g) => g.projectId === NO_PROJECT)).toBe(false)
  })

  it('lists an archived project only while one of its threads is listed', () => {
    const projects: RailProject[] = [...PROJECTS, { id: 'p-old', name: 'Old flat', status: 'archived' }, { id: 'p-older', name: 'Older flat', status: 'archived' }]
    const groups = railGroups([group('p-old', 'chat:lease')], projects)
    expect(groups.map((g) => g.name)).toContain('Old flat')
    expect(groups.map((g) => g.name)).not.toContain('Older flat')
  })

  it('before the project list is read, names each group by its project id and still puts "No project" last', () => {
    const groups = railGroups([group(NO_PROJECT, 'chat:loose'), group('p-garden', 'chat:seeds')], undefined)
    expect(shape(groups)).toEqual([['p-garden', ['chat:seeds']], [NO_PROJECT_LABEL, ['chat:loose']]])
  })

  it('a project with no name is named by its id, never blank', () => {
    const groups = railGroups([], [{ id: 'p-blank', name: '', status: 'active' }])
    expect(groups.map((g) => g.name)).toEqual(['p-blank'])
  })
})
