import { useCallback, useMemo, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { answeredText, endedText, failureText, type CardActions, type Outcome } from './NeedCards'

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
        const ended = endedText(err)
        if (ended) {
          mark(key, { state: 'ended', text: ended })
          refreshNeeds()
          return false
        }
        mark(key, { state: 'failed', text: failureText(verb, err) })
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
      void send(key, api.browseGrantResolve(id, action), action === 'approve' ? 'Allowed — the browser task goes on.' : 'Refused — the browser task stops here.', `${action} this`)
    },
    bridge: (key, token, run) => {
      void send(key, api.answerBridgeConfirmation(token, run), run ? 'Confirmed — it ran once.' : 'Dropped — nothing ran.', 'send that answer')
    },
    proposal: (key, id, action) => {
      const sent = action === 'accept' ? api.acceptSkillProposal(id) : api.rejectSkillProposal(id)
      void send(key, sent, action === 'accept' ? 'Accepted — the skill is saved.' : 'Rejected.', `${action} this proposal`,
        () => invalidateKeys('skill-proposals', true))
    },
    reply: (key, target, text) => {
      if (target.kind === 'gate') {
        return send(key, api.resumeWorkflowRun(target.runId, { answer: text, resume_token: target.resumeToken || undefined }),
          `Answered “${text}” — the run is moving again.`, 'send that answer')
      }
      if (target.kind === 'loop') return send(key, api.uLoopNudge(target.loopId, text), 'Sent to the loop.', 'send that answer')
      // The chat's own card sends a typed answer as `other`; one it no longer waits on is ended.
      const sent = api.answerChatQuestion(target.session, target.questionId, { answers: [{ selected: [], other: text }] })
        .then((r) => {
          if (r.outcome === 'answered') return r
          throw new ApiError('it was answered or ended somewhere else', 404)
        })
      return send(key, sent, 'Answered — the agent carries on.', 'send that answer')
    },
  }), [send])

  return { outcomes, actions }
}
