import { useMemo, useState } from 'react'
import { ChevronDown, RotateCcw } from 'lucide-react'
import { api, type NotificationRuleRow, type NotificationRulesDoc, type NotificationMode, type NotificationTarget } from '../../lib/api'
import { Field, Row, SegPills, Section } from './settingsUI'
import { ChipInput, Checkbox, TextInput, FieldError } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { Button } from '../../ui/Button'

const MODES: { key: NotificationMode; label: string }[] = [
  { key: 'never', label: 'Never' },
  { key: 'badge', label: 'Badge' },
  { key: 'immediate', label: 'Notify' },
  { key: 'digest', label: 'Digest' },
]

// push/native are accepted and persisted but inert until the mobile and desktop plans
// land. Showing them as choices you can make now would promise delivery that won't happen,
// so they're labelled rather than hidden — the setting survives, the expectation is honest.
const TARGET_LABELS: Record<NotificationTarget, string> = {
  dashboard: 'Dashboard',
  channel_dm: 'Channel DM',
  push: 'Push (mobile app required)',
  native: 'Desktop (desktop app required)',
}
const INERT_TARGETS: NotificationTarget[] = ['push', 'native']

/** Per-(source, kind) delivery rules.
 *
 *  The global controls above this decide WHETHER a notification is delivered at all
 *  (mute / severity / quiet hours). This decides HOW each kind is delivered once it gets
 *  through — the axis the global gate never had. Rows are grouped by source, because
 *  "quieten everything from heartbeat" is the most common thing a person wants and it
 *  should not require finding four separate rows.
 */
export function NotificationRulesMatrix({ doc, onSaved }: { doc: NotificationRulesDoc; onSaved: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const bySource = useMemo(() => {
    const groups = new Map<string, NotificationRuleRow[]>()
    for (const r of doc.rules) {
      const list = groups.get(r.source) ?? []
      list.push(r)
      groups.set(r.source, list)
    }
    return [...groups.entries()]
  }, [doc.rules])

  async function save(key: string, patch: Record<string, unknown>) {
    setBusy(key); setErr('')
    try { await api.saveNotificationRules({ rules: { [key]: patch } }); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(null) }
  }

  return (
    <Section title="Per-kind delivery"
      hint="What happens to each kind of notification once it passes the settings above. Never = dropped; Badge = kept in the list without interrupting; Notify = a toast; Digest = batched into the daily summary.">
      {err && <FieldError className="mb-m">{err}</FieldError>}
      <div className="flex flex-col gap-l">
        {bySource.map(([source, rows]) => (
          <div key={source}>
            <div className="mb-s text-on-surface-low text-[0.75rem] uppercase tracking-wide">{source}</div>
            <div className="flex flex-col gap-s">
              {rows.map((r) => {
                const isOpen = expanded === r.key
                const hasConditions = r.conditions.keywords.length > 0 || r.conditions.name_mention
                return (
                  <div key={r.key} className="rounded-md bg-surface-container px-m py-2">
                    <div className="flex flex-wrap items-center gap-s">
                      <span className="flex-1 min-w-0 truncate text-on-surface text-[0.875rem]">{r.label}</span>
                      {/* Only shown when the user has actually diverged — a "default" tag on
                          every untouched row would be noise on the common case. */}
                      {r.configured && r.mode !== r.default_mode && (
                        <Button size="xs" variant="ghost" onClick={() => save(r.key, { mode: r.default_mode })}
                          disabled={busy === r.key} title={`Reset to default (${r.default_mode})`}>
                          <RotateCcw size={11} /> reset
                        </Button>
                      )}
                      <SegPills value={r.mode} onChange={(v) => save(r.key, { mode: v })} options={MODES} />
                      <Button size="xs" variant="ghost" onClick={() => setExpanded(isOpen ? null : r.key)}
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? 'Hide' : 'Show'} delivery detail for ${r.label}`}>
                        {hasConditions ? 'conditions' : 'detail'}
                        <ChevronDown size={12} style={{ transform: isOpen ? 'rotate(180deg)' : undefined, transition: 'transform 150ms' }} />
                      </Button>
                    </div>
                    {isOpen && (
                      <div className="mt-m flex flex-col gap-m border-t border-outline-variant/40 pt-m">
                        <Field label="Where to deliver"
                          hint="Applies to Notify. Dashboard is always available; the others need their app.">
                          <div className="flex flex-col gap-1.5">
                            {doc.targets.map((t) => (
                              <label key={t} className="inline-flex items-center gap-2 text-on-surface-var text-[0.8125rem]">
                                <Checkbox checked={r.targets.includes(t)}
                                  ariaLabel={`Deliver ${r.label} to ${TARGET_LABELS[t]}`}
                                  onChange={(on) => {
                                    const next = on
                                      ? [...r.targets, t]
                                      : r.targets.filter((x) => x !== t)
                                    // Never leave a rule with zero targets: that is silence
                                    // by accident, and the backend would fall back to
                                    // dashboard anyway — so keep the UI honest about it.
                                    save(r.key, { targets: next.length ? next : ['dashboard'] })
                                  }} />
                                <span className={INERT_TARGETS.includes(t) ? 'text-on-surface-low' : undefined}>{TARGET_LABELS[t]}</span>
                              </label>
                            ))}
                          </div>
                        </Field>
                        <Field label="Escalate on keywords"
                          hint="A match upgrades a quieter mode to Notify — it does not add delivery targets you didn't choose.">
                          <ChipInput values={r.conditions.keywords}
                            onChange={(v) => save(r.key, { conditions: { ...r.conditions, keywords: v } })}
                            placeholder="add a keyword, Enter" ariaLabel="Add an escalation keyword" />
                        </Field>
                        <Row label="Escalate on name mention" hint="Upgrade when the text mentions you by name.">
                          <Toggle on={r.conditions.name_mention}
                            onChange={(v: boolean) => save(r.key, { conditions: { ...r.conditions, name_mention: v } })}
                            label="Name mention" />
                        </Row>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </Section>
  )
}

/** The digest schedule — a 5-field cron, validated server-side.
 *
 *  Kept beside the matrix because a `digest` mode with no schedule is a dead end: the
 *  notification goes into the queue and nothing drains it. */
export function DigestSchedule({ schedule, onSaved }: { schedule: string; onSaved: () => void }) {
  const [value, setValue] = useState(schedule)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const dirty = value.trim() !== schedule

  async function save() {
    setBusy(true); setErr('')
    try { await api.saveNotificationRules({ digest: { schedule: value.trim() } }); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(false) }
  }

  return (
    <Section title="Daily digest" hint="When batched notifications are collected into one summary.">
      <Field label="Schedule" hint="A 5-field cron expression in server-local time. Default 0 8 * * * (08:00 daily).">
        <div className="flex flex-wrap items-center gap-s">
          <div className="w-44">
            <TextInput value={value} onChange={setValue} size="sm" mono ariaLabel="Digest schedule" />
          </div>
          {dirty && <Button size="sm" onClick={save} disabled={busy}>Save</Button>}
          {dirty && <Button size="sm" variant="ghost" onClick={() => { setValue(schedule); setErr('') }} disabled={busy}>Cancel</Button>}
        </div>
      </Field>
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}
