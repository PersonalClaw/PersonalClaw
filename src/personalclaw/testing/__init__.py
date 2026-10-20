"""Test kits core SHIPS for app authors (not core's own test suite).

``tests/`` is not part of the installed distribution — ``[tool.setuptools.packages.find]
where = ["src"]`` (pyproject.toml) plus a ``MANIFEST.in`` that grafts only ``web/dist``
means nothing under ``tests/`` reaches a wheel or sdist. An app in the separate apps
repository, whose CI installs core with ``pip install "personalclaw @ git+…"``, therefore
cannot import anything from ``tests/``.

So a conformance kit that app suites must call lives HERE, inside the package, and is
re-exported through ``personalclaw.sdk.*`` like every other surface an app consumes.
This package is import-light on purpose: it pulls in ``pytest`` nowhere and only touches
core modules the contract it asserts is defined in.
"""
