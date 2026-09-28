import { useEffect, useState } from 'react'
import { AlertTriangle, CircleOff, DownloadCloud, CheckCircle2, ExternalLink, RefreshCw, Undo2, X } from 'lucide-react'
import { api, type UpdateCheck } from '../../lib/api'
import { updateVerdict, updateVerdictLabel } from './updateVerdict'
import { useQuery, invalidateKeys } from '../../lib/data'
import { PanelHeader, Section, RowGroup, Row, Field, Toggle, SegPills, SavedToast } from './settingsUI'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { TextInput, NumberField, useSyncedDraft } from '../../ui/forms'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { Markdown } from '../../ui/Markdown'
import { confirm } from '../../ui/dialog'
import { fvs } from '../../design/fontWeight'
import { notify } from '../../app/appSdk'
import { repoDocUrl } from '../../lib/repoDocs'

/** Where an update's upgrade steps live: the CHANGELOG is headline-only, so they are in the docs. */
export const UPGRADE_NOTES_DOC = 'docs/guides/getting-started.md#updating'

/** The advice every rollback's confirm carries: pre-1.0 there is no migration either way, so state
 *  written by the newer build is the real risk a downgrade meets. The dialog renders its body as
 *  given, so the command is marked up as code rather than wrapped in backticks it would print. */
const DOWNGRADE_ADVICE = (
  <>Run <code className="font-mono">personalclaw snapshot</code> first — a downgrade can meet state written by the newer version, and pre-1.0 releases carry no migration.</>
)

/** Updates — the whole release-tracking surface: which line this install follows, whether it
 *  applies on its own, whether it phones GitHub at all, and how to get back to the version
 *  that was working. Backed by /api/update/check (one snapshot for every control) +
 *  /api/changelog + POST /api/update (apply); every control writes its `updates.*` config
 *  field through PATCH /api/config/personalclaw.
 *
 *  Six controls, one per field the `updates` block owns. Before this, the panel
 *  rendered exactly TWO boolean switches over six fields: "Auto-update" (off ⇄ staged) and
 *  "Developer update mode" (stable ⇄ nightly). So `beta` was unreachable from the UI, a
 *  version pin could only be set by hand-editing `config.json`, and the one control the
 *  privacy section of the README tells users about — the update check's kill switch — had no
 *  home in the app at all.
 *
 *  🔑 EVERY CONTROL READS FROM THE SAME `/api/update/check` SNAPSHOT, not a second
 *  `GET /api/config/personalclaw`. Two reads would let this screen show a channel from one
 *  and an interval from the other, which is the class of drift a settings panel can never
 *  explain to the user.
 *
 *  🔑 THE ROLLBACK CONTROL IS NOT DECORATIVE. It offers `updates.last_version`, which
 *  `self_update.record_running_version` writes at gateway startup when the running version
 *  changes — until RUM-9 nothing wrote that field, so an earlier "Roll back" button
 *  would have read an always-empty string. It is hidden when there is nothing to offer,
 *  because "Roll back to v" with nothing after it is worse than no offer. */
