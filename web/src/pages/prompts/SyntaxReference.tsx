import { useMemo, useState } from 'react'
import { BookOpen, ChevronRight, Braces, GitBranch, Repeat, FunctionSquare, Puzzle, Hash, Minus } from 'lucide-react'
import { useCachedData } from '../../lib/useCachedData'
import { api, type PromptSyntax, type PromptSyntaxFn } from '../../lib/api'

/** The template-language reference, rendered as a categorized, collapsible,
 *  click-to-insert cheatsheet — the reference doubles as a palette (the strongest
 *  pattern from the peer prompt editors). Constructs first (the grammar), then the
 *  built-in functions grouped by category. Fetched once from /api/prompts/syntax
 *  so the docs are generated from the live engine and can never drift. */

const CONSTRUCT_ICON: Record<string, typeof Braces> = {
  variable: Braces, conditional: GitBranch, loop: Repeat,
  function: FunctionSquare, include: Puzzle, comment: Hash, whitespace: Minus,
}
const FN_CATEGORY_ORDER = ['string', 'array', 'object', 'math', 'logic', 'type', 'util']

export function SyntaxReference({ onInsert }: { onInsert?: (snippet: string) => void }) {
  const { data } = useCachedData<PromptSyntax>('prompts:syntax', () => api.promptSyntax())
  const [openCat, setOpenCat] = useState<string | null>('string')

  const fnsByCat = useMemo(() => {
    const m: Record<string, PromptSyntaxFn[]> = {}
    for (const f of data?.functions ?? []) (m[f.category] ??= []).push(f)
    return m
  }, [data])

  const cats = useMemo(() => {
    const present = Object.keys(fnsByCat)
    return [...FN_CATEGORY_ORDER.filter((c) => present.includes(c)), ...present.filter((c) => !FN_CATEGORY_ORDER.includes(c))]
  }, [fnsByCat])

  return (
    <div className="flex flex-col gap-3 text-[0.8125rem]">
      <div className="flex items-center gap-1.5 text-on-surface-var"><BookOpen size={14} /> <span data-type="title-s">Syntax</span></div>

      {/* Constructs — the grammar. Each row inserts a working scaffold. */}
      <div className="flex flex-col gap-1">
        {(data?.constructs ?? []).map((c) => {
          const Icon = CONSTRUCT_ICON[c.category] ?? Braces
          return (
            <button key={c.label} type="button" onClick={() => onInsert?.(c.snippet)}
              title={c.description}
              className="group flex items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-surface-high transition-colors disabled:cursor-default"
              disabled={!onInsert}>
              <Icon size={13} className="mt-0.5 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <span className="block text-on-surface">{c.label}</span>
                <code className="block truncate font-mono text-[0.75rem] text-on-surface-low">{c.snippet.split('\n')[0]}</code>
              </span>
            </button>
          )
        })}
      </div>

      {/* Functions — grouped by category, collapsible, click to insert fn(). */}
      <div className="flex flex-col gap-0.5 border-t border-outline-variant/40 pt-2">
        <div className="px-2 pb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Functions</div>
        {cats.map((cat) => {
          const fns = fnsByCat[cat] ?? []
          const open = openCat === cat
          return (
            <div key={cat}>
              <button type="button" onClick={() => setOpenCat(open ? null : cat)}
                className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left hover:bg-surface-high transition-colors">
                <ChevronRight size={13} className={`shrink-0 text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
                <span className="capitalize text-on-surface-var">{cat}</span>
                <span className="ml-auto rounded-pill bg-surface-high px-1.5 text-[0.7rem] text-on-surface-low">{fns.length}</span>
              </button>
              {open && (
                <div className="flex flex-col gap-0.5 pb-1 pl-5">
                  {fns.map((f) => (
                    <button key={f.name} type="button" onClick={() => onInsert?.(`{{ ${f.insert} }}`)}
                      title={f.description}
                      className="group flex flex-col rounded-md px-2 py-1 text-left hover:bg-surface-high transition-colors disabled:cursor-default"
                      disabled={!onInsert}>
                      <code className="font-mono text-[0.75rem] text-primary">{f.signature}</code>
                      <span className="text-on-surface-low text-[0.7rem]">{f.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
