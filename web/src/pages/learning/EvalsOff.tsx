import { TextLink } from '../../ui/TextLink'

/** What all four eval panels say when `evals.enabled` is off — said once, so they say it alike.
 *
 *  `/api/evals/{judge-bench,studies,retrieval,ablation}` all answer the same 404
 *  `evals_disabled` from the same `AppConfig.load().evals.enabled` check
 *  (`dashboard/handlers/evals.py:_enabled`), so this is one fact about one switch. Before this
 *  it had three renderings — a red "Couldn't load your judge benchmark" alert with a dead
 *  Retry, a red one for retrieval, and (in `StudiesPanel`) the "no study has been registered"
 *  empty state, which is a different fact entirely.
 *
 *  🔑 IT IS IN SETTINGS NOW, AND THAT IS WHY THIS SENTENCE CHANGED. The version immediately
 *  before this one sent the user to `personalclaw config set evals.enabled true`, and the comment
 *  here recorded why: `evals.enabled` was in `_EDITABLE_CONFIG` but on **no settings surface**
 *  (`git grep -in evals -- web/src/pages/settings` → 0 across 33 subpages), so the CLI was the
 *  only path that existed and the older "Turn on `evals.enabled` in Settings" link was a dead end.
 *  `#/settings/evals` now renders that switch and the four other allowlisted `evals.*` keys, so the
 *  UI can point at itself again — and the link is DEEP, to the subpage, not to the 34-card hub the
 *  dead-end version pointed at.
 *
 *  🔑 IT NAMES THE CONTROL, NOT THE CONFIG PATH. "Evals enabled" is that field's own `_meta` label
 *  in `config/loader.py`, which is the string the Settings row renders — so the words a user reads
 *  here are the words they then look for on the page. A dotted path is the right instruction for a
 *  terminal and the wrong one for a link: `evals.enabled` appears nowhere on the destination.
 *
 *  The link spans the whole instruction — control name AND destination — so its ACCESSIBLE NAME
 *  ("Evals enabled in Settings → Evaluations") carries the purpose on its own, out of context.
 *  `pages/inbox/TriageDigestCard.tsx` already ships this exact form ("Turn triage on in
 *  Settings → Inbox") for the same shape of dead switch, so this converges rather than inventing.
 *
 *  No Retry, deliberately: a switch that is off does not flip because the fetch is repeated.
 *  `LoadError`'s Retry is right for "the server didn't respond" and wrong for a decided answer.
 *  Nor is it a `role="alert"` — a decided answer is not unrequested bad news.
 *
 *  And ONE instruction, deliberately. Three things were cut after reading it on the page:
 *
 *  - The CLI command, now that a control exists. Both work (every handler re-reads the config per
 *    request, so neither needs a restart), but printing two ways to flip one switch in a state
 *    FOUR panels render at once on a fresh install is four copies of a choice nobody wants to make.
 *  - The panel's own run command. "Turning on a setting and registering a component send a user
 *    to two different places" is the rule `AblationPanel.test.tsx` already states, and each
 *    panel's `*_absent` state — the very next thing seen once the switch is on — owns its command.
 *  - "It takes effect on the next load; nothing restarts." True, but it is reassurance about a
 *    round trip the user is about to watch happen, and said four times in a column it stopped
 *    being reassurance and became a wall. It lives in this comment instead.
 *
 *  `underline`, not hue alone: the link measures 1.35:1 against the grey sentence around it, so
 *  colour is the only thing telling them apart (WCAG 1.4.1). axe's `link-in-text-block` happens to
 *  skip THIS one — its matcher ignores a link followed only by punctuation — but the perception
 *  problem is identical to the one it flags on `#/settings/evals`, and a rule's blind spot is not
 *  a licence. `JudgeBenchPanel`/`RetrievalBenchPanel` already underline their in-prose links.
 *
 *  `ink="emphasis"` because the ground here is `--color-canvas`: `#/learning` paints its column
 *  straight onto the page with no card behind it, where the base accent measures 4.37:1 and fails
 *  AA in 7 of the 12 schemes (the table in `ui/TextLink.tsx`).
 */
export function EvalsOff({ what }: {
  /** What cannot run, as the user would say it — "judge benchmark", "ablation". No article. */
  what: string
}) {
  return (
    <p className="text-on-surface-low text-[0.8125rem]">
      The eval substrate is off, so no {what} can run — turn on{' '}
      <TextLink href="#/settings/evals" ink="emphasis" className="underline">Evals enabled in Settings → Evaluations</TextLink>.
    </p>
  )
}
