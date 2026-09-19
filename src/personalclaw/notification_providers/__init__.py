"""Pluggable notification DELIVERY backends (MULTI-TENANCY-ENTITY `TSE2-5`).

``providers/registry.py`` carried the ``notification`` provider type as an
:class:`~personalclaw.providers.registry.EntitySeamHandler` whose ``source_of_truth`` said so
in as many words: *"No provider declares type=notification; pluggable delivery backends remain
a future design."* The type was declarable-but-dead — the #47 bug class the
manifest-vs-handler guard exists to prevent.

This package is that design, and it exists because
:mod:`personalclaw.notification_addressing` gave notifications an addressee. Once a note can
say it is for somebody else, the harness needs somewhere to hand it that is not the local
dashboard — otherwise "addressed to a teammate" can only ever mean "dropped".

**Client side only, per the TEAM-SHARED-ENTITIES soul guardrail.** Core never learns how to
reach another person. It hands a foreign-addressed note to an installed provider that says it
can address them, and that provider — a shared store, a team bridge, an app — owns the
transport. With no provider installed, a foreign-addressed note is visible-but-inert locally
and nothing leaves the machine.
"""
