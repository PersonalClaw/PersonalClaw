import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import {
  api,
  type ImageTier,
  type ModelRateBody,
  type ModelRateFields,
  type ModelRateProvenance,
  type ModelRateSource,
  type ModelRateUnit,
  type ModelRatesView,
  type UnitRateFields,
} from '../../lib/api'
import { useQuery, writeQuery } from '../../lib/data'
import { Section } from './settingsUI'
import { Field, FieldError, Select, TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { AddItemButton } from '../../ui/AddItemButton'
import { InlineLoadError, ListSkeleton } from '../../ui/ListScaffold'

/** "Model prices" — the rate each model's calls are counted at, and where one is set.
 *
 *  A price is what the daily dollar cap, this page's dollars and cost-aware routing count a model's
 *  calls at, in the unit the model is billed in: per 1M tokens, per image (by its size and quality
 *  where the vendor prices it so), per second of video, per minute of audio or per 1M characters of
 *  speech. It is the first of: your price, the known $0 of a model this machine runs itself, the
 *  provider app's own, and PersonalClaw's shipped table, which says whose list price it is and the
 *  day it recorded it. Any listed model can be given your price, a known one included; a price of
 *  yours shows the default it stands in front of, and resets to it. A model nothing prices is
 *  unpriced — its calls count toward no dollar figure, each figure says how many it leaves out, and
 *  a daily dollar cap refuses them — and this is where it gets one, $0 declaring it free. Listed:
 *  each model a use is bound to and each one spent on lately. Your prices are kept in your
 *  configuration (`config.json` → `model_prices`), and every field one holds is set here
 *  (`PUT /api/models/rates`), so the file needs no hand edit. */
const SOURCE_LABEL: Record<Exclude<ModelRateSource, ''>, string> = {
  overlay: 'your price',
  local: 'free: it runs on this machine',
  app_default: 'its provider app’s price',
  builtin: 'PersonalClaw’s price table',
}

/** What one of each unit is, as a price reads it. */
const PER: Record<ModelRateUnit, string> = {
  token: 'per 1M tokens',
  image: 'per image',
  second: 'per second of video',
  minute: 'per minute of audio',
  character: 'per 1M characters',
}

const UNIT_OPTIONS: Array<{ value: ModelRateUnit; label: string }> = [
  { value: 'token', label: 'Per 1M tokens' },
  { value: 'image', label: 'Per image' },
  { value: 'second', label: 'Per second of video' },
  { value: 'minute', label: 'Per minute of audio' },
  { value: 'character', label: 'Per 1M characters of speech' },
]

/** Dollars with no trailing zeros: $3, $0.075, $15. Exported for test. */
export function fmtRate(usd: number): string {
  return `$${Number(usd.toFixed(4))}`
}

/** A rate as the page reads it: a listed model's fields are `null` when nothing prices it. */
type RateLike = { unit: ModelRateUnit | '' }
  & { [F in keyof ModelRateFields]: number | null }
  & Partial<UnitRateFields>

/** One price by size and quality, as typed. */
export interface TierDraft { size: string; quality: string; price: string }

/** The form's fields, as typed. A cache rate left empty is not set. */
export interface RateDraft {
  key: string
  unit: ModelRateUnit
  input: string
  output: string
  cacheRead: string
  cacheWrite: string
  /** The one price of a model billed per image, second, minute or 1M characters. */
  price: string
  /** An image model priced by size and quality, rather than at one price per image. */
  byTier: boolean
  tiers: TierDraft[]
  defaultSize: string
  defaultQuality: string
}

/** The draft's fields a person types into. */
type TextField = 'key' | 'input' | 'output' | 'cacheRead' | 'cacheWrite' | 'price' | 'defaultSize' | 'defaultQuality'

const EMPTY_TIER: TierDraft = { size: '', quality: '', price: '' }

const EMPTY: RateDraft = {
  key: '', unit: 'token', input: '', output: '', cacheRead: '', cacheWrite: '', price: '',
  byTier: false, tiers: [EMPTY_TIER], defaultSize: '', defaultQuality: '',
}

const text = (n: number | null | undefined) => (n == null ? '' : String(n))

function draftOf(key: string, rate?: RateLike | null): RateDraft {
  const unit: ModelRateUnit = rate?.unit || 'token'
  const tiers = rate?.tiers ?? []
  return {
    ...EMPTY,
    key,
    unit,
    input: text(rate?.in_per_mtok),
    output: text(rate?.out_per_mtok),
    cacheRead: text(rate?.cache_read_per_mtok),
    cacheWrite: text(rate?.cache_write_per_mtok),
    price: text(rate?.per_unit),
    byTier: tiers.length > 0,
    tiers: tiers.length > 0
      ? tiers.map((t) => ({ size: t.size, quality: t.quality, price: String(t.per_image) }))
      : [EMPTY_TIER],
    defaultSize: rate?.default_size ?? '',
    defaultQuality: rate?.default_quality ?? '',
  }
}

const SIZE = /^\d+x\d+$/i

/** What is wrong with a price as typed, or `''`: a required price missing, or one that is not
 *  zero or more dollars. */
function priceProblem(value: string, label: string, required: boolean, per: string): string {
  if (value.trim() === '') return required ? `Give the ${label}, in dollars ${per}.` : ''
  const n = Number(value)
  return !Number.isFinite(n) || n < 0 ? `The ${label} must be zero or more dollars.` : ''
}

/** What is wrong with a draft, as a person reads it, or `''`. The rules the server holds
 *  (`routing.rates.rate_entry`), said at the form rather than only as a refusal. Exported for
 *  test. */
export function draftProblem(d: RateDraft): string {
  const key = d.key.trim()
  if (!key) return 'Name the model, as provider:model or a model name.'
  if (/\s/.test(key)) return 'A model key has no spaces.'
  if (key.length > 200) return 'A model key is at most 200 characters.'
  if (d.unit === 'token') {
    const rates: Array<[string, string, boolean]> = [
      ['input rate', d.input, true],
      ['output rate', d.output, true],
      ['cache read rate', d.cacheRead, false],
      ['cache write rate', d.cacheWrite, false],
    ]
    for (const [label, value, required] of rates) {
      const problem = priceProblem(value, label, required, 'per 1M tokens')
      if (problem) return problem
    }
    return ''
  }
  if (d.unit === 'image' && d.byTier) {
    for (const tier of d.tiers) {
      if (tier.size.trim() && !SIZE.test(tier.size.trim())) {
        return 'A size is a width x height in pixels, such as 1024x1024.'
      }
      const problem = priceProblem(tier.price, 'price per image', true, 'per image')
      if (problem) return problem
    }
    if (d.defaultSize.trim() && !SIZE.test(d.defaultSize.trim())) {
      return 'A size is a width x height in pixels, such as 1024x1024.'
    }
    return ''
  }
  return priceProblem(d.price, 'price', true, PER[d.unit])
}

function rateOf(d: RateDraft): ModelRateBody {
  const key = d.key.trim()
  const optional = (value: string) => (value.trim() === '' ? null : Number(value))
  switch (d.unit) {
    case 'token':
      return {
        key,
        unit: 'token',
        in_per_mtok: Number(d.input),
        out_per_mtok: Number(d.output),
        cache_read_per_mtok: optional(d.cacheRead),
        cache_write_per_mtok: optional(d.cacheWrite),
      }
    case 'image':
      if (d.byTier) {
        return {
          key,
          unit: 'image',
          tiers: d.tiers.map((t) => ({
            size: t.size.trim().toLowerCase(),
            quality: t.quality.trim().toLowerCase(),
            per_image: Number(t.price),
          })),
          default_size: d.defaultSize.trim().toLowerCase(),
          default_quality: d.defaultQuality.trim().toLowerCase(),
        }
      }
      return { key, unit: 'image', per_image: Number(d.price) }
    case 'second':
      return { key, unit: 'second', per_second: Number(d.price) }
    case 'minute':
      return { key, unit: 'minute', per_minute: Number(d.price) }
    case 'character':
      return { key, unit: 'character', per_mchar: Number(d.price) }
  }
}

function tierLabel(t: ImageTier): string {
  const size = t.size ? `up to ${t.size}` : 'any size'
  return `${size}${t.quality ? ` ${t.quality}` : ''} ${fmtRate(t.per_image)}`
}

/** A rate as one line, in the unit it is quoted in. Exported for test. */
export function rateText(rate: RateLike): string {
  if (rate.unit === 'token' || rate.unit === '') {
    if (rate.in_per_mtok == null || rate.out_per_mtok == null) return ''
    const cache = [
      rate.cache_read_per_mtok != null ? `${fmtRate(rate.cache_read_per_mtok)} cache read` : '',
      rate.cache_write_per_mtok != null ? `${fmtRate(rate.cache_write_per_mtok)} cache write` : '',
    ].filter(Boolean)
    return `${fmtRate(rate.in_per_mtok)} in · ${fmtRate(rate.out_per_mtok)} out`
      + `${cache.length > 0 ? ` · ${cache.join(' · ')}` : ''} per 1M tokens`
  }
  if (rate.tiers && rate.tiers.length > 0) {
    const made = [rate.default_size, rate.default_quality].filter(Boolean).join(' ')
    return `${rate.tiers.map(tierLabel).join(' · ')} per image${made ? ` (made at ${made} unless asked)` : ''}`
  }
  return rate.per_unit == null ? '' : `${fmtRate(rate.per_unit)} ${PER[rate.unit]}`
}

/** Where a listed model's price comes from, as the page says it: yours says the day you set it, an
 *  app's names the app, and a shipped price names whose list price it is, the day the table
 *  recorded it and the row it was found as. Exported for test. */
export function sourceText(m: { ref: string } & Pick<ModelRateProvenance, 'source' | 'vendor' | 'recorded' | 'priced_as'>): string {
  if (m.source === 'overlay') return `${SOURCE_LABEL.overlay}${m.recorded ? `, set ${m.recorded}` : ''}`
  if (m.source === 'app_default') return m.vendor ? `the ${m.vendor} app’s price` : SOURCE_LABEL.app_default
  if (m.source !== 'builtin') return m.source ? SOURCE_LABEL[m.source] : ''
  const model = m.ref.slice(m.ref.indexOf(':') + 1)
  const whose = m.vendor ? `${m.vendor}’s list price` : 'a list price'
  const when = m.recorded ? `, recorded ${m.recorded}` : ''
  const found = m.priced_as && m.priced_as !== model ? `, as ${m.priced_as}` : ''
  return `${whose}${when}${found} (${SOURCE_LABEL.builtin})`
}

function TierRows({ draft, setTiers }: {
  draft: RateDraft
  setTiers: (tiers: TierDraft[]) => void
}) {
  const edit = (i: number, field: keyof TierDraft) => (v: string) =>
    setTiers(draft.tiers.map((t, j) => (j === i ? { ...t, [field]: v } : t)))
  return (
    <div className="flex flex-col gap-s">
      {draft.tiers.map((tier, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] items-end gap-s">
          <Field label={`Size up to, price ${i + 1}`}>
            <TextInput value={tier.size} onChange={edit(i, 'size')} size="md" surface="high" mono placeholder="any size" />
          </Field>
          <Field label={`Quality, price ${i + 1}`}>
            <TextInput value={tier.quality} onChange={edit(i, 'quality')} size="md" surface="high" placeholder="any quality" />
          </Field>
          <Field label={`$ per image, price ${i + 1}`}>
            <TextInput type="number" min={0} step="any" value={tier.price} onChange={edit(i, 'price')} required size="md" surface="high" />
          </Field>
          <Button size="sm" variant="ghost" ariaLabel={`Remove price ${i + 1}`}
            disabled={draft.tiers.length === 1} disabledReason="An image price needs one size and quality."
            onClick={() => setTiers(draft.tiers.filter((_, j) => j !== i))}>
            <X size={14} aria-hidden />
          </Button>
        </div>
      ))}
      <AddItemButton className="self-start" onClick={() => setTiers([...draft.tiers, EMPTY_TIER])}>
        <Plus size={14} aria-hidden /> Add a size or quality
      </AddItemButton>
    </div>
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
  const set = (field: TextField) => (v: string) => setDraft((d) => {
    const next = { ...d }
    next[field] = v
    return next
  })

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
      <Field label="Billed" hint="What the model's maker charges by: tokens for a text model, an image, a second of video, a minute of audio, or characters of speech.">
        <Select value={draft.unit} onChange={(v) => setDraft((d) => ({ ...d, unit: v as ModelRateUnit }))}
          options={UNIT_OPTIONS} size="md" surface="high" />
      </Field>
      {draft.unit === 'token' && (
        <div className="grid grid-cols-2 gap-m">
          <Field label="Input, $ per 1M tokens">
            <TextInput type="number" min={0} step="any" value={draft.input} onChange={set('input')} required size="md" surface="high" />
          </Field>
          <Field label="Output, $ per 1M tokens">
            <TextInput type="number" min={0} step="any" value={draft.output} onChange={set('output')} required size="md" surface="high" />
          </Field>
          <Field label="Cache read, $ per 1M tokens" hint="Optional. Unset, a cached token costs what an input token does.">
            <TextInput type="number" min={0} step="any" value={draft.cacheRead} onChange={set('cacheRead')} size="md" surface="high" />
          </Field>
          <Field label="Cache write, $ per 1M tokens" hint="Optional. Unset, a cached token costs what an input token does.">
            <TextInput type="number" min={0} step="any" value={draft.cacheWrite} onChange={set('cacheWrite')} size="md" surface="high" />
          </Field>
        </div>
      )}
      {draft.unit === 'image' && (
        <Field label="Image price" hint="One price for every image, or a price for each size and quality the maker lists. A size is the largest image the price covers.">
          <Select value={draft.byTier ? 'tiers' : 'one'} onChange={(v) => setDraft((d) => ({ ...d, byTier: v === 'tiers' }))}
            options={[{ value: 'one', label: 'One price per image' }, { value: 'tiers', label: 'By size and quality' }]}
            size="md" surface="high" />
        </Field>
      )}
      {draft.unit === 'image' && draft.byTier && (
        <>
          <TierRows draft={draft} setTiers={(tiers) => setDraft((d) => ({ ...d, tiers }))} />
          <div className="grid grid-cols-2 gap-m">
            <Field label="Default size" hint="The size an image is made at when nothing asks for one.">
              <TextInput value={draft.defaultSize} onChange={set('defaultSize')} mono size="md" surface="high" placeholder="1024x1024" />
            </Field>
            <Field label="Default quality" hint="The quality an image is made at when nothing asks for one.">
              <TextInput value={draft.defaultQuality} onChange={set('defaultQuality')} size="md" surface="high" placeholder="standard" />
            </Field>
          </div>
        </>
      )}
      {draft.unit !== 'token' && !(draft.unit === 'image' && draft.byTier) && (
        <Field label={`Price, $ ${PER[draft.unit]}`}>
          <TextInput type="number" min={0} step="any" value={draft.price} onChange={set('price')} required size="md" surface="high" />
        </Field>
      )}
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
      setRemoveFailed(`Your price for ${key} was not reset: ${String((e as Error)?.message || e)}`)
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
  const priceOf = (p: ModelRateProvenance, ref: string) => (p.source === 'local'
    ? `$0 — ${SOURCE_LABEL.local}`
    : `${rateText(p)} — ${sourceText({ ref, ...p })}`)
  const refs = new Set(shown.models.map((m) => m.ref))
  const others = shown.rates.filter((r) => !refs.has(r.key))
  const asRate = (r: (typeof shown.rates)[number]): RateLike => (r.unit === 'token'
    ? { ...r, per_unit: null, tiers: [], default_size: '', default_quality: '' }
    : { ...r, in_per_mtok: null, out_per_mtok: null, cache_read_per_mtok: null, cache_write_per_mtok: null })

  return (
    <Section title="Model prices"
      hint="What a model's calls are counted at by the daily dollar cap, this page and cost-aware routing, in the unit the model is billed in: each model your uses are bound to, and each one spent on in the last 30 days. Your price comes first, for any model, a known one included, and resets to the default it stands in front of. A list price from PersonalClaw's table is the model maker's on the day it was recorded; whoever runs the model may bill differently, so set your own if it does. A model nothing prices counts toward no dollar figure, and a daily dollar cap refuses its calls, until it has one; a price of $0 declares it free.">
      <div className="flex flex-col gap-s">
        {shown.unreadable && (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-m py-2.5 text-on-surface-var" role="status">
            <span className="text-warning">Unreadable</span> — {shown.unreadable}. No price you set is in effect; fix the file to set prices here.
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
                    {!m.priced ? (
                      <span className="text-warning">No price: its calls count toward no dollar figure, and a daily dollar cap refuses them. Set its price, or $0 if it costs nothing.</span>
                    ) : (
                      <span className="tabular-nums">{priceOf(m, m.ref)}</span>
                    )}
                  </div>
                  {own && (
                    <div data-type="caption" className="mt-0.5 text-on-surface-low">
                      {m.default
                        ? <>Default: <span className="tabular-nums">{priceOf(m.default, m.ref)}</span></>
                        : 'No default: without your price nothing prices it.'}
                    </div>
                  )}
                </div>
                {!open && (
                  <div className="flex shrink-0 items-center gap-xs">
                    <Button size="sm" variant="secondary"
                      ariaLabel={own ? `Edit your price for ${m.ref}`
                        : m.priced ? `Set your own price for ${m.ref}` : `Set a price for ${m.ref}`}
                      onClick={() => setEditing({
                        draft: draftOf(m.ref, own ? asRate(own) : (m.priced && m.source !== 'local' ? m : null)),
                        keyLocked: true,
                      })}>
                      {own ? 'Edit price' : m.priced ? 'Set your price' : 'Set a price'}
                    </Button>
                    {own && (
                      <Button size="sm" variant="ghost"
                        ariaLabel={m.default ? `Reset the price for ${m.ref} to its default` : `Remove your price for ${m.ref}`}
                        onClick={() => remove(m.ref)}>
                        {m.default ? 'Reset to default' : 'Remove'}
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
              <div data-type="caption" className="mt-0.5 text-on-surface-low"><span className="tabular-nums">{rateText(asRate(r))}</span> — {sourceText({ ref: r.key, source: 'overlay', vendor: '', recorded: r.recorded, priced_as: '' })}</div>
            </div>
            <div className="flex shrink-0 items-center gap-xs">
              <Button size="sm" variant="secondary" ariaLabel={`Edit the price for ${r.key}`}
                onClick={() => setEditing({ draft: draftOf(r.key, asRate(r)), keyLocked: false })}>
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
