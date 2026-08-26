/** The streaming generative-UI renderer (AMBIENT-SURFACES §5.2).
 *
 *  Renders a `<widget kind="genui">` block's body IN THE HOST React tree (not a
 *  sandboxed iframe) — safe precisely *because* only registered, schema-validated
 *  components with typed props can appear. The body is the line-oriented genui DSL
 *  (see parse.ts); this component parses it, resolves `ref`/`refs` args to child
 *  components, validates each line against the registry, renders the valid ones,
 *  and DROPS the invalid ones with a visible, LLM-friendly typed error — never a
 *  null hole, never a crash. It re-parses on every render, so a streaming
 *  (partial) body paints its structure progressively.
 *
 *  Registered as the `embed` capability of the `genui` content type
 *  (registerBuiltins.ts), so Markdown.tsx's existing `embedFor(seg.kind)` dispatch
 *  routes `kind="genui"` here with no edit to blocks.ts / Markdown.tsx. */
import { memo, useCallback, useState, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Surface } from '../Surface'
import { fvs } from '../../design/fontWeight'
import type { EmbedProps } from '../content/contentTypes'
import { LAYER_CORE } from '../surfaces/layers'
import { LayerBoundary } from '../surfaces/LayerBoundary'
import { parseGenUi, type ParsedLine } from './parse'
import { getComponent, validateInvocation } from './registry'
import { registerCoreGenUiComponents } from './components'
import {
  GenUiActionCtx,
  composeDualPayload,
  routeGenUiAction,
  useGenUiHost,
  type GenUiEmit,
} from './actions'

// Ensure the core set is registered even if app bootstrap hasn't run (tests,
// SSR-less lazy paths). Idempotent — a no-op after the first call.
registerCoreGenUiComponents()

/** The one visible dropped-line notice: a danger-tinted strip naming the typed
 *  error so a producing model can self-correct. Deliberately compact so a widget
 *  full of them stays legible. */
function DroppedLine({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.75rem] text-danger"
      style={{ background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)' }}
    >
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words">{message}</span>
    </div>
  )
}

/** Resolve one parsed line to a rendered node, recursing into its `refs` children.
 *  Returns either the rendered component OR a DroppedLine when the line fails
 *  validation. `seen` breaks reference cycles (a line can't be its own ancestor). */
function renderLine(
  line: ParsedLine,
  byId: Map<string, ParsedLine>,
  seen: Set<string>,
): ReactNode {
  const error = validateInvocation(line.component, line.argKeys)
  if (error) return <DroppedLine key={line.id} message={error.message} />

  const def = getComponent(line.component)! // validateInvocation proved it exists
  // Resolve child references to rendered nodes, keyed by the arg they came from.
  const children: Record<string, ReactNode> = {}
  for (const [key, ids] of Object.entries(line.refs)) {
    children[key] = ids.map((id) => {
      if (seen.has(id)) return null // cycle guard — never recurse into an ancestor
      const child = byId.get(id)
      if (!child) return null // unresolved ref → dropped silently (no null hole in layout)
      return (
        <div key={id}>{renderLine(child, byId, new Set([...seen, line.id]))}</div>
      )
    })
  }
  const Comp = def.component
  const node = <Comp key={line.id} args={line.args} children={children} />
  // A component from a layer ABOVE core is app/user code fed model-authored args, so
  // it renders inside a boundary: it may fail, it may not take the tree with it (§6).
  // Core components are NOT wrapped — a boundary around every line would make a real
  // core crash silent, and L0 is the layer the build owns.
  if (def.layer <= LAYER_CORE) return node
  return (
    <LayerBoundary key={line.id} layer={def.layer} what={def.name}>
      {node}
    </LayerBoundary>
  )
}

/** The genui widget embed. Parses + validates + renders the DSL body. */
export const GenUiWidget = memo(function GenUiWidget({ content, title, slug }: EmbedProps) {
  // WHO is rendering this widget (§5.4). Supplied by the host, never by the widget's own
  // text — see actions.ts. Absent host ⇒ chat-born, the harmless default.
  const host = useGenUiHost()
  const { lines, parseErrors } = parseGenUi(content || '')
  // A refused / failed action is reported HERE, next to the widget that raised it —
  // a submit that quietly did nothing is the worst outcome of the three.
  const [actionError, setActionError] = useState('')
  const emit = useCallback<GenUiEmit>(async ({ action, label, payload }) => {
    setActionError('')
    const dual = composeDualPayload({
      action,
      label,
      payload,
      live: slug ? { saved: true, slug } : undefined,
    })
    if (!dual) {
      setActionError('That action could not be sent — its values are not serializable.')
      return
    }
    // The PRODUCER decides the sink: a component cannot choose its own router, or a chat
    // widget's click could be aimed at a workflow run.
    const result = await routeGenUiAction(dual, host.producer, { action, payload })
    if (!result.ok) setActionError(result.message || 'That action could not be completed.')
    // The host's own refresh runs only on success — closing an inbox row after a refused
    // submit would hide a gate that is still waiting.
    else host.onResolved?.()
  }, [host, slug])
  const byId = new Map<string, ParsedLine>()
  for (const l of lines) byId.set(l.id, l)

  // Roots = lines nothing else references (the top of the tree). Everything else
  // renders as a child of its referrer. When NOTHING is referenced (a flat list
  // of siblings), every line is a root — the common single-component case.
  const referenced = new Set<string>()
  for (const l of lines) for (const ids of Object.values(l.refs)) for (const id of ids) referenced.add(id)
  const roots = lines.filter((l) => !referenced.has(l.id))

  return (
    <Surface tone="low" radius="lg" className="my-3 overflow-hidden">
      <div className="border-b border-outline-variant/40 bg-surface-container px-3 py-1.5">
        <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{title || 'Widget'}</span>
      </div>
      <GenUiActionCtx.Provider value={emit}>
        <div className="flex flex-col gap-s p-l">
          {roots.map((l) => (
            <div key={l.id}>{renderLine(l, byId, new Set())}</div>
          ))}
          {parseErrors.map((pe) => (
            <DroppedLine key={`pe-${pe.line}`} message={`Line ${pe.line}: ${pe.message} — "${pe.text.slice(0, 60)}"`} />
          ))}
          {actionError && <DroppedLine message={actionError} />}
        </div>
      </GenUiActionCtx.Provider>
    </Surface>
  )
})
