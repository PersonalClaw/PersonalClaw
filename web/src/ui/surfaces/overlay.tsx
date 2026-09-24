/** The L2 overlay loader — the client half of the user/agent surface producer.
 *
 *  AMBIENT-SURFACES §6 declared three layers and shipped the ceiling, the boundary and
 *  the refusals for all three; until this module NOTHING wrote an L2 overlay, so the layer
 *  was declared and empty. The backend half (`personalclaw/surface_overlay.py`) carries
 *  the full threat posture in its docstring and owns path containment + the DATA shape.
 *  This module owns the two clauses that can only be checked where the registry lives:
 *
 *  * **Clause 2 — an unknown component name is REFUSED at load.** Note the deliberate
 *    difference from the chat path: `GenUiWidget` DROPS one bad line so the rest of a
 *    model's answer still paints, because a transcript is disposable. An overlay is a
 *    saved artifact someone authored on purpose, so half of it rendering is worse than a
 *    named refusal — one bad node refuses the WHOLE overlay and names the component.
 *  * **Clause 3 — shadowing is refused through the SAME `registerLayerComponent`** an
 *    app's L1 module uses. One rule, both producers, one code path. An overlay's
 *    `define` entries are the only way it can shadow anything, which is what keeps that
 *    rule from being vacuous.
 *
 *  Clause 4 (props are host-schema validated) is `validateInvocation`, the same function
 *  the chat renderer calls. Clause 6: safe mode (`maxSurfaceLayer() === 0`) returns before
 *  the fetch, so an operator in recovery loads no overlay at all — and even if one
 *  somehow arrived, `registerLayerComponent` refuses every L2 name under the ceiling.
 *
 *  A `define`d composite takes NO args (substituting a caller's value into a saved
 *  sub-tree is an expression language, which clause 1 refuses), so it declares `args: []`
 *  and `validateInvocation` reports any arg passed to it as `excess-args`. */
import { api, type SurfaceOverlayDoc, type SurfaceOverlayRefusal } from '../../lib/api'
import { GenUiNodes } from '../genui/GenUiWidget'
import { parseGenUi } from '../genui/parse'
import { registerLayerComponent, removeComponentsFrom, validateInvocation } from '../genui/registry'
import { LAYER_USER, maxSurfaceLayer } from './layers'

/** The `ERROR_CODES` row a client-side refusal carries. Kept in step with
 *  `personalclaw/errors.py` so a user reads ONE code vocabulary either way. */
export const CODE_OVERLAY_COMPONENT = 'ERR_SURFACE_OVERLAY_COMPONENT'

/** Where a refusal with no surface of its own renders: the surface that is always
 *  reachable, because L0 owns it. A backend refusal (bad JSON, a path escape) never got
 *  as far as naming a surface, and a refusal nobody can see is the invisible failure this
 *  whole clause exists to prevent. */
export const REFUSAL_HOME = 'dashboard'

/** One refused overlay, plus which surface it belongs to when it named one. */
export interface OverlayRefusalRow extends SurfaceOverlayRefusal {
  /** `''` when the overlay never got far enough to declare a surface. */
  surface: string
}

let _overlays: SurfaceOverlayDoc[] = []
let _refusals: OverlayRefusalRow[] = []
let _inflight: Promise<void> | null = null
let _done = false

/** The `source` an overlay's registrations carry, so refusing/removing one file's
 *  composites cannot touch another's. */
function sourceOf(file: string): string {
  return `overlay:${file}`
}

/** Every component name a DSL body invokes, in document order. */
export function overlayComponentNames(body: string): string[] {
  return parseGenUi(body || '').lines.map((l) => l.component)
}

/** Host-schema validate every line of a body. Returns the first refusal message, or ''.
 *
 *  This is clauses 2 + 4 in one walk, and it deliberately runs over the WHOLE tree
 *  before anything renders — the difference between "refused at load" and "dropped at
 *  render". */
export function validateOverlayBody(body: string): string {
  for (const line of parseGenUi(body || '').lines) {
    const error = validateInvocation(line.component, line.argKeys)
    if (error) return error.message
  }
  return ''
}

