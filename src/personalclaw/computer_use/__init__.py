"""Desktop computer use — driving the operator's own applications through the OS
accessibility layer (DESKTOP-COMPUTER-USE).

The only module here today is :mod:`personalclaw.computer_use.enable_state`, the keystone
out-of-band enable (§3 floor 1). The tool surface, the in-gateway dispatch chain and the
platform drivers are later atoms (`DCU-2`..`DCU-4`), and the whole capability is OFF until
an operator turns it on out-of-band — so there is deliberately nothing to import from here
yet. This package intentionally re-exports nothing: a convenience alias would be a declared
symbol with no reader until the dispatch chain exists, and callers say
``from personalclaw.computer_use import enable_state`` instead.
"""
