/** The buddy orb (#3891): the agent orb's halftone drawing for every state the buddy shows
 *  (`ui/buddy/buddyHalftone.ts`), with its exact line beside it so the art never carries the state
 *  alone.
 *
 *  Everything is drawn into one canvas with the agent orb's own frame painter: the halftone state for
 *  what the buddy shows (`buddySceneOf`), and, on the desktop's window, round 6's disc plate under it
 *  so it reads over any wallpaper. The dots take the orb's inks, all tokens, so a scheme change
 *  retints within a frame. A change of state is the agent orb's own (`ui/orb/useSceneMorph.ts`):
 *  every dot moves on the smooth spring, and done swirls and bumps once on arrival.
 *  One loop paints all of it, at most at the buddy's frame rate (`ui/orbDots.ts`), and only while
 *  the dots or the morph move: a still state draws nothing more. Under reduced motion every state is
 *  its still, and a change swaps; the branch is stated on `data-buddy-orb`. The buddy window, the
 *  playground, the card and the phone's views all draw this one component. */
import { useEffect, useRef, useSyncExternalStore } from 'react'
import { prefersReducedMotion } from '../design/motion'
import type { BuddySize } from '../buddyRig/poses'
import { onOrbMediaChange, orbContrast, orbInkTokens, orbInksOf, useInk } from './AgentOrb'
import { useSceneMorph } from './orb/useSceneMorph'
import { buddySceneOf, paintBuddy, PAD, STILL_WHOLE } from './buddy/buddyHalftone'
import { familyOf, movesOf, wordsOf, type BuddyState } from './buddy/orbPoses'
import { runDots, useOnScreen } from './orbDots'

/** What the platform asks of the orb: `reduced|contrast`, as the agent orb reads it. */
const mediaNow = () => `${prefersReducedMotion() ? 'reduced' : 'motion'}|${orbContrast()}`
const pageShown = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'
function onVisibilityChange(cb: () => void): () => void {
  document.addEventListener('visibilitychange', cb)
  return () => document.removeEventListener('visibilitychange', cb)
}

export interface BuddyOrbProps {
  /** What the orb shows: a phase of the contract, or restarting or offline. */
  state: BuddyState
  /** The state's exact line (`lib/buddy/copy.ts`), shown beside the orb. */
  line: string
  /** 18 to 96 px: below 30 px the dots are drawn larger and a plate state gives them up to its mark,
   *  as the agent orb's are. */
  size?: BuddySize
  /** Hold even a moving state still: a contact sheet's frames. */
  still?: boolean
  /** Sit on the disc plate: the desktop's window, over wallpaper and other windows. */
  plate?: boolean
  /** Wrap the line under its own width rather than cut it short: for a list whose rows say their
   *  whole line in a column, as the playground's running list does. */
  wraps?: boolean
}

export function BuddyOrb({ state, line, size = 48, still = false, plate = false, wraps = false }: BuddyOrbProps) {
  const [motion, contrast] = useSyncExternalStore(onOrbMediaChange, mediaNow, mediaNow).split('|') as ['reduced' | 'motion', ReturnType<typeof orbContrast>]
  const reduced = motion === 'reduced'
  const shown = useSyncExternalStore(onVisibilityChange, pageShown, pageShown)
  const scene = buddySceneOf(state)
  const family = familyOf(state)
  const mayMove = !reduced && shown
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  // As the agent orb's: a change nobody can see is an instant swap, and one cut short settles.
  const morph = useSceneMorph(scene, !mayMove || still || !onScreen)
  const tokens = orbInkTokens(contrast)
  const alive = !['ready', 'done', 'error', 'offline'].includes(scene)
  const inkToken = alive ? tokens.alive : tokens.rest
  const ink = useInk(inkToken)
  const dotsMove = movesOf(state) && !still && mayMove
  const running = (dotsMove || morph.changing !== 'none') && onScreen
  const pad = Math.round(size * PAD)

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const box = size + pad * 2
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    let inks = orbInksOf(tokens)
    const paint = (t: number) => {
      // The morph steps whether or not an ink has resolved yet, so a change in flight lands where it
      // would have; with no ink there is nothing to draw it in.
      const w = morph.weights()
      if (!inks) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, el.width, el.height); return }
      const clock = running ? { t, since: morph.since(t), still: false } : { t: 0.9, since: 1e6, still: true }
      paintBuddy(ctx, w, clock, inks, { size, pad, dpr, plate, whole: STILL_WHOLE, bloom: morph.blooms() })
    }
    // A scheme change reaches the painter as the change lands: the tokens are read again and the
    // dots repainted at once, moving or still.
    return runDots(paint, { running, speed: 1, retint: () => { inks = orbInksOf(tokens) } })
  }, [size, pad, plate, ink, contrast, running, scene, morph.changing]) // eslint-disable-line react-hooks/exhaustive-deps -- the morph is read through its refs at each paint

  return (
    <span
      data-buddy-orb={reduced ? 'still' : 'animated'} data-buddy-state={state} data-buddy-family={family} data-buddy-scene={scene}
      data-buddy-moving={String(running)} data-buddy-change={morph.changing} data-buddy-ink={ink ?? ''} data-buddy-ink-token={inkToken}
      data-buddy-contrast={contrast} {...(plate ? { 'data-buddy-plate': '' } : {})}
      className={wraps ? 'flex min-w-0 items-start gap-2' : 'inline-flex min-w-0 items-center gap-2'}
    >
      <span className="relative inline-block shrink-0" style={{ width: size, height: size }}>
        {/* Decoration (#3902): its state is said in words beside it, by the line or, without one,
            by the words below, never by the drawing. */}
        <canvas
          ref={canvas} aria-hidden="true"
          className="pointer-events-none absolute block"
          style={{ width: size + pad * 2, height: size + pad * 2, left: -pad, top: -pad }}
        />
      </span>
      {/* Without a line nothing sits beside the art, so an orb in a slot is in its middle (#5216). */}
      {line ? <span data-buddy-line="" data-type="body-s" className={`min-w-0 text-on-surface ${wraps ? 'break-words' : 'truncate'}`}>{line}</span> : <span data-buddy-words="" className="sr-only">{wordsOf(state)}</span>}
    </span>
  )
}
