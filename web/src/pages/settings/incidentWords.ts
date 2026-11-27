/** Incident mode in words: the ask that starts it, what it held back, who started and ended it, and
 *  the dropped fires the owner can run again. Settings › Guardrails and its overview card both start
 *  it through {@link askWhyAndStop}, so the two send the same request with the owner's own reason. */
import {
  api,
  type IncidentCounter,
  type IncidentHeld,
  type IncidentState,
  type TriggerRunResult,
} from '../../lib/api'
import { promptInput } from '../../ui/dialog'

/** The most characters a reason may have (`guardrails/incident.py` `REASON_MAX`). */
export const REASON_MAX = 200
/** The most characters a note may have (`NOTE_MAX`). */
export const NOTE_MAX = 4000

/** Ask why, then start incident mode with that reason; null when the owner cancels. The reason is
 *  required: Stop stays unavailable until it has text, and one over {@link REASON_MAX} is refused
 *  here as the gateway would refuse it. */
export async function askWhyAndStop(): Promise<IncidentState | null> {
  const reason = await promptInput({
    title: 'Why are you stopping unattended work?',
    body: 'Scheduled runs, triggers, hooks, loops and helper starts stop at once. Your chats keep working. The reason is kept with this incident.',
    label: 'Reason',
    placeholder: 'What did you see?',
    confirmLabel: 'Stop unattended work',
    required: true,
    validate: (value) => {
      const length = value.trim().length
      return length > REASON_MAX ? `Keep it to ${REASON_MAX} characters; this is ${length}.` : null
    },
  })
  if (reason === null) return null
  return api.incidentOn(reason.trim())
}

function counted(held: IncidentHeld | undefined, counter: IncidentCounter): number {
  const count = held?.[counter]?.count
  return typeof count === 'number' && count > 0 ? count : 0
}

function some(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** What *held* says was held back, one clause a kind, the kinds with none left out. */
export function heldParts(held: IncidentHeld | undefined): string[] {
  const parts: string[] = []
  const runs = counted(held, 'runs_skipped')
  if (runs) {
    parts.push(`${some(runs, 'run', 'runs')} skipped (a recurring one runs at its next time)`)
  }
  const hooks = counted(held, 'hooks_dropped')
  const events = counted(held, 'events_dropped')
  const dropped = [hooks && some(hooks, 'hook fire', 'hook fires'), events && some(events, 'event', 'events')]
  if (hooks || events) parts.push(`${dropped.filter(Boolean).join(' and ')} dropped`)
  const loops = counted(held, 'loop_cycles_skipped')
  if (loops) parts.push(`${some(loops, 'loop', 'loops')} skipped cycles (${loops === 1 ? 'it carries' : 'they carry'} on after it)`)
  const spawns = counted(held, 'spawns_refused')
  if (spawns) parts.push(`${some(spawns, 'subagent start', 'subagent starts')} refused`)
  return parts
}

/** "During this incident: 2 runs skipped (…); 1 hook fire dropped." — '' when nothing was held back. */
export function heldSentence(held: IncidentHeld | undefined): string {
  const parts = heldParts(held)
  return parts.length ? `During this incident: ${parts.join('; ')}.` : ''
}

/** Who started or ended it, as a sentence names them. */
export function whoSaid(by: string | undefined, app?: string): string {
  if (by === 'owner') return 'you'
  if (by === 'cli') return 'the command line'
  if (by === 'app') return app ? `the app ${app}` : 'an app'
  return 'someone this PersonalClaw did not record'
}

/** A fire the incident dropped, which the owner can run again now it is off. */
export interface DroppedFire {
  ref: string
  kind: 'hook' | 'event'
  /** What the Triggers page calls it (its id there). */
  label: string
  /** Runs it again and answers what happened, in words. */
  run: () => Promise<string>
}

function ranSaid(result: TriggerRunResult): string {
  if (result.refused) return result.refused
  if (!result.ok) return result.text || 'It did not run.'
  return result.status ? `Started: ${result.status}.` : 'Started.'
}

/** The dropped hook and event fires *held* names, each once, in the order they were dropped. A
 *  hook runs its own test run (`POST /api/triggers/lifecycle:<id>/test`), an automation its own run
 *  by hand (`POST /api/triggers/store:<id>/run`): neither has the event that was dropped. */
export function droppedFires(held: IncidentHeld | undefined): DroppedFire[] {
  const fires: DroppedFire[] = []
  for (const counter of ['hooks_dropped', 'events_dropped'] as const) {
    for (const ref of held?.[counter]?.refs ?? []) {
      if (!ref.startsWith('trigger:')) continue
      const id = ref.slice('trigger:'.length)
      if (counter === 'hooks_dropped' && id.startsWith('lifecycle:')) {
        const hook = id.slice('lifecycle:'.length)
        fires.push({
          ref,
          kind: 'hook',
          label: `Hook ${hook}`,
          run: async () => {
            const done = await api.testHook(hook, 'run again after an incident')
            return done.ok ? 'Ran.' : done.result?.error || 'It did not run.'
          },
        })
      } else if (counter === 'events_dropped') {
        fires.push({ ref, kind: 'event', label: `Automation ${id}`, run: async () => ranSaid(await api.runStoreTrigger(id)) })
      }
    }
  }
  return fires
}

/** How many references each counter names (`guardrails/incident.py` `REFS_KEPT`). */
export const REFS_KEPT = 50

/** Whether a dropped kind named as many as it keeps, so more may have dropped than are listed. */
export function droppedListIsCut(held: IncidentHeld | undefined): boolean {
  return (['hooks_dropped', 'events_dropped'] as const).some((c) => (held?.[c]?.refs?.length ?? 0) >= REFS_KEPT)
}
