"""End-to-end OpenAI-primary checks with a local official-SDK-shaped fixture."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke import find_free_port, http_request, wait_for_server


FAKE_OPENAI = '''
import json
import os
from types import SimpleNamespace

class Responses:
    def parse(self, **kwargs):
        payload = json.loads(kwargs["input"][1]["content"][0]["text"])
        with open(os.environ["P109_FAKE_OPENAI_CAPTURE"], "a") as target:
            target.write(json.dumps(payload, ensure_ascii=False) + "\\n")
        language = payload["language"]
        if language == "kk":
            values = {"summary": "Бүкіл үйде су жоқ.", "reasoning": "Оператор тексеруі керек.",
                      "suggested_reply": "Өтінішіңіз тіркелді. Мерзім әлі расталған жоқ."}
        else:
            values = {"summary": "Во всём доме нет воды.", "reasoning": "Нужна проверка оператора.",
                      "suggested_reply": "Обращение зарегистрировано. Срок пока не подтверждён."}
        if "unsafe" in payload["complaint"]:
            values["suggested_reply"] = "Авария будет устранена в течение 30 минут."
        values.update(clarification_question=None, recommended_action="prepare_reply")
        values["visual_evidence"] = ({"observations": ["Возможное скопление воды."],
                                      "suggested_category": "water_supply", "confidence": .71}
                                     if len(kwargs["input"][1]["content"]) > 1 else None)
        parsed = kwargs["text_format"].model_validate(values)
        usage = SimpleNamespace(input_tokens=100, output_tokens=30,
                                input_tokens_details=SimpleNamespace(cached_tokens=10))
        return SimpleNamespace(output_parsed=parsed, model=kwargs["model"], usage=usage)

class OpenAI:
    def __init__(self, **kwargs): self.responses = Responses()
'''


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        fake_root = temporary / "fake"
        package = fake_root / "openai"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(FAKE_OPENAI)
        capture = temporary / "capture.jsonl"
        port = find_free_port()
        env = os.environ.copy()
        for name in ("P109_COPILOT_BASE_URL", "P109_COPILOT_API_KEY", "P109_LAYA_BASE_URL"):
            env.pop(name, None)
        env.update({
            "PYTHONPATH": os.pathsep.join((str(fake_root), str(root))),
            "DATABASE_PATH": str(temporary / "openai.db"),
            "P109_AUTH_DISABLED": "1", "P109_DEMO_MODE": "1",
            "OPENAI_API_KEY": "fixture", "P109_COPILOT_PROVIDER": "openai",
            "P109_COPILOT_FALLBACKS": "deterministic",
            "P109_OPENAI_MODEL_FAST": "fixture-fast",
            "P109_OPENAI_MODEL_PRIMARY": "fixture-primary",
            "P109_OPENAI_MODEL_COMPLEX": "fixture-complex",
            "P109_AI_RETRIES": "0", "P109_FAKE_OPENAI_CAPTURE": str(capture),
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{port}"

        def call(path, body=None, expected=200):
            status, value = http_request(url + path, "POST" if body is not None else "GET", body)
            assert status == expected, (path, status, value)
            return value

        try:
            if not wait_for_server(url):
                stdout, stderr = process.communicate(timeout=3)
                raise RuntimeError(stdout.decode() + stderr.decode())
            health = call("/api/health")["copilot"]
            assert health["active_provider"] == "openai"
            assert health["fallbacks"] == ["deterministic"]

            png = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
            created = call("/api/workspace/intake", {
                "text": "На Абая 44 нет воды. ИИН 123456789012, +7 777 123 45 67, a@example.kz",
                "address": "Абая 44", "region_id": "KZ-ALA", "language": "ru",
                "photo_data": "data:image/png;base64," + png,
            }, 201)["id"]
            call(f"/api/workspace/complaints/{created}/triage", {})
            answer = call(f"/api/workspace/complaints/{created}/copilot", {"include_image": True})
            assert answer["provider"] == "openai" and answer["available"] is True
            assert answer["visual_evidence"]["confidence"] == .71
            assert answer["usage"]["input_tokens"] == 100

            kk = call("/api/workspace/intake", {
                "text": "Бүкіл үйде су жоқ", "region_id": "KZ-ALA", "language": "kk",
            }, 201)["id"]
            call(f"/api/workspace/complaints/{kk}/triage", {})
            kk_answer = call(f"/api/workspace/complaints/{kk}/copilot", {})
            assert kk_answer["provider"] == "openai" and kk_answer["suggested_reply"].startswith("Өтінішіңіз")

            unsafe = call("/api/workspace/intake", {
                "text": "unsafe: во всём доме нет воды", "region_id": "KZ-ALA", "language": "ru",
            }, 201)["id"]
            call(f"/api/workspace/complaints/{unsafe}/triage", {})
            fallback = call(f"/api/workspace/complaints/{unsafe}/copilot", {})
            assert fallback["provider"] == "deterministic" and fallback["available"] is True

            sent = capture.read_text()
            assert all(value not in sent for value in
                       ("Абая 44", "123456789012", "+7 777 123 45 67", "a@example.kz"))
            print("PASS: Brev-off OpenAI primary, image, KK, privacy and unsafe-output fallback")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
