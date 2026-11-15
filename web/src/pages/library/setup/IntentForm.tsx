import { useEffect, useId, useMemo, useState } from 'react'
import { ArrowLeft, Play, Trash2 } from 'lucide-react'
import { api, type KnowledgeIntent, type KnowledgeIntentRecord, type ResearchReport } from '../../../lib/api'
import { invalidateKeys, useQuery } from '../../../lib/data'
import { HELD_CHANGE_REASON, rebaseRecord, type Revisioned } from '../../../lib/staleWrite'
import { useStaleWriteGuard } from '../../../lib/useStaleWriteGuard'
import { notify } from '../../../app/appSdk'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { Checkbox, Field, Select, TextInput } from '../../../ui/forms'
import { ChoiceList } from '../../../ui/ChoiceList'
import { FormFooter } from '../../../ui/FormFooter'
import { Toggle, TOUCH_TARGET } from '../../../ui/Toggle'
import { HeldChange, StaleWriteNotice } from '../../../ui/StaleWriteNotice'
import { blank } from './reportDraft'
import { INTENT_KINDS, kindsOf, typesOf } from './intentKinds'
import { intentRecord, readIntent, writeIntent } from '../../knowledge/intentRecord'
import { n } from '../libraryWords'
import { deleteIntent, PAUSED_RUN, runOnSaved, useIntentRun } from './SetupPanel'
import { BUSY_REASON } from '../../../ui/unavailable'

// ── The intent form: New intent, Edit intent ─────────────────────────────────────────
//
// The goal is the one field to write; "Only these kinds" writes `enabled_for`. "Write a report from
// what it finds" is the intent's schedule: on, it creates or updates a scheduled report whose source
// is the intent; off deletes that report and keeps its last write-up. An edit saves over the
// record it opened on, under the revision guard (`useStaleWriteGuard`), through the intent's one writer.

export { INTENT_KINDS, kindsOf, typesOf }

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

/** A report's schedule as the form picks it: every day or one day of the week, at an hour. */
export interface Weekly { day: string; hour: number }

/** The form's schedule for a cron expression it wrote (`0 <h> * * <d|*>`); anything else reads as
 *  Monday at 8:00, and the report's own form keeps the expression until it is changed there. */
export function weeklyOf(cron: string | null | undefined): Weekly {
  const m = /^0 (\d{1,2}) \* \* (\*|[0-6])$/.exec((cron ?? '').trim())
  if (!m) return { day: '1', hour: 8 }
  return { day: m[2], hour: Math.min(23, Number(m[1])) }
}

export const cronOf = (w: Weekly) => `0 ${w.hour} * * ${w.day}`

/** "every Monday at 8:00", "every day at 18:00". */
export const weeklyWords = (w: Weekly) => `${w.day === '*' ? 'every day' : `every ${DAYS[Number(w.day)]}`} at ${w.hour}:00`

const defaultName = (goal: string) => `Report: ${goal.trim() || 'what it finds'}`

