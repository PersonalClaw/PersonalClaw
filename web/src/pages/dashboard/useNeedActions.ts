import { useCallback, useMemo, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { answeredText, failureText, outcomeFor, type CardActions, type Outcome } from './NeedCards'

/** ── Needs you's answers ─────────────────────────────────────────────────────────────────────────
 *
 *  What each card's answer does, and how it went: every answer goes to the item's own route,
 *  through the same `api` call its own page makes, and each card's outcome is kept by its key (busy,
 *  done, ended somewhere else, or failed in the gateway's words). Home reads it, and a project's page
 *  can, so an answer on either is the same answer. *refreshNeeds* reads the list again once an answer
 *  lands. */
export function useNeedActions(refreshNeeds: () => void): {
  outcomes: Record<string, Outcome>
  actions: CardActions
} {
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})
  const mark = useCallback((key: string, o: Outcome) => setOutcomes((prev) => ({ ...prev, [key]: o })), [])

  /** Send one answer and say how it went: done, ended elsewhere, or failed in the gateway's words. */
  const send = useCallback((key: string, sent: Promise<unknown>, done: string, verb: string, after?: () => void) => {
    mark(key, { state: 'busy' })
    return sent
      .then((res) => {
        const refused = res && typeof res === 'object' && 'refused' in res ? (res as { refused?: string }).refused : ''
        if (refused) {
          mark(key, { state: 'failed', text: failureText(verb, new Error(refused)) })
          return false
        }
        mark(key, { state: 'done', text: done })
        after?.()
        refreshNeeds()
        return true
      })
      .catch((err) => {
        // Each refusal says what it means for this card, by its code (`outcomeFor`).
        const outcome = outcomeFor(err, verb)
        mark(key, outcome)
        if (outcome.state === 'ended') refreshNeeds()
        return false
      })
  }, [mark, refreshNeeds])

  const actions: CardActions = useMemo(() => ({
    approval: (key, id, action) => send(key, api.resolveApproval(id, action), action === 'approve' ? 'Approved.' : 'Denied.', `${action} this`),
    question: (key, q, value) => {
      const sent = q.triggerId
        ? api.answerTriggerPark(q.triggerId, { resume_token: q.resumeToken, answer: value === true })
        : q.confirm
          ? api.confirmWorkflowRun(q.runId, { verb: value === true ? 'approve' : 'reject', resume_token: q.resumeToken || undefined })
          : api.resumeWorkflowRun(q.runId, { answer: value, resume_token: q.resumeToken || undefined })
      void send(key, sent, answeredText(q, value), 'send that answer')
    },
    grant: (key, id, action) => {
      void send(key, api.browseGrantResolve(id, action), action === 'approve' ? 'Allowed. The browser task goes on.' : 'Refused. The browser task stops here.', `${action} this`)
    },
    bridge: (key, token, run) => {
      void send(key, api.answerBridgeConfirmation(token, run), run ? 'Confirmed. It ran once.' : 'Dropped. Nothing ran.', 'send that answer')
    },
    proposal: (key, id, action, done) => {
      const sent = action === 'accept' ? api.acceptSkillProposal(id) : api.rejectSkillProposal(id)
      void send(key, sent, done ?? (action === 'accept' ? 'Accepted. The skill is saved.' : 'Rejected.'), `${action} this proposal`,
        () => invalidateKeys('skill-proposals', true))
    },
    learning: (key, id, action, done) => {
      const sent = action === 'accept' ? api.acceptLearningProposal(id) : api.rejectLearningProposal(id)
      void send(key, sent, done ?? (action === 'accept' ? 'Accepted.' : 'Rejected.'), `${action} this proposal`,
        () => invalidateKeys('inbox', true))
    },
    appProposal: (key, itemId, action) => {
      // Approve applies it through the Inbox's apply; Reject dismisses its row, as the Inbox does.
      const sent = action === 'approve'
        ? api.applyInboxProposal(itemId).then((r) => {
          if (r && typeof r === 'object' && 'ok' in r && (r as { ok?: boolean }).ok === false) {
            throw new Error(String((r as { error?: unknown }).error ?? 'it was not applied'))
          }
          return r
        })
        : api.updateInboxItem(itemId, { status: 'dismissed' })
      void send(key, sent, action === 'approve' ? 'Approved. It was applied.' : 'Rejected.', `${action} this`,
        () => invalidateKeys('inbox', true))
    },
    organize: (key, session, proposal, action) => {
      const sent = action === 'accept' ? api.organizeAccept(session, proposal) : api.organizeDecline(session, proposal)
      void send(key, sent, action === 'accept' ? 'Accepted. The chat is organized as suggested.' : 'Declined. It won’t be suggested again.',
        `${action} this`, () => invalidateKeys('inbox', true))
    },
    reply: (key, target, text) => {
      if (target.kind === 'gate') {
        return send(key, api.resumeWorkflowRun(target.runId, { answer: text, resume_token: target.resumeToken || undefined }),
          `Answered “${text}”. The run is moving again.`, 'send that answer')
      }
      if (target.kind === 'loop') return send(key, api.uLoopNudge(target.loopId, text), 'Sent to the loop.', 'send that answer')
      // The chat's own card sends a typed answer as `other`; one it no longer waits on is ended.
      const sent = api.answerChatQuestion(target.session, target.questionId, { answers: [{ selected: [], other: text }] })
        .then((r) => {
          if (r.outcome === 'answered') return r
          throw new ApiError('it was decided or ended somewhere else', 404)
        })
      return send(key, sent, 'Answered. The agent carries on.', 'send that answer')
    },
    trusted: (key) => {
      mark(key, { state: 'done', text: 'Trusted.' })
      refreshNeeds()
    },
  }), [send, mark, refreshNeeds])

  return { outcomes, actions }
}
