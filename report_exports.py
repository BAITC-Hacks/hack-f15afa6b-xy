"""Small Unicode PDF and XLSX exporters for analytics reports."""

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


FONT_PATHS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
)


def pdf_report(lines: list[str]) -> bytes:
    font_path = next((path for path in FONT_PATHS if path.exists()), None)
    if not font_path:
        raise RuntimeError("Unicode PDF font is unavailable")
    if "Pulse109Unicode" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Pulse109Unicode", str(font_path)))
    output = io.BytesIO()
    page = canvas.Canvas(output, pagesize=(595, 842), pageCompression=0)
    page.setTitle("Pulse 109 — аналитический отчёт")
    y = 790
    for line in lines:
        words, current = str(line).split(), ""
        wrapped = []
        for word in words or [""]:
            candidate = f"{current} {word}".strip()
            if current and pdfmetrics.stringWidth(candidate, "Pulse109Unicode", 10) > 495:
                wrapped.append(current); current = word
            else:
                current = candidate
        wrapped.append(current)
        for part in wrapped:
            if y < 50:
                page.showPage(); y = 790
            page.setFont("Pulse109Unicode", 10); page.drawString(50, y, part); y -= 14
    page.save()
    return output.getvalue()


def xlsx_report(rows: list[list[str | int]]) -> bytes:
    sheet_rows = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row, 1):
            letters, number = "", column
            while number:
                number, remainder = divmod(number - 1, 26); letters = chr(65 + remainder) + letters
            ref = f"{letters}{row_index}"
            cells.append(f'<c r="{ref}"><v>{value}</v></c>' if isinstance(value, int) else f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>"
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Pulse 109" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
