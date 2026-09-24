"""Deterministic, explainable DEMO rules. Scores are synthetic, not calibrated probabilities."""
import json
import os
import re
from datetime import datetime, timezone

HIGH_CONFIDENCE = float(os.environ.get("P109_HIGH_CONFIDENCE", "0.85"))
MEDIUM_CONFIDENCE = float(os.environ.get("P109_MEDIUM_CONFIDENCE", "0.55"))
if not 0 <= MEDIUM_CONFIDENCE < HIGH_CONFIDENCE <= 1:
    raise ValueError("Confidence thresholds must satisfy 0 <= medium < high <= 1")

SERVICE_NAMES = {
    "srv_vodokanal": "Алматинский Су", "srv_sewerage": "Служба канализации",
    "srv_energo": "Электросети", "srv_roads": "Дорожная служба",
    "srv_lighting": "Городское освещение", "srv_clean": "Служба вывоза мусора",
    "srv_teplo": "Теплосети", "srv_housing": "Жилищная служба",
    "srv_transit": "Общественный транспорт", "srv_parks": "Благоустройство",
}

# Synthetic DEMO effort weights. Not calibrated labour estimates; tune only here.
# Case weight precedence, first match wins:
#   linked duplicate 15 (already covered by an open incident) -> phone 100 -> telegram/whatsapp 50
#   -> urgent or complex topic 40 -> anything else 30. A live channel costs more than urgency,
#   so an urgent phone case weighs 100, not 140.
DEMO_WORKLOAD_WEIGHTS = {
    "baseline": 30, "phone": 100, "telegram_whatsapp": 50,
    "urgent_complex": 40, "normal": 30, "linked_duplicate": 15,
}
COMPLEX_TOPICS = {"heating", "water_supply", "sewerage", "electricity"}


def moment(value):
    return datetime.fromisoformat(value) if value else datetime.now(timezone.utc)


def age_minutes(c):
    return max(0, (datetime.now(timezone.utc) - moment(c["received_at"] or c["ingested_at"])).total_seconds() / 60)


def address_in(text):
    match = re.search(r"\b(Абая|Абай|Байтурсынова|Шевченко|Масанчи|Толе би|Сейфуллина)\s*(?:көшесі\s*)?(\d+[а-яa-z]?(?:/\d+)?)", text, re.I)
    return (match[1].capitalize().replace("Абай", "Абая") + " " + match[2]) if match else None


def symptom(text):
    text = text.lower()
    if any(t in text for t in ["нет воды", "нет холодной воды", "без воды", "без холодной воды", "су жоқ", "сусыз", "отключ", "су тоқта"]):
        return "outage"
    if any(t in text for t in ["теч", "течёт", "прорыв", "ағып", "трубы", "люка"]):
        return "leak"
    return "unknown"


