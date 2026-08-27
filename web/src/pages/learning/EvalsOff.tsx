/** What all four eval panels say when `evals.enabled` is off — said once, so they say it alike.
 *
 *  `/api/evals/{judge-bench,studies,retrieval,ablation}` all answer the same 404
 *  `evals_disabled` from the same `AppConfig.load().evals.enabled` check
 *  (`dashboard/handlers/evals.py:_enabled`), so this is one fact about one switch. Before this
 *  it had three renderings — a red "Couldn't load your judge benchmark" alert with a dead
 *  Retry, a red one for retrieval, and (in `StudiesPanel`) the "no study has been registered"
 *  empty state, which is a different fact entirely.
 *
 *  🔑 IT IS NOT IN SETTINGS. The one existing copy for this state read "Turn on `evals.enabled`
 *  in Settings" and linked `#/settings`. Measured: `evals.enabled` appears in no
 *  `_EDITABLE_CONFIG` entry and on no settings surface, so that link led to a page with no such
 *  control — a dead end the branch's inertness had kept invisible. `personalclaw config set
 *  evals.enabled true` is the path that exists, and it takes effect without a restart (every
 *  handler re-reads the config per request).
 *
 *  No Retry, deliberately: a switch that is off does not flip because the fetch is repeated.
 *  `LoadError`'s Retry is right for "the server didn't respond" and wrong for a decided answer.
 *
 *  And ONE sentence, deliberately. Two things were cut after reading it on the page:
 *
 *  - The panel's own run command. "Turning on a setting and registering a component send a user
 *    to two different places" is the rule `AblationPanel.test.tsx` already states, and each
 *    panel's `*_absent` state — the very next thing seen once the switch is on — owns its command.
 *  - "It takes effect on the next load; nothing restarts." True (every handler re-reads the
 *    config per request) but it is reassurance about a CLI command, and `evals.enabled` defaults
 *    to false, so all FOUR panels render this at once on a fresh install. Said four times in a
 *    column it stopped being reassurance and became a wall. It lives in this comment instead.
 *
 *  No Retry either: a switch that is off does not flip because the fetch is repeated. `LoadError`'s
 *  Retry is right for "the server didn't respond" and wrong for a decided answer.
 */
export function EvalsOff({ what }: {
  /** What cannot run, as the user would say it — "judge benchmark", "ablation". No article. */
  what: string
}) {
  return (
    <p className="text-on-surface-low text-[0.8125rem]">
      The eval substrate is off, so no {what} can run — turn it on with{' '}
      <code className="text-on-surface-var">personalclaw config set evals.enabled true</code>.
    </p>
  )
}
