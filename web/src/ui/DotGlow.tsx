import { useEffect, useRef } from 'react'
import { prefersReducedMotion } from '../design/motion'
import { runtime } from '../design/runtime'
import { useAppearance } from '../app/appearance'
import { TOKENS } from '../design/tokenRegistry'

// The master backdrop switch (design/tokenRegistry '--bg-style'):
//   'waves' — the animated 3D dot-wave surface (default)
//   'still' — the same dot field, frozen (no breathing; one static frame)
//   'glow'  — only the soft light hugging the composer, no dot lattice
//   'none'  — off (a transparent, empty layer)
// Resolved LIVE from the appearance store below so a change re-keys the render
// loop (a runtime-bridge read couldn't restart a loop that 'still'/'none' stops).
type BgStyle = 'waves' | 'still' | 'glow' | 'none'
const BG_STYLE_TOKEN = TOKENS.find((t) => t.kind === 'select' && t.varName === '--bg-style')

/** "dot glow" — an animated 3D halftone WAVE surface that
 *  reads as light cast by the composer onto a rippling surface behind it.
 *
 *  This is a REAL 3D height field, not a 2D plane with fake perspective:
 *   - a grid of points lives in world space (x across, z into the screen) on a
 *     ground plane that recedes from the viewer,
 *   - each point is displaced in the HEIGHT (y) axis by two traveling waves,
 *   - points are projected through a pitched pinhole camera, so depth affects
 *     both screen position AND dot scale (far = higher on screen, smaller,
 *     tighter; near = lower, larger) — genuine 3D undulation toward a horizon,
 *   - brightness = wave crest × radial falloff from the light origin (the
 *     composer) × distance fade, lavender→pink.
 *
 *  Canvas + requestAnimationFrame. Respects prefers-reduced-motion (static
 *  frame). Decorative; fills its stage (the box it is mounted in) and lights it
 *  from the composer (see ComposerStage).
 *
 *  ONE light, and it never meets an edge. Both halves are measured decisions:
 *
 *   · There used to be a SECOND light that split off the composer on send and
 *     parked behind the streaming turn. It had no surface to come from — a 1px
 *     anchor in the transcript — so it rendered as a ~180px smudge behind the
 *     text that climbed the page with the auto-scroll and jumped when turns
 *     appeared (measured: 638px → 192px in 20s of one reply). On a light canvas a
 *     tinted glow LOWERS luminance, so there it was literally a grey shadow
 *     ((240,244,248) → (239,239,242) at its core). It is gone, not tuned: light
 *     with no emitter reads as a stain at any strength that is visible at all.
 *   · The bloom reaches `HALO_REACH` past the composer, but a docked composer sits
 *     one page gutter (16px) from the stage's bottom and, at the shipped `full`
 *     width, from its sides. The layer's own `overflow-hidden` then cut the halo
 *     at ~94% strength into a straight line at the rail and the page bottom. So
 *     the layer now fades its light to zero before each edge, over exactly the
 *     room there is (`haloFades`), and the clip underneath is never reached. */
export interface GlowRect { cx: number; cy: number; halfW: number; halfH: number; radius?: number }

/** The bloom's box-shadow geometry. Declared once so the paint and the reach the
 *  edge fades clamp to can never disagree. */
const BLOOM_SPREAD = 60
const BLOOM_BLUR = 120
/** The coral aura a FOCUSED composer gathers close around it. It used to be painted by
 *  the composer itself (the cards' `--shadow-lift`), where the page gutter clipped it
 *  into a line; here it lives inside the edge-faded layer, so it keeps its ~40px reach
 *  and still never meets an edge. Well inside `HALO_REACH`, so the fades cover it. */
const FOCUS_BLOOM = '0 0 48px -6px color-mix(in srgb, var(--glow-a) 50%, transparent)'
/** How far past the light source's edge the bloom is still visible: its spread plus
 *  its blur radius (a blur radius is ~2σ of the Gaussian, out from the spread edge). */
export const HALO_REACH = BLOOM_SPREAD + BLOOM_BLUR

/** Per-edge fade widths, in the stage's layout px. */
export interface HaloFades { l: number; r: number; t: number; b: number }

