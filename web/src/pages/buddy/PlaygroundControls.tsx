/** The playground's controls (#3892): one button per input the machine hears, grouped by where
 *  the input comes from, the clock that fires its timers, and the look the orb is drawn in.
 *
 *  A control the machine cannot take now is off, and says why: there is nothing for it to act on,
 *  or the interruption matrix says it cannot happen in this situation. The look controls are the
 *  app's own settings (Settings → Design): the colour scheme, light or dark, and the two motion
 *  dials. Reduced motion is the system's setting, so the playground shows it rather than sets it. */

import { useSyncExternalStore } from 'react'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { Segmented } from '../../ui/Segmented'
import { Select } from '../../ui/forms'
import { Slider } from '../../ui/Slider'
import { onOrbMediaChange } from '../../ui/AgentOrb'
import { useAppearance } from '../../app/appearance'
import { useMode } from '../../app/theme'
import { prefersReducedMotion } from '../../design/motion'
import { TOKENS } from '../../design/tokenRegistry'
import { situationOf } from '../../lib/buddy/machine'
import { WORK_PHASES, phaseLabel, type WorkPhase } from '../../lib/workPhase'
import { CONTROLS, CONTROL_GROUPS, cannotOccur, inputOf, nextDue, type ControlGroup, type Playground } from './playground'

/** Why a control is off, or undefined while it is on. */
function offBecause(pg: Playground, id: string): string | undefined {
  const control = CONTROLS.find((c) => c.id === id)!
  if (inputOf(pg, control)) return undefined
  const input = control.input(pg)
  return input && cannotOccur(pg.machine, input)
    ? `The interruption matrix says this cannot happen while ${situationOf(pg.machine)}.`
    : 'There is nothing for it to act on now.'
}

/** The clock as minutes and seconds, to a tenth. */
export function clockOf(ms: number): string {
  const tenths = Math.floor(ms / 100)
  const seconds = Math.floor(tenths / 10)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.${tenths % 10}`
}

/** How far each clock button moves the clock. */
const STEPS = [
  { label: '+1 s', ms: 1000 }, { label: '+10 s', ms: 10_000 }, { label: '+1 min', ms: 60_000 }, { label: '+10 min', ms: 600_000 },
]

export function PlaygroundControls({ pg, onUse, onPhase, onFire, onAdvance }: {
  pg: Playground
  onUse: (id: string) => void
  onPhase: (phase: WorkPhase) => void
  onFire: () => void
  onAdvance: (ms: number) => void
}) {
  const next = nextDue(pg)
  const groups = Object.keys(CONTROL_GROUPS) as ControlGroup[]
  return (
    <div className="flex min-w-0 flex-col gap-l">
      <section role="group" aria-label="Clock" className="flex flex-col gap-s">
        <Eyebrow as="h3">Clock · {clockOf(pg.now)}</Eyebrow>
        <div className="flex flex-wrap gap-s">
          <Button variant="secondary" size="sm" onClick={onFire} disabled={!next} disabledReason="No timer is running.">
            {next ? `Next timer: ${next.name} at ${clockOf(next.at)}` : 'Next timer'}
          </Button>
          {STEPS.map((s) => <Button key={s.label} variant="secondary" size="sm" onClick={() => onAdvance(s.ms)}>{s.label}</Button>)}
        </div>
      </section>
      {groups.map((group) => (
        <section key={group} role="group" aria-label={CONTROL_GROUPS[group]} className="flex flex-col gap-s">
          <Eyebrow as="h3">{CONTROL_GROUPS[group]}</Eyebrow>
          {group === 'work' ? (
            <div className="max-w-xs">
              <Select
                ariaLabel="The phase Set its phase sets" size="md" value={pg.phase}
                onChange={(v) => onPhase(v as WorkPhase)}
                options={WORK_PHASES.map((p) => ({ value: p, label: phaseLabel(p) }))}
              />
            </div>
          ) : null}
          <div className="flex flex-wrap gap-s">
            {CONTROLS.filter((c) => c.group === group).map((c) => {
              const off = offBecause(pg, c.id)
              return (
                <Button key={c.id} variant="secondary" size="sm" onClick={() => onUse(c.id)} disabled={Boolean(off)} disabledReason={off}>
                  {c.label}
                </Button>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

/** The three schemes the snapshots are taken in: the default, one that turns the primary teal, and
 *  the one with no colour. */
export const SHEET_SCHEMES = [
  { key: 'coral', label: 'Coral' }, { key: 'jade', label: 'Jade' }, { key: 'mono', label: 'Mono' },
] as const

const reducedNow = () => prefersReducedMotion()

const DIALS = ['--bounciness', '--expressiveness'] as const

/** The look the orb is drawn in: the app's scheme, mode and motion dials, and the system's reduced
 *  motion. */
export function LookControls() {
  const { activeScheme, applyScheme, scalarValue, setScalar } = useAppearance()
  const { mode, setPreference } = useMode()
  const reduced = useSyncExternalStore(onOrbMediaChange, reducedNow, reducedNow)
  return (
    <section role="group" aria-label="Look" className="flex min-w-0 flex-col gap-s">
      <Eyebrow as="h3">Look</Eyebrow>
      <p data-type="body-s" className="text-on-surface-var">These are the app's own settings, as in Settings → Design.</p>
      <div className="flex flex-wrap items-center gap-m">
        <Segmented
          ariaLabel="Colour scheme" size="sm" collapse="wrap" value={SHEET_SCHEMES.some((s) => s.key === activeScheme) ? activeScheme : ''}
          options={SHEET_SCHEMES.map((s) => ({ key: s.key, label: s.label }))} onChange={applyScheme}
        />
        <Segmented
          ariaLabel="Light or dark" size="sm" value={mode}
          options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }]}
          onChange={(m) => setPreference(m as 'light' | 'dark')}
        />
      </div>
      {DIALS.map((name) => {
        const token = TOKENS.find((t) => t.varName === name)!
        return (
          <label key={name} className="flex max-w-sm items-center gap-m">
            <span data-type="body-s" className="w-32 shrink-0 text-on-surface">{token.label}</span>
            <Slider ariaLabel={token.label} min={0} max={1} step={0.05} value={scalarValue(token)} onChange={(v) => setScalar(name, v)} />
          </label>
        )
      })}
      <p data-type="body-s" className="text-on-surface-var">
        Reduced motion is {reduced ? 'on' : 'off'}, from your system's setting.
      </p>
    </section>
  )
}
