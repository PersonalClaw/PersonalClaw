/** The pure half of the workflow editor: what a definition's editable part IS, how its steps are
 *  walked and addressed, and where a server issue belongs.
 *
 *  Kept free of React so the rules that have to match the engine are testable on their own —
 *  above all the step PATH, which is the engine's contract (`models.walk`): `root`,
 *  `root.children[0]`, `root.body`, `root.cases[hit]`, `root.default`. Every validation issue and
 *  lint finding the save route returns carries one, and it is the only thing that says which step
 *  an issue belongs to. */
import type { WorkflowDef, WorkflowNode } from '../../lib/api'

/** The fields the definition store owns. Everything ELSE a definition has is the author's, and the
 *  editor carries it through a save untouched even when no control edits it — `runtime_hints`,
 *  `defaults`, `on_overlap` and `workspace` included. */
const BOOKKEEPING = new Set(['name', 'version', 'spec_semver', 'source', 'provenance', 'created_at', 'updated_at'])

/** The part of a definition an author edits. An open record on purpose: a key this type does not
 *  name is still the definition's, and dropping it on save is the defect this editor exists to not
 *  have. */
export interface EditableDoc {
  description: string
  inputs: Record<string, unknown>
  tags: string[]
  metadata: Record<string, unknown>
  root: WorkflowNode
  [key: string]: unknown
}

export function editableDoc(def: WorkflowDef): EditableDoc {
  const doc: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(def as unknown as Record<string, unknown>)) {
    if (!BOOKKEEPING.has(key)) doc[key] = value
  }
  return {
    ...doc,
    description: typeof def.description === 'string' ? def.description : '',
    inputs: (def.inputs ?? {}) as Record<string, unknown>,
    tags: Array.isArray(def.tags) ? def.tags : [],
    metadata: (def.metadata ?? {}) as Record<string, unknown>,
    root: def.root,
  }
}

// ── hidden values ──────────────────────────────────────────────────────────────

/** A value the read hid. The backend never sends a value held under a credential-shaped key
 *  (`api_key`, `max_tokens`, `authors`…): it sends `_has_<key>: true` in its place, and restores
 *  the real value when the definition is saved. So a flag is shown, carried and saved back as-is. */
export const HIDDEN_PREFIX = '_has_'
export const isHidden = (key: string) => key.startsWith(HIDDEN_PREFIX)
export const hiddenName = (key: string) => key.slice(HIDDEN_PREFIX.length)

// ── the step tree ──────────────────────────────────────────────────────────────

/** One step of the locator from the root to a node: the structured twin of the path string, so an
 *  update never has to PARSE a path (a case label can hold `]` or `.`). */
export type Hop = { at: 'child'; index: number } | { at: 'body' } | { at: 'case'; label: string } | { at: 'default' }

export interface StepRow {
  /** The engine's path for this node — what an issue's `path` is compared with. */
  path: string
  hops: Hop[]
  depth: number
  node: WorkflowNode
  /** Where this node sits in its parent: '' for an ordinary child, else `body`, `default`, or `case <label>`. */
  slot: string
  /** Its position in reading order, `1`, `2.1`… — '' for the root, which is the workflow itself. */
  number: string
}

const isNode = (v: unknown): v is WorkflowNode =>
  !!v && typeof v === 'object' && !Array.isArray(v) && typeof (v as { kind?: unknown }).kind === 'string'

/** Every step, in the engine's walk order (node, children, body, cases, default). */
export function stepRows(root: WorkflowNode): StepRow[] {
  const out: StepRow[] = []
  const visit = (node: WorkflowNode, path: string, hops: Hop[], depth: number, slot: string, number: string) => {
    out.push({ path, hops, depth, node, slot, number })
    const prefix = number ? `${number}.` : ''
    let n = 0
    const next = () => `${prefix}${++n}`
    ;(node.children ?? []).forEach((child, index) => {
      if (isNode(child)) visit(child, `${path}.children[${index}]`, [...hops, { at: 'child', index }], depth + 1, '', next())
    })
    if (isNode(node.body)) visit(node.body, `${path}.body`, [...hops, { at: 'body' }], depth + 1, 'body', next())
    for (const [label, child] of Object.entries(node.cases ?? {})) {
      if (isNode(child)) visit(child, `${path}.cases[${label}]`, [...hops, { at: 'case', label }], depth + 1, `case ${label}`, next())
    }
    if (isNode(node.default)) visit(node.default, `${path}.default`, [...hops, { at: 'default' }], depth + 1, 'default', next())
  }
  visit(root, 'root', [], 0, '', '')
  return out
}

/** A copy of `root` with the node at `hops` replaced by `update(node)`. Only the spine to that node
 *  is copied; every other subtree is shared, so an edit costs its depth, not the tree. */
export function updateNode(root: WorkflowNode, hops: Hop[], update: (node: WorkflowNode) => WorkflowNode): WorkflowNode {
  if (hops.length === 0) return update(root)
  const [hop, ...rest] = hops
  switch (hop.at) {
    case 'child': {
      const children = [...(root.children ?? [])]
      children[hop.index] = updateNode(children[hop.index], rest, update)
      return { ...root, children }
    }
    case 'body':
      return { ...root, body: updateNode(root.body as WorkflowNode, rest, update) }
    case 'default':
      return { ...root, default: updateNode(root.default as WorkflowNode, rest, update) }
    case 'case':
      return { ...root, cases: { ...(root.cases ?? {}), [hop.label]: updateNode((root.cases ?? {})[hop.label], rest, update) } }
  }
}

