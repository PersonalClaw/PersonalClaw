import { useMemo, useState } from 'react'
import { Code2, Eye } from 'lucide-react'
import { Button } from './Button'
import { Markdown } from './Markdown'

/** An authored source (a prompt, a template, an instruction file) shown rendered by default, with
 *  a switch to its raw source: the text as written is what runs, and how it reads is what the
 *  owner checks. The switch's label and title both flip ("Raw" ⇄ "Rendered"), so it needs no
 *  second channel (`ui/rawToggleState.test.ts`).
 *
 *  Lifted out of the prompt inspector's Template section, which keeps its defaults substituted
 *  through `transform`. */
export function RenderedSource({ source, label = 'Template', transform, rawTitle, renderedTitle }: {
  /** The source as written. Nothing shows a dash in either view. */
  source: string
  /** The section's caption (default "Template"). */
  label?: string
  /** What the rendered view reads instead of the source (the prompt inspector fills each
   *  variable's default in). The raw view always shows the source. */
  transform?: (source: string) => string
  /** The switch's title while the rendered view shows (default "Show raw template"). */
  rawTitle?: string
  /** The switch's title while the raw view shows (default "Show rendered"). */
  renderedTitle?: string
}) {
  const [raw, setRaw] = useState(false)
  const rendered = useMemo(() => (transform ? transform(source) : source), [source, transform])
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-s">
        <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">{label}</div>
        <Button variant="secondary" size="xs" className="ml-auto" onClick={() => setRaw((r) => !r)}
          title={raw ? (renderedTitle ?? 'Show rendered') : (rawTitle ?? 'Show raw template')}>
          {raw ? <><Eye size={12} /> Rendered</> : <><Code2 size={12} /> Raw</>}
        </Button>
      </div>
      {raw ? (
        <pre data-type="body-s" className="rounded-md bg-surface-container px-m py-s text-on-surface-var font-mono overflow-x-auto whitespace-pre-wrap break-words">{source || '—'}</pre>
      ) : (
        <div data-type="body-s" className="rounded-md bg-surface-container px-m py-s text-on-surface leading-relaxed">
          <Markdown>{rendered || '—'}</Markdown>
        </div>
      )}
    </div>
  )
}
