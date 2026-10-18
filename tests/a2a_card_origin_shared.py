"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a2a_card_origin.py``

Its checks:
``tests/rails/test_a2a_card_origin_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

#: The literal #2620 removed. Scanned for as a string so this rail's own docstring and
#: comments cannot satisfy it (comments never reach the AST).
_GUESSED_PORT = "10000"
