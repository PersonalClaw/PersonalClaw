import { useCallback, useMemo, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { capsHaveRoom, retryWindow, spendCapStop } from '../workflows/attentionMeta'
import { clockTime } from '../../lib/epoch'
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
    gateReply: (key, runId, resumeToken, value) => {
      const sent = api.resumeWorkflowRun(runId, { answer: value, resume_token: resumeToken || undefined })
      void send(key, sent, 'Answered. The run is moving again.', 'send that answer')
      return sent
    },
    chatReply: (key, ref, reply) => {
      const sent = api.answerChatQuestion(ref.session, ref.question, reply)
      void send(key, sent, 'skip' in reply ? 'Skipped. The agent goes on without your answer.' : 'Answered. The agent carries on.', 'send that answer')
      return sent
    },
    chat: (key, ref, answer) => {
      const sent = api.answerChatQuestion(ref.session, ref.question,
        answer === 'skip' ? { skip: true } : { answers: [{ selected: [answer.pick], other: '' }] })
      void send(key, sent, answer === 'skip' ? 'Skipped. The agent goes on without your answer.'
        : `Answered “${answer.label}”. The agent carries on.`, 'send that answer')
    },
    planApprove: (key, step) => {
      const sent = step.on === 'loop'
        ? api.uLoopPlanApprove(step.id, step.stepId, step.base)
        : api.chatPlanApprove(step.id, step.stepId, 'here', step.base)
      void send(key, sent, 'Approved. The plan goes on.', 'approve this step')
    },
    planComment: (key, step, text) => {
      const sent = step.on === 'loop'
        ? api.uLoopPlanComment(step.id, step.stepId, text)
        : api.chatPlanComment(step.id, step.stepId, text)
      return send(key, sent, 'Sent. The step is drafted again with your change.', 'send that comment')
    },
    launch: (key, loopId) => {
      void send(key, api.uLoopAction(loopId, 'start'), 'Launched. The loop is running.', 'launch it')
    },
    merge: (key, loopId, tips, done) => {
      void send(key, api.uLoopMerge(loopId, tips), done, 'merge it')
    },
    conflict: (key, loopId, choice, taskId, tip) => {
      void send(key, api.uLoopConflict(loopId, choice, taskId, tip),
        choice === 'redo' ? 'The task runs again on top of your branch.' : 'Dropped. The loop carries on without it.',
        choice === 'redo' ? 'redo the task' : 'drop its work')
    },
    loopResume: (key, loopId) =>
      send(key, api.uLoopAction(loopId, 'resume'), 'Resumed. The loop merges it, or asks again if it still conflicts.', 'resume the loop'),
    run: (key, runId, action) => {
      const sent = action === 'resume' ? api.resumeWorkflowRun(runId, {}) : api.cancelWorkflowRun(runId)
      void send(key, sent, action === 'resume' ? 'Resumed. The run is moving again.' : 'Cancelled. Completed work is kept.',
        action === 'resume' ? 'resume it' : 'cancel it', () => invalidateKeys('inbox', true))
    },
    retry: (key, runId) => {
      // As the run's page retries (`WorkflowRunDetail`): read the run again, and its caps for a run
      // a spend cap stopped, then a fork of it, started. A refused start leaves the new run a draft.
      mark(key, { state: 'busy' })
      void (async () => {
        let child = ''
        try {
          const fresh = await api.workflowRun(runId)
          const window_ = retryWindow(fresh)
          if (window_ && window_.retryAt > Date.now() / 1000) {
            mark(key, { state: 'failed', text: `It can be retried from ${clockTime(new Date(window_.retryAt * 1000).toISOString())}. Nothing was started.` })
            return
          }
          if (spendCapStop(fresh) && !capsHaveRoom(await api.usageBudget())) {
            mark(key, { state: 'failed', text: "The day's spend caps are still full, so nothing was started. Raise them in Settings, or retry once they reset." })
            return
          }
          if (!window_ && !spendCapStop(fresh)) {
            mark(key, { state: 'failed', text: 'A new attempt would stop the same way, so nothing was started. Open the run to see why.' })
            return
          }
          child = (await api.forkWorkflowRun(runId, { note: 'retry after a transient failure' })).child_run_id
          await api.startDraftWorkflowRun(child)
          mark(key, { state: 'done', text: 'Started again as a new run.', link: { href: `#/workflows/runs/${encodeURIComponent(child)}`, label: 'Open the new run' } })
          refreshNeeds()
        } catch (err) {
          if (child) {
            mark(key, { state: 'failed', text: failureText('start the new run', err).replace(/ Nothing was recorded.*$/, ' It waits as a draft.'),
              link: { href: `#/workflows/runs/${encodeURIComponent(child)}`, label: 'Open the new run' } })
          } else mark(key, outcomeFor(err, 'retry it'))
        }
      })()
    },
    dismiss: (key, itemId, done) => {
      void send(key, api.updateInboxItem(itemId, { status: 'dismissed' }), done, 'dismiss it', () => invalidateKeys('inbox', true))
    },
    promote: (key, actionKey, rung, done) => {
      void send(key, api.autonomyGrant(actionKey, rung), done, 'promote it', () => invalidateKeys('inbox', true))
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
