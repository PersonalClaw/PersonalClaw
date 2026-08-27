/** `dashboard.document_editing` → the office types' edit capability (§C6).
 *
 *  The switch is OFF by default, and off has to mean **exactly today's read-only
 *  preview** — not an editor that greys itself out. So the flag is applied to the
 *  content-type REGISTRY rather than read inside the editor: with it off, the office
 *  types carry no `edit` capability at all, which is the same registration they had
 *  before this atom, so `isEditable` is false and `<ContentSurface>` shows no view
 *  toggle, no Save, and no editor. There is no second code path to keep in step.
 *
 *  Re-registration is the registry's own documented override ("last registration for an
 *  id wins"), and it copies the live type rather than restating it, so the office types
 *  keep one definition in `registerBuiltins.ts`.
 *
 *  The server enforces the same flag on `PUT …/model` (403 while off), so a client that
 *  ignores this — or an older bundle — still cannot reach the lossy re-render. This layer
 *  is what stops the UI from OFFERING an edit that would be refused.
 */
import { useEffect, useState, type ComponentType } from 'react'
import { api } from '../../lib/api'
import { getContentType, registerContentType, type DocumentEditorProps } from './contentTypes'
import { DocumentEditor } from './DocumentEditor'
import { SheetGrid } from './SheetGrid'

/** The types whose editor this flag governs, each with the editor it mounts.
 *
 *  A spreadsheet is not a flowing document — it has no blocks to show — so `xlsx` mounts
 *  the GRID (DFE-7) rather than the block editor. `pptx` is still listed because the same
 *  switch is about it, and it keeps the document editor until `DFE-8` gives it a slide
 *  editor; the server refuses its model writes anyway (`MODEL_KINDS` has no parser for it),
 *  so the flag governing it here is the UI half of a refusal that already holds.
 *
 *  A table rather than a branch inside `setDocumentEditing`, so "which editor does this
 *  type get?" has exactly one answer to read. */
const EDITORS: Record<string, ComponentType<DocumentEditorProps>> = {
  docx: DocumentEditor,
  xlsx: SheetGrid,
  pptx: DocumentEditor,
}

export const DOCUMENT_EDITING_TYPE_IDS = ['docx', 'xlsx', 'pptx'] as const

let current = false
let inflight: Promise<boolean> | null = null

/** Apply the flag to the registry. Idempotent, and a no-op when nothing changes. */
export function setDocumentEditing(on: boolean): void {
  for (const id of DOCUMENT_EDITING_TYPE_IDS) {
    const type = getContentType(id)
    if (!type) continue
    if (on && !type.edit) {
      registerContentType({ ...type, edit: { language: 'plaintext', render: EDITORS[id] ?? DocumentEditor } })
    } else if (!on && type.edit) {
      const { edit: _dropped, ...rest } = type
      registerContentType(rest)
    }
  }
  current = on
}

/** Whether in-place document editing is currently applied to the registry. */
export function documentEditingApplied(): boolean {
  return current
}

/** Read the flag once per page load and apply it. Shared promise, so several mounted
 *  hosts cost one request; a failed read leaves the safe state (off) in place. */
export function loadDocumentEditing(): Promise<boolean> {
  if (!inflight) {
    inflight = api.dashboardConfig()
      .then((cfg) => { setDocumentEditing(!!cfg.document_editing); return current })
      .catch(() => current)
  }
  return inflight
}

/** For a host that renders an office artifact: resolves the flag and re-renders the host
 *  once it is known, so the type it resolved is re-resolved with the editor attached. */
export function useDocumentEditing(): boolean {
  const [on, setOn] = useState(current)
  useEffect(() => {
    let alive = true
    void loadDocumentEditing().then((v) => { if (alive) setOn(v) })
    return () => { alive = false }
  }, [])
  return on
}

/** Test seam: forget the cached read so a case can drive the other value. */
export function resetDocumentEditingForTests(): void {
  inflight = null
  setDocumentEditing(false)
}
