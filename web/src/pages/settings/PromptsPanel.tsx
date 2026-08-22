import { useState } from 'react'
import { api, type PromptBinding, type PromptItem, type PromptBindings } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section } from './settingsUI'
import { ListSkeleton } from '../../ui/ListScaffold'

/** Settings → Prompts: bind which prompt (from the prompt provider) serves each
 *  runtime use-case — the prompt analog of Settings → Models. Unbound use-cases
 *  fall back to the bundled default system prompt.
 *
 *  🔴 This panel used to carry its own four-entry label table against a catalog of
 *  forty-four bindable contexts. Measured live: **4 rows named, 40 rendering their
 *  raw key** — `nl_to_cron`, `history_compression`, `cycle_judge_skeptic` — with no
 *  description, in a single flat list, and with `aria-label="Prompt for nl_to_cron"`
 *  as the accessible name of the picker. A local table can only ever cover the
 *  contexts that existed when someone wrote it, and this vocabulary is OPEN: an app
 *  may contribute a bindable use case at any time.
 *
 *  So the label, the hint and the grouping now arrive with each binding, from the
 *  module that owns the vocabulary. The catalog had already declared the grouping —
 *  `category`, whose docstring says it "groups it for the Settings UI" — and the UI
 *  had simply never been sent it. */
export function PromptsPanel() {
  const { data, refresh } = useQuery<PromptBindings | null>(
    'settings:prompt-bindings', () => api.promptBindings().catch(() => null), { persist: true },
  )
  const [saving, setSaving] = useState('')

  const onPick = async (useCase: string, ref: string) => {
    setSaving(useCase)
    try {
      await api.setPromptBinding(useCase, ref)
      refresh()
    } finally {
      setSaving('')
    }
  }

  return (
    <div>
      <PanelHeader title="Prompts" hint="Bind which prompt serves each runtime context. Edit the prompts themselves on the Prompts page; unset uses each context's bundled default." />
      {!data ? (
        <ListSkeleton rows={4} />
      ) : (
        data.categories.map((c) => {
          const rows = data.bindings.filter((b) => b.category === c.key)
          // The backend only sends a category that holds a row, but filtering here
          // too means a stale cached payload cannot render an empty heading.
          if (rows.length === 0) return null
          return (
            <Section key={c.key} title={c.label} hint={c.hint}>
              <div className="flex flex-col gap-2">
                {rows.map((b) => (
                  <BindingRow key={b.use_case} binding={b} available={data.available}
                    saving={saving === b.use_case} onPick={onPick} />
                ))}
              </div>
            </Section>
          )
        })
      )}
    </div>
  )
}

/** One context → its bound prompt. The label and hint are the backend's; this row
 *  invents no copy, so a context added tomorrow arrives already described. */
function BindingRow({ binding, available, saving, onPick }: {
  binding: PromptBinding
  available: PromptItem[]
  saving: boolean
  onPick: (useCase: string, ref: string) => void
}) {
  // When unbound, the effective prompt is this use-case's own bundled default.
  const defName = binding.effective_ref.split(':').slice(1).join(':')
  return (
    <div className="flex items-center gap-3 rounded-lg bg-surface-container px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-on-surface text-[0.8125rem]">{binding.label}</div>
        {binding.hint && <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{binding.hint}</div>}
      </div>
      <select
        value={binding.ref}
        disabled={saving}
        onChange={(e) => onPick(binding.use_case, e.target.value)}
        aria-label={`Prompt for ${binding.label}`}
        className="shrink-0 max-w-[55%] rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50"
      >
        <option value="">Default ({defName})</option>
        {/* Only system-kind prompts are bindable to a use-case — a user
            prompt is invoked in chat, not injected as the system prompt. */}
        {available.filter((p) => (p.kind ?? 'system') === 'system').map((p) => (
          <option key={p.name} value={`native:${p.name}`}>{p.title || p.name}</option>
        ))}
      </select>
    </div>
  )
}
