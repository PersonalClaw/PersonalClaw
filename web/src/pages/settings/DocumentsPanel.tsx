import { useEffect, useState } from 'react'
import { api, type DashboardConfig } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { setDocumentEditing } from '../../ui/content/documentEditing'

/** Documents — how generated Word/Excel/PowerPoint files behave in the app.
 *
 *  One control today: whether an office document can be edited IN PLACE, or stays
 *  download-only. It is off by default and deliberately blunt about why, because saying
 *  yes accepts a real trade: PersonalClaw re-creates the file from the structure it could
 *  parse, so anything its document model cannot hold is not in the saved copy. The editor
 *  names those constructs before the first edit and again in the save confirmation, and
 *  the pre-edit version stays restorable — but the honest place to describe the trade is
 *  here, where the consent is given. */
export function DocumentsPanel() {
  const [cfg, setCfg] = useState<DashboardConfig | null>(null)
  const [saved, setSaved] = useState(false)

  const { data, error: loadErr, refresh } = useQuery('settings:documents', () => api.dashboardConfig())

  useEffect(() => { if (data) setCfg(data) }, [data])

  // A failed read must not render as "this is what you saved" — the toggle below would be
  // showing OFF whether or not the flag is on.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="settings" />

  const save = (patch: Partial<DashboardConfig>) => {
    const prev = cfg
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch)
      .then(() => {
        setSaved(true)
        setTimeout(() => setSaved(false), 1500)
        // Apply it to the content-type registry immediately: the flag decides whether the
        // office types carry an editor, and a user who just turned it on should not have
        // to reload to see the document they were looking at become editable.
        if (patch.document_editing !== undefined) setDocumentEditing(!!patch.document_editing)
      })
      .catch((e) => {
        setCfg(prev)
        notify(`Couldn't save document settings: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  return (
    <div>
      <PanelHeader title="Documents" hint="How generated Word, Excel and PowerPoint files behave. Download-only by default — editing one re-creates it, which is a trade worth choosing deliberately." />

      <Section title="Editing" hint="Whether an office document opens in an editor or stays download-only.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <Row label="Edit documents in place"
            hint="Opens a generated Word document in a structural editor instead of download-only. Saving RE-CREATES the file from the structure PersonalClaw could parse, so constructs its document model cannot hold (comments, footnotes, embedded objects, exact styling) are not in the saved copy. The editor lists them before your first edit and repeats them in the save confirmation, and the version you started from is always restorable from the document's Details › Versions. With this off, office documents are read-only previews and the server refuses a document save outright.">
            <div className="flex items-center gap-2">
              <SavedToast show={saved} />
              <Toggle on={cfg.document_editing} onChange={(v) => save({ document_editing: v })} label="Edit documents in place" />
            </div>
          </Row>
        </div>
      </Section>
    </div>
  )
}
