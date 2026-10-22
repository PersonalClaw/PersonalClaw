/** Rig candidate 1 (#3887): the agent orb's drawing extended in place.
 *
 *  Everything is drawn into one canvas with the agent orb's own engine and painter: the phase's
 *  drawing with the pose's density and arrangement, the whole orb's rise and fall inside the frame,
 *  and the badge and the wash drawn with the dots, in the alert's tone. One frame loop paints all of
 *  it, at most at the buddy's frame rate, and runs only while the dots move or the pose does
 *  (`docs/design/buddy/rig.md`, "Candidates"). */

import { useEffect, useMemo, useRef } from 'react'
import { paintFrame, resolvePreset } from 'thinking-orbs/engine'
import { useMode } from '../app/theme'
import { inkOf } from '../ui/AgentOrb'
import { modeOf, poseFrame } from './arrange'
import { runDots, tintOf, useOnScreen } from '../ui/orbDots'
import { useMayMove, useRigInks, type CandidateProps } from './candidate'
import { PAD, badgeDots, washDots } from './inPlaceParts'
import { poseOf, rigLook, tunedDrawingFor } from './poses'
import { usePose } from './usePose'

export function CandidateInPlace({ phase, size, still = false, dials = '', onPose, fps }: CandidateProps) {
  const look = rigLook(phase)
  const pose = usePose(phase, dials, fps)
  const mayMove = useMayMove()
  const { mode } = useMode()
  const { inkToken, ink, toneToken, tone } = useRigInks(look)
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  const dotsMove = look.moves && !still && mayMove
  const running = (dotsMove || (pose.moving && mayMove)) && onScreen
  const { values } = pose

  useEffect(() => { onPose?.(pose) }, [onPose, pose])

  const preset = useMemo(() => resolvePreset(look.drawing, tunedDrawingFor(size)), [look.drawing, size])
  const frame = useMemo(() => poseFrame(modeOf(look.drawing), pose.read, { still: !dotsMove, lift: true }), [look.drawing, pose.read, dotsMove])

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const pad = Math.round(size * PAD)
    const box = size + pad * 2
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    const dark = mode === 'dark'
    let inkTint = tintOf(ink)
    let toneTint = tintOf(tone)
    const paint = (t: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, box, box)
      ctx.setTransform(dpr, 0, 0, dpr, pad * dpr, pad * dpr)
      paintFrame(ctx, frame(size, t, preset.opts), dark, inkTint)
      const dy = values.lift.get() * size
      const wash = values.wash.get()
      if (wash > 0.01) paintFrame(ctx, { dots: washDots(size, wash, dy), lines: [] }, dark, toneTint)
      if (look.badge) {
        const badge = badgeDots(size, look.badge, dy)
        ctx.globalCompositeOperation = 'destination-out'
        ctx.beginPath()
        ctx.arc(badge.cx, badge.cy, badge.radius * 1.15, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalCompositeOperation = 'source-over'
        paintFrame(ctx, { dots: badge.dots, lines: [] }, dark, toneTint)
      }
    }
    // A scheme change reaches the painter as the change lands: the tokens are read again and the
    // dots repainted at once, moving or still.
    const retint = () => {
      inkTint = tintOf(inkOf(inkToken))
      toneTint = tintOf(inkOf(toneToken))
    }
    return runDots(paint, { running, fps, speed: preset.speed, retint })
  }, [frame, preset, size, mode, ink, tone, inkToken, toneToken, running, look.badge, values, phase, dials, fps])

  const pad = Math.round(size * PAD)
  return (
    <span
      data-rig-candidate="in-place" data-rig-phase={phase} data-rig-family={look.family}
      data-rig-moving={String(running)} data-rig-ink={ink ?? ''} data-rig-ink-token={inkToken}
      data-rig-tone={look.tone} data-rig-tone-ink={tone ?? ''}
      {...(look.badge ? { 'data-rig-badge': look.badge } : {})}
      {...(poseOf(phase).wash > 0 ? { 'data-rig-wash': look.tone } : {})}
      className="relative inline-block shrink-0"
      style={{ width: size, height: size }}
    >
      <canvas
        ref={canvas} role="img" aria-label={look.label}
        className="absolute block"
        style={{ width: size + pad * 2, height: size + pad * 2, left: -pad, top: -pad }}
      />
    </span>
  )
}
