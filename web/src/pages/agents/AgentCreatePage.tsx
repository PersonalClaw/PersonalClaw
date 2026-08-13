import { useState, useEffect, useRef } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { PageTitle } from '../../ui/PageTitle'
import { api } from '../../lib/api'
import { AgentForm, emptyDraft, draftToPayload, type AgentDraft } from './AgentForm'

/** Dedicated create PAGE for a native agent (matches the app-wide pattern). */
export function AgentCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [draft, setDraft] = useState<AgentDraft>(emptyDraft)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  // A failed create used to render its message at the BOTTOM OF THE SCROLLING BODY while the Create
  // button lives in a sticky footer. Measured on `#/tasks/new` at 1440x900: the button sat at y=848 and
  // the message at y=1744 — 844px BELOW the fold — with `role` null and no live region, so clicking
  // Create produced no observable effect at all. The role announces it; the ref scrolls it into view,
  // using the `scrollIntoView({ block: 'nearest' })` idiom this app already uses in 13 places.
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])

  async function create() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.createAgent(draftToPayload(draft)); onCreated() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>New agent</PageTitle></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          <AgentForm draft={draft} onChange={setDraft} />
          {err && <p ref={errRef} role="alert" className="mt-l text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} disabled={saving || !draft.name.trim()} disabledReason={!draft.name.trim() ? 'Enter an agent name first' : undefined}><Check size={16} /> {saving ? 'Creating…' : 'Create agent'}</Button>
        </div>
      </div>
    </div>
  )
}
