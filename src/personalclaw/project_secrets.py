"""A project's secret files, by name: the one rule every reader of the credential check asks."""

from __future__ import annotations

import fnmatch
import re

#: A project's secret files, refused by NAME wherever they sit (:func:`is_project_secret_name`):
#: the files a project keeps its keys in, named the way projects name them. ``.env`` and its
#: variants are the environment files a framework loads (``.env.local``, ``.env.production``,
#: ``prod.env``) and direnv's ``.envrc``; a ``secrets`` data file (``secrets.json``,
#: ``secrets.yaml``…, never a source module such as ``secrets.py``, which a project's code may be),
#: ``credentials*.json`` and ``service-account*.json`` hold a project's keys and a cloud account's;
#: ``.npmrc``, ``.pypirc`` and ``.netrc`` hold a package index's or a host's sign-in. Matched
#: regardless of case.
PROJECT_SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    ".envrc",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "secrets.ini",
    "secrets.cfg",
    "secrets.conf",
    "secrets.properties",
    "secrets.txt",
    "secrets.xml",
    "credentials*.json",
    "service-account*.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
)

#: :data:`PROJECT_SECRET_FILE_PATTERNS` as one expression, so a name is matched once: the shell's
#: screen asks it of every word a command holds, and a 256 KB command holds tens of thousands.
_PROJECT_SECRET_RE = re.compile(
    "|".join(fnmatch.translate(p) for p in PROJECT_SECRET_FILE_PATTERNS)
)

#: A part of a name (between dots) that makes it a template of a secret file, which a project
#: commits for others to copy and fill in, and which holds no secret: ``.env.example``,
#: ``secrets.template.json``.
_SECRET_TEMPLATE_PARTS = frozenset({"example", "sample", "template"})


def is_project_secret_name(name: str) -> bool:
    """Whether a file named *name* is one of a project's secret files
    (:data:`PROJECT_SECRET_FILE_PATTERNS`), and not a template of one.

    The one rule for every reader: the credential check (``security.SensitivePaths``) asks it of a
    path's name, so the file tools, the Files view (``file_roots.admit``) and the shell's screen
    (``security.is_sensitive_bash_command``) refuse the same files.
    """
    folded = name.casefold()
    if not _PROJECT_SECRET_RE.match(folded):
        return False
    return not _SECRET_TEMPLATE_PARTS & set(folded.split("."))
