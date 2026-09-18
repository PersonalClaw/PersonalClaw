import { describe, expect, it } from 'vitest'
import type { WorkflowNodeState } from '../../lib/api'
import { COL_GAP, NODE_H, NODE_W, ROW_GAP, dagState, isAwaitingHuman, layoutRunDag } from './runDag'
import { buildTree } from './nodeTree'

/** Laying a run out as a DAG (TASKS-SOPS §7 R6 — S61j).
 *
 *  `DagView`'s `onApprove`/`onDeny` had been a declared-but-unwired extension point since it was
 *  written; `WorkflowRunDetail` rendered no graph at all. The layout is a pure module so what lands
 *  where — and which nodes offer which verbs — is assertable without a rendered SVG.
 *
 *  Measured while writing: `buildTree` derives parenthood from the instance PATH, and paths mix `.`
 *  and `[n]` separators (`root.children[1].children[0]`). A parent lookup that split on `.` alone
 *  would find no parent for any bracketed segment and every node would render as a root.
 *
 *  The ITERATION forms carry no bracket at all — `<path>.body#<i>` for a foreach item and
 *  `<path>.body@<n>` for a while iteration (`workflows/tick.py`). Every path in this file is one
 *  the engine actually emits; the fictional `body[n]` this suite used to test with is what let
 *  issue #567 sit here for 85 cycles.
 */

