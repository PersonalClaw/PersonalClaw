import { useState } from 'react'
import { Check, Sparkles, Volume2 } from 'lucide-react'
import { usePersonality } from '../../app/personality'
import { DEFAULT_PERSONALITY, type Personality } from '../../design/personalities'
import { prefersReducedMotion } from '../../design/motion'
import { setSoundCuesEnabled, soundCuesEnabled, type CuePoint } from '../../design/soundCues'
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
/** The MASTER sound-cue toggle (PERSONALITY-THEMES §S2, T2.1).
 *
 *  Default OFF, and this is the only surface that can turn it on. It lives beside
 *  the personality tiles because a cue is part of an identity, not a notification
 *  setting — and because that keeps the whole "how the app presents itself" story
 *  in one place.
 *
 *  Flipping it ON is where the AudioContext gets built: this handler runs inside the
 *  switch's own click, which is the user activation a browser demands before it will
 *  let a page make sound. Priming it anywhere else would mean the first cue of the
 *  session is silently swallowed.
 *
 *  The prose is deliberately specific about the two things a user would otherwise
 *  have to discover by being annoyed: nothing is downloaded, and a background tab
 *  stays quiet. */
/** How to name a cue point in prose. Only the three POINTS need a label — a voice is
 *  something you hear, not something to list — and this is the user-facing wording,
 *  which is why it lives with the copy rather than in `soundCues.ts`. */
const CUE_POINT_LABELS: Record<CuePoint, string> = {
  turn_complete: 'a finished turn',
  approval_needed: 'an approval',
  error: 'a failure',
}

function SoundCuesToggle() {
  const { personality } = usePersonality()
  const [on, setOn] = useState(soundCuesEnabled)
  const flip = (v: boolean) => {
    setSoundCuesEnabled(v)
    setOn(v)
  }
  // Which moments the active identity re-voices. Said out loud because otherwise
  // the only way to discover that this identity sounds different is to hear it and
  // wonder whether something is broken.
  const voiced = (Object.keys(personality.behavior.soundCues ?? {}) as CuePoint[])
    .map((point) => CUE_POINT_LABELS[point])
    .filter(Boolean)
  return (
    <div className="mt-l flex items-start gap-m rounded-lg bg-surface-container px-m py-3">
      <div className="mt-0.5 shrink-0">
        <Toggle on={on} onChange={flip} size="sm" label="Sound cues" />
      </div>
      <div className="min-w-0">
        <p data-type="body-s" className="flex items-center gap-1.5 text-on-surface">
          <Volume2 size={13} aria-hidden className="shrink-0 text-on-surface-low" />
          Sound cues
        </p>
        <p data-type="body-s" className="mt-0.5 text-on-surface-low">
          A brief tone when a turn finishes, when an approval needs you, and when something
          fails. Off by default. The tones are generated in the browser, so nothing is
          downloaded — and a cue never plays while this tab is in the background.
        </p>
        {voiced.length > 0 && (
          <p data-type="body-s" className="mt-1 text-on-surface-low">
            {personality.label} has its own tone for {voiced.join(' and ')}.
          </p>
        )}
        {on && prefersReducedMotion() && (
          <p data-type="body-s" className="mt-1 text-on-surface-low">
            Silent right now: your system asks for reduced motion, which turns cues off too.
          </p>
        )}
      </div>
    </div>
  )
}

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
        wordmark, the tab title, the interface density, and the motion and backdrop dials
        together — and can offer the assistant a matching name. Every one of those stays
        yours to adjust afterwards, and picking one never changes your saved configuration
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

      <SoundCuesToggle />
      {pending && (
        <Modal title={`Switch to ${pending.label}?`} onClose={() => setPending(null)}>
          <div className="flex flex-col gap-m">
            <p className="text-on-surface text-[0.875rem] leading-relaxed">
              The colors, wordmark, tab title, density, and motion dials change right away —
              pick another personality any time to change them back.
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
