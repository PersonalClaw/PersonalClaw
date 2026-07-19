import { useRef, useState } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { api, type PromptKind } from '../../lib/api'
import { PromptForm, emptyDraft, draftToPayload, type PromptDraft } from './PromptForm'
import { SnippetForm, emptySnippetDraft, snippetDraftToPayload, type SnippetDraft } from './SnippetForm'
import { PromptPreviewPane } from './PromptPreviewPane'
import { SyntaxReference } from './SyntaxReference'

/** Dedicated full-page create flow. `mode` selects prompt (kind=user|system) vs
 *  snippet authoring; the kind is chosen up front for a prompt and is fixed. */
export function PromptCreatePage({ onBack, onCreated, mode = 'user' }: {
  onBack: () => void
  onCreated: () => void
  mode?: PromptKind | 'snippets'
}) {
  const isSnippet = mode === 'snippets'
  const [kind, setKind] = useState<PromptKind>(mode === 'system' ? 'system' : 'user')
  const [draft, setDraft] = useState<PromptDraft>(() => emptyDraft(mode === 'system' ? 'system' : 'user'))
  const [snipDraft, setSnipDraft] = useState<SnippetDraft>(() => emptySnippetDraft())
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [rail, setRail] = useState<'preview' | 'reference'>('preview')
  // Insert-at-cursor handle the editor registers; the reference palette calls it.
  const insertRef = useRef<(text: string) => void>(() => {})
  // The snippet form reuses the prompt draft's content shape for preview purposes.
  const previewDraft: PromptDraft = isSnippet
    ? { ...emptyDraft(), name: snipDraft.name, content: snipDraft.content, variables: snipDraft.variables, tags: snipDraft.tags }
    : draft

  const name = isSnippet ? snipDraft.name : draft.name

  async function create() {
    if (!name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try {
      if (isSnippet) await api.createSnippet(snippetDraftToPayload(snipDraft))
      else await api.createPrompt(draftToPayload({ ...draft, kind }))
      onCreated()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface">{isSnippet ? 'New snippet' : 'New prompt'}</span></div>} />
      {/* Split view: authoring form (left) + a sticky preview/reference rail
          (right). The rail collapses below the form on narrow viewports. Body,
          header, and footer all honor the SAME --content-width preference so the
          three bands align (a hardcoded cap here left the body as a centered
          island floating away from its full-width header/footer). */}
      <div className="flex-1 overflow-hidden">
        <div className="mx-auto flex h-full flex-col gap-l overflow-y-auto px-l py-l lg:flex-row lg:gap-xl lg:overflow-hidden"
          style={{ maxWidth: 'var(--content-width)' }}>
          <div className="flex min-w-0 flex-1 flex-col gap-l lg:overflow-y-auto lg:pb-2xl lg:pr-1">
            {!isSnippet && (
              <div className="flex items-center gap-m">
                <span className="text-on-surface-var text-[0.8125rem]">Kind</span>
                <Segmented ariaLabel="Prompt kind" value={kind} onChange={(v) => setKind(v as PromptKind)} options={[{ key: 'user', label: 'User' }, { key: 'system', label: 'System' }]} />
                <span className="text-on-surface-low text-[0.75rem]">{kind === 'system' ? 'Bound to a use-case, injected as the system prompt.' : 'Invoked in chat with filled-in variables.'}</span>
              </div>
            )}
            {isSnippet
              ? <SnippetForm draft={snipDraft} onChange={setSnipDraft} registerInsert={(fn) => { insertRef.current = fn }} />
              : <PromptForm draft={draft} onChange={setDraft} registerInsert={(fn) => { insertRef.current = fn }} />}
            {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
          </div>

          <aside className="flex shrink-0 flex-col lg:w-[400px] lg:overflow-hidden">
            <div className="mb-2 shrink-0">
              <Segmented ariaLabel="Right rail" value={rail} onChange={(v) => setRail(v as 'preview' | 'reference')}
                options={[{ key: 'preview', label: 'Preview' }, { key: 'reference', label: 'Reference' }]} />
            </div>
            <div className="min-h-0 flex-1 rounded-xl border border-outline-variant/40 bg-surface p-3 lg:overflow-y-auto">
              {rail === 'preview'
                ? <PromptPreviewPane draft={previewDraft} />
                : <SyntaxReference onInsert={(s) => insertRef.current(s)} />}
            </div>
          </aside>
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} disabled={saving || !name.trim()}><Check size={16} /> {saving ? 'Creating…' : isSnippet ? 'Create snippet' : 'Create prompt'}</Button>
        </div>
      </div>
    </div>
  )
}
