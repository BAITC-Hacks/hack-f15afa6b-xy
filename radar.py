"""Pulse 109 — Radar: deterministic, explainable grouping of recent complaints.

Rule: only synthetic, active, non-quarantined cases with a known region, a known
district and a confident category are grouped. The group key is
category + region + district + symptom, so an outage, a leak, another district
and another topic never share a signal. A case with an unclear symptom needs word
overlap with an earlier member of the same group. Repeated messages from one
sender with the same text count once.

Counts and time ranges are observed values. baseline_count/growth stay null when
the previous 60 minutes hold no history long enough to describe a normal level.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import current_actor
from incidents import ACTIVE_INCIDENT_STATUSES, attach_incident_routes, incident_detail, incident_event
from triage import HIGH_CONFIDENCE, address_in, analyze, moment, symptom, tokens

RADAR_MIN_CASES = int(os.environ.get("P109_RADAR_MIN_CASES", "5"))
RADAR_WINDOW_MINUTES = int(os.environ.get("P109_RADAR_WINDOW_MINUTES", "15"))
if not 1 <= RADAR_MIN_CASES <= 1000 or not 1 <= RADAR_WINDOW_MINUTES <= 1440:
    raise ValueError("Radar threshold must be 1..1000 cases and its window 1..1440 minutes")
PREVIOUS_WINDOW_MINUTES = 60
# ponytail: a baseline needs matching history covering half the comparison window.
# One old record is a partial sample; widen only with real historical coverage.
BASELINE_MIN_SPAN_MINUTES = 30
BASELINE_MIN_CASES = 2
SIMILARITY_THRESHOLD = 0.12  # same lexical floor as triage.related_cases
DEFAULT_SEVERITY = 2  # synthetic starting value; the operator confirms the real one
DEMO_REGION = "KZ-ALA"
DEMO_DISTRICT = "Бостандыкский"
TOPIC_TITLES = {
    "water_supply": ("воды", "водоснабжение"),
    "heating": ("отопления", "отопление"),
    "electricity": ("электроснабжения", "электроснабжение"),
    "sewerage": ("канализации", "канализация"),
    "waste_management": ("вывоза мусора", "вывоз мусора"),
    "street_lighting": ("уличного освещения", "уличное освещение"),
    "roads": ("дороги", "дороги"),
    "public_transport": ("транспорта", "транспорт"),
    "housing_maintenance": ("обслуживания дома", "обслуживание дома"),
    "landscaping": ("благоустройства", "благоустройство"),
}
DEMO_CASES = (
    ("Бостандыкский район, Тимирязева 42: с утра нет холодной воды, весь дом без воды. Когда включат?",
     "Тимирязева 42", "ru"),
    ("Бостандық ауданы, Тимирязев көшесі 42: таңертеңнен бері су жоқ, бүкіл үй сусыз қалды.",
     "Тимирязев көшесі 42", "kk"),
    ("Аль-Фараби 77, Бостандыкский: воды нет с самого утра, подъезд полностью без воды.",
     "Аль-Фараби 77", "ru"),
    ("Әл-Фараби 77, Бостандық ауданы: су тоқтады, ыстық су да жоқ, балалар сусыз отыр.",
     "Әл-Фараби 77", "kk"),
    ("Гагарина 12 (Бостандыкский район): нет воды, напор пропал после девяти утра.",
     "Гагарина 12", "ru"),
    ("Гагарин көшесі 12: таңертеңнен бері су жоқ, крандардан су шықпайды, бүкіл үй сусыз.",
     "Гагарин көшесі 12", "kk"),
)


def _hash_id(parts: list[str], prefix: str = "rad-") -> str:
    return prefix + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _normalized(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _dedup(cases: list[dict]) -> list[dict]:
    """One sender repeating one text counts once; a case without a sender never collapses."""
    seen, kept = set(), []
    for case in cases:
        key = (case["sender_key"], _normalized(case["text"])) if case["sender_key"] else case["id"]
        if key in seen:
            continue
        seen.add(key)
        kept.append(case)
    return kept


def _similar(first: dict, second: dict) -> bool:
    a, b = tokens(first["text"]), tokens(second["text"])
    return bool(a and b) and len(a & b) / len(a | b) >= SIMILARITY_THRESHOLD


def _known_regions() -> set:
    from app import VALID_REGION_IDS  # local import: app imports this module at load time
    return VALID_REGION_IDS


def _category_for(row: dict, classifier, topic_services) -> tuple[str | None, float, str]:
    """A human-confirmed category wins over the proposal and over the demo rules."""
    if row["decision_status"] == "confirmed" and row["topic"]:
        return row["topic"], 1.0, "подтверждена оператором"
    analysis = analyze(row, classifier, topic_services)
    if row["proposed_topic"]:
        return row["proposed_topic"], analysis["category_confidence"], "предложение системы"
    return analysis["category"], analysis["category_confidence"], "правило по тексту"


def _title(category: str, district: str, sym: str) -> str:
    genitive, nominative = TOPIC_TITLES.get(category, (category, category))
    if sym == "outage":
        return f"Возможное отключение {genitive} — {district} район"
    if sym == "leak":
        return f"Возможная утечка {genitive} — {district} район"
    return f"Возможная проблема: {nominative} — {district} район"


def collect_cases(conn, classifier, topic_services, previous_start: datetime) -> list[dict]:
    """Recent synthetic, active, non-quarantined cases with a known place and category."""
    rows = conn.execute(
        "SELECT * FROM complaints WHERE data_origin = 'synthetic' AND quarantined = 0 AND resolved_at IS NULL "
        "AND decision_status != 'needs_clarification' AND district IS NOT NULL AND region_id IS NOT NULL "
        "AND COALESCE(received_at, ingested_at) >= ?", (previous_start.isoformat(),)).fetchall()
    regions = _known_regions()
    cases = []
    for raw in rows:
        row = dict(raw)
        at = moment(row["received_at"] or row["ingested_at"])
        if at < previous_start or not row["district"].strip() or row["region_id"] not in regions:
            continue
        category, confidence, source = _category_for(row, classifier, topic_services)
        # An unconfirmed ambiguous category (0.63, water or sewer) and an unclear text (0.32) are not
        # signals; only a confident rule/proposal or a category a human confirmed.
        if category not in topic_services or confidence < HIGH_CONFIDENCE:
            continue
        cases.append({"id": row["id"], "text": row["text"], "address": row["address"] or address_in(row["text"]),
                      "district": row["district"], "region_id": row["region_id"], "at": at,
                      "sender_key": row["sender_key"], "incident_id": row["incident_id"], "category": category,
                      "confidence": confidence, "source": source, "symptom": symptom(row["text"])})
    cases.sort(key=lambda case: (case["at"], case["id"]))
    return cases


def group_cases(cases: list[dict]) -> dict[tuple, list[dict]]:
    """Group by category + region + district + symptom; an unclear symptom needs word overlap."""
    # ponytail: lexical comparison within recent groups; index candidate tokens for high-volume streams.
    groups: dict[tuple, list[dict]] = {}
    for case in cases:
        members = groups.setdefault((case["category"], case["region_id"], case["district"], case["symptom"]), [])
        if case["symptom"] == "unknown" and members and not any(_similar(case, other) for other in members):
            continue
        members.append(case)
    return groups


def _active_links(conn, members: list[dict]) -> list[dict]:
    links = []
    for iid in sorted({m["incident_id"] for m in members if m["incident_id"]}):
        row = conn.execute("SELECT id, title, status FROM incidents WHERE id = ?", (iid,)).fetchone()
        if row and row["status"] in ACTIVE_INCIDENT_STATUSES:
            links.append(dict(row))
    return links


def _baseline(previous: list[dict], count: int, current_minutes: int) -> tuple[float | None, float | None, str]:
    window = PREVIOUS_WINDOW_MINUTES
    if len(previous) < BASELINE_MIN_CASES:
        return None, None, (f"Базовая линия недоступна: за предыдущие {window} мин сопоставимых обращений меньше "
                            f"{BASELINE_MIN_CASES}; уровень и рост не рассчитываются.")
    span = round((previous[-1]["at"] - previous[0]["at"]).total_seconds() / 60)
    if span < BASELINE_MIN_SPAN_MINUTES:
        return None, None, (f"Базовая линия недоступна: частичная выборка — {len(previous)} обращения на интервале "
                            f"{span} мин из {window}; по такому срезу обычный уровень не определяется.")
    baseline = len(previous) * current_minutes / window
    growth = round(count / baseline, 2)
    return round(baseline, 2), growth, (f"В истории {len(previous)} обращений за предыдущие {window} мин "
                                      f"(между первым и последним {span} мин). Это {baseline:g} за окно "
                                      f"{current_minutes} мин; отношение темпов ×{growth}. Полнота истории не проверена.")


def build_signals(conn, classifier, topic_services, min_cases: int | None = None,
                  window_minutes: int | None = None, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    window = RADAR_WINDOW_MINUTES if window_minutes is None else window_minutes
    minimum = RADAR_MIN_CASES if min_cases is None else min_cases
    current_start = now - timedelta(minutes=window)
    previous_start = current_start - timedelta(minutes=PREVIOUS_WINDOW_MINUTES)
    signals = []
    for key, members in group_cases(collect_cases(conn, classifier, topic_services, previous_start)).items():
        raw_current = [case for case in members if case["at"] >= current_start]
        current = _dedup(raw_current)
        if not current:
            continue
        case_ids = sorted(case["id"] for case in current)
        signal_id = _hash_id([*key, *case_ids])
        ignored = _is_ignored(conn, signal_id)
        if len(current) < minimum and not ignored:
            continue
        baseline, growth, baseline_note = _baseline(_dedup([c for c in members if c["at"] < current_start]), len(current), window)
        links = _active_links(conn, current)
        reasons = [
            f"Правило: не менее {minimum} обращений одной категории, одного района и одного характера проблемы "
            f"за {window} мин.",
            f"В окне {len(current)} уникальных обращений из {len(raw_current)}: повтор одного текста от одного "
            "отправителя считается один раз.",
            f"Категория: {key[0]} ({current[0]['source']}, уверенность {current[0]['confidence']:g}).",
            baseline_note,
        ]
        if len(links) > 1:
            reasons.append("Обращения связаны с несколькими активными инцидентами — автоматическое объединение "
                           "запрещено, нужно решение человека.")
        signals.append({
            "id": signal_id, "title": _title(key[0], key[2], key[3]), "category": key[0], "region_id": key[1],
            "district": key[2], "count": len(current), "similar_count": len(raw_current),
            "window_minutes": window, "first_at": min(c["at"] for c in current).isoformat(),
            "last_at": max(c["at"] for c in current).isoformat(), "baseline_count": baseline, "growth": growth,
            "case_ids": case_ids,
            "members": [{"id": c["id"], "text": c["text"], "address": c["address"]} for c in current],
            "incident_id": links[0]["id"] if len(links) == 1 else None,
            "unlinked_count": sum(1 for c in current if not c["incident_id"]),
            "ignored": ignored, "reason": " ".join(reasons),
        })
    signals.sort(key=lambda signal: (-signal["count"], signal["id"]))
    return signals


def _is_ignored(conn, signal_id: str) -> bool:
    row = conn.execute("SELECT restored_at FROM radar_ignored WHERE signal_id = ?", (signal_id,)).fetchone()
    return bool(row) and not row["restored_at"]


class ConfirmRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    incident_id: str | None = None


class IgnoreRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    ignored: bool


def _new_incident_id(conn) -> str:
    for _ in range(5):
        candidate = "INC-" + uuid.uuid4().hex[:6].upper()
        if not conn.execute("SELECT 1 FROM incidents WHERE id = ?", (candidate,)).fetchone():
            return candidate
    raise HTTPException(500, "Не удалось создать идентификатор инцидента")


def build_incident_router(get_connection: Callable[[], Any], classifier, topic_services) -> APIRouter:
    """Radar and incident control-center routes on the /api/workspace prefix."""
    router = APIRouter(prefix="/api/workspace")

    def audit(conn, case_id: str, event_type: str, payload: dict, actor: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("evt-" + uuid.uuid4().hex[:12], case_id, event_type, now, now, actor,
             json.dumps(payload, ensure_ascii=False)),
        )

    def find_signal(conn, signal_id: str) -> dict | None:
        for signal in build_signals(conn, classifier, topic_services, min_cases=0):
            if signal["id"] == signal_id:
                return signal
        return None

    def checked_signal(conn, signal_id: str, case_ids: list[str]) -> dict:
        signal = find_signal(conn, signal_id)
        if not signal:
            raise HTTPException(404, "Сигнал радара не найден: состав обращений изменился, обновите радар")
        if signal["case_ids"] != sorted(set(case_ids)):
            raise HTTPException(409, "Предпросмотр устарел: состав обращений изменился. Обновите радар и повторите")
        return signal

    def create_incident(conn, signal: dict, case_ids: list[str], actor: str) -> dict:
        iid = _new_incident_id(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO incidents (id, title, category, region_id, district, service_id, status, started_at, "
            "next_update, severity, data_origin, created_at, first_signal_at, last_update_at, revision, source_signal) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Проверяется', ?, NULL, ?, 'synthetic', ?, ?, ?, 1, ?)",
            (iid, signal["title"], signal["category"], signal["region_id"], signal["district"],
             topic_services[signal["category"]], signal["first_at"], DEFAULT_SEVERITY, now, signal["first_at"], now,
             signal["id"]),
        )
        incident_event(
            conn, iid, "incident_created",
            f"Инцидент создан человеком по сигналу радара: {len(case_ids)} обращений, {signal['district']} район. "
            f"Серьёзность по умолчанию {DEFAULT_SEVERITY} не подтверждена оператором.",
            {"signal_id": signal["id"], "case_ids": case_ids, "category": signal["category"],
             "severity_default": DEFAULT_SEVERITY}, actor,
        )
        return dict(conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone())

    @router.get("/radar")
    def radar():
        with get_connection() as conn:
            return {"items": build_signals(conn, classifier, topic_services), "min_cases": RADAR_MIN_CASES,
                    "window_minutes": RADAR_WINDOW_MINUTES, "data_origin": "synthetic"}

    @router.post("/radar/demo")
    def radar_demo():
        """One fresh synthetic RU/KK water-outage batch per click; the seed fixtures stay untouched."""
        batch = uuid.uuid4().hex[:6].upper()
        now = datetime.now(timezone.utc)
        ids, inserted = [], 0
        with get_connection() as conn:
            for index, (text, address, language) in enumerate(DEMO_CASES, start=1):
                cid = f"PULSE-RADAR-{batch}-{index}"
                at = (now - timedelta(minutes=index)).isoformat()
                cursor = conn.execute(
                    "INSERT INTO complaints (id, data_origin, source_system, text, region_id, received_at, "
                    "ingested_at, language, address, district, channel, sender_key) "
                    "VALUES (?, 'synthetic', 'radar_demo_v1', ?, ?, ?, ?, ?, ?, ?, 'phone', ?)",
                    (cid, text, DEMO_REGION, at, at, language, address, DEMO_DISTRICT, f"radar-demo-{index}"))
                inserted += cursor.rowcount
                audit(conn, cid, "intake", {"channel": "phone", "text_len": len(text), "batch": batch}, "radar_demo")
                ids.append(cid)
        return {"ids": ids, "count": inserted}

    @router.post("/radar/{signal_id}/confirm")
    def confirm_signal(signal_id: str, req: ConfirmRequest):
        actor = current_actor()
        case_ids = sorted(set(req.case_ids))
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            signal = checked_signal(conn, signal_id, case_ids)
            marks = ",".join("?" * len(case_ids))
            members = [dict(row) for row in conn.execute(
                f"SELECT id, incident_id, quarantined, resolved_at, decision_status FROM complaints "
                f"WHERE id IN ({marks})", case_ids)]
            if len(members) != len(case_ids):
                raise HTTPException(409, "Часть обращений из предпросмотра недоступна. Обновите радар")
            for member in members:
                if member["quarantined"]:
                    raise HTTPException(409, f"Обращение {member['id']} в карантине: связь с инцидентом запрещена")
                if member["resolved_at"]:
                    raise HTTPException(409, f"Обращение {member['id']} закрыто: связь с инцидентом не создаётся")
                if member["decision_status"] == "needs_clarification":
                    raise HTTPException(409, f"Обращение {member['id']} ждёт уточнения у гражданина")
            links = _active_links(conn, members)
            if len(links) > 1:
                raise HTTPException(409, "Обращения уже связаны с несколькими активными инцидентами ("
                                         + ", ".join(link["id"] for link in links)
                                         + "): объединение требует решения человека")
            if req.incident_id:
                row = conn.execute("SELECT * FROM incidents WHERE id = ?", (req.incident_id,)).fetchone()
                if not row:
                    raise HTTPException(404, "Инцидент не найден")
                if row["status"] not in ACTIVE_INCIDENT_STATUSES:
                    raise HTTPException(409, f"Инцидент {req.incident_id} завершён: новые обращения к нему не привязываются")
                if (row["category"], row["region_id"], row["district"]) != (signal["category"], signal["region_id"],
                                                                          signal["district"]):
                    raise HTTPException(409, "Инцидент не совпадает с сигналом по категории, региону или району")
                target = dict(row)
            elif links:
                target = dict(conn.execute("SELECT * FROM incidents WHERE id = ?", (links[0]["id"],)).fetchone())
            else:
                row = conn.execute("SELECT * FROM incidents WHERE source_signal = ?", (signal_id,)).fetchone()
                target = dict(row) if row else None
            replayed = bool(target) and all(member["incident_id"] == target["id"] for member in members)
            if not replayed:
                if target is None:
                    target = create_incident(conn, signal, case_ids, actor)
                else:
                    incident_event(conn, target["id"], "radar_confirmed",
                                   f"Сигнал радара подтверждён человеком: {len(case_ids)} обращений.",
                                   {"signal_id": signal_id, "case_ids": case_ids}, actor)
                root = case_ids[0]
                for member in members:
                    if member["incident_id"] == target["id"]:
                        continue
                    related = None if member["id"] == root else root
                    conn.execute(
                        "UPDATE complaints SET incident_id = ?, related_to = COALESCE(related_to, ?) WHERE id = ?",
                        (target["id"], related, member["id"]))
                    audit(conn, member["id"], "incident_linked",
                          {"incident_id": target["id"], "related_to": related, "signal_id": signal_id,
                           "previous_incident_id": member["incident_id"]}, actor)
            return {"incident": incident_detail(conn, target["id"]), "replayed": replayed}

    @router.post("/radar/{signal_id}/ignore")
    def ignore_signal(signal_id: str, req: IgnoreRequest):
        actor = current_actor()
        case_ids = sorted(set(req.case_ids))
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            checked_signal(conn, signal_id, case_ids)
            if req.ignored:
                conn.execute(
                    "INSERT INTO radar_ignored VALUES (?, ?, ?, ?, NULL, NULL) "
                    "ON CONFLICT(signal_id) DO UPDATE SET signature = excluded.signature, "
                    "ignored_at = excluded.ignored_at, actor = excluded.actor, restored_at = NULL, restored_by = NULL",
                    (signal_id, _hash_id(case_ids, prefix=""), now, actor))
                event_type = "radar_ignored"
            else:
                conn.execute("UPDATE radar_ignored SET restored_at = ?, restored_by = ? WHERE signal_id = ?",
                             (now, actor, signal_id))
                event_type = "radar_restored"
            for cid in case_ids:
                audit(conn, cid, event_type, {"signal_id": signal_id, "case_ids": case_ids, "ignored": req.ignored},
                      actor)
            return {"signal_id": signal_id, "ignored": req.ignored, "case_ids": case_ids, "at": now}

    attach_incident_routes(router, get_connection)
    return router
