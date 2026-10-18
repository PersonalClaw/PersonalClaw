"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_forwarded_header_docs_match_code.py``

Its checks:
``tests/rails/test_forwarded_header_docs_match_code_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

import personalclaw

SRC = pathlib.Path(personalclaw.__file__).resolve().parent
