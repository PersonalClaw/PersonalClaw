import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Orbit } from 'lucide-react'
import { prefersReducedMotion } from '../../../design/motion'
import { useAgentActivity, type AgentActivityEntity } from '../../../lib/useAgentActivity'
import { SlotEmptyState } from '../widgets/kit'
import {
  KIND_SCALE, STATE_VISUAL, interpolateScene, layoutScene, pickRenderTier, sceneSummary,
  staticScene, type RenderTier, type SceneNode,
} from './worldScene'

// ── Orbit: the first-party agent world (AMBIENT-SURFACES A2-3) ───────────────
// An ambient scene of everything your agents are doing: every running loop, live
// chat session and spawned subagent is a body in orbit, pulled INWARD as it starts
// to want you. Nothing here is a control — a world is something you glance at.
//
// 🔴 THIS COMPONENT FETCHES NOTHING. Its only data source is `useAgentActivity()`.
// That is the atom's constraint ("renders live states from the hook alone, no
// private endpoints") and it is what makes the seam real: an app-contributed world
// (APP-PLATFORM-EVOLUTION) gets handed the identical `AgentActivityFeed` value and
// needs no network permission at all. `agentWorldRender.test.tsx` fails the
// build if this module or `worldScene.ts` ever mentions an `/api/` path or imports
// `lib/api`. Keep it that way: add a FIELD to the contract, never a fetch here.
//
// The scene MODEL (layout, easing, tone crossfade, reduced-motion collapse) lives
// in `worldScene.ts` as pure functions, because a canvas cannot be asserted on in
// jsdom. This file is the painter: clock, context, and pixels.

/** Base node radius in CSS px at the reference height, scaled with the canvas. */
const BASE_NODE = 7
/** Glow layers per node. Three additive passes read as light; one reads as a blob. */
const GLOW_LAYERS = 3

/** Resolve a design-token custom property to a paintable colour. A canvas cannot
 *  take a Tailwind class or a `var()`, so the token is read off the live document —
 *  which means the world follows the active theme (light/dark/custom) with no
 *  colour of its own.
 *
 *  🪤 The `fallback` argument is not defensive noise: if a theme ever fails to
 *  declare one of these tokens, every node resolves to '' and the world paints an
 *  EMPTY RECTANGLE while its `role="img"` name still promises a scene — the exact
 *  silent-blank that `pickRenderTier`'s static tier exists to prevent, arriving
 *  through the back door. So an unresolvable token falls back to the canvas's own
 *  inherited text colour, which is still theme-derived (never a literal). Only the
 *  faint ring GUIDES may resolve to nothing, because those are decoration. */
function resolveTone(root: Element, tone: string, fallback = ''): string {
  return getComputedStyle(root).getPropertyValue(tone).trim() || fallback
}

/** Mix two resolved colours for the state-change crossfade. Both tokens resolve to
 *  whatever the theme declares (hex, rgb(), oklch()), and parsing every CSS colour
 *  syntax here would be its own bug farm — so the crossfade is done by ALPHA
 *  (paint `from` under `to`), which is correct for any syntax. */
function crossfadeAlpha(mix: number): { from: number; to: number } {
  return { from: 1 - mix, to: mix }
}

