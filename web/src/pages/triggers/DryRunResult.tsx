import { Fragment } from 'react'
import { FlaskConical, X } from 'lucide-react'
import type { ActionProvider, ManualGatePlan, TriggerRunResult } from '../../lib/api'
import { Eyebrow } from '../../ui/Eyebrow'
import { IconButton } from '../../ui/IconButton'
import { schemaMeta, schemaProps } from '../tools/schema'
import { actionIcon, actionLabel } from './triggerMeta'

/** One configured field of an action, labelled the way that provider's own form labels it. */
export interface ActionField { key: string; label: string; value: string }

/** Whether a config value was actually SET, for a read-back. Blank, zero, false and empty
 *  collections are the form's unset states (`seedActionConfig` writes '' and false; the schedule
 *  wire writes `timeout: 0` for "the default"), and a panel that listed them would bury the one
 *  field that matters under a column of empties. */
function isSet(v: unknown): boolean {
  if (v === undefined || v === null || v === '' || v === false || v === 0) return false
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'object') return Object.keys(v as object).length > 0
  return true
}

function humanizeKey(key: string): string {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** The configured fields of an action worth reading back, in the order its create form shows them.
 *
 *  Labels come from the provider's `settingsSchema` (`x-meta.label` — "Title", "Body") so a field
 *  reads here exactly as it did where it was typed; a key the schema does not declare still shows,
 *  under its own humanized name, because it is on the row and a read-back that hid it would be
 *  describing a different action. A field the schema marks `sensitive` is shown as set, never
 *  shown. A value equal to the schema default is left out: it is not something anyone chose. */
export function actionFields(config: Record<string, unknown> | undefined, provider?: ActionProvider): ActionField[] {
  const cfg = config ?? {}
  const { props } = schemaProps(provider?.settingsSchema)
  const declared = new Map(props)
  const keys = [...props.map(([k]) => k).filter((k) => k in cfg), ...Object.keys(cfg).filter((k) => !declared.has(k))]
  const out: ActionField[] = []
  for (const key of keys) {
    const raw = cfg[key]
    if (!isSet(raw)) continue
    const schema = declared.get(key)
    if (schema && schema.default !== undefined && Object.is(raw, schema.default)) continue
    const meta = schema ? schemaMeta(schema) : {}
    const value = meta.sensitive ? '(set)'
      : typeof raw === 'string' ? raw
        : typeof raw === 'number' || typeof raw === 'boolean' ? String(raw)
          : JSON.stringify(raw)
    out.push({ key, label: meta.label ?? humanizeKey(key), value })
  }
  return out
}

/** A read-only label/value list of an action's configured fields. */
export function ActionFieldList({ fields }: { fields: ActionField[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs">
      {fields.map((f) => (
        <Fragment key={f.key}>
          <dt data-type="caption" className="text-on-surface-low">{f.label}</dt>
          <dd data-type="body-s" className="min-w-0 whitespace-pre-wrap break-words text-on-surface-var">{f.value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

/** The two gates a hand-run skips (`triggers.tools.MANUAL_BYPASSES`), in words. Any other name is
 *  shown as the backend spells it rather than guessed at. */
const BYPASS_WORDS: Record<string, string> = { quiet: 'quiet hours', duty: 'duty limits' }

function planOf(result: unknown): ManualGatePlan {
  const plan = (result as { plan?: unknown } | null | undefined)?.plan
  return plan && typeof plan === 'object' ? plan as ManualGatePlan : {}
}

function isSwitchedOff(result: unknown): boolean {
  return (result as { trigger?: { enabled?: unknown } } | null | undefined)?.trigger?.enabled === false
}

/** What a dry run found, rendered from its RESPONSE — the only record a dry run leaves.
 *
 *  🔴 WHY THIS EXISTS (failure mode 3). `POST …/run {dry_run: true}` answers in under 100ms and
 *  executes nothing, but the Run button treated it as a started run: it entered "Running…" and
 *  waited for a `last_run_ts` a dry run never moves, with a note promising "See history for the
 *  result" that no row would ever fulfil. On a disabled trigger it was still "Running…" 110s
 *  later; on a live one it cleared only when the next REAL scheduled fire happened to land. So
 *  the response is rendered here, where the user is looking, and nothing waits on anything.
 *
 *  Says what a real run WOULD do (the resolved action and the fields that define it), that it
 *  did not, and the one difference a hand-run makes — rather than reciting the gate plan's
 *  vocabulary, which is the backend's and not the user's. */
export function DryRunResult({ result, providers = [], onDismiss }: {
  result: TriggerRunResult
  providers?: ActionProvider[]
  onDismiss?: () => void
}) {
  const would = result.would_run ?? {}
  const name = typeof would.provider === 'string' ? would.provider : ''
  const provider = providers.find((p) => p.name === name)
  const Icon = actionIcon(name)
  const fields = actionFields(would.config, provider)
  const bypassed = planOf(result.result).bypassed ?? []
  const skips = bypassed.map((g) => BYPASS_WORDS[g] ?? g)
  return (
    <div role="status" className="flex flex-col gap-s rounded-lg bg-surface-container px-m py-3">
      <div className="flex items-center gap-s">
        <FlaskConical size={14} className="shrink-0 text-info" />
        <span data-type="label-s" className="flex-1 text-on-surface">Dry run — nothing was executed</span>
        {onDismiss && <IconButton icon={X} label="Dismiss the dry run result" title="Dismiss" size={28} iconSize={14} onClick={onDismiss} />}
      </div>
      {name ? (
        <div className="flex flex-col gap-xs">
          <Eyebrow>What a real run would do</Eyebrow>
          <div data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface">
            <Icon size={13} className="shrink-0" /> {provider?.display_name ?? actionLabel(name)}
          </div>
          {fields.length > 0 && <ActionFieldList fields={fields} />}
        </div>
      ) : (
        <p data-type="body-s" className="text-on-surface-var">This automation names no action for a run to start.</p>
      )}
      {isSwitchedOff(result.result) && (
        <p data-type="body-s" className="text-on-surface-var">It is switched off — Run now would still run it once, and leave it off.</p>
      )}
      <p data-type="caption" className="text-on-surface-low">
        {skips.length > 0
          ? `Run now skips ${skips.join(' and ')}; every other check a scheduled run makes still applies.`
          : 'Run now makes every check a scheduled run makes.'}
      </p>
    </div>
  )
}
