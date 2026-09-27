"""Aggregate analytics, alerts, forecasts and exports for Pulse 109."""

from __future__ import annotations

import csv
import io
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field


class DataQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    region_id: str | None = None
    topic: str | None = None


def init_analytics(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS regional_monthly_counts (
            month TEXT NOT NULL, region_id TEXT NOT NULL, topic TEXT NOT NULL,
            count INTEGER NOT NULL CHECK (count >= 0), data_origin TEXT NOT NULL,
            source_sha256 TEXT, PRIMARY KEY (month, region_id, topic, data_origin)
        )"""
    )
    fixture = Path(__file__).resolve().parent / "fixtures" / "organizer_monthly_aggregates.csv"
    if fixture.exists() and not conn.execute(
        "SELECT 1 FROM regional_monthly_counts WHERE data_origin='organizer' LIMIT 1"
    ).fetchone():
        with fixture.open(encoding="utf-8", newline="") as stream:
            conn.executemany(
                "INSERT INTO regional_monthly_counts VALUES (:month,:region_id,:topic,:count,'organizer',:source_sha256)",
                csv.DictReader(stream),
            )
    conn.commit()


def _validate(value: str | None, allowed: set[str], label: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"Unknown {label}")


def _next_month(period: str, step: int = 1) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + step
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _series(conn, region_id=None, topic=None, origin="organizer") -> list[dict]:
    conditions, values = ["data_origin = ?"], [origin]
    if region_id:
        conditions.append("region_id = ?")
        values.append(region_id)
    if topic:
        conditions.append("topic = ?")
        values.append(topic)
    rows = conn.execute(
        f"SELECT month, SUM(count) count FROM regional_monthly_counts WHERE {' AND '.join(conditions)} "
        "GROUP BY month ORDER BY month",
        values,
    ).fetchall()
    if not rows:
        return []
    by_month = {row["month"]: row["count"] for row in rows}
    result, current = [], rows[0]["month"]
    while current <= rows[-1]["month"]:
        result.append({"month": current, "count": by_month.get(current, 0)})
        current = _next_month(current)
    return result


def _linear_forecast(values: list[int], horizon: int) -> list[int]:
    if len(values) == 1:
        return [values[0]] * horizon
    x_mean, y_mean = (len(values) - 1) / 2, statistics.mean(values)
    denominator = sum((x - x_mean) ** 2 for x in range(len(values)))
    slope = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(values)) / denominator
    intercept = y_mean - slope * x_mean
    return [max(0, round(intercept + slope * (len(values) + step))) for step in range(horizon)]


def forecast(conn, horizon: int, region_id=None, topic=None) -> dict:
    history = _series(conn, region_id, topic)
    if len(history) < 6:
        raise HTTPException(status_code=409, detail="At least six monthly observations are required")
    values = [item["count"] for item in history]
    predicted = _linear_forecast(values, horizon)
    errors, percentages = [], []
    for index in range(max(4, len(values) - 6), len(values)):
        estimate, actual = _linear_forecast(values[:index], 1)[0], values[index]
        errors.append(abs(estimate - actual))
        if estimate + actual:
            percentages.append(200 * abs(estimate - actual) / (estimate + actual))
    mae = round(statistics.mean(errors), 2)
    smape = round(statistics.mean(percentages), 2) if percentages else 0.0
    interval = max(1, round(1.96 * math.sqrt(statistics.mean(error * error for error in errors))))
    points = [
        {"month": _next_month(history[-1]["month"], step + 1), "value": value,
         "lower": max(0, value - interval), "upper": value + interval}
        for step, value in enumerate(predicted)
    ]
    return {
        "data_origin": "organizer", "method": "linear_trend", "horizon_months": horizon,
        "filters": {"region_id": region_id, "topic": topic}, "history_months": len(history),
        "forecast": points, "evaluation": {"backtest_points": len(errors), "mae": mae, "smape_percent": smape},
        "interval_method": "1.96 × rolling backtest RMSE",
    }


def alerts(conn) -> dict:
    rows = conn.execute(
        "SELECT month,region_id,topic,count FROM regional_monthly_counts "
        "WHERE data_origin='organizer' ORDER BY region_id,topic,month"
    ).fetchall()
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault((row["region_id"], row["topic"]), []).append(row)
    items = []
    for (region_id, topic), values in groups.items():
        if len(values) < 4:
            continue
        current, previous = values[-1], [row["count"] for row in values[-4:-1]]
        baseline = statistics.median(previous)
        if baseline >= 5 and current["count"] >= baseline * 1.5 and current["count"] - baseline >= 10:
            items.append({
                "region_id": region_id, "topic": topic, "observed_month": current["month"],
                "observed": current["count"], "baseline": baseline,
                "increase_percent": round((current["count"] / baseline - 1) * 100, 1),
                "supporting_months": [row["month"] for row in values[-4:-1]],
            })
    items.sort(key=lambda item: item["increase_percent"], reverse=True)
    return {"data_origin": "organizer", "method": "latest month vs median of prior 3 months", "items": items[:20]}


def _rank(conn, field: str, region_id=None, topic=None) -> list[dict]:
    conditions, values = ["data_origin='organizer'"], []
    if region_id:
        conditions.append("region_id=?")
        values.append(region_id)
    if topic:
        conditions.append("topic=?")
        values.append(topic)
    return [dict(row) for row in conn.execute(
        f"SELECT {field} id,SUM(count) value FROM regional_monthly_counts WHERE {' AND '.join(conditions)} "
        f"GROUP BY {field} ORDER BY value DESC LIMIT 5", values
    ).fetchall()]


def answer_question(conn, request: DataQuestion, region_names: dict, topic_names: dict) -> dict:
    text = request.question.lower()
    if any(word in text for word in ("прогноз", "болжам", "forecast")):
        result = forecast(conn, 3, request.region_id, request.topic)
        points = result["forecast"]
        return {"answer": f"Прогноз на 3 месяца: {', '.join(str(p['value']) for p in points)} обращений.",
                "value": points[-1]["value"], "chart": {"type": "line", "labels": [p["month"] for p in points], "values": [p["value"] for p in points]}, "provenance": result}
    if any(word in text for word in ("регион", "област", "өңір")) and any(word in text for word in ("топ", "больше", "көп", "лидер", "лидир")):
        rows = _rank(conn, "region_id", request.region_id, request.topic)
        return {"answer": "Больше всего обращений: " + ", ".join(f"{region_names.get(r['id'], r['id'])} — {r['value']}" for r in rows),
                "value": rows[0]["value"] if rows else 0, "chart": {"type": "bar", "labels": [region_names.get(r["id"], r["id"]) for r in rows], "values": [r["value"] for r in rows]}, "provenance": {"data_origin": "organizer", "operation": "sum_by_region"}}
    if any(word in text for word in ("категор", "тем", "санат")) and any(word in text for word in ("топ", "больше", "көп", "лидер", "лидир")):
        rows = _rank(conn, "topic", request.region_id, request.topic)
        return {"answer": "Ведущие категории: " + ", ".join(f"{topic_names.get(r['id'], r['id'])} — {r['value']}" for r in rows),
                "value": rows[0]["value"] if rows else 0, "chart": {"type": "bar", "labels": [topic_names.get(r["id"], r["id"]) for r in rows], "values": [r["value"] for r in rows]}, "provenance": {"data_origin": "organizer", "operation": "sum_by_topic"}}
    if any(word in text for word in ("динами", "тренд", "ай сайын", "месяц")):
        rows = _series(conn, request.region_id, request.topic)[-18:]
        return {"answer": f"Динамика за {len(rows)} месяцев; последнее значение — {rows[-1]['count'] if rows else 0}.",
                "value": rows[-1]["count"] if rows else 0, "chart": {"type": "line", "labels": [r["month"] for r in rows], "values": [r["count"] for r in rows]}, "provenance": {"data_origin": "organizer", "operation": "monthly_sum"}}
    raise HTTPException(status_code=422, detail="Поддерживаются вопросы о топ-регионах, топ-категориях, динамике и прогнозе")


def _pdf(lines: list[str]) -> bytes:
    safe = [line.encode("ascii", "replace").decode().replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 11 Tf 50 790 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in safe) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode())
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    output.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    output.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _xlsx(rows: list[list[str | int]]) -> bytes:
    cells = []
    for row_index, row in enumerate(rows, 1):
        content = []
        for column, value in enumerate(row, 1):
            letters, number = "", column
            while number:
                number, remainder = divmod(number - 1, 26); letters = chr(65 + remainder) + letters
            ref = f"{letters}{row_index}"
            content.append(f'<c r="{ref}"><v>{value}</v></c>' if isinstance(value, int) else f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        cells.append(f'<row r="{row_index}">{"".join(content)}</row>')
    sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(cells) + "</sheetData></worksheet>"
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


def build_analytics_router(get_connection: Callable, regions: list[dict], topics: list[dict]) -> APIRouter:
    router = APIRouter(prefix="/api")
    region_ids, topic_ids = {item["id"] for item in regions}, {item["id"] for item in topics}
    region_names = {item["id"]: item["name_ru"] for item in regions}
    topic_names = {item["id"]: item["name_ru"] for item in topics}

    @router.get("/alerts")
    def get_alerts():
        with get_connection() as conn:
            return alerts(conn)

    @router.get("/forecast")
    def get_forecast(horizon_months: int = Query(1, ge=1, le=3), region_id: str | None = None, topic: str | None = None):
        _validate(region_id, region_ids, "region_id"); _validate(topic, topic_ids, "topic")
        with get_connection() as conn:
            return forecast(conn, horizon_months, region_id, topic)

    @router.post("/query")
    def natural_query(request: DataQuestion):
        _validate(request.region_id, region_ids, "region_id"); _validate(request.topic, topic_ids, "topic")
        with get_connection() as conn:
            return answer_question(conn, request, region_names, topic_names)

    @router.get("/reports")
    def get_report(format: str = Query("pdf", pattern="^(pdf|xlsx)$"), region_id: str | None = None, topic: str | None = None):
        _validate(region_id, region_ids, "region_id"); _validate(topic, topic_ids, "topic")
        with get_connection() as conn:
            series = _series(conn, region_id, topic)
        total = sum(row["count"] for row in series)
        filters = f"region={region_id or 'all'}; topic={topic or 'all'}"
        if format == "pdf":
            body = _pdf(["Pulse 109 analytics report", f"Generated UTC: {datetime.now(timezone.utc).isoformat()}", "Data origin: organizer", filters, f"Total mapped complaints: {total}", "Month | Count", *[f"{row['month']} | {row['count']}" for row in series[-36:]]])
            media = "application/pdf"
        else:
            body = _xlsx([["Pulse 109 analytics report"], ["Data origin", "organizer"], ["Filters", filters], ["Total mapped complaints", total], [], ["Month", "Count"], *[[row["month"], row["count"]] for row in series]])
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="pulse109-analytics.{format}"'})

    return router
