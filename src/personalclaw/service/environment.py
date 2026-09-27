"""The environment a PersonalClaw service starts the gateway in.

A launchd agent and a systemd unit start the gateway in an environment of their own, and it
held ``HOME`` and ``PATH`` and nothing else. Everything a user's shell sets for the tools the
gateway runs was gone: ``AWS_PROFILE``, so the Amazon Bedrock app fell back to the default
credential chain and failed; ``CLAUDE_CONFIG_DIR`` and ``CODEX_HOME``, so the agent CLIs and the
import read another directory; ``HF_HOME``, so local models were looked for in the default cache
rather than the one the user chose; even ``PERSONALCLAW_HOME``, so the service ran on
``~/.personalclaw`` while the CLI used the home the user chose.

So ``personalclaw service install`` carries a documented allowlist (:data:`CARRIED`) from the
shell that runs it into the service file, a name at a time, only when that shell sets it. It
never carries a secret: a variable whose name is a credential, or whose value holds one (a
proxy URL with a password in it), is refused with a sentence that says where to put it instead.
A service file is not a secret store. The systemd unit is readable by every user on the machine,
and the launchd plist by any process running as you. The credential store is where a secret
belongs, and the gateway exports every named secret saved there into its own environment when it
starts (``AppConfig.load_credentials``), so a secret reaches the gateway, and the tools it runs,
by reference rather than as a copy in a file.

``personalclaw service status`` prints what the installed file carries; ``--env NAME`` carries one
more variable and ``--no-env NAME`` leaves one out, both at install time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: What ``personalclaw service install`` carries from its shell into the service, grouped by
#: what needs it. Only names, and only non-secret ones: each is a location, a name that points at
#: credentials held elsewhere (``AWS_PROFILE``), or network plumbing. ``docs/reference/cli.md``
#: lists the same set; ``tests/test_service_keeps_its_environment.py`` holds the two together.
CARRIED: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "PersonalClaw",
        (
            "PERSONALCLAW_HOME",
            "PERSONALCLAW_WORKSPACE",
            "PERSONALCLAW_PORT",
            "PERSONALCLAW_CREDENTIAL_BACKEND",
            "PERSONALCLAW_FIRST_PARTY_APPS_DIR",
        ),
    ),
    (
        "AWS, for the Amazon Bedrock app and any AWS tool",
        (
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "AWS_CONFIG_FILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_SDK_LOAD_CONFIG",
            "AWS_CA_BUNDLE",
            "AWS_ROLE_ARN",
            "AWS_ROLE_SESSION_NAME",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_STS_REGIONAL_ENDPOINTS",
            "AWS_ENDPOINT_URL",
        ),
    ),
    (
        "Where agent CLIs and model tools keep their files",
        (
            "CLAUDE_CONFIG_DIR",
            "CODEX_HOME",
            "HF_HOME",
            "HF_HUB_CACHE",
            "HF_TOKEN_PATH",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
        ),
    ),
    (
        "Proxies and TLS trust",
        (
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
        ),
    ),
)

#: Secrets a tool the gateway runs reads straight from the environment. None is ever carried, but
#: one set in the installing shell is named with the reason, so a user whose Bedrock worked from
#: exported keys learns why the service's does not, and where the keys go instead.
SECRETS_READ_FROM_ENV: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
)

#: Set by the service file itself, whatever the shell says: the gateway's own home and the PATH
#: ``service.common.service_path`` builds (plus ``USER`` in the systemd unit).
SERVICE_OWN = frozenset({"HOME", "PATH", "USER"})

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Where a refused secret goes instead. True of both credential backends: Settings → Secrets and
#: `setup --credential` write the credential store, and `Gateway.__init__` calls
#: `load_credentials`, which exports every named secret into the gateway's environment.
_WHERE_SECRETS_GO = "the gateway puts every secret saved there in its environment when it starts"


def carried_names() -> tuple[str, ...]:
    """Every name :data:`CARRIED` lists, in its order."""
    return tuple(name for _, names in CARRIED for name in names)


@dataclass
class Capture:
    """What a service file will carry, and each variable left out with the reason."""

    #: name → value, in :data:`CARRIED` order and then the ``--env`` order.
    env: dict[str, str] = field(default_factory=dict)
    #: Variables left out because their name is a secret's. Said in one sentence, since they
    #: share one reason and one remedy (:func:`summary`).
    secrets: list[str] = field(default_factory=list)
    #: ``(name, sentence)`` for every other variable left out.
    refused: list[tuple[str, str]] = field(default_factory=list)


def capture(
    environ: Mapping[str, str], *, extra: Iterable[str] = (), without: Iterable[str] = ()
) -> Capture:
    """The variables of *environ* a service installed now carries.

    :data:`CARRIED`, plus the names in *extra*, less those in *without*. A name the shell does not
    set is skipped, and a name asked for with ``--env`` that the shell does not set is said to be
    missing, because the user asked for it by name.
    """
    from personalclaw.apps.secret_fields import is_credential_field_name
    from personalclaw.security import redact_credentials

    result = Capture()
    left_out = set(without)
    for name in left_out:
        if not _NAME.fullmatch(name):
            result.refused.append((name, f"{name!r}, because it is not a variable name."))
    asked = list(dict.fromkeys(extra))
    wanted = [
        n
        for n in dict.fromkeys((*carried_names(), *SECRETS_READ_FROM_ENV, *asked))
        if n not in left_out
    ]
    for name in wanted:
        if not _NAME.fullmatch(name):
            result.refused.append((name, f"{name!r}, because it is not a variable name."))
            continue
        if name in SERVICE_OWN:
            result.refused.append((name, f"{name}, because the service file sets it itself."))
            continue
        value = environ.get(name)
        if not value:
            if name in asked:
                result.refused.append((name, f"{name}, because this shell does not set it."))
            continue
        if name in SECRETS_READ_FROM_ENV or is_credential_field_name(name):
            result.secrets.append(name)
            continue
        _, findings = redact_credentials(value)
        if findings:
            result.refused.append(
                (
                    name,
                    f"{name}, because its value has a credential in it and other programs can "
                    "read a service file. Set it without the credential, or save the whole value "
                    f"in Settings → Secrets: {_WHERE_SECRETS_GO}.",
                )
            )
            continue
        if any(ch in value for ch in "\n\r\0"):
            result.refused.append((name, f"{name}, because its value spans lines."))
            continue
        result.env[name] = value
    return result


def summary(carried: Capture) -> list[str]:
    """What ``personalclaw service install`` says it carried and left out, one line each."""
    lines = []
    if carried.env:
        lines.append(f"Carried from this shell: {', '.join(carried.env)}")
    if carried.secrets:
        names = carried.secrets
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        each = "it names a secret" if len(names) == 1 else "each names a secret"
        lines.append(
            f"Not carried: {listed}, because {each} and other programs can read a service file. "
            "Save secrets in Settings → Secrets or with `personalclaw setup --credential "
            f"NAME=…`: {_WHERE_SECRETS_GO}."
        )
    lines += [f"Not carried: {sentence}" for _, sentence in carried.refused]
    return lines


# ── systemd ``Environment=`` lines ────────────────────────────────────────────────────────────


def systemd_assignment(name: str, value: str) -> str:
    """One ``Environment=`` line that systemd reads back as exactly ``name=value``.

    The whole assignment is quoted, so a value may hold spaces; ``\\`` and ``"`` are escaped
    inside the quotes, and ``%`` is doubled because systemd expands ``%`` specifiers in
    ``Environment=``.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'Environment="{name}={escaped}"'


def systemd_environment(unit_text: str) -> dict[str, str]:
    """The variables a unit's ``Environment=`` lines set.

    Exact for the lines :func:`systemd_assignment` writes. systemd's own parser also takes C
    escapes such as ``\\n`` inside quotes, which those lines never contain.
    """
    import shlex

    env: dict[str, str] = {}
    for line in unit_text.splitlines():
        if not line.startswith("Environment="):
            continue
        for assignment in shlex.split(line[len("Environment=") :]):
            name, sep, value = assignment.partition("=")
            if sep:
                env[name] = value.replace("%%", "%")
    return env


def describe(env: Mapping[str, str]) -> list[str]:
    """The lines ``personalclaw service status`` prints for a service's environment."""
    lines = ["Environment the service starts with (carried at `personalclaw service install`):"]
    lines += [f"  {name}={value}" for name, value in env.items()]
    lines.append(
        "Change it: set or unset the variable in your shell and run `personalclaw service "
        "install` again; `--env NAME` carries one more and `--no-env NAME` leaves one out."
    )
    return lines
