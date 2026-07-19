import { useRef, useState } from 'react'
import { Download, Upload, AlertTriangle, Loader2, FileArchive } from 'lucide-react'
import { api, type PortabilityManifest } from '../../lib/api'
import { confirm } from '../../ui/dialog'
import { PanelHeader, Section } from './settingsUI'
import { Button } from '../../ui/Button'

/** Import / Export — back up this instance as a portable archive, or import one
 *  from another instance (with a preview before applying). Backed by
 *  /api/portability/export (download) + /preview + /import (multipart zip upload). */
export function PortabilityPanel() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [manifest, setManifest] = useState<PortabilityManifest | null>(null)
  const [busy, setBusy] = useState<'preview' | 'import' | null>(null)
  const [msg, setMsg] = useState('')

  const pickFile = (f: File | null) => { setFile(f); setManifest(null); setMsg('') }

  const runPreview = async () => {
    if (!file) return
    setBusy('preview'); setMsg(''); setManifest(null)
    try {
      const r = await api.portabilityPreview(file)
      if (r.ok && r.manifest) setManifest(r.manifest)
      else setMsg(r.error || 'Archive failed validation')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Preview failed') }
    setBusy(null)
  }

  const runImport = async () => {
    if (!file) return
    if (!(await confirm({ title: 'Import this archive?', body: `Merge "${file.name}" into THIS instance? Existing data is kept; the archive fills in what is missing (memory and notifications are deduplicated).`, confirmLabel: 'Import' }))) return
    setBusy('import'); setMsg('')
    try {
      const r = await api.portabilityImport(file)
      if (r.ok) setMsg(`Import complete: ${r.summary?.items?.join(', ') || 'nothing to merge'}.`)
      else setMsg(r.error || 'Import failed')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Import failed') }
    setBusy(null)
  }

  return (
    <div>
      <PanelHeader title="Import / Export" hint="Back up this instance as a portable archive, or import one from another instance." />

      <Section title="Export" hint="Download a portable archive of your settings and data.">
        <a href={api.portabilityExportUrl()} download
          className="inline-flex h-10 items-center gap-s rounded-pill bg-primary px-xl text-on-primary text-[0.9375rem] no-underline transition-colors hover:bg-primary-emphasis" style={{ fontVariationSettings: '"wght" 470' }}>
          <Download size={16} /> Export archive
        </a>
        <p className="mt-2 text-on-surface-low text-[0.78rem]">Downloads a portable bundle (config, crons, hooks, memory, workspace, skills — credentials excluded) you can store as a backup or import elsewhere. Large workspaces can take a minute to package.</p>
      </Section>

      <Section title="Import" hint="Bring settings and data in from another PersonalClaw instance.">
        <div className="rounded-lg border border-warn/30 bg-warn/5 px-4 py-3">
          <div className="flex items-start gap-2 text-[0.8rem]" style={{ color: 'var(--color-warning)' }}>
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>Importing merges the archive's data into this instance. Preview first to check what the archive contains.</span>
          </div>
          <input ref={fileRef} type="file" accept=".zip,application/zip" className="hidden" aria-label="Choose export archive"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
              <FileArchive size={15} /> {file ? file.name : 'Choose archive…'}
            </Button>
            <Button variant="secondary" size="sm" onClick={runPreview} disabled={busy !== null || !file}>
              {busy === 'preview' ? <><Loader2 size={15} className="animate-spin" /> Previewing…</> : 'Preview'}
            </Button>
            <Button size="sm" onClick={runImport} disabled={busy !== null || !file}>
              {busy === 'import' ? <><Loader2 size={15} className="animate-spin" /> Importing…</> : <><Upload size={15} /> Import</>}
            </Button>
            {msg && <span className="text-on-surface-low text-[0.78rem]">{msg}</span>}
          </div>
          {manifest && (
            <div className="mt-3 rounded-md bg-surface px-3 py-2 text-[0.78rem]">
              <div className="text-on-surface" style={{ fontVariationSettings: '"wght" 550' }}>
                Archive from {manifest.hostname} · {manifest.user} · {manifest.created_at}
              </div>
              <ul className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 text-on-surface-low sm:grid-cols-3">
                {Object.entries(manifest.contents || {}).map(([k, v]) => (
                  <li key={k}>{k}: {String(v)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Section>
    </div>
  )
}
