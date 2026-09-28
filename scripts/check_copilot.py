"""End-to-end check for the private self-hosted Qwen copilot."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot import CopilotResponseError, QwenCopilot
from smoke import find_free_port, http_request, wait_for_server


class FakeQwen(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw)
        self.requests.append({"authorization": self.headers.get("Authorization"), "body": body})
        context = json.loads(body["messages"][1]["content"])
        content = "not-json" if "malformed" in context["complaint"] else json.dumps({
            "summary": "Во всём доме нет воды с утра.",
            "reasoning": "Указаны масштаб дома, время и категория водоснабжения.",
            "suggested_reply": "Обращение зарегистрировано. Оператор проверит данные и маршрут.",
            "clarification_question": None,
            "recommended_action": "prepare_reply",
        }, ensure_ascii=False)
        response = json.dumps({
            "model": "Qwen/Qwen3-4B-Instruct-2507",
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def start_fake_qwen():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQwen)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def start_pulse(root, db, qwen_url):
    port = find_free_port()
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(root), "DATABASE_PATH": str(db), "P109_AUTH_DISABLED": "1",
        "P109_DEMO_MODE": "1",
        "P109_COPILOT_BASE_URL": qwen_url, "P109_COPILOT_API_KEY": "fixture-secret",
        "P109_COPILOT_MODEL": "Qwen/Qwen3-4B-Instruct-2507", "P109_COPILOT_TIMEOUT": "1",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    if not wait_for_server(url):
        stdout, stderr = process.communicate(timeout=3)
        raise RuntimeError(stdout.decode() + stderr.decode())
    return process, url


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run():
    try:
        QwenCopilot("https://api.example.com", "secret")
        raise AssertionError("Public Qwen endpoint was accepted")
    except ValueError:
        pass
    assert QwenCopilot("http://127.0.0.1:11434", "secret", revision="a" * 64).status()["configured"]
    try:
        QwenCopilot._validate({
            "summary": "x", "reasoning": "x", "suggested_reply": "x",
            "clarification_question": None, "recommended_action": "review_incident",
        }, {"incident_candidate": False})
        raise AssertionError("Unavailable incident action was accepted")
    except CopilotResponseError:
        pass
    try:
        QwenCopilot._validate({
            "summary": "x", "reasoning": "x", "suggested_reply": "Мы уже устранили проблему.",
            "clarification_question": None, "recommended_action": "prepare_reply",
        }, {"incident_candidate": False})
        raise AssertionError("Unverified service action was accepted")
    except CopilotResponseError:
        pass
    print("PASS 1: Copilot accepts only private endpoints with authentication")

    root = Path(__file__).resolve().parents[1]
    qwen_server, qwen_url = start_fake_qwen()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            process, url = start_pulse(root, Path(tmp) / "copilot.db", qwen_url)

            def call(path, body=None, expected=200):
                code, data = http_request(url + path, "POST" if body is not None else "GET", body)
                assert code == expected, (path, code, data)
                return data

            try:
                health = call("/api/health")
                assert health["copilot"] == {
                    "configured": True, "mode": "self_hosted_qwen",
                    "model": "Qwen/Qwen3-4B-Instruct-2507",
                    "model_revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
                    "endpoint": "private",
                }
                created = call("/api/workspace/intake", {
                    "text": "На Абая 44 с утра нет воды во всём доме. ИИН 123456789012, телефон +7 777 123 45 67, a@example.kz",
                    "address": "Абая 44", "region_id": "KZ-ALA", "language": "ru",
                }, 201)
                cid = created["id"]
                call(f"/api/workspace/complaints/{cid}/triage", {})
                answer = call(f"/api/workspace/complaints/{cid}/copilot", {})
                assert answer["available"] and answer["mode"] == "self_hosted_qwen"
                assert answer["recommended_action"] == "prepare_reply" and len(answer["result_id"]) == 64
                assert answer["model_revision"] == "cdbee75f17c01a7cc42f958dc650907174af0554"
                sent = FakeQwen.requests[-1]
                assert sent["authorization"] == "Bearer fixture-secret"
                assert sent["body"]["reasoning_effort"] == "none"
                prompt = sent["body"]["messages"][1]["content"]
                assert "Абая 44" not in prompt and "123456789012" not in prompt
                assert "+7 777" not in prompt and "a@example.kz" not in prompt
                print("PASS 2: real API calls private Qwen and removes address, phone, IIN and email")

                events = call(f"/api/complaints/{cid}")["events"]
                generated = next(json.loads(item["payload"]) for item in events
                                 if item["event_type"] == "copilot_generated")
                assert generated["result_id"] == answer["result_id"]
                assert "Во всём доме" not in json.dumps(generated, ensure_ascii=False)
                call(f"/api/workspace/complaints/{cid}/copilot/feedback", {
                    "result_id": answer["result_id"], "helpful": True,
                })
                call(f"/api/workspace/complaints/{cid}/copilot/feedback", {
                    "result_id": "0" * 64, "helpful": False,
                }, 409)
                print("PASS 3: audit stores only model metadata; feedback must match the latest result")

                request_count = len(FakeQwen.requests)
                kk = call("/api/workspace/intake", {
                    "text": "Бүкіл үйде таңертеңнен бері су жоқ", "region_id": "KZ-ALA", "language": "kk",
                }, 201)["id"]
                call(f"/api/workspace/complaints/{kk}/triage", {})
                kk_fallback = call(f"/api/workspace/complaints/{kk}/copilot", {})
                assert not kk_fallback["available"] and kk_fallback["suggested_reply"].startswith("Өтінішіңіз")
                assert len(FakeQwen.requests) == request_count and "quality gate" in kk_fallback["fallback_reason"]
                print("PASS 4: KK stays behind the quality gate with a natural Kazakh fallback")

                bad = call("/api/workspace/intake", {
                    "text": "malformed: непонятная проблема", "region_id": "KZ-ALA", "language": "ru",
                }, 201)["id"]
                call(f"/api/workspace/complaints/{bad}/triage", {})
                fallback = call(f"/api/workspace/complaints/{bad}/copilot", {})
                assert not fallback["available"] and fallback["mode"] == "deterministic_fallback"
                assert fallback["suggested_reply"] and fallback["fallback_reason"]
                print("PASS 5: malformed model output falls back without blocking the operator")
            finally:
                stop(process)
    finally:
        qwen_server.shutdown()
        qwen_server.server_close()
    print("ALL 5 COPILOT CHECKS PASSED")


if __name__ == "__main__":
    run()
