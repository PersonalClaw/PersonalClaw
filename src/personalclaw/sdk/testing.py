"""SDK: what an app's test suite needs to keep its tests off the machine it runs on.

An app's tests run on a developer's machine, and some of what core reaches there is not
inside any PersonalClaw home. ``keychain_off()`` is the one such switch today. The OS keychain
serves every home on the machine, and core reads it, writes it and deletes from it whenever
``keyring`` is importable, so a scratch ``PERSONALCLAW_HOME`` alone leaves a test able to read
or delete the owner's real secrets. After the call, core finds no keychain and credentials live
in the scratch home's ``.env``. It returns the call that lets the keychain back in. A
``conftest.py``'s ``pytest_configure`` turns it off before anything is collected, and
``pytest_unconfigure`` calls what it returned.

An app's harness imports core through ``personalclaw.sdk`` like the app does
(``tests/test_apps_import_boundary.py``), which is why the switch is published here rather than
patched into ``personalclaw.config.credentials`` by each conftest.
"""

from personalclaw.config.credentials import keychain_off  # noqa: F401

__all__ = ["keychain_off"]
