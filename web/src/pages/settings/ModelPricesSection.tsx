import { useState } from 'react'
import { Plus } from 'lucide-react'
import { api, type ModelRateFields, type ModelRateSource, type ModelRatesView } from '../../lib/api'
import { useQuery, writeQuery } from '../../lib/data'
import { Section } from './settingsUI'
import { Field, FieldError, TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { AddItemButton } from '../../ui/AddItemButton'
import { InlineLoadError, ListSkeleton } from '../../ui/ListScaffold'

/** "Model prices" — the rate each model's calls are counted at, and where one is set.
 *
 *  A price is what the daily dollar cap, this page's dollars and cost-aware routing count a model's
 *  calls at. It is the first of: a price set here, the known $0 of a model this machine serves
 *  itself, the provider app's own, and PersonalClaw's shipped table. A model none of them prices is
 *  unpriced — its calls count toward no dollar figure, each figure says how many it leaves out, and
 *  a daily dollar cap refuses them — and this is where it gets one, $0 declaring it free. Listed:
 *  each model a use is bound to and each one spent on lately. Every field `model_rates.json` holds
 *  is set here (`PUT /api/models/rates`), so the file needs no hand edit. */
const SOURCE_LABEL: Record<Exclude<ModelRateSource, ''>, string> = {
  overlay: 'your price',
  local: 'free: it runs on this machine',
  app_default: 'its provider app’s price',
  builtin: 'PersonalClaw’s price table',
}

/** Dollars per 1M tokens with no trailing zeros: $3, $0.075, $15. Exported for test. */
export function fmtRate(usd: number): string {
  return `$${Number(usd.toFixed(4))}`
}

/** A rate as the page reads it: a listed model's fields are `null` when nothing prices it. */
type RateLike = { [F in keyof ModelRateFields]: number | null }

/** The form's fields, as typed. A cache rate left empty is not set. */
export interface RateDraft {
  key: string
  input: string
  output: string
  cacheRead: string
  cacheWrite: string
}

const EMPTY: RateDraft = { key: '', input: '', output: '', cacheRead: '', cacheWrite: '' }

function draftOf(key: string, rate?: RateLike | null): RateDraft {
  const text = (n: number | null | undefined) => (n == null ? '' : String(n))
  return {
    key,
    input: text(rate?.in_per_mtok),
    output: text(rate?.out_per_mtok),
    cacheRead: text(rate?.cache_read_per_mtok),
    cacheWrite: text(rate?.cache_write_per_mtok),
  }
}

/** What is wrong with a draft, as a person reads it, or `''`. The rules the server holds
 *  (`routing.rates.rate_entry`), said at the form rather than only as a refusal. Exported for
 *  test. */
export function draftProblem(d: RateDraft): string {
  const key = d.key.trim()
  if (!key) return 'Name the model, as provider:model or a model name.'
  if (/\s/.test(key)) return 'A model key has no spaces.'
  if (key.length > 200) return 'A model key is at most 200 characters.'
  const rates: Array<[string, string, boolean]> = [
    ['input', d.input, true],
    ['output', d.output, true],
    ['cache read', d.cacheRead, false],
    ['cache write', d.cacheWrite, false],
  ]
  for (const [label, text, required] of rates) {
    if (text.trim() === '') {
      if (required) return `Give the ${label} rate, in dollars per 1M tokens.`
      continue
    }
    const n = Number(text)
    if (!Number.isFinite(n) || n < 0) return `The ${label} rate must be zero or more dollars.`
  }
  return ''
}

function rateOf(d: RateDraft): { key: string } & ModelRateFields {
  const optional = (text: string) => (text.trim() === '' ? null : Number(text))
  return {
    key: d.key.trim(),
    in_per_mtok: Number(d.input),
    out_per_mtok: Number(d.output),
    cache_read_per_mtok: optional(d.cacheRead),
    cache_write_per_mtok: optional(d.cacheWrite),
  }
}

function RateLine({ rate }: { rate: RateLike }) {
  if (rate.in_per_mtok == null || rate.out_per_mtok == null) return null
  const cache = [
    rate.cache_read_per_mtok != null ? `${fmtRate(rate.cache_read_per_mtok)} cache read` : '',
    rate.cache_write_per_mtok != null ? `${fmtRate(rate.cache_write_per_mtok)} cache write` : '',
  ].filter(Boolean)
  return (
    <span className="tabular-nums">
      {fmtRate(rate.in_per_mtok)} in · {fmtRate(rate.out_per_mtok)} out
      {cache.length > 0 && ` · ${cache.join(' · ')}`} per 1M tokens
    </span>
  )
}

function RateForm({ initial, keyLocked, onSaved, onCancel }: {
  initial: RateDraft
  /** Editing the price of one listed model: its key is that model's. */
  keyLocked: boolean
  onSaved: (view: ModelRatesView) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState('')
  const problem = draftProblem(draft)
  const set = (field: keyof RateDraft) => (v: string) => setDraft((d) => ({ ...d, [field]: v }))

  const save = async () => {
    if (problem) return
    setSaving(true)
    setFailed('')
    try {
      onSaved(await api.setModelRate(rateOf(draft)))
    } catch (e) {
      setFailed(`The price was not saved: ${String((e as Error)?.message || e)}`)
    } finally {
      setSaving(false)
    }
  }

  // Opened on a listed model, the form sits inside that model's row, which already names it; a new
  // price is a block of its own, keyed by what the owner types.
  return (
    <div className={keyLocked ? 'flex flex-col gap-m' : 'flex flex-col gap-m rounded-lg bg-surface-container px-m py-m'}>
      {!keyLocked && (
        <Field label="Model"
          hint="A model as provider:model, a pattern such as provider:claude-*, or a model name alone, which prices it whoever serves it, this machine included.">
          <TextInput value={draft.key} onChange={set('key')} mono required size="md" surface="high" maxLength={200}
            placeholder="provider:model" />
        </Field>
      )}
      <div className="grid grid-cols-2 gap-m">
        <Field label="Input, $ per 1M tokens">
          <TextInput type="number" min={0} value={draft.input} onChange={set('input')} required size="md" surface="high" />
        </Field>
        <Field label="Output, $ per 1M tokens">
          <TextInput type="number" min={0} value={draft.output} onChange={set('output')} required size="md" surface="high" />
        </Field>
        <Field label="Cache read, $ per 1M tokens" hint="Optional. Unset, a cached token costs what an input token does.">
          <TextInput type="number" min={0} value={draft.cacheRead} onChange={set('cacheRead')} size="md" surface="high" />
        </Field>
        <Field label="Cache write, $ per 1M tokens" hint="Optional. Unset, a cached token costs what an input token does.">
          <TextInput type="number" min={0} value={draft.cacheWrite} onChange={set('cacheWrite')} size="md" surface="high" />
        </Field>
      </div>
      {failed && <FieldError>{failed}</FieldError>}
      <div className="flex items-center gap-s">
        <Button size="sm" loading={saving} disabled={saving || !!problem}
          disabledReason={problem || undefined} onClick={save}>
          Save
        </Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

const RATES_KEY = 'settings:model-rates'

export function ModelPricesSection() {
  const { data: shown, error, refresh } = useQuery(RATES_KEY, () => api.modelRates(), { persist: false })
  // What the form is open for: a listed model's key (locked), a new key, or nothing.
  const [editing, setEditing] = useState<{ draft: RateDraft; keyLocked: boolean } | null>(null)
  const [removeFailed, setRemoveFailed] = useState('')

  const remove = async (key: string) => {
    setRemoveFailed('')
    try {
      await api.clearModelRate(key)
      writeQuery(RATES_KEY, await api.modelRates())
    } catch (e) {
      setRemoveFailed(`The price for ${key} was not removed: ${String((e as Error)?.message || e)}`)
    }
  }
  // The write answers the page's new view, which every reader of it repaints from.
  const saved = (next: ModelRatesView) => { writeQuery(RATES_KEY, next); setEditing(null) }

  if (!shown) return (
    <Section title="Model prices">
      {error
        ? <InlineLoadError what="model prices" error={error} onRetry={refresh} />
        : <ListSkeleton rows={2} what="model prices" />}
    </Section>
  )

  const byKey = new Map(shown.rates.map((r) => [r.key, r]))
  const refs = new Set(shown.models.map((m) => m.ref))
  const others = shown.rates.filter((r) => !refs.has(r.key))

  return (
    <Section title="Model prices"
      hint="What a model's calls are counted at by the daily dollar cap, this page and cost-aware routing: each model your uses are bound to, and each one spent on in the last 30 days. A price set here comes first. A model nothing prices counts toward no dollar figure, and a daily dollar cap refuses its calls, until it has one; a price of $0 declares it free.">
      <div className="flex flex-col gap-s">
        {shown.unreadable && (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-m py-2.5 text-on-surface-var" role="status">
            <span className="text-warning">Unreadable</span> — {shown.unreadable}. No price in it is in effect; fix the file or remove it to set prices here.
          </div>
        )}
        {shown.models.length === 0 && others.length === 0 && (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-m py-2.5 text-on-surface-low">
            No models are bound yet. Bind one in Settings → Models, or add a price for one below.
          </div>
        )}
        {shown.models.map((m) => {
          const own = byKey.get(m.ref)
          const open = editing?.keyLocked && editing.draft.key === m.ref
          return (
            <div key={m.ref} className="flex flex-col gap-s rounded-lg bg-surface-container px-m py-2.5">
              <div className="flex items-start justify-between gap-l">
                <div className="min-w-0">
                  <div data-type="body-s" className="truncate font-mono text-on-surface">{m.ref}</div>
                  <div data-type="caption" className="mt-0.5 text-on-surface-low">
                    {m.priced ? (
                      <><RateLine rate={m} /> — {SOURCE_LABEL[m.source as Exclude<ModelRateSource, ''>] ?? m.source}</>
                    ) : (
                      <span className="text-warning">No price: its calls count toward no dollar figure, and a daily dollar cap refuses them. Set its price, or $0 if it costs nothing.</span>
                    )}
                  </div>
                </div>
                {!open && (
                  <div className="flex shrink-0 items-center gap-xs">
                    <Button size="sm" variant="secondary"
                      ariaLabel={own ? `Edit the price for ${m.ref}` : `Set a price for ${m.ref}`}
                      onClick={() => setEditing({ draft: draftOf(m.ref, own ?? (m.priced ? m : null)), keyLocked: true })}>
                      {own ? 'Edit price' : 'Set a price'}
                    </Button>
                    {own && (
                      <Button size="sm" variant="ghost" ariaLabel={`Remove the price for ${m.ref}`}
                        onClick={() => remove(m.ref)}>
                        Remove
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {open && editing && (
                <RateForm initial={editing.draft} keyLocked onSaved={saved} onCancel={() => setEditing(null)} />
              )}
            </div>
          )
        })}
        {others.map((r) => (
          <div key={r.key} className="flex items-start justify-between gap-l rounded-lg bg-surface-container px-m py-2.5">
            <div className="min-w-0">
              <div data-type="body-s" className="truncate font-mono text-on-surface">{r.key}</div>
              <div data-type="caption" className="mt-0.5 text-on-surface-low"><RateLine rate={r} /> — your price</div>
            </div>
            <div className="flex shrink-0 items-center gap-xs">
              <Button size="sm" variant="secondary" ariaLabel={`Edit the price for ${r.key}`}
                onClick={() => setEditing({ draft: draftOf(r.key, r), keyLocked: false })}>
                Edit price
              </Button>
              <Button size="sm" variant="ghost" ariaLabel={`Remove the price for ${r.key}`}
                onClick={() => remove(r.key)}>
                Remove
              </Button>
            </div>
          </div>
        ))}
        {removeFailed && <FieldError>{removeFailed}</FieldError>}
        {editing && !editing.keyLocked ? (
          <RateForm initial={editing.draft} keyLocked={false} onSaved={saved} onCancel={() => setEditing(null)} />
        ) : (
          <AddItemButton className="self-start" onClick={() => setEditing({ draft: EMPTY, keyLocked: false })}>
            <Plus size={14} aria-hidden /> Add a price
          </AddItemButton>
        )}
      </div>
    </Section>
  )
}