/** How wide the halo's fade to nothing must be at each edge of its stage.
 *
 *  The room between the light and that edge, capped at the light's reach: where the
 *  halo has its full reach it falls off on its own and the fade is a no-op; where it
 *  does not, the fade spends exactly the room there is, so the light is at full
 *  strength where it leaves the composer and at zero where the stage ends — never
 *  cut. No source (nothing measured yet) fades every edge by the full reach, so the
 *  unanchored fallback field is contained too. */
export function haloFades(src: GlowRect | null, w: number, h: number, reach = HALO_REACH): HaloFades {
  if (!src) return { l: reach, r: reach, t: reach, b: reach }
  const room = (px: number) => Math.round(Math.max(0, Math.min(reach, px)))
  return {
    l: room(src.cx - src.halfW),
    r: room(w - (src.cx + src.halfW)),
    t: room(src.cy - src.halfH),
    b: room(h - (src.cy + src.halfH)),
  }
}

/** The CSS custom properties `haloFades` is written to, on the layer itself. */
export const HALO_FADE_VARS = { l: '--halo-fade-l', r: '--halo-fade-r', t: '--halo-fade-t', b: '--halo-fade-b' } as const

/** One edge-to-edge axis of the mask: transparent AT each edge, opaque once its fade
 *  width is spent. The ramp is smoothstep-shaped (0 · .16 · .5 · .84 · 1 at quarter
 *  steps) so it has zero slope at both ends — neither the stage edge nor the point
 *  where the fade begins shows as a crease. */
function maskAxis(dir: 'right' | 'bottom', start: string, end: string): string {
  const a = `var(${start}, 0px)`
  const b = `var(${end}, 0px)`
  return `linear-gradient(to ${dir}, `
    + `rgb(0 0 0 / 0) 0, rgb(0 0 0 / .16) calc(${a} * .25), rgb(0 0 0 / .5) calc(${a} * .5), rgb(0 0 0 / .84) calc(${a} * .75), #000 ${a}, `
    + `#000 calc(100% - ${b}), rgb(0 0 0 / .84) calc(100% - ${b} * .75), rgb(0 0 0 / .5) calc(100% - ${b} * .5), rgb(0 0 0 / .16) calc(100% - ${b} * .25), rgb(0 0 0 / 0) 100%)`
}

/** The layer's mask: the two axes INTERSECTED, so a corner fades on both. (The default
 *  composite is `add` — a union — which would leave every edge unfaded wherever the
 *  other axis is opaque.) */
export const HALO_MASK = `${maskAxis('right', HALO_FADE_VARS.l, HALO_FADE_VARS.r)}, ${maskAxis('bottom', HALO_FADE_VARS.t, HALO_FADE_VARS.b)}`

/** Draw one dot of the given shape at (x,y) with radius r. */
function drawDot(g: CanvasRenderingContext2D, shape: string, x: number, y: number, r: number) {
  switch (shape) {
    case 'square':
      g.fillRect(x - r, y - r, r * 2, r * 2)
      return
    case 'diamond':
      g.beginPath(); g.moveTo(x, y - r); g.lineTo(x + r, y); g.lineTo(x, y + r); g.lineTo(x - r, y); g.closePath(); g.fill()
      return
    case 'star': {
      // 5-point star
      g.beginPath()
      for (let k = 0; k < 10; k++) {
        const rad = k % 2 === 0 ? r : r * 0.45
        const a = -Math.PI / 2 + (k * Math.PI) / 5
        const px = x + Math.cos(a) * rad, py = y + Math.sin(a) * rad
        k === 0 ? g.moveTo(px, py) : g.lineTo(px, py)
      }
      g.closePath(); g.fill()
      return
    }
    case 'sparkle': {
      // 4-point concave spark (the Gemini/AI-magic star)
      const o = r, iN = r * 0.32
      g.beginPath()
      g.moveTo(x, y - o)
      g.quadraticCurveTo(x + iN, y - iN, x + o, y)
      g.quadraticCurveTo(x + iN, y + iN, x, y + o)
      g.quadraticCurveTo(x - iN, y + iN, x - o, y)
      g.quadraticCurveTo(x - iN, y - iN, x, y - o)
      g.closePath(); g.fill()
      return
    }
    case 'burst': {
      // 6-point asterisk/burst (thin rays)
      const lw = Math.max(0.6, r * 0.5)
      for (let k = 0; k < 3; k++) {
        const a = (k * Math.PI) / 3
        g.save(); g.translate(x, y); g.rotate(a)
        g.fillRect(-lw / 2, -r, lw, r * 2)
        g.restore()
      }
      return
    }
    case 'claude': {
      // Anthropic Claude "sunburst" mark — radiating tapered spokes
      const spokes = 11
      for (let k = 0; k < spokes; k++) {
        const a = (k * 2 * Math.PI) / spokes
        const lw = Math.max(0.5, r * 0.22)
        g.save(); g.translate(x, y); g.rotate(a)
        g.beginPath()
        g.moveTo(-lw, 0); g.lineTo(lw, 0); g.lineTo(0, -r * 1.15); g.closePath(); g.fill()
        g.restore()
      }
      return
    }
    default: // circle
      g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill()
  }
}

