"""Measure the configured Laya endpoint on 50–100 synthetic Pulse requests."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import TOPICS
from decision import DecisionGate, LayaDecisionProvider
from laya_client import LayaClient, LayaError


def run(base_url, count, timeout):
    if not 50 <= count <= 100:
        raise SystemExit("--requests must be between 50 and 100")
    root = Path(__file__).resolve().parents[1]
    fixtures = [item for item in json.loads((root / "fixtures/demo.json").read_text())["complaints"]
                if item.get("language") in {"ru", "kk"}]
    provider = LayaDecisionProvider(LayaClient(base_url, timeout=timeout, model="multilingual"),
                                    TOPICS, DecisionGate())
    latencies, errors, verification = [], 0, 0
    versions = set()
    for index in range(count):
        item = fixtures[index % len(fixtures)]
        complaint = {"text": item["text"], "language": item["language"]}
        started = time.perf_counter()
        try:
            result = provider.classify(complaint)
            versions.add(result.provider_version)
            if result.decision == "VERIFY" and result.category.value:
                verification += 1
                provider.verify(complaint, result.category.value)
            latencies.append((time.perf_counter() - started) * 1000)
        except LayaError:
            errors += 1
    ordered = sorted(latencies)
    report = {
        "requests": count, "successful": len(latencies), "errors": errors,
        "error_rate": round(errors / count, 4),
        "median_latency_ms": round(statistics.median(ordered), 2) if ordered else None,
        "p95_latency_ms": round(ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 2) if ordered else None,
        "verification_count": verification,
        "verification_percentage": round(100 * verification / count, 2),
        "provider_versions": sorted(versions),
        "dataset": "fixtures/demo.json — synthetic RU/KK requests",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    raise SystemExit(run(args.base_url, args.requests, args.timeout))
