"""Check the voice bridge, staged dialogue and photo/video intake boundaries."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from smoke import find_free_port, http_request, wait_for_server
from voice_api import (cached_prompt, clean_spoken_address, detect_spoken_city,
                       detect_text_language, normalize_address_numbers, resolve_language)


class FakeVoice(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        model = "fixture-omnivoice" if self.server.service == "tts" else "fixture-rukk"
        self.send_json(200, {"status": "healthy", "model": model, "device": "cpu"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.service == "tts":
            assert body["language"] in {"ru", "kk", "mixed"} and body["text"]
            self.server.requests.append(body)
            audio = wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("X-Model", "fixture-omnivoice")
            self.end_headers()
            self.wfile.write(audio)
            return
        text = ("Сәлем, у нас сегодня нет воды" if body.get("language") == "ru" else
                "На Абая сорок четыре с утра нет воды" if body["field"] == "problem" else
                "Астана қаласы Абай көшесі қырық төртінші үй")
        self.send_json(200, {"text": text, "model": "fixture-rukk", "device": "cpu",
                             "latency_ms": 12, "audio_stored": False})


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as target:
        target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        target.writeframes(b"\0\0" * 3200)
    return stream.getvalue()


def wav_data_url():
    return "data:audio/wav;base64," + base64.b64encode(wav_bytes()).decode()


def start_pulse(root, db, stt_url, tts_url):
    port = find_free_port()
    env = os.environ.copy()
    env.update({"DATABASE_PATH": str(db), "PYTHONPATH": str(root), "P109_AUTH_DISABLED": "1",
                "P109_DEMO_MODE": "1",
                "P109_STT_BASE_URL": stt_url, "P109_STT_TIMEOUT": "1",
                "P109_TTS_BASE_URL": tts_url, "P109_TTS_TIMEOUT": "1",
                "P109_VOICE_REQUESTS_PER_MINUTE": "40"})
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    if not wait_for_server(url):
        stdout, stderr = proc.communicate(timeout=3)
        raise RuntimeError(stdout.decode() + stderr.decode())
    return proc, url


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run():
    assert normalize_address_numbers("Абая сорок четыре, дом двадцать шесть") == "Абая 44, дом 26"
    assert normalize_address_numbers("Абай көшесі екі жүз қырық төртінші үй") == "Абай көшесі 244 үй"
    assert detect_spoken_city("город Ассана, улица Дала 35")["code"] == "710000000"
    assert detect_spoken_city("Нур-Султан, проспект Кабанбай батыра")["code"] == "710000000"
    assert detect_spoken_city("улица Абая 44") is None
    astana = detect_spoken_city("город Ассана, улица Улы Дала 35")
    assert clean_spoken_address("город Ассана, улица Улы Дала 35", astana) == "улица Улы Дала 35"
    assert detect_text_language("На Абая с утра нет воды")["language"] == "ru"
    assert detect_text_language("Абай көшесінде таңертең су жоқ")["language"] == "kk"
    mixed = detect_text_language("Сәлем, у нас сегодня нет воды")
    assert mixed["language"] == "mixed" and mixed["response_language"] == "ru"
    assert detect_text_language("Абай 44")["needs_language_choice"]
    manual = resolve_language("Абай көшесінде су жоқ", "ru", "kk", .99)
    assert manual["language"] == "ru" and manual["source"] == "manual"
    stt = ThreadingHTTPServer(("127.0.0.1", 0), FakeVoice)
    stt.service = "stt"
    tts = ThreadingHTTPServer(("127.0.0.1", 0), FakeVoice)
    tts.service = "tts"
    tts.requests = []
    threading.Thread(target=stt.serve_forever, daemon=True).start()
    threading.Thread(target=tts.serve_forever, daemon=True).start()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        cache_dir = Path(temporary) / "voice-cache"
        cache_dir.mkdir()
        (cache_dir / "ru_problem.wav").write_bytes(wav_bytes())
        assert cached_prompt(cache_dir, "ru_problem") == wav_bytes()
        os.environ["P109_VOICE_CACHE_DIR"] = str(Path(temporary) / "empty-cache")
        proc, url = start_pulse(root, Path(temporary) / "voice.db",
                                f"http://127.0.0.1:{stt.server_port}",
                                f"http://127.0.0.1:{tts.server_port}")

        def call(path, payload=None, expected=200):
            status, result = http_request(url + path, "POST" if payload is not None else "GET", payload)
            assert status == expected, (status, result)
            return result

        try:
            health = call("/api/voice/health")
            assert health["status"] == "healthy" and health["model"] == "fixture-rukk"
            assert health["tts"]["status"] == "healthy"
            assert health["realtime"]["status"] == "disabled"
            print("PASS 1: Pulse reports private RU/KK STT and OmniVoice health")

            speech = call("/api/voice/speak", {"prompt": "ru_problem"})
            assert speech["model"] == "fixture-omnivoice" and speech["audio_stored"] is False
            encoded = speech["audio_data"].split(",", 1)[1]
            assert base64.b64decode(encoded).startswith(b"RIFF")
            call("/api/voice/speak", {"prompt": "arbitrary_text"}, 422)
            print("PASS 2: OmniVoice bridge serves only fixed dialogue prompts as WAV")

            problem = call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "auto", "field": "problem",
            })
            assert problem["text"].startswith("На Абая") and problem["next_field"] == "address"
            assert problem["audio_stored"] is False and problem["model"] == "fixture-rukk"
            assert problem["language"] == "ru" and problem["response_language"] == "ru"
            assert problem["source"] == "text" and not problem["needs_language_choice"]
            analysis = call("/api/voice/analyze", {
                "text": problem["text"], "language": "auto", "region_id": "KZ-ALA",
            })
            assert analysis["category"] == "water_supply"
            assert analysis["category_label"] == "Водоснабжение"
            address = call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "auto", "field": "address",
            })
            assert address["text"] == "Абай көшесі 44 үй"
            assert address["language"] == "kk" and address["response_language"] == "kk"
            assert address["detected_city"] == {"code": "710000000", "name_ru": "Астана", "region_id": "KZ-AST"}
            assert address["next_field"] == "review"
            realtime_final = call("/api/voice/finalize-transcript", {
                "text": "Астана Абай көшесі қырық төртінші үй", "language": "kk",
                "field": "address",
            })
            assert realtime_final["text"] == "Абай көшесі 44 үй"
            assert realtime_final["detected_city"]["code"] == "710000000"
            call("/api/voice/transcribe", {
                "audio_data": "data:audio/wav;base64,bm90LXdhdg==", "language": "ru", "field": "problem",
            }, 422)
            assert problem["assistant_prompt"] == "ru_address"
            assert address["assistant_prompt"] == "kk_review"
            kk_analysis = call("/api/voice/analyze", {
                "text": "Абай көшесінде таңертең су жоқ", "language": "auto", "region_id": "KZ-ALA",
            })
            assert kk_analysis["response_language"] == "kk" and kk_analysis["assistant_message"].startswith("Түсіндім")
            speech_kk = call("/api/voice/speak", {"prompt": "kk_review"})
            assert speech_kk["audio_stored"] is False and tts.requests[-1]["language"] == "kk"
            print("PASS 3: RU and KK are detected automatically; replies follow the detected language")

            uncertain = call("/api/voice/analyze", {
                "text": "Абай 44", "language": "auto", "region_id": "KZ-ALA",
            })
            assert uncertain["language"] == "unknown" and uncertain["needs_language_choice"]
            assert "Тілді таңдаңыз" in uncertain["assistant_message"]
            manual_analysis = call("/api/voice/analyze", {
                "text": "Абай көшесінде таңертең су жоқ", "language": "ru", "region_id": "KZ-ALA",
            })
            assert manual_analysis["language"] == "ru" and manual_analysis["source"] == "manual"
            assert manual_analysis["assistant_message"].startswith("Похоже")
            mixed_transcript = "Сәлем, у нас сегодня нет воды"
            mixed_result = call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "auto", "hint_language": "ru", "field": "problem",
            })
            assert mixed_result["text"] == mixed_transcript
            assert mixed_result["language"] == "mixed" and mixed_result["response_language"] == "ru"
            print("PASS 4: mixed speech stays intact; uncertain auto asks for a choice; manual language wins")

            video = b"\x1aE\xdf\xa3pulse109-demo"
            video_data = "data:video/webm;base64," + base64.b64encode(video).decode()
            created = call("/api/workspace/intake", {
                "text": problem["text"], "address": address["text"], "region_id": "KZ-ALA",
                "city_code": "750000000", "language": "auto", "channel": "web",
                "latitude": 43.238949, "longitude": 76.945123, "video_data": video_data,
            }, 201)
            detail = call(f"/api/workspace/complaints/{created['id']}/triage", {})
            tracked = call(f"/api/workspace/tracking/{created['id']}")
            public = call("/api/workspace/public/complaints")
            public_case = next(item for item in public["items"] if item["id"] == created["id"])
            assert created["language"] == "ru" and detail["complaint"]["language"] == "ru"
            assert detail["complaint"]["has_video"] and tracked["has_video"] and public_case["has_video"]
            with urlopen(Request(url + f"/api/workspace/public/complaints/{created['id']}/video")) as response:
                assert response.headers.get_content_type() == "video/webm" and response.read() == video
            print("PASS 5: voice intake stores the detected language, transcript and WebM evidence")

            call("/api/workspace/intake", {
                "text": "test", "region_id": "KZ-ALA", "photo_data": "x", "video_data": "x",
            }, 422)
            print("PASS 6: intake accepts one bounded photo or video, never both")

            stt.shutdown()
            call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "auto", "field": "problem",
            }, 503)
            manual_intake = call("/api/workspace/intake", {
                "text": "Абай көшесінде су жоқ", "region_id": "KZ-ALA",
                "city_code": "750000000", "language": "kk", "channel": "web",
            }, 201)
            assert manual_intake["language"] == "kk"
            print("PASS 7: unavailable STT leaves manual intake working")

            for _ in range(35):
                status, _ = http_request(url + "/api/voice/speak", "POST", {"prompt": "ru_problem"})
                if status == 429:
                    break
            assert status == 429
            print("PASS 8: public voice inference is rate limited per client")
        finally:
            stop(proc)
    for server in (stt, tts):
        server.shutdown()
        server.server_close()
    os.environ.pop("P109_VOICE_CACHE_DIR", None)
    print("ALL 8 VOICE AGENT CHECKS PASSED")


if __name__ == "__main__":
    run()
