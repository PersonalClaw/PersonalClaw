import { useCallback, useEffect, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Palette } from 'lucide-react'
import { Segmented } from '../../ui/Segmented'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { api } from '../../lib/api'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { reportingWrite } from '../../app/reportingWrite'
import {
  TokensView, ContrastView, overridesOf, withTokenOverride, TOKEN_EDIT_HELD,
  type ResolvedTokens, type Scheme, type TokenOverrides,
} from './DesignCockpitPage'

/** In the design planning walkthrough, a token-bearing step (foundations / palette
 *  / typography) renders the EXTRACTED token values as the editable whole-system design
 *  preview: the step's `token_overrides` patch is merged onto the loop, then the loop's
 *  RESOLVED tokens (defaults + all approved overrides + this step's) render as TokensView
 *  — the user sees the NET EFFECT of the whole system at once and can edit any token
 *  (writes back to the loop's token_overrides) before approving the step. Reuses the
 *  cockpit's TokensView/ContrastView so planning + cockpit show the identical system. */
export function DesignStepPreview({ loopId, stepKind, overrides }: {
  loopId: string
  stepKind: string
  overrides: Record<string, unknown>
}) {
  const [scheme, setScheme] = useState<Scheme>('light')
  const [tokens, setTokens] = useState<ResolvedTokens | null>(null)
  const [tab, setTab] = useState<'tokens' | 'contrast'>('tokens')
  const [err, setErr] = useState(false)
  // Merge this step's extracted overrides onto the loop ONCE (not on every scheme
  // toggle / edit re-render) — the step artifact is the source for its own patch, but
  // user edits (setOverride below) then own the loop's token_overrides going forward.
  const merged = useRef(false)

  const loadTokens = useCallback(async () => {
    try { const t = await api.uLoopDesignTokens(loopId, scheme); setTokens(t as ResolvedTokens); setErr(false) }
    catch { setErr(true) }
  }, [loopId, scheme])

  useEffect(() => {
    let alive = true
    const run = async () => {
      if (!merged.current && overrides && Object.keys(overrides).length) {
        merged.current = true
        // Deep-merge the step's extracted overrides into the loop's token_overrides so
        // the resolved preview reflects them. update_spec accepts kind_config edits
        // while the loop is pre-launch (planning/review). Written over the read just made
        // (its revision), and as `token_overrides` alone: the server merges it into the
        // stored `kind_config`, where echoing the read's config back would write its
        // REDACTED view over real values.
        try {
          const base = overridesOf(await api.uLoop(loopId))
          const next = deepMerge(base.value, overrides)
          await api.saveULoopSpec(loopId, { kind_config: { token_overrides: next } }, base.revision)
        } catch { /* best-effort; preview still loads from whatever's there */ }
      }
      if (alive) await loadTokens()
    }
    run()
    return () => { alive = false }
  }, [loopId, overrides, loadTokens])

  // A user token edit, as the cockpit makes it: an operation on the overrides (`withTokenOverride`),
  // built on a read made just now and naming its revision. A write that lands in between refuses it,
  // and the notice re-applies the same edit onto what is stored. The refetch runs in `onSaved` — only a
  // write that landed reaches it, the first try or one re-applied from the notice.
  const guard = useStaleWriteGuard<TokenOverrides>({
    read: async () => overridesOf(await api.uLoop(loopId)),
    write: (next, revision) => api.saveULoopSpec(loopId, { kind_config: { token_overrides: next } }, revision),
    onSaved: () => { void loadTokens() },
    onDiscard: () => { void loadTokens() },
  })
  const held = guard.conflict !== null

  // Edit any token: deep-set the path into the loop's token_overrides (empty = reset),
  // then reload the resolved preview. The same edit as the cockpit's setTokenOverride.
  const setOverride = useCallback(async (path: string, value: string) => {
    // 🔑 A USER EDIT, not the mount-time merge above. This is wired to `TokensView`'s `onOverride`, so
    // somebody typed a value; the old `catch { /* keep the current preview */ }` then left the OLD
    // value on screen with no sentence, so the edit silently did not stick. Same shape as the three
    // `/* leave dirty */` editors #2237 converted. The auto-merge in the effect above stays silent
    // deliberately — nobody asked for that one.
    await reportingWrite(`save the ${path} override`, async () => {
      await guard.apply(overridesOf(await api.uLoop(loopId)), (ov) => withTokenOverride(ov, path, value))
    })
  }, [loopId, guard.apply])

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-low/40 p-2.5">
      <div className="mb-2 flex items-center gap-2">
        <Palette size={13} className="text-primary" />
        <span data-type="caption" className="text-on-surface" style={fvs(600)}>
          Live design system — net effect of every choice so far
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Segmented ariaLabel="View" value={tab} onChange={(v) => setTab(v as 'tokens' | 'contrast')}
            options={[{ key: 'tokens', label: 'Tokens' }, { key: 'contrast', label: 'Contrast' }]} />
          <Segmented ariaLabel="Scheme" value={scheme} onChange={(v) => setScheme(v as Scheme)}
            options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }]} />
        </div>
      </div>
      <p data-type="caption" className="mb-2 text-on-surface-low">
        {stepKind === 'palette' ? 'The extracted palette is applied below.' : stepKind === 'typography' ? 'The extracted type + spacing are applied below.' : 'The extracted foundation tokens are applied below.'} Click a swatch / value to edit it — changes apply to the whole system instantly and carry into the cockpit.
      </p>
      <StaleWriteNotice guard={guard} what="This design system" className="mb-s" />
      {err
        ? <div data-type="caption" className="text-on-surface-low">Couldn't load the live preview.</div>
        : tab === 'tokens'
          ? <TokensView tokens={tokens} scheme={scheme} onOverride={setOverride} locked={held ? TOKEN_EDIT_HELD : undefined} />
          : <ContrastView tokens={tokens} scheme={scheme} />}
    </div>
  )
}

/** Deep-merge `patch` onto `base` (objects recurse; scalars/arrays from patch win). */
function deepMerge(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = JSON.parse(JSON.stringify(base || {}))
  for (const [k, v] of Object.entries(patch || {})) {
    if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
      out[k] = deepMerge(out[k] as Record<string, unknown>, v as Record<string, unknown>)
    } else {
      out[k] = v
    }
  }
  return out
}