const node = (instance_path: string, state = 'done'): WorkflowNodeState => ({
  instance_path,
  node_id: (instance_path.split(/[.[#@]/).filter(Boolean).pop() ?? instance_path).replace(/]$/, ''),
  state,
})

const NESTED = [
  node('root'),
  node('root.children[0]'),
  node('root.children[1]'),
  node('root.children[1].children[0]'),
  node('root.children[1].children[1]'),
]

describe('layout', () => {
  it('places every node exactly once', () => {
    const out = layoutRunDag(NESTED)
    expect(out.nodes).toHaveLength(NESTED.length)
    expect(new Set(out.nodes.map((n) => n.id)).size).toBe(NESTED.length)
  })

  it('uses the instance PATH as the node id, not the node_id', () => {
    // Two iterations of one foreach node share a `node_id` and would collide into one box, hiding
    // whichever item was stuck.
    const out = layoutRunDag([node('root.body#0'), node('root.body#1')])
    expect(out.nodes.map((n) => n.id)).toEqual(['root.body#0', 'root.body#1'])
  })

  it('columns by DEPTH so nesting reads left to right', () => {
    const out = layoutRunDag(NESTED)
    const byId = Object.fromEntries(out.nodes.map((n) => [n.id, n]))
    expect(byId['root'].x).toBe(0)
    expect(byId['root.children[0]'].x).toBe(NODE_W + COL_GAP)
    expect(byId['root.children[1].children[0]'].x).toBe(2 * (NODE_W + COL_GAP))
  })

  it('stacks siblings DOWNWARD instead of overlapping them', () => {
    // A wide fan-out is the common shape; two nodes at one point is a graph that shows less than
    // the list it replaced.
    const out = layoutRunDag(NESTED)
    const byId = Object.fromEntries(out.nodes.map((n) => [n.id, n]))
    expect(byId['root.children[0]'].y).toBe(0)
    expect(byId['root.children[1]'].y).toBe(NODE_H + ROW_GAP)
  })

  it('draws a containment edge from each node to its nearest placed ancestor', () => {
    const ids = layoutRunDag(NESTED).edges.map((e) => e.id)
    expect(ids).toContain('root->root.children[1]')
    expect(ids).toContain('root.children[1]->root.children[1].children[0]')
  })

  it('finds a parent across BOTH path separators', () => {
    // Paths mix `.` and `[n]`. Splitting on `.` alone would leave every bracketed node parentless,
    // and the graph would render as a row of disconnected roots.
    const out = layoutRunDag([node('root'), node('root.children[3]')])
    expect(out.edges.map((e) => e.id)).toEqual(['root->root.children[3]'])
  })

  it('SKIPS an edge when the ancestor is absent rather than pointing at nothing', () => {
    // A mid-run projection can omit a container that has not started. An edge to a node that is not
    // drawn would render as a line into empty space.
    const out = layoutRunDag([node('root.children[1].children[0]')])
    expect(out.edges).toEqual([])
    expect(out.nodes).toHaveLength(1)
  })

  it('links to the NEAREST placed ancestor, not the textual parent', () => {
    const out = layoutRunDag([node('root'), node('root.children[1].children[0]')])
    expect(out.edges.map((e) => e.id)).toEqual(['root->root.children[1].children[0]'])
  })

  it('animates an edge only into RUNNING work', () => {
    // An animated edge into a finished node reads as progress that is not happening.
    const out = layoutRunDag([node('root'), node('root.children[0]', 'running')])
    expect(out.edges[0].active).toBe(true)
    expect(layoutRunDag([node('root'), node('root.children[0]', 'done')]).edges[0].active).toBe(false)
  })

  it('reports a ZERO size for an empty run', () => {
    // `DagView` renders an SVG of the size it is given; a stray 1px box reads as a rendering bug.
    expect(layoutRunDag([])).toEqual({ nodes: [], edges: [], width: 0, height: 0 })
  })

  it('sizes the canvas to the widest column and the tallest stack', () => {
    const out = layoutRunDag(NESTED)
    expect(out.width).toBe(3 * NODE_W + 2 * COL_GAP)
    expect(out.height).toBeGreaterThanOrEqual(2 * NODE_H + ROW_GAP)
  })

  it('renders the per-item label when a foreach node has one', () => {
    // A twelve-way fan-out otherwise renders as twelve boxes distinguishable only by index.
    const out = layoutRunDag(
      [{ ...node('root.body#0'), item_label: 'auth.py' }],
      { label: (n) => (n.item_label ? `${n.node_id} · ${n.item_label}` : n.node_id) },
    )
    expect(out.nodes[0].content).toBe('0 · auth.py')
  })
})

describe('state mapping', () => {
  it('treats the engine SUCCESS states as done', () => {
    // `degraded` and `no_change` are in the engine's SUCCESS_STATES. Painting them as errors would
    // tell the user work failed when it did not.
    expect(dagState('degraded')).toBe('done')
    expect(dagState('no_change')).toBe('done')
    expect(dagState('done')).toBe('done')
  })

  it('treats scope_violation and escalated as errors', () => {
    // Both mean the run stopped and needs a human to look.
    expect(dagState('scope_violation')).toBe('error')
    expect(dagState('escalated')).toBe('error')
  })

  it('separates blocked from awaiting', () => {
    expect(dagState('blocked')).toBe('blocked')
    expect(dagState('waiting')).toBe('awaiting')
  })

  it('maps an UNKNOWN state to the one that claims least', () => {
    // The engine has 14 states and may grow more; an older frontend must not paint a new state as
    // an error it is not.
    expect(dagState('quantum')).toBe('todo')
  })
})

describe('answerability', () => {
  const gate = node('root.children[0]', 'waiting')

  it('is FALSE for a waiting node with no continuation', () => {
    // A `wait` node is parked on the CLOCK. Offering Approve on one asks the user to answer
    // something nobody asked them.
    expect(isAwaitingHuman(gate, [])).toBe(false)
  })

  it('is TRUE once a live continuation exists', () => {
    expect(isAwaitingHuman(gate, [{ instance_path: gate.instance_path }])).toBe(true)
  })

  it('is FALSE for an EXPIRED continuation', () => {
    // The token is gone, so the button would fail — and a button that always fails teaches the user
    // the UI lies.
    expect(isAwaitingHuman(gate, [{ instance_path: gate.instance_path, expired: true }])).toBe(false)
  })

  it('is FALSE for a node that is not waiting at all', () => {
    expect(isAwaitingHuman(node('root.children[0]', 'running'), [
      { instance_path: 'root.children[0]' },
    ])).toBe(false)
  })

  it('marks an awaiting gate AWAITING even against the state map', () => {
    // The one state a user can act on must not be flattened into the generic look.
    const out = layoutRunDag([gate], { continuations: [{ instance_path: gate.instance_path }] })
    expect(out.nodes[0].state).toBe('awaiting')
  })

  it('leaves a clock-parked wait node in its mapped state', () => {
    const out = layoutRunDag([gate], { continuations: [] })
    expect(out.nodes[0].state).toBe('awaiting') // mapped from `waiting`, but not answerable
    expect(isAwaitingHuman(gate, [])).toBe(false)
  })
})

/** Issue #567 — the fan-out grammar the ENGINE emits.
 *
 *  `foreach` fans out to `<path>.body#<i>` and `while` iterates as `<path>.body@<n>`
 *  (`workflows/tick.py`). Neither carries a bracket, so the old reader — which derived a column by
 *  counting `[` and parentage by raw string prefix — scored every item at its container's depth and
 *  reparented every double-digit item onto its own sibling `#1`. Both are asserted here against the
 *  real paths, plus the invariant the module claims: the DAG and `buildTree` agree about parenthood.
 */
describe('foreach / while fan-out layout (#567)', () => {
  const CONTAINER = 'root.children[0]'
  const items = (n: number, sep: '#' | '@') =>
    Array.from({ length: n }, (_, i) => node(`${CONTAINER}.body${sep}${i}`))
  const fanout = (n: number, sep: '#' | '@' = '#') => [node('root'), node(CONTAINER), ...items(n, sep)]
  const byId = (out: ReturnType<typeof layoutRunDag>) =>
    Object.fromEntries(out.nodes.map((nd) => [nd.id, nd]))

  it('gives foreach items their OWN column, one past the container', () => {
    // Counting `[` scored `root.children[0].body#3` at depth 1 — the container's own depth — so the
    // container and all twelve items shared one column and the graph showed no fan-out at all.
    const out = layoutRunDag(fanout(12))
    const at = byId(out)
    expect(at[CONTAINER].x).toBe(NODE_W + COL_GAP)
    for (let i = 0; i < 12; i += 1) {
      expect(at[`${CONTAINER}.body#${i}`].x).toBe(2 * (NODE_W + COL_GAP))
    }
    expect(new Set(out.nodes.map((nd) => nd.x)).size).toBe(3)
  })

  it('parents the TENTH item to its container, not to sibling #1', () => {
    // `'body#10'.startsWith('body#1')` is true — no delimiter between the `1` and the `0` — so a
    // prefix scan silently reparented every item from #10 up. `children[10]` escaped it only
    // because the `]` blocks the match, which is why bracketed test data never showed the bug.
    const out = layoutRunDag(fanout(12))
    const parentsOf = (id: string) => out.edges.filter((e) => e.to === id).map((e) => e.from)
    expect(parentsOf(`${CONTAINER}.body#10`)).toEqual([CONTAINER])
    expect(parentsOf(`${CONTAINER}.body#11`)).toEqual([CONTAINER])
    expect(out.edges.some((e) => e.from === `${CONTAINER}.body#1`)).toBe(false)
  })

  it('draws every containment edge FORWARD', () => {
    // Parent and child in one column made x1 > x2 on every item edge — an arrow pointing back at
    // the node it came from, which reads as a rendering fault rather than a layout one.
    const out = layoutRunDag(fanout(12))
    expect(out.edges.length).toBeGreaterThan(0)
    for (const e of out.edges) expect(e.x2).toBeGreaterThan(e.x1)
  })

  it('treats a while iteration (`body@n`) exactly like a foreach item', () => {
    const out = layoutRunDag(fanout(11, '@'))
    const at = byId(out)
    expect(at[`${CONTAINER}.body@10`].x).toBe(2 * (NODE_W + COL_GAP))
    expect(out.edges.filter((e) => e.to === `${CONTAINER}.body@10`).map((e) => e.from))
      .toEqual([CONTAINER])
  })

  it('nests a step INSIDE a fan-out item one column deeper again', () => {
    const item = `${CONTAINER}.body#7`
    const out = layoutRunDag([node('root'), node(CONTAINER), node(item), node(`${item}.children[0]`)])
    const at = byId(out)
    expect(at[`${item}.children[0]`].x).toBe(3 * (NODE_W + COL_GAP))
    expect(out.edges.filter((e) => e.to === `${item}.children[0]`).map((e) => e.from)).toEqual([item])
  })

  it('does not split a `cases[label]` whose label contains a dot', () => {
    // The label is spec-authored text. Counting separators inside brackets would score
    // `root.cases[auth.py]` a level deeper than the sibling branch beside it.
    const out = layoutRunDag([node('root'), node('root.cases[auth.py]'), node('root.cases[b]')])
    const at = byId(out)
    expect(at['root.cases[auth.py]'].x).toBe(NODE_W + COL_GAP)
    expect(at['root.cases[b]'].x).toBe(NODE_W + COL_GAP)
  })

  it('agrees with buildTree about who contains whom', () => {
    // The module's own stated invariant. It was FALSE for a fan-out: `buildTree` reported
    // `body#1.descendants = []` while `runDag` gave that same node two children (#10 and #11).
    const nodes = fanout(12)
    const rows = buildTree(nodes)
    const out = layoutRunDag(nodes)
    const childrenOf = (id: string) => out.edges.filter((e) => e.from === id).map((e) => e.to).sort()
    for (const row of rows) {
      const direct = row.descendants.filter((d) => {
        const rest = d.slice(row.node.instance_path.length + 1)
        return !rest.includes('.')
      })
      expect(childrenOf(row.node.instance_path)).toEqual([...direct].sort())
    }
  })
})
