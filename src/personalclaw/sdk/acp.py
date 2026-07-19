"""SDK: the ACP-CLI agent-bundle helpers.

Stable re-export of the generic ACP-CLI infrastructure an agent-bundle app uses to
(a) resolve a launch argv for its CLI (``resolve_acp_cli``) and (b) register an
``acp:<cli>`` agent entry against the core registry (``register_acp_cli_entry``).
Provider-agnostic: claude-code, codex, … are all ACP-CLI bundles built on these;
the CLI-/vendor-specific knowledge (binary names, dialect, npm pkg) stays in
each bundle app, not here.
"""

from personalclaw.acp.cli_resolve import (  # noqa: F401
    is_npx_fallback,
    node_manager_bin_globs,
    provision_acp_adapter,
    resolve_acp_cli,
)
from personalclaw.acp_bundles._register import register_acp_cli_entry  # noqa: F401

__all__ = [
    'resolve_acp_cli',
    'register_acp_cli_entry',
    'is_npx_fallback',
    'provision_acp_adapter',
    'node_manager_bin_globs',
]
