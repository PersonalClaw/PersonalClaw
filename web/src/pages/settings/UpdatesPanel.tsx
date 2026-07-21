import { useEffect, useState } from 'react'
import { DownloadCloud, CheckCircle2, RefreshCw, Loader2 } from 'lucide-react'
import { api, type UpdateCheck } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { FormSkeleton } from '../../ui/ListScaffold'
import { Markdown } from '../../ui/Markdown'
import { confirm } from '../../ui/dialog'

/** Updates — current version, available updates, auto-update toggle, and the
 *  rendered changelog. Backed by /api/update/check + /api/changelog + POST
 *  /api/update (apply) + /api/update/auto. */
export function UpdatesPanel() {
  const [applying, setApplying] = useState(false)
  const [msg, setMsg] = useState('')
  const [saved, setSaved] = useState(false)

  // Version + changelog change slowly — one persisted snapshot, instant on revisit.
  const { data, loading: checking, refresh } = useCachedData('settings:updates', async () => {
    const [info, changelog] = await Promise.all([
      api.updateCheck().catch(() => null as UpdateCheck | null),
      api.changelog().catch(() => ''),
    ])
    return { info, changelog }
  }, { persist: true })

  // Local editable copy of `info` so the auto-update toggle can flip optimistically
  // before the backend confirms; re-hydrated whenever a fresh snapshot lands.
  const [info, setInfo] = useState<UpdateCheck | null>(null)
  useEffect(() => { setInfo(data?.info ?? null) }, [data?.info])
  const changelog = data?.changelog ?? ''

  const check = () => { invalidateCache('settings:updates'); refresh() }

  const apply = async () => {
    if (!(await confirm({ title: 'Apply the available update?', body: 'The backend will update and may restart.', confirmLabel: 'Apply update' }))) return
    setApplying(true); setMsg('')
    try {
      const r = await api.applyUpdate()
      // Container/desktop kinds return a structured instructions payload rather
      // than applying in place — surface the commands instead of a restart note.
      if ((r as { status?: string }).status === 'instructions') {
        setMsg((r as { detail?: string }).detail || 'This install updates out-of-band — see the commands below.')
      } else {
        setMsg(r.error || 'Update started — the backend may restart.')
      }
    }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Update failed') }
    setApplying(false)
  }
  const toggleAuto = (v: boolean) => {
    setInfo((p) => p && { ...p, auto_update: v })
    api.setAutoUpdate(v).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }
  const toggleDevMode = (v: boolean) => {
    setInfo((p) => p && { ...p, update_dev_mode: v })
    api.setUpdateDevMode(v).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }

  if (!info) return <FormSkeleton sections={3} />
  const kind = info.kind ?? 'git'
  const isContainer = kind === 'container'
  const isDesktop = kind === 'desktop'
  const isGit = kind === 'git'
  // Only git+pip apply in place; container shows commands, desktop self-updates.
  const canApplyInApp = isGit || kind === 'pip'
  const kindLabel = { git: 'Git checkout', pip: 'pip / uv install', container: 'Container', desktop: 'Desktop app' }[kind] ?? kind
  return (
    <div>
      <PanelHeader title="Updates" hint="Keep the PersonalClaw core current — check for updates, auto-update, and read the changelog. Apps update individually from the Store." />

      <Section title="Version">
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-center gap-3">
            <DownloadCloud size={20} className="shrink-0 text-on-surface-low" />
            <div className="min-w-0 flex-1">
              {info.available ? (
                <>
                  <div className="text-on-surface text-[0.9rem]" style={{ fontVariationSettings: '"wght" 550' }}>Update available{info.latest ? ` — ${info.latest}` : ''}</div>
                  <div className="text-on-surface-low text-[0.78rem]">
                    {info.changes || 'A new version is ready to install.'}
                    {isGit && typeof info.commits_behind === 'number' && info.commits_behind > 0 ? ` (${info.commits_behind} commit${info.commits_behind === 1 ? '' : 's'} behind)` : ''}
                  </div>
                </>
              ) : (
                <div className="flex items-center gap-1.5 text-[0.9rem]" style={{ color: 'var(--color-success)' }}>
                  <CheckCircle2 size={15} /> <span className="text-on-surface">{info.checked ? 'Up to date' : 'No update check yet'}</span>
                </div>
              )}
              <div className="text-on-surface-low mt-0.5 text-[0.7rem]">Install type: {kindLabel}{info.current ? ` · v${info.current}` : ''}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button busy={checking} onClick={check} label="Check"><RefreshCw size={14} /> Check</Button>
              {info.available && canApplyInApp && <Button busy={applying} primary onClick={apply} label="Update"><DownloadCloud size={14} /> Update</Button>}
            </div>
          </div>
          {msg && <div className="mt-2 text-on-surface-low text-[0.78rem]">{msg}</div>}

          {/* Container: no in-place apply — show the exact pull+recreate commands. */}
          {isContainer && info.available && (
            <div className="mt-3 rounded-md bg-surface-high px-3 py-2">
              <div className="text-on-surface-low mb-1 text-[0.72rem]">Update this container install by pulling the new image and recreating:</div>
              <pre className="overflow-auto text-[0.75rem] leading-relaxed text-on-surface"><code>{(info.instructions?.length ? info.instructions : ['docker compose -f deploy/compose/compose.yaml pull', 'docker compose -f deploy/compose/compose.yaml up -d']).join('\n')}</code></pre>
            </div>
          )}
          {/* Desktop: the shell (electron-updater) owns updates. */}
          {isDesktop && info.available && (
            <div className="mt-3 rounded-md bg-surface-high px-3 py-2 text-on-surface-low text-[0.75rem]">The desktop app updates itself on the next launch.</div>
          )}
        </div>
      </Section>

      <Section title="Automatic updates">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <Row label="Auto-update" hint="Download and apply updates automatically when available.">
            <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={info.auto_update} onChange={toggleAuto} label="Auto-update" /></div>
          </Row>
          {/* Dev-mode toggle: git checkouts only (track every commit vs. ride release tags). */}
          {isGit && (
            <Row label="Developer update mode" hint="Track every new commit on your branch instead of only tagged releases (contributors).">
              <div className="flex items-center gap-2"><Toggle on={!!info.update_dev_mode} onChange={toggleDevMode} label="Developer update mode" /></div>
            </Row>
          )}
        </div>
      </Section>

      <Section title="Changelog" hint="What's changed recently.">
        {changelog.trim()
          // CHANGELOG.md is markdown — render it (headings/lists/links), not a raw <pre>.
          ? <div className="max-h-96 overflow-auto rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem]">
              <Markdown>{changelog}</Markdown>
            </div>
          : <p className="text-on-surface-low text-[0.8rem] italic">No changelog available.</p>}
      </Section>
    </div>
  )
}

// small inline button (the shared Button is fine, but these need a busy spinner inline)
function Button({ children, onClick, busy, primary, label }: { children: React.ReactNode; onClick: () => void; busy?: boolean; primary?: boolean; label: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-label={label}
      className="inline-flex h-8 items-center gap-1.5 rounded-pill px-l text-[0.8125rem] transition-colors disabled:opacity-50"
      style={primary ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' }}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : children}
    </button>
  )
}
