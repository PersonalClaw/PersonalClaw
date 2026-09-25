import { api } from './api'
import { useMutation, useQuery } from './data'
import { reportActionFailure } from '../app/reportingWrite'

/** The user's DEFAULT PROJECT — where the dashboard's create forms (a new task, a new loop)
 *  start. Set on a project's page ("Make default") or when creating a project.
 *
 *  🔑 AN ACCOUNT PREFERENCE, SO IT IS STORED ON THE SERVER (`entity_settings/projects.json`,
 *  `GET/PUT /api/projects/settings`). It used to be a `localStorage` key while the project page
 *  called it "Default project" — measured across two browsers on one gateway: the project made
 *  the default in the first read "Make default" in the second, and a new task there started in
 *  Personal. The per-entity preference home in the config round-trip contract is exactly this,
 *  and it is what makes the label true on every browser, the desktop app and the phone.
 *
 *  It is changed only on purpose. Picking a project in a create form scopes THAT piece of work;
 *  it no longer moves the default, because a default that silently follows the last pick is not
 *  a default and would make the project page's "Default project" wrong the moment you started
 *  something elsewhere.
 *
 *  `""` means none. The server resolves a deleted or archived default to `""`, so a caller never
 *  has to validate the id against the project list itself. */
export const DEFAULT_PROJECT_KEY = 'projects:settings'

export function useDefaultProject(): {
  defaultProjectId: string
  /** True once the read has answered — with a value OR a failure. A create form waits for this
   *  before choosing its starting project, so it never pre-selects Personal only to jump. */
  settled: boolean
  /** The failed read, when there is no value to show. A caller says so rather than presenting
   *  "no default" as a fact it never learned. */
  error: unknown
  setDefaultProject: (projectId: string) => Promise<unknown>
  pending: boolean
} {
  const { data, error } = useQuery(DEFAULT_PROJECT_KEY, () => api.projectSettings(), { persist: true })
  const { mutate, pending } = useMutation({
    run: (projectId: string) => api.updateProjectSettings({ default_project_id: projectId }),
    invalidates: [DEFAULT_PROJECT_KEY],
    onError: reportActionFailure('change your default project'),
  })
  return {
    defaultProjectId: data?.default_project_id ?? '',
    settled: data !== undefined || !!error,
    error: data === undefined ? error : null,
    setDefaultProject: mutate,
    pending,
  }
}