def analyze(c, classifier, topic_services, extra_text=""):
    text = c["text"] + " " + extra_text
    lower = text.lower()
    topic, service, urgency = classifier(text)
    # More specific signals precede broad legacy keyword matches (e.g. street lights vs electricity).
    if any(w in lower for w in ["фонар", "уличного освещ", "көше жары"]):
        topic = "street_lighting"
    elif any(w in lower for w in ["нет воды", "нет холодной воды", "без воды", "без холодной воды", "су жоқ", "сусыз"]):
        topic = "water_supply"
    elif "открыт люк" in lower:
        topic = "sewerage"
    ambiguous = (("труб" in lower and "люк" in lower) or ("құбыр" in lower and "кәріз" in lower))
    urgency = "urgent" if urgency == "urgent" or any(w in lower for w in ["искрит", "гари", "опасно", "открыт люк", "сымы үзілген"]) else "normal"
    confidence = .94 if topic else .32
    if ambiguous:
        topic, confidence = "water_supply", .63
    service = topic_services.get(topic)
    band = "high" if confidence > HIGH_CONFIDENCE else "medium" if confidence >= MEDIUM_CONFIDENCE else "low"
    alternatives = [{"category": topic, "confidence": confidence}] if topic else []
    if ambiguous:
        alternatives.append({"category": "sewerage", "confidence": .31})
    question = ("Уточните: вода течёт из трубы или из канализационного люка?" if ambiguous else
                "Уточните адрес и что именно произошло: нет воды, света или другая проблема?")
    extracted = c.get("address") or address_in(text)
    scope = "Весь дом" if any(w in lower for w in ["весь дом", "бүкіл үй", "во всем доме"]) else "Не уточнён"
    start = re.search(r"\b\d{1,2}:\d{2}\b", text)
    onset = start[0] if start else "С утра" if any(w in lower for w in ["утра", "таңертең"]) else "Не уточнено"
    return {
        "mode": "mock", "training_status": "not_trained", "checkpoint_id": None,
        "confidence_kind": "synthetic_demo", "category": topic, "category_confidence": confidence,
        "urgency": urgency, "urgency_confidence": .91 if urgency == "urgent" else .72,
        "suggested_service": service, "service_name": SERVICE_NAMES.get(service, "Старший оператор") if c["region_id"] == "KZ-ALA" else "Региональная очередь — служба требует проверки",
        "summary": (f"Житель сообщает об отсутствии воды. Адрес: {extracted or 'не указан'}. Масштаб: {scope.lower()}." if topic == "water_supply" and symptom(text) == "outage" else text.strip()[:220]),
        "extracted_address": extracted, "scope": scope, "onset": onset,
        "reasoning_short": "Две возможные причины: нужна проверка оператора." if ambiguous else
            "Правило по словам обращения; место и служба проверяются оператором." if topic else "Недостаточно конкретных признаков для выбора категории.",
        "confidence_band": band, "alternatives": alternatives,
        "clarification_question": question if band != "high" else None,
        "thresholds": {"high": HIGH_CONFIDENCE, "medium": MEDIUM_CONFIDENCE},
    }


def case_weight(case):
    """Synthetic workload weight for one active case, by the documented precedence."""
    if case.get("incident_id"):
        return DEMO_WORKLOAD_WEIGHTS["linked_duplicate"]
    channel = (case.get("channel") or "web").lower()
    if channel == "phone":
        return DEMO_WORKLOAD_WEIGHTS["phone"]
    if channel in {"telegram", "whatsapp"}:
        return DEMO_WORKLOAD_WEIGHTS["telegram_whatsapp"]
    if case.get("priority") == "urgent" or case.get("topic") in COMPLEX_TOPICS:
        return DEMO_WORKLOAD_WEIGHTS["urgent_complex"]
    return DEMO_WORKLOAD_WEIGHTS["normal"]


def operators_with_load(conn):
    rows = []
    for r in conn.execute("SELECT * FROM operators ORDER BY id"):
        op = dict(r)
        for key in ["skills", "languages"]:
            op[key] = json.loads(op[key])
        op["active_count"] = 0
        op["workload"] = op["base_load"] * DEMO_WORKLOAD_WEIGHTS["baseline"]
        op["workload_capacity"] = op["capacity"] * DEMO_WORKLOAD_WEIGHTS["baseline"]
        rows.append(op)
    by_id = {op["id"]: op for op in rows}
    # One pass over active cases instead of a COUNT query per operator.
    for row in conn.execute("""SELECT assigned_operator, channel, priority, topic, incident_id FROM complaints
            WHERE assigned_operator IS NOT NULL AND resolved_at IS NULL AND quarantined = 0"""):
        case = dict(row)
        op = by_id.get(case["assigned_operator"])
        if op is None:
            continue
        op["active_count"] += 1
        op["workload"] += case_weight(case)
    for op in rows:
        op["current_load"] = op["base_load"] + op["active_count"]
        op["workload_ratio"] = round(op["workload"] / op["workload_capacity"], 4) if op["workload_capacity"] else 1.0
    return rows


def _workload(o):
    base = o.get("base_load", o.get("current_load", 0))
    workload = o.get("workload", base * DEMO_WORKLOAD_WEIGHTS["baseline"])
    capacity = o.get("workload_capacity", max(1, o.get("capacity", 1)) * DEMO_WORKLOAD_WEIGHTS["baseline"])
    return workload, capacity


def incoming_weight(c, ai):
    """Cost of the case being routed. Confirmed complaint values win over the demo proposal;
    `ai` may carry the human-selected `priority` / `incident_id` so a manual confirmation is
    costed with what the operator actually chose."""
    return case_weight({"incident_id": c.get("incident_id") or ai.get("incident_id"),
                        "channel": c.get("channel"),
                        "priority": c.get("priority") or ai.get("priority") or ai.get("urgency"),
                        "topic": c.get("topic") or ai.get("category")})


