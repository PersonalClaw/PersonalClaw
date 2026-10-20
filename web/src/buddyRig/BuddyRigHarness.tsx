/** The rig spike's harness (#3887): both candidate extensions of the agent orb, driven by one set
 *  of controls, at `#/buddy-rig` in a build made with `VITE_BUDDY_RIG=1`.
 *
 *  The stage plays the transitions the spike measures: the change into each of the four families
 *  and the focused card opening and closing, one at a time or in a loop. The dials and the scheme
 *  are the app's own, set through the appearance store as Settings › Design sets them, so a
 *  candidate reads what the app would hand it. Reduced motion is the system's setting, and the
 *  harness only says which way it is set. Each candidate is its own chunk, so the measurement can
 *  tell what each one adds to the route (`docs/design/buddy/decisions/rig.md`).
 *
 *  The measurement window drives the same controls through `window.__buddyRig` (`probe.ts`). */

import { AnimatePresence, motion } from 'framer-motion'
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ComponentType, type ReactNode } from 'react'
import { useAppearance } from '../app/appearance'
import { useMode } from '../app/theme'
import { runtime } from '../design/runtime'
import { SCHEMES } from '../design/schemes'
import { Button } from '../ui/Button'
import { Segmented } from '../ui/Segmented'
import { Slider } from '../ui/Slider'
import { useReducedMotionSetting, type CandidateProps } from './candidate'
import { BUDDY_SIZES, RIG_LOOP, RIG_PHASES, cardTransition, rigLook, type BuddySize, type RigPhase } from './poses'
import { makeProbe } from './probe'
import { RIG_CANDIDATES, isBuddyRigHash, rigViewOf, type RigCandidate, type RigView } from './route'
import type { PoseHandle } from './usePose'

const CANDIDATES: Record<RigCandidate, { title: string; Component: ComponentType<CandidateProps> }> = {
  'in-place': {
    title: '1 · Extended in place',
    Component: lazy(() => import('./CandidateInPlace').then((m) => ({ default: m.CandidateInPlace }))),
  },
  layered: {
    title: '2 · Inside a code-drawn layer',
    Component: lazy(() => import('./CandidateLayered').then((m) => ({ default: m.CandidateLayered }))),
  },
}

const PHASE_NAMES: Record<RigPhase, string> = {
  idle: 'Idle', thinking: 'Thinking', tool: 'Working', approval: 'Approval', input: 'Question', error: 'Error', done: 'Done',
}

const VIEW_OPTIONS = [
  { key: 'both', label: 'Side by side' },
  { key: 'in-place', label: 'In place' },
  { key: 'layered', label: 'Layered' },
  { key: 'sheet', label: 'Contact sheet' },
]

/** The loop's pace: each step holds this long, so every transition settles before the next. */
const STEP_MS = 800

function shown(view: RigView): RigCandidate[] {
  if (view === 'both' || view === 'sheet') return [...RIG_CANDIDATES]
  return view === 'none' ? [] : [view]
}

