"""Focused Laya client, triage, fallback, audit and routing checks."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laya_client import LayaClient, LayaResponseError, LayaUnavailable
from smoke import find_free_port, http_request, wait_for_server


CHECKPOINT_ID = "pulse109-laya-test-sha"


class FakeLaya(BaseHTTPRequestHandler):
    states: list[dict] = []

    def log_message(self, *_):
        pass

    def _send(self, status, body, content_type="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "loaded": ["multilingual"],
                             "revisions": {"multilingual": "fixture-revision"}})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/v1/systemone":
            self._send(404, {"detail": "not found"})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        state = body["state"]
        self.states.append(state)
        text = state.get("text", "") if isinstance(state, dict) else str(state)
        if "laya-unavailable" in text:
            self._send(503, {"detail": "fixture unavailable"})
            return
        if "invalid-json" in text:
            self._send(200, b"not json", "text/plain")
            return
        if "bad-schema" in text:
            self._send(200, {"model": "laya-rl-agent", "answers": {}})
            return
        if "slow-response" in text:
            time.sleep(.2)
        questions = body["questions"]
        answers = {}
        if "category_confirmed" in questions:
            if "verify-slow" in text:
                time.sleep(.2)
            probability = .29 if "возле люка" in text else .91
            answers["category_confirmed"] = {"type": "noul", "noul": probability,
                                               "confidence": max(probability, 1 - probability)}
        else:
            spam_probability = (.89 if "borderline-spam" in text else
                                .92 if "рекламу" in text.lower() else .02)
            category = "sewerage" if "disagreement" in text else "water_supply"
            confidence = (.77 if "disagreement" in text else .31 if "low-confidence" in text else
                          .57 if "возле люка" in text else .72 if "medium-yes" in text or "verify-slow" in text else .94)
            criteria = questions["category"]["criteria"]
            probabilities = {key: .001 for key in criteria}
            probabilities[category] = confidence
            if category == "water_supply" and "возле люка" in text:
                probabilities["sewerage"] = .38
            answers = {
                "category": {"type": "choice", "choice": category,
                             "probabilities": probabilities, "confidence": confidence,
                             "answer_confidence": confidence},
                "needs_clarification": {"type": "noul", "noul": .91 if "needs-clarification" in text else .08,
                                        "confidence": .91 if "needs-clarification" in text else .92},
                "spam_suspected": {"type": "noul", "noul": spam_probability,
                                   "confidence": max(spam_probability, 1 - spam_probability)},
                "urgency": {"type": "choice", "choice": "urgent" if "опасно" in text else "normal",
                            "probabilities": {"normal": .07 if "опасно" in text else .9,
                                              "urgent": .93 if "опасно" in text else .1}, "confidence": .93 if "опасно" in text else .9},
            }
        self._send(200, {"model": "laya-rl-agent", "answers": answers,
                         "usage": {"input_tokens": 42, "output_tokens": 0},
                         "routing": {"model": "multilingual",
                                     "repo": "convaiinnovations/laya/multilingual"}})


def start_fake_laya():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLaya)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def start_pulse(root: Path, db: Path, laya_url: str, mode="laya"):
    port = find_free_port()
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(root), "DATABASE_PATH": str(db),
        "P109_AUTH_DISABLED": "1",
        "P109_DEMO_MODE": "1",
        "P109_DECISION_PROVIDER": mode, "P109_LAYA_BASE_URL": laya_url,
        "P109_LAYA_MODEL": "multilingual", "P109_LAYA_TIMEOUT": ".08",
        "P109_LAYA_CHECKPOINT_ID": CHECKPOINT_ID,
        "P109_LAYA_SPAM_THRESHOLD": ".9",
        "P109_ENABLE_VERIFICATION": "1", "P109_LAYA_DEMO_FALLBACK": "1",
    })
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
    root = Path(__file__).resolve().parents[1]
    laya_server, laya_url = start_fake_laya()
    client = LayaClient(laya_url, timeout=.08)
    questions = {"category": {"type": "choice", "instructions": "category",
                              "criteria": {"water_supply": "water", "sewerage": "sewer"}}}
    try:
        result = client.predict({"text": "нет холодной воды"}, questions)
        assert result.answers["category"]["choice"] == "water_supply" and result.latency_ms >= 0
        assert client.health()["status"] == "healthy"
        print("PASS 1: Laya client success and health")

        for text, error in (("invalid-json", LayaResponseError), ("bad-schema", LayaResponseError),
                            ("slow-response", LayaUnavailable)):
            try:
                client.predict({"text": text}, questions)
                raise AssertionError(f"{text} unexpectedly succeeded")
            except error:
                pass
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        unused.close()
        try:
            LayaClient(f"http://127.0.0.1:{port}", timeout=.05).predict({"text": "x"}, questions)
            raise AssertionError("unavailable endpoint unexpectedly succeeded")
        except LayaUnavailable:
            pass
        print("PASS 2: timeout, invalid JSON, unavailable server and unexpected schema")

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "laya.db"
            proc, url = start_pulse(root, db, laya_url)

            def call(path, body=None, expected=200):
                code, data = http_request(url + path, "POST" if body is not None else "GET", body)
                assert code == expected, (path, code, data)
                return data

            def intake(text, language="ru"):
                return call("/api/workspace/intake", {"text": text, "region_id": "KZ-ALA",
                            "language": language, "district": "Алмалинский", "channel": "web"}, 201)["id"]

            try:
                call("/api/workspace/seed", {})
                health = call("/api/health")
                assert health["status"] == "ok" and health["laya"]["status"] == "healthy"
                assert health["training_status"] == "candidate_checkpoint_configured"
                assert health["checkpoint_id"] == CHECKPOINT_ID
                assert "TRAINED MODEL" in health["banner"]

                high = intake("На Абая 44 с утра нет холодной воды во всём доме, телефон +7 777 123 45 67, ИИН 123456789012")
                detail = call(f"/api/workspace/complaints/{high}/triage", {})
                ai = detail["triage"]
                assert ai["provider"] == "laya" and ai["category"] == "water_supply"
                assert ai["decision_mode"] == "AUTO_PRESELECT" and ai["category_confidence"] == .94
                assert ai["checkpoint_id"] == CHECKPOINT_ID and "convaiinnovations" not in json.dumps(ai)
                assert detail["routing"]["operator"]["id"] == "op-aidana"
                sent = FakeLaya.states[-1]["text"]
                assert "+7 777" not in sent and "123456789012" not in sent
                audit = call(f"/api/complaints/{high}")["events"][-1]
                payload = json.loads(audit["payload"])
                assert payload["provider"] == "laya" and payload["decision_mode"] == "AUTO_PRESELECT"
                assert "На Абая" not in audit["payload"]
                print("PASS 3: request → Laya → AUTO_PRESELECT → existing operator routing; PII minimized")

                medium = intake("Во дворе что-то течёт возле люка")
                detail = call(f"/api/workspace/complaints/{medium}/triage", {})
                ai = detail["triage"]
                assert ai["verification"]["value"] is False and ai["decision_mode"] == "CLARIFY_OR_HUMAN_REVIEW"
                assert ai["confidence_band"] == "low"
                call(f"/api/workspace/complaints/{medium}/decide", {"topic": "water_supply", "priority": "normal"}, 409)
                call(f"/api/complaints/{medium}/clarification", {"reason": "unclear_event",
                     "question": "Вода из трубы или канализационного люка?"})
                print("PASS 4: medium result → binary verification NO → existing clarification flow")

                verified = intake("medium-yes: возле трубы течёт вода")
                verified_ai = call(f"/api/workspace/complaints/{verified}/triage", {})["triage"]
                assert verified_ai["verification"]["value"] is True
                assert verified_ai["decision_mode"] == "AUTO_PRESELECT"
                low = intake("low-confidence: непонятное обращение")
                assert call(f"/api/workspace/complaints/{low}/triage", {})["triage"]["decision_mode"] == "CLARIFY_OR_HUMAN_REVIEW"
                needs = intake("needs-clarification: нет данных")
                assert call(f"/api/workspace/complaints/{needs}/triage", {})["triage"]["needs_clarification"]["value"] is True
                urgent = intake("На улице опасно: открытый люк")
                assert call(f"/api/workspace/complaints/{urgent}/triage", {})["triage"]["urgency"] == "urgent"
                unavailable_verify = intake("verify-slow: medium-yes вода течёт")
                unavailable_ai = call(f"/api/workspace/complaints/{unavailable_verify}/triage", {})["triage"]
                assert unavailable_ai["decision_mode"] == "VERIFY" and unavailable_ai["verification"]["available"] is False
                print("PASS 5: verify YES, low confidence, clarification flag, urgency and verification fallback")

                spam = intake("Купите рекламу, быстрый заработок")
                spam_detail = call(f"/api/workspace/complaints/{spam}/triage", {})
                assert "spam_suspected" in spam_detail["flags"] and not spam_detail["complaint"]["quarantined"]
                assert spam_detail["triage"]["spam_suspected"] == {
                    "value": True, "confidence": .92, "probability_true": .92, "threshold": .9,
                }
                assert any("порог 90%" in reason for reason in spam_detail["risk"]["reasons"])
                borderline = intake("borderline-spam: пограничное сообщение")
                borderline_detail = call(f"/api/workspace/complaints/{borderline}/triage", {})
                assert "spam_suspected" not in borderline_detail["flags"]
                assert borderline_detail["triage"]["spam_suspected"]["threshold"] == .9
                print("PASS 6: calibrated Laya spam threshold adds review only at 90%+; no auto-quarantine")

                fallback = intake("invalid-json, но на Абая нет холодной воды")
                fallback_detail = call(f"/api/workspace/complaints/{fallback}/triage", {})
                assert fallback_detail["triage"]["provider"] == "demo_fallback"
                assert fallback_detail["triage"]["fallback_reason"] and fallback_detail["triage"]["category"] == "water_supply"
                print("PASS 7: malformed Laya response falls back visibly to existing classification")

                call(f"/api/workspace/complaints/{high}/decide", {"topic": "sewerage", "priority": "normal"})
                metrics = call("/api/workspace/metrics")["laya_quality"]
                assert metrics["operator_override_percent"] == 100 and metrics["language_agreement_percent"]["ru"] == 0
                confirmed = call(f"/api/complaints/{high}")["events"][-1]
                confirmed_payload = json.loads(confirmed["payload"])
                assert confirmed_payload["operator_override"] is True and confirmed_payload["suggested_value"] == "water_supply"
                print("PASS 8: operator override audit and synthetic RU/KK quality metrics")
            finally:
                stop(proc)

            shadow_db = Path(tmp) / "shadow.db"
            proc, url = start_pulse(root, shadow_db, laya_url, "shadow")
            try:
                shadow_health = http_request(url + "/api/health")[1]
                assert shadow_health["training_status"] == "shadow_evaluation"
                assert shadow_health["checkpoint_id"] == CHECKPOINT_ID
                assert shadow_health["laya"]["spam_threshold"] == .9
                assert "TRAINED MODEL · SHADOW ONLY" in shadow_health["banner"]

                def shadow_triage(text):
                    code, created = http_request(url + "/api/workspace/intake", "POST", {
                        "text": text, "region_id": "KZ-ALA", "language": "ru",
                        "district": "Алмалинский", "channel": "web",
                    })
                    assert code == 201
                    return created["id"], http_request(
                        url + f"/api/workspace/complaints/{created['id']}/triage", "POST", {}
                    )[1]

                agreeing_id, agreeing = shadow_triage("На Абая 44 нет холодной воды")
                triage = agreeing["triage"]
                sources = triage["source_decisions"]
                assert triage["provider"] == "shadow" and triage["checkpoint_id"] == CHECKPOINT_ID
                assert triage["category"] == sources["existing_classifier"]["category"] == "water_supply"
                assert triage["urgency"] == sources["existing_classifier"]["urgency"]
                assert triage["decision_mode"] == sources["existing_classifier"]["decision"]
                assert sources["comparison"] == {
                    "status": "completed", "category_agreement": True,
                    "urgency_agreement": True, "decision_agreement": True,
                }
                assert "convaiinnovations" not in json.dumps(triage)

                _, shadow_spam = shadow_triage("Купите рекламу, быстрый заработок")
                assert shadow_spam["triage"]["source_decisions"]["laya"]["spam_suspected"] == {
                    "value": True, "confidence": .92, "probability_true": .92, "threshold": .9,
                }

                _, disagreement = shadow_triage("disagreement: нет холодной воды")
                triage = disagreement["triage"]
                sources = triage["source_decisions"]
                assert triage["category"] == sources["existing_classifier"]["category"] == "water_supply"
                assert sources["laya"]["category"] == "sewerage"
                assert sources["comparison"]["category_agreement"] is False
                assert triage["decision_mode"] != "MODEL_DISAGREEMENT"

                _, failed = shadow_triage("laya-unavailable, но нет холодной воды")
                triage = failed["triage"]
                sources = triage["source_decisions"]
                assert triage["category"] == sources["existing_classifier"]["category"] == "water_supply"
                assert triage["urgency"] == sources["existing_classifier"]["urgency"]
                assert triage["decision_mode"] == sources["existing_classifier"]["decision"]
                assert triage["fallback_reason"] and sources["laya"]["status"] == "failed"
                assert sources["comparison"] == {"status": "failed"}

                audit = http_request(url + f"/api/complaints/{agreeing_id}")[1]["events"][-1]
                payload = json.loads(audit["payload"])
                assert payload["provider"] == "shadow"
                assert payload["triage_contract"]["source_decisions"]["comparison"]["status"] == "completed"
                code, _ = http_request(
                    url + f"/api/workspace/complaints/{agreeing_id}/decide", "POST",
                    {"topic": "water_supply", "priority": "normal"},
                )
                assert code == 200
                print("PASS 9: shadow keeps existing decisions; agreement, disagreement, failure and audit stay visible")
            finally:
                stop(proc)

            proc, url = start_pulse(root, db, laya_url)
            try:
                queue = http_request(url + "/api/workspace/queue")[1]
                stored = next(item for item in queue["items"] if item["complaint"]["id"] == high)
                assert stored["triage"]["provider"] == "laya"
                print("PASS 10: stored Laya decision survives restart without reclassification")
            finally:
                stop(proc)

            hybrid_db = Path(tmp) / "hybrid.db"
            proc, url = start_pulse(root, hybrid_db, laya_url, "hybrid")
            try:
                code, created = http_request(url + "/api/workspace/intake", "POST", {
                    "text": "disagreement: нет холодной воды", "region_id": "KZ-ALA",
                    "language": "ru", "district": "Алмалинский", "channel": "web",
                })
                assert code == 201
                detail = http_request(url + f"/api/workspace/complaints/{created['id']}/triage", "POST", {})[1]
                assert detail["triage"]["decision_mode"] == "MODEL_DISAGREEMENT"
                assert detail["triage"]["source_decisions"]["existing_classifier"]["category"] == "water_supply"
                assert detail["triage"]["source_decisions"]["laya"]["category"] == "sewerage"
                assert detail["routing"]["operator"] is None
                agreeing = http_request(url + "/api/workspace/intake", "POST", {
                    "text": "На Абая 44 нет холодной воды", "region_id": "KZ-ALA",
                    "language": "kk", "district": "Алмалинский", "channel": "web",
                })[1]["id"]
                agreed = http_request(url + f"/api/workspace/complaints/{agreeing}/triage", "POST", {})[1]
                assert agreed["triage"]["decision_mode"] == "AUTO_PRESELECT"
                assert agreed["triage"]["source_decisions"]["existing_classifier"]["category"] == "water_supply"
                unavailable = http_request(url + "/api/workspace/intake", "POST", {
                    "text": "laya-unavailable, но нет холодной воды", "region_id": "KZ-ALA",
                    "language": "ru", "district": "Алмалинский", "channel": "web",
                })[1]["id"]
                degraded = http_request(url + f"/api/workspace/complaints/{unavailable}/triage", "POST", {})[1]
                assert degraded["triage"]["provider"] == "demo_fallback" and degraded["triage"]["fallback_reason"]
                print("PASS 11: hybrid agreement, disagreement and one-provider fallback")
            finally:
                stop(proc)
    finally:
        laya_server.shutdown()
        laya_server.server_close()
    print("ALL 11 LAYA INTEGRATION CHECKS PASSED")


if __name__ == "__main__":
    run()