/** What a step DOES, in one line: the action's provider, a gate's kind, a sub-workflow's name. */
export function stepAction(node: WorkflowNode): string {
  const cfg = node.config ?? {}
  const pick = (k: string) => (typeof cfg[k] === 'string' && cfg[k] ? String(cfg[k]) : '')
  switch (node.kind) {
    case 'action': return pick('provider')
    case 'gate': return pick('kind') ? `${pick('kind')} gate` : ''
    case 'subworkflow': return pick('ref')
    case 'loop': return pick('mode')
    case 'foreach': return pick('items')
    case 'branch': return pick('on')
    default: return ''
  }
}

// ── issues ─────────────────────────────────────────────────────────────────────

/** One validation issue or lint finding, as the save route returns it. `message` is the platform's
 *  sentence and is shown verbatim — it names the field, and a rewording here would drift from it. */
export interface EditIssue {
  code: string
  message: string
  path?: string
  severity?: string
  /** `lint` for a conventions finding: advice, never a reason the save was refused. */
  source?: 'validation' | 'lint'
}

export interface PlacedIssues {
  /** Issues keyed by the step path they name. */
  byPath: Map<string, EditIssue[]>
  /** Issues about the declared inputs (`path: "inputs"`). */
  inputs: EditIssue[]
  /** Issues naming no step on screen — shown at the top, never dropped. */
  unplaced: EditIssue[]
}

export function placeIssues(issues: EditIssue[], rows: StepRow[]): PlacedIssues {
  const paths = new Set(rows.map((r) => r.path))
  const placed: PlacedIssues = { byPath: new Map(), inputs: [], unplaced: [] }
  for (const issue of issues) {
    const path = issue.path ?? ''
    if (paths.has(path)) {
      placed.byPath.set(path, [...(placed.byPath.get(path) ?? []), issue])
    } else if (path === 'inputs') {
      placed.inputs.push(issue)
    } else {
      placed.unplaced.push(issue)
    }
  }
  return placed
}

export const isError = (i: EditIssue) => i.source !== 'lint' && (i.severity ?? 'error') === 'error'

/** The issue list out of a save/check response or a refused save's `error.detail`. Lint findings are
 *  tagged as such: the backend attaches them to every answer as advice. */
export function issuesFrom(body: unknown): EditIssue[] {
  if (!body || typeof body !== 'object') return []
  const b = body as { issues?: unknown; lint?: { findings?: unknown } }
  const shape = (raw: unknown, source: EditIssue['source']): EditIssue[] =>
    Array.isArray(raw)
      ? raw.filter((i): i is EditIssue => !!i && typeof i === 'object' && typeof (i as EditIssue).message === 'string')
          .map((i) => ({ code: String(i.code ?? ''), message: i.message, path: i.path, severity: i.severity, source }))
      : []
  return [...shape(b.issues, 'validation'), ...shape(b.lint?.findings, 'lint')]
}

/** An inline-secret refusal names the step by ID and the field by key, not by path. */
export function inlineSecretIssues(detail: unknown, rows: StepRow[]): EditIssue[] {
  const findings = (detail as { findings?: unknown } | undefined)?.findings
  if (!Array.isArray(findings)) return []
  return findings.map((f) => {
    const nodeId = String((f as { node_id?: unknown }).node_id ?? '')
    const key = String((f as { key?: unknown }).key ?? '')
    const hint = String((f as { hint?: unknown }).hint ?? '')
    const row = rows.find((r) => r.node.id === nodeId)
    return { code: 'WF_DEF_INLINE_SECRET', message: key ? `\`${key}\`: ${hint}` : hint, path: row?.path ?? '', severity: 'error', source: 'validation' as const }
  })
}

// ── the raw tab ────────────────────────────────────────────────────────────────

export function docToJson(doc: EditableDoc): string {
  return JSON.stringify(doc, null, 2)
}

/** Parse the JSON tab. The result must still be a definition — an object with a `root` step —
 *  because the Steps tab and the save both read one. */
export function jsonToDoc(text: string): { doc: EditableDoc } | { error: string } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'This is not valid JSON.' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { error: 'The definition must be one JSON object.' }
  const obj = parsed as Record<string, unknown>
  if (!isNode(obj.root)) return { error: 'The definition needs a `root` step: an object with a `kind`.' }
  return {
    doc: {
      ...obj,
      description: typeof obj.description === 'string' ? obj.description : '',
      inputs: obj.inputs && typeof obj.inputs === 'object' && !Array.isArray(obj.inputs) ? obj.inputs as Record<string, unknown> : {},
      tags: Array.isArray(obj.tags) ? obj.tags.map(String) : [],
      metadata: obj.metadata && typeof obj.metadata === 'object' && !Array.isArray(obj.metadata) ? obj.metadata as Record<string, unknown> : {},
      root: obj.root,
    },
  }
}

// ── names ──────────────────────────────────────────────────────────────────────

/** The engine's own name rule (`models.valid_name`): it becomes a directory. */
export const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/

/** Names to offer a copy under, best first: `<name>-copy`, then `-copy-2`… — each within the
 *  engine's 63-character limit. Which one is FREE is the backend's answer, asked per name. */
export function copyCandidates(name: string, count = 5): string[] {
  const out = [`${name}-copy`.slice(0, 63)]
  for (let i = 2; out.length < count; i++) {
    const suffix = `-copy-${i}`
    out.push(`${name.slice(0, 63 - suffix.length)}${suffix}`)
  }
  return out
}
