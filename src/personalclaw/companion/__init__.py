"""Companion-app support (COMPANION-APPS) — the seam native clients connect through.

Today this is discovery only (:mod:`personalclaw.companion.discovery`): an optional
mDNS/DNS-SD advertiser so a phone or a second desktop can *find* this gateway on the
local network, plus the resolver a client uses to look for one.

Discovery answers **where**, never **who may**. Pairing and authorization stay entirely
in the auth layer (``personalclaw.auth.enrollment`` and the token rail) — nothing here
grants, carries, or weakens a credential.
"""
