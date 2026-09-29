"""Small Unicode PDF and XLSX exporters for analytics reports."""

import io
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit


FONT_PATHS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
)


FONT = "Pulse109Unicode"
INK, MUTED, TEAL, BLUE, GRID = "#172C36", "#526771", "#087F8C", "#425BB5", "#DAE4E8"
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
METHODS = {"last_value": "Последнее наблюдение", "moving_average_3": "Среднее за 3 месяца",
           "linear_trend": "Линейный тренд"}


def number(value):
    return f"{value:,.0f}".replace(",", " ") if value is not None else "Нет данных"


def month(value):
    return f"{MONTHS[int(value[5:7]) - 1]} {value[:4]}"


def change(value, baseline):
    return f"{(value / baseline - 1) * 100:+.1f}%" if baseline else "нет базы сравнения"


def text(page, x, y, value, size=10, color=INK, align="left"):
    page.setFillColor(HexColor(color)); page.setFont(FONT, size)
    draw = page.drawRightString if align == "right" else page.drawString
    draw(x, y, str(value))


def paragraph(page, value, y, width=515, size=10, color=MUTED, x=40):
    for line in simpleSplit(str(value), FONT, size, width):
        text(page, x, y, line, size, color)
        y -= size * 1.45
    return y


def line_chart(page, history, points):
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, Polygon, Circle, String
    from reportlab.graphics import renderPDF

    history = history[-12:]
    if not history:
        paragraph(page, "Нет наблюдений для выбранных фильтров.", 415)
        return
    drawing = Drawing(515, 250)
    left, bottom, width, height = 53, 36, 447, 190
    maximum = max([row["count"] for row in history] + [row["upper"] for row in points] + [1])
    raw_step = maximum / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = max(1, next(v for v in (1, 2, 2.5, 5, 10) if v * magnitude >= raw_step) * magnitude)
    ticks = math.ceil(maximum / step)
    ceiling = step * ticks
    count = len(history) + len(points)
    x = lambda i: left + i * width / max(1, count - 1)
    y = lambda value: bottom + value / ceiling * height
    for tick in range(ticks + 1):
        ordinate = y(tick * step)
        drawing.add(Line(left, ordinate, left + width, ordinate, strokeColor=HexColor(GRID), strokeWidth=.5))
        drawing.add(String(left - 8, ordinate - 3, number(tick * step), fontName=FONT, fontSize=8,
                           textAnchor="end", fillColor=HexColor(MUTED)))
    if points:
        upper = [(x(len(history) + i), y(row["upper"])) for i, row in enumerate(points)]
        lower = [(x(len(history) + i), y(row["lower"])) for i, row in reversed(list(enumerate(points)))]
        drawing.add(Polygon([v for pair in upper + lower for v in pair], fillColor=HexColor("#E1E7FA"), strokeColor=None))
        boundary = x(len(history) - .5)
        drawing.add(Line(boundary, bottom, boundary, bottom + height, strokeColor=HexColor(MUTED),
                         strokeWidth=.6, strokeDashArray=[3, 3]))
        projected = [(x(len(history) - 1), y(history[-1]["count"]))]
        projected += [(x(len(history) + i), y(row["value"])) for i, row in enumerate(points)]
        drawing.add(PolyLine([v for pair in projected for v in pair], strokeColor=HexColor(BLUE),
                             strokeWidth=2, strokeDashArray=[5, 3]))
    actual = [(x(i), y(row["count"])) for i, row in enumerate(history)]
    drawing.add(PolyLine([v for pair in actual for v in pair], strokeColor=HexColor(TEAL), strokeWidth=2))
    for i, row in enumerate(history + points):
        color = BLUE if i >= len(history) else TEAL
        drawing.add(Circle(x(i), y(row.get("count", row.get("value"))), 2.3, fillColor=HexColor(color), strokeColor=None))
        if count <= 8 or i % 2 == 0 or i == count - 1:
            drawing.add(String(x(i), 17, row["month"][5:] + "." + row["month"][2:4], fontName=FONT,
                               fontSize=7.5, textAnchor="middle", fillColor=HexColor(MUTED)))
    renderPDF.draw(drawing, page, 40, 263)
    text(page, 40, 527, "Количество обращений в месяц", 9, MUTED)
    for x_pos, label, color in ((40, "Наблюдения", TEAL), (175, "Прогноз", BLUE), (292, "Диапазон по прошлым ошибкам", "#BAC6EE")):
        page.setFillColor(HexColor(color)); page.rect(x_pos, 246, 13, 4, fill=1, stroke=0)
        text(page, x_pos + 20, 244, label, 8, MUTED)


