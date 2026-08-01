import { describe, expect, it } from 'vitest'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import type { WorkflowNodeState } from '../../lib/api'

// ── Collapsible containers (WF2 Slice 10b) ──────────────────────────────────
//
// The run view renders one row per node instance, which is right until a spec fans out: the
// `deep-research` template expands to 21 rows and 18 of them are one untaken subgraph. A flat list
// of those is unreadable — the three rows that matter are buried in the ones that did not run.
//
// The interesting behaviour is entirely about WHICH rows a user sees, which is exactly what a
// rendered-component test asserts badly. So the grouping rules live in a pure module and are
// asserted directly.

const n = (path: string, state = 'done'): WorkflowNodeState => ({
  instance_path: path,
  node_id: path.split('.').pop() ?? path,
  state,
})

// The real shape from a `produce-and-audit` run: a branch whose `sweep` case was not taken.
const REAL_RUN: WorkflowNodeState[] = [
  n('root.children[0]'),
  n('root.children[1]'),
  n('root.children[1].cases[deep]', 'skipped'),
  n('root.children[1].cases[deep].children[0]', 'skipped'),
  n('root.children[1].cases[deep].children[1]', 'skipped'),
  n('root.children[1].cases[standard]'),
  n('root.children[2]', 'running'),
]

describe('buildTree', () => {
  it('derives parenthood from the instance PATH', () => {
    // Not from the spec: the path is what the projection carries, and re-parsing the spec tree to
    // render its own rows would put two sources of truth on screen.
    const rows = buildTree(REAL_RUN)
    const branch = rows.find((r) => r.node.instance_path === 'root.children[1]')!
    expect(branch.descendants).toEqual([
      'root.children[1].cases[deep]',
      'root.children[1].cases[deep].children[0]',
      'root.children[1].cases[deep].children[1]',
      'root.children[1].cases[standard]',
    ])
  })

  it('sorts by path so the list reads in spec order', () => {
    const rows = buildTree([n('root.children[2]'), n('root.children[0]'), n('root.children[1]')])
    expect(rows.map((r) => r.node.instance_path)).toEqual([
      'root.children[0]', 'root.children[1]', 'root.children[2]',
    ])
  })

  it('a leaf has no descendants and is not collapsible', () => {
    const rows = buildTree(REAL_RUN)
    const leaf = rows.find((r) => r.node.instance_path === 'root.children[0]')!
    expect(leaf.descendants).toEqual([])
    expect(leaf.collapsible).toBe(false)
  })

  it('a container with ONE child is not collapsible', () => {
    // A disclosure control there costs a click and saves a row, which is a bad trade.
    const rows = buildTree([n('root.body'), n('root.body.children[0]')])
    expect(rows.find((r) => r.node.instance_path === 'root.body')!.collapsible).toBe(false)
  })

  it('handles an empty node list', () => {
    expect(buildTree([])).toEqual([])
  })
})

describe('summarize', () => {
  it('counts by state rather than reducing to a percentage', () => {
    // "18 skipped" says the branch was not taken; "17 done · 1 failed" says where to look. A
    // progress bar says neither.
    const paths = ['root.body#0', 'root.body#1', 'root.body#2']
    const nodes = [n('root.body#0'), n('root.body#1', 'failed'), n('root.body#2', 'skipped')]
    const s = summarize(paths, nodes)
    expect(s.total).toBe(3)
    expect(s.byState).toEqual({ done: 1, failed: 1, skipped: 1 })
  })

  it('breaks a tie toward the most ALARMING state', () => {
    // A subtree that is half done and half failed must not present itself as done.
    const nodes = [n('a', 'done'), n('b', 'failed')]
    expect(summarize(['a', 'b'], nodes).dominant).toBe('failed')
  })

  it('picks the most common state when there is no tie', () => {
    const nodes = [n('a', 'skipped'), n('b', 'skipped'), n('c', 'done')]
    expect(summarize(['a', 'b', 'c'], nodes).dominant).toBe('skipped')
  })

  it('ignores paths that are not in the node list', () => {
    expect(summarize(['nope'], [n('a')]).total).toBe(0)
  })
})

describe('initialCollapsed', () => {
  it('collapses a finished, untaken subgraph', () => {
    // Exactly the noise case: 18 skipped rows of a branch that was not taken.
    const rows = buildTree(REAL_RUN)
    const collapsed = initialCollapsed(rows, REAL_RUN)
    expect(collapsed.has('root.children[1]')).toBe(true)
  })

  it('does NOT collapse a subtree with live work', () => {
    // That is the only thing a user watching a run actually wants on screen.
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'running'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
  })

  it('does NOT collapse a subtree containing a FAILURE', () => {
    // Hiding a failure behind a disclosure control is how a run "silently" fails from the user's
    // point of view.
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'failed'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
  })

  it('does not collapse a scope violation or a blocked node either', () => {
    for (const bad of ['scope_violation', 'blocked']) {
      const nodes = [n('root.body'), n('root.body.children[0]', 'done'), n('root.body.children[1]', bad)]
      expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
    }
  })

  it('DOES collapse a subtree that merely degraded', () => {
    // Degraded is a success with a reason — it does not need the user's eyes the way a failure does.
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'degraded'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(true)
  })
})

describe('visibleRows', () => {
  it('hides everything under a collapsed container', () => {
    const rows = buildTree(REAL_RUN)
    const shown = visibleRows(rows, new Set(['root.children[1]']))
    expect(shown.map((r) => r.node.instance_path)).toEqual([
      'root.children[0]', 'root.children[1]', 'root.children[2]',
    ])
  })

  it('hides a GRANDCHILD under a collapsed grandparent', () => {
    // Checking only the immediate parent would leave a grandchild visible under a collapsed
    // grandparent, which reads as a rendering bug.
    const rows = buildTree(REAL_RUN)
    const shown = visibleRows(rows, new Set(['root.children[1]']))
    expect(shown.some((r) => r.node.instance_path.includes('cases[deep].children'))).toBe(false)
  })

  it('keeps the collapsed container itself visible', () => {
    // It is the row carrying the disclosure control and the summary.
    const shown = visibleRows(buildTree(REAL_RUN), new Set(['root.children[1]']))
    expect(shown.some((r) => r.node.instance_path === 'root.children[1]')).toBe(true)
  })

  it('returns everything when nothing is collapsed', () => {
    const rows = buildTree(REAL_RUN)
    expect(visibleRows(rows, new Set())).toHaveLength(rows.length)
  })
})

describe('summaryLabel', () => {
  it('puts the most alarming state FIRST', () => {
    // The part a user needs to see must not be at the end of the line.
    const nodes = [n('a'), n('b'), n('c', 'failed')]
    const label = summaryLabel(summarize(['a', 'b', 'c'], nodes))
    expect(label.startsWith('1 failed')).toBe(true)
  })

  it('humanizes an underscored state', () => {
    const label = summaryLabel(summarize(['a'], [n('a', 'scope_violation')]))
    expect(label).toBe('1 scope violation')
  })

  it('reads as a single line for the common untaken-branch case', () => {
    const paths = Array.from({ length: 18 }, (_, i) => `p${i}`)
    const nodes = paths.map((p) => n(p, 'skipped'))
    expect(summaryLabel(summarize(paths, nodes))).toBe('18 skipped')
  })
})
