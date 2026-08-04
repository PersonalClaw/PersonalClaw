"""`{{secret:KEY}}` in a trigger's action config (§7 item 6 / decision 11 — S115).

**🔴 THE DEFECT THIS CLOSES.** Workflows have carried `{{secret:KEY}}` since WF2-R14 — the validator
rejects an inline credential and tells the author to use that form, and three separate surfaces say
so in their error text. A TRIGGER action did not resolve it. Driven before writing a line:

    bash action, command "echo tok={{secret:MY_KEY}}"
      → stdout: tok={{secret:MY_KEY}}        # the literal placeholder reached the shell

So a user following the documented pattern got a broken command, and the only way to make a trigger
authenticate was to paste the credential into `triggers.json` — a file that is world-readable in the
home, copied into every snapshot (S113 just made sure of that), and echoed into run records and the
UI. The guidance and the mechanism disagreed, and the mechanism won.

**Why this module and not `workflows/secrets.py`.** That module's resolution lives inside the
workflow engine's binding context (`BindingContext.secret_resolver`, reached through `_walk_path`
and the pipe grammar). A trigger action config is a flat `{provider, config}` dict resolved at
dispatch with no binding tree, so reusing it would mean building a fake context around two lines of
string substitution. What IS shared is the thing that matters: both resolve against the same
`CredentialStore`, so a key means one thing across the whole product.

**The disciplines, and why each one is here:**

* **Resolution is at DISPATCH, never at save.** The stored config keeps the placeholder, so the
  secret is not in `triggers.json`, not in a snapshot, and not in the run record.
* **An unresolved key is an ERROR, not an empty string.** Substituting "" would run
  `curl -H "Authorization: Bearer "` — a request that fails somewhere remote with a 401 the user
  cannot trace back to a missing credential. Refusing names the key.
* **Resolved values NEVER travel back.** The resolved dict is handed to the provider and dropped;
  nothing writes it, and `redact_credentials` still guards the output path.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: `{{secret:KEY}}`, with optional inner whitespace so `{{ secret:KEY }}` works too — a user who
#: pads the braces has expressed the same intent, and failing on whitespace would be the kind of
#: silent near-miss that sends someone back to pasting the credential inline.
SECRET_REF_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")


class UnresolvedSecret(Exception):
    """A trigger's action references a credential that is not configured.

    Carries the KEY, because "a secret is missing" without the name leaves the user checking every
    credential they have.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"the action references {{{{secret:{key}}}}}, which is not in the credential store — "
            f"add it with `personalclaw auth` or remove the reference"
        )


def references(value: Any) -> list[str]:
    """Every secret key referenced anywhere in `value`, in first-seen order.

    Walks dicts, lists and strings, because an action config nests (`{"headers": {"Authorization":
    "Bearer {{secret:X}}"}}` is the shape a webhook action actually has). Order is stable so an
    error message and a doctor finding name keys the same way twice.
    """
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for key in SECRET_REF_RE.findall(node):
                if key not in found:
                    found.append(key)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return found


def default_resolver(key: str) -> str:
    """Resolve one key against the shipped `CredentialStore`. Returns "" when unset.

    Mirrors `workflows.controller._secret_resolver` deliberately — the same store, the same
    empty-on-missing contract — so a key resolves identically whether a workflow or a trigger asks.
    The caller decides what "" means; `resolve()` below treats it as a refusal.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.credentials import CredentialStore

    try:
        cred = CredentialStore(config_dir()).resolve(key)
    except KeyError:
        return ""
    except Exception:  # noqa: BLE001 - an unreadable store is a missing secret, not a crash
        logger.debug("credential store unreadable while resolving %r", key, exc_info=True)
        return ""
    return cred.secret or ""


def resolve(config: Any, *, resolver: Callable[[str], str] | None = None) -> Any:
    """`config` with every `{{secret:KEY}}` replaced by its value. Raises `UnresolvedSecret`.

    Returns the input UNCHANGED (same object) when it holds no reference at all, so the common case
    costs one regex scan and allocates nothing.

    A reference that is the WHOLE string yields the raw value; one embedded in a larger string is
    substituted in place (`"Bearer {{secret:X}}"`). Both matter: the first carries a token as a
    field, the second builds a header.

    `resolver` is injected so a test never touches real credentials — the same seam the workflow
    engine uses, and the reason this module's tests need no credential store.
    """
    keys = references(config)
    if not keys:
        return config

    fn = resolver or default_resolver
    values: dict[str, str] = {}
    for key in keys:
        value = fn(key)
        if not value:
            # 🔴 REFUSE, do not substitute "". An empty Authorization header produces a remote 401
            # the user cannot trace to a missing credential; naming the key is the whole point.
            raise UnresolvedSecret(key)
        values[key] = value

    def _sub(node: Any) -> Any:
        if isinstance(node, str):
            whole = SECRET_REF_RE.fullmatch(node.strip())
            if whole:
                return values[whole.group(1)]
            return SECRET_REF_RE.sub(lambda m: values[m.group(1)], node)
        if isinstance(node, dict):
            return {k: _sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_sub(v) for v in node]
        if isinstance(node, tuple):
            return tuple(_sub(v) for v in node)
        return node

    return _sub(config)