def unavailable_reason(o, incoming=0):
    """None when the operator can take this case; otherwise the demo reason to show.

    Slots stay strict (current_load < capacity). Workload is projected forward: the operator
    must still fit the incoming case, so workload + incoming must not exceed capacity*30.
    """
    if o.get("status") != "online":
        return "Статус: " + str(o.get("status"))
    if o.get("current_load", 0) >= o.get("capacity", 0):
        return f"Слоты заняты: {o.get('current_load')}/{o.get('capacity')}"
    workload, capacity = _workload(o)
    if workload + incoming > capacity:
        return f"Нагрузка: {workload + incoming}/{capacity}"
    return None


def _match_reasons(o, c, ai, service_match):
    reasons = []
    if service_match(o):
        reasons.append(f"Служба {ai.get('suggested_service')} · навык {ai.get('category')}")
    if c.get("language") in o.get("languages", []):
        reasons.append(f"Язык {c['language']}")
    if c.get("district") and o.get("district") == c["district"]:
        reasons.append(f"Район {c['district']}")
    if o.get("department") == "general":
        reasons.append("Общая очередь · старший оператор")
    return reasons


def route(c, ai, operators):
    """Same four-level demo fallback, now workload-aware.

    Levels: служба+район+язык -> язык -> служба -> общая очередь. An operator is only offered
    while both slots (current_load < capacity) and assigned workload (workload < capacity*30)
    have room; inside one level the lowest projected ratio ((workload + incoming case cost) /
    capacity*30) wins. `candidates` explains every operator, including rejected alternatives.
    """
    incoming = incoming_weight(c, ai)

    def service_match(o):
        return (c.get("region_id") == "KZ-ALA" and o.get("department") == ai.get("suggested_service")
                and ai.get("category") in o.get("skills", []))

    def language_match(o):
        return service_match(o) and c.get("language") in o.get("languages", [])

    def district_match(o):
        return language_match(o) and bool(c.get("district")) and o.get("district") == c["district"]

    stages = [([o for o in operators if district_match(o)], "Подходит по службе, району и языку."),
              ([o for o in operators if language_match(o)], "Подходит по службе и языку; другой район."),
              ([o for o in operators if service_match(o)], "Подходит по службе; язык и район требуют проверки."),
              ([o for o in operators if o.get("department") == "general"],
               "Старший оператор: профильная служба недоступна или не определена.")]
    matched_stage = {}
    for index, (group, _) in enumerate(stages, 1):
        for o in group:
            matched_stage.setdefault(o["id"], index)
    rejected = {o["id"]: unavailable_reason(o, incoming) for o in operators}
    chosen, chosen_level, chosen_reason = None, None, None
    for level, (group, reason) in enumerate(stages, 1):
        choices = [o for o in group if not rejected[o["id"]]]
        if choices:
            chosen = min(choices, key=lambda o: ((_workload(o)[0] + incoming) / _workload(o)[1], o["id"]))
            chosen_level, chosen_reason = level, reason
            break
    candidates = []
    for o in operators:
        workload, capacity = _workload(o)
        stage = matched_stage.get(o["id"])
        entry = {"id": o["id"], "name": o["name"], "load": o["current_load"],
                 "current_load": o["current_load"], "capacity": o["capacity"],
                 "workload": workload, "workload_capacity": capacity, "stage": stage,
                 "eligible": not rejected[o["id"]], "match_reasons": _match_reasons(o, c, ai, service_match)}
        if rejected[o["id"]]:
            entry["reason"] = rejected[o["id"]]
        elif chosen is not None and o["id"] == chosen["id"]:
            entry["reason"] = "Выбран: " + chosen_reason
        elif stage is None:
            entry["reason"] = "Профиль службы, язык или район не совпали"
        elif chosen is None:
            entry["reason"] = "Этап недоступен или перегружен"
        elif stage == chosen_level:
            entry["reason"] = "Тот же этап, но выше нагрузка"
        else:
            entry["reason"] = f"Этап {stage} ниже приоритета этапа {chosen_level}"
        candidates.append(entry)
    if chosen is None:
        return {"operator": None, "reason": "Все подходящие операторы заняты. Оставить в общей очереди.",
                "level": 4, "route_type": "queue", "queue": True, "incoming_weight": incoming,
                "candidates": candidates}
    return {"operator": chosen, "reason": chosen_reason, "level": chosen_level,
            "route_type": "primary" if chosen_level < 4 else "fallback", "queue": False,
            "incoming_weight": incoming, "candidates": candidates}


