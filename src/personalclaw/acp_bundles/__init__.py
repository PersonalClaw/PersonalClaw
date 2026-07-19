"""Removable ``acp:<cli>`` agent-provider bundles.

Each module here is the implementation behind a first-party agent app under
``apps/<cli>-agent/app.json`` (``provider.type == "agent"``). A
bundle's job is narrow and vendor-specific: resolve its CLI's launch ``argv``
(via the neutral :mod:`personalclaw.acp.cli_resolve`), pick the ACP dialect id
(the ``<cli>`` of ``acp:<cli>``, dispatched by the committed core dialect
registry), apply any CLI-specific spawn hardening, and register an ``acp_agent``
:class:`~personalclaw.llm.registry.ProviderEntry` so the agent runtime is
selectable as ``acp:<cli>`` and probes readiness through the existing seam.

The vendor-neutral core (``acp/dialect.py``, ``acp/client.py``,
``llm/acp_agent.py``) names none of these CLIs; all binary names, model
catalogues, and config isolation live here in the removable bundles.
"""
