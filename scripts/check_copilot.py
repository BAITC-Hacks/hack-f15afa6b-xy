"""Runnable checks for Copilot providers, failover, safety and API integration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot_contracts import (
    CopilotContext,
    CopilotOutput,
    CopilotRateLimitError,
    CopilotResponseError,
    CopilotSchemaError,
    CopilotUnavailable,
    VisualEvidence,
    sanitize_text,
    validate_copilot_output,
)
from copilot_router import CircuitBreaker, CopilotModelRouter, ProviderRouter
from deterministic_copilot import DeterministicCopilot
from openai_copilot import OpenAICopilot
from qwen_copilot import QwenCopilot
from smoke import find_free_port, http_request, wait_for_server


def context(**changes):
    base = {
        "language": "ru",
        "complaint": "Во всём доме нет воды с утра.",
        "category": "water_supply",
        "confidence_band": "high",
        "decision_status": "pending",
        "incident_candidate": False,
        "fallback_summary": "Во всём доме нет воды с утра.",
        "fallback_reasoning": "Нужна проверка оператора.",
        "fallback_reply": "Обращение зарегистрировано. Срок пока не подтверждён.",
        "fallback_question": None,
        "fallback_action": "prepare_reply",
        "request_id": "1" * 32,
    }
    return CopilotContext(**{**base, **changes})


def output(language="ru"):
    if language == "kk":
        return CopilotOutput(
            summary="Бүкіл үйде таңертеңнен бері су жоқ.",
            reasoning="Су мәселесі және үй көлемі көрсетілген.",
            suggested_reply="Өтінішіңіз тіркелді. Мерзім әлі расталған жоқ.",
            clarification_question=None,
            recommended_action="prepare_reply",
        )
    if language == "mixed":
        return CopilotOutput(
            summary="Абая көшесінде свет жоқ.",
            reasoning="Электр мәселесін оператор тексеруі керек.",
            suggested_reply="Өтініш тіркелді. Срок пока не подтверждён.",
            clarification_question=None,
            recommended_action="prepare_reply",
        )
    return CopilotOutput(
        summary="Во всём доме нет воды с утра.",
        reasoning="Указаны масштаб дома и категория водоснабжения.",
        suggested_reply="Обращение зарегистрировано. Срок пока не подтверждён.",
        clarification_question=None,
        recommended_action="prepare_reply",
    )


class FakeResponses:
    def __init__(self, error=None, parsed=None):
        self.error = error
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        payload = json.loads(kwargs["input"][1]["content"][0]["text"])
        parsed = self.parsed if self.parsed is not None else output(payload["language"])
        if len(kwargs["input"][1]["content"]) > 1 and self.parsed is None:
            parsed = parsed.model_copy(update={"visual_evidence": VisualEvidence(
                observations=["Возможное скопление воды на поверхности."],
                suggested_category="water_supply", confidence=.71,
            )})
        usage = SimpleNamespace(
            input_tokens=120,
            output_tokens=40,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
        )
        return SimpleNamespace(output_parsed=parsed, model=kwargs["model"], usage=usage)


class FakeClient:
    def __init__(self, **kwargs):
        self.responses = FakeResponses(**kwargs)


class UnavailableProvider:
    provider_name = "openai"

    def assist(self, _):
        raise CopilotUnavailable("offline")

    def health(self):
        return {"configured": True, "status": "unavailable"}


class QwenFixtureProvider:
    provider_name = "qwen"

    def assist(self, value):
        return DeterministicCopilot().assist(value).model_copy(update={"provider": "qwen"})

    def health(self):
        return {"configured": True, "status": "healthy"}


class FakeQwen(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw)
        self.requests.append({"authorization": self.headers.get("Authorization"), "body": body})
        sent = json.loads(body["messages"][1]["content"])
        content = "not-json" if "malformed" in sent["complaint"] else output(sent["language"]).model_dump_json()
        response = json.dumps({
            "model": "Qwen/Qwen3-4B-Instruct-2507",
            "usage": {"prompt_tokens": 90, "completion_tokens": 30},
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def start_fake_qwen():
    FakeQwen.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQwen)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def start_pulse(root, db, qwen_url):
    port = find_free_port()
    env = os.environ.copy()
    for name in (
        "OPENAI_API_KEY", "P109_COPILOT_PROVIDER", "P109_COPILOT_FALLBACKS",
        "P109_OPENAI_MODEL_FAST", "P109_OPENAI_MODEL_PRIMARY", "P109_OPENAI_MODEL_COMPLEX",
    ):
        env.pop(name, None)
    env.update({
        "PYTHONPATH": str(root),
        "DATABASE_PATH": str(db),
        "P109_AUTH_DISABLED": "1",
        "P109_DEMO_MODE": "1",
        "P109_COPILOT_BASE_URL": qwen_url,
        "P109_COPILOT_API_KEY": "fixture-secret",
        "P109_COPILOT_MODEL": "Qwen/Qwen3-4B-Instruct-2507",
        "P109_COPILOT_TIMEOUT": "1",
        "P109_AI_RETRIES": "1",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def check_contracts():
    redacted = sanitize_text(
        "ИИН 123456789012, +7 777 123 45 67, a@example.kz, Абая 44", "Абая 44"
    )
    assert all(value not in redacted for value in ("123456789012", "+7 777", "a@example.kz", "Абая 44"))
    try:
        validate_copilot_output(output().model_copy(update={
            "suggested_reply": "Мы уже устранили проблему."
        }), context())
        raise AssertionError("Fake completion claim was accepted")
    except CopilotResponseError:
        pass
    try:
        validate_copilot_output(output().model_copy(update={
            "suggested_reply": "Авария будет устранена в течение 30 минут."
        }), context())
        raise AssertionError("Invented deadline was accepted")
    except CopilotResponseError:
        pass
    try:
        validate_copilot_output(output().model_copy(update={
            "recommended_action": "clarify"
        }), context())
        raise AssertionError("Clarify without question was accepted")
    except CopilotSchemaError:
        pass
    try:
        validate_copilot_output(output().model_copy(update={"visual_evidence": VisualEvidence(
            observations=["Авария подтверждена по фото."], confidence=.9,
        )}), context())
        raise AssertionError("Visual evidence confirmed an incident")
    except CopilotResponseError:
        pass
    print("PASS 1: shared contract removes PII and rejects unsafe claims and inconsistent actions")


def check_openai():
    models = {"fast": "gpt-fast", "primary": "gpt-primary", "complex": "gpt-complex"}
    fake = FakeClient()
    provider = OpenAICopilot("test", models, input_usd_per_million=1,
                             output_usd_per_million=2, client=fake)
    result = provider.assist(context())
    call = fake.responses.calls[0]
    assert result.provider == "openai" and result.model_tier == "fast"
    assert result.usage.input_tokens == 120 and result.usage.cached_tokens == 20
    assert result.usage.estimated_cost_usd == .0002
    assert call["text_format"] is CopilotOutput and call["store"] is False
    sent_payload = json.loads(call["input"][1]["content"][0]["text"])
    assert "fallback_reply" not in sent_payload and "operator_identity" not in sent_payload
    image = provider.assist(context(image_data_url="data:image/png;base64,AAAA"))
    assert image.provider == "openai"
    assert image.visual_evidence and image.visual_evidence.confidence == .71
    assert fake.responses.calls[-1]["input"][1]["content"][1]["type"] == "input_image"

    RateLimitError = type("RateLimitError", (Exception,), {})
    failing = OpenAICopilot("test", models, client=FakeClient(error=RateLimitError("slow_down")))
    try:
        failing.assist(context())
        raise AssertionError("Rate limit was not mapped")
    except CopilotRateLimitError:
        pass
    ContentFilterFinishReasonError = type("ContentFilterFinishReasonError", (Exception,), {})
    refused = OpenAICopilot(
        "test", models, client=FakeClient(error=ContentFilterFinishReasonError("refused"))
    )
    try:
        refused.assist(context())
        raise AssertionError("Content refusal was not mapped")
    except CopilotResponseError:
        pass
    invalid = OpenAICopilot("test", models, client=FakeClient(parsed={"summary": "missing"}))
    try:
        invalid.assist(context())
        raise AssertionError("Invalid schema was accepted")
    except CopilotSchemaError:
        pass
    print("PASS 2: OpenAI uses Responses Structured Outputs, image input, usage and typed errors")


def check_routing():
    model_router = CopilotModelRouter()
    assert model_router.select(context()) == "fast"
    assert model_router.select(context(confidence_band="medium")) == "primary"
    assert model_router.select(context(complexity_signals=["model_disagreement"])) == "complex"
    assert model_router.select(context(complexity_signals=["model_disagreement"]), True) == "primary"
    assert model_router.select(context(confidence_band="medium",
                                       complexity_signals=["budget_degraded"])) == "fast"

    half_open = CircuitBreaker(1, .01)
    half_open.failure("openai")
    time.sleep(.02)
    assert half_open.allow("openai")
    half_open.failure("openai")
    assert half_open.status("openai") == "open"

    deterministic = DeterministicCopilot()
    router = ProviderRouter(
        {"openai": UnavailableProvider(), "deterministic": deterministic},
        ["openai", "qwen", "deterministic"],
        CircuitBreaker(1, 30),
        retries=0,
    )
    result = router.assist(context())
    assert result.provider == "deterministic" and result.available and result.fallback_reason
    health = router.health()
    assert health["providers"]["openai"]["circuit"] == "open"
    assert health["providers"]["qwen"]["configured"] is False
    assert health["providers"]["deterministic"]["status"] == "healthy"
    shadow = ProviderRouter(
        {"openai": QwenFixtureProvider(), "qwen": QwenFixtureProvider(),
         "deterministic": deterministic}, ["openai", "deterministic"],
        CircuitBreaker(), retries=0, shadow_qwen=True,
    ).assist(context())
    assert shadow.shadow_comparison["agreement"] is True
    print("PASS 3: model tiers, failover and circuit breaker keep deterministic available")


def check_api():
    try:
        QwenCopilot("https://api.example.com", "secret")
        raise AssertionError("Public Qwen endpoint was accepted")
    except ValueError:
        pass
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
                health = call("/api/health")["copilot"]
                assert health["active_provider"] == "qwen"
                assert health["fallbacks"] == ["deterministic"]
                assert health["providers"]["qwen"]["endpoint"] == "private"

                created = call("/api/workspace/intake", {
                    "text": "На Абая 44 нет воды. ИИН 123456789012, +7 777 123 45 67, a@example.kz",
                    "address": "Абая 44", "region_id": "KZ-ALA", "language": "ru",
                }, 201)
                cid = created["id"]
                call(f"/api/workspace/complaints/{cid}/triage", {})
                answer = call(f"/api/workspace/complaints/{cid}/copilot", {})
                assert answer["provider"] == "qwen" and answer["mode"] == "self_hosted_qwen"
                assert answer["usage"]["input_tokens"] == 90 and len(answer["result_id"]) == 64
                sent = FakeQwen.requests[-1]
                prompt = sent["body"]["messages"][1]["content"]
                assert sent["authorization"] == "Bearer fixture-secret"
                assert all(value not in prompt for value in ("Абая 44", "123456789012", "+7 777", "a@example.kz"))

                events = call(f"/api/complaints/{cid}")["events"]
                generated = next(json.loads(item["payload"]) for item in events
                                 if item["event_type"] == "copilot_generated")
                assert generated["provider"] == "qwen" and generated["prompt_version"]
                assert "Во всём доме" not in json.dumps(generated, ensure_ascii=False)
                call(f"/api/workspace/complaints/{cid}/copilot/feedback", {
                    "result_id": answer["result_id"], "helpful": False, "reason": "not_useful",
                })

                for language, text, expected in (
                    ("kk", "Бүкіл үйде таңертеңнен бері су жоқ", "Өтінішіңіз"),
                    ("mixed", "Абая көшесінде свет жоқ", "Өтініш"),
                ):
                    item = call("/api/workspace/intake", {
                        "text": text, "region_id": "KZ-ALA", "language": language,
                    }, 201)["id"]
                    call(f"/api/workspace/complaints/{item}/triage", {})
                    translated = call(f"/api/workspace/complaints/{item}/copilot", {})
                    assert translated["provider"] == "qwen"
                    assert translated["suggested_reply"].startswith(expected)

                bad = call("/api/workspace/intake", {
                    "text": "malformed: непонятная проблема", "region_id": "KZ-ALA", "language": "ru",
                }, 201)["id"]
                call(f"/api/workspace/complaints/{bad}/triage", {})
                fallback = call(f"/api/workspace/complaints/{bad}/copilot", {})
                assert fallback["provider"] == "deterministic" and fallback["available"]
                assert fallback["mode"] == "deterministic_fallback" and fallback["fallback_reason"]
                code, unsupported = http_request(
                    url + f"/api/workspace/complaints/{bad}/copilot", "POST", {"include_image": True}
                )
                assert code == 422 and "JPEG" in unsupported["detail"]
            finally:
                stop(process)
    finally:
        qwen_server.shutdown()
        qwen_server.server_close()
    print("PASS 4: API supports RU/KK/mixed, safe audit/feedback and Qwen to deterministic failover")


def run():
    check_contracts()
    check_openai()
    check_routing()
    check_api()
    print("ALL 4 COPILOT CHECK GROUPS PASSED")


if __name__ == "__main__":
    run()