export function BuddyRigHarness() {
  const initial = useMemo(() => rigViewOf(location.hash), [])
  const [view, setView] = useState<RigView>(initial.view)
  const [size, setSize] = useState<BuddySize>(initial.size)
  const [phase, setPhase] = useState<RigPhase>('idle')
  const [card, setCard] = useState(false)
  const [looping, setLooping] = useState<number | null>(null)
  const [dials, setDialState] = useState(() => ({ bounciness: runtime.bounciness, expressiveness: runtime.expressiveness }))
  const appearance = useAppearance()
  const { mode, toggle } = useMode()
  const reduced = useReducedMotionSetting()
  const poses = useRef<Partial<Record<RigCandidate, PoseHandle>>>({})
  const dialsKey = `${dials.bounciness}|${dials.expressiveness}`

  // The motion runtime is written here as well as through the store: the store writes it in its own
  // effect, which runs after the candidates' effects, so without this a pose would re-read the dial
  // it is leaving. The store is still told, so it keeps the same value and the app's slider agrees.
  const setDials = useCallback((next: { bounciness?: number; expressiveness?: number }) => {
    if (next.bounciness !== undefined) { runtime.bounciness = next.bounciness; appearance.setScalar('--bounciness', next.bounciness) }
    if (next.expressiveness !== undefined) { runtime.expressiveness = next.expressiveness; appearance.setScalar('--expressiveness', next.expressiveness) }
    setDialState({ bounciness: runtime.bounciness, expressiveness: runtime.expressiveness })
  }, [appearance])

  const changeView = (next: RigView, nextSize: BuddySize) => {
    setView(next)
    setSize(nextSize)
    history.replaceState(null, '', `#/buddy-rig?view=${next}&size=${nextSize}`)
  }

  useEffect(() => {
    if (looping === null) return
    let i = 0
    let timer = 0
    const tick = () => {
      const step = RIG_LOOP[i++ % RIG_LOOP.length]
      if ('phase' in step) setPhase(step.phase)
      else setCard(step.card)
      timer = window.setTimeout(tick, looping)
    }
    tick()
    return () => window.clearTimeout(timer)
  }, [looping])

  const latest = useRef({ phase, card, looping, view, size, fps: initial.fps, dials, scheme: appearance.activeScheme, mode, reduced })
  latest.current = { phase, card, looping, view, size, fps: initial.fps, dials, scheme: appearance.activeScheme, mode, reduced }
  useEffect(() => {
    window.__buddyRig = makeProbe({
      show: setPhase,
      card: setCard,
      loop: (on, stepMs = STEP_MS) => setLooping(on ? stepMs : null),
      dials: (bounciness, expressiveness) => setDials({ bounciness, expressiveness }),
      scheme: (id) => appearance.applyScheme(id),
      state: () => ({ ...latest.current }),
      poses: () => ({ ...poses.current }),
    })
    return () => { delete window.__buddyRig }
  }, [appearance, setDials])

  const register = useMemo(() => Object.fromEntries(RIG_CANDIDATES.map((c) => [c, (p: PoseHandle) => { poses.current[c] = p }])) as Record<RigCandidate, (p: PoseHandle) => void>, [])

  return (
    <main data-buddy-rig="" className="min-h-full bg-surface p-6 text-on-surface">
      <header className="flex flex-col gap-1">
        <h1 data-type="title-l">Buddy rig spike</h1>
        <p data-type="body-s" className="max-w-prose text-on-surface-low">
          The two ways to extend the agent orb’s drawing, through the change into each of the four families and the focused card opening.
          A spike harness: only a build made with VITE_BUDDY_RIG=1 carries it.
        </p>
      </header>

      {view === 'sheet' ? <ContactSheet /> : (
        <section role="group" aria-label="Stage" className="mt-5 flex flex-wrap gap-6">
          {shown(view).map((c) => {
            const { title, Component } = CANDIDATES[c]
            return (
              <figure key={c} className="flex w-72 flex-col items-center gap-3 rounded-lg border border-outline-variant p-5">
                <figcaption data-type="label-m">{title}</figcaption>
                <div className="grid h-32 place-items-center">
                  <Suspense fallback={null}><Component phase={phase} size={size} dials={dialsKey} onPose={register[c]} fps={initial.fps} /></Suspense>
                </div>
                <p data-type="body-s" aria-live="polite">{rigLook(phase).label}</p>
                <AnimatePresence>
                  {card && (
                    <motion.div
                      key="card" data-rig-card=""
                      initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1, transition: cardTransition(true) }}
                      exit={{ opacity: 0, scale: 0.96, transition: cardTransition(false) }}
                      className="flex w-full items-center gap-3 rounded-lg border border-outline-variant bg-surface-container p-3"
                    >
                      <Suspense fallback={null}><Component phase={phase} size={48} dials={dialsKey} /></Suspense>
                      <div className="flex flex-col">
                        <span data-type="label-m">{rigLook(phase).label}</span>
                        <span data-type="caption" className="text-on-surface-low">The focused status card</span>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </figure>
            )
          })}
        </section>
      )}

      <section role="group" aria-label="Controls" className="mt-6 flex flex-col gap-3">
        <Row label="View">
          <Segmented ariaLabel="View" options={VIEW_OPTIONS} value={view === 'none' ? 'both' : view} onChange={(k) => changeView(k as RigView, size)} />
          <Segmented ariaLabel="Size" options={BUDDY_SIZES.map((s) => ({ key: String(s), label: `${s} px` }))} value={String(size)} onChange={(k) => changeView(view, Number(k) as BuddySize)} />
        </Row>
        <Row label="State">
          {RIG_PHASES.map((p) => (
            <Button key={p} size="sm" variant={p === phase ? 'tonal' : 'secondary'} ariaPressed={p === phase} onClick={() => setPhase(p)}>{PHASE_NAMES[p]}</Button>
          ))}
          <Button size="sm" variant="secondary" ariaExpanded={card} onClick={() => setCard(!card)}>{card ? 'Close the card' : 'Open the card'}</Button>
          <Button size="sm" variant={looping === null ? 'secondary' : 'tonal'} ariaPressed={looping !== null} onClick={() => setLooping(looping === null ? STEP_MS : null)}>
            {looping === null ? 'Play the loop' : 'Stop the loop'}
          </Button>
        </Row>
        <Row label="Dials">
          <Dial label="Bounciness" value={dials.bounciness} onChange={(v) => setDials({ bounciness: v })} />
          <Dial label="Expressiveness" value={dials.expressiveness} onChange={(v) => setDials({ expressiveness: v })} />
        </Row>
        <Row label="Scheme">
          <Segmented ariaLabel="Scheme" collapse="wrap" options={SCHEMES.map((s) => ({ key: s.id, label: s.label }))} value={appearance.activeScheme} onChange={(id) => appearance.applyScheme(id)} />
          <Button size="sm" variant="secondary" onClick={toggle}>{mode === 'dark' ? 'Switch to light' : 'Switch to dark'}</Button>
        </Row>
        <p data-type="body-s" className="text-on-surface-low">
          Reduced motion is {reduced ? 'on' : 'off'}. It follows the system setting, so turn it on or off there.
        </p>
      </section>
    </main>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span data-type="label-m" className="w-28 text-on-surface-low">{label}</span>
      {children}
    </div>
  )
}

