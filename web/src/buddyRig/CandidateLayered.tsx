/** Rig candidate 2 (#3887): the agent orb's drawing inside a code-drawn layer.
 *
 *  The drawing is the agent orb's own component, given only parameters for its dots' density and
 *  arrangement (its `frame`, from the pose). The badge, the wash and the whole orb's rise and fall
 *  sit in an SVG layer around it, moved by the same presets through motion values, and painted with
 *  the tokens as CSS variables, so a scheme change reaches them with no reading of styles at all
 *  (`docs/design/buddy/rig.md`, "Candidates"). */

import { useEffect, useId, useMemo, useRef } from 'react'
import { motion, useTransform } from 'framer-motion'
import { ThinkingOrb } from 'thinking-orbs'
import { useMode } from '../app/theme'
import { modeOf, poseFrame } from './arrange'
import { useMayMove, useOnScreen, useRigInks, type CandidateProps } from './candidate'
import { BADGE_GLYPHS, poseOf, rigLook, tunedDrawingFor } from './poses'
import { usePose } from './usePose'

export function CandidateLayered({ phase, size, still = false, dials = '', onPose }: CandidateProps) {
  const look = rigLook(phase)
  const pose = usePose(phase, dials)
  const mayMove = useMayMove()
  const { mode } = useMode()
  const { inkToken, ink, tone } = useRigInks(look)
  const root = useRef<HTMLSpanElement>(null)
  const onScreen = useOnScreen(root)
  const dotsMove = look.moves && !still && mayMove
  const running = (dotsMove || (pose.moving && mayMove)) && onScreen
  const washId = `rig-wash-${useId().replace(/[^\w-]/g, '')}`
  const { values } = pose
  const y = useTransform(values.lift, (lift) => lift * size)

  useEffect(() => { onPose?.(pose) }, [onPose, pose])

  // A new frame function for each pose and each still-or-moving answer: the drawing paints it once
  // when it changes, which is what shows a pose that was entered instantly under reduced motion.
  const frame = useMemo(
    () => poseFrame(modeOf(look.drawing), pose.read, { still: !dotsMove, lift: false }),
    [look.drawing, pose.read, dotsMove, phase, dials],
  )

  const tuned = tunedDrawingFor(size)
  const tint = `var(--color-${look.tone === 'neutral' ? 'orb-rest' : look.tone})`
  const badge = { r: Math.max(4, size * 0.17), cx: size * 0.86, cy: size * 0.14 }

  return (
    <span
      ref={root} data-rig-candidate="layered" data-rig-phase={phase} data-rig-family={look.family}
      data-rig-moving={String(running)} data-rig-ink={ink ?? ''} data-rig-ink-token={inkToken}
      data-rig-tone={look.tone} data-rig-tone-ink={tone ?? ''}
      {...(look.badge ? { 'data-rig-badge': look.badge } : {})}
      {...(poseOf(phase).wash > 0 ? { 'data-rig-wash': look.tone } : {})}
      className="relative inline-block shrink-0"
      style={{ width: size, height: size }}
    >
      <motion.span className="absolute inset-0 block" style={{ y }}>
        <ThinkingOrb
          state={look.drawing} size={tuned} theme={mode} color={ink} paused={!running}
          frame={frame} aria-label={look.label} style={{ width: size, height: size }}
        />
        <svg
          aria-hidden="true" width={size} height={size} viewBox={`0 0 ${size} ${size}`}
          className="pointer-events-none absolute inset-0 overflow-visible"
        >
          <g data-rig-wash-layer="">
            <defs>
              <linearGradient id={washId} x1="0" y1="1" x2="0" y2="0">
                <stop offset="0" stopColor={tint} stopOpacity="0.55" />
                <stop offset="0.5" stopColor={tint} stopOpacity="0" />
              </linearGradient>
            </defs>
            <motion.circle cx={size / 2} cy={size / 2} r={size / 2} fill={`url(#${washId})`} style={{ opacity: values.wash }} />
          </g>
          {look.badge && (
            <g data-rig-badge-layer="" style={{ color: tint }}>
              <circle cx={badge.cx} cy={badge.cy} r={badge.r} fill="var(--color-surface)" stroke="currentColor" strokeWidth={Math.max(1, size * 0.02)} />
              <text
                x={badge.cx} y={badge.cy} textAnchor="middle" dominantBaseline="central" fill="currentColor"
                fontSize={badge.r * (look.badge === 'working' ? 0.9 : 1.3)} className="font-semibold"
              >
                {BADGE_GLYPHS[look.badge]}
              </text>
            </g>
          )}
        </svg>
      </motion.span>
    </span>
  )
}
