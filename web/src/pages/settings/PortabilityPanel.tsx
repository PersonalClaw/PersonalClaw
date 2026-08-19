import { useRef, useState } from 'react'
import { Download, Upload, AlertTriangle, Loader2, FileArchive, ShieldCheck } from 'lucide-react'
import { api, type PortabilityManifest } from '../../lib/api'
import { humanBytes } from '../../lib/chunkedUpload'
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

/** The archive summary a person can read, at the moment they decide whether to merge it.
 *
 *  🔴 WHAT IT REPLACED. The panel rendered `manifest.contents` straight, `JSON.stringify` and all, so a
 *  full v3 archive answered "what is in this?" with eighteen rows like these — measured, not paraphrased:
 *
 *    workspace/knowledge/knowledge.db: 253952      run_history_files: 0
 *    security_events.jsonl: 1025511                skill_count: 25
 *    store_files: {"agents":3,"apps":61,"entity_settings":1,"projects":2,"prompt_snippets":43,…}
 *
 *  Raw store paths as labels, raw byte counts, zero-valued rows advertising what the archive does NOT
 *  hold, and a JSON object inline. Meanwhile `domain_counts` — the per-area summary the server already
 *  computes, `{knowledge: {bytes, files}, …}` for seven areas — sat UNREAD in the same payload. Same
 *  family as the packs row that printed `connector_missing:health-records`: a machine value shown to a
 *  person while the human-shaped field goes unused.
 *
 *  So: read `domain_counts` when the archive has it, and keep the raw inventory as the FALLBACK for a
 *  pre-v3 archive that carries none — dropping it entirely would turn an old archive's answer into
 *  silence, which is worse than an ugly answer.
 */
export interface ArchiveRow { label: string; detail: string }

/** Area keys the server can emit (`durability.DOMAINS`), in the words the panel already uses for the
 *  three it exports by name. The rest are capitalised rather than guessed at. */
const AREA_LABELS: Record<string, string> = {
  memory: 'Memory', knowledge: 'Knowledge', work: 'Work', automation: 'Automation',
  platform: 'Platform', config: 'Config', security: 'Security',
}
const areaLabel = (key: string) => AREA_LABELS[key] ?? (key.charAt(0).toUpperCase() + key.slice(1))
const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`

/** A `contents` value is bytes when its key looks like a FILE (it has an extension); everything else is
 *  a count. Inferring that from the key is the least-bad option available — the payload does not say —
 *  and it is why the per-area summary above is preferred whenever the archive carries one. */
const looksLikeFile = (key: string) => /\.[a-z0-9]+$/i.test(key)

export function archiveAreas(manifest: PortabilityManifest): ArchiveRow[] {
  const counts = manifest.domain_counts
  if (!counts) return []
  return Object.entries(counts)
    .sort((a, b) => (b[1]?.bytes ?? 0) - (a[1]?.bytes ?? 0))
    .map(([key, v]) => ({
      label: areaLabel(key),
      detail: `${plural(v?.files ?? 0, 'file')} · ${humanBytes(v?.bytes ?? 0)}`,
    }))
}

/** Only for an archive with no `domain_counts`. Zeros are dropped — a row saying an area holds nothing
 *  is noise at a decision point — and a nested map is spelled out rather than stringified. */
export function archiveInventory(manifest: PortabilityManifest): ArchiveRow[] {
  const raw = (manifest.contents || {}) as Record<string, unknown>
  return Object.entries(raw).flatMap(([key, v]) => {
    if (v && typeof v === 'object') {
      const inner = Object.entries(v as Record<string, number>).filter(([, n]) => Number(n) > 0)
      if (!inner.length) return []
      return [{ label: key, detail: inner.map(([k, n]) => `${k} ${n}`).join(', ') }]
    }
    const n = Number(v)
    if (!Number.isFinite(n) || n <= 0) return []
    return [{ label: key, detail: looksLikeFile(key) ? humanBytes(n) : String(n) }]
  })
}

/** The archive's own timestamp, in the reader's timezone. The sibling `ArchivePanel` learned this the
 *  hard way — its comment says `toISOString` "showed UTC — hours off from the archive's actual local
 *  write time" — and this panel was still printing the raw `2026-08-19T18:13:44Z`. */
export function archiveWhen(created: string): string {
  const d = new Date(created)
  if (Number.isNaN(d.getTime())) return created
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function PortabilityPanel() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [manifest, setManifest] = useState<PortabilityManifest | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  // Named for what it holds: the IMPORT's own result. It used to be a shared `msg` that every failure on
  // this panel — export, validation, import — also wrote to, and it renders inside the Import card. So a
  // failed EXPORT reported itself 244px below the button that failed, in the other section, under a
  // notice about importing. Failures now take the error channel (`notify`), which is where the other 61
  // failure reports in these panels go and what the sibling `DurabilityPanel` does for the same
  // `/api/durability/*` calls. The name is the guard: a catch has nothing here to write to.
  const [importResult, setImportResult] = useState('')

  const download = async (spec: (typeof EXPORTS)[number]) => {
    setBusy(spec.key)
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
      notify(`Couldn't export ${spec.label.toLowerCase()}: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
    setBusy(null)
  }

  /** Choosing a file validates it immediately — no `mode`, so nothing is written. */
  const pickFile = async (f: File | null) => {
    setFile(f); setManifest(null); setImportResult('')
    if (!f) return
    setBusy('validate')
    try {
      const r = await api.durabilityImport(f)
      if (r.ok && r.manifest) setManifest(r.manifest)
      else notify(`Couldn't read that archive: ${r.error?.message || 'it failed validation'}`, 'error')
    } catch (e) {
      notify(`Couldn't read that archive: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
    setBusy(null)
  }

  const runImport = async () => {
    if (!file) return
    if (!(await confirm({ title: 'Import this archive?', body: `Merge "${file.name}" into THIS instance? Existing data is kept; the archive fills in what is missing (memory and notifications are deduplicated).`, confirmLabel: 'Import' }))) return
    setBusy('import'); setImportResult('')
    try {
      const r = await api.durabilityImport(file, 'merge')
      if (r.ok) {
        const what = r.summary?.items?.join(', ') || 'nothing to merge'
        // Both channels, deliberately: the toast ANNOUNCES it (an on-demand `role="status"` span is not
        // reliably observed), and the line beside the button is what is still readable a minute later.
        setImportResult(`Import complete: ${what}.`)
        notify(`Import complete: ${what}`, 'success')
      } else notify(`Import failed: ${r.error?.message || 'the server gave no reason'}`, 'error')
    } catch (e) {
      notify(`Import failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
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
            {importResult && <span className="text-on-surface-low text-[0.75rem]">{importResult}</span>}
          </div>
          {manifest && (
            <div className="mt-3 rounded-md bg-surface px-3 py-2 text-[0.75rem]">
              <div className="text-on-surface" style={fvs(550)}>
                Archive from {manifest.hostname} · {manifest.user} · {archiveWhen(manifest.created_at)}
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
              {(() => {
                const areas = archiveAreas(manifest)
                const rows = areas.length ? areas : archiveInventory(manifest)
                if (!rows.length) return null
                return (
                  <ul className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 text-on-surface-low sm:grid-cols-3">
                    {rows.map((r) => (
                      <li key={r.label}>
                        <span className="text-on-surface-var">{r.label}</span> {r.detail}
                      </li>
                    ))}
                  </ul>
                )
              })()}
            </div>
          )}
        </div>
      </Section>
    </div>
  )
}
