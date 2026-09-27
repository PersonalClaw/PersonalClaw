import { lazy, Suspense } from 'react'
import { useMode } from '../../app/theme'
import { Loading } from '../../ui/ListScaffold'

const MonacoEditor = lazy(() => import('@monaco-editor/react'))

/** The workflow editor's JSON tab: the whole editable definition as text, for everything the Steps
 *  view has no control for — adding, removing or moving a step, a new setting, a new input.
 *
 *  The same locally bundled Monaco the Files page and gists use (`app/monacoSetup.ts`), in its JSON
 *  mode, so a syntax slip is underlined where it is rather than only reported on Save. Named for the
 *  definition it holds: Monaco's default name is the same "Editor content" on every mount, which
 *  tells a screen-reader user nothing about which document they are in. Fills its container.
 *
 *  `readOnly` while the editor holds a change a save was refused for (`HeldChange` cannot reach
 *  Monaco's own input): Reload and reapply puts back the change as it was at Save, so anything
 *  typed after the refusal would be silently left out of it. */
export function WorkflowJsonEditor({ name, value, onChange, readOnly = false }: {
  name: string
  value: string
  onChange: (v: string) => void
  readOnly?: boolean
}) {
  const { mode } = useMode()
  return (
    <Suspense fallback={<Loading what="the JSON editor" />}>
      <MonacoEditor
        height="100%"
        language="json"
        value={value}
        onChange={(v) => onChange(v ?? '')}
        theme={mode === 'light' ? 'light' : 'vs-dark'}
        options={{
          ariaLabel: `${name} definition (JSON)`,
          readOnly,
          domReadOnly: readOnly,
          fontSize: 13,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          lineNumbers: 'on',
          automaticLayout: true,
          padding: { top: 10, bottom: 10 },
          tabSize: 2,
        }}
      />
    </Suspense>
  )
}
