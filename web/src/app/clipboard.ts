import { reportingWrite } from './reportingWrite'

/** Copy text to the clipboard and SAY whether it worked. `true` only when the write landed.
 *
 * 🔴 EVERY COPY BUTTON IN THE APP BUT ONE FAILED WITHOUT SAYING SO, and two distinct mechanisms did
 * it. Censused across `web/src`: **13** `writeText` call sites, **1** correct.
 *
 * **1. The optional chain short-circuits the WHOLE chain — 11 sites.** The idiom was
 *
 *     navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {})
 *
 * and `?.` does not stop at the one property: when `navigator.clipboard` is nullish the entire chain
 * evaluates to `undefined`, so **neither** handler runs. No "Copied", no error, no throw — the button
 * does nothing at all, and there is nothing to catch because nothing was ever called.
 *
 * That is not hypothetical. `navigator.clipboard` is undefined outside a secure context, and
 * `settings/DevicesPanel` — the one site that got this right — already names the case in its own
 * words: *"a non-secure context — which a LAN `http://` dashboard is"*. Reaching this dashboard from
 * another machine on the LAN is a first-class supported setup, so the whole clipboard surface was
 * dead for exactly those users, silently.
 *
 * **2. `.catch(() => {})` swallows a real rejection — 10 of those 11.** So even ON a secure origin,
 * a refusal (no permission, document not focused) was discarded. Two independent silences in one
 * line, which is why fixing only the `?.` would not have been enough.
 *
 * 🪤 AND ONE SITE WAS WORSE THAN SILENT. `ChatPage.copyLink` awaited inside a try/catch and then set
 * `setLinkCopied(true)` **unconditionally** — so a blocked write produced a button reading "Copied"
 * over a clipboard that still held whatever was there before. A silent failure wastes a click; that
 * one sends the user off to paste something else entirely.
 *
 * 🔑 THE SENTENCE IS NOT NEW EITHER. This delegates to `reportingWrite`, whose own doc says "ONE
 * module owns the sentence in both forms, so the two cannot drift into different wording" — so a
 * failed copy now reads in the same voice as every other failed user-triggered write, and the
 * clipboard-specific part is only the advice: a copy has a manual fallback that an API write does not.
 *
 * @param what  Names the thing being copied, for the message: `copyText(url, 'the chat link')` →
 *              "Couldn't copy the chat link: …". A LIST surface must be specific, because "couldn't
 *              copy" names nothing when four rows are showing.
 */
export async function copyText(value: string, what: string): Promise<boolean> {
  return reportingWrite(`copy ${what}`, async () => {
    const clip = navigator.clipboard
    // Absence is not a rejection, so it has to be turned into one — that asymmetry is the whole
    // reason the old idiom could not report: there was no promise to attach a handler to.
    if (!clip) throw new Error('this page needs https:// or localhost to reach the clipboard. Select the text and copy it manually.')
    await clip.writeText(value)
  })
}
