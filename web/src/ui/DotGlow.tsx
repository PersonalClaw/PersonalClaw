import { useEffect, useRef } from 'react'
import { runtime } from '../design/runtime'

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
 *  frame). Decorative; fills a box centred on the composer (see ComposerStage). */
export interface GlowRect { cx: number; cy: number; halfW: number; halfH: number; radius?: number }

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
  className, intensity = 1, composerRef, focusRef,
}: {
  className?: string
  intensity?: number
  /** ref to the composer element; the glow measures it LIVE each frame so it
   *  tracks the composer exactly in sync (the composer itself spring-animates
   *  its position), with no separate easing. Glow falls off a uniform distance
   *  from the composer's rounded-rect edges. */
  composerRef?: React.RefObject<HTMLElement | null>
  /** Optional DYNAMIC focus target (chat glow-travel). When provided and its
   *  `.current` is non-null, the glow focus TRAVELS toward this element's rect
   *  instead of the composer — a held rect lerps each frame, so switching the
   *  target (composer → landing message → back) reads as a smooth glide rather
   *  than a snap. When this prop is absent, behavior is identical to before
   *  (measure the composer live, no easing) so the other surfaces don't change. */
  focusRef?: React.RefObject<HTMLElement | null>
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const bloomRef = useRef<HTMLDivElement>(null)
  const bloom2Ref = useRef<HTMLDivElement>(null)  // bloom for the split-off traveling light
  // target glow intensity (1 = rest, >1 = composer focused/lifted); lerped.
  const targetI = useRef(intensity)
  targetI.current = intensity
  // stable holder for the dynamic focus ref so the rAF loop reads the latest
  // without re-subscribing the effect each render.
  const focusElRef = useRef(focusRef)
  focusElRef.current = focusRef

  useEffect(() => {
    const canvas: HTMLCanvasElement | null = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const cv = canvas
    const g = ctx

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let raf = 0
    let w = 0, h = 0, dpr = 1

    const parent = cv.parentElement!
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
    const ro = new ResizeObserver(resize)
    ro.observe(parent)

    let inten = targetI.current                // lerped glow intensity
    let held: GlowRect | null = null           // smoothed focus rect (glow-travel)
    let travelI = 0                            // lerped 0..1 strength of the split-off light (smooth fade in/out)
    const TRAVEL_STRENGTH = 0.55               // the traveling light is subtler than the composer's

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

      // PRIMARY light: the composer, measured LIVE each frame (it spring-animates
      // its own position) → glow tracks it in sync, no easing. This ALWAYS stays
      // lit (the glow never leaves the composer).
      let rc: GlowRect | null = null
      const cel = composerRef?.current
      if (cel) rc = measure(cel)

      // SECOND light (glow-travel): when a dynamic focus target is set (the active
      // turn's stable thinking anchor), a subtler glow SPLITS OFF from the composer
      // and travels with the message — additive, so the composer keeps its glow.
      // `held` seeds at the composer rect then lerps toward the target (detach +
      // glide). `travelI` lerps 0↔1 so the light FADES in on send and FADES out on
      // done (no hard pop). We keep `held` alive through the fade-out.
      const fel = focusElRef.current?.current ?? null
      travelI += ((fel ? 1 : 0) - travelI) * 0.08   // slow, graceful fade
      if (fel) {
        const target = measure(fel)
        if (!held) held = rc ?? target               // split off FROM the composer
        else {
          const k = 0.14                              // travel smoothing (visible glide)
          held = {
            cx: held.cx + (target.cx - held.cx) * k,
            cy: held.cy + (target.cy - held.cy) * k,
            halfW: held.halfW + (target.halfW - held.halfW) * k,
            halfH: held.halfH + (target.halfH - held.halfH) * k,
          }
        }
      } else if (travelI < 0.02) {
        held = null                                   // fully faded → drop the rect
      }
      const rc2 = (held && travelI > 0.02) ? held : null
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
      // second bloom rides the traveling (split-off) light; opacity tracks the
      // travelI fade so it eases in/out rather than popping.
      if (bloom2Ref.current && rc2) {
        const b = bloom2Ref.current.style
        b.left = rc2.cx - rc2.halfW + 'px'; b.top = rc2.cy - rc2.halfH + 'px'
        b.width = rc2.halfW * 2 + 'px'; b.height = rc2.halfH * 2 + 'px'
        if (rc2.radius != null) b.borderRadius = rc2.radius + 'px'  // hug the traveling target's corner radius
        b.opacity = String(Math.min(1, travelI * 1.2))
      } else if (bloom2Ref.current) {
        bloom2Ref.current.style.opacity = '0'
      }
      // The surface geometry is WORLD-FIXED to the canvas — it never moves with
      // the composer. Horizon + centre are pinned; only the illumination
      // (`prox`, below, from the composer rect) travels.
      const horizonPx = h * 0.32
      const focalPx = FOCAL * h * 0.5
      const cxPx = w / 2

      for (let j = ROWS - 1; j >= 0; j--) {
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
          // reach so it hugs the source. Two lights — the composer (rc) and the
          // split-off traveling light (rc2) — combine by MAX so the composer stays
          // lit while a second pool rides the active turn.
          const REACH = Math.min(w, h) * 0.18
          const proxOf = (q: GlowRect) => {
            const dx = Math.max(Math.abs(sx - q.cx) - q.halfW, 0)
            const dy = Math.max(Math.abs(sy - q.cy) - q.halfH, 0)
            return Math.max(0, 1 - Math.hypot(dx, dy) / REACH) ** 3.4
          }
          let prox: number
          if (rc || rc2) {
            // composer light at full strength; the traveling light scaled by its
            // fade (travelI) and a subtler ceiling (TRAVEL_STRENGTH).
            prox = Math.max(rc ? proxOf(rc) : 0, rc2 ? proxOf(rc2) * travelI * TRAVEL_STRENGTH : 0)
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

      if (!reduce) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [composerRef])

  return (
    <div className={`pointer-events-none absolute inset-0 overflow-hidden ${className ?? ''}`} aria-hidden>
      {/* soft light bloom hugging the composer rect — positioned live each frame
          by the canvas loop (in perfect sync with the composer). */}
      <div
        ref={bloomRef}
        className="absolute"
        style={{
          borderRadius: 'var(--radius-xli)',
          boxShadow: '0 0 120px 60px color-mix(in srgb, var(--glow-a) 18%, transparent)',
          opacity: 0,
        }}
      />
      {/* second bloom — the split-off light that travels with the sent message
          and sits behind the active turn while it streams. Subtler than the
          composer bloom; opacity is driven per-frame by the travelI fade. */}
      <div
        ref={bloom2Ref}
        className="absolute"
        style={{
          borderRadius: 'var(--radius-xli)',
          boxShadow: '0 0 90px 36px color-mix(in srgb, var(--glow-a) 11%, transparent)',
          opacity: 0,
        }}
      />
      <canvas ref={ref} className="absolute inset-0" />
    </div>
  )
}
