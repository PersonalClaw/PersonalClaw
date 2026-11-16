/** The buddy orb (#3891): the agent orb's claw ring for every state the buddy shows, moved by its
 *  pose (`docs/design/buddy/character.md`, "The poses"), with its exact line beside it so the art
 *  never carries the state alone.
 *
 *  Everything is drawn into one canvas with the agent orb's own form and painter: the state's form
 *  (`lib/orbState.ts`, `ui/orb/clawRing.ts`), as its pose moves it, the whole orb's rise and fall,
 *  and the badge and the wash in strokes in the alert's tone (`ui/buddy/buddyRing.ts`,
 *  `ui/buddy/paintOrb.ts`). The strokes take the alive ink while an agent works or waits on the
 *  owner and the rest ink where nothing is, both tokens, so a scheme change retints within a frame.
 *  A change of state morphs as the agent orb's does (`ui/orb/useMorph.ts`); the buddy's done has no
 *  bloom, since its form stays the open ring, and its flourish is done's turn.
 *  One loop paints all of it, at most at the buddy's frame rate (`ui/orbDots.ts`), and only while
 *  the strokes, the morph or the pose move: a pose that holds draws nothing. Under reduced motion
 *  every state is its still, the pose and the form jumping between stills, and the branch is stated
 *  on `data-buddy-orb`. The buddy window, the playground, the card and the phone's views all draw
 *  this one component. */

import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { prefersReducedMotion } from '../design/motion'
import { runtime } from '../design/runtime'
import { onEditorTokensChange } from '../design/editorTheme'
import type { BuddySize } from '../buddyRig/poses'
import { inkOf, onOrbMediaChange, orbContrast, useInk } from './AgentOrb'
import { formAt, restForm } from './orb/clawRing'
import { useMorph } from './orb/useMorph'
import { PAD, posedForm } from './buddy/buddyRing'
import { familyOf, movesOf, orbLookOf, poseOf, stillOf, transitionOf, wordsOf, type BuddyState } from './buddy/orbPoses'
import { paintOrb } from './buddy/paintOrb'
import { useOrbPose } from './buddy/useOrbPose'
import { runDots, tintOf, useOnScreen } from './orbDots'

/** What the platform asks of the orb: `reduced|contrast`, as the agent orb reads it. */
const mediaNow = () => `${prefersReducedMotion() ? 'reduced' : 'motion'}|${orbContrast()}`
const pageShown = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'
function onVisibilityChange(cb: () => void): () => void {
  document.addEventListener('visibilitychange', cb)
  return () => document.removeEventListener('visibilitychange', cb)
}
const dialsNow = () => `${runtime.bounciness}|${runtime.expressiveness}`

export interface BuddyOrbProps {
  /** What the orb shows: a phase of the contract, or restarting or offline. */
  state: BuddyState
  /** The state's exact line (`lib/buddy/copy.ts`), shown beside the orb. */
  line: string
  /** 18 to 96 px: the strokes are drawn wider at 20 px and below, as the agent orb's are. */
  size?: BuddySize
  /** Hold even a moving state still: a contact sheet's frames. */
  still?: boolean
}

export function BuddyOrb({ state, line, size = 48, still = false }: BuddyOrbProps) {
  const [motion, contrast] = useSyncExternalStore(onOrbMediaChange, mediaNow, mediaNow).split('|') as ['reduced' | 'motion', ReturnType<typeof orbContrast>]
  const reduced = motion === 'reduced'
  const shown = useSyncExternalStore(onVisibilityChange, pageShown, pageShown)
  const dials = useSyncExternalStore(onEditorTokensChange, dialsNow, dialsNow)
  const frameOf = useMemo(() => stillOf(state), [state, dials]) // eslint-disable-line react-hooks/exhaustive-deps -- a still reads the dials
  const family = familyOf(state)
  const pose = useOrbPose(`${state}|${dials}`, {
    targetOf: (key, now) => (key === 'swirl' ? now + poseOf(state).swirl : poseOf(state)[key]),
    transitionOf: (key) => transitionOf(state, key),
  })
  const mayMove = !reduced && shown
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  // As the agent orb's: a change nobody can see is an instant swap, and one cut short settles.
  const morph = useMorph(orbLookOf(state), !mayMove || still || !onScreen)
  // As the agent orb's: under more contrast the strokes take the words' ink, and under forced
  // colours the system's, the badge and the wash with them.
  const inkToken = contrast === 'forced' ? 'CanvasText' : contrast === 'more' ? '--color-on-surface' : frameOf.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest'
  const toneToken = contrast === 'forced' ? 'CanvasText' : frameOf.tone === 'neutral' ? '--color-orb-rest' : `--color-${frameOf.tone}`
  const ink = useInk(inkToken)
  const tone = useInk(toneToken)
  const strokesMove = movesOf(state) && !still && mayMove
  const running = (strokesMove || (pose.moving && mayMove) || morph.changing !== 'none') && onScreen
  const { values } = pose

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const pad = Math.round(size * PAD)
    const box = size + pad * 2
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    let inkTint = tintOf(ink)
    let toneTint = tintOf(tone)
    const paint = (t: number) => {
      const base = morph.formOf(strokesMove ? formAt(frameOf.form, t) : restForm(frameOf.form))
      const form = posedForm(base, { density: values.density.get(), spread: values.spread.get(), sink: values.sink.get(), swirl: values.swirl.get() })
      paintOrb(ctx, form, {
        size, pad, dpr, ink: inkTint, tone: toneTint, dim: frameOf.dim,
        lift: values.lift.get(), sink: values.sink.get(), wash: values.wash.get(), mark: frameOf.mark, swell: morph.scale(),
      })
    }
    // A scheme change reaches the painter as the change lands: the tokens are read again and the
    // strokes repainted at once, moving or still.
    const retint = () => {
      inkTint = tintOf(inkOf(inkToken))
      toneTint = tintOf(inkOf(toneToken))
    }
    return runDots(paint, { running, speed: 1, retint })
  }, [size, ink, tone, inkToken, toneToken, running, values, frameOf, strokesMove, state, dials, morph.changing]) // eslint-disable-line react-hooks/exhaustive-deps -- the morph is read through its refs at each paint

  const pad = Math.round(size * PAD)
  return (
    <span
      data-buddy-orb={reduced ? 'still' : 'animated'} data-buddy-state={state} data-buddy-family={family} data-buddy-form={frameOf.form}
      data-buddy-moving={String(running)} data-buddy-change={morph.changing} data-buddy-ink={ink ?? ''} data-buddy-ink-token={inkToken} data-buddy-contrast={contrast} data-buddy-tone={frameOf.tone}
      {...(frameOf.mark ? { 'data-buddy-badge': frameOf.mark } : {})}
      className="inline-flex min-w-0 items-center gap-2"
    >
      <span className="relative inline-block shrink-0" style={{ width: size, height: size }}>
        {/* Decoration (#3902): its state is said in words beside it, by the line or, without one,
            by the words below, never by the drawing. */}
        <canvas
          ref={canvas} aria-hidden="true"
          className="absolute block"
          style={{ width: size + pad * 2, height: size + pad * 2, left: -pad, top: -pad }}
        />
      </span>
      {/* Without a line nothing sits beside the art, so an orb in a slot is in its middle (#5216). */}
      {line ? <span data-buddy-line="" data-type="body-s" className="min-w-0 truncate text-on-surface">{line}</span> : <span data-buddy-words="" className="sr-only">{wordsOf(state)}</span>}
    </span>
  )
}
