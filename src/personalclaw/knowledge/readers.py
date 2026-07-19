"""File readers for knowledge ingestion. Supports text, PDF, PPTX, DOCX, HTML, XLSX."""

import os
import re
from pathlib import Path

from personalclaw.security import is_sensitive_path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore[assignment]

try:
    from pptx import Presentation  # type: ignore[import-untyped]
except ImportError:
    Presentation = None  # type: ignore[assignment,misc]

try:
    from docx import Document  # type: ignore[import-untyped]
except ImportError:
    Document = None  # type: ignore[assignment,misc]

try:
    import html2text as _html2text_mod
except ImportError:
    _html2text_mod = None  # type: ignore[assignment]

try:
    from openpyxl import load_workbook as _load_workbook  # type: ignore[import-untyped]
except ImportError:
    _load_workbook = None  # type: ignore[assignment]


class FileReader:
    # Note: .pdf/.pptx require optional runtime dependencies (pdfplumber, python-pptx)
    # not declared in setup.cfg. .docx requires python-docx (pip install python-docx).
    SUPPORTED = {
        '', '.md', '.markdown', '.txt', '.text', '.py', '.java', '.ts', '.js', '.rs', '.go',
        '.html', '.htm', '.docx',
        '.csv', '.tsv', '.log', '.json', '.yaml', '.yml', '.sh', '.rb', '.c', '.cpp', '.h',
        '.xlsx', '.xls',
    }

    _DISPATCH = {
        '.pdf': '_read_pdf',
        '.pptx': '_read_pptx',
        '.docx': '_read_docx',
        '.html': '_read_html',
        '.htm': '_read_html',
        '.xlsx': '_read_xlsx',
        '.xls': '_read_xlsx',
        '.csv': '_read_csv',
        '.tsv': '_read_csv',
    }

    # Cap the markdown-table rendering of a large CSV so a huge file doesn't bloat the
    # stored content / embedding; the true row_count is still recorded in metadata.
    _CSV_MAX_TABLE_ROWS = 500

    def read(self, path: str) -> tuple[str, dict]:
        if is_sensitive_path(path):
            raise PermissionError(f"Refusing to read sensitive path: {path}")
        p = Path(path)
        ext = p.suffix.lower()
        base_meta = {
            'format': ext.lstrip('.'),
            'title': p.stem,
            'file_size': os.path.getsize(path),
            'extension': ext,
        }
        method_name = self._DISPATCH.get(ext)
        if method_name:
            text, meta = getattr(self, method_name)(path)
            base_meta.update(meta)
        else:
            text, meta = self._read_text(path, ext.lstrip('.'))
            base_meta.update(meta)
        base_meta['line_count'] = text.count('\n') + 1 if text else 0
        return text, base_meta

    def _read_text(self, path: str, fmt: str) -> tuple[str, dict]:
        try:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, 'r', encoding='latin-1') as f:
                    text = f.read()
            return text, {'format': fmt}
        except Exception as e:
            return f'Error reading file: {e}', {'format': 'error', 'error': str(e)}

    def _read_pdf(self, path: str) -> tuple[str, dict]:
        if pdfplumber is None:
            return ('PDF support requires pdfplumber: pip install pdfplumber',
                    {'format': 'error', 'error': 'PDF support requires pdfplumber'})
        try:
            with pdfplumber.open(path) as pdf:
                pages = [p.extract_text() or '' for p in pdf.pages]
                return '\n'.join(pages), {'format': 'pdf', 'page_count': len(pages)}
        except Exception as e:
            # A .pdf that isn't a real PDF is often a mislabeled text/markdown file.
            # Salvage it: read as text when the bytes decode to mostly-printable
            # content, rather than failing outright and losing the user's content.
            salvaged = self._salvage_as_text(path)
            if salvaged is not None:
                return salvaged, {'format': 'text', 'recovered_from': 'pdf'}
            return f'Error reading file: {e}', {'format': 'error', 'error': str(e)}

    @staticmethod
    def _salvage_as_text(path: str) -> str | None:
        """Return decoded text if the file is plausibly plain text (mostly printable),
        else None. Used to recover a mislabeled text file from a failed binary parse."""
        try:
            with open(path, 'rb') as f:
                raw = f.read(200_000)
        except OSError:
            return None
        if not raw:
            return None
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return None
        sample = text[:2000]
        printable = sum(1 for c in sample if c.isprintable() or c in '\n\r\t ')
        # Require the sample to be ≥90% printable to treat it as genuine text.
        if printable / max(1, len(sample)) < 0.9:
            return None
        return text

    def _read_pptx(self, path: str) -> tuple[str, dict]:
        if Presentation is None:
            return ('PPTX support requires python-pptx: pip install python-pptx',
                    {'format': 'error', 'error': 'PPTX support requires python-pptx'})
        try:
            prs = Presentation(path)
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                title = ''
                body_parts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        text = shape.text_frame.text.strip()
                        if shape == slide.shapes.title:
                            title = text
                        else:
                            body_parts.append(text)
                notes = ''
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                section = f'## Slide {i}: {title}\n{chr(10).join(body_parts)}'
                if notes:
                    section += f'\n{notes}'
                parts.append(section)
            return '\n\n'.join(parts), {'format': 'pptx', 'slide_count': len(prs.slides)}
        except Exception as e:
            return f'Error reading file: {e}', {'format': 'error', 'error': str(e)}

    def _read_docx(self, path: str) -> tuple[str, dict]:
        if Document is None:
            return ('DOCX support requires python-docx: pip install python-docx',
                    {'format': 'error', 'error': 'DOCX support requires python-docx'})
        try:
            doc = Document(path)
            lines = []
            for para in doc.paragraphs:
                style = para.style.name if para.style else ''
                text = para.text
                if style.startswith('Heading'):
                    try:
                        level = int(style.split()[-1])
                    except (ValueError, IndexError):
                        level = 1
                    lines.append(f'{"#" * level} {text}')
                else:
                    lines.append(text)
            return '\n'.join(lines), {'format': 'docx', 'content_type': 'markdown', 'paragraph_count': len(doc.paragraphs)}
        except Exception as e:
            return f'Error reading file: {e}', {'format': 'error', 'error': str(e)}

    def _read_xlsx(self, path: str) -> tuple[str, dict]:
        """Extract a spreadsheet as markdown tables (one per sheet). Without openpyxl,
        return an error rather than letting the binary .xlsx be read as raw text."""
        if _load_workbook is None:
            return ('XLSX support requires openpyxl: pip install openpyxl',
                    {'format': 'error', 'error': 'XLSX support requires openpyxl'})
        try:
            wb = _load_workbook(path, read_only=True, data_only=True)
            parts, total_rows = [], 0
            for ws in wb.worksheets:
                rows = [
                    ['' if c is None else str(c) for c in row]
                    for row in ws.iter_rows(values_only=True)
                ]
                rows = [r for r in rows if any(cell.strip() for cell in r)]
                if not rows:
                    continue
                total_rows += len(rows)
                lines = [f'## {ws.title}', '| ' + ' | '.join(rows[0]) + ' |',
                         '| ' + ' | '.join('---' for _ in rows[0]) + ' |']
                lines += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
                parts.append('\n'.join(lines))
            sheet_count = len(wb.worksheets)
            wb.close()
            return ('\n\n'.join(parts),
                    {'format': 'xlsx', 'content_type': 'markdown',
                     'sheet_count': sheet_count, 'row_count': total_rows})
        except Exception as e:
            return f'Error reading spreadsheet: {e}', {'format': 'error', 'error': str(e)}

    def _read_csv(self, path: str) -> tuple[str, dict]:
        """Render a CSV/TSV as a markdown table (consistent with _read_xlsx), so a tabular
        upload — a 'sheet'-type item — ingests as structured content + row_count metadata
        rather than raw delimited text. Uses the csv module so quoted fields/embedded
        delimiters parse correctly. Large files render a capped table; row_count is true."""
        import csv as _csv

        # .tsv is tab-delimited; everything else (.csv) is comma-delimited.
        delimiter = '\t' if Path(path).suffix.lower() == '.tsv' else ','
        fmt = 'tsv' if delimiter == '\t' else 'csv'
        try:
            try:
                f = open(path, newline='', encoding='utf-8')
            except UnicodeDecodeError:
                f = open(path, newline='', encoding='latin-1')
            with f:
                rows = [
                    [('' if c is None else str(c)) for c in row]
                    for row in _csv.reader(f, delimiter=delimiter)
                ]
        except Exception as e:
            return f'Error reading {fmt.upper()}: {e}', {'format': 'error', 'error': str(e)}
        rows = [r for r in rows if any(cell.strip() for cell in r)]
        if not rows:
            return '', {'format': fmt, 'content_type': 'markdown', 'row_count': 0}
        width = max(len(r) for r in rows)
        rows = [r + [''] * (width - len(r)) for r in rows]  # pad ragged rows
        shown = rows[: self._CSV_MAX_TABLE_ROWS]
        # Escape pipes so cell content can't break the markdown table layout.
        def cells(r: list[str]) -> str:
            return '| ' + ' | '.join(c.replace('|', '\\|') for c in r) + ' |'
        lines = [cells(shown[0]), '| ' + ' | '.join('---' for _ in shown[0]) + ' |']
        lines += [cells(r) for r in shown[1:]]
        if len(rows) > len(shown):
            lines.append(f'\n_…{len(rows) - len(shown)} more rows_')
        return '\n'.join(lines), {'format': fmt, 'content_type': 'markdown', 'row_count': len(rows)}

    def _read_html(self, path: str) -> tuple[str, dict]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                html = f.read()
        except UnicodeDecodeError:
            with open(path, 'r', encoding='latin-1') as f:
                html = f.read()
        except Exception as e:
            return f'Error reading file: {e}', {'format': 'error', 'error': str(e)}
        # Reduce to the page's content (drop nav/header/footer/aside/script/…) before
        # conversion — an uploaded .html file shouldn't ingest its site chrome as content
        # any more than a scraped bookmark should. Same primitive as the bookmark scrape.
        from personalclaw.knowledge.connectors.base import strip_html_chrome

        html = strip_html_chrome(html)
        if _html2text_mod is not None:
            h = _html2text_mod.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            return h.handle(html), {'format': 'html'}
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text, {'format': 'html'}
