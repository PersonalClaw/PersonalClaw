import { useEffect, useState } from 'react'
import { Palette, Contrast } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Segmented } from '../../ui/Segmented'
import { api } from '../../lib/api'
import { TokensView, ContrastView, type ResolvedTokens, type Scheme } from './DesignCockpitPage'

/** Read-only preview of PersonalClaw's canonical DEFAULT design system — the
 *  "most comprehensive design system possible" the Design loop builds on. Opened
 *  from the Design composer (before any loop exists), so the user can explore the
 *  defaults — every token axis + WCAG contrast for the key role pairings — and
 *  decide what to override, without launching a loop. Reuses the cockpit's token +
 *  contrast views in readOnly mode (no override/refresh affordances). Wires the
 *  previously-unused `designDefaultTokens` client + `/api/design/tokens/default`. */
export function DesignSystemPreview({ onClose }: { onClose: () => void }) {
  const [scheme, setScheme] = useState<Scheme>('light')
  const [tokens, setTokens] = useState<ResolvedTokens | null>(null)
  const [tab, setTab] = useState<'tokens' | 'contrast'>('tokens')
  const [err, setErr] = useState(false)

  useEffect(() => {
    let alive = true
    api.designDefaultTokens(scheme)
      .then((t) => { if (alive) { setTokens(t as ResolvedTokens); setErr(false) } })
      .catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [scheme])

  return (
    <Modal title="PersonalClaw design system" icon={<Palette size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l p-l">
        <p className="text-on-surface-low text-[0.8125rem] max-w-[52rem]">
          The canonical default tokens every Design loop starts from — variables for every look-and-feel
          axis. Launch a Design loop to override any of these and generate React components from the system.
        </p>
        <div className="flex items-center gap-2">
          <Segmented ariaLabel="View" value={tab} onChange={(v) => setTab(v as 'tokens' | 'contrast')}
            options={[{ key: 'tokens', label: 'Tokens' }, { key: 'contrast', label: 'Contrast' }]} />
          <div className="ml-auto">
            <Segmented ariaLabel="Scheme" value={scheme} onChange={(v) => setScheme(v as Scheme)}
              options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }]} />
          </div>
        </div>
        {err
          ? <div className="text-on-surface-low text-sm">Could not load the default token set.</div>
          : tab === 'tokens'
            ? <TokensView tokens={tokens} scheme={scheme} readOnly />
            : <div className="flex flex-col gap-2"><div className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide"><Contrast size={12} /> WCAG contrast</div><ContrastView tokens={tokens} scheme={scheme} /></div>}
      </div>
    </Modal>
  )
}
