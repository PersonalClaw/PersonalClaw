"""Desktop computer use — driving the operator's own applications through the OS
accessibility layer (DESKTOP-COMPUTER-USE).

Three modules today, in the order the dispatch chain runs them:
:mod:`personalclaw.computer_use.enable_state` is the keystone out-of-band enable plus the
operator's target allowlist (§3 floor 1, `DCU-1`/`DCU-2`); :mod:`~personalclaw.computer_use.policy`
DECIDES (steps 2 and 4 — the app allowlist and the secure-field screen); and
:mod:`~personalclaw.computer_use.gate` only RECORDS (step 5 — the SEL audit, which has no veto).
The platform drivers are later atoms (`DCU-3`/`DCU-4`), and the whole capability is OFF until an
operator turns it on out-of-band.

This package intentionally re-exports nothing: a convenience alias would be a declared symbol
with a second reader, and the one thing this package cannot afford is two readers of one
decision — see ``enable_state.require_enabled``'s docstring for the measured version of that
lesson. Callers say ``from personalclaw.computer_use import enable_state`` instead.
"""
