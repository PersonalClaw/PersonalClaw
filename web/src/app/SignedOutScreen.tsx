import { Fragment, useEffect, useRef } from 'react'
import { LogOut } from 'lucide-react'
import { Button } from '../ui/Button'
import type { SignedOut } from '../lib/signedOut'

/** The gateway's sentence, with its `backticked` commands shown as code — the same rendering the
 *  gateway's own sign-in pages give the same sentence (`token_auth.notice_html`).
 *
 *  The prose stays bare text nodes rather than spans: an accessible-description computation
 *  trims each element child, so "run <span>…</span><code>" is announced "runpersonalclaw token". */
function Sentence({ text }: { text: string }) {
  return (
    <>
      {text.split('`').map((part, i) => (i % 2
        ? <code key={i} className="rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface">{part}</code>
        : <Fragment key={i}>{part}</Fragment>))}
    </>
  )
}

/** What this tab shows once its session has ended — instead of the app.
 *
 *  It replaces the whole shell rather than sitting over it: every panel, poll and socket of a
 *  signed-out tab can only be refused, and a half-live app behind a notice reads as a broken one.
 *  The sentence is the gateway's (why it ended, when, and how to sign back in); "Sign in again"
 *  reloads, which lands on the gateway's sign-in page or paste-token gate — the one door, carrying
 *  the same sentence, so there is no second sign-in form here to drift from it. */
export function SignedOutScreen({ notice }: { notice: SignedOut }) {
  const heading = useRef<HTMLHeadingElement | null>(null)
  // The page the user was on is gone; put them on the thing that replaced it.
  useEffect(() => { heading.current?.focus() }, [])
  return (
    <main aria-labelledby="signed-out-heading" aria-describedby="signed-out-why"
      className="grid h-full place-items-center overflow-y-auto px-l" style={{ background: 'var(--color-canvas)' }}>
      <div className="flex max-w-[480px] flex-col items-center gap-l py-2xl text-center">
        <LogOut size={32} className="text-on-surface-low" aria-hidden />
        <div>
          {/* Programmatic focus target only (tabIndex -1), so it is announced on arrival without
              becoming a tab stop. No live region: one mounted together with its text is not
              reliably observed, and focusing the heading is what announces the screen. The global
              focus ring stays on, as on onboarding's step headings: a keyboard user moved here
              sees where they were moved. */}
          <h1 id="signed-out-heading" ref={heading} tabIndex={-1} data-type="headline-s"
            className="text-on-surface">
            You’re signed out
          </h1>
          <p id="signed-out-why" data-type="body-m" className="mt-s text-on-surface-low">
            <Sentence text={notice.message} />
          </p>
        </div>
        <Button size="sm" onClick={() => window.location.reload()}>Sign in again</Button>
      </div>
    </main>
  )
}
