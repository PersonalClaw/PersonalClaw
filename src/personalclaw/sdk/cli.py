"""SDK: the app-contributed CLI seams — ``SetupContext`` and ``DoctorLine``.

Plan 32 (Provider-Boundary Completion) adds two generic seams so an app can
hook into the two core CLI commands WITHOUT living in core:

- ``personalclaw setup`` — the setup runner imports the app's declared
  ``cli.setup`` (``"module:function"``) and calls it with a
  :class:`SetupContext`; the function runs its own interactive step (e.g.
  collecting provider tokens) using the context's credential/settings/IO
  helpers. This is the seam the slack app's ``_setup_slack_tokens`` moves onto.

- ``personalclaw doctor`` — the doctor renderer imports the app's declared
  ``cli.doctor`` (``"module:function"``) and calls it (with a hard timeout +
  exception guard); the function returns a ``list[DoctorLine]`` that doctor
  renders as a per-app section.

Both types live HERE (in the published SDK surface) so an app imports them from
``personalclaw.sdk.cli`` only — never a deep core internal — per the app/core
boundary (§2.8). Core's setup/doctor runners construct + consume them.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:  # avoid a hard import cycle; only needed for typing
    from personalclaw.sdk.settings import ProviderSettings


# Doctor line status — the four render buckets a probe may report.
DoctorStatus = Literal["ok", "warn", "fail", "info"]


@dataclass
class SetupContext:
    """The context a ``cli.setup`` function receives during ``personalclaw setup``.

    A small, typed handle onto the few things an app's interactive setup step
    needs — credential store accessors, its own persisted settings, and IO
    helpers — so the step never reaches into core internals directly.

    - ``app_name`` — the installed app's manifest ``name`` (for messaging + SEL).
    - ``get_credential`` / ``save_credential`` — read/write a secret by name in
      the shared credential store (from ``personalclaw.sdk.credentials``).
      ``get_credential`` returns ``""`` when the key is unset.
    - ``settings`` — the :class:`ProviderSettings` accessor (a static-method
      namespace: ``.load(app_name)`` / ``.update(app_name, partial)`` / …) for
      this app's non-secret config (e.g. a slash-command name) per its
      ``settingsSchema``. The app passes its own ``app_name`` to each call.
    - ``print`` — emit a line to the user (defaults to ``builtins.print``).
    - ``input`` — prompt the user for a line; honors non-interactive runs by
      returning ``""`` (the setup step must treat empty as "skip / keep").
    """

    app_name: str
    get_credential: Callable[[str], str]
    save_credential: Callable[[str, str], None]
    settings: "type[ProviderSettings]"
    print: Callable[[str], None] = print
    input: Callable[[str], str] = input


@dataclass
class DoctorLine:
    """One line an app's ``cli.doctor`` probe reports for its doctor section.

    - ``label`` — the check name shown to the user (e.g. "Slack bot token").
    - ``status`` — one of ``ok`` / ``warn`` / ``fail`` / ``info``; the doctor
      renderer maps each to a glyph/color.
    - ``detail`` — optional supporting text (a value hint, remedy, or reason).
    """

    label: str
    status: DoctorStatus
    detail: str = ""


__all__ = ["SetupContext", "DoctorLine", "DoctorStatus"]
