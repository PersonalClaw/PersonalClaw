import { useEffect, useMemo, useState } from 'react'
import { api, type PromptItem } from '../../lib/api'
import { Combobox } from '../../ui/Combobox'
import type { WidgetMap } from '../tools/schema'

/** The saved-prompt schema widget shared by every form that adopts
 *  `x-meta.widget: "prompt"`. The schema renderer stays feature-agnostic: this
 *  hook owns the prompt API and supplies the caller-provided WidgetMap. */
export function usePromptWidgets(needsPrompt: boolean): {
  prompts: PromptItem[]
  widgets: WidgetMap
} {
  const [loadedPrompts, setLoadedPrompts] = useState<PromptItem[] | null>(null)

  useEffect(() => {
    if (!needsPrompt || loadedPrompts !== null) return
    let alive = true
    api.prompts('user')
      .then((items) => { if (alive) setLoadedPrompts(items) })
      .catch(() => { if (alive) setLoadedPrompts([]) })
    return () => { alive = false }
  }, [loadedPrompts, needsPrompt])

  const prompts = loadedPrompts ?? []
  const widgets: WidgetMap = useMemo(() => ({
    prompt: ({ value, onChange, placeholder }) => (
      <Combobox
        options={prompts.map((p) => ({
          value: p.name,
          label: p.name,
          description: p.description || undefined,
        }))}
        value={String(value ?? '')}
        onChange={onChange}
        placeholder={placeholder || 'Pick a saved prompt…'}
        emptyText="No saved prompts"
      />
    ),
  }), [prompts])

  return { prompts, widgets }
}
