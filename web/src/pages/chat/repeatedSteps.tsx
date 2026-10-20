/** Running a turn again asks first when the attempt it replaces finished steps that may have
 *  changed something.
 *
 *  Retry, Regenerate, Rewind to here and a resend she did not change each run a turn again from its
 *  message, and the attempt it replaces goes, the calls it finished included, so the turn asked
 *  again may make them again: a file written twice, a command run twice, a message sent twice. The
 *  gateway decides which turns that is (`dashboard/repeated_steps.py`, from what each call's tool
 *  declares) and answers such a request with its question instead of running it:
 *  `retry_repeats_steps`, with the title, the sentence, the steps and the `confirm` her yes sends
 *  back in `error.detail`. This page shows the question, each step in the words its card in the
 *  chat uses, and sends the request again only on her yes. The `confirm` binds that yes to the
 *  steps she was shown: a turn that changed since is asked about again rather than run. */
import { confirm } from '../../ui/dialog'
import type { ToolSegment } from './chatTypes'
import { labelForTool } from './toolRenderers/registry'

/** A finished step the question names: its tool, what it named first (a path, a command), and
 *  whether it failed, which its card in the chat says too. A failed step is still asked about:
 *  whether it ran before it failed is not kept. */
export interface RepeatedStep { tool: string; target: string; failed?: boolean }

/** The gateway's question, as `error.detail` carries it. */
export interface RepeatsAsked {
  title: string
  said: string
  steps: RepeatedStep[]
  /** Steps past the ones listed, counted. */
  more: number
  confirm: string
}

/** The gateway's question inside a rejection, or `null` when it is anything else. Recognised by
 *  shape (`ApiError` carries `.code` and `.detail`), so this module needs nothing from `api.ts`. */
export function repeatsAsked(e: unknown): RepeatsAsked | null {
  if (!(e instanceof Error) || (e as { code?: unknown }).code !== 'retry_repeats_steps') return null
  const detail = (e as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return null
  const { title, said, steps, more, confirm: yes } = detail as Record<string, unknown>
  if (typeof title !== 'string' || !title.trim() || typeof said !== 'string' || !said.trim()) return null
  if (typeof yes !== 'string' || !yes || !Array.isArray(steps)) return null
  const listed = steps.flatMap((s): RepeatedStep[] => {
    if (!s || typeof s !== 'object') return []
    const { tool, target, failed } = s as Record<string, unknown>
    if (typeof tool !== 'string' || !tool) return []
    return [{ tool, target: typeof target === 'string' ? target : '', ...(failed === true ? { failed } : {}) }]
  })
  return { title, said, steps: listed, more: typeof more === 'number' && more > 0 ? more : 0, confirm: yes }
}

/** A step's tool in the words its card in the chat says it in ("Run command" for `bash`). */
function stepLabel(step: RepeatedStep): string {
  return labelForTool({ kind: 'tool', id: '', tool: step.tool, done: true } satisfies ToolSegment)
}

/** The same name twice is said once: `Terminal` is its own label. */
const sameName = (a: string, b: string) => a.toLowerCase().replace(/[\s_-]+/g, '') === b.toLowerCase().replace(/[\s_-]+/g, '')

/** The question's body: the gateway's sentence, then each step in words, with its tool and target. */
export function RepeatedSteps({ asked }: { asked: RepeatsAsked }) {
  return (
    <>
      <span>{asked.said}</span>
      <ul aria-label="Steps that may repeat" className="mt-s flex flex-col gap-xs whitespace-normal">
        {asked.steps.map((step, i) => {
          const label = stepLabel(step)
          return (
            <li key={`${step.tool}\u0000${step.target}\u0000${i}`} className="flex min-w-0 items-baseline gap-xs">
              <span className="shrink-0 text-on-surface">{label}</span>
              {step.target && <span className="min-w-0 truncate font-mono text-on-surface-var">{step.target}</span>}
              {!sameName(label, step.tool) && <span className="shrink-0 font-mono text-on-surface-low">{step.tool}</span>}
              {step.failed && <span className="shrink-0 text-on-surface-low">· failed</span>}
            </li>
          )
        })}
      </ul>
      {asked.more > 0 && <span className="mt-xs block">…and {asked.more} more.</span>}
    </>
  )
}

/** Ask the owner the gateway's question; resolves to her answer. */
export function confirmRepeat(asked: RepeatsAsked): Promise<boolean> {
  return confirm({ title: asked.title, body: <RepeatedSteps asked={asked} />, confirmLabel: 'Run it again' })
}

/** What the failure handler of a request that runs a turn again does with the gateway's question:
 *  asks the owner and, on her yes, sends the request `again` with the question's `confirm`.
 *  `false` when the failure is not the question, which the caller then reports as the failure it
 *  is. */
export function askToRepeat(e: unknown, again: (confirmed: string) => unknown): boolean {
  const asked = repeatsAsked(e)
  if (!asked) return false
  void confirmRepeat(asked).then((yes) => { if (yes) again(asked.confirm) })
  return true
}
