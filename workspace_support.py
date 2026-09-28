"""Schemas and boundary helpers shared by the operator workspace routes."""
import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from auth import current_actor


def ai_evidence():
    evidence_dir = Path(__file__).with_name("training") / "evidence"
    files = {
        "laya": evidence_dir / "laya-gpu-hardcases-shadow-20260926.json",
        "rejected": evidence_dir / "laya-gpu-phase0-rejected-20260926.json",
        "stt": evidence_dir / "voice-stt-gpu-20260927.json",
        "tts": evidence_dir / "voice-tts-ab-20260927.json",
        "similarity": evidence_dir / "similarity-e5-gpu-20260928.json",
    }
    try:
        documents = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in files.items()}
        laya, stt, tts, similarity = (
            documents["laya"], documents["stt"], documents["tts"], documents["similarity"]
        )
        metrics = laya["experiment"]["aggregate_test_metrics"]
        speech = {item["language"]: item for item in stt["evaluation"]["results"]}
        omnivoice = [item for item in tts["roundtrip_evaluation"]["results"]
                     if item["engine"] == "omnivoice"]
        return {
            "available": True, "stage": laya["promotion"]["stage"],
            "checkpoint_id": laya["promotion"]["version"], "model": laya["source"]["base_model"],
            "dataset_records": laya["dataset"]["records"], "trained_runs": len(laya["experiment"]["runs"]),
            "rejected_runs": len(documents["rejected"]["runs"]),
            "hardware": {"provider": laya["compute"]["provider"], "gpu_count": laya["compute"]["gpus"],
                         "gpu_model": laya["compute"]["gpu_model"]},
            "category_accuracy": {"baseline": metrics["baseline_category_accuracy"]["mean"],
                                  "trained": metrics["trained_category_accuracy"]["mean"]},
            "category_macro_f1": metrics["trained_category_macro_f1"]["mean"],
            "calibrated_ece": metrics["checkpoint_calibrated_ece"]["mean"],
            "stt": {"model": stt["model"]["id"], "runs_per_language": stt["evaluation"]["runs_per_language"],
                    "ru_median_ms": speech["ru"]["median_ms"], "kk_median_ms": speech["kk"]["median_ms"]},
            "tts": {"engine": "OmniVoice", "ru_kk_roundtrip_cer": max(item["character_error_rate"] for item in omnivoice)},
            "similarity": {
                "model": similarity["base_model"],
                "selected_run": similarity["selection"]["selected_run"],
                "checkpoint_sha256": similarity["selection"]["checkpoint_sha256"],
                "test": {"baseline": similarity["baseline"]["test"], "trained": similarity["trained"]["test"]},
                "cross_language_test": {
                    "baseline": similarity["baseline"]["cross_language_test"],
                    "trained": similarity["trained"]["cross_language_test"],
                },
                "claim_boundary": similarity["claim_boundary"],
            },
            "evidence_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()},
            "claim_boundary": laya["claim_boundary"], "next_gate": laya["activation"]["next_gate"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False}


class Intake(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    region_id: str
    city_code: str | None = Field(default=None, min_length=9, max_length=9, pattern=r"^\d{9}$")
    language: Literal["ru", "kk", "mixed", "unknown"] = "ru"
    address: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    district: str | None = Field(default=None, max_length=100)
    channel: Literal["web", "phone", "telegram", "whatsapp"] = "web"
    sender_key: str | None = Field(default=None, max_length=100)
    photo_data: str | None = Field(default=None, max_length=5_600_000)
    video_data: str | None = Field(default=None, max_length=16_800_000)
    public_consent: bool = False

    @model_validator(mode="after")
    def location_is_complete(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите широту и долготу вместе")
        if self.location_accuracy_m is not None and self.latitude is None:
            raise ValueError("Точность требует координаты")
        if self.photo_data and self.video_data:
            raise ValueError("Прикрепите один файл: фото или видео")
        return self


def decode_photo(value):
    if not value:
        return None
    try:
        header, encoded = value.split(",", 1)
        if header not in {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"}:
            raise ValueError
        declared = header[5:-7]
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Не удалось прочитать фото") from None
    detected = ("image/jpeg" if content.startswith(b"\xff\xd8\xff") else
                "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else
                "image/webp" if content.startswith(b"RIFF") and content[8:12] == b"WEBP" else None)
    if declared not in {"image/jpeg", "image/png", "image/webp"} or detected != declared:
        raise HTTPException(422, "Допустимы фото JPEG, PNG или WebP")
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(422, "Фото должно быть не больше 4 МБ")
    return detected, content


def decode_video(value):
    if not value:
        return None
    try:
        header, encoded = value.split(",", 1)
        if header not in {"data:video/mp4;base64", "data:video/webm;base64"}:
            raise ValueError
        declared = header[5:-7]
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Не удалось прочитать видео") from None
    detected = ("video/mp4" if len(content) >= 12 and content[4:8] == b"ftyp" else
                "video/webm" if content.startswith(b"\x1aE\xdf\xa3") else None)
    if detected != declared:
        raise HTTPException(422, "Допустимы видео MP4 или WebM")
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(422, "Видео должно быть не больше 12 МБ")
    return detected, content


class Decision(BaseModel):
    topic: str
    priority: Literal["urgent", "normal"]
    operator_id: str | None = None
    incident_id: str | None = None


class Link(BaseModel):
    incident_id: str | None = None
    separate: bool = False


class Safety(BaseModel):
    quarantine: bool


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Moderation(BaseModel):
    status: Literal["approved", "rejected"]
    public_text: str | None = Field(default=None, max_length=1000)


class Subscription(BaseModel):
    subscriber_key: str = Field(min_length=8, max_length=100)


def event(conn, cid, kind, payload, actor=None, event_id=None):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (event_id or "evt-" + uuid.uuid4().hex, cid, kind, now, now, actor or current_actor(),
                  json.dumps(payload, ensure_ascii=False)))


def suggested_response(c):
    if c["language"] == "kk":
        return ("Өтінішіңіз тіркелді. Ол " + c["incident_id"] + " оқиғасымен байланыстырылды. Жауапты қызметке хабар берілді. Орындалу мерзімі әлі расталған жоқ."
                if c["incident_id"] else "Өтінішіңіз тіркелді. Оператор ақпаратты тексеріп, жауапты қызметке бағыттайды. Орындалу мерзімі әлі расталған жоқ.")
    return ("Ваше обращение зарегистрировано. Оно связано с инцидентом " + c["incident_id"] + ". Ответственная служба уведомлена в демо-системе. Срок устранения пока не подтверждён."
            if c["incident_id"] else "Ваше обращение зарегистрировано. Оператор проверит информацию и направит её в ответственную службу. Срок устранения пока не подтверждён.")
