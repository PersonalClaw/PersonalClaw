import { useEffect, useState } from 'react'

/** A react artifact's preview, prepared entirely in this app: its JSX compiled (`reactJsx`) and
 *  React itself inlined from the installed copy (`reactFrameRuntime`). Both load on first use, so
 *  neither weighs on a page that shows no react artifact. */
export type ReactPreview = { code: string; runtime: string } | { error: string }

/** Prepared previews, by source. Bounded: a long session shows many artifacts, and a preview is
 *  cheap to prepare again. */
const prepared = new Map<string, Promise<ReactPreview>>()
const KEEP = 32

export function prepareReactPreview(jsx: string): Promise<ReactPreview> {
  let preview = prepared.get(jsx)
  if (!preview) {
    preview = Promise.all([import('./reactJsx'), import('./reactFrameRuntime')])
      .then(async ([{ transformJsx }, { REACT_FRAME_RUNTIME }]): Promise<ReactPreview> => {
        const out = await transformJsx(jsx)
        return 'error' in out ? out : { code: out.code, runtime: REACT_FRAME_RUNTIME }
      })
      .catch((e: unknown): ReactPreview => ({
        error: `The preview could not be prepared: ${String((e as Error)?.message || e)}`,
      }))
    prepared.set(jsx, preview)
    if (prepared.size > KEEP) prepared.delete(prepared.keys().next().value as string)
  }
  return preview
}

/** For a render: the preview of *jsx*, or `null` while it is being prepared. A `null` *jsx* — a
 *  host showing something other than a react artifact — prepares nothing. */
export function useReactPreview(jsx: string | null): ReactPreview | null {
  const [state, setState] = useState<{ jsx: string; preview: ReactPreview } | null>(null)
  useEffect(() => {
    if (jsx === null) return
    let alive = true
    void prepareReactPreview(jsx).then((preview) => { if (alive) setState({ jsx, preview }) })
    return () => { alive = false }
  }, [jsx])
  return jsx !== null && state?.jsx === jsx ? state.preview : null
}
