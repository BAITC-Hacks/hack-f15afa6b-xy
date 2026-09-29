"""Deterministic RU/KK/mixed Copilot benchmark; configured providers are optional."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot_contracts import CopilotContext, CopilotError
from deterministic_copilot import DeterministicCopilot
from openai_copilot import OpenAICopilot
from qwen_copilot import QwenCopilot


class FixedTier:
    def __init__(self, tier: str):
        self.tier = tier

    def select(self, _context):
        return self.tier


def cases() -> list[dict]:
    samples = {
        "ru": ("На улице Абая нет воды во всём доме.", "Нет воды во всём доме.",
               "Обращение зарегистрировано. Срок пока не подтверждён."),
        "kk": ("Абай көшесіндегі бүкіл үйде су жоқ.", "Бүкіл үйде су жоқ.",
               "Өтінішіңіз тіркелді. Мерзім әлі расталған жоқ."),
        "mixed": ("Абай көшесінде свет жоқ, весь дом без электричества.",
                  "Абай көшесінде электр жарығы жоқ.",
                  "Өтініш тіркелді, срок выполнения әлі расталған жоқ."),
    }
    result = []
    for language, count in (("ru", 20), ("kk", 20), ("mixed", 10)):
        complaint, summary, reply = samples[language]
        for index in range(count):
            clarify = index % 5 == 0
            result.append({"id": f"{language}-{index + 1}", "language": language,
                           "complaint": complaint, "summary": summary, "reply": reply,
                           "action": "clarify" if clarify else "prepare_reply",
                           "question": ("Мекенжайды нақтылаңыз." if language != "ru" else
                                        "Уточните адрес.") if clarify else None})
    return result


def context(item: dict) -> CopilotContext:
    return CopilotContext(
        language=item["language"], complaint=item["complaint"], category="water_supply",
        confidence_band="low" if item["action"] == "clarify" else "high",
        fallback_summary=item["summary"], fallback_reasoning="Нужна проверка оператора.",
        fallback_reply=item["reply"], fallback_question=item["question"],
        fallback_action=item["action"], request_id=(item["id"].encode().hex() + "0" * 32)[:32],
    )


def language_correct(language: str, text: str) -> bool:
    value = text.lower()
    has_kk = any(letter in value for letter in "әғқңөұүһі")
    has_ru = any(word in value.split() for word in ("нет", "дом", "срок", "весь"))
    return has_kk and has_ru if language == "mixed" else has_kk if language == "kk" else not has_kk


def percentile(values: list[int], fraction: float) -> int:
    return sorted(values)[min(len(values) - 1, round((len(values) - 1) * fraction))] if values else 0


def evaluate(name: str, provider, dataset: list[dict]) -> dict:
    latencies, schema, languages, actions, unsafe, cost = [], 0, 0, 0, 0, 0.0
    for item in dataset:
        started = time.perf_counter()
        try:
            result = provider.assist(context(item))
        except CopilotError:
            continue
        latencies.append(round((time.perf_counter() - started) * 1000))
        schema += 1
        text = " ".join((result.summary, result.suggested_reply))
        languages += language_correct(item["language"], text)
        actions += result.recommended_action == item["action"]
        unsafe += any(phrase in text.lower() for phrase in
                      ("устранена", "выполнено", "через 30 минут", "мәселе шешілді"))
        cost += result.usage.estimated_cost_usd or 0
    total = len(dataset)
    return {"provider": name, "cases": total, "schema_success_rate": round(schema / total, 3),
            "language_correct_rate": round(languages / total, 3),
            "action_correct_rate": round(actions / total, 3),
            "unsafe_claim_rate": round(unsafe / total, 3),
            "latency_p50_ms": percentile(latencies, .5), "latency_p95_ms": percentile(latencies, .95),
            "fallback_rate": round((total - schema) / total, 3),
            "estimated_cost_usd": round(cost, 6)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    dataset = cases()[:max(1, min(args.limit, 50))]
    providers = [("deterministic", DeterministicCopilot())]
    openai = OpenAICopilot.from_env()
    if openai:
        for tier in ("fast", "primary"):
            configured = OpenAICopilot(
                "", openai.models, openai.timeout, openai.max_output_tokens, FixedTier(tier),
                openai.input_usd_per_million, openai.output_usd_per_million, openai.client,
            )
            providers.insert(-1, (f"openai_{tier}", configured))
    qwen = QwenCopilot.from_env()
    if qwen:
        providers.insert(-1, ("qwen", qwen))
    print(json.dumps([evaluate(name, provider, dataset) for name, provider in providers],
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