export function IntentForm({ intent, report, onClose, onSaved }: {
  /** The intent to edit; none for New intent. */
  intent?: KnowledgeIntent | null
  /** Its report (the one whose source is this intent), when it has one. */
  report?: ResearchReport | null
  onClose: () => void
  /** Saved: the intent's id. */
  onSaved: (id: string) => void
}) {
  const isEdit = !!intent?.id
  const headingId = useId()
  const inId = useId()
  const [goal, setGoal] = useState(intent?.goal ?? '')
  const [kinds, setKinds] = useState<string[]>(() => kindsOf(intent?.enabled_for ?? []))
  const [only, setOnly] = useState(() => (intent?.enabled_for ?? []).length > 0)
  // The types the intent was given. An edit that leaves its kinds alone saves them as they are: an
  // intent set to plain notes is not widened to every kind of note by a change to its words.
  const [given, setGiven] = useState<string[]>(() => intent?.enabled_for ?? [])
  const [kindsTouched, setKindsTouched] = useState(false)
  const [enabled, setEnabled] = useState(intent?.enabled !== false)
  const [proposeSkill, setProposeSkill] = useState(!!intent?.propose_skill)
  const [checkSaved, setCheckSaved] = useState(false)
  const [writes, setWrites] = useState(!!report)
  const [weekly, setWeekly] = useState<Weekly>(() => weeklyOf(report?.schedule.cron_expr))
  const [name, setName] = useState(report?.name ?? '')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const types = only ? (kindsTouched || !given.length ? typesOf(kinds) : given) : []
  // The run's own candidates: active, unarchived items of the chosen kinds, never the total.
  const candidates = useQuery(`knowledge:intent-candidates:${types.join(',')}`, () => api.intentCandidates(types))
  const total = candidates.data?.total ?? 0
  // A count it couldn't read still offers the check, without a number.
  const uncounted = !!candidates.error && candidates.data === undefined
  const these = uncounted ? 'the items already in your library' : `the ${n(total)} items already in your library`
  const { run } = useIntentRun(intent?.id ?? '')
  const checking = run?.status === 'running'

  const [base, setBase] = useState<Revisioned<KnowledgeIntentRecord> | null>(
    () => (intent?.id ? { value: intentRecord(intent), revision: intent.revision ?? '' } : null))
  const guard = useStaleWriteGuard<KnowledgeIntentRecord>({
    read: () => readIntent(intent?.id ?? ''),
    write: (next, rev) => writeIntent(next, rev),
    onSaved: () => void afterIntent(intent?.id ?? ''),
    onDiscard: () => {
      if (!intent?.id) return
      readIntent(intent.id).then((stored) => {
        setBase(stored)
        setGoal(stored.value.goal); setKinds(kindsOf(stored.value.enabled_for)); setOnly(stored.value.enabled_for.length > 0)
        setGiven(stored.value.enabled_for); setKindsTouched(false)
        setProposeSkill(stored.value.propose_skill); setEnabled(stored.value.enabled)
      }).catch(() => setErr("Couldn't read the intent as it's stored now."))
    },
  })
  useEffect(() => { if (!enabled) setCheckSaved(false) }, [enabled])

  /** The report half: create, update or delete the intent's report to match the switch. */
  async function syncReport(id: string, g: string) {
    const fields = { name: name.trim() || defaultName(g), schedule: { kind: 'cron', cron_expr: cronOf(weekly) } }
    if (writes && report) await api.updateResearchReport(report.id, fields)
    else if (writes) {
      await api.createResearchReport({
        ...blank(), ...fields,
        prompt: `Write a short update from what the intent “${g}” found since the last report.`,
        source: { tags: [], window_secs: 0, intent: id },
      })
    } else if (report) await api.deleteResearchReport(report.id)
  }

  async function afterIntent(id: string) {
    const g = goal.trim()
    try { await syncReport(id, g) } catch (e) {
      setErr(e instanceof Error ? `Saved the intent, but not its report: ${e.message}` : 'Saved the intent, but not its report.')
      invalidateKeys('knowledge', true)
      return
    }
    if (checkSaved && enabled) await runOnSaved({ ...(intent ?? { id }), id, goal: g, enabled })
    invalidateKeys('knowledge', true)
    notify(isEdit ? `Saved “${g}”.` : `Looking out for “${g}” in everything you add.`, 'success')
    onSaved(id)
  }

  async function save() {
    setErr('')
    const g = goal.trim()
    if (!g) { setErr('Say what to look out for.'); return }
    if (only && !types.length) { setErr('Pick at least one kind, or choose Everything you add.'); return }
    const mine: KnowledgeIntentRecord = { id: intent?.id ?? '', goal: g, enabled, enabled_for: types, propose_skill: proposeSkill }
    setSaving(true)
    try {
      // A refused edit resolves `false`: the notice holds it, and the form keeps what was typed.
      if (isEdit && base) await guard.save(base, mine, rebaseRecord(base.value, mine))
      else {
        const r = await writeIntent(mine)
        await afterIntent(r.id)
      }
    } catch (e) {
      // A goal another intent already covers answers with the server's own sentence.
      setErr(e instanceof Error ? e.message: "Couldn't save the intent.")
    } finally { setSaving(false) }
  }

  const kindChoices = useMemo(() => INTENT_KINDS, [])
  const pausedReason = !enabled ? PAUSED_RUN: undefined
  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex items-start gap-s">
        <IconButton icon={ArrowLeft} label="Back to Feeds, intents and reports" onClick={onClose} className={TOUCH_TARGET} />
        <div className="min-w-0 flex-1">
          <h2 id={headingId} data-type="title-l" className="text-on-surface">{isEdit ? 'Edit intent' : 'New intent'}</h2>
          <p data-type="body-s" className="mt-xs text-on-surface-var">Tell the agent what to look out for. It checks each item you add and keeps what matches.</p>
        </div>
      </header>
      <HeldChange guard={guard}>
        <Field label="Look out for" hint="For example: anything that could improve my homelab.">
          <TextInput value={goal} onChange={setGoal} autoFocus={!isEdit} required placeholder="Ideas for the garden's spring beds" />
        </Field>
        <div className="flex flex-col gap-xs">
          <p id={inId} data-type="label-l" className="text-on-surface">In</p>
          <ChoiceList labelledBy={inId} value={only ? 'only' : 'all'} onChange={(k) => setOnly(k === 'only')}
            options={[{ key: 'all', label: 'Everything you add' }, { key: 'only', label: 'Only these kinds' }]} />
          {only && (
            <div role="group" aria-label="Kinds it checks" className="flex flex-wrap gap-m pl-l">
              {kindChoices.map((k) => (
                <label key={k.key} data-type="body-s" className="flex items-center gap-xs text-on-surface">
                  <Checkbox checked={kinds.includes(k.key)} ariaLabel={k.label}
                    onChange={(on) => { setKindsTouched(true); setKinds((ks) => (on ? [...ks, k.key] : ks.filter((x) => x !== k.key))) }} />
                  {k.label}
                </label>
              ))}
            </div>
          )}
        </div>
        {isEdit && (
          <div className="flex items-center justify-between gap-m">
            <span data-type="body-m" className="text-on-surface">Checking new items</span>
            <Toggle on={enabled} onChange={setEnabled} label={`Checking new items: ${goal.trim() || 'this intent'}`} />
          </div>
        )}
        {(total > 0 || uncounted) && !checking && (
          <label data-type="body-s" className="flex items-start gap-s text-on-surface">
            <Checkbox checked={checkSaved && enabled} onChange={setCheckSaved} className="mt-0.5"
              ariaLabel={`Also check ${these}`} />
            <span>
              Also check {these}. This uses your model once for each.
              {pausedReason && <span className="block text-on-surface-low">{pausedReason}</span>}
            </span>
          </label>
        )}
        <div className="flex flex-col gap-s">
          <div className="flex items-center justify-between gap-m">
            <span data-type="body-m" className="text-on-surface">Write a report from what it finds</span>
            <Toggle on={writes} onChange={setWrites} label="Write a report from what it finds" />
          </div>
          {writes && (
            <div className="flex flex-col gap-s pl-l">
              <div className="flex flex-wrap items-center gap-s" data-type="body-s">
                <span className="text-on-surface">Every</span>
                <Select ariaLabel="Which day" size="sm" value={weekly.day} onChange={(day) => setWeekly((w) => ({ ...w, day }))}
                  options={[{ value: '*', label: 'day' }, ...DAYS.map((d, i) => ({ value: String(i), label: d }))]} />
                <span className="text-on-surface">at</span>
                <Select ariaLabel="What time" size="sm" value={String(weekly.hour)} onChange={(h) => setWeekly((w) => ({ ...w, hour: Number(h) }))}
                  options={Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${h}:00` }))} />
              </div>
              <Field label="Name">
                <TextInput value={name} onChange={setName} placeholder={defaultName(goal)} />
              </Field>
              <p data-type="caption" className="text-on-surface-low">A short write-up, kept in your library and rewritten each time.</p>
            </div>
          )}
          {!writes && report && (
            <p data-type="caption" className="text-on-surface-low">Saving stops its report “{report.name}”. Its last write-up stays in your library.</p>
          )}
        </div>
        <label data-type="body-s" className="flex items-start gap-s text-on-surface">
          <Checkbox checked={proposeSkill} onChange={setProposeSkill} className="mt-0.5" ariaLabel="Offer to build a skill from what it finds" />
          <span>Offer to build a skill from what it finds. Adds Generate skill to its findings log.</span>
        </label>
      </HeldChange>
      <p data-type="caption" className="text-on-surface-low">Your model checks each new item once for this intent.</p>
      {isEdit && intent && (
        <div className="flex flex-wrap gap-s">
          <Button variant="secondary" size="sm" onClick={() => void runOnSaved(intent)} loading={checking} loadingLabel="Checking…"
            disabled={!enabled} disabledReason={PAUSED_RUN}>
            <Play size={14} aria-hidden /> Run on saved items
          </Button>
          <Button variant="danger" size="sm"
            onClick={async () => { if (await deleteIntent(intent, report)) onClose() }}>
            <Trash2 size={14} aria-hidden /> Delete intent…
          </Button>
        </div>
      )}
      <StaleWriteNotice guard={guard} what="This intent" />
      <FormFooter error={err}>
        <Button variant="ghost" onClick={onClose} disabled={saving} disabledReason={BUSY_REASON}>Cancel</Button>
        <Button onClick={() => void save()} loading={saving} loadingLabel="Saving…"
          disabled={guard.conflict !== null || !goal.trim()} disabledReason={guard.conflict ? HELD_CHANGE_REASON: 'Say what to look out for first.'}>
          {isEdit ? 'Save' : 'Start looking'}
        </Button>
      </FormFooter>
    </section>
  )
}
