import { ApiError } from '../../lib/api'

/** Why Consolidate now did not start one chat, from the route's answer
 *  (`api_memory_consolidate`): a consolidation of it already running (409), no conversation kept
 *  under its key yet (404), or, refused (403), the route's own sentence: a conversation an app
 *  started without your memory, or lasting work someone else asked for. */
function skippedBecause(reason: unknown): string {
  if (reason instanceof ApiError && reason.status === 409) return 'already running'
  if (reason instanceof ApiError && reason.status === 404) return 'no conversation kept yet'
  const said = reason instanceof Error ? reason.message.trim().replace(/\.$/, '') : ''
  return said || 'not started'
}

/** What Settings → Memory says after Consolidate now: the chats whose consolidation started (a
 *  200 answer, and only that), then the ones skipped, by reason. It gave the count alone ("started
 *  for 2/4 sessions"), and nothing of why the rest were not. */
export function consolidateMessage(results: PromiseSettledResult<unknown>[]): string {
  const started = results.filter((r) => r.status === 'fulfilled').length
  const why = new Map<string, number>()
  for (const r of results) {
    if (r.status === 'rejected') {
      const k = skippedBecause(r.reason)
      why.set(k, (why.get(k) ?? 0) + 1)
    }
  }
  const head = `Consolidation started for ${started} chat${started === 1 ? '' : 's'}`
  if (why.size === 0) return `${head}.`
  const reasons = [...why].map(([k, n]) => `${k} (${n})`).join('; ')
  return `${head}; ${results.length - started} skipped: ${reasons}.`
}
