/** Progressive plan-review parsing (UNIVERSAL-PLANNING UP-R7, WF2UNI-10).
 *
 *  The plan spec arrives in chunks over the `plan_streaming` SSE event — a growing buffer of
 *  JSON that is INVALID until the last brace lands. The review surface must render the plan as
 *  it fills (proposal cards + a read-only graph + the raw JSON, kept in sync), so it re-parses
 *  the whole buffer on every chunk and shows the best-effort partial. That "best-effort parse of
 *  a truncated JSON document" is the interesting, failure-prone bit — and it is pure, so it lives
 *  here with `planStream.test.ts` driving partial→full transitions, not inside a component.
 *
 *  The approach is deliberately NOT a streaming tokenizer: a mid-stream buffer is repaired by
 *  closing the brackets/strings still open, then parsed with the stock JSON parser. A repair that
 *  fails leaves the last good parse standing — a half-arrived chunk must never blank a plan the
 *  user was already reading. */

/** One step in the streamed plan — the shape the proposal cards + graph render. Fields are all
 *  optional because an in-flight step may have only its id before its label/target arrive. */
export interface PlanStep {
  id: string
  /** Named by the small-model naming call (or its deterministic fallback). */
  label?: string
  role?: string
  kind?: string
  target?: string
  /** True while this step is still streaming — the cue for the shimmer. */
  pending?: boolean
  /** Ids this step depends on / hands off to — the graph's edges. */
  depends_on?: string[]
}

export interface PlanDraft {
  title?: string
  description?: string
  steps: PlanStep[]
}

/** Repair a truncated JSON buffer into something parseable, then parse it.
 *
 *  Walks the buffer tracking string/escape state and the open-container stack — and, crucially,
 *  whether an open trailing string is a VALUE or a KEY. A partial value ("Half a titl…) is closed
 *  and kept; a partial key ("desc…, the chunk cut mid-name before its colon) is DROPPED, because
 *  a key with no value is not valid JSON. Then it strips a dangling comma / `key:` with no value
 *  and appends the closers the stack still needs. Returns `null` when even the repaired form won't
 *  parse — the caller keeps its last good draft rather than flashing empty. */
export function parsePartialJson(buffer: string): unknown {
  const trimmed = buffer.trim()
  if (!trimmed) return null
  // Fast path: it's already valid (the final chunk, or a whole small plan in one frame).
  try {
    return JSON.parse(trimmed)
  } catch {
    /* fall through to repair */
  }
  // Per-container state: its closer + (for objects) whether we've seen the `:` and are now
  // expecting/holding a value. A string opened while `afterColon` (object) or inside an array is
  // a VALUE; otherwise it is a KEY.
  const stack: { close: string; afterColon: boolean }[] = []
  const top = () => stack[stack.length - 1]
  let inString = false
  let escaped = false
  let stringStart = -1
  let stringIsValue = false
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') {
      inString = true
      stringStart = i
      const t = top()
      stringIsValue = !t || t.close === ']' || t.afterColon
      continue
    }
    if (ch === '{') { stack.push({ close: '}', afterColon: false }); continue }
    if (ch === '[') { stack.push({ close: ']', afterColon: false }); continue }
    if (ch === '}' || ch === ']') { stack.pop(); continue }
    if (ch === ':') { const t = top(); if (t && t.close === '}') t.afterColon = true; continue }
    if (ch === ',') { const t = top(); if (t && t.close === '}') t.afterColon = false; continue }
  }
  let repaired = trimmed
  if (inString) {
    // A partial value string is closed + kept; a partial key is dropped back to its opening quote.
    if (stringIsValue) repaired += '"'
    else repaired = trimmed.slice(0, stringStart)
  }
  // Strip trailing incompletes: a dangling comma, or a `key:` (with optional leading comma) whose
  // value never arrived — both leave JSON that can't parse.
  let prev: string
  do {
    prev = repaired
    repaired = repaired.replace(/\s+$/, '')
    repaired = repaired.replace(/,$/, '')
    repaired = repaired.replace(/,?\s*"(?:[^"\\]|\\.)*"\s*:$/, '')
  } while (repaired !== prev)
  for (let i = stack.length - 1; i >= 0; i--) repaired += stack[i].close
  try {
    return JSON.parse(repaired)
  } catch {
    return null
  }
}

/** Coerce a parsed (possibly-partial) plan object into the draft the views render. Tolerant of
 *  the two shapes the buffer passes through: a bare `{steps:[…]}` and a wrapped `{plan:{…}}`. A
 *  step with no id yet is dropped (an edge to a nameless node is a rendering artifact); a step
 *  present without an end marker is `pending` so the shimmer shows until the plan closes. */
export function toPlanDraft(parsed: unknown, opts: { complete?: boolean } = {}): PlanDraft {
  const root = (parsed && typeof parsed === 'object' ? parsed : {}) as Record<string, unknown>
  const planNode = (root.plan && typeof root.plan === 'object' ? root.plan : root) as Record<string, unknown>
  const rawSteps = Array.isArray(planNode.steps)
    ? planNode.steps
    : Array.isArray(planNode.nodes)
      ? planNode.nodes
      : []
  const steps: PlanStep[] = []
  rawSteps.forEach((s, i) => {
    if (!s || typeof s !== 'object') return
    const step = s as Record<string, unknown>
    const id = String(step.id ?? step.node_id ?? '').trim()
    if (!id) return
    // The last step in an unfinished buffer is the one still arriving → pending. Once the plan
    // is complete (final chunk parsed) nothing shimmers.
    const pending = !opts.complete && i === rawSteps.length - 1
    steps.push({
      id,
      label: str(step.label) || str(step.title) || undefined,
      role: str(step.role) || undefined,
      kind: str(step.kind) || undefined,
      target: str(step.target) || str(step.objective) || undefined,
      depends_on: Array.isArray(step.depends_on) ? step.depends_on.map(String) : undefined,
      pending,
    })
  })
  return {
    title: str(planNode.title) || undefined,
    description: str(planNode.description) || undefined,
    steps,
  }
}

/** The one call the streaming view makes each chunk: parse the growing buffer, coerce to a
 *  draft, and — when the parse fails on a half-arrived chunk — keep the last good draft rather
 *  than blanking the plan. Returns the draft to render + whether this chunk parsed cleanly. */
export function reparseBuffer(
  buffer: string,
  last: PlanDraft | null,
  opts: { complete?: boolean } = {},
): { draft: PlanDraft; parsed: boolean } {
  const parsed = parsePartialJson(buffer)
  if (parsed == null) return { draft: last ?? { steps: [] }, parsed: false }
  return { draft: toPlanDraft(parsed, opts), parsed: true }
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}
