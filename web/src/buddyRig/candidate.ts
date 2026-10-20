/** What the two rig candidates share (#3887): their props, the platform's answer on motion and
 *  visibility, and the inks they paint with. Everything that differs between them is the drawing. */

import { useEffect, useState, useSyncExternalStore, type RefObject } from 'react'
import { prefersReducedMotion } from '../design/motion'
import { useInk } from '../ui/AgentOrb'
import type { BuddySize, RigLook, RigPhase } from './poses'
import type { PoseHandle } from './usePose'

export interface CandidateProps {
  phase: RigPhase
  size: BuddySize
  /** Hold even an active pose still: the contact sheet's frames. */
  still?: boolean
  /** Changes when the motion dials do, so the pose re-reads its amplitudes and presets. */
  dials?: string
  /** Hands the pose to the harness, whose probe measures how the dials move it. */
  onPose?: (pose: PoseHandle) => void
  /** Paint at most this many frames a second, where the candidate owns its frame loop. */
  fps?: number | null
}

const REDUCED = '(prefers-reduced-motion: reduce)'
function onMotionChange(cb: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const list = window.matchMedia(REDUCED)
  list.addEventListener?.('change', cb)
  return () => list.removeEventListener?.('change', cb)
}

const visible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'
function onVisibilityChange(cb: () => void): () => void {
  document.addEventListener('visibilitychange', cb)
  return () => document.removeEventListener('visibilitychange', cb)
}

/** Whether the system asks for reduced motion, read through the motion module's own gate, so the
 *  candidates and the presets give one answer. */
export function useReducedMotionSetting(): boolean {
  return useSyncExternalStore(onMotionChange, prefersReducedMotion, prefersReducedMotion)
}

/** Whether the dots may move now: motion allowed and the page visible. */
export function useMayMove(): boolean {
  const reduced = useReducedMotionSetting()
  const shown = useSyncExternalStore(onVisibilityChange, visible, visible)
  return !reduced && shown
}

/** Whether `ref`'s element is on screen: an orb scrolled out of view pauses, as the agent orb's
 *  drawing does. On screen wherever there is no way to tell. */
export function useOnScreen(ref: RefObject<Element | null>): boolean {
  const [onScreen, setOnScreen] = useState(true)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting))
    io.observe(el)
    return () => io.disconnect()
  }, [ref])
  return onScreen
}

/** The dots' ink and the badge's and the wash's tone: their tokens, and the colours they resolve
 *  to now. */
export function useRigInks(look: RigLook): { inkToken: string; ink: string | undefined; toneToken: string; tone: string | undefined } {
  const inkToken = look.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest'
  const toneToken = look.tone === 'neutral' ? '--color-orb-rest' : `--color-${look.tone}`
  return { inkToken, ink: useInk(inkToken), toneToken, tone: useInk(toneToken) }
}
