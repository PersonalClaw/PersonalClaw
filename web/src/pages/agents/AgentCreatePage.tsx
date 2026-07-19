import { useState } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { api } from '../../lib/api'
import { AgentForm, emptyDraft, draftToPayload, type AgentDraft } from './AgentForm'

/** Dedicated create PAGE for a native agent (matches the app-wide pattern). */
export function AgentCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [draft, setDraft] = useState<AgentDraft>(emptyDraft)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function create() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.createAgent(draftToPayload(draft)); onCreated() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface">New agent</span></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          <AgentForm draft={draft} onChange={setDraft} />
          {err && <p className="mt-l text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} disabled={saving || !draft.name.trim()}><Check size={16} /> {saving ? 'Creating…' : 'Create agent'}</Button>
        </div>
      </div>
    </div>
  )
}
