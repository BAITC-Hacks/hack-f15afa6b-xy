"""Evidence-based analytics actions and persistent human review."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


class ActionUpdate(BaseModel):
    data_origin: Literal["organizer", "synthetic_demo"]
    region_id: str | None = None
    topic: str | None = None
    revision: int = Field(ge=0)
    status: Literal["planned", "in_progress", "done"]
    outcome: Literal["confirmed", "false_alarm", "data_issue"] | None = None
    note: str = Field(default="", max_length=1000)


def init_actions(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS analytics_action_events (
        action_id TEXT NOT NULL, revision INTEGER NOT NULL,
        data_origin TEXT NOT NULL, region_id TEXT, topic TEXT,
        status TEXT NOT NULL, outcome TEXT, note TEXT NOT NULL,
        actor TEXT NOT NULL, updated_at TEXT NOT NULL, snapshot TEXT NOT NULL,
        PRIMARY KEY (action_id, revision)
    )""")


def action(kind, origin, region, topic, period, title, evidence, steps, measure, basis):
    identity = json.dumps([kind, origin, region, topic, period, basis], sort_keys=True)
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest()[:24],
        "kind": kind, "data_origin": origin, "region_id": region, "topic": topic,
        "period": period, "title": title, "evidence": evidence,
        "steps": steps, "measure": measure,
    }


def build_plan(conn, region_names, topic_names, origin, region=None, topic=None):
    from analytics import report_data

    report = report_data(conn, region_names, topic_names, region, topic, origin)
    series, projection = report["series"], report["forecast"]
    period = series[-1]["month"] if series else None
    actual_months = {row[0] for row in conn.execute(
        "SELECT DISTINCT month FROM regional_monthly_counts WHERE data_origin=? "
        "AND (? IS NULL OR region_id=?) AND (? IS NULL OR topic=?)",
        (origin, region, region, topic, topic),
    )}
    missing = [row["month"] for row in series if row["month"] not in actual_months]
    issues = []
    if not series:
        issues.append("В выбранном срезе нет наблюдений.")
    if period and period < datetime.now(timezone.utc).strftime("%Y-%m"):
        issues.append(f"Последние данные — {period}; они не описывают текущую нагрузку.")
    if missing:
        issues.append(f"Пропущено месяцев: {len(missing)}. В расчёте они пока представлены нулями.")
    if projection and projection["excluded_partial_month"]:
        issues.append(f"Месяц {projection['excluded_partial_month']['month']} исключён как вероятно неполный.")
    if origin == "synthetic_demo":
        issues.append("Искусственные данные: можно проверить процесс, но нельзя оценить реальную потребность в ресурсах.")
    if not projection and series:
        issues.append("Истории недостаточно для проверки прогноза на три месяца.")
    items = []
    if issues:
        items.append(action(
            "data", origin, region, topic, period, "1. Проверить основу для решений",
            " ".join(issues),
            ["Сверить полноту выгрузки и определения категорий с владельцем данных.",
             "Записать ограничения; после новой выгрузки повторить оценку прогноза."],
            "Полные месяцы без пропусков; актуальный период и известное покрытие регионов.",
            [series, missing, issues],
        ))
    for signal in report["alerts"]:
        items.append(action(
            "signal", origin, signal["region_id"], signal["topic"], signal["observed_month"],
            f"2. Проверить всплеск: {signal['topic_name']}",
            f"{signal['region_name']} · {signal['observed_month']}: {signal['observed']} обращений "
            f"против медианы {signal['baseline']:g} по {', '.join(signal['supporting_months'])} (+{signal['increase_percent']}%).",
            ["Проверить первичные обращения: повторные контакты, сбой импорта или общая проблема.",
             "Если рост подтверждён, согласовать проверку со службой; зафиксировать вывод и следующий шаг."],
            "Подтверждённый рост / ложный сигнал / ошибка данных; число новых обращений после проверки.",
            signal,
        ))
    if projection:
        last = report["history"][-1]
        next_point = projection["forecast"][0]
        evaluation = projection["evaluation"]
        items.append(action(
            "forecast", origin, region, topic, last["month"], "3. Проверить план нагрузки",
            f"База {last['month']}: {last['count']} обращений. Прогноз на {next_point['month']}: "
            f"{next_point['value']} (диапазон {next_point['lower']}–{next_point['upper']}). "
            f"Ошибка MAE на горизонте 3 месяца: {evaluation['mae']:g}; проверок: {evaluation['backtest_points']}.",
            ["Сопоставить диапазон нагрузки с фактической пропускной способностью службы.",
             "Записать согласованное изменение процесса и дату проверки; после закрытия месяца сравнить план с фактом."],
            "Ошибка прогноза и просрочки до/после изменения. Снижение числа обращений само по себе не доказывает эффект.",
            [report["history"], projection["method"], projection["forecast"]],
        ))
    return items, projection