function Dial({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <span className="flex w-56 items-center gap-2">
      <span data-type="body-s" className="w-28">{label} {value.toFixed(2)}</span>
      <Slider ariaLabel={label} min={0} max={1} step={0.05} value={value} onChange={onChange} />
    </span>
  )
}

/** Every pose of both candidates at every buddy size, still, for the legibility judgement. */
function ContactSheet() {
  return (
    <section role="group" aria-label="Contact sheet" className="mt-5 flex flex-col gap-6">
      {RIG_CANDIDATES.map((c) => {
        const { title, Component } = CANDIDATES[c]
        return (
          <figure key={c} className="flex flex-col gap-2">
            <figcaption data-type="label-m">{title}</figcaption>
            <div className="grid grid-cols-[6rem_repeat(4,6rem)] items-center gap-y-3">
              <span />
              {BUDDY_SIZES.map((s) => <span key={s} data-type="caption" className="text-on-surface-low">{s} px</span>)}
              {RIG_PHASES.map((p) => (
                <SheetRow key={p} name={PHASE_NAMES[p]}>
                  {BUDDY_SIZES.map((s) => (
                    <span key={s} className="grid h-24 place-items-start"><Suspense fallback={null}><Component phase={p} size={s} still /></Suspense></span>
                  ))}
                </SheetRow>
              ))}
            </div>
          </figure>
        )
      })}
    </section>
  )
}

function SheetRow({ name, children }: { name: string; children: ReactNode }) {
  return (
    <>
      <span data-type="body-s">{name}</span>
      {children}
    </>
  )
}

function onHashChange(cb: () => void): () => void {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

/** The entry's choice in a build that carries the harness: the harness at `#/buddy-rig`, the app
 *  everywhere else. */
export default function BuddyRigEntry({ app }: { app: ReactNode }) {
  const hash = useSyncExternalStore(onHashChange, () => location.hash, () => '')
  return isBuddyRigHash(hash) ? <BuddyRigHarness /> : <>{app}</>
}