export function DotGlow({
  className, intensity = 1, focused = false, composerRef,
}: {
  className?: string
  intensity?: number
  /** The composer has the caret: the halo gathers a coral aura close around it
   *  (`FOCUS_BLOOM`), cross-fading in and out over 200ms. */
  focused?: boolean
  /** ref to the composer element; the glow measures it LIVE each frame so it
   *  tracks the composer exactly in sync (the composer itself spring-animates
   *  its position), with no separate easing. Glow falls off a uniform distance
   *  from the composer's rounded-rect edges. */
  composerRef?: React.RefObject<HTMLElement | null>
}) {
  // Master backdrop mode, resolved live from the appearance store so a change
  // re-keys the render effect below (tear down + rebuild the loop cleanly).
  const { selectValue } = useAppearance()
  const bgStyle = (BG_STYLE_TOKEN ? selectValue(BG_STYLE_TOKEN) : 'waves') as BgStyle
  // Reduced motion through `design/motion`'s ONE accessor, read in RENDER rather than
  // inside the effect. Two reasons, and the second is the reason it moved:
  //   • it re-keys the effect exactly like `bgStyle` does (tear down + rebuild the loop),
  //     so a mid-session OS change stops the loop instead of leaving it running forever;
  //   • it can be stated on the element, which is what lets the family's rail assert the
  //     decision from the DOM instead of counting frames through a canvas stub.
  // Still CALL-TIME, not a cached hook: `DotGlow.reducedMotion.test.tsx` swaps the
  // `matchMedia` stub between its two describes and depends on the second one seeing the
  // new answer, which framer's module-cached `useReducedMotion` would not give it.
  const reduce = prefersReducedMotion()
  const ref = useRef<HTMLCanvasElement>(null)
  const bloomRef = useRef<HTMLDivElement>(null)
  const focusBloomRef = useRef<HTMLDivElement>(null)
  // target glow intensity (1 = rest, >1 = composer focused/lifted); lerped.
  const targetI = useRef(intensity)
  targetI.current = intensity

  useEffect(() => {
    const canvas: HTMLCanvasElement | null = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const cv = canvas
    const g = ctx

    // Backdrop mode → what the loop does:
    //   drawDots — render the dot lattice ('waves' + 'still').
    //   animate  — keep a live rAF loop ('waves' + 'glow', unless reduced-motion).
    //              'still' draws exactly one frozen frame, like reduced-motion.
    const drawDots = bgStyle === 'waves' || bgStyle === 'still'
    const animate = (bgStyle === 'waves' || bgStyle === 'glow') && !reduce
    // 'none' → a transparent, empty layer: clear the canvas, hide the blooms,
    // never start the loop or observe resizes.
    if (bgStyle === 'none') {
      g.setTransform(1, 0, 0, 1, 0, 0)
      g.clearRect(0, 0, cv.width, cv.height)
      if (bloomRef.current) bloomRef.current.style.opacity = '0'
      return
    }
    let raf = 0
    let w = 0, h = 0, dpr = 1

    // The layer itself: its box is the STAGE the light is contained in, and it
    // carries the edge fades the mask reads.
    const parent = cv.parentElement!
    let fades: HaloFades | null = null
    const writeFades = (next: HaloFades) => {
      // Only on change: a style write per frame would re-resolve the mask 60×/s
      // for a value that moves only while the composer does.
      if (fades && next.l === fades.l && next.r === fades.r && next.t === fades.t && next.b === fades.b) return
      fades = next
      for (const k of ['l', 'r', 't', 'b'] as const) parent.style.setProperty(HALO_FADE_VARS[k], `${next[k]}px`)
    }
    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = parent.clientWidth
      h = parent.clientHeight
      cv.width = Math.floor(w * dpr)
      cv.height = Math.floor(h * dpr)
      cv.style.width = w + 'px'
      cv.style.height = h + 'px'
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    // A resize reassigns cv.width/height, which CLEARS the canvas. When the loop
    // is live it repaints on the next rAF, but a non-animating mode ('still' /
    // reduced-motion) paints only ONE frame — and rAF callbacks run before the
    // ResizeObserver's within a frame, so the observer's initial fire would wipe
    // that single frame and leave the field blank. Repaint once after any resize
    // in those modes (guarded so the live loop is never double-scheduled).
    const ro = new ResizeObserver(() => {
      resize()
      if (!animate) { cancelAnimationFrame(raf); raf = requestAnimationFrame(frame) }
    })
    ro.observe(parent)

    let inten = targetI.current                // lerped glow intensity

    // ── 3D ground-plane scene (perspective floor, steep ~45° tilt) ──
    // Camera at height CAM_H above a floor (y=0), pitched toward it. A floor
    // point at forward distance z, lateral x projects to:
    //   sx = cx + (x / z)·f ,  sy = horizon + ((CAM_H - waveHeight)/z)·f
    // A LOW camera + long depth makes the plane recede steeply (≈45° to the
    // screen) rather than reading flat. The wave lifts the floor in y.
    // Steep tilt comes from the CAMERA geometry (low height + long depth), NOT
    // from violent waves — keep the dot lattice ORDERLY with gentle swells.
    // Big, dense, deep plane so its near/far/side EDGES always fall outside the
    // visible (glow-lit) region — you never see where the field ends. High focal
    // zooms the near ground down past the screen bottom (fills the view); a small
    // near distance + far horizon make a continuous "field" at any pitch.
    const COLS_BASE = 150    // grid columns (lateral) at density 1
    const ROWS_BASE = 130    // grid rows (depth) at density 1
    const PLANE_W = 30       // NARROW world → tight column spacing = dense FIELD
    const NEAR_Z = 0.02      // essentially at the camera → near rows run far OFF the bottom (no near edge)
    const FAR_Z_BASE = 40    // bounded depth → no hard converging "ray" tip
    const AMP = 1.0          // rolling-hill relief
    const CAM_H = 3.4        // camera height → looking down from the sky
    const FOCAL = 1.15       // perspective focal length

    function frame(tms: number) {
      // customizable params (read live from the appearance runtime bridge)
      const t = (tms / 1000) * runtime.animSpeed
      const amp = AMP * runtime.waveAmount
      // point-of-view:
      //  • angle → TRUE camera pitch in degrees (0 = edge-on/steep recession,
      //    90 = top-down flat; up to 180 keeps turning past top-down).
      //  • distance → how far the horizon recedes; near edge fixed (no cut-off).
      //  • density → dot count / spacing.
      const pitch = (runtime.surfaceAngle * Math.PI) / 180   // radians
      const sinP = Math.sin(pitch)
      const cosP = Math.cos(pitch)
      const FAR_Z = FAR_Z_BASE * runtime.surfaceDistance
      const density = runtime.dotDensity
      const COLS = Math.round(COLS_BASE * density)
      const ROWS = Math.round(ROWS_BASE * density)
      const dotSizeMul = runtime.dotSize
      const dotShape = runtime.dotShape
      const pattern = runtime.dotPattern
      const colStep = (2 * PLANE_W) / COLS
      const glowMul = runtime.glow
      const COLOR = { r: runtime.glowA[0], g: runtime.glowA[1], b: runtime.glowA[2] }
      const COLOR2 = { r: runtime.glowB[0], g: runtime.glowB[1], b: runtime.glowB[2] }
      g.clearRect(0, 0, w, h)
      inten += (targetI.current - inten) * 0.12

      // Measure an element into a canvas-local GlowRect. The canvas draws + the
      // CSS bloom position in UNZOOMED layout px, but getBoundingClientRect
      // returns ZOOMED visual px when the UI-zoom design control is active
      // (`html { zoom }`) — divide back into layout space so the glow stays
      // locked at any zoom.
      const pr = cv.getBoundingClientRect()
      const z = parseFloat(getComputedStyle(document.documentElement).zoom) || 1
      const measure = (el: HTMLElement): GlowRect => {
        const cr = el.getBoundingClientRect()
        // Match the bloom's rounding to the SOURCE's actual corner radius so the
        // soft-light halo hugs the composer's real shape. The composer morphs its
        // radius (mobile rest = soft capsule 2xl; focus/desktop = xli) — a hardcoded
        // bloom radius left the halo's tighter corners poking past the composer's
        // rounder ones (the mobile corner artifact). Read the rounded SURFACE (the
        // outer ref may be an unrounded wrapper, so prefer the largest rounded
        // descendant), zoom-corrected into layout px.
        const rounded = el.querySelector<HTMLElement>('[style*="border-radius"]') ?? el
        const brRaw = parseFloat(getComputedStyle(rounded).borderTopLeftRadius) || 0
        return { cx: (cr.left - pr.left + cr.width / 2) / z, cy: (cr.top - pr.top + cr.height / 2) / z, halfW: (cr.width / 2) / z, halfH: (cr.height / 2) / z, radius: brRaw / z }
      }

      // The light: the composer, measured LIVE each frame (it spring-animates its
      // own position) → glow tracks it in sync, no easing. This ALWAYS stays lit
      // (the glow never leaves the composer).
      let rc: GlowRect | null = null
      const cel = composerRef?.current
      if (cel) rc = measure(cel)
      // …and never reaches the stage's edges: the fades follow the same live rect,
      // so a composer that moves (hero → dock) or grows keeps its halo whole.
      writeFades(haloFades(rc, w, h))
      // drive the soft CSS bloom from the same live rect (in sync)
      if (bloomRef.current && rc) {
        const b = bloomRef.current.style
        b.left = rc.cx - rc.halfW + 'px'; b.top = rc.cy - rc.halfH + 'px'
        b.width = rc.halfW * 2 + 'px'; b.height = rc.halfH * 2 + 'px'
        if (rc.radius != null) b.borderRadius = rc.radius + 'px'  // hug the composer's live corner radius
        b.opacity = '1'
      } else if (bloomRef.current) {
        bloomRef.current.style.opacity = '0'
      }
      // The focus aura rides the same rect. Its OPACITY is React's (the `focused`
      // prop, on the effects curve), so only placement is written here.
      if (focusBloomRef.current) {
        const f = focusBloomRef.current.style
        f.visibility = rc ? '' : 'hidden'
        if (rc) {
          f.left = rc.cx - rc.halfW + 'px'; f.top = rc.cy - rc.halfH + 'px'
          f.width = rc.halfW * 2 + 'px'; f.height = rc.halfH * 2 + 'px'
          if (rc.radius != null) f.borderRadius = rc.radius + 'px'
        }
      }
      // The surface geometry is WORLD-FIXED to the canvas — it never moves with
      // the composer. Horizon + centre are pinned; only the illumination
      // (`prox`, below, from the composer rect) travels.
      const horizonPx = h * 0.32
      const focalPx = FOCAL * h * 0.5
      const cxPx = w / 2

      // 'glow' mode keeps the composer's soft light (the CSS bloom above) but
      // skips the dot lattice entirely — the canvas stays clear behind it.
      if (drawDots) for (let j = ROWS - 1; j >= 0; j--) {
        const dz = j / (ROWS - 1)
        const wz = NEAR_Z + (FAR_Z - NEAR_Z) * (dz * dz)   // ease → dense horizon
        // arrangement pattern → per-row lateral offset / skipping of the lattice
        const odd = j % 2 === 1
        let rowOffset = 0
        if (pattern === 'diamond' || pattern === 'hex') rowOffset = odd ? colStep * 0.5 : 0
        else if (pattern === 'brick') rowOffset = odd ? colStep * 0.5 : 0
        for (let i = 0; i <= COLS; i++) {
          // hex drops every other dot on offset rows for a true honeycomb feel
          if (pattern === 'hex' && odd && i % 2 === 0) continue
          // lateral position + arrangement offset + a depth-varying meander so
          // columns DON'T trace straight radial lines (which read as rays).
          const wx = (i / COLS - 0.5) * 2 * PLANE_W + rowOffset + Math.sin(wz * 0.4 + t * 0.2) * 1.6

          // height field: smooth rolling HILLS — long-wavelength swells in both
          // x and z that drift over time (a gentle wave across a hilly field).
          const wave =
            Math.sin(wx * 0.22 + wz * 0.18 + t * 0.5) * 0.5 +
            Math.sin(wx * 0.12 - wz * 0.26 - t * 0.32) * 0.5
          const wy = wave * amp                              // hill height

          // Camera pitched DOWN toward a floor (y = -CAM_H below the eye). Pitch
          // 0° = edge-on (steep recession to a horizon); 90° = straight down
          // (top-down, no recession). Rotate the point (wz forward, depth0 down)
          // by the pitch into camera space; project. cz stays positive for the
          // floor across 0–90°, then the surface tips past top-down up to 180°.
          const depth0 = CAM_H - wy                   // how far the floor sits below the eye
          const cz = wz * cosP + depth0 * sinP        // forward distance in view
          if (cz <= 0.12) continue                    // at/behind camera → skip
          const cv2 = depth0 * cosP - wz * sinP       // vertical offset in view (down = +)
          const sx = cxPx + (wx / cz) * focalPx
          const sy = horizonPx + (cv2 / cz) * focalPx
          if (sx < -40 || sx > w + 40 || sy < -40 || sy > h + 40) continue

          const crest = (wave + 1) / 2
          // Falloff = uniform distance OUTWARD from a rounded-rect's edges (not a
          // centre oval): brightest near the edges, decaying with distance, tight
          // reach so it hugs the source.
          const REACH = Math.min(w, h) * 0.18
          let prox: number
          if (rc) {
            const dx = Math.max(Math.abs(sx - rc.cx) - rc.halfW, 0)
            const dy = Math.max(Math.abs(sy - rc.cy) - rc.halfH, 0)
            prox = Math.max(0, 1 - Math.hypot(dx, dy) / REACH) ** 3.4
          } else {
            const dxn = (sx - cxPx) / (Math.min(w, 1100) * 0.6)
            const dyn = (sy - h * 0.5) / (h * 0.34)
            prox = Math.max(0, 1 - Math.hypot(dxn, dyn)) ** 1.7
          }
          if (prox <= 0.01) continue
          const alpha = (0.08 + crest * 0.7) * prox * inten * glowMul
          if (alpha <= 0.012) continue

          // perspective dot size: nearer (small cz) = larger → depth cue ×size.
          // Size is independent of density, so the user can spread large dots
          // apart by LOWERING density (more space between).
          const r = Math.max(0.4, Math.min(9, (focalPx / cz) * 0.022)) * (0.75 + crest * 0.5) * dotSizeMul

          const mix = crest
          const cr = Math.round(COLOR.r * (1 - mix) + COLOR2.r * mix)
          const cg = Math.round(COLOR.g * (1 - mix) + COLOR2.g * mix)
          const cb = Math.round(COLOR.b * (1 - mix) + COLOR2.b * mix)
          g.fillStyle = `rgba(${cr},${cg},${cb},${Math.min(0.95, alpha)})`

          drawDot(g, dotShape, sx, sy, r)
        }
      }

      if (animate) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [composerRef, bgStyle, reduce])

  return (
    <div
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className ?? ''}`}
      aria-hidden
      data-dot-glow={reduce ? 'instant' : 'animated'}
      // The edge fades (see `haloFades`). `overflow-hidden` above still keeps the light
      // off the stage's neighbours; this is what keeps it from ever being CUT there.
      style={{ maskImage: HALO_MASK, WebkitMaskImage: HALO_MASK, maskComposite: 'intersect', WebkitMaskComposite: 'source-in' }}
    >
      {/* soft light bloom hugging the composer rect — positioned live each frame
          by the canvas loop (in perfect sync with the composer). */}
      <div
        ref={bloomRef}
        className="absolute"
        style={{
          borderRadius: 'var(--radius-xli)',
          boxShadow: `0 0 ${BLOOM_BLUR}px ${BLOOM_SPREAD}px color-mix(in srgb, var(--glow-a) 18%, transparent)`,
          opacity: 0,
        }}
      />
      {/* the focus aura — close around the composer while it has the caret. */}
      <div
        ref={focusBloomRef}
        data-dot-glow-focus
        className="absolute transition-opacity duration-200"
        style={{
          borderRadius: 'var(--radius-xli)',
          boxShadow: FOCUS_BLOOM,
          opacity: focused && bgStyle !== 'none' ? 1 : 0,
        }}
      />
      <canvas ref={ref} className="absolute inset-0" />
    </div>
  )
}
