import { useState } from 'react'
import { api, type PromptBindings } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section } from './settingsUI'
import { ListSkeleton } from '../../ui/ListScaffold'

/** Friendly labels for each prompt use-case (the runtime contexts that assemble
 *  a default system prompt). */
const USE_CASE_LABEL: Record<string, { title: string; hint: string }> = {
  chat: { title: 'Chat', hint: 'Interactive sessions — dashboard, Slack, CLI' },
  background: { title: 'Background', hint: 'Unattended runs — cron, heartbeat, campaigns' },
  code: { title: 'Code', hint: 'The Code feature’s coder agent' },
  goal_loop: { title: 'Goal Loop', hint: 'Autonomous goal-engine workers' },
}

/** Settings → Prompts: bind which prompt (from the prompt provider) serves each
 *  runtime use-case — the prompt analog of Settings → Models. Unbound use-cases
 *  fall back to the bundled default system prompt. */
export function PromptsPanel() {
  const { data, refresh } = useCachedData<PromptBindings | null>(
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
        <Section title="System prompt bindings" hint="Each context defaults to its own tailored prompt; pick another to override.">
          <div className="flex flex-col gap-2">
            {data.bindings.map((b) => {
              const meta = USE_CASE_LABEL[b.use_case] ?? { title: b.use_case, hint: '' }
              // When unbound, the effective prompt is this use-case's own bundled default.
              const defName = b.effective_ref.split(':').slice(1).join(':')
              return (
                <div key={b.use_case} className="flex items-center gap-3 rounded-lg bg-surface-container px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-on-surface text-[0.875rem]">{meta.title}</div>
                    {meta.hint && <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{meta.hint}</div>}
                  </div>
                  <select
                    value={b.ref}
                    disabled={saving === b.use_case}
                    onChange={(e) => onPick(b.use_case, e.target.value)}
                    aria-label={`Prompt for ${meta.title}`}
                    className="shrink-0 max-w-[55%] rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none"
                  >
                    <option value="">Default ({defName})</option>
                    {/* Only system-kind prompts are bindable to a use-case — a user
                        prompt is invoked in chat, not injected as the system prompt. */}
                    {data.available.filter((p) => (p.kind ?? 'system') === 'system').map((p) => (
                      <option key={p.name} value={`native:${p.name}`}>{p.title || p.name}</option>
                    ))}
                  </select>
                </div>
              )
            })}
          </div>
        </Section>
      )}
    </div>
  )
}
