import type { WorkflowSaver } from '../../lib/api'

/** Who saved a workflow version, as the owner reads it. The codes are the gateway's
 *  (`workflows/versions.py` `SAVERS`), each set by the door the save came through: an automation
 *  follows a newer version only when it is `owner`, her save in the workflow's editor. */
const SAVED_BY: Record<Exclude<WorkflowSaver, ''>, string> = {
  owner: 'you, in the editor',
  publish: 'the publish switch',
  agent: 'an agent',
  refiner: 'the refiner',
  import: 'an import',
  app: 'an app',
  shipped: 'PersonalClaw',
  brought_in: 'another machine, a restore or a pack',
}

/** Who saved a version, in words; "not recorded" for one saved before the history said. */
export function savedByWords(savedBy: string): string {
  return SAVED_BY[savedBy as Exclude<WorkflowSaver, ''>] ?? 'not recorded'
}
