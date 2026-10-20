import { useRef, useState } from 'react'
import { api, type CodeProject } from '../../../../lib/api'
import { type CodeToastState } from '../../CodeToast'
import { loopToCodeProject } from '../codeProject'

// ── The cockpit title's controller ────────────────────────────────────────────────────────────
//
// Renaming the project from its title: the draft, Escape cancelling without a save, one save for
// the Enter and the blur that follows it, and a failed rename said through the cockpit toast.
// `CockpitHeader.tsx` draws it.

export function useTitleRename({ id, project: p, setProject, setToast }: {
  id: string; project: CodeProject; setProject: (p: CodeProject) => void; setToast: (t: CodeToastState | null) => void
}) {
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // Set by Escape so the blur that fires as the input unmounts skips the save.
  const cancelRename = useRef(false)
  // Guards the double commitRename (Enter → unmount → onBlur) from a double PUT.
  const renameInFlight = useRef(false)
  // Rename works in ANY state — the name is pure metadata; the rest of the spec
  // freezes once started. updateCode with a name-only body routes to the rename
  // path server-side (PUT /api/code/{id} → store.rename), so a running or finished
  // project can still be retitled.
  function startRename() { cancelRename.current = false; setTitleDraft(p.name || ''); setEditingTitle(true) }
  function abortRename() { cancelRename.current = true; setEditingTitle(false) }
  async function commitRename() {
    setEditingTitle(false)
    if (cancelRename.current) { cancelRename.current = false; return }
    // Enter sets editingTitle=false, which unmounts the input → fires onBlur →
    // commitRename AGAIN. The 2nd call still sees the old p.name (setProject is
    // post-await), so the name-unchanged guard below doesn't catch it → a double PUT.
    // Guard re-entry while the rename is in flight.
    if (renameInFlight.current) return
    const name = titleDraft.trim()
    if (!name || name === (p.name || '')) return
    renameInFlight.current = true
    // Surface a failed rename instead of silently keeping the old name — a swallowed
    // catch left the user thinking the rename took when it didn't (network/5xx, or a
    // backend rejection). Toast the reason so the no-op isn't a mystery.
    try { setProject(loopToCodeProject(await api.updateULoop(id, { name }))) }
    catch (e) { setToast({ kind: 'error', text: `Couldn't rename: ${(e as Error).message || 'unknown error'}` }) }
    finally { renameInFlight.current = false }
  }
  return { editingTitle, titleDraft, setTitleDraft, startRename, abortRename, commitRename }
}
