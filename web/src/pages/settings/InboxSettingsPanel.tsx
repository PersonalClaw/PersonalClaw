import { useEffect, useState } from 'react'
import { X, Plus } from 'lucide-react'
import { api, type InboxSettings } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, Toggle, SavedToast } from './settingsUI'
import { FormSkeleton } from '../../ui/ListScaffold'

/** Inbox settings → /api/inbox/settings. Alert keywords + name-mention alerts +
 *  auto-cleanup retention for the unified inbox (messaging/email source items). */
export function InboxSettingsPanel() {
  const [s, setS] = useState<InboxSettings | null>(null)
  const [saved, setSaved] = useState(false)
  const [kw, setKw] = useState('')

  // Stale-while-revalidate + persist: paint instantly on revisit/reload. The
  // editable form state `s` is seeded/rehydrated from this read-only `data`;
  // the patch handler keeps mutating `s` optimistically + saving.
  const { data } = useCachedData('settings:inbox', () => api.inboxSettings().catch(() => null), { persist: true })
  useEffect(() => { if (data) setS(data) }, [data])

  const patch = (p: Partial<InboxSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveInboxSettings(p).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }

  if (!data || !s) return <FormSkeleton sections={2} />
  return (
    <div>
      <PanelHeader title="Inbox" hint="What gets flagged in the unified inbox, and how long items are kept." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      <Section title="Alerts" hint="When to flag an inbox item for your attention.">
        <Field label="Alert keywords" hint="Flag items containing any of these words.">
          <div className="flex flex-wrap items-center gap-1.5">
            {s.alert_keywords.map((k) => (
              <span key={k} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface text-[0.78rem]">
                {k}
                <button type="button" onClick={() => patch({ alert_keywords: s.alert_keywords.filter((x) => x !== k) })} aria-label={`Remove ${k}`} className="text-on-surface-low hover:text-on-surface"><X size={12} /></button>
              </span>
            ))}
            <input value={kw} onChange={(e) => setKw(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && kw.trim() && !s.alert_keywords.includes(kw.trim())) { patch({ alert_keywords: [...s.alert_keywords, kw.trim()] }); setKw('') } }}
              placeholder="Add keyword…" className="h-8 w-36 rounded-md bg-surface-high px-2 text-[0.78rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            {kw.trim() && <button type="button" onClick={() => { patch({ alert_keywords: [...s.alert_keywords, kw.trim()] }); setKw('') }} className="grid size-7 place-items-center rounded-md text-primary"><Plus size={15} /></button>}
          </div>
        </Field>
        <Row label="Alert on name mention" hint="Flag items that mention your name.">
          <Toggle on={s.alert_on_name_mention} onChange={(v) => patch({ alert_on_name_mention: v })} label="Name-mention alerts" />
        </Row>
      </Section>

      <Section title="Retention" hint="Automatically clean up old inbox items.">
        <Row label="Auto-cleanup" hint="Remove items past their retention window.">
          <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto-cleanup" />
        </Row>
        {s.auto_cleanup_enabled && (
          <Row label="Retention" hint="How long to keep inbox items (all sources).">
            <NumDays value={s.retention_days} onChange={(v) => patch({ retention_days: v })} />
          </Row>
        )}
      </Section>
    </div>
  )
}

function NumDays({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [local, setLocal] = useState(String(value))
  useEffect(() => { setLocal(String(value)) }, [value])
  const commit = () => {
    const n = Number(local)
    if (local === '' || Number.isNaN(n)) { setLocal(String(value)); return }
    const clamped = Math.max(1, Math.min(3650, n))
    setLocal(String(clamped))
    if (clamped !== value) onChange(clamped)
  }
  return (
    <div className="flex items-center gap-2">
      <input type="number" value={local} min={1} max={3650} onChange={(e) => setLocal(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
        className="h-8 w-20 rounded-md bg-surface-high px-2 text-right text-[0.8125rem] text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <span className="text-on-surface-low text-[0.75rem]">days</span>
    </div>
  )
}
