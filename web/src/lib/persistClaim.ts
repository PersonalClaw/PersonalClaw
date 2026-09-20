import { useEffect, useState } from 'react'
import { api } from './api'

/** THE ONE OWNER OF "SESSIONS SURVIVE A RESTART" — the fact and every sentence about it.
 *
 *  🔴 THE UI PROMISED A CAPABILITY THE HOST DID NOT HAVE. The terminal header's persistence
 *  toggle rendered off `dashboard.terminal.persist` — the config flag it had just written — and
 *  so read "Sessions are tmux-backed, so they survive a restart." on a machine with no tmux,
 *  where the backend's own gate (`terminal.py::_persist_enabled`) had already refused to make
 *  that promise (issue 545). `persist_available` closed the headline: the host fact now rides
 *  `GET /api/terminal/sessions`, straight from the identical `tmux_substrate.tmux_available()`
 *  call the gate makes, and the terminal hint branches on it.
 *
 *  What that fix left behind is why this module exists — three residues, all the same shape:
 *
 *    1. the terminal toggle still rendered ENABLED and LIT on a tmux-less host. Only the hint
 *       changed, so `aria-pressed` reported ON for a setting the gate can only ever answer
 *       False to, and clicking it wrote a flag that does nothing.
 *    2. Settings › Agent's "Durable worker sessions" row said "Requires the tmux binary;
 *       without it this has no effect" — naming the precondition without ever consulting it,
 *       20 lines of code away from a page that already knew the answer.
 *    3. both sentences were spelled AT THE CALL SITE, so there were two independent
 *       derivations of one promise again — the precise shape that produced the original bug.
 *
 *  So the claim itself is owned here:
 *
 *    * the fact — `persistAvailableFrom` / `usePersistAvailable`. Named once so no surface
 *      re-derives "can this host persist" from a config flag.
 *    * the claim — `persistToggleCopy` / `durableWorkersHint`. Both take the capability as an
 *      ARGUMENT, so a surface physically cannot state the promise without consulting the host.
 *
 *  `persistClaimHasOneOwner.test.ts` holds that boundary by walking `web/src`: no other
 *  non-test source may contain a tmux-survival sentence. Its backend twin,
 *  `test_the_tmux_probe_has_exactly_one_owner`, forbids a second `which("tmux")` outside
 *  `tmux_substrate`. Two owners of one fact is how a gate, a reaper and a label disagree.
 */

/** The published capability, read out of a `GET /api/terminal/sessions` payload.
 *
 *  Absent (an older gateway) reads as UNAVAILABLE, deliberately: the direction to fail in is
 *  the one that declines to promise. Claiming survival because a field was missing is the bug
 *  this module exists to prevent — which is also why the backend publishes the key on its
 *  panel-DISABLED branch too, since "absent" here cannot mean "unknown". */
export function persistAvailableFrom(payload: { persist_available?: boolean }): boolean {
  return Boolean(payload.persist_available)
}

// One probe per page load, shared by every surface that asks. A surface that already lists
// terminal sessions for its own reasons passes its payload to `persistAvailableFrom` instead of
// adding a second request. A rejected probe is cleared so a later mount can retry rather than
// caching a transient failure as "this host cannot persist".
let probe: Promise<boolean> | null = null

/** The host capability, for a surface with no session list of its own (e.g. Settings).
 *  `null` while unknown — surfaces state no claim at all until the answer arrives.
 *
 *  🪤 A CAPABILITY PROBE MAY NOT TAKE DOWN THE SURFACE THAT ASKS. This reads a TERMINAL endpoint
 *  on behalf of a page that has nothing else to do with terminals, so its failure has to stay
 *  local: the call is made inside the async body, where a synchronous throw becomes a rejection
 *  the catch already handles, and the hook simply stays at `null` (= claim nothing). Without that
 *  wrapper a settings panel whose host never answered rendered its error boundary instead of its
 *  form — for a hint. */
export function usePersistAvailable(): boolean | null {
  const [available, setAvailable] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    if (!probe) probe = (async () => persistAvailableFrom(await api.terminalSessions()))()
    probe
      .then((v) => { if (alive) setAvailable(v) })
      .catch(() => { probe = null })
    return () => { alive = false }
  }, [])
  return available
}

/** What the terminal header's persistence toggle says, given the host and the saved flag.
 *
 *  THREE states, and only the first is allowed to promise anything:
 *
 *    host CAN persist    → the label is the verb flip (`label` IS the accessible name and the
 *                          icon-tier tooltip, so it must say what the CLICK does, not what the
 *                          state is), and `active` carries the saved flag.
 *    host CANNOT         → the label names the PRECONDITION, the control is disabled, and
 *                          `active` is FALSE whatever the flag says — because nothing IS on.
 *                          With the flag set the hint says exactly that, so a user who turned
 *                          it on before installing tmux finds out why it did nothing.
 *
 *                          🪤 KEEP THIS LABEL SHORT. The header cluster degrades as a WHOLE
 *                          (`ui/HeaderActions`), so a long label on ONE control demotes every
 *                          sibling to icon-only and every reason becomes a tooltip. "Disable
 *                          persistent sessions" is the widest sibling this has to live beside;
 *                          "Persistent sessions need tmux" is a few px wider, and a fuller
 *                          sentence ("Install tmux to enable persistent sessions") is ~50px
 *                          wider and demotes the row outright. That is why the explanation
 *                          lives in `hint` and the label stays a bare precondition.
 *    host NOT YET KNOWN  → says so, and stays disabled. Callers hide the control while the
 *                          saved flag is still loading, but the promise is withheld here as
 *                          well: a copy generator that promised survival for "no answer yet"
 *                          is one forgetful caller away from being the bug again. Only a
 *                          confirmed `true` earns the claim. */
export function persistToggleCopy(available: boolean | null, on: boolean): {
  label: string
  hint: string
  disabled: boolean
  active: boolean
} {
  if (available === null) {
    return {
      label: on ? 'Disable persistent sessions' : 'Enable persistent sessions',
      hint: 'Checking whether this host can keep sessions alive past a gateway restart…',
      disabled: true,
      active: false,
    }
  }
  if (!available) {
    return {
      label: 'Persistent sessions need tmux',
      hint: on
        ? 'This is on but inert: tmux is not installed on this host, so a session still dies with the gateway. Install tmux and it starts working.'
        : 'Sessions are lost on restart. This needs tmux, which is not installed on this host.',
      disabled: true,
      active: false,
    }
  }
  return {
    label: on ? 'Disable persistent sessions' : 'Enable persistent sessions',
    hint: on
      ? 'Sessions are tmux-backed, so they survive a restart.'
      : 'Sessions are lost on restart. Enabling keeps them alive with tmux.',
    disabled: false,
    active: on,
  }
}

/** The hint for Settings › Agent's "Durable worker sessions" switch — the same tmux fact, asked
 *  about workers instead of terminals. Its old wording named the requirement without ever
 *  checking it ("Requires the tmux binary; without it this has no effect"), which is honest but
 *  leaves the user to discover the answer themselves; the host's answer is right here. The
 *  unknown case keeps the old bare requirement rather than guessing either way. */
export function durableWorkersHint(available: boolean | null): string {
  const base = 'Run workers inside a tmux session on PersonalClaw’s own socket so their shell outlives the gateway. On restart the recovery sweep finds the still-alive worker and marks the run resumable instead of aborting it.'
  if (available === false) return `${base} tmux is not installed on this host, so this has no effect until you install it.`
  if (available === true) return `${base} This host has tmux, so it takes effect.`
  return `${base} Requires the tmux binary.`
}
