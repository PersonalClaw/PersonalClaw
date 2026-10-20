import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { DependencyAnalysis, TaskGraphData, TaskItem } from '../../lib/api'

const taskGraph = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      taskGraph: (...args: unknown[]) => taskGraph(...args),
    },
  }
})

import { TaskGraph } from './TaskGraph'

const task = (id: string, title: string, status: string): TaskItem => ({
  id,
  title,
  status,
  priority: 'medium',
})

const analysis = (over: Partial<DependencyAnalysis> = {}): DependencyAnalysis => ({
  completion_pct: 31,
  leaf_task_ids: [],
  root_task_ids: [],
  critical_path: [],
  cycles: [],
  bottleneck_tasks: [],
  ...over,
})

const graph = (tasks: TaskItem[], over: Partial<DependencyAnalysis> = {}): TaskGraphData => ({
  tasks,
  edges: [],
  analysis: analysis(over),
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('a scoped dependency graph names the population behind every metric', () => {
  it('computes completion from the drawn tasks and keeps library analysis explicitly labelled', async () => {
    const drawn = [
      task('scoped-open', 'Deliver the gallery', 'open'),
      task('scoped-done', 'Confirm the print proof', 'done'),
    ]
    const library = [
      ...drawn,
      task('off-critical', 'Run the compile checks', 'done'),
      task('off-bottleneck', 'Coordinate the whole project', 'open'),
      task('off-cycle-a', 'Cycle task A', 'open'),
      task('off-cycle-b', 'Cycle task B', 'open'),
    ]
    taskGraph.mockResolvedValue(graph(library, {
      critical_path: ['off-critical'],
      bottleneck_tasks: [{ id: 'off-bottleneck', dependents: 4 }],
      cycles: [['off-cycle-a', 'off-cycle-b']],
    }))

    const { container } = render(<TaskGraph tasks={drawn} onOpen={() => {}} />)

    expect(await screen.findByText('50% complete')).toBeInTheDocument()
    expect(screen.queryByText('31% complete')).toBeNull()
    expect(screen.getByText('Critical path: 1 in library, 0 in view')).toBeInTheDocument()

    const bottlenecks = screen.getByText('Bottlenecks: 1 in library, 0 in view')
    expect(bottlenecks).toHaveAttribute('title', 'Coordinate the whole project (4)')
    expect(bottlenecks.getAttribute('title')).not.toContain('off-bottleneck')

    expect(screen.getByText('1 cycle detected in library, 0 in view')).toBeInTheDocument()
    expect(screen.queryByText(/critical-path tasks are ringed below/i)).toBeNull()
    expect(container.querySelectorAll(
      '[data-dag-node] .dag-node-hit > rect[width="210"][stroke="var(--color-primary)"]',
    )).toHaveLength(0)
  })

  it('uses the server completion when the drawn set is the whole library', async () => {
    const library = [
      task('all-open', 'Open task', 'open'),
      task('all-done', 'Done task', 'done'),
    ]
    taskGraph.mockResolvedValue(graph(library))

    render(<TaskGraph tasks={library} onOpen={() => {}} />)

    expect(await screen.findByText('31% complete')).toBeInTheDocument()
    expect(screen.queryByText('50% complete')).toBeNull()
  })

  it('rings and explains only the critical-path tasks that are actually in view', async () => {
    const drawn = [
      task('visible-critical', 'Visible critical task', 'open'),
      task('visible-ordinary', 'Visible ordinary task', 'open'),
    ]
    const library = [...drawn, task('off-critical', 'Off-scope critical task', 'open')]
    taskGraph.mockResolvedValue(graph(library, {
      critical_path: ['off-critical', 'visible-critical'],
    }))

    const { container } = render(<TaskGraph tasks={drawn} onOpen={() => {}} />)

    expect(await screen.findByText('Critical path: 2 in library, 1 in view')).toBeInTheDocument()
    expect(screen.getByText(/critical-path tasks are ringed below/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Visible critical task.*on the critical path/i }))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Visible ordinary task/i }))
      .not.toHaveAccessibleName(/on the critical path/i)
    expect(container.querySelectorAll(
      '[data-dag-node] .dag-node-hit > rect[width="210"][stroke="var(--color-primary)"]',
    )).toHaveLength(1)
  })
})
