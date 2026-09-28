"""Small deterministic primitives shared by the operations API."""

from __future__ import annotations

import heapq
import math
import random
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from triage import moment, operators_with_load


HEX_SIZE_METRES = 1800
EARTH_RADIUS_METRES = 6_378_137


def _project(latitude: float, longitude: float) -> tuple[float, float]:
    latitude = max(-85.05112878, min(85.05112878, latitude))
    x = EARTH_RADIUS_METRES * math.radians(longitude)
    y = EARTH_RADIUS_METRES * math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))
    return x, y


def _unproject(x: float, y: float) -> tuple[float, float]:
    longitude = math.degrees(x / EARTH_RADIUS_METRES)
    latitude = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS_METRES)) - math.pi / 2)
    return latitude, longitude


def operations_cell(latitude: float, longitude: float) -> str:
    """Return a stable pointy-top hex cell without claiming H3 compatibility."""
    x, y = _project(latitude, longitude)
    q = (math.sqrt(3) / 3 * x - y / 3) / HEX_SIZE_METRES
    r = (2 * y / 3) / HEX_SIZE_METRES
    hx, hz, hy = round(q), round(r), round(-q - r)
    dx, dz, dy = abs(hx - q), abs(hz - r), abs(hy + q + r)
    if dx > dz and dx > dy:
        hx = -hy - hz
    elif dz > dy:
        hz = -hx - hy
    return f"hx1:{int(hx)}:{int(hz)}"


def cell_neighbors(cell_id: str) -> list[str]:
    _, q, r = cell_id.split(":")
    q, r = int(q), int(r)
    return [f"hx1:{q + dq}:{r + dr}" for dq, dr in ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))]


def cell_polygon(cell_id: str) -> list[list[float]]:
    _, q, r = cell_id.split(":")
    q, r = int(q), int(r)
    cx = HEX_SIZE_METRES * math.sqrt(3) * (q + r / 2)
    cy = HEX_SIZE_METRES * 1.5 * r
    points = []
    for index in range(6):
        angle = math.radians(60 * index - 30)
        latitude, longitude = _unproject(
            cx + HEX_SIZE_METRES * math.cos(angle), cy + HEX_SIZE_METRES * math.sin(angle)
        )
        points.append([round(longitude, 6), round(latitude, 6)])
    points.append(points[0])
    return points


def topic_for(row: dict, classifier) -> str | None:
    return row.get("topic") or row.get("proposed_topic") or classifier(row["text"])[0]


def operational_forecast(conn, classifier, horizon_minutes: int = 60,
                         now: datetime | None = None) -> dict:
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    rows = [dict(row) for row in conn.execute("SELECT * FROM complaints WHERE quarantined = 0")]
    active = [row for row in rows if not row.get("resolved_at") and row.get("decision_status") != "needs_clarification"]
    topics = sorted({topic_for(row, classifier) for row in active} - {None})
    online = [operator for operator in operators_with_load(conn) if operator["status"] == "online"]
    active_incidents = Counter(row["category"] for row in conn.execute(
        "SELECT category FROM incidents WHERE status != 'Завершён'"))
    queues = []
    for topic in topics:
        backlog = [row for row in active if topic_for(row, classifier) == topic]
        recent = [row for row in rows if topic_for(row, classifier) == topic and
                  moment(row["received_at"] or row["ingested_at"]) >= now - timedelta(minutes=60)]
        rates = []
        for window in (5, 15, 30, 60):
            count = sum(moment(row["received_at"] or row["ingested_at"]) >= now - timedelta(minutes=window)
                        for row in recent)
            rates.append(count / window)
        arrival_rate = max(rates) * (1 + .15 * bool(active_incidents[topic]))
        operators = sum(topic in operator["skills"] for operator in online)
        average_handle_minutes = 8
        service_rate = operators / average_handle_minutes
        predicted_incoming = round(arrival_rate * horizon_minutes)
        handled = math.floor(service_rate * horizon_minutes)
        predicted_backlog = max(0, len(backlog) + predicted_incoming - handled)
        estimated_wait_seconds = round(predicted_backlog / service_rate * 60) if service_rate else None
        current_risk = sum(
            (now - moment(row["received_at"] or row["ingested_at"])).total_seconds() / 60 + horizon_minutes >=
            (60 if row.get("priority") == "urgent" else 240) for row in backlog
        )
        sla_risk = min(predicted_backlog, max(current_risk, predicted_backlog - handled))
        needed = math.ceil((len(backlog) + predicted_incoming) * average_handle_minutes / horizon_minutes)
        extra = max(0, needed - operators)
        queues.append({
            "queue": topic, "horizon_minutes": horizon_minutes, "current_backlog": len(backlog),
            "arrival_rate_per_minute": round(arrival_rate, 3), "predicted_incoming": predicted_incoming,
            "predicted_backlog": predicted_backlog, "estimated_wait_seconds": estimated_wait_seconds,
            "sla_risk_count": sla_risk, "current_operators": operators,
            "recommended_operators": extra,
            "recommendation": f"+{extra} резервных операторов" if extra else "Текущей ёмкости достаточно",
            "reasons": [f"{len(recent)} обращений за 60 минут", f"текущий backlog {len(backlog)}",
                        f"активных инцидентов {active_incidents[topic]}",
                        f"среднее время обработки {average_handle_minutes} мин · demo assumption"],
        })
    queues.sort(key=lambda item: (-item["sla_risk_count"], -item["predicted_backlog"], item["queue"]))
    origins = {row["data_origin"] for row in active}
    return {
        "label": "Synthetic simulation" if origins <= {"synthetic"} else "Operational estimate",
        "method": "deterministic queue capacity v1", "horizon_minutes": horizon_minutes,
        "generated_at": now.isoformat(), "queues": queues,
        "forecast_latency_ms": round((time.perf_counter() - started) * 1000),
    }


def simulate_queue(initial_backlog: int, arrival_rate: float, operators: int,
                   average_handle_minutes: float, horizon_minutes: int,
                   priority_percent: int, seed: int) -> dict:
    rng = random.Random(seed)
    incoming = max(0, round(arrival_rate * horizon_minutes))
    jobs = [(0.0, rng.random() < priority_percent / 100) for _ in range(initial_backlog)]
    if incoming:
        interval = horizon_minutes / incoming
        jobs.extend((max(0, min(horizon_minutes, (index + .5 + rng.uniform(-.2, .2)) * interval)),
                     rng.random() < priority_percent / 100) for index in range(incoming))
    jobs.sort(key=lambda item: (item[0], not item[1]))
    if operators <= 0:
        return {"queue": len(jobs), "wait_seconds": None, "sla_risk": len(jobs),
                "completed": 0, "incoming": incoming, "operators": 0}
    available = [0.0] * operators
    heapq.heapify(available)
    waits, completions, risks = [], [], 0
    for arrival, priority in jobs:
        ready = heapq.heappop(available)
        start = max(arrival, ready)
        service = average_handle_minutes * rng.uniform(.8, 1.2) * (.85 if priority else 1)
        finish = start + service
        wait = start - arrival
        waits.append(wait)
        completions.append(finish)
        risks += wait > (3 if priority else 10)
        heapq.heappush(available, finish)
    return {
        "queue": sum(finish > horizon_minutes for finish in completions),
        "wait_seconds": round(sum(waits) / len(waits) * 60) if waits else 0,
        "sla_risk": risks, "completed": sum(finish <= horizon_minutes for finish in completions),
        "incoming": incoming, "operators": operators,
    }
