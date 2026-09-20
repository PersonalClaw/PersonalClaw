"""SheetModel → UTF-8 CSV bytes using the standard-library dialect."""

from __future__ import annotations

import csv
import io

from personalclaw.documents.model import SheetModel
from personalclaw.documents.registry import register_writer


def render_csv(model: object) -> bytes:
    """Render one sheet as CSV.

    CSV has no sheet concept, so refusing a multi-sheet workbook is less ambiguous than
    silently concatenating tables or discarding all but the first sheet.
    """
    if not isinstance(model, SheetModel):
        raise TypeError("csv writer expects a SheetModel; prose documents have no rows")
    if len(model.sheets) > 1:
        raise ValueError("csv writer cannot represent multiple sheets; provide one sheet")

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if model.sheets:
        writer.writerows(model.sheets[0].rows)
    return output.getvalue().encode("utf-8")


register_writer("csv", render_csv)
