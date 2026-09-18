import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent, createEvent, within } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { DagView, type DagNode } from './DagView'

// ── Every DAG node is reachable and operable by keyboard (#474) ──────────────────────────────────
//
// `DagView` is the ONE renderer behind all three graph surfaces — the Tasks dependency graph, the
// Workflow run graph and loop Plan Review — and it drew each node as a bare `<g onClick>`:
//
//     <g data-dag-node={n.id} className="dag-node cursor-pointer group" onClick={…}>
//
// no `role`, no `tabIndex`, no key handler, no accessible name. Measured on a 27-node task graph:
// `dag.querySelectorAll('a[href],button,input,[tabindex]:not([tabindex="-1"])').length === 0`, and
// `grep -nE 'tabIndex|onKeyDown|role=|aria-'` over the file returned nothing at all. Clicking a node
// was the ONLY way to open the task or step it stood for, so a keyboard or screen-reader user could
// not reach a single one of them.
//
// Same defect class as the already-fixed #307 (`ListRow` focusable but not operable); `ListScaffold`
// is the in-repo reference for the pattern.
//
// 🪤 THE FIX HAD A TRAP IN IT, which is the most useful thing in this file. The obvious shape —
// `role="button"` + `tabIndex` on the node group itself — would have REGRESSED the workflow gate.
// An `awaiting` node renders real Approve/Deny `<button>`s in a `foreignObject`, and assistive tech
// presents a `role="button"` subtree as a LEAF: both controls would have been swallowed into the
// node's name and become unreachable, trading one a11y defect for another. So the focusable surface
// is an inner `<g>` wrapping only the node's box and content, and the gate buttons are its SIBLINGS.
// The nesting assertion below is what pins that, and it is the one that would break first if
// someone "simplified" the two groups back into one.

afterEach(cleanup)

const node = (id: string, over: Partial<DagNode> = {}): DagNode => ({
  id, x: 0, y: 0, w: 200, h: 60, state: 'todo',
  label: `${id} — Not started`,
  content: <div>{id}</div>,
  ...over,
})

const hitOf = (root: ParentNode, id: string) =>
  root.querySelector(`[data-dag-node="${id}"] .dag-node-hit`) as SVGGElement