def latest_reviews(conn, origin, region=None, topic=None):
    return [dict(row) for row in conn.execute(
        "SELECT e.* FROM analytics_action_events e JOIN "
        "(SELECT action_id, MAX(revision) revision FROM analytics_action_events GROUP BY action_id) latest "
        "USING(action_id, revision) WHERE data_origin=? "
        "AND (? IS NULL OR region_id=?) AND (? IS NULL OR topic=?) ORDER BY updated_at DESC, action_id",
        (origin, region, region, topic, topic),
    )]


def build_actions_router(get_connection, region_names, topic_names):
    router = APIRouter(prefix="/analytics")

    def validate_scope(region, topic):
        if (region is not None and region not in region_names) or (topic is not None and topic not in topic_names):
            raise HTTPException(422, "Неизвестный регион или категория")

    @router.get("/actions")
    def get_actions(data_origin: Literal["organizer", "synthetic_demo"] = "synthetic_demo",
                    region_id: str | None = None, topic: str | None = None):
        validate_scope(region_id, topic)
        with get_connection() as conn:
            items, projection = build_plan(conn, region_names, topic_names, data_origin, region_id, topic)
            reviews = latest_reviews(conn, data_origin, region_id, topic)
        by_id = {row["action_id"]: row for row in reviews}
        for item in items:
            saved = by_id.get(item["id"])
            item["review"] = ({key: saved[key] for key in ("revision", "status", "outcome", "note", "actor", "updated_at")}
                              if saved else {"revision": 0, "status": "planned", "outcome": None, "note": ""})
        counts = {key: 0 for key in ("confirmed", "false_alarm", "data_issue")}
        for review in reviews:
            if review["status"] == "done" and review["outcome"]:
                counts[review["outcome"]] += 1
        judged = counts["confirmed"] + counts["false_alarm"]
        history = []
        for review in reviews[:20]:
            snapshot = json.loads(review.pop("snapshot"))
            history.append(review | {"title": snapshot["title"], "evidence": snapshot["evidence"]})
        return {
            "data_origin": data_origin, "items": items, "history": history,
            "feedback": counts | {"reviewed_precision_percent": round(100 * counts["confirmed"] / judged, 1) if judged else None},
            "comparison": projection and {
                "method": projection["method"], "evaluation": projection["evaluation"],
                "candidates": projection["model_selection"]["candidates"],
            },
        }

    @router.post("/actions/{action_id}")
    def update_action(action_id: str, update: ActionUpdate, request: Request):
        validate_scope(update.region_id, update.topic)
        note = update.note.strip()
        if update.status == "done" and not note:
            raise HTTPException(422, "Запишите результат проверки и следующий шаг")
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            items, _ = build_plan(conn, region_names, topic_names, update.data_origin, update.region_id, update.topic)
            item = next((item for item in items if item["id"] == action_id), None)
            if not item:
                raise HTTPException(409, "Данные рекомендации изменились. Обновите аналитику и повторите проверку")
            if item["kind"] == "signal" and update.status == "done" and update.outcome is None:
                raise HTTPException(422, "Укажите результат проверки сигнала")
            if update.outcome and (item["kind"] != "signal" or update.status != "done"):
                raise HTTPException(422, "Оценка сигнала допустима только после его проверки")
            revision = conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM analytics_action_events WHERE action_id=?", (action_id,),
            ).fetchone()[0]
            if revision != update.revision:
                raise HTTPException(409, "Другой оператор уже изменил запись. Обновите аналитику перед сохранением")
            actor = request.state.auth_user["id"]
            updated_at = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO analytics_action_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                action_id, revision + 1, update.data_origin, item["region_id"], item["topic"],
                update.status, update.outcome, note, actor, updated_at, json.dumps(item, ensure_ascii=False),
            ))
        return {"revision": revision + 1, "status": update.status, "outcome": update.outcome,
                "note": note, "actor": actor, "updated_at": updated_at}

    return router
