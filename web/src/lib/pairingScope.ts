import { ApiError } from './api'

/** The gateway's one refusal of a route outside an automation client's scope
 *  (`device_scopes.ERR_SCOPE_REFUSED`). Every route the client's pairing doesn't reach answers it
 *  byte for byte, whatever the route and whether its target exists, so it says nothing about the
 *  thing asked for: only that this sign-in may not ask. */
export const SCOPE_REFUSED = 'device_scope_refused'

/** Whether *e* is that refusal: a read this sign-in may not make, never a failed one. A page shows
 *  it as {@link notAvailableToThisPairing} once and stops asking, never as a load error to retry. */
export function isScopeRefusal(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && e.code === SCOPE_REFUSED
}

/** The plain line a page shows for what its sign-in may not read, *what* a plural phrase: "This
 *  loop's artifacts and tasks are not available to this pairing." */
export function notAvailableToThisPairing(what: string): string {
  return `${what} are not available to this pairing.`
}
