import { useRef, useState } from 'react'
import { Link2, MonitorPlay } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { TextInput } from '../../../ui/forms'
import { api, type WatchedSource } from '../../../lib/api'
import type { Rebase } from '../../../lib/staleWrite'
import { HELD_CHANGE_REASON, sameDocument } from '../../../lib/staleWrite'
import { useStaleWriteGuard } from '../../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../../ui/StaleWriteNotice'
import { notify } from '../../../app/appSdk'

// ── What to do about a feed's last check ──────────────────────────────────────────────────────────
//
// A feed's set-up row draws this under itself when its last check found nothing it could use.

/** The two objects a remediation writes back WHOLE — the document the source's `revision` covers. */
type SourceSettings = { spec: Record<string, unknown>; budget: Record<string, unknown> }

/** The remediation strip. Its whole reason for existing is that the two failures the backend
 *  discriminates have OPPOSITE fixes — a page that rendered plenty of text and found nothing
 *  is the wrong URL; a page that is a JavaScript shell needs the render tier. Both the
 *  message and the action come from the backend's `remediation` verdict, so this component
 *  never decides which advice applies; collapsing them into one "nothing found" strip would
 *  send half the users the wrong way. */
export function Remediation({ source, onChanged }: { source: WatchedSource; onChanged: () => void }) {
  const rem = source.remediation
  const [url, setUrl] = useState(String(source.spec?.url ?? ''))
  const [busy, setBusy] = useState(false)
  // The success line for the save in flight. A ref, because a save re-applied from the notice
  // lands after this click's handler has returned, and it still owes the user that line.
  const done = useRef('')
  // 🔴 A FIX IS SAVED AS BOTH OBJECTS, built from this row's copy of them: `{...source.budget,
  // allow_render}` and `{...source.spec, url}`. A tab that had the page open before another tab —
  // or the agent — changed this source's spec or budget used to write its own copy back over that
  // change without a word. The save now names the revision its copy was read at, a stale one is
  // refused, and the fix — an operation on the objects, never the objects themselves — is
  // re-applied on top of what is stored (`ui/StaleWriteNotice`).
  const guard = useStaleWriteGuard<SourceSettings>({
    read: () => api.knowledgeSources().then((r) => {
      const now = r.sources.find((s) => s.id === source.id)
      if (!now) throw new Error('the source no longer exists')
      return { value: { spec: now.spec, budget: now.budget }, revision: now.revision }
    }),
    write: (next, base) => api.saveKnowledgeSourceSettings(source.id, next, base),
    onSaved: () => { notify(done.current, 'success'); onChanged() },
    onDiscard: onChanged,
  })
  if (!rem?.kind) return null

  const isRender = rem.kind === 'render_tier'
  // A pending refusal holds the user's fix; a second one would replace it before they chose.
  const pending = guard.conflict !== null

  /** One setting of the source's spec or budget, as an edit re-applicable onto what is stored:
   *  refused (`null`) when the setting itself was changed elsewhere to something else since this row
   *  read it — choosing between the two is the user's call, made in the review, not the reapply's. */
  function setting<K extends keyof SourceSettings>(part: K, key: string, value: unknown): Rebase<SourceSettings> {
    const read = (source[part] as Record<string, unknown> | undefined)?.[key]
    return (t) => {
      const now = (t[part] as Record<string, unknown> | undefined)?.[key]
      if (!sameDocument(now, read) && !sameDocument(now, value)) return null
      return { ...t, [part]: { ...(t[part] as Record<string, unknown> | undefined), [key]: value } }
    }
  }

  async function apply(fix: Rebase<SourceSettings>, message: string) {
    done.current = message
    setBusy(true)
    try {
      await guard.apply({ value: { spec: source.spec || {}, budget: source.budget || {} }, revision: source.revision }, fix)
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
          <p data-type="body-s" className="text-on-surface leading-relaxed">{rem.guidance}</p>
          {rem.detail && (
            <p data-type="caption" className="mt-1 text-on-surface-low">{rem.detail}</p>
          )}

          {rem.action === 'allow_render' && (
            <Button size="xs" variant="tonal" className="mt-m" loading={busy}
              disabled={pending} disabledReason={HELD_CHANGE_REASON}
              onClick={() => apply(setting('budget', 'allow_render', true), `${source.name} may now use the render tier`)}>
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
                disabled={pending || !/^https?:\/\//.test(url.trim()) || url.trim() === String(source.spec?.url ?? '')}
                disabledReason={pending ? HELD_CHANGE_REASON
                  : !/^https?:\/\//.test(url.trim()) ? 'Enter a URL starting with http:// or https://' : 'That is already this source’s URL'}
                onClick={() => apply(setting('spec', 'url', url.trim()), `${source.name} now watches ${url.trim()}`)}>
                Point it here
              </Button>
            </div>
          )}
          <StaleWriteNotice guard={guard} what="This source’s settings" className="mt-m" />
        </div>
      </div>
    </div>
  )
}
