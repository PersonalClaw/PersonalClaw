import { describe, expect, it } from 'vitest'
import { resolveTouchedPath } from './CodeCockpitPage'

const ROOT = '/workspace/project'
const REL = 'src/retry.ts'

const pathForms = [
  {
    name: 'absolute-under-root',
    raw: `${ROOT}/${REL}`,
  },
  {
    name: 'parallel-worktree',
    raw: `${ROOT}/.pclaw-worktrees/task-7/${REL}`,
  },
  {
    name: 'bare-relative',
    raw: REL,
  },
]

const annotationForms = [
  {
    name: 'parenthetical',
    suffix: ' (added the retry loop (including the timeout guard))',
  },
  {
    name: 'em-dash',
    suffix: ' — NOT TOUCHED (md5 4c04a1b4)',
  },
]

describe('resolveTouchedPath annotation cleanup', () => {
  it.each(pathForms.flatMap((pathForm) => annotationForms.map((annotationForm) => ({
    pathForm: pathForm.name,
    annotationForm: annotationForm.name,
    raw: `${pathForm.raw}${annotationForm.suffix}`,
  }))))(
    'strips the $annotationForm annotation from an $pathForm path',
    ({ raw }) => {
      expect(resolveTouchedPath(raw, ROOT)).toEqual({
        abs: `${ROOT}/${REL}`,
        rel: REL,
      })
    },
  )
})