function refusal(doc: SurfaceOverlayDoc, what: string, fix: string): OverlayRefusalRow {
  return {
    file: doc.file,
    surface: doc.surface || '',
    error: {
      code: CODE_OVERLAY_COMPONENT,
      what,
      why:
        'An overlay is a tree of references to already-registered components. A name the ' +
        'host registry does not have, or a prop its schema refuses, is refused whole — a ' +
        'dropped node would be an invisible failure.',
      fix,
      suggestions: [],
    },
  }
}

/** Register one overlay's `define`d composites, then validate every body it ships.
 *  Returns '' on acceptance, or the refusal reason. On refusal NOTHING of this file's
 *  stays registered — a half-applied overlay is the state clause 2 refuses. */
function applyOverlay(doc: SurfaceOverlayDoc): OverlayRefusalRow | null {
  const source = sourceOf(doc.file)
  // Register every composite FIRST so one composite may reference another declared later
  // in the same file, then validate — a name resolves against the finished registry.
  for (const def of doc.define || []) {
    const result = registerLayerComponent(
      {
        name: def.name,
        group: 'Layout',
        description: def.description || `A ${doc.file} overlay composite.`,
        args: [],
        component: () => <GenUiNodes content={def.body} />,
      },
      { layer: LAYER_USER, source },
    )
    if (!result.ok) {
      removeComponentsFrom(source)
      return refusal(
        doc,
        `${doc.file} could not define "${def.name}": ${result.message}`,
        result.code === 'shadows-core'
          ? `Rename the composite — an overlay may ADD component names, never take a core one.`
          : `Rename the composite so it does not collide with an existing registration.`,
      )
    }
  }
  for (const def of doc.define || []) {
    const bad = validateOverlayBody(def.body)
    if (bad) {
      removeComponentsFrom(source)
      return refusal(doc, `${doc.file} composite "${def.name}": ${bad}`, 'Fix the named component or prop, then reload.')
    }
  }
  const bad = validateOverlayBody(doc.body)
  if (bad) {
    removeComponentsFrom(source)
    return refusal(doc, `${doc.file}: ${bad}`, 'Fix the named component or prop, then reload.')
  }
  return null
}

/** Fetch + validate + register the L2 layer, once per session.
 *
 *  Never throws: no overlays is the common case, and a gateway that cannot answer must
 *  leave L0 rendering everything it owns. */
export async function loadSurfaceOverlays(): Promise<void> {
  if (maxSurfaceLayer() < LAYER_USER) return // safe mode: no fetch at all (clause 6)
  if (_done) return
  if (_inflight) return _inflight
  _inflight = (async () => {
    let payload
    try {
      payload = await api.surfaceOverlays()
    } catch {
      _done = true
      return
    }
    const accepted: SurfaceOverlayDoc[] = []
    const refused: OverlayRefusalRow[] = (payload.refusals || []).map((r) => ({ ...r, surface: '' }))
    for (const doc of payload.overlays || []) {
      const bad = applyOverlay(doc)
      if (bad) {
        refused.push(bad)
        // eslint-disable-next-line no-console
        console.warn(`[surfaces] overlay refused: ${bad.error.what}`)
        continue
      }
      accepted.push(doc)
    }
    _overlays = accepted
    _refusals = refused
    _done = true
  })()
  try {
    await _inflight
  } finally {
    _inflight = null
  }
}

/** The accepted overlays targeting `surface`, in file order. */
export function overlaysFor(surface: string): SurfaceOverlayDoc[] {
  return _overlays.filter((o) => o.surface === surface)
}

/** The refusals `surface` should show: its own, plus the surface-less ones on the home
 *  surface so a refusal is never invisible. */
export function overlayRefusalsFor(surface: string): OverlayRefusalRow[] {
  return _refusals.filter((r) => r.surface === surface || (!r.surface && surface === REFUSAL_HOME))
}

/** Test seam: drop the loaded layer and everything it registered. */
export function resetSurfaceOverlays(): void {
  for (const o of _overlays) removeComponentsFrom(sourceOf(o.file))
  for (const r of _refusals) removeComponentsFrom(sourceOf(r.file))
  _overlays = []
  _refusals = []
  _inflight = null
  _done = false
}