describe('DAG nodes are keyboard-operable buttons', () => {
  it('every clickable node is a tab stop with button semantics and a name', () => {
    const onNodeClick = vi.fn()
    const { container } = render(
      <DagView nodes={[node('a'), node('b'), node('c')]} edges={[]} width={600} height={300}
        label="Dependency graph — 3 tasks" onNodeClick={onNodeClick} />,
    )
    const svg = container.querySelector('svg')!

    // The exact measurement the report took, inverted: 0 focusable descendants → one per node.
    const focusable = svg.querySelectorAll('a[href],button,input,[tabindex]:not([tabindex="-1"])')
    expect(focusable.length, 'one tab stop per node').toBe(3)

    for (const id of ['a', 'b', 'c']) {
      const hit = hitOf(container, id)
      expect(hit, `node ${id} has an operable surface`).toBeTruthy()
      expect(hit.getAttribute('role')).toBe('button')
      expect(hit.getAttribute('tabindex')).toBe('0')
      expect(hit.getAttribute('aria-label')).toBe(`${id} — Not started`)
    }
  })

  it('Enter and Space open the node, and Space does not also scroll', () => {
    const onNodeClick = vi.fn()
    const { container } = render(
      <DagView nodes={[node('a')]} edges={[]} width={300} height={120}
        label="Dependency graph — 1 task" onNodeClick={onNodeClick} />,
    )
    const hit = hitOf(container, 'a')

    fireEvent.keyDown(hit, { key: 'Enter' })
    expect(onNodeClick).toHaveBeenCalledWith('a')

    // Space must be preventDefault'd: without it, activating a node ALSO pages the scroll
    // container the graph sits in, so the node you just opened jumps off screen.
    const space = createEvent.keyDown(hit, { key: ' ' })
    fireEvent(hit, space)
    expect(onNodeClick).toHaveBeenCalledTimes(2)
    expect(space.defaultPrevented, 'Space must not scroll the graph as well').toBe(true)

    // An unrelated key does nothing — the handler is not a catch-all.
    fireEvent.keyDown(hit, { key: 'x' })
    expect(onNodeClick).toHaveBeenCalledTimes(2)
  })

  it('the graph names itself as a group, so its nodes stay exposed', () => {
    const { container } = render(
      <DagView nodes={[node('a')]} edges={[]} width={300} height={120}
        label="Dependency graph — 1 task" onNodeClick={() => {}} />,
    )
    const svg = container.querySelector('svg')!
    // `role="img"` would have been the tempting choice and it collapses the whole graph to its own
    // label, hiding every node inside it — the opposite of the fix.
    expect(svg.getAttribute('role')).toBe('group')
    expect(svg.getAttribute('aria-label')).toBe('Dependency graph — 1 task')
  })

  it('a gate node keeps Approve/Deny OUTSIDE its button, not nested inside it', () => {
    const onApprove = vi.fn()
    const { container } = render(
      <DagView nodes={[node('g', { state: 'awaiting' })]} edges={[]} width={300} height={160}
        label="Run graph — 1 step" onNodeClick={() => {}} onApprove={onApprove} onDeny={() => {}} />,
    )
    const group = container.querySelector('[data-dag-node="g"]') as SVGGElement
    const hit = hitOf(container, 'g')

    // Present on the node…
    expect(within(group as unknown as HTMLElement).getByText('Approve')).toBeTruthy()
    // …but NOT inside the role="button" surface, where AT would swallow them.
    expect(
      within(hit as unknown as HTMLElement).queryByText('Approve'),
      'Approve must not be nested inside the node button',
    ).toBeNull()

    // And it still works.
    fireEvent.click(within(group as unknown as HTMLElement).getByText('Approve'))
    expect(onApprove).toHaveBeenCalledWith('g')
  })

  it('a read-only graph names its nodes without making them tab stops', () => {
    // Plan Review passes no `onNodeClick`. An unnamed, unfocusable node was announced as nothing at
    // all; a named `img` is the honest shape — there is nothing to activate.
    const { container } = render(
      <DagView nodes={[node('r')]} edges={[]} width={300} height={120} label="Plan graph — 1 step" />,
    )
    const hit = hitOf(container, 'r')
    expect(hit.getAttribute('role')).toBe('img')
    expect(hit.getAttribute('tabindex'), 'nothing to activate → no tab stop').toBeNull()
    expect(hit.getAttribute('aria-label')).toBe('r — Not started')
  })

  it('a node with no label claims no semantics at all', () => {
    // The vacuity floor for the two assertions above: if `role` were unconditional, every one of
    // them would pass on a node carrying no name, which is exactly the state being fixed.
    const { container } = render(
      <DagView nodes={[node('n', { label: undefined })]} edges={[]} width={300} height={120} />,
    )
    const hit = hitOf(container, 'n')
    expect(hit.getAttribute('role')).toBeNull()
    expect(hit.getAttribute('aria-label')).toBeNull()
    expect(container.querySelector('svg')!.getAttribute('role')).toBeNull()
  })
})

// ── Every CALLER supplies the names, or the fix is only half-installed ───────────────────────────
//
// `DagView` cannot name a node for itself: `content` is arbitrary JSX inside a `foreignObject`, so
// the name has to come from the caller. That makes the component's fix necessary but not sufficient
// — a surface that forgets `label` ships unnamed nodes again, and nothing above would catch it
// because the component is doing its job. This scan is the anti-blind-spot rail: it enumerates the
// call sites from the tree rather than from a hand-written list, so a FOURTH graph surface is
// enrolled the moment it is added.

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) tsxFiles(p, out)
    else if (/\.tsx?$/.test(e) && !/\.test\.tsx?$/.test(e)) out.push(p)
  }
  return out
}

describe('every DagView surface names its graph and its nodes', () => {
  const root = join(process.cwd(), 'src')
  const files = tsxFiles(root).map((p) => [p, readFileSync(p, 'utf8')] as const)

  const callSites = files.filter(([p, s]) => s.includes('<DagView') && !p.endsWith('DagView.tsx'))
  const nodeBuilders = files.filter(([, s]) => /import type \{[^}]*DagNode/.test(s) || /\): DagNode =>/.test(s))

  it('finds the call sites and node builders — an empty scan cannot read as clean', () => {
    // Vacuity floor. Measured at the time of the fix: 3 call sites (TaskGraph, WorkflowRunDetail,
    // PlanStreamReview) and 3 node builders (TaskGraph, runDag, planGraph).
    expect(callSites.length, 'DagView call sites').toBeGreaterThanOrEqual(3)
    expect(nodeBuilders.length, 'DagNode builders').toBeGreaterThanOrEqual(3)
  })

  it('every <DagView> is given a graph label', () => {
    for (const [p, s] of callSites) {
      expect(/<DagView[\s\S]{0,600}?label=/.test(s), `${p} must pass a label to DagView`).toBe(true)
    }
  })

  it('every file that builds DagNodes sets an accessible label on them', () => {
    for (const [p, s] of nodeBuilders) {
      expect(/\blabel:/.test(s), `${p} builds DagNodes and must set label:`).toBe(true)
    }
  })
})
