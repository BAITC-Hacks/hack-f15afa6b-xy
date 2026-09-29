"""Aggregate analytics, alerts, forecasts and exports for Pulse 109."""

from __future__ import annotations

import csv
import hashlib
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from report_exports import pdf_report, xlsx_report


class DataQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    region_id: str | None = None
    topic: str | None = None
    data_origin: str = "synthetic_demo"


DATA_ORIGINS = {"organizer", "synthetic_demo"}
DEMO_REGIONS = (
    "KZ-ABA", "KZ-AKM", "KZ-AKT", "KZ-ALM", "KZ-ATY", "KZ-VKO", "KZ-ZHA", "KZ-ZHE", "KZ-ZKO", "KZ-KAR",
    "KZ-KOS", "KZ-KZY", "KZ-MAN", "KZ-PAV", "KZ-SEV", "KZ-TUR", "KZ-ULY", "KZ-AST", "KZ-ALA", "KZ-SHY",
)
DEMO_TOPICS = (
    "heating", "water_supply", "electricity", "roads", "street_lighting", "waste_management",
    "public_transport", "housing_maintenance", "landscaping", "sewerage",
)
DEMO_GENERATOR = "pulse109_deterministic_national_v1"
DEMO_SOURCE_HASH = hashlib.sha256(DEMO_GENERATOR.encode()).hexdigest()
REGION_TERMS = {
    "KZ-ABA": ("абай",), "KZ-AKM": ("акмолинск", "акмола", "ақмола"),
    "KZ-AKT": ("актюбинск", "актобе", "ақтөбе"),
    "KZ-ALM": ("алматинск", "алматы облыс"), "KZ-ATY": ("атырауск", "атырау облыс"),
    "KZ-VKO": ("восточно казахстан", "шығыс қазақстан", "шыгыс казахстан"),
    "KZ-ZHA": ("жамбылск", "жамбыл облыс"), "KZ-ZHE": ("жетісу", "жетису"),
    "KZ-ZKO": ("западно казахстан", "батыс қазақстан", "батыс казахстан"),
    "KZ-KAR": ("карагандинск", "караганда", "қарағанды"),
    "KZ-KOS": ("костанайск", "костанай", "қостанай"),
    "KZ-KZY": ("кызылординск", "кызылорда", "қызылорда"),
    "KZ-MAN": ("мангистауск", "мангистау", "маңғыстау"),
    "KZ-PAV": ("павлодарск", "павлодар облыс"),
    "KZ-SEV": ("северо казахстан", "солтүстік қазақстан", "солтустик казахстан"),
    "KZ-TUR": ("туркестанск", "туркестан облыс", "түркістан облыс"),
    "KZ-ULY": ("улытау", "ұлытау"),
    "KZ-AST": ("город астана", "астана қаласы"),
    "KZ-ALA": ("город алматы", "алматы қаласы"),
    "KZ-SHY": ("город шымкент", "шымкент қаласы"),
}
MONTH_TERMS = {
    1: ("январ", "қаңтар", "кантар"), 2: ("феврал", "ақпан", "акпан"),
    3: ("март", "наурыз"), 4: ("апрел", "сәуір", "сауир"), 5: ("май", "мамыр"),
    6: ("июн", "маусым"), 7: ("июл", "шілде", "шилде"), 8: ("август", "тамыз"),
    9: ("сентябр", "қыркүйек", "кыркуйек"), 10: ("октябр", "қазан", "казан"),
    11: ("ноябр", "қараша", "караша"), 12: ("декабр", "желтоқсан", "желтоксан"),
}


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
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    first = _next_month(current, -23)
    rows, month = [], first
    while month <= current:
        absolute_month = int(month[:4]) * 12 + int(month[5:])
        for region_index, region_id in enumerate(DEMO_REGIONS):
            for topic_index, topic in enumerate(DEMO_TOPICS):
                seasonal = ((absolute_month + topic_index * 2) % 7) - 3
                count = 18 + region_index * 2 + topic_index * 3 + seasonal
                if month == current and (region_index + topic_index) % 19 == 0:
                    count *= 2
                rows.append((month, region_id, topic, count, "synthetic_demo", DEMO_SOURCE_HASH))
        month = _next_month(month)
    conn.executemany("INSERT OR REPLACE INTO regional_monthly_counts VALUES (?,?,?,?,?,?)", rows)
    conn.commit()


def _validate(value: str | None, allowed: set[str], label: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"Unknown {label}")


