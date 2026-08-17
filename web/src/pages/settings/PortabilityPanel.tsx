import { useRef, useState } from 'react'
import { Download, Upload, AlertTriangle, Loader2, FileArchive, ShieldCheck } from 'lucide-react'
import { api, type PortabilityManifest } from '../../lib/api'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { PanelHeader, Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { fvs } from '../../design/fontWeight'

/** Import / Export — the DSAR surface (DURABILITY-AND-SYNC §6, DAS-10).
 *
 *  Backed by `POST /api/durability/export` + `POST /api/durability/import`. Those
 *  RETIRED `/api/portability/*`: the old panel had one "Export archive" link and no way
 *  to ask for part of your data, so "give me my documents" meant downloading the whole
 *  home including the memory database.
 *
 *  §6 requires memory and knowledge to be SEPARATE buttons rather than one blob, and
 *  they are not interchangeable: a knowledge export is the user's own documents (the
 *  `files/` originals travel), while a memory export is the assistant's internals. The
 *  labels say which is which, because a user asking for "my data" almost always means
 *  the first one.
 *
 *  Import is plan-first: choosing a file VALIDATES it (no `mode`, nothing written) and
 *  shows what the archive claims before the Import button does anything. */

/** The domain buttons §6 names. `undefined` domains = the whole home. */
const EXPORTS: { key: string; label: string; domains?: string[]; hint: string }[] = [
  {
    key: 'full',
    label: 'Everything',
    hint: 'Every non-derived store PersonalClaw holds. Credentials never travel.',
  },
  {
    key: 'knowledge',
    label: 'Knowledge',
    domains: ['knowledge'],
    hint: 'Your documents — the files/ originals, plus the knowledge and lexicon stores.',
  },
  {
    key: 'memory',
    label: 'Memory',
    domains: ['memory'],
    hint: "The assistant's own memory: what it recorded about you, and its learning log.",
  },
]

export function PortabilityPanel() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [manifest, setManifest] = useState<PortabilityManifest | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')

  const download = async (spec: (typeof EXPORTS)[number]) => {
    setBusy(spec.key); setMsg('')
    try {
      const blob = await api.durabilityExport(spec.domains)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const tag = spec.domains ? `-${spec.domains.join('-')}` : ''
      a.href = url
      a.download = `personalclaw-export${tag}.zip`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
      notify(`${spec.label} export downloaded`, 'success')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Export failed')
    }
    setBusy(null)
  }

  /** Choosing a file validates it immediately — no `mode`, so nothing is written. */
  const pickFile = async (f: File | null) => {
    setFile(f); setManifest(null); setMsg('')
    if (!f) return
    setBusy('validate')
    try {
      const r = await api.durabilityImport(f)
      if (r.ok && r.manifest) setManifest(r.manifest)
      else setMsg(r.error?.message || 'Archive failed validation')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Validation failed') }
    setBusy(null)
  }

  const runImport = async () => {
    if (!file) return
    if (!(await confirm({ title: 'Import this archive?', body: `Merge "${file.name}" into THIS instance? Existing data is kept; the archive fills in what is missing (memory and notifications are deduplicated).`, confirmLabel: 'Import' }))) return
    setBusy('import'); setMsg('')
    try {
      const r = await api.durabilityImport(file, 'merge')
      if (r.ok) setMsg(`Import complete: ${r.summary?.items?.join(', ') || 'nothing to merge'}.`)
      else setMsg(r.error?.message || 'Import failed')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Import failed') }
    setBusy(null)
  }

  return (
    <div>
      <PanelHeader title="Import / Export" hint="Take your data out of this instance, or bring an archive in from another one." />

      <Section title="Export" hint="Download your data as a portable archive. Credentials are never included.">
        <div className="flex flex-col gap-3">
          {EXPORTS.map((spec) => (
            <div key={spec.key} className="flex flex-wrap items-center gap-3">
              <Button
                variant={spec.key === 'full' ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => download(spec)}
                disabled={busy !== null}
              >
                {busy === spec.key
                  ? <><Loader2 size={15} className="animate-spin" /> Packaging…</>
                  : <><Download size={15} /> Export {spec.label.toLowerCase()}</>}
              </Button>
              <span className="text-on-surface-low text-[0.75rem]">{spec.hint}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-on-surface-low text-[0.75rem]">
          Rebuildable caches (search indexes, model files) are left out — they regenerate,
          and a stale index restored next to newer data is worse than none. Large
          workspaces can take a minute to package.
        </p>
      </Section>

      <Section title="Import" hint="Bring settings and data in from another PersonalClaw instance.">
        <div className="rounded-lg border border-warn/30 bg-warn/5 px-4 py-3">
          <div className="flex items-start gap-2 text-[0.8125rem]" style={{ color: 'var(--color-warning)' }}>
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>Importing merges the archive's data into this instance — nothing you already have is overwritten. Choosing a file checks it first and shows what it contains.</span>
          </div>
          <input ref={fileRef} type="file" accept=".zip,application/zip" className="hidden" aria-label="Choose export archive"
            onChange={(e) => void pickFile(e.target.files?.[0] ?? null)} />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
              <FileArchive size={15} /> {file ? file.name : 'Choose archive…'}
            </Button>
            <Button size="sm" onClick={runImport} disabled={busy !== null || !file || !manifest} disabledReason={!file && busy === null ? 'Choose a file first' : (!manifest && busy === null ? 'The archive has not passed validation' : undefined)}>
              {busy === 'import' ? <><Loader2 size={15} className="animate-spin" /> Importing…</> : <><Upload size={15} /> Import</>}
            </Button>
            {busy === 'validate' && <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.75rem]"><Loader2 size={13} className="animate-spin" /> Checking archive…</span>}
            {msg && <span className="text-on-surface-low text-[0.75rem]">{msg}</span>}
          </div>
          {manifest && (
            <div className="mt-3 rounded-md bg-surface px-3 py-2 text-[0.75rem]">
              <div className="text-on-surface" style={fvs(550)}>
                Archive from {manifest.hostname} · {manifest.user} · {manifest.created_at}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low">
                <span>format v{manifest.version}</span>
                {manifest.scope && <span>scope: {manifest.scope}{manifest.domains?.length ? ` (${manifest.domains.join(', ')})` : ''}</span>}
                {/* An unverified archive is the NORMAL case for v1/v2 (they carry no
                    checksums). Saying so beats implying every archive was verified. */}
                {manifest.verified
                  ? <span className="inline-flex items-center gap-1 text-success"><ShieldCheck size={12} /> checksums verified</span>
                  : <span>no checksums to verify (pre-v3 archive)</span>}
              </div>
              <ul className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 text-on-surface-low sm:grid-cols-3">
                {Object.entries(manifest.contents || {}).map(([k, v]) => (
                  <li key={k}>{k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Section>
    </div>
  )
}