def bar_chart(page, rows, total, top, row_height=25):
    if not rows:
        return paragraph(page, "Нет данных для выбранного среза.", top)
    maximum = max(row["value"] for row in rows) or 1
    for index, row in enumerate(rows):
        y = top - index * row_height
        paragraph(page, row["label"], y, width=225, size=8.5)
        page.setFillColor(HexColor("#EDF3F5")); page.rect(276, y - 2, 160, 8, fill=1, stroke=0)
        page.setFillColor(HexColor(TEAL)); page.rect(276, y - 2, 160 * row["value"] / maximum, 8, fill=1, stroke=0)
        text(page, 492, y, number(row["value"]), 9, align="right")
        text(page, 555, y, f"{row['value'] / total * 100:.1f}%" if total else "—", 8.5, MUTED, "right")
    return top - len(rows) * row_height


def pdf_report(report: dict) -> bytes:
    font_path = next((path for path in FONT_PATHS if path.exists()), None)
    if not font_path:
        raise RuntimeError("Unicode PDF font is unavailable")
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(font_path)))
    output = io.BytesIO()
    page = canvas.Canvas(output, pagesize=(595, 842), pageCompression=0)
    page.setTitle("Pulse 109 — аналитический отчёт")
    page.setAuthor("Pulse 109")
    provenance = report["provenance"]
    history, projection = report["history"], report["forecast"]
    points = projection["forecast"] if projection else []
    latest = history[-1] if history else None
    total = latest["count"] if latest else 0
    source = "СИНТЕТИЧЕСКОЕ ДЕМО" if provenance["synthetic"] else "АГРЕГАТЫ ОРГАНИЗАТОРОВ"
    scope = f"{report['region']} · {report['topic']}"

    def header(title, subtitle, index):
        text(page, 40, 798, "PULSE 109", 12, TEAL)
        text(page, 555, 799, report["generated_at"], 8, MUTED, "right")
        text(page, 40, 755, title, 23)
        paragraph(page, subtitle, 731, size=10)
        text(page, 40, 32, f"{source} · {provenance['data_origin']}", 7.5, MUTED)
        text(page, 555, 32, f"{index} / 3", 8, MUTED, "right")

    header("Нагрузка и прогноз", "Аналитический отчёт · " + scope, 1)
    warnings = ["Демонстрационные сценарии; не статистика реальных обращений." if provenance["synthetic"]
                else "Неполный пакет организаторов; выводы относятся только к доступным агрегатам."]
    warnings.append(f"Источник охватывает {provenance['coverage_regions']} регионов; в базовом месяце выбранного среза — {len(report['regions'])}.")
    if latest:
        warnings.append(f"Базовый месяц: {month(latest['month'])}. История среза: {month(history[0]['month'])} — {month(latest['month'])}.")
        if latest["month"] < provenance["freshness"]["as_of_month"]:
            warnings.append("Архивные данные: прогноз продолжает этот ряд, а не описывает текущую нагрузку.")
    else:
        warnings.append("Для выбранных фильтров наблюдения отсутствуют. Это не нулевое количество обращений.")
    if projection and projection["excluded_partial_month"]:
        warnings.append(f"{month(projection['excluded_partial_month']['month'])} исключён алгоритмом как вероятно неполный; полноту нужно проверить.")
    paragraph(page, " ".join(warnings), 692, size=9)
    previous = history[-2]["count"] if len(history) > 1 else None
    cards = [("Базовый месяц", number(total) if latest else "Нет данных", month(latest["month"]) if latest else "Нет наблюдений"),
             ("К предыдущему месяцу", change(total, previous) if previous else "—", "Сравнение наблюдений"),
             ("Следующий месяц", number(points[0]["value"]) if points else "Нет прогноза", month(points[0]["month"]) if points else "Нужно ≥7 месяцев")]
    for index, (label, value, note) in enumerate(cards):
        x = 40 + index * 175
        page.setFillColor(HexColor("#F0F5F6")); page.roundRect(x, 559, 165, 67, 6, fill=1, stroke=0)
        text(page, x + 12, 607, label, 9, MUTED)
        text(page, x + 12, 584, value, 18)
        text(page, x + 12, 570, note, 8, MUTED)
    line_chart(page, history, points)
    text(page, 40, 211, "Как использовать этот прогноз", 13)
    if points:
        estimate = sum(row["value"] for row in points)
        recent = sum(row["count"] for row in history[-3:])
        message = f"На следующие 3 месяца ожидается {number(estimate)} обращений: {change(estimate, recent)} к последним 3 наблюдаемым месяцам."
        message += " Это ориентир для планирования нагрузки. Штат нельзя рассчитать без времени обработки и доступности операторов."
    else:
        message = "Прогноз не рассчитан: требуется минимум 7 месячных наблюдений. Соберите более длинную историю; отсутствие прогноза не означает отсутствие нагрузки."
    y = paragraph(page, message, 188)
    if report["categories"] and total:
        leader = report["categories"][0]
        paragraph(page, f"Основной поток — {leader['label']}: {number(leader['value'])} обращений ({leader['value'] / total * 100:.1f}%). Проверьте готовность профильной службы.", y - 8)
    text(page, 40, 64, "График: последние 12 наблюдаемых месяцев и до 3 прогнозных. Шкала начинается с нуля.", 8, MUTED)
    page.showPage()

    header("Где сосредоточена нагрузка", scope, 2)
    period = month(latest["month"]) if latest else "нет наблюдений"
    paragraph(page, f"Срез: {period}. Доли рассчитаны от {number(total)} обращений выбранного среза."
              if latest else "Для выбранных фильтров данных нет; распределения недоступны.", 695, size=9)
    text(page, 40, 661, "Категории", 14)
    text(page, 492, 662, "Обращения", 8, MUTED, "right"); text(page, 555, 662, "Доля", 8, MUTED, "right")
    bar_chart(page, report["categories"], total, 635)
    text(page, 40, 352, f"Регионы · показано {min(8, len(report['regions']))} из {len(report['regions'])}", 14)
    bar_chart(page, report["regions"][:8], total, 324, 26)
    remaining = sum(row["value"] for row in report["regions"][8:])
    if remaining:
        text(page, 40, 95, f"Остальные регионы: {number(remaining)} обращений ({remaining / total * 100:.1f}%).", 9, MUTED)
    paragraph(page, "Сравнивайте абсолютную нагрузку для распределения ресурсов. Без численности населения эти числа не показывают качество работы региона.", 72, size=8)
    page.showPage()

    header("Прогноз и сигналы для проверки", scope, 3)
    y = 695
    if points:
        text(page, 40, y, "Плановый объём и диапазон неопределённости", 13); y -= 29
        for x, label in ((40, "Месяц"), (206, "Ожидается"), (313, "Диапазон"), (463, "К базовому")):
            text(page, x, y, label, 9, MUTED)
        y -= 25
        for row in points:
            for x, value in ((40, month(row["month"])), (206, number(row["value"])),
                             (313, f"{number(row['lower'])} – {number(row['upper'])}"), (463, change(row["value"], total))):
                text(page, x, y, value, 10)
            y -= 27
        y = paragraph(page, "Диапазон — оценка по прошлым ошибкам, не гарантированный 95%-й интервал. Значения округлены до целых; реальное число может выйти за границы.", y - 2, size=9)
        text(page, 40, y - 15, "Насколько можно доверять прогнозу", 13); y -= 37
        y = paragraph(page, f"Метод: {METHODS.get(projection['method'], projection['method'])}. Выбран по минимальной средней ошибке на прошлых трёхмесячных прогнозах.", y, size=9)
        for horizon, evaluation in projection["evaluation"]["by_horizon"].items():
            y = paragraph(page, f"Горизонт {horizon} мес.: средняя ошибка (MAE) {evaluation['mae']:,.1f} обращений; относительная ошибка (sMAPE) {evaluation['smape_percent']:.1f}%; проверок — {evaluation['backtest_points']}.", y - 3, size=9)
        y = paragraph(page, "MAE показывает среднее отклонение в обращениях; sMAPE — относительное отклонение. Чем меньше, тем лучше. Модель выбрана и оценена на этих же проверках; независимая точность на будущих данных не подтверждена.", y - 4, size=8)
    else:
        y = paragraph(page, "Прогноз недоступен: в выбранном срезе недостаточно истории для трёхмесячного расчёта.", y)
    y -= 20
    text(page, 40, y, "Всплески: что проверить в первую очередь", 13); y -= 22
    y = paragraph(page, "Сигнал: последний месяц на 50% и минимум на 10 обращений выше медианы трёх предыдущих. Это повод проверить причину, а не доказательство инцидента.", y, size=9)
    for signal in report["alerts"][:4]:
        y = paragraph(page, f"{signal['region_name']} · {signal['topic_name']} · {month(signal['observed_month'])}: {number(signal['observed'])} вместо обычных {number(signal['baseline'])} (+{signal['increase_percent']:.1f}%).", y - 7, size=9)
    if not report["alerts"]:
        paragraph(page, "Сигналов по этому правилу не найдено или истории недостаточно для сравнения.", y - 7, size=9)
    elif len(report["alerts"]) > 4:
        text(page, 40, y - 12, "Показаны 4 крупнейших сигнала; остальные доступны в аналитике приложения.", 8, MUTED)
    text(page, 40, 64, f"Источник данных: {provenance['source']}", 7.5, MUTED)
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