def _normal(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яәіңғүұқөһё]+", " ", value.casefold().replace("ё", "е")).strip()


def _question_filters(conn, request: DataQuestion, region_terms: dict[str, tuple[str, ...]]) -> tuple[str | None, str | None]:
    raw_text = request.question.casefold().replace("ё", "е")
    text = _normal(raw_text)
    matched_regions = {
        region_id for region_id, terms in region_terms.items() if any(term in text for term in terms)
    }
    if "алматы" in text and "алматы облыс" not in text and "алматинск" not in text:
        matched_regions.add("KZ-ALA")
    if re.search(r"\bастан(?:а|е|ы|у|ой)\b", text):
        matched_regions.add("KZ-AST")
    if re.search(r"\bшымкент(?:е|а|у|ом)?\b", text):
        matched_regions.add("KZ-SHY")
    if len(matched_regions) > 1:
        raise HTTPException(status_code=422, detail="Укажите один регион: в вопросе найдено несколько регионов")
    inferred_region = next(iter(matched_regions), None)
    if request.region_id and inferred_region and request.region_id != inferred_region:
        raise HTTPException(status_code=422, detail="Регион в вопросе не совпадает с выбранным фильтром")

    periods = {f"{year}-{int(month):02d}" for year, month in re.findall(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b", raw_text)}
    periods |= {f"{year}-{int(month):02d}" for month, year in re.findall(r"\b(0?[1-9]|1[0-2])[-./](20\d{2})\b", raw_text)}
    word_months = {month for month, terms in MONTH_TERMS.items() if any(term in text for term in terms)}
    years = set(re.findall(r"\b20\d{2}\b", text))
    if len(word_months) > 1 or len(years) > 1 or len(periods) > 1:
        raise HTTPException(status_code=422, detail="Укажите один месяц и год")
    if word_months:
        if not years:
            raise HTTPException(status_code=422, detail="Для месяца укажите год")
        periods.add(f"{next(iter(years))}-{next(iter(word_months)):02d}")
    if len(periods) > 1:
        raise HTTPException(status_code=422, detail="Числовая и текстовая даты в вопросе не совпадают")
    period = next(iter(periods), next(iter(years), None))
    if period:
        operator = "=" if len(period) == 7 else "LIKE"
        value = period if len(period) == 7 else f"{period}-%"
        conditions, values = ["data_origin=?", f"month {operator} ?"], [request.data_origin, value]
        resolved_region = request.region_id or inferred_region
        if resolved_region:
            conditions.append("region_id=?")
            values.append(resolved_region)
        if request.topic:
            conditions.append("topic=?")
            values.append(request.topic)
        if not conn.execute(
            f"SELECT 1 FROM regional_monthly_counts WHERE {' AND '.join(conditions)} LIMIT 1", values,
        ).fetchone():
            raise HTTPException(status_code=422, detail=f"Для выбранных фильтров нет данных за {period}")
    return request.region_id or inferred_region, period


def _next_month(period: str, step: int = 1) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + step
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _series(conn, region_id=None, topic=None, origin="synthetic_demo", period=None) -> list[dict]:
    conditions, values = ["data_origin = ?"], [origin]
    if region_id:
        conditions.append("region_id = ?")
        values.append(region_id)
    if topic:
        conditions.append("topic = ?")
        values.append(topic)
    if period:
        conditions.append("month = ?" if len(period) == 7 else "month LIKE ?")
        values.append(period if len(period) == 7 else f"{period}-%")
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


def _forecast_values(values: list[int], horizon: int, method: str) -> list[int]:
    if method == "last_value":
        return [values[-1]] * horizon
    if method == "moving_average_3":
        rolling = list(values)
        result = []
        for _ in range(horizon):
            result.append(round(statistics.mean(rolling[-3:])))
            rolling.append(result[-1])
        return result
    return _linear_forecast(values, horizon)


def _backtest(values: list[int], method: str, horizon: int) -> tuple[list[int], list[float]]:
    errors, percentages = [], []
    for cutoff in range(max(4, len(values) - horizon - 5), len(values) - horizon + 1):
        estimate = _forecast_values(values[:cutoff], horizon, method)[-1]
        actual = values[cutoff + horizon - 1]
        errors.append(abs(estimate - actual))
        if estimate + actual:
            percentages.append(200 * abs(estimate - actual) / (estimate + actual))
    return errors, percentages


def _freshness(conn, origin: str) -> dict:
    latest = conn.execute(
        "SELECT MAX(month) FROM regional_monthly_counts WHERE data_origin=?", (origin,)
    ).fetchone()[0]
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    months_behind = None
    if latest:
        current_index = int(current[:4]) * 12 + int(current[5:])
        latest_index = int(latest[:4]) * 12 + int(latest[5:])
        months_behind = max(0, current_index - latest_index)
    return {
        "last_observation_month": latest,
        "as_of_month": current,
        "months_behind": months_behind,
        "status": "unavailable" if months_behind is None else "current" if months_behind == 0 else "stale",
    }


def _provenance(conn, origin: str, filters=None) -> dict:
    coverage = conn.execute(
        "SELECT COUNT(DISTINCT region_id),MIN(month),MAX(month) FROM regional_monthly_counts WHERE data_origin=?",
        (origin,),
    ).fetchone()
    synthetic = origin == "synthetic_demo"
    return {
        "data_origin": origin,
        "source": DEMO_GENERATOR if synthetic else "organizer_monthly_aggregates.csv",
        "synthetic": synthetic,
        "claim": "deterministic product demonstration; not organizer data" if synthetic else "organizer-provided aggregate fixture",
        "coverage_regions": coverage[0],
        "first_observation_month": coverage[1],
        "last_observation_month": coverage[2],
        "freshness": _freshness(conn, origin),
        "filters": filters or {},
    }


def forecast(conn, horizon: int, region_id=None, topic=None, origin="synthetic_demo") -> dict:
    history = _series(conn, region_id, topic, origin)
    minimum = max(6, horizon + 4)
    if len(history) < minimum:
        raise HTTPException(status_code=409, detail=f"At least {minimum} monthly observations are required")
    excluded_partial_month = None
    recent_baseline = statistics.median(item["count"] for item in history[-4:-1])
    if len(history) > 6 and recent_baseline >= 10 and history[-1]["count"] < recent_baseline * .35:
        excluded_partial_month = history.pop()
    if len(history) < minimum:
        raise HTTPException(status_code=409, detail=f"At least {minimum} complete monthly observations are required")
    values = [item["count"] for item in history]
    candidates = {}
    for method in ("last_value", "moving_average_3", "linear_trend"):
        method_errors, method_percentages = _backtest(values, method, horizon)
        candidates[method] = {
            "horizon_months": horizon,
            "mae": round(statistics.mean(method_errors), 2),
            "smape_percent": round(statistics.mean(method_percentages), 2) if method_percentages else 0.0,
        }
    method = min(candidates, key=lambda name: (candidates[name]["mae"], candidates[name]["smape_percent"]))
    predicted = _forecast_values(values, horizon, method)
    evaluations = {}
    intervals = []
    for step in range(1, horizon + 1):
        step_errors, step_percentages = _backtest(values, method, step)
        evaluations[str(step)] = {
            "horizon_months": step,
            "backtest_points": len(step_errors),
            "mae": round(statistics.mean(step_errors), 2),
            "smape_percent": round(statistics.mean(step_percentages), 2) if step_percentages else 0.0,
        }
        intervals.append(max(1, round(1.96 * math.sqrt(statistics.mean(error * error for error in step_errors)))))
    errors, percentages = _backtest(values, method, horizon)
    mae = round(statistics.mean(errors), 2)
    smape = round(statistics.mean(percentages), 2) if percentages else 0.0
    points = [
        {"month": _next_month(history[-1]["month"], step + 1), "value": value,
         "lower": max(0, value - intervals[step]), "upper": value + intervals[step]}
        for step, value in enumerate(predicted)
    ]
    filters = {"region_id": region_id, "topic": topic}
    return {
        "data_origin": origin, "method": method, "horizon_months": horizon,
        "filters": filters, "history_months": len(history), "forecast": points,
        "evaluation": {"horizon_months": horizon, "backtest_points": len(errors), "mae": mae,
                       "smape_percent": smape, "by_horizon": evaluations},
        "model_selection": {"criterion": f"lowest {horizon}-month rolling backtest MAE", "candidates": candidates},
        "excluded_partial_month": excluded_partial_month,
        "interval_method": "1.96 × horizon-specific rolling backtest RMSE",
        "provenance": _provenance(conn, origin, filters),
    }


def alerts(conn, origin="synthetic_demo", region_id=None, topic=None) -> dict:
    conditions, values = ["data_origin=?"], [origin]
    if region_id:
        conditions.append("region_id=?")
        values.append(region_id)
    if topic:
        conditions.append("topic=?")
        values.append(topic)
    rows = conn.execute(
        "SELECT month,region_id,topic,count FROM regional_monthly_counts "
        f"WHERE {' AND '.join(conditions)} ORDER BY region_id,topic,month", values
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
    filters = {"region_id": region_id, "topic": topic}
    return {"data_origin": origin, "method": "latest month vs median of prior 3 months", "items": items[:20],
            "provenance": _provenance(conn, origin, filters)}


def _rank(conn, field: str, region_id=None, topic=None, origin="synthetic_demo", period=None, limit=5) -> list[dict]:
    conditions, values = ["data_origin=?"], [origin]
    if region_id:
        conditions.append("region_id=?")
        values.append(region_id)
    if topic:
        conditions.append("topic=?")
        values.append(topic)
    if period:
        conditions.append("month=?" if len(period) == 7 else "month LIKE ?")
        values.append(period if len(period) == 7 else f"{period}-%")
    return [dict(row) for row in conn.execute(
        f"SELECT {field} id,SUM(count) value FROM regional_monthly_counts WHERE {' AND '.join(conditions)} "
        f"GROUP BY {field} ORDER BY value DESC, {field} LIMIT ?", [*values, limit]
    ).fetchall()]


def report_data(conn, region_names, topic_names, region_id=None, topic=None, origin="synthetic_demo"):
    series = _series(conn, region_id, topic, origin)
    projection = None
    try:
        projection = forecast(conn, 3, region_id, topic, origin)
    except HTTPException as error:
        if error.status_code != 409:
            raise
    excluded = projection and projection["excluded_partial_month"]
    history = series[:-1] if excluded else series
    period = history[-1]["month"] if history else None
    categories = _rank(conn, "topic", region_id, topic, origin, period, 10) if period else []
    regions = _rank(conn, "region_id", region_id, topic, origin, period, 20) if period else []
    for rows, names in ((categories, topic_names), (regions, region_names)):
        for row in rows:
            row["label"] = names.get(row["id"], row["id"])
    signals = alerts(conn, origin, region_id, topic)["items"]
    for signal in signals:
        signal["region_name"] = region_names.get(signal["region_id"], signal["region_id"])
        signal["topic_name"] = topic_names.get(signal["topic"], signal["topic"])
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        "region": region_names.get(region_id, "Все регионы"),
        "topic": topic_names.get(topic, "Все категории"),
        "series": series, "history": history, "forecast": projection,
        "categories": categories, "regions": regions, "alerts": signals,
        "provenance": _provenance(conn, origin, {"region_id": region_id, "topic": topic}),
    }


def answer_question(conn, request: DataQuestion, region_names: dict, topic_names: dict,
                    region_terms: dict[str, tuple[str, ...]]) -> dict:
    text = _normal(request.question)
    region_id, period = _question_filters(conn, request, region_terms)
    filters = {"region_id": region_id, "topic": request.topic, "period": period}
    provenance = _provenance(conn, request.data_origin, filters)
    if any(word in text for word in ("прогноз", "болжам", "forecast")):
        if period:
            raise HTTPException(status_code=422, detail="Для прогноза не указывайте исторический месяц или год")
        result = forecast(conn, 3, region_id, request.topic, request.data_origin)
        points = result["forecast"]
        provenance = result["provenance"] | {"operation": "forecast", "method": result["method"]}
        return {"answer": f"Прогноз на 3 месяца: {', '.join(str(p['value']) for p in points)} обращений.",
                "value": points[-1]["value"], "chart": {"type": "line", "labels": [p["month"] for p in points], "values": [p["value"] for p in points]}, "provenance": provenance}
    if any(word in text for word in ("категор", "тем", "санат")):
        rows = _rank(conn, "topic", region_id, request.topic, request.data_origin, period)
        provenance["operation"] = "sum_by_topic"
        return {"answer": "Ведущие категории: " + ", ".join(f"{topic_names.get(r['id'], r['id'])} — {r['value']}" for r in rows),
                "value": rows[0]["value"] if rows else 0, "chart": {"type": "bar", "labels": [topic_names.get(r["id"], r["id"]) for r in rows], "values": [r["value"] for r in rows]}, "provenance": provenance}
    if any(word in text for word in ("регион", "област", "өңір")) and any(word in text for word in ("топ", "больше", "көп", "лидер", "лидир")):
        rows = _rank(conn, "region_id", region_id, request.topic, request.data_origin, period)
        provenance["operation"] = "sum_by_region"
        return {"answer": "Больше всего обращений: " + ", ".join(f"{region_names.get(r['id'], r['id'])} — {r['value']}" for r in rows),
                "value": rows[0]["value"] if rows else 0, "chart": {"type": "bar", "labels": [region_names.get(r["id"], r["id"]) for r in rows], "values": [r["value"] for r in rows]}, "provenance": provenance}
    if any(word in text for word in ("динами", "тренд", "ай сайын", "месяц")):
        rows = _series(conn, region_id, request.topic, request.data_origin, period)[-18:]
        provenance["operation"] = "monthly_sum"
        return {"answer": f"Динамика за {len(rows)} месяцев; последнее значение — {rows[-1]['count'] if rows else 0}.",
                "value": rows[-1]["count"] if rows else 0, "chart": {"type": "line", "labels": [r["month"] for r in rows], "values": [r["count"] for r in rows]}, "provenance": provenance}
    raise HTTPException(status_code=422, detail="Поддерживаются вопросы о топ-регионах, топ-категориях, динамике и прогнозе")


def build_analytics_router(get_connection: Callable, regions: list[dict], topics: list[dict]) -> APIRouter:
    router = APIRouter(prefix="/api")
    region_ids, topic_ids = {item["id"] for item in regions}, {item["id"] for item in topics}
    region_names = {item["id"]: item["name_ru"] for item in regions}
    topic_names = {item["id"]: item["name_ru"] for item in topics}
    region_terms = {
        item["id"]: tuple({_normal(item["name_ru"]), _normal(item["name_kk"]), *REGION_TERMS.get(item["id"], ())})
        for item in regions
    }

    @router.get("/alerts")
    def get_alerts(data_origin: str = "synthetic_demo", region_id: str | None = None,
                   topic: str | None = None):
        _validate(region_id, region_ids, "region_id"); _validate(topic, topic_ids, "topic"); _validate(data_origin, DATA_ORIGINS, "data_origin")
        with get_connection() as conn:
            return alerts(conn, data_origin, region_id, topic)

    @router.get("/forecast")
    def get_forecast(horizon_months: int = Query(1, ge=1, le=3), region_id: str | None = None,
                     topic: str | None = None, data_origin: str = "synthetic_demo"):
        _validate(region_id, region_ids, "region_id"); _validate(topic, topic_ids, "topic"); _validate(data_origin, DATA_ORIGINS, "data_origin")
        with get_connection() as conn:
            return forecast(conn, horizon_months, region_id, topic, data_origin)

    @router.post("/query")
    def natural_query(request: DataQuestion):
        _validate(request.region_id, region_ids, "region_id"); _validate(request.topic, topic_ids, "topic"); _validate(request.data_origin, DATA_ORIGINS, "data_origin")
        with get_connection() as conn:
            return answer_question(conn, request, region_names, topic_names, region_terms)

    @router.get("/reports")
    def get_report(format: str = Query("pdf", pattern="^(pdf|xlsx)$"), region_id: str | None = None,
                   topic: str | None = None, data_origin: str = "synthetic_demo"):
        _validate(region_id, region_ids, "region_id"); _validate(topic, topic_ids, "topic"); _validate(data_origin, DATA_ORIGINS, "data_origin")
        with get_connection() as conn:
            series = _series(conn, region_id, topic, data_origin)
            provenance = _provenance(conn, data_origin, {"region_id": region_id, "topic": topic})
            report = report_data(conn, region_names, topic_names, region_id, topic, data_origin) if format == "pdf" else None
        total = sum(row["count"] for row in series)
        filters = f"region={region_id or 'all'}; topic={topic or 'all'}"
        if format == "pdf":
            body = pdf_report(report)
            media = "application/pdf"
        else:
            body = xlsx_report([["Pulse 109 analytics report"], ["Data origin", data_origin], ["Source", provenance["source"]],
                          ["Claim", provenance["claim"]], ["Freshness", provenance["freshness"]["status"]],
                          ["Last observation", provenance["last_observation_month"]], ["Filters", filters],
                          ["Total mapped complaints", total], [], ["Month", "Count"], *[[row["month"], row["count"]] for row in series]])
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="pulse109-analytics.{format}"'})

    return router
