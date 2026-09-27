"""Safe public views and duplicate deflection for synthetic complaints."""
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from cities import CITY_BY_CODE
from triage import SERVICE_NAMES, analyze, address_in, symptom, tokens


class SimilarRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    region_id: str
    city_code: str | None = Field(default=None, pattern=r"^\d{9}$")
    address: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def location_is_complete(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите широту и долготу вместе")
        return self


class Subscription(BaseModel):
    subscriber_key: str = Field(min_length=8, max_length=100)


def distance_metres(a_lat, a_lng, b_lat, b_lng):
    earth = 6_371_000
    d_lat, d_lng = radians(b_lat - a_lat), radians(b_lng - a_lng)
    value = sin(d_lat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(d_lng / 2) ** 2
    return round(earth * 2 * asin(sqrt(min(1, value))))


def attach_public_issue_routes(router: APIRouter, get_connection, classifier, topic_services,
                               valid_regions, topic_names):
    def category(item):
        return item.get("topic") or item.get("proposed_topic") or analyze(
            item, classifier, topic_services
        )["category"]

    @router.get("/public/complaints")
    def public_complaints():
        with get_connection() as conn:
            rows = conn.execute("""SELECT c.id, substr(c.text, 1, 1000) AS text, c.region_id,
                    c.city_code, c.district, c.latitude, c.longitude,
                    COALESCE(c.topic, c.proposed_topic) AS topic, c.service_id, c.incident_id,
                    CASE WHEN c.resolved_at IS NOT NULL THEN 'resolved' ELSE c.decision_status END AS status,
                    COALESCE(c.received_at, c.ingested_at) AS registered_at,
                    COALESCE((SELECT MAX(a.occurred_at) FROM audit_events a WHERE a.complaint_id = c.id),
                             c.received_at, c.ingested_at) AS last_updated,
                    (SELECT i.next_update FROM incidents i WHERE i.id = c.incident_id) AS next_update,
                    (SELECT COUNT(*) FROM complaint_subscriptions s WHERE s.complaint_id = c.id) AS subscribers,
                    EXISTS(SELECT 1 FROM complaint_photos p WHERE p.complaint_id = c.id) AS has_photo
                FROM complaints c
                WHERE c.data_origin = 'synthetic' AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
                ORDER BY COALESCE(c.received_at, c.ingested_at) DESC""").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["topic"] = category(item)
            item["city"] = CITY_BY_CODE.get(item.pop("city_code"), {}).get("name_ru")
            item["topic_name"] = topic_names.get(item["topic"], "Другая проблема")
            item["service_name"] = SERVICE_NAMES.get(item.pop("service_id"))
            item["latitude"], item["longitude"] = round(item["latitude"], 3), round(item["longitude"], 3)
            item["location_precision_m"] = 100
            item["has_photo"] = bool(item["has_photo"])
            items.append(item)
        return {"items": items, "count": len(items), "data_origin": "synthetic"}

    @router.post("/public/similar")
    def similar(req: SimilarRequest):
        if req.region_id not in valid_regions or not req.text.strip():
            raise HTTPException(422, "Укажите текст обращения и известный регион")
        city = CITY_BY_CODE.get(req.city_code) if req.city_code else None
        if req.city_code and (not city or city["region_id"] != req.region_id):
            raise HTTPException(422, "Город не соответствует выбранному региону")
        probe = {"text": req.text.strip(), "address": req.address, "region_id": req.region_id}
        probe_topic, probe_symptom = category(probe), symptom(req.text)
        if not probe_topic:
            return {"items": [], "count": 0, "method": "category + symptom + place"}
        with get_connection() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT c.*,
                    (SELECT COUNT(*) FROM complaint_subscriptions s WHERE s.complaint_id = c.id) AS subscribers
                FROM complaints c WHERE c.data_origin = 'synthetic' AND c.region_id = ?
                  AND c.resolved_at IS NULL AND c.quarantined = 0""", (req.region_id,))]
        probe_address = (req.address or address_in(req.text) or "").strip().casefold()
        radius = 2500 if probe_symptom == "outage" else 500 if probe_topic in {
            "roads", "street_lighting", "waste_management", "sewerage"
        } else 900
        found = []
        for row in rows:
            if req.city_code and row.get("city_code") and req.city_code != row["city_code"]:
                continue
            if category(row) != probe_topic or symptom(row["text"]) != probe_symptom:
                continue
            row_address = (row.get("address") or address_in(row["text"]) or "").strip().casefold()
            same_address = bool(probe_address and probe_address == row_address)
            metres = None
            if req.latitude is not None and row.get("latitude") is not None:
                metres = distance_metres(req.latitude, req.longitude, row["latitude"], row["longitude"])
            nearby = metres is not None and metres <= radius
            district_outage = bool(probe_symptom == "outage" and req.district and
                                   req.district.casefold() == (row.get("district") or "").casefold())
            if not (same_address or nearby or district_outage):
                continue
            overlap = len(tokens(req.text) & tokens(row["text"])) / max(1, len(tokens(req.text) | tokens(row["text"])))
            reason = ("Тот же адрес и тип проблемы" if same_address else
                      f"Похожая проблема примерно в {metres} м" if nearby else
                      "Такой же массовый сбой в этом районе")
            found.append({"id": row["id"], "text": row["text"][:280], "district": row.get("district"),
                          "status": "confirmed" if row.get("decision_status") == "confirmed" else "pending",
                          "registered_at": row.get("received_at") or row["ingested_at"],
                          "incident_id": row.get("incident_id"), "subscribers": row["subscribers"],
                          "distance_m": metres, "reason": reason,
                          "_score": (2 if same_address else 1 if nearby else 0) + overlap})
        found.sort(key=lambda item: (-item["_score"], item["distance_m"] or 10**9, item["id"]))
        for item in found:
            item.pop("_score")
        return {"items": found[:3], "count": min(3, len(found)), "method": "category + symptom + place"}

    @router.post("/public/complaints/{cid}/subscribe")
    def subscribe(cid: str, req: Subscription):
        with get_connection() as conn:
            cid = cid.strip().upper()
            if not conn.execute("SELECT id FROM complaints WHERE id = ? AND data_origin = 'synthetic'", (cid,)).fetchone():
                raise HTTPException(404, "Обращение не найдено")
            conn.execute("INSERT OR IGNORE INTO complaint_subscriptions VALUES (?, ?, ?)",
                         (cid, req.subscriber_key, datetime.now(timezone.utc).isoformat()))
            count = conn.execute("SELECT COUNT(*) FROM complaint_subscriptions WHERE complaint_id = ?", (cid,)).fetchone()[0]
        return {"subscribed": True, "subscribers": count, "delivery": "demo_only"}

    @router.get("/public/complaints/{cid}/photo")
    def public_photo(cid: str):
        with get_connection() as conn:
            row = conn.execute("""SELECT p.mime_type, p.content FROM complaint_photos p
                JOIN complaints c ON c.id = p.complaint_id
                WHERE p.complaint_id = ? AND c.data_origin = 'synthetic'""", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Фото не найдено")
        return Response(row["content"], media_type=row["mime_type"],
                        headers={"Cache-Control": "public, max-age=3600"})
