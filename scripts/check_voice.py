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
            audio = wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("X-Model", "fixture-omnivoice")
            self.end_headers()
            self.wfile.write(audio)
            return
        text = ("На Абая сорок четыре с утра нет воды" if body["field"] == "problem"
                else "Абай көшесі қырық төртінші үй")
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
                "P109_STT_BASE_URL": stt_url, "P109_STT_TIMEOUT": "1",
                "P109_TTS_BASE_URL": tts_url, "P109_TTS_TIMEOUT": "1",
                "P109_VOICE_REQUESTS_PER_MINUTE": "5"})
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
    stt = ThreadingHTTPServer(("127.0.0.1", 0), FakeVoice)
    stt.service = "stt"
    tts = ThreadingHTTPServer(("127.0.0.1", 0), FakeVoice)
    tts.service = "tts"
    threading.Thread(target=stt.serve_forever, daemon=True).start()
    threading.Thread(target=tts.serve_forever, daemon=True).start()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        proc, url = start_pulse(root, Path(temporary) / "voice.db",
                                f"http://127.0.0.1:{stt.server_port}",
                                f"http://127.0.0.1:{tts.server_port}")

        def call(path, payload=None, expected=200):
            status, result = http_request(url + path, "POST" if payload is not None else "GET", payload)
            assert status == expected, (status, result)
            return result

        try:
            health = call("/api/voice/health")
            assert health == {"status": "healthy", "model": "fixture-rukk", "device": "cpu",
                              "latency_ms": health["latency_ms"],
                              "tts": {"status": "healthy", "model": "fixture-omnivoice",
                                      "device": "cpu", "latency_ms": health["tts"]["latency_ms"]}}
            print("PASS 1: Pulse reports private RU/KK STT and OmniVoice health")

            speech = call("/api/voice/speak", {"prompt": "ru_problem"})
            assert speech["model"] == "fixture-omnivoice" and speech["audio_stored"] is False
            encoded = speech["audio_data"].split(",", 1)[1]
            assert base64.b64decode(encoded).startswith(b"RIFF")
            call("/api/voice/speak", {"prompt": "arbitrary_text"}, 422)
            print("PASS 2: OmniVoice bridge serves only fixed dialogue prompts as WAV")

            problem = call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "ru", "field": "problem",
            })
            assert problem["text"].startswith("На Абая") and problem["next_field"] == "address"
            assert problem["audio_stored"] is False and problem["model"] == "fixture-rukk"
            address = call("/api/voice/transcribe", {
                "audio_data": wav_data_url(), "language": "kk", "field": "address",
            })
            assert address["text"].startswith("Абай көшесі") and address["next_field"] == "review"
            call("/api/voice/transcribe", {
                "audio_data": "data:audio/wav;base64,bm90LXdhdg==", "language": "ru", "field": "problem",
            }, 422)
            assert problem["assistant_prompt"] == "ru_address"
            assert address["assistant_prompt"] == "kk_review"
            print("PASS 3: problem → address → review dialogue validates WAV and keeps no audio")

            call("/api/voice/speak", {"prompt": "ru_problem"}, 429)
            print("PASS 4: public voice inference is rate limited per client")

            video = b"\x1aE\xdf\xa3pulse109-demo"
            video_data = "data:video/webm;base64," + base64.b64encode(video).decode()
            created = call("/api/workspace/intake", {
                "text": problem["text"], "address": address["text"], "region_id": "KZ-ALA",
                "city_code": "750000000", "language": "mixed", "channel": "web",
                "latitude": 43.238949, "longitude": 76.945123, "video_data": video_data,
            }, 201)
            detail = call(f"/api/workspace/complaints/{created['id']}/triage", {})
            tracked = call(f"/api/workspace/tracking/{created['id']}")
            public = call("/api/workspace/public/complaints")
            public_case = next(item for item in public["items"] if item["id"] == created["id"])
            assert detail["complaint"]["has_video"] and tracked["has_video"] and public_case["has_video"]
            with urlopen(Request(url + f"/api/workspace/public/complaints/{created['id']}/video")) as response:
                assert response.headers.get_content_type() == "video/webm" and response.read() == video
            print("PASS 5: voice transcript and WebM evidence create a normal mapped complaint")

            call("/api/workspace/intake", {
                "text": "test", "region_id": "KZ-ALA", "photo_data": "x", "video_data": "x",
            }, 422)
            print("PASS 6: intake accepts one bounded photo or video, never both")
        finally:
            stop(proc)
    for server in (stt, tts):
        server.shutdown()
        server.server_close()
    print("ALL 6 VOICE AGENT CHECKS PASSED")


if __name__ == "__main__":
    run()
