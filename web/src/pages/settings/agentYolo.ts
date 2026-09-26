import { api } from '../../lib/api'
import { confirm } from '../../ui/dialog'
import { reportingWrite } from '../../app/reportingWrite'

/** The consent YOLO mode asks for — moved here verbatim from the Agent defaults panel (#753), so
 *  every surface that can turn it on shows the same sentence. Security copy: move it, don't
 *  reword it. */
export const YOLO_CONFIRM = {
  title: 'Turn on YOLO mode?',
  body: 'Every tool-approval confirmation will be skipped, for every session, until you turn this off again — there is no expiry. Only enable this inside a sandbox or for trusted automation.',
  confirmLabel: 'Turn on YOLO mode',
} as const

/** THE ONE WAY TO SET `agent.yolo`.
 *
 *  🔴 TWO SURFACES WROTE THIS SWITCH AND ONLY ONE ASKED. The Agent defaults panel confirmed before
 *  turning YOLO on; the Settings hub's tile called the generic config PATCH directly, so one click
 *  sent `agent.yolo: true` ~40 ms later and every tool-approval confirmation was skipped from then
 *  on — measured on a real gateway: no dialog, no alert, persisted across reload. The confirmation
 *  lived in one caller, so a second caller skipped it by not knowing it existed.
 *
 *  So the question and the write are ONE function, and the server holds the other end: its PATCH
 *  refuses `true` without the `confirm` flag `api.setAgentYolo` sends, and only this function
 *  calls that (`yoloOneWriter.test.ts`).
 *
 *  Turning ON asks; turning OFF never does — it is the tightening direction and must stay one
 *  click. Resolves `true` once the server has accepted the change, and `false` when nothing
 *  changed: the dialog was declined (NOTHING was sent), or the write failed and `reportingWrite`
 *  has already told the user why. Neither surface flips its switch before the server answers, so
 *  on `false` there is nothing to reconcile — both callers simply do nothing.
 *
 *  No cache handling here on purpose: the panel keeps an optimistic local copy of every agent
 *  field, and re-reading the shared key from inside this function would re-seed that copy under
 *  an unrelated edit still in flight. Each caller reconciles the way it reconciles its other
 *  writes. */
export async function setAgentYolo(next: boolean): Promise<boolean> {
  if (next && !(await confirm({
    title: YOLO_CONFIRM.title,
    body: YOLO_CONFIRM.body,
    confirmLabel: YOLO_CONFIRM.confirmLabel,
    danger: true,
  }))) return false
  return reportingWrite(next ? 'turn on YOLO mode' : 'turn off YOLO mode', () => api.setAgentYolo(next))
}
