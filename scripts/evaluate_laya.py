"""Evaluate Laya on the checked synthetic RU/KK Pulse fixture labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import TOPICS
from decision import DecisionGate, LayaDecisionProvider
from laya_client import LayaClient, LayaError


def scores(rows, labels):
    matrix = {actual: Counter(pred for a, pred, *_ in rows if a == actual) for actual in labels}
    per_category = {}
    for label in labels:
        tp = sum(actual == pred == label for actual, pred, *_ in rows)
        fp = sum(actual != label and pred == label for actual, pred, *_ in rows)
        fn = sum(actual == label and pred != label for actual, pred, *_ in rows)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        per_category[label] = {"support": tp + fn, "precision": round(precision, 4),
                               "recall": round(recall, 4), "f1": round(f1, 4)}
    return {
        "count": len(rows),
        "accuracy": round(sum(actual == pred for actual, pred, *_ in rows) / len(rows), 4) if rows else None,
        "macro_f1": round(sum(item["f1"] for item in per_category.values()) / len(labels), 4) if rows else None,
        "per_category": per_category,
        "confusion_matrix": {label: dict(matrix[label]) for label in labels},
    }


def group_id(item):
    if item.get("incident_id"):
        return "incident:" + item["incident_id"]
    if item.get("duplicate_of"):
        return "duplicate:" + item["duplicate_of"]
    normalized = re.sub(r"\W+", " ", item["text"].lower()).strip()
    return "fingerprint:" + hashlib.sha256(normalized.encode()).hexdigest()[:16]


def run(base_url, timeout, output=None):
    root = Path(__file__).resolve().parents[1]
    items = [item for item in json.loads((root / "fixtures/demo.json").read_text())["complaints"]
             if item.get("topic") and item.get("language") in {"ru", "kk"}]
    provider = LayaDecisionProvider(LayaClient(base_url, timeout=timeout, model="multilingual"),
                                    TOPICS, DecisionGate())
    rows, failures = [], []
    clarification_count = 0
    for item in items:
        complaint = {"text": item["text"], "language": item["language"]}
        try:
            result = provider.classify(complaint)
        except LayaError as error:
            failures.append({"id": item["id"], "error": str(error)})
            continue
        rows.append((item["topic"], result.category.value, item["language"],
                     item["priority"], result.urgency.value, group_id(item)))
        clarification_count += result.needs_clarification.value
    labels = [topic["id"] for topic in TOPICS]
    report = {
        "dataset": "fixtures/demo.json — synthetic labels only",
        "model": "multilingual", "samples": len(items), "evaluated": len(rows),
        "failures": failures, "groups": len({row[5] for row in rows}),
        "split_note": "Evaluation-only synthetic set. For real data, split by incident_id, duplicate cluster, then fingerprint.",
        "overall": scores(rows, labels),
        "by_language": {language: scores([row for row in rows if row[2] == language], labels)
                        for language in ("ru", "kk")},
        "clarification_rate": round(clarification_count / len(rows), 4) if rows else None,
        "override_rate": None,
        "urgent_recall": (round(sum(expected == predicted == "urgent" for *_, expected, predicted, _group in rows) /
                                sum(expected == "urgent" for *_, expected, _predicted, _group in rows), 4)
                          if any(expected == "urgent" for *_, expected, _predicted, _group in rows) else None),
        "spam_precision": None,
        "limitations": [
            "No real citizen text or measured production quality.",
            "No labeled spam cases; spam precision is unavailable.",
            "Operator override rate requires confirmed operator decisions.",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    raise SystemExit(run(args.base_url, args.timeout, args.output))