/** The document's front matter is written for CONTRIBUTORS, and it was rendering as product copy.
 *
 *  Measured on `#/settings/updates`: `/api/changelog` serves CHANGELOG.md verbatim (255,413 chars), so the
 *  card headed "Changelog · What's changed recently" opened with
 *
 *    H1  Changelog                                    ← a SECOND <h1>, nested inside this h2 section
 *    P   All notable changes to PersonalClaw are recorded here. The format follows Keep a Changelog…
 *    P   The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."
 *
 *  — a duplicated title, a note about the format, and a sentence telling the reader how the panel they
 *  are looking at is implemented. The endpoint is right to serve the file whole; deciding what "what's
 *  changed recently" means is this panel's job.
 *
 *  Also fixes the outline. `## [Unreleased]` rendered as an `h2`, a SIBLING of the panel's own
 *  "Version" / "Automatic updates" / "Changelog" sections, so heading navigation read a release as a
 *  peer of the page's furniture. Demoting by one puts the release under the section that introduces it:
 *  h1 Updates › h2 Changelog › h3 Unreleased › h4 Added.
 *
 *  The CHANGELOG is headline-only — one line per entry, `- **<headline>**` — so two more things the
 *  card used to get from the document are wrong for it, and both are reshaped here:
 *  · THE BOLD. It separated a headline from the paragraphs under it. With no paragraphs it marks
 *    nothing, and measured on the card, 926 of 962 items rendered bold (weight 600) under an `Added`
 *    heading drawn lighter than the items it heads. An entry that is one bold span is shown plain; an
 *    entry with bold INSIDE its sentence keeps it, since there the bold picks out a word.
 *  · THE GAPS. Sections that separate entries with blank lines parse as "loose" lists, which wrap
 *    every item in a paragraph: the gap between two items read 6px in one section and 36px in the
 *    next. A blank line between two entries carries nothing now, so it is dropped.
 *
 *  Two deliberate refusals:
 *  · Fenced code is left alone — headings, bullets and blank lines inside it. The CHANGELOG has no
 *    fence today, which is exactly why the guard is asserted synthetically in the rail rather than
 *    trusted to a green run; the release notes this also renders can have one.
 *  · A document with no `## ` release heading is returned UNCHANGED. Hiding everything because a parse
 *    found nothing is the worse failure: an empty "what's new" reads as "nothing has changed". */
