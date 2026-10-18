"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_no_frame_or_flag_is_left_without_a_reader.py``

Its checks:
``tests/rails/test_no_frame_or_flag_is_left_without_a_reader_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