def tokens(text):
    words = re.findall(r"[\w]+", text.lower())
    return set(w for w in words if len(w) > 2)


def related_cases(c, ai, rows, classifier, topic_services):
    if not ai["category"] or c.get("incident_dismissed"):
        return []
    found = []
    # ponytail: exact scan for a 50-case demo; add indexed candidate retrieval beyond 10k active rows.
    for other in rows:
        if other["id"] == c["id"] or other["resolved_at"] or other.get("quarantined"):
            continue
        if other["region_id"] != c["region_id"] or not c.get("district") or other.get("district") != c["district"]:
            continue
        minutes = abs((moment(c["received_at"] or c["ingested_at"]) - moment(other["received_at"] or other["ingested_at"])).total_seconds() / 60)
        if minutes > 360:
            continue
        topic = other["topic"] or other["proposed_topic"] or analyze(other, classifier, topic_services)["category"]
        if topic != ai["category"] or symptom(other["text"]) != symptom(c["text"]):
            continue
        a, b = tokens(c["text"]), tokens(other["text"])
        lexical = len(a & b) / max(1, len(a | b))
        same_address = ai["extracted_address"] and ai["extracted_address"] == (other.get("address") or address_in(other["text"]))
        if lexical < .12 and not same_address and symptom(c["text"]) != "outage":
            continue
        found.append({"id": other["id"], "text": other["text"], "address": other.get("address"),
                      "incident_id": other["incident_id"], "similarity": round(.55 + .25 * lexical + .1 * bool(same_address), 2),
                      "reason": "Одна категория, район, симптом и временное окно; требуется подтверждение."})
    return sorted(found, key=lambda r: (-r["similarity"], r["id"]))


def risk_for(c, rows):
    repeats = [r for r in rows if c.get("sender_key") and r.get("sender_key") == c["sender_key"]
               and r["text"].strip().lower() == c["text"].strip().lower()
               and abs((moment(r["ingested_at"]) - moment(c["ingested_at"])).total_seconds()) <= 120]
    reasons = []
    if len(repeats) >= 3:
        reasons.append(f"{len(repeats)} одинаковых сообщений от одного отправителя за 2 минуты")
    if any(x in c["text"].lower() for x in ["купите рекламу", "быстрый заработок", "promo.example"]):
        reasons.append("Рекламные формулировки и внешняя ссылка")
    return {"score": .87 if reasons else 0, "reasons": reasons, "kind": "synthetic_rule_score"}


def queue_state(c, ai, risk, linked):
    waiting = round(age_minutes(c))
    sla = 15 if (c["priority"] or ai["urgency"]) == "urgent" else 60
    remaining = round(sla - age_minutes(c))
    flags = []
    if risk["reasons"] and not c.get("safety_reviewed"):
        flags.append("spam_suspected")
    if linked and not c["incident_id"]:
        flags.append("duplicate_candidate")
    if ai["confidence_band"] == "low":
        flags.append("needs_clarification")
    if any(s in c["text"].lower() for s in ["гороскоп", "заказать пиццу", "купите рекламу"]):
        flags.append("out_of_scope")
    factors = {"urgency": 70 if ai["urgency"] == "urgent" or c["priority"] == "urgent" else 0,
               "sla_risk": 40 if remaining < 10 else 0, "waiting": min(20, waiting // 3),
               "incident": 12 if linked else 0, "review": 10 if ai["confidence_band"] != "high" else 0}
    group = "urgent" if factors["urgency"] else "attention" if flags or remaining < 10 or ai["confidence_band"] != "high" else "normal"
    if c["decision_status"] == "confirmed":
        group = "awaiting_service"
    if c["decision_status"] == "needs_clarification":
        group = "awaiting_citizen"
    if c.get("quarantined"):
        group = "quarantine"
    if c["resolved_at"]:
        group = "resolved"
    return {"group": group, "flags": flags, "priority_score": sum(factors.values()), "priority_factors": factors,
            "waiting_minutes": waiting, "sla_remaining": remaining if group in {"urgent", "attention", "normal"} else None}