export function changelogBody(md: string): string {
  const lines = md.split('\n')
  const first = lines.findIndex((l) => l.startsWith('## '))
  if (first < 0) return md
  const out: string[] = []
  let fenced = false
  for (let i = first; i < lines.length; i++) {
    const l = lines[i]
    if (l.trimStart().startsWith('```')) { fenced = !fenced; out.push(l); continue }
    if (fenced) { out.push(l); continue }
    if (/^#{1,5} /.test(l)) { out.push(`#${l}`); continue }
    if (!l.trim() && out.length > 0 && out[out.length - 1].startsWith('- ')) {
      let next = i + 1
      while (next < lines.length && !lines[next].trim()) next++
      if (next < lines.length && lines[next].startsWith('- ')) { i = next - 1; continue }
    }
    out.push(plainHeadline(l))
  }
  return out.join('\n')
}

/** A headline-only entry, `- **<headline>**`, as the card shows it: without the bold.
 *
 *  The bold span is read the way the CHANGELOG is written (`tests/test_changelog_headline_only.py`):
 *  it closes at the first `**` after a non-space that is not followed by a word character, a backtick
 *  or `*`, and that leaves the bold inside it balanced. Only an entry that is ONE such span loses it:
 *  `- **A → **B** C**` becomes `- A → **B** C`, and `- **A** and **B**` is left as written. */
export function plainHeadline(line: string): string {
  if (!line.startsWith('- **')) return line
  const text = line.slice(2)
  for (const close of text.matchAll(/(?<=\S)\*\*(?![\w`*])/g)) {
    const at = close.index ?? 0
    if (at < 2) continue
    if ((text.slice(2, at).match(/\*\*/g) ?? []).length % 2 !== 0) continue
    return at + 2 === text.length ? `- ${text.slice(2, at)}` : line
  }
  return line
}

/** The entries an update brings, from the CHANGELOG lines the git check found added upstream
 *  (`changes`): the entry lines only, one list, shown plain like the card below. The headings in the
 *  diff (a release cut's `## [X.Y.Z]`) name no change, so they are left out. Empty when the diff
 *  added no entry. */
export function updateEntries(changes: string): string {
  return changes.split('\n').filter((l) => l.startsWith('- ')).map(plainHeadline).join('\n')
}

export function UpdatesPanel() {
  const [applying, setApplying] = useState(false)
  const [msg, setMsg] = useState('')
  // WHICH control just saved, not merely "something did": six controls share this panel, and a
  // boolean would flash "Saved ✓" beside all of them for a write the user made to one.
  const [saved, setSaved] = useState('')

  // Version + changelog change slowly — one persisted snapshot, instant on revisit.
  const { data, loading: checking, error: loadErr, refresh } = useQuery('settings:updates', async () => {
    const [info, changelog] = await Promise.all([
      // 🔴 The version check IS the panel — a substituted null read as "still loading" and left it
      // shimmering forever (measured: 0 controls, one `aria-busy` skeleton, no alert). The changelog
      // keeps its fallback: it decorates a section further down.
      api.updateCheck(),
      api.changelog().catch(() => ''),
    ])
    return { info, changelog }
  }, { persist: true })

  // Local editable copy of `info` so each control can flip optimistically before the backend
  // confirms; re-hydrated whenever a fresh snapshot lands.
  const [info, setInfo] = useState<UpdateCheck | null>(null)
  useEffect(() => { setInfo(data?.info ?? null) }, [data?.info])
  // The pin is the one free-text control, so it holds a draft until commit — patching per
  // keystroke would write `0`, `0.`, `0.2` and refuse most of them.
  //
  // 🪤 SEEDED FROM THE LIVE `info`, NOT FROM `data.info` — found by driving the panel. The query
  // snapshot's `pin` does not move when THIS panel writes one (the write updates `info`
  // optimistically and does not refetch), so a draft seeded from `data` never re-synced: after
  // clearing a pin and pressing Check, the box still showed the version it had just removed while
  // the row beside it correctly said "Stable channel". Following `info.pin` re-syncs after every
  // Save/Clear and every refresh that changes the stored pin, and still never clobbers typing —
  // `info.pin` does not change while you type, only when a write lands, and it is never re-seeded
  // on mount (`useSyncedDraft`).
  const [pinDraft, setPinDraft] = useSyncedDraft(info?.pin ?? '')
  const changelog = data?.changelog ?? ''

  const check = () => { invalidateKeys('settings:updates'); refresh() }

  /** Apply whatever the backend resolves for the current channel/pin, and say what happened. */
  const runApply = async () => {
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

  const apply = async () => {
    if (!(await confirm({ title: 'Apply the available update?', body: 'The backend will update and may restart.', confirmLabel: 'Apply update' }))) return
    await runApply()
  }
  // 🔑 THE EXACT SHAPE `saveFailureReported` WAS WRITTEN FOR, and its sweep could not see these: it
  // matches `api.save*` and `api.patchConfig` only, so an optimistic write named `set*` is invisible to
  // it. Both toggles flipped `info` locally, showed "Saved" on `.then` only, and discarded the rejection —
  // so a refused write left the switch on, no confirmation, and nothing said. A reload silently reverts it.
  //
  // `notify` rather than this file's `setMsg`: `msg` renders inside the apply-update block, several
  // sections away from these controls, so a failure message there would appear detached from the
  // control that caused it. The toast is the app-wide affordance and is announced through `role="alert"`.
  //
  // Not reverting the optimistic flip — the family's remedy for this shape is to tell, not to fight the
  // control the user just touched (see `chat/selectionPersistReported`).
  const reportSettingFailure = (what: string) => (e: unknown) => {
    let msg = e instanceof Error ? e.message : 'the request failed'
    try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw text */ }
    notify(`Couldn't ${what}: ${msg}`, 'error')
  }

  /** Write one `updates.*` field: flip locally, PATCH, then confirm beside THAT control or report.
   *
   *  One helper rather than six near-identical handlers, because the failure mode they all share is
   *  the one `saveFailureReported` pins: an optimistic flip whose rejection is discarded leaves the
   *  control displaying a value the server refused.
   *
   *  🪤 THE PIN IS NOT OPTIMISTIC, and it is the one exception. The server now refuses a pin that is
   *  not a release version, and a pin flipped locally before that answer would have the Version row
   *  read "pinned to not-a-version!!" beside the toast saying it was refused. The draft keeps what
   *  was typed either way, so nothing the user wrote is lost while they fix it.
   *
   *  🪤 AND THREE FIELDS RE-RUN THE CHECK ONCE SAVED. The channel, the pin and the check switch each
   *  change which release the check compares against — or whether it compares at all — so the
   *  headline computed before the write describes a different question. Re-checking is what makes a
   *  pin that names no release say so at the moment it is saved, rather than after the next Check. */
  const write = (field: keyof UpdateCheck & string, value: unknown, what: string) => {
    if (field !== 'pin') setInfo((p) => p && { ...p, [field]: value })
    api.patchConfig(`updates.${field}`, value)
      .then(() => { setSaved(field); window.setTimeout(() => setSaved((k) => (k === field ? '' : k)), 1600)
        if (field === 'pin') setInfo((p) => p && { ...p, pin: String(value) })
        if (field === 'channel' || field === 'pin' || field === 'check_enabled') check()
      })
      .catch(reportSettingFailure(what))
  }

  /** Pin the previous version and apply it — the rollback.
   *
   *  A PIN, not a one-shot install: without it the next scheduled check would resolve the channel's
   *  newest release and offer to jump straight back to the version the user just left. The snapshot
   *  advice is in the confirm body because pre-1.0 there is no migration either way, so state written
   *  by the newer build is the real risk a downgrade carries. */
  const rollback = async () => {
    const to = info?.last_version ?? ''
    if (!to) return
    if (!(await confirm({
      title: `Roll back to v${to}?`,
      body: <>This pins updates.pin to {to} and installs it, so later checks stay on that release. {DOWNGRADE_ADVICE}</>,
      confirmLabel: `Roll back to v${to}`,
    }))) return
    setInfo((p) => p && { ...p, pin: to })
    setPinDraft(to)
    // 🔴 THE PIN MUST LAND BEFORE THE APPLY, and a failed pin must STOP it: applying with the pin
    // still unset resolves the CHANNEL, which on an install that has a rollback to offer means
    // installing the newest release — an upgrade for a user who just asked to go back. So the
    // rejection is reported through this panel's own reporter and the apply is abandoned, rather
    // than letting a confirmed action half-happen.
    const pinned = await api.patchConfig('updates.pin', to)
      .then(() => true)
      .catch((e: unknown) => { reportSettingFailure(`pin v${to}`)(e); return false })
    if (!pinned) return
    await runApply()
  }

  /** Apply the pinned release this install is NEWER than — the rollback a pin set up and has not
   *  applied. The pin is already stored, so this is only the apply, which resolves exactly that
   *  release; the confirm carries the same snapshot advice as `rollback`. */
  const applyPinnedRollback = async () => {
    const to = info?.latest || info?.pin || ''
    if (!to) return
    if (!(await confirm({
      title: `Roll back to v${to}?`,
      body: <>This installs the pinned release v{to}, older than the v{info?.current ?? ''} running now. {DOWNGRADE_ADVICE}</>,
      confirmLabel: `Roll back to v${to}`,
    }))) return
    await runApply()
  }

  // Error before the skeleton, or the skeleton wins forever.
  if (!info && loadErr) return <LoadError what="update status" error={loadErr} onRetry={refresh} />
  if (!info) return <FormSkeleton sections={3} what="update status" />
  const kind = info.kind ?? 'git'
  const isContainer = kind === 'container'
  const isDesktop = kind === 'desktop'
  const isGit = kind === 'git'
  // Only git+pip apply in place; container shows commands, desktop self-updates.
  const canApplyInApp = isGit || kind === 'pip'
  const kindLabel = { git: 'Git checkout', pip: 'pip / uv install', container: 'Container', desktop: 'Desktop app' }[kind] ?? kind
  const channel = info.channel ?? 'stable'
  // `nightly` tracks the checked-out branch, so it exists only on a git install — offering it on a
  // wheel would offer a lane with no published artifact (the resolver silently rides `stable`).
  const channelOptions: { key: 'stable' | 'beta' | 'nightly'; label: string }[] = [
    { key: 'stable', label: 'Stable' },
    { key: 'beta', label: 'Beta' },
    ...(isGit ? [{ key: 'nightly' as const, label: 'Developer' }] : []),
  ]
  const checkEnabled = info.check_enabled !== false
  const rollbackTo = info.last_version ?? ''
  const pinnedTo = info.latest || info.pin || ''
  const releaseNotes = info.release_notes ?? ''
  const newEntries = updateEntries(info.changes ?? '')
  // Each CHANGELOG entry is a headline and nothing else, so what a breaking change asks of you — a
  // field to set, a token to re-mint — is not under it any more: it is in the Updating guide.
  const upgradeNotes = (
    <TextLink href={repoDocUrl(UPGRADE_NOTES_DOC)} external size="sm" ink="emphasis" icon={ExternalLink}
      iconPosition="trailing" aria-label="Open the upgrade notes: what an update asks of you">Upgrade notes</TextLink>
  )
  const channelName = { stable: 'Stable', beta: 'Beta', nightly: 'Developer' }[channel] ?? channel
  const verdict = updateVerdict(info)
  // A pin already set back to that release offers its rollback in the Version row, so the
  // Release line row would be the same action twice.
  const canRollBack = Boolean(rollbackTo) && rollbackTo !== (info.current ?? '')
    && !(verdict === 'pin_older' && rollbackTo === pinnedTo)
  return (
    <div>
      <PanelHeader title="Updates" hint="Keep the PersonalClaw core current — choose a release line, pin a version, decide whether updates apply on their own, and read what changed. Apps update individually from the Store." />

      <Section title="Version">
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-center gap-3">
            <DownloadCloud size={20} className="shrink-0 text-on-surface-low" />
            <div className="min-w-0 flex-1">
              {verdict === 'available' ? (
                <>
                  <div data-type="title-m" className="text-on-surface" style={fvs(550)}>{updateVerdictLabel(info)}</div>
                  {/* `changes` is the CHANGELOG lines the git check found added upstream. It was printed
                      as caption text, so its entries showed their `- **` marks and ran together into
                      one line; they are headline-only now, so they render as the list they are. */}
                  <div data-type="caption" className="text-on-surface-low">
                    {newEntries ? 'What the update brings:' : 'A new version is ready to install.'}
                    {isGit && typeof info.commits_behind === 'number' && info.commits_behind > 0 ? ` (${info.commits_behind} commit${info.commits_behind === 1 ? '' : 's'} behind)` : ''}
                  </div>
                  {newEntries && (
                    <div data-type="body-s" tabIndex={0} role="group" aria-label="What the update brings"
                      className="mt-xs max-h-40 overflow-auto">
                      <Markdown>{newEntries}</Markdown>
                    </div>
                  )}
                </>
              ) : (
                // The verdict every non-available state gets, from `updateVerdict` — the same words the
                // hub tile shows. `role="status"` because Check re-renders this line in place, and a
                // result that is only drawn is not one a screen-reader user was told about.
                <>
                  <div role="status" data-type="body-m" className="flex items-center gap-1.5"
                    style={{ color: verdict === 'up_to_date' ? 'var(--color-success)' : verdict === 'checks_off' ? 'var(--color-on-surface-low)' : 'var(--color-warning)' }}>
                    {verdict === 'up_to_date' ? <CheckCircle2 size={15} aria-hidden />
                      : verdict === 'checks_off' ? <CircleOff size={15} aria-hidden />
                      : verdict === 'pin_older' ? <Undo2 size={15} aria-hidden />
                      : <AlertTriangle size={15} aria-hidden />}
                    <span className="text-on-surface">{updateVerdictLabel(info)}</span>
                  </div>
                  {verdict === 'pin_miss' && (
                    <div data-type="caption" className="text-on-surface-low">
                      Nothing is offered or installed while this pin stands. Fix it below, or clear it to follow the {channelName} channel.
                    </div>
                  )}
                  {verdict === 'pin_older' && (
                    <div data-type="caption" className="text-on-surface-low">
                      Nothing newer is offered while this pin stands. Roll back to it, or clear it to follow the {channelName} channel.
                    </div>
                  )}
                  {verdict === 'checks_off' && (
                    <div data-type="caption" className="text-on-surface-low">
                      Nothing is fetched from GitHub. Turn on Check for updates below to look for new releases.
                    </div>
                  )}
                  {verdict === 'not_checked' && (
                    <div data-type="caption" className="text-on-surface-low">
                      No release information could be fetched — press Check to try again.
                    </div>
                  )}
                </>
              )}
              <div data-type="caption" className="text-on-surface-low mt-0.5">Install type: {kindLabel}{info.current ? ` · v${info.current}` : ''}{info.pin ? ` · pinned to ${info.pin}` : ` · ${channelName} channel`}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="secondary" size="sm" loading={checking} onClick={check}><RefreshCw size={14} /> Check</Button>
              {info.available && canApplyInApp && <Button size="sm" loading={applying} onClick={apply}><DownloadCloud size={14} /> Update</Button>}
              {verdict === 'pin_older' && canApplyInApp && (
                <Button size="sm" loading={applying} onClick={applyPinnedRollback}><Undo2 size={14} /> Roll back to v{pinnedTo}</Button>
              )}
            </div>
          </div>
          {msg && <div data-type="caption" className="mt-2 text-on-surface-low">{msg}</div>}

          {/* Container: no in-place apply — show the exact pull+recreate commands for the
              channel/pin-resolved image tag. The backend emits them for that tag and
              for the way the container was started (the README's `docker run`, or Compose), so
              this renders them verbatim rather than a hard-coded `latest`. A pin that matches no
              release yields no commands, and the headline above says why — for every install
              kind. That notice used to live HERE,
              gated on `info.available`, which a pin-miss can never be (it resolves no release),
              so it was the one message about the state it described that no one could see.
              A pin set back to an older release gets its rollback commands here too, the ones
              `personalclaw update` prints: they were hidden behind `available`, which a pin back
              never is, so the panel said "Up to date" and showed nothing to run. */}
          {isContainer && (info.available || verdict === 'pin_older') && info.instructions?.length ? (
            <div className="mt-3 rounded-md bg-surface-high px-3 py-2">
              <div data-type="caption" className="text-on-surface-low mb-1">
                {verdict === 'pin_older'
                  ? 'Roll this container install back by pulling the pinned image and recreating:'
                  : 'Update this container install by pulling the new image and recreating:'}
              </div>
              <pre tabIndex={0} role="group" aria-label={verdict === 'pin_older' ? 'Rollback commands' : 'Update commands'}
                data-type="caption" className="overflow-auto leading-relaxed text-on-surface"><code>{info.instructions.join('\n')}</code></pre>
            </div>
          ) : null}
          {/* Desktop: the SHELL owns updates, so there is no in-app apply. What the shell does
              about it is a re-download today — the electron-updater half of `DC-1` is unbuilt
              (no electron-updater dependency in desktop/package.json, nothing in the shell
              checks for a release), and this note promised a self-update on next launch for the
              whole life of the shipped Linux artifact (issue 2673). Naming the release page is
              the same answer `personalclaw update` gives on this kind. */}
          {isDesktop && (info.available || verdict === 'pin_older') && (
            <div data-type="caption" className="mt-3 rounded-md bg-surface-high px-3 py-2 text-on-surface-low">
              Install the {verdict === 'pin_older' ? 'pinned' : 'new'} version from the <a className="underline" href="https://github.com/PersonalClaw/PersonalClaw/releases" target="_blank" rel="noreferrer noopener">releases page</a>, then reopen the app.
            </div>
          )}
        </div>
      </Section>

      {/* The licences, beside the version they are the licences of. Both notices are plain text at
          the dist root: the fonts' is tracked in web/public, and the packages' is written by the
          web build from its own module graph (web/scripts/thirdPartyNotices.mjs), because the
          minifier strips every licence comment from the code. */}
      <Section title="Licences" hint="PersonalClaw is MIT-licensed. The dashboard also ships third-party open-source code and fonts, each under its own licence.">
        <RowGroup>
          <Row label="Open-source packages" hint="Every package in the dashboard's code, with its version, its licence and the licence's full text.">
            <TextLink href="/THIRD_PARTY_NOTICES_NPM.txt" external size="sm" ink="emphasis" icon={ExternalLink} iconPosition="trailing"
              aria-label="Open the open-source packages' licence notices">Open</TextLink>
          </Row>
          <Row label="Fonts" hint="The dashboard's typefaces, its math fonts and the code editor's icon font.">
            <TextLink href="/THIRD_PARTY_NOTICES.txt" external size="sm" ink="emphasis" icon={ExternalLink} iconPosition="trailing"
              aria-label="Open the fonts' licence notices">Open</TextLink>
          </Row>
        </RowGroup>
      </Section>

      <Section title="Release line" hint="Which releases this install follows. A version pin overrides the channel.">
        <RowGroup>
          <Row label="Channel" hint={isGit
            ? 'Stable follows the newest normal release. Beta includes release candidates. Developer tracks every commit on your checked-out branch (contributors — needs a clean tree).'
            : 'Stable follows the newest normal release; Beta includes release candidates. The Developer channel tracks a git branch, so it needs a source checkout.'}>
            <div className="flex items-center gap-2">
              <SavedToast show={saved === 'channel'} />
              <SegPills value={channel} options={channelOptions} ariaLabel="Update channel"
                onChange={(v) => write('channel', v, `switch to the ${v} channel`)} />
            </div>
          </Row>
          {/* The example is a release that exists (`updatePinExample.test.ts` holds it to a released
              CHANGELOG heading): it read 0.2.1, which was never published, so a user who typed the
              hint's own example got a pin that stopped every update. */}
          <Field label="Version pin" hint="Stay on an exact release (e.g. 0.1.3) whatever the channel says — the update check and every apply respect it. Leave empty to follow the channel.">
            <div className="flex items-center gap-2">
              <TextInput value={pinDraft} onChange={setPinDraft} placeholder="0.1.3" ariaLabel="Version pin" size="sm" mono
                onKeyDown={(e) => { if (e.key === 'Enter') write('pin', pinDraft.trim(), 'set the version pin') }} />
              <Button variant="secondary" size="sm" onClick={() => write('pin', pinDraft.trim(), 'set the version pin')}>Save pin</Button>
              {/* A pin you cannot clear is a trap: clearing is how you go back to following the
                  channel, and typing an empty string into the box then pressing Save is not a
                  discoverable way to do it. */}
              {Boolean(info.pin) && (
                <Button variant="ghost" size="sm" onClick={() => { setPinDraft(''); write('pin', '', 'clear the version pin') }}>
                  <X size={14} /> Clear
                </Button>
              )}
              <SavedToast show={saved === 'pin'} />
            </div>
          </Field>
          {/* The rollback offer. Hidden — not disabled — when there is nothing to offer: a greyed
              "Roll back to v" naming no version explains nothing, and this install may genuinely
              never have run another version. */}
          {canRollBack && (
            <Row label="Roll back" hint={`Return to the version this install ran before v${info.current ?? ''}. Pins that release, then applies it — take a snapshot first.`}>
              <Button variant="secondary" size="sm" loading={applying} onClick={rollback}>
                <Undo2 size={14} /> Roll back to v{rollbackTo}
              </Button>
            </Row>
          )}
        </RowGroup>
      </Section>

      <Section title="Automatic updates" hint="Whether PersonalClaw looks for new releases, and whether it installs them for you.">
        <RowGroup>
          <Row label="Check for updates" hint="Ask GitHub whether a newer release exists, on a schedule. Off means ZERO outbound calls from the updater — this is the egress kill switch the README describes.">
            <div className="flex items-center gap-2">
              <SavedToast show={saved === 'check_enabled'} />
              <Toggle on={checkEnabled} onChange={(v) => write('check_enabled', v, `${v ? 'enable' : 'disable'} the update check`)} label="Check for updates" />
            </div>
          </Row>
          {/* Hidden while the check is off rather than shown disabled: the interval is ignored
              entirely then, and an inert number invites the reader to tune something that has no
              effect. `NumberField` carries no disabled state, so this is also the honest rendering. */}
          {checkEnabled && (
            <Field label="Check every" hint="Hours between release checks (1–168). The default is 12.">
              <div className="flex items-center gap-2">
                <NumberField value={info.check_interval_hours ?? 12} min={1} max={168} ariaLabel="Check every"
                  onChange={(n) => write('check_interval_hours', n, 'save the check interval')} />
                <span data-type="caption" className="text-on-surface-low">hours</span>
                <SavedToast show={saved === 'check_interval_hours'} />
              </div>
            </Field>
          )}
          <Row label="Apply updates" hint="Off only notifies you. Staged applies at the next safe point — it holds while a session or subagent is running, and only ever installs the resolved release, never raw main.">
            <div className="flex items-center gap-2">
              <SavedToast show={saved === 'auto'} />
              <SegPills value={info.auto ?? 'off'} ariaLabel="Apply updates"
                options={[{ key: 'off', label: 'Off' }, { key: 'staged', label: 'Staged' }]}
                onChange={(v) => write('auto', v, v === 'staged' ? 'enable staged automatic updates' : 'turn automatic updates off')} />
            </div>
          </Row>
        </RowGroup>
      </Section>

      {/* Release notes for the RESOLVED release, which is why this can differ from the changelog
          below: `/api/update/check` resolves the channel/pin and returns THAT release's notes, so a
          Beta install reads the release candidate's notes rather than the newest stable's. */}
      <Section title="Release notes" hint={verdict === 'pin_miss'
        ? `No published release is version ${info.pin}, so there are no notes to show.`
        : info.pin ? `What is in the pinned release ${info.pin}.` : `What is in the newest ${channelName} release.`}>
        {releaseNotes.trim()
          ? <div data-type="body-s" className="max-h-96 overflow-auto rounded-lg bg-surface-container px-4 py-3">
              <Markdown>{changelogBody(`## ${info.latest ? `v${info.latest}` : channelName}\n\n${releaseNotes}`)}</Markdown>
            </div>
          : <p data-type="body-s" className="text-on-surface-low italic">
              {/* "run a check" would send a pinned user to a button that cannot help them. */}
              {verdict === 'pin_miss' ? 'Fix or clear the version pin to read a release’s notes.'
                : checkEnabled ? 'No release notes yet — run a check.' : 'Update checks are off, so no release notes have been fetched.'}
            </p>}
      </Section>

      <Section title="Changelog" hint="What's changed recently." right={upgradeNotes}>
        {changelog.trim()
          // CHANGELOG.md is markdown — render it (headings/lists/links), not a raw <pre>.
          ? <div data-type="body-s" className="max-h-96 overflow-auto rounded-lg bg-surface-container px-4 py-3">
              <Markdown>{changelogBody(changelog)}</Markdown>
            </div>
          : <p data-type="body-s" className="text-on-surface-low italic">No changelog available.</p>}
      </Section>
    </div>
  )
}
