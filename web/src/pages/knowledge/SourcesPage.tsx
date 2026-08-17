import { useState } from 'react'
import { ArrowLeft, Plus, Rss, ShieldOff, Link2, MonitorPlay, AlertTriangle, Zap, type LucideIcon } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { IconButton } from '../../ui/IconButton'
import { PageTitle } from '../../ui/PageTitle'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { TextInput } from '../../ui/forms'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type WatchedSource } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { notify } from '../../app/appSdk'
import { relFuture, relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../design/fontWeight'
import { RAW_ENRICHMENT, TONE_CLASS, eventDrivenMetaLine, fmtInterval, formIcon, healthMeta } from './sourceMeta'

/** The kinds catalog keyed by provider, so a row can name its own kind and pick its icon
 *  from the same `form` discriminator the create page switches on. */
type KindIndex = Record<string, { display_name: string; form: string }>

/** A compact status/attribute chip. A `<span>` on purpose — these are readouts, not
 *  controls, and a chip that looked pressable inside a row with real buttons beside it
 *  would be two interaction affordances competing for the same glance. */
function Chip({ label, tone, icon: Icon, title }: {
  label: string
  tone?: 'ok' | 'warn' | 'danger' | 'neutral'
  icon?: LucideIcon
  title?: string
}) {
  const ink = tone && tone !== 'neutral' ? TONE_CLASS[tone] : 'text-on-surface-var'
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] ${ink}`} style={fvs(500)}>
      {Icon && <Icon size={11} aria-hidden />}
      {label}
    </span>
  )
}

/** The remediation strip. Its whole reason for existing is that the two failures WS-3
 *  discriminates have OPPOSITE fixes — a page that rendered plenty of text and found nothing
 *  is the wrong URL; a page that is a JavaScript shell needs the render tier. Both the
 *  message and the action come from the backend's `remediation` verdict, so this component
 *  never decides which advice applies; collapsing them into one "nothing found" strip would
 *  send half the users the wrong way. */
function Remediation({ source, onChanged }: { source: WatchedSource; onChanged: () => void }) {
  const rem = source.remediation
  const [url, setUrl] = useState(String(source.spec?.url ?? ''))
  const [busy, setBusy] = useState(false)
  if (!rem?.kind) return null

  const isRender = rem.kind === 'render_tier'

  async function apply(body: Parameters<typeof api.updateKnowledgeSource>[1], done: string) {
    setBusy(true)
    try {
      await api.updateKnowledgeSource(source.id, body)
      notify(done, 'success')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That change did not save', 'error')
    } finally { setBusy(false) }
  }

  return (
    <div className="mt-m rounded-lg bg-surface-high/60 p-m">
      <div className="flex items-start gap-2">
        {isRender ? <MonitorPlay size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          : <Link2 size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
        <div className="min-w-0 flex-1">
          {/* The provider's own words. Rendered as text (never markup) — the string is a
              constant from `web_source.py`, and the UI holds no copy of it to drift. */}
          <p className="text-on-surface text-[0.8125rem] leading-relaxed">{rem.guidance}</p>
          {rem.detail && (
            <p className="mt-1 text-on-surface-low text-[0.75rem]">{rem.detail}</p>
          )}

          {rem.action === 'allow_render' && (
            <Button size="xs" variant="tonal" className="mt-m" loading={busy}
              onClick={() => apply({ budget: { ...(source.budget || {}), allow_render: true } }, `${source.name} may now use the render tier`)}>
              Allow the render tier
            </Button>
          )}

          {rem.action === 'edit_url' && (
            <div className="mt-m flex flex-wrap items-center gap-s">
              <span className="min-w-0 flex-1 basis-56">
                <TextInput value={url} onChange={setUrl} size="sm" placeholder="https://…/changelog"
                  ariaLabel={`Listing-page URL for ${source.name}`} />
              </span>
              <Button size="xs" variant="tonal" loading={busy}
                disabled={!/^https?:\/\//.test(url.trim()) || url.trim() === String(source.spec?.url ?? '')}
                disabledReason={!/^https?:\/\//.test(url.trim()) ? 'Enter a URL starting with http:// or https://' : 'That is already this source’s URL'}
                onClick={() => apply({ spec: { ...(source.spec || {}), url: url.trim() } }, `${source.name} now watches ${url.trim()}`)}>
                Point it here
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** One watched source. NOT a clickable row: it carries real controls (a switch, a
 *  remediation button, a URL field), and wrapping those in a button would nest interactive
 *  elements — the row would swallow their names and keyboard activation. */
export function SourceRow({ source, index, kinds, onChanged }: {
  source: WatchedSource
  index?: number
  kinds: KindIndex
  onChanged: () => void
}) {
  const kind = kinds[source.provider]
  const Icon = formIcon(kind?.form ?? '')
  const health = healthMeta(source.health_status)
  const [busy, setBusy] = useState(false)

  async function setEnabled(on: boolean) {
    setBusy(true)
    try {
      await api.updateKnowledgeSource(source.id, { enabled: on })
      notify(`${source.name} ${on ? 'resumed' : 'paused'}`, 'success')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That change did not save', 'error')
    } finally { setBusy(false) }
  }

  return (
    <ListRow index={index}>
      <div className="flex min-w-0 items-start gap-m">
        <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-high">
          <Icon size={16} className="text-on-surface-var" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{source.name}</span>
            {/* `sources.health_status` DEFAULTS to `ok` in the store, so a source that has
                never been polled reports a health it has not earned. Measured by driving the
                real thing: a source saved seconds ago read "Healthy · never polled" side by
                side. The rollup only means something once a poll has written it. */}
            {source.event_driven ? null : source.last_poll_at
              ? <Chip label={health.label} tone={health.tone} title={health.hint} />
              : <Chip label="Not polled yet" title="No poll has run, so there is no health to report yet." />}
            {/* §6.3's promise, read off the source's real enrichment field. `raw` means the
                ingest graph for these items has NO LLM nodes — absent, not skipped — so this
                is a readout of a structural guarantee, not a UI preference. */}
            {source.enrichment === RAW_ENRICHMENT && (
              <Chip label="no AI" icon={ShieldOff}
                title="Raw source: items are indexed and embedded locally, and never reach a model." />
            )}
            {!source.enabled && !source.event_driven && <Chip label="Paused" title="This source is not being polled." />}
            {/* An event-driven source is not a poller, so every poll-shaped verdict below is
                about a mechanism it does not use. Suppressing them is not cosmetic: the
                "No provider" chip is a DANGER chip saying a working mechanism is broken, and
                "never polled" is true of something that will never be polled by design. */}
            {source.event_driven ? (
              <Chip label="Live" icon={Zap} title="Indexed as artifacts change — this source is not polled." />
            ) : !source.enrolled && (
              <Chip label="No provider" tone="danger" icon={AlertTriangle}
                title={`Nothing is registered to poll a ${source.provider} source, so this row will never collect anything.`} />
            )}
          </div>
          {/* Wraps rather than truncating: at 390px this line is longer than the row, and the
              facts at its END — how many arrived, when the next check runs — are the ones a
              truncation eats. Same call the item metadata row made on a phone. */}
          {/* Two whole lines rather than one line with five interleaved ternaries: an
              event-driven source shares only the kind's name with a poller, so threading a
              boolean through cadence/last-poll/count/next-poll is how a ` · ` ends up
              separating nothing. The poller line below is unchanged. */}
          {source.event_driven ? (
            <p className="mt-1 text-on-surface-low text-[0.75rem]">{eventDrivenMetaLine()}</p>
          ) : (
          <p className="mt-1 text-on-surface-low text-[0.75rem]">
            {kind?.display_name ?? source.provider}
            {' · every '}{fmtInterval(source.poll_interval_secs)}
            {' · '}{source.last_poll_at ? `polled ${relPast(source.last_poll_at)}` : 'never polled'}
            {source.last_poll_at ? ` · ${source.last_new_count ?? 0} new last time` : ''}
            {/* The health rollup describes the LAST poll and does not move when you fix a
                source — inventing a fresh verdict on an edit would be worse. So the next
                check is stated instead, which is what tells you the fix will be tested. */}
            {source.enabled && source.next_poll_at ? ` · next ${relFuture(source.next_poll_at)}` : ''}
          </p>
          )}
          {!!source.last_escalations?.length && (
            // The expensive tier, made visible. WS-3 records escalations on success too,
            // because an escalation nobody can see is indistinguishable from a cheap poll.
            <p className="mt-1 text-on-surface-low text-[0.75rem]">{source.last_escalations.join(' · ')}</p>
          )}
          <Remediation source={source} onChanged={onChanged} />
        </div>
        {/* No `disabledReason`: the only reason this is ever unavailable is an in-flight
            save, which is transient — the native attribute is right there, and a reason
            would keep a control focusable to explain something that resolves itself.
            ABSENT for an event-driven source, deliberately: pausing that row's `enabled`
            column would change nothing (the mirror reads its config field, not this flag),
            and a switch that saves successfully while doing nothing is worse than no switch.
            Its one real control is `knowledge.auto_ingest_artifacts`, which the meta line
            above names. */}
        {!source.event_driven && (
          <Toggle on={source.enabled} size="sm" disabled={busy}
            label={`${source.enabled ? 'Pause' : 'Resume'} ${source.name}`}
            onChange={setEnabled} />
        )}
      </div>
    </ListRow>
  )
}

/** The Sources destination inside the Knowledge section (`#/knowledge/sources`).
 *
 *  Everything WS-2..WS-5 built was unreachable before this page: the store, the poll engine
 *  and three providers all worked, and `create_source` had no caller. So this page is
 *  deliberately the whole loop — see what you watch, how healthy it is, what the last poll
 *  cost, what to do about a failing one, and add another. */
export function SourcesPage({ onBack, onCreate }: { onBack: () => void; onCreate: () => void }) {
  const { data, loading, error, refresh } = useCachedData('knowledge:sources', () => api.knowledgeSources())
  const reload = () => { invalidateCache('knowledge:sources'); refresh() }

  const kinds: KindIndex = Object.fromEntries(
    (data?.kinds ?? []).map((k) => [k.provider, { display_name: k.display_name, form: k.form }]),
  )
  const sources = data?.sources

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to knowledge" size={40} onClick={onBack} /><PageTitle>Watched sources</PageTitle></div>}
        // The ONE responsive header cluster, not a bare Button. Measured at 390px with a
        // plain pill: it laid out 44→206px and painted straight over BOTH the back button
        // (44→84) and the page title, which was squeezed to 8px of width. Through the cluster
        // the control sheds label → icon and the title reclaims the space.
        right={
          <HeaderActions>
            <HeaderControl icon={Plus} label="Watch something" variant="primary" priority="primary"
              hint="Add a page, feed or folder to watch" onClick={onCreate} />
          </HeaderActions>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {/* A failed fetch and an empty list are different facts, and telling a user they
              watch nothing when the truth is "we could not load it" is the worse of the two. */}
          {sources === undefined && error ? (
            <LoadError what="watched sources" error={error} onRetry={reload} />
          ) : sources === undefined || loading ? (
            <ListSkeleton rows={3} what="watched sources" />
          ) : sources.length === 0 ? (
            <EmptyState icon={Rss} title="Nothing watched yet"
              hint="Point PersonalClaw at a changelog, a feed or a folder and new entries land in your library on their own."
              action={{ label: 'Watch something', onClick: onCreate, icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-m">
              {sources.map((s, i) => (
                <SourceRow key={s.id} source={s} index={i} kinds={kinds} onChanged={reload} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
