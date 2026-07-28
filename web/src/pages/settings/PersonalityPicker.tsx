import { useState } from 'react'
import { Check, Sparkles } from 'lucide-react'
import { usePersonality } from '../../app/personality'
import { DEFAULT_PERSONALITY, type Personality } from '../../design/personalities'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { TileButton } from '../../ui/TileButton'
import { notify } from '../../app/appSdk'
import { api } from '../../lib/api'

/** The personality picker (PERSONALITY-THEMES §S1).
 *
 *  A personality bundles colors + chrome + (optionally) the assistant's name into
 *  one identity. The colors and chrome apply immediately — they're local
 *  preferences, instantly reversible by picking another entry.
 *
 *  The NAME is different, and that difference is the whole design of this dialog.
 *  `agent.bot_name` is server config the user may have set deliberately, so
 *  activating a personality **offers** the rename with a toggle rather than
 *  performing it. Propose-don't-write: nothing about your saved configuration
 *  changes unless you turn that toggle on. Switching back offers the reverse. */
export function PersonalityPicker() {
  const { personality, all, activate } = usePersonality()
  const [pending, setPending] = useState<Personality | null>(null)
  const [alsoRename, setAlsoRename] = useState(true)
  const [busy, setBusy] = useState(false)

  const pick = (next: Personality) => {
    if (next.id === personality.id) return
    // Only stop for a dialog when there's a rename to consent to; a
    // colors-and-chrome-only switch is instantly reversible, so a confirm would
    // just be a speed bump.
    if (next.behavior.displayName || personality.behavior.displayName) {
      setAlsoRename(true)
      setPending(next)
      return
    }
    activate(next.id)
  }

  const confirm = async () => {
    if (!pending) return
    setBusy(true)
    try {
      activate(pending.id)
      if (alsoRename) {
        // Returning to the default identity clears the name rather than setting
        // a placeholder — the server treats "" as "use the product default".
        const name =
          pending.id === DEFAULT_PERSONALITY ? '' : (pending.behavior.displayName ?? '')
        await api.patchConfig('agent.bot_name', name)
      }
      setPending(null)
    } catch (e) {
      notify(
        `Switched the look, but couldn't rename the assistant: ${String((e as Error)?.message || e)}`,
        'error',
      )
      setPending(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Sparkles size={14} className="text-on-surface-low" />
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">
          Personality
        </span>
      </div>
      <p className="mb-m text-on-surface-low text-[0.8125rem] leading-relaxed">
        A personality is a whole identity, not just a palette: it sets the colors, the
        wordmark, the tab title, and the interface density together — and can offer the
        assistant a matching name. Picking one never changes your saved configuration
        without asking.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-m">
        {all.map((p) => {
          const active = p.id === personality.id
          return (
            <TileButton key={p.id} onClick={() => pick(p)} active={active} title={p.hint}
              className="p-m">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-on-surface text-[0.875rem]">{p.label}</span>
                {active && <Check size={13} className="shrink-0 text-primary" />}
              </div>
              <p className="mt-0.5 line-clamp-2 text-left text-on-surface-low text-[0.75rem] leading-snug">
                {p.hint}
              </p>
              {p.behavior.displayName && (
                <p className="mt-1 text-left text-on-surface-low text-[0.6875rem]">
                  calls itself “{p.behavior.displayName}”
                </p>
              )}
            </TileButton>
          )
        })}
      </div>

      {pending && (
        <Modal title={`Switch to ${pending.label}?`} onClose={() => setPending(null)}>
          <div className="flex flex-col gap-m">
            <p className="text-on-surface text-[0.875rem] leading-relaxed">
              The colors, wordmark, tab title, and density change right away — pick another
              personality any time to change them back.
            </p>
            <div className="flex items-start gap-m rounded-lg bg-surface-container px-m py-3">
              {/* Toggle is the kit's binary control. The design system has no
                  checkbox primitive, and hand-rolling one here would be bespoke
                  chrome the adoption ratchet rightly rejects. */}
              <div className="mt-0.5 shrink-0">
                <Toggle on={alsoRename} onChange={setAlsoRename} size="sm"
                  label={pending.id === DEFAULT_PERSONALITY
                    ? "Also restore the assistant's default name"
                    : `Also rename the assistant to ${pending.behavior.displayName}`} />
              </div>
              <span className="text-on-surface text-[0.8125rem] leading-relaxed">
                {pending.id === DEFAULT_PERSONALITY ? (
                  <>
                    Also restore the assistant’s default name.{' '}
                    <span className="text-on-surface-low">
                      This clears <code className="font-mono">agent.bot_name</code>, so it goes
                      back to PersonalClaw.
                    </span>
                  </>
                ) : (
                  <>
                    Also rename the assistant to{' '}
                    <strong>{pending.behavior.displayName}</strong>.{' '}
                    <span className="text-on-surface-low">
                      This writes <code className="font-mono">agent.bot_name</code>, which the
                      assistant uses to refer to itself. Turn it off to keep the name
                      you have.
                    </span>
                  </>
                )}
              </span>
            </div>
            <div className="flex justify-end gap-s">
              <Button variant="ghost" size="sm" onClick={() => setPending(null)}>Cancel</Button>
              <Button variant="primary" size="sm" disabled={busy} onClick={confirm}>
                {busy ? 'Switching…' : `Switch to ${pending.label}`}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
