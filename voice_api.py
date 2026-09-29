"""Private speech bridge for the citizen voice intake."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest
from pydantic import BaseModel, Field

from cities import CITIES
from realtime_voice import RealtimeConfig, TranscriptRequest, attach_realtime_route


MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_TTS_AUDIO_BYTES = 2 * 1024 * 1024
CACHED_TTS_MODEL = "k2-fsa/OmniVoice cached prompts"
VOICE_PROMPTS = {
    "ru_problem": ("ru", "Расскажите, что произошло. Говорите до тридцати секунд."),
    "ru_address": ("ru", "Теперь назовите адрес или ближайший ориентир."),
    "ru_review": ("ru", "Проверьте текст, точку на карте и приложите фото или видео."),
    "kk_problem": ("kk", "Не болғанын айтып беріңіз. Отыз секундқа дейін сөйлеңіз."),
    "kk_address": ("kk", "Енді мекенжайды немесе жақын жердегі нысанды айтыңыз."),
    "kk_review": ("kk", "Мәтінді, картадағы орынды тексеріп, фото немесе видео тіркеңіз."),
    "mixed_problem": ("mixed", "Расскажите о проблеме. Мәселе туралы айтып беріңіз."),
    "mixed_address": ("mixed", "Теперь назовите адрес. Енді мекенжайды айтыңыз."),
    "mixed_review": ("mixed", "Проверьте данные. Мәліметтерді тексеріңіз."),
    "mixed_language_retry": ("mixed", "Не удалось определить язык. Тілді таңдаңыз немесе фразаны қайталаңыз."),
}

KAZAKH_LETTERS = set("әғқңөұүһі")
KAZAKH_WORDS = {
    "біз", "бүгін", "және", "жоқ", "кеше", "көше", "көмектесіңіз", "қала", "қаласы",
    "қашан", "қауіпті", "мекенжай", "мәселе", "не", "өтінемін", "су", "таңертең",
    "тұр", "үй", "үйде", "шықты", "бар", "болды", "басталды",
}
RUSSIAN_WORDS = {
    "адрес", "была", "было", "был", "в", "вчера", "вода", "воды", "город", "где",
    "дом", "доме", "есть", "и", "когда", "мусор", "мы", "на", "началось", "не", "нет",
    "опасно", "пожалуйста", "помогите", "проблема", "с", "сегодня", "свет", "улица", "утра",
}

NUMBER_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "первый": 1, "первая": 1, "первое": 1,
    "два": 2, "две": 2, "второй": 2, "вторая": 2, "три": 3, "третий": 3,
    "четыре": 4, "четвертый": 4, "четвёртый": 4, "пять": 5, "пятый": 5,
    "шесть": 6, "шестой": 6, "семь": 7, "седьмой": 7, "восемь": 8, "восьмой": 8,
    "девять": 9, "девятый": 9, "десять": 10, "десятый": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200,
    "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
    "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
    "нөл": 0, "бір": 1, "бірінші": 1, "екі": 2, "екінші": 2, "үш": 3, "үшінші": 3,
    "төрт": 4, "төртінші": 4, "бес": 5, "бесінші": 5, "алты": 6, "алтыншы": 6,
    "жеті": 7, "жетінші": 7, "сегіз": 8, "сегізінші": 8, "тоғыз": 9, "тоғызыншы": 9,
    "он": 10, "оныншы": 10, "жиырма": 20, "отыз": 30, "қырық": 40, "елу": 50,
    "алпыс": 60, "жетпіс": 70, "сексен": 80, "тоқсан": 90, "жүз": 100, "жүзінші": 100,
}
NUMBER_SEQUENCE = re.compile(
    r"\b(?:" + "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True)) +
    r")(?:[ -]+(?:" + "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True)) + r"))*\b",
    re.IGNORECASE,
)
CITY_ALIASES = {
    "нур султан": "710000000", "нурсултан": "710000000",
    "алма ата": "750000000", "алмаата": "750000000",
}
CITY_CUES = {"адрес", "в", "во", "г", "город", "городе", "города",
             "кала", "каласы", "каласында"}


def normalize_address_numbers(text: str) -> str:
    def replace(match: re.Match) -> str:
        words = re.split(r"[ -]+", match.group().lower())
        values = [NUMBER_WORDS[word] for word in words]
        if len(values) > 1 and all(value < 10 for value in values):
            return "".join(str(value) for value in values)
        total = current = 0
        for word, value in zip(words, values):
            if word in {"жүз", "жүзінші"}:
                current = max(1, current) * 100
            elif value >= 100:
                total += value
            else:
                current += value
        return str(total + current)

    return NUMBER_SEQUENCE.sub(replace, text)


def normalize_city_text(text: str) -> str:
    table = str.maketrans("ёқғңөұүіһ", "екгноууих")
    return re.sub(r"[^a-zа-я0-9]+", " ", text.lower().translate(table)).strip()


def one_edit_apart(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) >= len(right):
            i += 1
        if len(right) >= len(left):
            j += 1
    return edits + (i < len(left) or j < len(right)) <= 1


def detect_spoken_city(text: str) -> dict | None:
    value = normalize_city_text(text)
    tokens = value.split()
    by_code = {city["code"]: city for city in CITIES}
    variants = []
    for city in CITIES:
        for name in {city["name_ru"], city["name_kk"]}:
            variants.append((normalize_city_text(name), city))
    variants.extend((alias, by_code[code]) for alias, code in CITY_ALIASES.items())
    variants.sort(key=lambda item: len(item[0]), reverse=True)
    for variant, city in variants:
        if value == variant or value.startswith(variant + " ") or value.startswith("адрес " + variant + " "):
            return city
    for index, token in enumerate(tokens):
        if token not in CITY_CUES:
            continue
        tail = tokens[index + 1:index + 4]
        for variant, city in variants:
            count = len(variant.split())
            candidate = " ".join(tail[:count])
            if candidate and (candidate == variant or one_edit_apart(candidate, variant)):
                return city
    return None


def clean_spoken_address(text: str, city: dict | None) -> str:
    if not city:
        return text
    words = list(re.finditer(r"[\w-]+", text, re.UNICODE))
    normalized = [normalize_city_text(word.group()) for word in words]
    start = 0
    while start < len(normalized) and normalized[start] in CITY_CUES:
        start += 1
    variants = {normalize_city_text(city["name_ru"]), normalize_city_text(city["name_kk"])}
    variants.update(alias for alias, code in CITY_ALIASES.items() if code == city["code"])
    for variant in sorted(variants, key=len, reverse=True):
        count = len(variant.split())
        candidate = " ".join(normalized[start:start + count])
        if not candidate or (candidate != variant and not one_edit_apart(candidate, variant)):
            continue
        end = start + count
        while end < len(normalized) and normalized[end] in CITY_CUES:
            end += 1
        cleaned = text[words[end - 1].end():].lstrip(" ,.:;—–-")
        return cleaned or text
    return text


class VoiceRequest(BaseModel):
    audio_data: str = Field(max_length=2_800_000)
    language: Literal["auto", "ru", "kk", "mixed"] = "auto"
    hint_language: Literal["ru", "kk"] | None = None
    field: Literal["problem", "address"]


class SpeechRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=32)


class DraftAnalysisRequest(BaseModel):
    text: str = Field(min_length=3, max_length=10000)
    language: Literal["auto", "ru", "kk", "mixed"] = "auto"
    region_id: str = Field(default="KZ-ALA", pattern=r"^KZ-[A-Z]{3}$")


def detect_text_language(text: str) -> dict:
    words = re.findall(r"[а-яёәғқңөұүһі]+", text.lower())
    scores = {"ru": 0, "kk": 0}
    last_signal = None
    for word in words:
        if any(letter in KAZAKH_LETTERS for letter in word):
            scores["kk"] += 3
            last_signal = "kk"
        if word in KAZAKH_WORDS:
            scores["kk"] += 1
            last_signal = "kk"
        if word in RUSSIAN_WORDS:
            scores["ru"] += 1
            last_signal = "ru"
    strongest = max(scores, key=scores.get)
    weakest = "kk" if strongest == "ru" else "ru"
    total = scores["ru"] + scores["kk"]
    if scores[strongest] < 2 or not total:
        return {"language": "unknown", "response_language": None, "confidence": 0.0,
                "source": "text", "needs_language_choice": True}
    if scores[weakest] >= 2 and scores[weakest] / scores[strongest] >= .45:
        return {"language": "mixed", "response_language": last_signal or strongest,
                "confidence": round(scores[weakest] / total, 3), "source": "text",
                "needs_language_choice": False}
    confidence = scores[strongest] / total
    if confidence < .67:
        return {"language": "unknown", "response_language": None,
                "confidence": round(confidence, 3), "source": "text",
                "needs_language_choice": True}
    return {"language": strongest, "response_language": strongest,
            "confidence": round(confidence, 3), "source": "text",
            "needs_language_choice": False}


def resolve_language(text: str, requested: str = "auto", provider_language=None,
                     provider_confidence=None) -> dict:
    if requested in {"ru", "kk"}:
        return {"language": requested, "response_language": requested, "confidence": 1.0,
                "source": "manual", "needs_language_choice": False}
    detected = str(provider_language or "").lower()
    if detected in {"ru", "kk"}:
        confidence = provider_confidence if isinstance(provider_confidence, (int, float)) else 1.0
        return {"language": detected, "response_language": detected,
                "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
                "source": "provider", "needs_language_choice": False}
    if detected == "mixed":
        result = detect_text_language(text)
        result.update(language="mixed", source="provider", needs_language_choice=False)
        result["response_language"] = result["response_language"] or "ru"
        return result
    return detect_text_language(text)


def decode_wav_data(value: str) -> bytes:
    try:
        header, encoded = value.split(",", 1)
        if header != "data:audio/wav;base64":
            raise ValueError
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Запись должна быть WAV-аудио") from None
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(422, "Голосовая запись должна быть не длиннее 30 секунд")
    if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
        raise HTTPException(422, "Не удалось прочитать голосовую запись")
    return content


def cached_prompt(cache_dir: Path, prompt: str) -> bytes | None:
    path = cache_dir / f"{prompt}.wav"
    if not path.is_file():
        return None
    content = path.read_bytes()
    if (len(content) < 44 or len(content) > MAX_TTS_AUDIO_BYTES or
            not content.startswith(b"RIFF") or content[8:12] != b"WAVE"):
        return None
    return content


def build_voice_router(analyze_draft: Callable[[dict], dict] | None = None,
                       topics: list[dict] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/voice")
    stt_url = os.environ.get("P109_STT_BASE_URL", "").strip().rstrip("/")
    tts_url = os.environ.get("P109_TTS_BASE_URL", "").strip().rstrip("/")
    stt_timeout = float(os.environ.get("P109_STT_TIMEOUT", "35"))
    tts_timeout = float(os.environ.get("P109_TTS_TIMEOUT", "35"))
    stt_key = os.environ.get("P109_STT_API_KEY")
    tts_key = os.environ.get("P109_TTS_API_KEY")
    realtime = RealtimeConfig.from_env(stt_timeout)
    cache_dir = Path(os.environ.get(
        "P109_VOICE_CACHE_DIR", Path(__file__).with_name("static") / "voice"
    )).expanduser()
    prompt_cache = {name: cached_prompt(cache_dir, name) for name in VOICE_PROMPTS}
    try:
        requests_per_minute = int(os.environ.get("P109_VOICE_REQUESTS_PER_MINUTE", "30"))
    except ValueError:
        raise ValueError("P109_VOICE_REQUESTS_PER_MINUTE must be an integer") from None
    if not 1 <= requests_per_minute <= 600:
        raise ValueError("P109_VOICE_REQUESTS_PER_MINUTE must be between 1 and 600")
    attempts: dict[str, deque[float]] = {}
    attempts_lock = threading.Lock()
    for name, value in (("P109_STT_BASE_URL", stt_url), ("P109_TTS_BASE_URL", tts_url)):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(f"{name} must start with http:// or https://")
    if stt_timeout <= 0 or tts_timeout <= 0:
        raise ValueError("Voice service timeouts must be positive")

    def limit(request: FastAPIRequest):
        key = request.client.host if request.client else "unknown"
        cutoff = time.monotonic() - 60
        with attempts_lock:
            recent = attempts.setdefault(key, deque())
            while recent and recent[0] < cutoff:
                recent.popleft()
            if len(recent) >= requests_per_minute:
                raise HTTPException(429, "Слишком много голосовых запросов. Подождите минуту")
            recent.append(time.monotonic())

    def request_json(base_url: str, timeout: float, api_key: str | None,
                     path: str, payload: dict | None = None) -> tuple[dict, int]:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        started = time.perf_counter()
        try:
            with urlopen(Request(base_url + path, data=data, headers=headers, method=method),
                         timeout=timeout) as response:
                result = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise HTTPException(503, "Распознавание речи временно недоступно") from None
        if not isinstance(result, dict):
            raise HTTPException(503, "Не удалось обработать голосовую запись")
        return result, round((time.perf_counter() - started) * 1000)

    def request_audio(path: str, payload: dict) -> tuple[bytes, str | None, int]:
        headers = {"Accept": "audio/wav", "Content-Type": "application/json"}
        if tts_key:
            headers["Authorization"] = "Bearer " + tts_key
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        started = time.perf_counter()
        try:
            with urlopen(Request(tts_url + path, data=data, headers=headers, method="POST"),
                         timeout=tts_timeout) as response:
                content_type = response.headers.get_content_type()
                model = response.headers.get("X-Model")
                content = response.read(MAX_TTS_AUDIO_BYTES + 1)
        except (HTTPError, URLError, TimeoutError):
            raise HTTPException(503, "Озвучивание временно недоступно") from None
        if content_type != "audio/wav" or len(content) > MAX_TTS_AUDIO_BYTES:
            raise HTTPException(503, "Не удалось подготовить голосовой ответ")
        if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
            raise HTTPException(503, "Не удалось подготовить голосовой ответ")
        return content, model, round((time.perf_counter() - started) * 1000)

    @router.get("/health")
    def health():
        if not stt_url:
            result = {"status": "disabled"}
        else:
            try:
                upstream, latency = request_json(stt_url, stt_timeout, stt_key, "/health")
                result = {"status": upstream.get("status", "unavailable"),
                          "model": upstream.get("model"), "device": upstream.get("device"),
                          "latency_ms": latency}
            except HTTPException as error:
                result = {"status": "unavailable", "detail": error.detail}
        cache_ready = all(value for name, value in prompt_cache.items()
                          if name != "mixed_language_retry")
        if cache_ready:
            result["tts"] = {"status": "healthy", "model": CACHED_TTS_MODEL,
                             "device": "local", "latency_ms": 0, "mode": "cache"}
        elif not tts_url:
            result["tts"] = {"status": "disabled"}
        else:
            try:
                upstream, latency = request_json(tts_url, tts_timeout, tts_key, "/health")
                result["tts"] = {"status": upstream.get("status", "unavailable"),
                                 "model": upstream.get("model"), "device": upstream.get("device"),
                                 "latency_ms": latency}
            except HTTPException as error:
                result["tts"] = {"status": "unavailable", "detail": error.detail}
        result["realtime"] = realtime.health()
        return result

    def finalize_transcript(req: TranscriptRequest, provider_language=None,
                            provider_confidence=None) -> dict:
        text = normalize_address_numbers(req.text.strip()) if req.field == "address" else req.text.strip()
        language = resolve_language(text, req.language, provider_language, provider_confidence)
        response_language = language["response_language"]
        assistant_prompt = (f"{response_language}_{'address' if req.field == 'problem' else 'review'}"
                            if response_language else "mixed_language_retry")
        city = detect_spoken_city(text) if req.field == "address" else None
        if city:
            text = clean_spoken_address(text, city)
        return {"text": text, "field": req.field,
                "next_field": "address" if req.field == "problem" else "review",
                "assistant_message": VOICE_PROMPTS[assistant_prompt][1],
                "assistant_prompt": assistant_prompt,
                "detected_city": ({"code": city["code"], "name_ru": city["name_ru"],
                                   "region_id": city["region_id"]} if city else None),
                **language, "audio_stored": False}

    attach_realtime_route(router, limit, realtime)

    @router.post("/finalize-transcript")
    def finalize(req: TranscriptRequest, request: FastAPIRequest):
        limit(request)
        return finalize_transcript(req)

    @router.post("/transcribe")
    def transcribe(req: VoiceRequest, request: FastAPIRequest):
        limit(request)
        decode_wav_data(req.audio_data)
        if not stt_url:
            raise HTTPException(503, "Распознавание речи временно недоступно")
        upstream_request = req.model_dump(exclude={"hint_language"})
        if req.language == "auto" and req.hint_language:
            upstream_request["language"] = req.hint_language
        result, total_latency = request_json(
            stt_url, stt_timeout, stt_key, "/v1/transcribe", upstream_request
        )
        text = result.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise HTTPException(503, "Не удалось распознать речь")
        finalized = finalize_transcript(TranscriptRequest(
            text=text, language=req.language, hint_language=req.hint_language, field=req.field,
        ), result.get("language") or result.get("detected_language"),
           result.get("language_confidence"))
        return {**finalized, "model": result.get("model"),
                "stt_latency_ms": result.get("latency_ms"), "total_latency_ms": total_latency}

    @router.post("/analyze")
    def analyze(req: DraftAnalysisRequest, request: FastAPIRequest):
        limit(request)
        if analyze_draft is None:
            raise HTTPException(503, "ИИ-помощник временно недоступен")
        language = resolve_language(req.text, req.language)
        if language["needs_language_choice"]:
            return {"category": None, "category_label": "Категория пока не определена",
                    "confidence": None, "urgency": None, "needs_clarification": True,
                    "spam_suspected": False, "ai_active": False,
                    "assistant_message": VOICE_PROMPTS["mixed_language_retry"][1], **language}
        response_language = language["response_language"]
        view = analyze_draft({"text": req.text.strip(), "language": language["language"],
                              "region_id": req.region_id, "address": None})
        laya = (view.get("source_decisions") or {}).get("laya") or {}
        use_laya = bool(laya.get("category"))
        source = laya if use_laya else view
        category = source.get("category")
        labels = {topic["id"]: topic["name_kk" if response_language == "kk" else "name_ru"]
                  for topic in (topics or [])}
        needs = source.get("needs_clarification") or {}
        spam = source.get("spam_suspected") or {}
        needs_clarification = (bool(needs.get("value")) or not category or bool(spam.get("value")) or
                               source.get("decision") == "CLARIFY_OR_HUMAN_REVIEW")
        label = labels.get(category, "Категория пока не определена")
        if response_language == "kk":
            assistant_message = ("Мәселені нақтырақ сипаттаңыз: не болды, қашан басталды және қауіп бар ма?"
                                 if needs_clarification else
                                 f"Түсіндім: «{label}». Енді оқиға орнын нақтылайық.")
        else:
            assistant_message = ("Опишите точнее, что произошло, когда началось и есть ли опасность."
                                 if needs_clarification else
                                 f"Похоже, это «{label}». Теперь уточним место.")
        return {"category": category, "category_label": label,
                "confidence": source.get("confidence", source.get("category_confidence")),
                "urgency": source.get("urgency"), "needs_clarification": needs_clarification,
                "spam_suspected": bool(spam.get("value")),
                "provider": "laya_shadow" if use_laya else view.get("provider"),
                "ai_active": use_laya or view.get("provider") in {"laya", "hybrid"},
                "assistant_message": assistant_message,
                "latency_ms": view.get("triage_total_latency_ms"), **language}

    @router.post("/speak")
    def speak(req: SpeechRequest, request: FastAPIRequest):
        limit(request)
        prompt = VOICE_PROMPTS.get(req.prompt)
        if prompt is None:
            raise HTTPException(422, "Неизвестная реплика голосового помощника")
        audio = prompt_cache[req.prompt]
        if audio is not None:
            return {"audio_data": "data:audio/wav;base64," + base64.b64encode(audio).decode(),
                    "model": CACHED_TTS_MODEL, "tts_latency_ms": 0, "audio_stored": False}
        if not tts_url:
            raise HTTPException(503, "Озвучивание временно недоступно")
        language, text = prompt
        audio, model, latency = request_audio("/v1/speech", {"text": text, "language": language})
        return {"audio_data": "data:audio/wav;base64," + base64.b64encode(audio).decode(),
                "model": model, "tts_latency_ms": latency, "audio_stored": False}

    return router