export function AgentWorld() {
  const { entities, truncated, error, loading } = useAgentActivity()
  const reduced = prefersReducedMotion()

  // ── The canvas element lives in STATE, not a ref, and this is load-bearing ──
  //
  // 🔴 THE DEFECT THIS SHAPE EXISTS TO PREVENT (found by a real browser drive, not
  // by the suite): the element does not exist on the first render. The `loading`
  // holdback below returns `null` until the fold settles, so a mount-time
  // `useEffect(() => setTier(pickRenderTier(canvasRef.current)), [])` probed a ref
  // that was still `null`, got `'static'`, and — having empty deps — never re-probed
  // when the canvas finally appeared. The paint effect then early-returned forever.
  // Measured result in Chrome: `canvas.width/height` left at the HTML default
  // `300x150` against a CSS box of `1210x288`, and `getImageData` over the whole
  // backing store returning `anyAlphaPx: 0` — a user saw a 288px empty rectangle
  // while `role="img"` promised a scene. The holdback that fixed one honesty bug
  // stranded the probe for another.
  //
  // The cure is to make the ELEMENT the dependency. A callback ref publishes the
  // node into state the moment it attaches (first render or the tenth), the tier is
  // probed FROM that node rather than from a ref that might be empty, and the paint
  // effect below lists `canvasEl` in its deps — so it cannot run before the element
  // exists and cannot fail to run once it does. Do not turn this back into a ref.
  const [canvasEl, setCanvasEl] = useState<HTMLCanvasElement | null>(null)
  // `null` = not probed yet, distinct from a probed `'static'`. Without that
  // distinction the DOM fallback list would flash for one commit on every mount,
  // because "we have not looked yet" and "there is no context" would look the same.
  const [tier, setTier] = useState<RenderTier | null>(null)
  const attachCanvas = useCallback((el: HTMLCanvasElement | null) => {
    setCanvasEl(el)
    setTier(el ? pickRenderTier(el) : null)
  }, [])

  const summary = useMemo(() => sceneSummary(entities, truncated), [entities, truncated])

  // Live nodes, held across frames so a state change can EASE. Held in a ref, not
  // state: the animation writes it ~60x/s and re-rendering React that often would
  // be the whole cost of the widget.
  const nodes = useRef<SceneNode[]>([])
  // Reduced motion has no clock at all, so the settled scene is computed straight
  // from the entities and painted once. Recomputed only when the entities change.
  const target = useMemo(() => layoutScene(entities), [entities])
  const settled = useMemo(() => staticScene(entities), [entities])

  useEffect(() => {
    const canvas = canvasEl
    if (!canvas || tier !== '2d') return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const root = document.documentElement

    // Device-pixel-ratio-correct backing store, re-measured on resize so the scene
    // stays crisp and correctly centred in a responsive widget slot.
    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      const w = canvas.clientWidth || 1
      const h = canvas.clientHeight || 1
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      return { w, h }
    }
    let size = resize()
    const onResize = () => { size = resize(); if (reduced) paint(settled, 0, size) }
    window.addEventListener('resize', onResize)

    // ── REDUCED MOTION: one paint, then still. No rAF is ever scheduled. ──
    // This is the clause, in code: not a slower animation — the absence of one, and
    // a layout that never moves. `agentWorldRender.test.tsx` asserts
    // `requestAnimationFrame` is NOT called here, with a positive control that it IS
    // called on the animated path (so the audit cannot pass vacuously).
    if (reduced) {
      nodes.current = settled
      paint(settled, 0, size)
      return () => { window.removeEventListener('resize', onResize) }
    }

    let raf = 0
    let last = performance.now()
    const frame = (now: number) => {
      const dt = Math.min(64, now - last)  // clamp: a backgrounded tab must not jump
      last = now
      nodes.current = interpolateScene(nodes.current, target, dt)
      paint(nodes.current, now, size)
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }

    function paint(list: SceneNode[], now: number, dim: { w: number; h: number }) {
      const { w, h } = dim
      ctx!.clearRect(0, 0, w, h)
      // Resolved once per frame, not once per node: `getComputedStyle` is a layout
      // read, and calling it 64 nodes x 2 tones x 60fps would be the whole frame cost.
      const ink = getComputedStyle(canvas!).color
      const unit = Math.min(w, h)
      const nodeR = BASE_NODE * Math.max(0.7, Math.min(1.6, unit / 260))

      // Faint ring guides, one per occupied state ring. They give the orbits a
      // structure to read against — without them the nodes look scattered.
      const guide = resolveTone(root, '--color-outline-variant')
      if (guide) {
        ctx!.save()
        ctx!.globalAlpha = 0.16
        ctx!.strokeStyle = guide
        ctx!.lineWidth = 1
        for (const state of new Set(list.map((n) => n.state))) {
          ctx!.beginPath()
          ctx!.arc(w / 2, h / 2, STATE_VISUAL[state].ring * 0.5 * unit, 0, Math.PI * 2)
          ctx!.stroke()
        }
        ctx!.restore()
      }

      for (const n of list) {
        // Orbit + breathe. Both terms are ZERO under reduced motion (staticScene sets
        // speed and pulse to 0), so the same painter draws the static layout — there
        // is no second, divergent code path to keep in step.
        const spin = n.speed === 0 ? 0 : (now / 1000) * n.speed * 0.18
        const dx = n.x - 0.5
        const dy = n.y - 0.5
        const cos = Math.cos(spin)
        const sin = Math.sin(spin)
        const px = w / 2 + (dx * cos - dy * sin) * unit
        const py = h / 2 + (dx * sin + dy * cos) * unit
        const breathe = n.pulse === 0 ? 1 : 1 + Math.sin(now / 620 + n.phase) * 0.16 * n.pulse
        const r = nodeR * n.r * breathe
        const enter = Math.max(0.35, n.mix === 1 ? 1 : 0.35 + n.mix * 0.65)
        const { from, to } = crossfadeAlpha(n.mix)

        for (const [tone, alpha] of [[n.fromTone, from], [n.tone, to]] as const) {
          if (alpha <= 0.001) continue
          const color = resolveTone(root, tone, ink)
          if (!color) continue
          // Additive glow: widest + faintest first, then the core. `lighter` is what
          // makes overlapping agents bloom instead of occluding each other.
          ctx!.save()
          ctx!.globalCompositeOperation = 'lighter'
          for (let layer = GLOW_LAYERS; layer >= 1; layer--) {
            ctx!.globalAlpha = alpha * enter * (0.1 + 0.16 / layer)
            ctx!.fillStyle = color
            ctx!.beginPath()
            ctx!.arc(px, py, r * (1 + layer * 0.9), 0, Math.PI * 2)
            ctx!.fill()
          }
          ctx!.restore()

          ctx!.save()
          ctx!.globalAlpha = alpha * enter
          ctx!.fillStyle = color
          ctx!.beginPath()
          ctx!.arc(px, py, r, 0, Math.PI * 2)
          ctx!.fill()
          ctx!.restore()

          // Progress arc — only when the entity HAS progress. An unknown draws no
          // arc at all rather than an empty ring, which would read as "0% done".
          if (n.progress !== undefined) {
            ctx!.save()
            ctx!.globalAlpha = alpha * enter * 0.9
            ctx!.strokeStyle = color
            ctx!.lineWidth = Math.max(1.5, r * 0.28)
            ctx!.lineCap = 'round'
            ctx!.beginPath()
            ctx!.arc(px, py, r * 2.1, -Math.PI / 2, -Math.PI / 2 + n.progress * Math.PI * 2)
            ctx!.stroke()
            ctx!.restore()
          }
        }
      }
    }
    // `canvasEl` FIRST: it is the dependency whose absence caused the empty-rectangle
    // defect. An effect that needs a DOM node must depend on the node.
  }, [canvasEl, tier, reduced, target, settled])

  // The probe's own failure is never silence: a calm empty world while every fetch
  // is failing is the worst thing this surface could show.
  if (error) {
    return <SlotEmptyState icon={Orbit}>Couldn&rsquo;t read what your agents are doing, so the world is unknown right now.</SlotEmptyState>
  }
  if (entities.length === 0) {
    // 🪤 The `loading` guard is load-bearing and a test caught its absence: the
    // caption renders `sceneSummary([])` = "Nothing is running.", so the FIRST paint
    // of a busy machine asserted the opposite of the truth for as long as the four
    // GETs took. An empty scene is a CLAIM, and it may only be made once measured.
    if (loading) return null
    return <SlotEmptyState icon={Orbit}>Nothing is running. Loops, chats and subagents appear here as they start.</SlotEmptyState>
  }

  return (
    <div className="flex min-w-0 flex-col gap-s">
      {/* The canvas is `role="img"` with the summary as its name — a moving dot field
          is invisible to assistive tech, and the same facts are repeated visibly in
          the caption below, so nobody depends on seeing the animation. */}
      <div className="relative min-w-0 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-low">
        <canvas
          ref={attachCanvas}
          role="img"
          aria-label={`Agent world. ${summary}`}
          className="block h-[15rem] w-full sm:h-[18rem]"
        />
        {/* No drawing context (headless, or canvas blocked by an extension): the
            world degrades to the same scene as a plain list, overlaid in the same
            box, rather than leaving a blank rectangle. `tier === null` (not probed
            yet) deliberately renders NEITHER — showing the list before we have looked
            would flash it on every mount of a perfectly capable browser. */}
        {tier === 'static' && (
          <ul className="absolute inset-0 flex flex-col gap-xs overflow-y-auto p-m">
            {settled.map((n) => (
              <StaticRow key={n.id} entity={entities.find((e) => e.id === n.id)} />
            ))}
          </ul>
        )}
      </div>
      <p data-type="body-s" className="text-on-surface-low">{summary}</p>
    </div>
  )
}

/** One row of the no-canvas fallback. Deliberately the same three facts a node
 *  paints — kind weight, state tone, title — so the fallback is the scene in text,
 *  not a different feature. */
function StaticRow({ entity }: { entity: AgentActivityEntity | undefined }) {
  if (!entity) return null
  const v = STATE_VISUAL[entity.state]
  return (
    <li className="flex min-w-0 items-center gap-s" data-type="body-s">
      <span
        aria-hidden="true"
        className="shrink-0 rounded-full"
        style={{
          background: `var(${v.tone})`,
          width: `${0.45 + KIND_SCALE[entity.kind] * 0.3}rem`,
          height: `${0.45 + KIND_SCALE[entity.kind] * 0.3}rem`,
        }}
      />
      <span className="truncate text-on-surface-var">{entity.title}</span>
    </li>
  )
}
