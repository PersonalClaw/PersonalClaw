import { useEffect, useRef } from 'react'

/** Detach a Monaco DiffEditor's text models BEFORE React unmounts it (issue 582).
 *
 *  `@monaco-editor/react` disposes the models on unmount while the DiffEditorWidget still
 *  references them, so every unmount throws "TextModel got disposed before DiffEditorWidget
 *  model got reset". ArtifactCompare already avoids the SAME error on version switches (by
 *  never blanking its bodies mid-swap) — this covers the route its comment does not: the
 *  host conditionally unmounting the whole component (Close compare, cockpit diff close).
 *
 *  One hook, both call sites (artifacts/ArtifactCompare, code/DiffView), so the next
 *  DiffEditor consumer inherits the guard instead of rediscovering the error. Usage:
 *  `const onDiffMount = useDiffTeardown()` → `<MonacoDiff onMount={onDiffMount} …/>`.
 */
type DetachableDiffEditor = { setModel: (model: null) => void }

export function useDiffTeardown(): (editor: DetachableDiffEditor) => void {
  const ref = useRef<DetachableDiffEditor | null>(null)
  useEffect(
    () => () => {
      // Best-effort: a hot-reload or an already-disposed editor may throw here, and a
      // teardown guard that itself throws on teardown would recreate the noise it exists
      // to remove.
      try {
        ref.current?.setModel(null)
      } catch {
        /* already disposed */
      }
      ref.current = null
    },
    [],
  )
  return (editor: DetachableDiffEditor) => {
    ref.current = editor
  }
}
