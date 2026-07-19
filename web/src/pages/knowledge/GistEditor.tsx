import { lazy, Suspense } from 'react'
import { Loader2 } from 'lucide-react'
import { useMode } from '../../app/theme'

const MonacoEditor = lazy(() => import('@monaco-editor/react'))

// Gist language (knowledgeMeta.GIST_LANGUAGES / highlight.js ids) → Monaco language id.
// Most match 1:1; only the few that differ are mapped. Unknown → 'plaintext'.
const MONACO_LANG: Record<string, string> = {
  typescript: 'typescript', javascript: 'javascript', python: 'python', go: 'go',
  rust: 'rust', java: 'java', c: 'c', cpp: 'cpp', html: 'html', css: 'css',
  sql: 'sql', bash: 'shell', json: 'json', yaml: 'yaml', markdown: 'markdown',
}

export function gistMonacoLang(lang?: string | null): string {
  return MONACO_LANG[(lang || '').trim()] ?? 'plaintext'
}

/** A fully-featured code editor for gist content — the same Monaco the Files page uses,
 *  with syntax highlighting driven by the gist's selected language. Fills its container
 *  (give it a sized parent). Read-only mode dims + disables editing (e.g. an immutable
 *  journal-day, though journals aren't gists — kept for symmetry with the textarea it
 *  replaces). */
export function GistEditor({ value, onChange, language, readOnly = false }: {
  value: string
  onChange: (v: string) => void
  language?: string | null
  readOnly?: boolean
}) {
  const { mode } = useMode()
  return (
    <Suspense fallback={<div className="grid h-full place-items-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
      <MonacoEditor
        height="100%"
        language={gistMonacoLang(language)}
        value={value}
        onChange={(v) => onChange(v ?? '')}
        theme={mode === 'light' ? 'light' : 'vs-dark'}
        options={{
          readOnly,
          fontSize: 13,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          lineNumbers: 'on',
          automaticLayout: true,
          padding: { top: 10, bottom: 10 },
          tabSize: 2,
          renderWhitespace: 'selection',
        }}
      />
    </Suspense>
  )
}
