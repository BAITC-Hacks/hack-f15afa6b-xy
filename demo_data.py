"""Explicitly synthetic operator scenarios; never imports organizer records."""
import json
from datetime import datetime, timedelta, timezone

DEMO_TEXT = "Добрый день, на Абая 44 с утра нет воды, весь дом без воды, когда включат?"
DEMO_DISTRICT = "Алмалинский"


def init_workspace(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(complaints)")}
    for name, definition in {
        "address": "TEXT", "city_code": "TEXT", "district": "TEXT", "channel": "TEXT DEFAULT 'web'",
        "latitude": "REAL", "longitude": "REAL", "location_accuracy_m": "REAL",
        "sender_key": "TEXT", "assigned_operator": "TEXT", "first_response_at": "TEXT", "related_to": "TEXT",
        "quarantined": "INTEGER NOT NULL DEFAULT 0", "safety_reviewed": "INTEGER NOT NULL DEFAULT 0",
        "incident_dismissed": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE complaints ADD COLUMN {name} {definition}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            region_id TEXT NOT NULL, district TEXT NOT NULL, service_id TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, next_update TEXT,
            severity INTEGER NOT NULL, data_origin TEXT NOT NULL DEFAULT 'synthetic'
        );
        CREATE TABLE IF NOT EXISTS operators (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, skills TEXT NOT NULL,
            languages TEXT NOT NULL, department TEXT NOT NULL, district TEXT,
            status TEXT NOT NULL, base_load INTEGER NOT NULL, capacity INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incident_subscriptions (
            incident_id TEXT NOT NULL, subscriber_key TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (incident_id, subscriber_key)
        );
        CREATE TABLE IF NOT EXISTS playbook_previews (
            token TEXT PRIMARY KEY, complaint_id TEXT NOT NULL, playbook_id TEXT NOT NULL,
            request TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
            executed_at TEXT, result TEXT, actor TEXT
        );
    """)
    preview_columns = {r[1] for r in conn.execute("PRAGMA table_info(playbook_previews)")}
    if "actor" not in preview_columns:
        conn.execute("ALTER TABLE playbook_previews ADD COLUMN actor TEXT")


def seed_workspace(conn, topic_services):
    now = datetime.now(timezone.utc)
    operators = [
        ("op-aidana", "Айдана К.", ["water_supply", "sewerage"], ["ru", "kk"], "srv_vodokanal", DEMO_DISTRICT, "online", 2, 5),
        ("op-timur", "Тимур С.", ["water_supply"], ["ru"], "srv_vodokanal", "Бостандыкский", "online", 3, 6),
        ("op-dana", "Дана М.", ["electricity", "street_lighting"], ["ru", "kk"], "srv_energo", "Медеуский", "online", 1, 5),
        ("op-nurlan", "Нурлан А.", ["roads", "landscaping", "waste_management"], ["ru", "kk"], "srv_roads", DEMO_DISTRICT, "online", 4, 6),
        ("op-senior", "Мария В.", list(topic_services), ["ru", "kk", "mixed", "unknown"], "general", None, "online", 1, 8),
        ("op-away", "Арман Т.", ["water_supply"], ["ru", "kk"], "srv_vodokanal", DEMO_DISTRICT, "away", 0, 5),
    ]
    for op in operators:
        conn.execute("INSERT OR IGNORE INTO operators VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (*op[:2], json.dumps(op[2]), json.dumps(op[3]), *op[4:]))
    conn.execute("""INSERT OR IGNORE INTO incidents
        (id, title, category, region_id, district, service_id, status, started_at, next_update, severity, data_origin)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic')""", (
        "INC-204", "Отключение воды — Алмалинский район", "water_supply", "KZ-ALA", DEMO_DISTRICT,
        "srv_vodokanal", "Работы ведутся", (now - timedelta(minutes=38)).isoformat(),
        (now + timedelta(minutes=45)).isoformat(), 2,
    ))
    cases = []
    for i in range(17):
        street = ["Абая", "Байтурсынова", "Шевченко", "Масанчи"][i % 4]
        address = f"{street} {44 + i * 2}"
        text = (f"{address}: с утра нет воды, весь дом без холодной воды. Просим проверить водоснабжение."
                if i % 3 else f"{address} үйде су жоқ, таңертеңнен бері бүкіл үй сусыз қалды.")
        cases.append((2400 + i, text, address, "kk" if i % 3 == 0 else "ru", "water_supply", 38 - i * 2, "INC-204", None))
    for i in range(5):
        cases.append((2420 + i, "Купите рекламу! Быстрый заработок: promo.example", None, "ru", None, 2 - i * .3, None, "synthetic-spam-sender"))
    cases.extend([
        (2430, "На Абая 44 течёт вода из трубы или люка, не понимаю откуда", "Абая 44", "ru", None, 12, None, None),
        (2431, "Здесь опять эта проблема, помогите", None, "ru", None, 9, None, None),
        (2432, "Аулада су ағып жатыр, құбыр ма әлде кәріз бе?", None, "kk", None, 7, None, None),
        (2440, "Срочно! На Толе би 118 искрит электрощит, запах гари в подъезде", "Толе би 118", "ru", None, 12, None, None),
        (2441, "На Сейфуллина 92 открыт люк на проезжей части, опасно для людей", "Сейфуллина 92", "ru", None, 7, None, None),
        (2442, "Абай 80: электр сымы үзілген, балаларға қауіп бар", "Абая 80", "kk", None, 5, None, None),
        (2450, "На улице Абая 78 не горят фонари уличного освещения", "Абая 78", "ru", None, 18, None, None),
        (2451, "Байтурсынова 60: қоқыс жәшіктері толған, қоқыс шығарылмады", "Байтурсынова 60", "kk", None, 22, None, None),
    ])
    for i, text, address, lang, topic, minutes, incident, sender in cases:
        at = (now - timedelta(minutes=minutes)).isoformat()
        conn.execute("""INSERT OR IGNORE INTO complaints
            (id, data_origin, source_system, text, region_id, received_at, ingested_at, language,
             topic, service_id, priority, decision_status, incident_id, address, district, channel, sender_key)
            VALUES (?, 'synthetic', 'operator_demo_v1', ?, 'KZ-ALA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"PULSE-{i}", text, at, at, lang, topic, topic_services.get(topic),
             "normal" if incident else None, "confirmed" if incident else "pending", incident,
             address, DEMO_DISTRICT, ["web", "phone", "telegram"][i % 3], sender))
